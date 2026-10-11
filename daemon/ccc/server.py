# SPDX-License-Identifier: GPL-2.0+
"""HTTP server + JSON API for the Control Center daemon."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import clevo
from .clevo import ClevoBridge, ClevoError, IF_NAMES
from .config import PRESET_CURVES

log = logging.getLogger("cccd.server")

APP_NAME = "colorful-control-center"
APP_VERSION = "1.0.5"

UI_DIR_CANDIDATES = [
    "/usr/share/colorful-control-center/ui",
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "ui"),
]

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".json": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
}


def ui_dir():
    for d in UI_DIR_CANDIDATES:
        if d and os.path.isdir(d):
            return d
    return None


class State:
    """Shared runtime state."""

    def __init__(self, bridge, monitor, fans, kbd, config):
        self.bridge = bridge
        self.monitor = monitor
        self.fans = fans
        self.kbd = kbd
        self.config = config
        self.started = time.time()
        self.lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{APP_VERSION}"
    state: State = None          # set by make_server
    port: int = 8632

    # ---- helpers -------------------------------------------------------
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _err(self, code, message):
        self._json({"ok": False, "error": message}, code=code)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _body_json(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 262144:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    def log_message(self, fmt, *args):  # quieter logs
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- routing -------------------------------------------------------
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if not self._host_ok():
            return self._err(403, "forbidden host")
        if path.startswith("/api/"):
            return self._api_get(path)
        return self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if not self._host_ok():
            return self._err(403, "forbidden host")
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        data = self._body_json()
        if data is None:
            return self._err(400, "invalid json body")
        try:
            return self._api_post(path, data)
        except (ClevoError, OSError) as e:
            return self._err(500, str(e))
        except ValueError as e:
            return self._err(400, str(e))

    # ---- static --------------------------------------------------------
    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        base = ui_dir()
        if not base:
            return self._err(500, "ui files missing")
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith("..") or os.path.isabs(rel):
            return self._err(403, "forbidden")
        full = os.path.join(base, rel)
        if not os.path.isfile(full):
            return self._err(404, "not found")
        ext = os.path.splitext(full)[1].lower()
        try:
            with open(full, "rb") as f:
                body = f.read()
        except OSError:
            return self._err(404, "not found")
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    # ---- API GET -------------------------------------------------------
    def _api_get(self, path):
        s = self.state
        if path == "/api/ping":
            return self._json({"ok": True, "app": APP_NAME, "version": APP_VERSION})
        if path == "/api/status":
            return self._json({"ok": True, **self._status_payload()})
        if path == "/api/diag":
            return self._json({"ok": True, "report": self._diag_report()})
        if path == "/api/config":
            return self._json({"ok": True, "config": s.config.data})
        if path == "/api/autostart":
            return self._json({"ok": True, "enabled": self._autostart_enabled()})
        return self._err(404, "not found")

    def _status_payload(self):
        s = self.state
        try:
            snap = s.monitor.snapshot()
        except Exception as e:  # sensors should never break the API
            log.warning("snapshot failed: %s", e)
            snap = {}
        bridge = {
            "present": s.bridge.present(),
            "error": s.bridge.last_error,
            "interface": None,
            "interface_name": "unknown",
            "version": None,
            "secure_boot": self._secure_boot(),
            "module_loaded": self._module_loaded(),
        }
        try:
            iface = s.bridge.active_interface()
            bridge["interface"] = iface
            bridge["interface_name"] = IF_NAMES.get(iface, str(iface))
        except ClevoError:
            pass
        kbd = s.kbd.status()
        fans = s.fans.status()
        return {
            "identity": s.monitor.identity(),
            "temps": {
                "cpu": snap.get("cpu_temp"),
                "gpu": (snap.get("gpu") or {}).get("temp"),
                "gpu_util": (snap.get("gpu") or {}).get("util"),
                "gpu_name": (snap.get("gpu") or {}).get("name"),
                "nvme": snap.get("nvme_temps") or [],
                "zones": snap.get("zones") or {},
            },
            "fans": {
                "rpm": snap.get("fan_rpm") or [],
                "state": fans,
                "presets": PRESET_CURVES,
            },
            "load": {
                "cpu": snap.get("cpu_load"),
                "cpu_mhz": snap.get("cpu_mhz"),
                "mem": snap.get("mem"),
            },
            "battery": {**(snap.get("battery") or {}),
                        "threshold": s.monitor.charge_thresholds()},
            "keyboard": kbd,
            "bridge": bridge,
            "perf": {
                "profile": s.config.get("perf", "profile", default=None),
                "profiles": clevo.PERF_PROFILES,
            },
            "autostart": {"enabled": self._autostart_enabled()},
            "prefs": {
                "kbd_apply_on_start": s.config.get("kbd", "apply_on_start", default=True),
                "perf_apply_on_start": s.config.get("perf", "apply_on_start", default=False),
                "restore_auto_on_stop": s.config.get("daemon", "restore_auto_on_stop", default=True),
            },
            "meta": {
                "app": APP_NAME,
                "version": APP_VERSION,
                "uptime": int(time.time() - s.started),
            },
        }

    # ---- API POST ------------------------------------------------------
    def _api_post(self, path, data):
        s = self.state
        if path == "/api/fan":
            mode = data.get("mode")
            curve = data.get("curve")
            manual = data.get("manual")
            linked = data.get("linked")
            if mode is None and curve is None and manual is None:
                return self._err(400, "nothing to do")
            if mode is None:
                mode = s.fans.status()["mode"]
                if curve is not None and mode not in ("custom",):
                    mode = "custom"
            s.fans.set_mode(mode, curve=curve, manual=manual, linked=linked)
            return self._json({"ok": True, "fan": s.fans.status()})

        if path == "/api/kbd":
            st = s.kbd.update(
                on=data.get("on"),
                brightness=data.get("brightness"),
                brightness_pct=data.get("brightness_pct"),
                effect=data.get("effect"),
                colors=data.get("colors"),
                color=data.get("color"),
                linked=data.get("linked"),
                white_level=data.get("white_level"),
            )
            return self._json({"ok": True, "keyboard": st})

        if path == "/api/perf":
            profile = data.get("profile")
            if profile is None:
                return self._err(400, "profile required")
            profile = int(profile)
            if profile < 0 or profile > 3:
                return self._err(400, "profile must be 0-3")
            r = s.bridge.set_perf_profile(profile)
            st, res = r[0], r[1]
            if st != 0:
                return self._err(502, f"固件返回异常 {st}（该机型可能不支持此预设）")
            s.config.set("perf", "profile", profile)
            return self._json({"ok": True, "status": st, "result": res})

        if path == "/api/raw":
            cmd = data.get("cmd")
            arg = int(data.get("arg") or 0)
            if cmd is None:
                return self._err(400, "cmd required")
            cmd = int(cmd, 0) if isinstance(cmd, str) else int(cmd)
            want_buf = bool(data.get("buf"))
            st, res, buf = s.bridge.call(cmd, arg, want_buf=want_buf)
            out = {
                "ok": True,
                "status": st,
                "result": res,
                "result_hex": f"0x{res & 0xFFFFFFFF:08X}",
            }
            if buf is not None:
                out["buf_hex"] = buf.hex()
            return self._json(out)

        if path == "/api/module":
            action = data.get("action", "load")
            if action == "load":
                msg = self._load_module()
                return self._json({"ok": True, "message": msg,
                                   "present": s.bridge.present()})
            if action == "reprobe":
                iface = s.bridge.reprobe()
                return self._json({"ok": True, "interface": iface,
                                   "interface_name": IF_NAMES.get(iface, str(iface))})
            if action == "force":
                iface = int(data.get("interface", 0))
                got = s.bridge.force_interface(iface)
                return self._json({"ok": True, "interface": got})
            return self._err(400, "unknown action")

        if path == "/api/autostart":
            enabled = bool(data.get("enabled"))
            got = self._set_autostart(enabled)
            return self._json({"ok": True, "enabled": got})

        if path == "/api/reapply":
            s.fans.set_mode(s.fans.status()["mode"])
            s.kbd.apply()
            return self._json({"ok": True})

        if path == "/api/kbd/redetect":
            s.kbd._detect()
            return self._json({"ok": True, "keyboard": s.kbd.status()})

        if path == "/api/config":
            for section, values in data.items():
                if not isinstance(values, dict):
                    continue
                if section not in ("kbd", "fan", "perf", "daemon"):
                    continue
                for k, v in values.items():
                    s.config.set(section, k, v)
            return self._json({"ok": True, "config": s.config.data})

        return self._err(404, "not found")

    # ---- helpers -------------------------------------------------------
    def _load_module(self):
        try:
            r = subprocess.run(["modprobe", "clevo_cc"],
                               capture_output=True, text=True, timeout=10)
            msg = (r.stdout + r.stderr).strip() or "modprobe done"
        except (OSError, subprocess.TimeoutExpired) as e:
            msg = f"modprobe failed: {e}"
        time.sleep(0.3)
        if not self.state.bridge.present():
            msg += " | /dev/clevo_cc 仍不存在（可能需要在诊断页查看安全启动状态）"
        return msg

    def _diag_report(self):
        s = self.state
        lines = []
        ident = s.monitor.identity()
        lines.append("== 系统 ==")
        lines.append(f"主机: {ident['hostname']}  型号: {ident['vendor']} {ident['product']}")
        lines.append(f"主板: {ident['board']}  BIOS: {ident['bios']}")
        lines.append(f"内核: {ident['kernel']}")

        sb = self._secure_boot()
        lines.append(f"安全启动: {sb}")

        lines.append("")
        lines.append("== 内核桥接模块 ==")
        loaded = False
        try:
            with open("/proc/modules") as f:
                loaded = any(l.startswith("clevo_cc ") for l in f)
        except OSError:
            pass
        lines.append(f"clevo_cc 模块: {'已加载' if loaded else '未加载'}")
        lines.append(f"/dev/clevo_cc: {'存在' if s.bridge.present() else '不存在'}")
        lines.append(f"最近错误: {s.bridge.last_error or '无'}")
        try:
            iface = s.bridge.active_interface()
            lines.append(f"活动接口: {IF_NAMES.get(iface, iface)}")
        except ClevoError as e:
            lines.append(f"活动接口: 不可用 ({e})")
        try:
            ver = s.bridge.module_version()
            lines.append(f"模块版本: 0x{ver:08X}")
        except ClevoError:
            pass

        lines.append("")
        lines.append("== 键盘 ==")
        kb = s.kbd.status()
        lines.append(f"类型: {kb['type_name']} (0x{(kb['type'] or 0):02x})")
        lines.append(f"规格原始: {kb.get('specs')}")
        try:
            w = s.bridge.kb_get_white()
            lines.append(f"白色背光亮度(0x3D): {w}")
        except ClevoError:
            pass

        lines.append("")
        lines.append("== 风扇 ==")
        lines.append(f"hwmon 风扇: {s.monitor.fan_paths}")
        lines.append(f"当前转速: {s.monitor.fan_rpms()} RPM")
        fan = s.fans.status()
        lines.append(f"控制模式: {fan['mode']}  占空比: {fan['duty_pct']}%")
        lines.append(f"引擎错误: {fan['error'] or '无'}")
        for name, i in (("0x63", 0), ("0x64", 1), ("0x6E", 2)):
            try:
                info = s.bridge.fan_info(i)
                lines.append(f"风扇信息 {name}: raw=0x{info['raw']:08X} "
                             f"rpm={info['rpm']} duty={info['duty']}")
            except ClevoError:
                pass

        lines.append("")
        lines.append("== ACPI / WMI ==")
        for p in ("/sys/bus/acpi/devices/CLV0001:00",
                  "/sys/bus/acpi/devices/CLV0002:00"):
            lines.append(f"{p}: {'存在' if os.path.exists(p) else '不存在'}")
        wmi_dir = "/sys/bus/wmi/devices"
        if os.path.isdir(wmi_dir):
            for d in sorted(os.listdir(wmi_dir)):
                if d.upper().startswith("ABBC"):
                    lines.append(f"WMI: {d}")

        lines.append("")
        lines.append("== 冷却设备 ==")
        import glob as _glob
        for c in sorted(_glob.glob("/sys/class/thermal/cooling_device*")):
            t = self._read(os.path.join(c, "type"))
            if t in ("TFN1", "TFN2"):
                cur = self._read(os.path.join(c, "cur_state"))
                mx = self._read(os.path.join(c, "max_state"))
                lines.append(f"{c.split('/')[-1]} {t}: cur={cur} max={mx}")

        lines.append("")
        lines.append("== 内核日志 (clevo_cc) ==")
        try:
            r = subprocess.run(["dmesg"], capture_output=True, text=True, timeout=5)
            for l in r.stdout.splitlines()[-400:]:
                if "clevo_cc" in l:
                    lines.append(l)
        except (OSError, subprocess.TimeoutExpired):
            lines.append("(无法读取 dmesg)")

        return "\n".join(lines)

    @staticmethod
    def _read(path):
        try:
            with open(path) as f:
                return f.read().strip()
        except OSError:
            return None

    # ---- autostart (tray app at login) ---------------------------------
    DESKTOP_TEMPLATE = (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=控制中心 (托盘)\n"
        "Name[zh_CN]=控制中心 (托盘)\n"
        "Comment=隐星控制中心托盘常驻，关闭窗口后仍在后台运行\n"
        "Exec=/usr/bin/colorful-control-center --hidden\n"
        "Icon=colorful-control-center\n"
        "Terminal=false\n"
        "StartupNotify=false\n"
        "X-GNOME-Autostart-enabled=true\n"
    )

    @staticmethod
    def _desktop_user():
        import glob as _glob
        import pwd
        for d in sorted(_glob.glob("/run/user/*")):
            try:
                uid = int(os.path.basename(d))
            except ValueError:
                continue
            if uid < 1000:
                continue
            if (os.path.exists(os.path.join(d, "wayland-0")) or
                    os.path.exists(os.path.join(d, "X11"))):
                try:
                    return pwd.getpwuid(uid)
                except KeyError:
                    continue
        for h in sorted(_glob.glob("/home/*")):
            try:
                st = os.stat(h)
                pw = pwd.getpwuid(st.st_uid)
            except (OSError, KeyError):
                continue
            if 1000 <= pw.pw_uid < 60000:
                return pw
        try:
            for pw in pwd.getpwall():
                if (1000 <= pw.pw_uid < 60000 and
                        pw.pw_dir.startswith("/home/") and
                        os.path.isdir(pw.pw_dir)):
                    return pw
        except Exception:
            pass
        return None

    def _autostart_path(self):
        pw = self._desktop_user()
        if not pw:
            return None
        return os.path.join(pw.pw_dir, ".config", "autostart",
                            "colorful-control-center.desktop")

    def _autostart_enabled(self):
        p = self._autostart_path()
        return bool(p and os.path.exists(p))

    def _set_autostart(self, enabled):
        pw = self._desktop_user()
        path = self._autostart_path()
        if not pw or not path:
            raise OSError("无法定位桌面用户主目录")
        if enabled:
            d = os.path.dirname(path)
            os.makedirs(d, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.DESKTOP_TEMPLATE)
            os.chmod(path, 0o644)
            for pth in (d, path):
                try:
                    os.chown(pth, pw.pw_uid, pw.pw_gid)
                except OSError:
                    pass
        else:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        return self._autostart_enabled()

    @staticmethod
    def _secure_boot():
        p = ("/sys/firmware/efi/efivars/"
             "SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c")
        try:
            with open(p, "rb") as f:
                data = f.read()
            if len(data) >= 5:
                return "开启" if data[4] == 1 else "关闭"
        except OSError:
            pass
        return "未知"

    @staticmethod
    def _module_loaded():
        try:
            with open("/proc/modules") as f:
                return any(l.startswith("clevo_cc ") for l in f)
        except OSError:
            return False


def make_server(state: State, port: int = 8632):
    Handler.state = state
    Handler.port = port
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.daemon_threads = True
    return httpd
