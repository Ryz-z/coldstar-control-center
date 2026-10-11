#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0+
"""隐星控制中心 桌面外壳：WebKit 窗口 + 系统托盘（QQ 式后台驻留）。

v1.0.4 加固版:
  - 全量文件日志: ~/.cache/colorful-control-center/gui.log（问题排查黑匣子）
  - hold() 防自动退出保险 + 看门狗（窗口异常消失时自动救回/降级浏览器）
  - 启动顺序调整: 先显示窗口，再初始化托盘
  - --hidden: 启动时直接驻留托盘（开机自启动用）; --no-tray: 禁用托盘（排查用）

行为:
  - 点击窗口关闭按钮 = 最小化到系统托盘，不会退出
  - 托盘（右键）菜单: 打开控制中心 / 工作模式(性能/平衡/安静/自动) / 退出控制中心
  - 若系统缺少托盘组件，则维持旧行为（关窗即退出），不影响使用
"""

import datetime
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request

PORT = os.environ.get("CCC_PORT", "8632")
BASE_URL = f"http://127.0.0.1:{PORT}/"
APP_ID = "org.colorful.ControlCenter"
PRGNAME = "colorful-control-center"
ICON_NAME = "colorful-control-center"
ICON_SVG = "/usr/share/icons/hicolor/scalable/apps/colorful-control-center.svg"

LOG_DIR = os.path.expanduser("~/.cache/colorful-control-center")
LOG_FILE = os.path.join(LOG_DIR, "gui.log")

MODE_LABELS = [
    ("performance", "性能模式"),
    ("balanced", "平衡模式"),
    ("silent", "安静模式"),
    ("auto", "自动 (EC)"),
]

_LOG_LOCK = threading.Lock()


