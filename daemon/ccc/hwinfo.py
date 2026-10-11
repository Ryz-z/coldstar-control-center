# SPDX-License-Identifier: GPL-2.0+
"""System info: temperatures, fan RPM, load, battery, GPU."""

from __future__ import annotations

import glob
import logging
import os
import re
import subprocess
import threading
import time

log = logging.getLogger("cccd.hwinfo")

HWMON = "/sys/class/hwmon"


def _read(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def _read_int(path: str, default=None):
    v = _read(path)
    if v is None:
        return default
    try:
        return int(v)
    except ValueError:
        return default


def _hwmon_dirs():
    return sorted(glob.glob(os.path.join(HWMON, "hwmon*")),
                  key=lambda p: int(re.sub(r"\D", "", p) or 0))


class SystemMonitor:
    """Collects system-wide information. Cheap, call from request threads."""

    def __init__(self, fake: bool = False):
        self.fake = fake
        self._prev_cpu = None
        self._gpu_cache = {"ts": 0, "data": None}
        self._gpu_lock = threading.Lock()
        self.fan_paths = self._discover_fans()

    # ---- fans ----------------------------------------------------------
    def _discover_fans(self):
        fans = []
        for d in _hwmon_dirs():
            name = _read(os.path.join(d, "name"))
            if name in ("acpi_fan", "clevo", "uniwill"):
                p = os.path.join(d, "fan1_input")
                if os.path.exists(p):
                    fans.append(p)
        return fans

    def fan_rpms(self):
        out = []
        for p in self.fan_paths:
            v = _read_int(p)
            if v is not None and v >= 0:
                out.append(v)
        return out

    # ---- temperatures --------------------------------------------------
    def cpu_temp(self):
        best = None
        for d in _hwmon_dirs():
            if _read(os.path.join(d, "name")) != "coretemp":
                continue
            # package temperature preferred
            for i in range(1, 32):
                lbl = _read(os.path.join(d, f"temp{i}_label"))
                val = _read_int(os.path.join(d, f"temp{i}_input"))
                if val is None:
                    continue
                if lbl == "Package id 0":
                    return val // 1000
                best = max(best or 0, val)
        return best // 1000 if best else None

    def nvme_temps(self):
        if self.fake:
            return [41, 44]
        temps = []
        for d in _hwmon_dirs():
            if _read(os.path.join(d, "name")) != "nvme":
                continue
            v = _read_int(os.path.join(d, "temp1_input"))
            if v is not None:
                temps.append(v // 1000)
        return temps

    def other_zones(self):
        """SEN1..SEN4 / TCPU etc. from Clevo EC thermal zones."""
        if self.fake:
            return {"SEN1": 55, "SEN2": 42, "SEN3": 50, "SEN4": 47, "TCPU": 58}
        out = {}
        for z in sorted(glob.glob("/sys/class/thermal/thermal_zone*")):
            t = _read(os.path.join(z, "type"))
            v = _read_int(os.path.join(z, "temp"))
            if t and v is not None and t in ("SEN1", "SEN2", "SEN3", "SEN4",
                                             "TCPU", "TCPU_PCI"):
                out[t] = v // 1000
        return out

    # ---- GPU -----------------------------------------------------------
    def gpu_info(self):
        """Returns dict(temp, util) - cached 2s, uses nvidia-smi if present."""
        with self._gpu_lock:
            now = time.time()
            if now - self._gpu_cache["ts"] < 2 and self._gpu_cache["data"] is not None:
                return self._gpu_cache["data"]
        data = {"temp": None, "util": None, "name": None}
        if self.fake:
            data = {"temp": 52, "util": 17, "name": "NVIDIA GeForce RTX 4060 Laptop GPU"}
        else:
            try:
                r = subprocess.run(
                    ["nvidia-smi",
                     "--query-gpu=temperature.gpu,utilization.gpu,name",
                     "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=2)
                if r.returncode == 0 and r.stdout.strip():
                    parts = [p.strip() for p in r.stdout.strip().splitlines()[0].split(",")]
                    if len(parts) >= 3:
                        data["temp"] = int(parts[0]) if parts[0].isdigit() else None
                        data["util"] = int(parts[1]) if parts[1].isdigit() else None
                        data["name"] = parts[2]
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
        with self._gpu_lock:
            self._gpu_cache = {"ts": time.time(), "data": data}
        return data

    # ---- load ----------------------------------------------------------
    def cpu_load(self):
        if self.fake:
            return int(20 + 10 * (1 + __import__("math").sin(time.time() / 5)))
        try:
            with open("/proc/stat") as f:
                line = f.readline()
        except OSError:
            return None
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        total = sum(parts)
        prev = self._prev_cpu
        self._prev_cpu = (idle, total)
        if not prev:
            return None
        di, dt = idle - prev[0], total - prev[1]
        if dt <= 0:
            return None
        return max(0, min(100, int(100 * (dt - di) / dt)))

    def cpu_freq_mhz(self):
        if self.fake:
            return 3200
        vals = []
        for p in glob.glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"):
            v = _read_int(p)
            if v:
                vals.append(v)
        if not vals:
            return None
        return int(sum(vals) / len(vals) / 1000)

    def memory(self):
        if self.fake:
            return {"total_gb": 32.0, "used_gb": 12.4, "pct": 39}
        d = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    k, _, rest = line.partition(":")
                    d[k] = int(rest.split()[0])
        except (OSError, ValueError):
            return None
        total = d.get("MemTotal", 0)
        avail = d.get("MemAvailable", 0)
        if not total:
            return None
        return {
            "total_gb": round(total / 1048576, 1),
            "used_gb": round((total - avail) / 1048576, 1),
            "pct": int(100 * (total - avail) / total),
        }

    # ---- battery -------------------------------------------------------
    def battery(self):
        base = "/sys/class/power_supply/BAT0"
        if not os.path.isdir(base):
            return {"present": False}
        cap = _read_int(os.path.join(base, "capacity"))
        status = _read(os.path.join(base, "status"), "Unknown")
        power = _read_int(os.path.join(base, "power_now"))
        if power is None:
            curr = _read_int(os.path.join(base, "current_now"))
            volt = _read_int(os.path.join(base, "voltage_now"))
            if curr is not None and volt is not None:
                power = abs(curr) * volt // 1000000  # uA * uV -> W(uW)
        ac = _read(os.path.join("/sys/class/power_supply/AC", "online"))
        return {
            "present": True,
            "capacity": cap,
            "status": status,
            "power_w": round(power / 1e6, 1) if power else None,
            "ac": ac == "1",
        }

    def charge_thresholds(self):
        base = "/sys/class/power_supply/BAT0"
        end = _read_int(os.path.join(base, "charge_control_end_threshold"))
        start = _read_int(os.path.join(base, "charge_control_start_threshold"))
        if end is None:
            return None
        return {"start": start, "end": end}

    # ---- identity ------------------------------------------------------
    def identity(self):
        def dmi(f):
            return _read(f"/sys/class/dmi/id/{f}", "?")
        uptime_s = None
        try:
            with open("/proc/uptime") as f:
                uptime_s = int(float(f.read().split()[0]))
        except (OSError, ValueError):
            pass
        return {
            "hostname": os.uname().nodename,
            "kernel": os.uname().release,
            "product": dmi("product_name"),
            "vendor": dmi("sys_vendor"),
            "board": dmi("board_name"),
            "bios": dmi("bios_version"),
            "uptime_s": uptime_s,
        }

    def snapshot(self):
        return {
            "cpu_temp": self.cpu_temp(),
            "gpu": self.gpu_info(),
            "nvme_temps": self.nvme_temps(),
            "zones": self.other_zones(),
            "fan_rpm": self.fan_rpms(),
            "cpu_load": self.cpu_load(),
            "cpu_mhz": self.cpu_freq_mhz(),
            "mem": self.memory(),
            "battery": self.battery(),
        }