def _log(msg):
    """Best-effort file logger; never raises."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with _LOG_LOCK:
            try:
                if os.path.getsize(LOG_FILE) > 524288:
                    os.replace(LOG_FILE, LOG_FILE + ".1")
            except OSError:
                pass
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.datetime.now():%H:%M:%S.%f}] {msg}\n")
    except Exception:
        pass


def _install_excepthooks():
    def _hook(etype, value, tb):
        _log("UNCAUGHT: " + "".join(
            traceback.format_exception(etype, value, tb)).rstrip())
    sys.excepthook = _hook
    try:
        def _thook(args):
            _log("THREAD-UNCAUGHT: " + "".join(
                traceback.format_exception(args.exc_type, args.exc_value,
                                           args.exc_traceback)).rstrip())
        threading.excepthook = _thook
    except Exception:
        pass


def open_browser(url=BASE_URL):
    _log(f"open_browser: {url}")
    try:
        subprocess.Popen(["xdg-open", url],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        _log(f"open_browser failed: {e}")


def api(path, method="GET", body=None, timeout=3.0):
    """本地守护进程 JSON 客户端；永不抛异常，失败返回 None。"""
    try:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(BASE_URL.rstrip("/") + path,
                                     data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def main():
    _install_excepthooks()
    args = list(sys.argv[1:])
    start_hidden = "--hidden" in args
    _log("=" * 60)
    _log(f"start: pid={os.getpid()} args={args} "
         f"session={os.environ.get('XDG_SESSION_TYPE')} "
         f"backend_env={os.environ.get('GDK_BACKEND')} "
         f"wayland={os.environ.get('WAYLAND_DISPLAY')}")

    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gio, GLib
        _log("import: Gtk/Gio/GLib ok")
    except Exception:
        _log("import: GTK unavailable:\n" + traceback.format_exc())
        open_browser()
        return 0

    try:
        GLib.set_prgname(PRGNAME)
        GLib.set_application_name("隐星控制中心")
    except Exception:
        pass

    WebKit2 = None
    try:
        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2  # noqa: F401
        _log("import: WebKit2 ok")
    except Exception as e:
        WebKit2 = None
        _log(f"import: WebKit2 unavailable ({e})")

    AppIndicator = None
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: F401
        _log("import: AyatanaAppIndicator3 ok")
    except Exception:
        try:
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator  # noqa: F401
            _log("import: AppIndicator3 ok")
        except Exception as e:
            AppIndicator = None
            _log(f"import: AppIndicator unavailable ({e})")

    class ControlCenterApp(Gtk.Application):
        def __init__(self):
            super().__init__(application_id=APP_ID,
                             flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
            self.window = None
            self.indicator = None
            self.radio_items = {}
            self.updating_menu = False
            self.menu_ready = False
            self.start_hidden = False
            self.no_tray = False
            self.notified = False
            self.load_retries = 0
            self.want_visible = False
            self.watchdog_fails = 0
            self.held = False

        # ---------- lifecycle ----------
        def do_startup(self):
            Gtk.Application.do_startup(self)
            try:
                self.hold()          # 保险：任何意外都不能把常驻进程带走
                self.held = True
                _log("startup: hold() ok")
            except Exception as e:
                _log(f"startup: hold() failed: {e}")
            try:
                st = Gtk.Settings.get_default()
                if st is not None:
                    st.set_property("gtk-application-prefer-dark-theme", True)
            except Exception:
                pass

        def do_command_line(self, command_line):
            try:
                argv = list(command_line.get_arguments()[1:])
                self.start_hidden = "--hidden" in argv
                self.no_tray = "--no-tray" in argv
                _log(f"command_line: argv={argv} hidden={self.start_hidden} "
                     f"no_tray={self.no_tray}")
                self.activate()
            except Exception:
                _log("command_line EXC:\n" + traceback.format_exc())
                open_browser()
            return 0

        def do_activate(self):
            _log(f"activate: begin (built={self.window is not None}, "
                 f"hidden={self.start_hidden})")
            try:
                if self.window is None:
                    self._build_window()
                    self.show_window()          # 先让窗口现身，再搭托盘
                    self._build_indicator()
                    self._start_watcher()
                    GLib.timeout_add_seconds(6, self._watchdog)
                if not self.start_hidden or self.indicator is None:
                    self.show_window()
            except Exception:
                _log("activate EXC:\n" + traceback.format_exc())
                open_browser()
            self.start_hidden = False
            _log("activate: end")

        def do_shutdown(self):
            _log("shutdown: main loop ending")
            try:
                Gtk.Application.do_shutdown(self)
            except Exception:
                pass

        def _watchdog(self):
            try:
                if self.window is None or not self.want_visible:
                    return True
                if self.window.get_visible():
                    self.watchdog_fails = 0
                    return True
                self.watchdog_fails += 1
                _log(f"watchdog: window not visible (try {self.watchdog_fails})")
                self.show_window()
                if self.watchdog_fails >= 3:
                    _log("watchdog: persistent failure, browser fallback")
                    open_browser()
                    return False
            except Exception:
                _log("watchdog EXC:\n" + traceback.format_exc())
            return True

        # ---------- window ----------
        def _build_window(self):
            _log("build_window: begin")
            win = Gtk.Window(title="隐星控制中心")
            win.set_default_size(1180, 780)
            win.set_position(Gtk.WindowPosition.CENTER)
            try:
                win.set_icon_from_file(ICON_SVG)
            except Exception:
                pass
            win.connect("delete-event", self._on_close)
            win.connect("map-event", lambda *a: _log("window: map"))
            win.connect("unmap-event", lambda *a: _log("window: unmap"))
            win.connect("destroy", lambda *a: _log("window: destroy !!"))

            if WebKit2 is not None:
                view = WebKit2.WebView()
                try:
                    s = view.get_settings()
                    s.set_enable_developer_extras(False)
                except Exception:
                    pass
                view.connect("load-failed", self._on_load_failed)
                view.connect("load-changed", self._on_load_changed)
                view.load_uri(BASE_URL)
                win.add(view)
            else:
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
                for m in ("top", "bottom", "start", "end"):
                    getattr(box, f"set_margin_{m}")(48)
                label = Gtk.Label(
                    label="当前环境缺少 WebKit 组件，无法内嵌显示界面。\n"
                          "请安装 gir1.2-webkit2-4.1；或点击下方按钮用浏览器打开。")
                btn = Gtk.Button(label="用浏览器打开控制中心")
                btn.connect("clicked", lambda *_: open_browser())
                box.add(label)
                box.add(btn)
                win.add(box)
            self.window = win
            _log("build_window: done")

        def _on_load_failed(self, view, event, uri, error):
            if self.load_retries >= 10:
                return True
            self.load_retries += 1
            _log(f"load_failed ({self.load_retries}): {error}")

            def _retry():
                try:
                    view.load_uri(uri)
                except Exception:
                    pass
                return False

            GLib.timeout_add(1500, _retry)
            return True

        def _on_load_changed(self, view, event):
            try:
                if event == WebKit2.LoadEvent.FINISHED:
                    if self.load_retries:
                        _log("load: finished")
                    self.load_retries = 0
            except Exception:
                pass

        def _on_close(self, *args):
            _log(f"close requested (indicator="
                 f"{'yes' if self.indicator is not None else 'none'})")
            if self.indicator is not None:
                self.window.hide()
                self.want_visible = False
                if not self.notified:
                    self.notified = True
                    self._notify("控制中心仍在后台运行",
                                 "右键系统托盘图标可切换工作模式，或选择退出。")
                return True      # 拦截关闭：驻留托盘
            return False         # 无托盘组件时维持旧行为（关窗即退出）

        def show_window(self):
            if self.window is None:
                return
            self.want_visible = True
            self.window.show_all()
            self.window.present()
            _log("show_window: shown")

        # ---------- tray ----------
        def _build_indicator(self):
            if AppIndicator is None or self.no_tray:
                _log(f"indicator: skipped (lib={'ok' if AppIndicator else 'no'}, "
                     f"no_tray={self.no_tray})")
                return
            try:
                ind = AppIndicator.Indicator.new(
                    "colorful-control-center", ICON_NAME,
                    AppIndicator.IndicatorCategory.APPLICATION_STATUS)
                try:
                    ind.set_icon_full(ICON_NAME, "隐星控制中心")
                except Exception:
                    try:
                        ind.set_icon_full(ICON_SVG, "隐星控制中心")
                    except Exception:
                        pass
                ind.set_status(AppIndicator.IndicatorStatus.ACTIVE)

                menu = Gtk.Menu()
                item_open = Gtk.MenuItem(label="打开控制中心")
                item_open.connect("activate", lambda *_: self.show_window())
                menu.append(item_open)
                menu.append(Gtk.SeparatorMenuItem())

                header = Gtk.MenuItem(label="工作模式")
                header.set_sensitive(False)
                menu.append(header)

                group = None
                for key, label in MODE_LABELS:
                    item = Gtk.RadioMenuItem(label="  " + label)
                    if group is None:
                        group = item
                    else:
                        item.join_group(group)
                    item.connect("toggled", self._on_mode_clicked, key)
                    menu.append(item)
                    self.radio_items[key] = item

                menu.append(Gtk.SeparatorMenuItem())
                item_quit = Gtk.MenuItem(label="退出控制中心")
                item_quit.connect("activate", lambda *_: self.quit_app())
                menu.append(item_quit)

                menu.show_all()
                ind.set_menu(menu)
                try:
                    ind.set_secondary_activate_target(item_open)
                except Exception:
                    pass
                self.indicator = ind
                _log("indicator: ready")
            except Exception:
                self.indicator = None
                _log("indicator EXC:\n" + traceback.format_exc())

        def _on_mode_clicked(self, item, key):
            if not self.menu_ready or self.updating_menu or not item.get_active():
                return
            _log(f"tray: switch mode -> {key}")
            threading.Thread(
                target=lambda: api("/api/fan", "POST", {"mode": key}),
                daemon=True).start()

        # ---------- background watcher ----------
        def _start_watcher(self):
            threading.Thread(target=self._watch_loop, daemon=True).start()

        def _watch_loop(self):
            first = True
            while True:
                data = api("/api/status", timeout=4)
                mode = None
                if data and isinstance(data.get("fans"), dict):
                    mode = (data["fans"].get("state") or {}).get("mode")
                if first:
                    _log(f"watcher: first status ok={bool(data)} mode={mode}")
                    first = False
                GLib.idle_add(self._sync_radios, mode)
                time.sleep(4)

        def _sync_radios(self, mode):
            self.updating_menu = True
            try:
                for key, item in self.radio_items.items():
                    item.set_active(bool(mode) and key == mode)
            except Exception:
                _log("sync_radios EXC:\n" + traceback.format_exc())
            finally:
                self.updating_menu = False
                self.menu_ready = True
            return False

        # ---------- misc ----------
        def _notify(self, title, body):
            try:
                n = Gio.Notification.new(title)
                n.set_body(body)
                try:
                    n.set_icon(Gio.ThemedIcon.new(ICON_NAME))
                except Exception:
                    pass
                self.send_notification("console", n)
            except Exception:
                pass

        def quit_app(self):
            _log("quit_app: user requested")
            try:
                if self.indicator is not None:
                    self.indicator.set_status(
                        AppIndicator.IndicatorStatus.PASSIVE)
            except Exception:
                pass
            self.want_visible = False
            try:
                if self.held:
                    self.release()
            except Exception:
                pass
            self.quit()

    app = ControlCenterApp()
    rc = app.run([sys.argv[0]] + args)
    _log(f"run() returned: {rc}")
    return rc


if __name__ == "__main__":
    sys.exit(main() or 0)
