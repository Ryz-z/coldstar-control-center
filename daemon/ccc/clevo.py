# SPDX-License-Identifier: GPL-2.0+
"""Clevo Control Center bridge - transport layer to /dev/clevo_cc."""

from __future__ import annotations

import os
import struct
import threading
import time
import logging

log = logging.getLogger("cccd.clevo")

DEV = "/dev/clevo_cc"

# ---- firmware commands -------------------------------------------------
CMD_GET_EVENT = 0x01
CMD_GET_WEBCAM_SW = 0x06
CMD_GET_FLIGHTMODE_SW = 0x07
CMD_GET_TOUCHPAD_SW = 0x09
CMD_GET_SPECS = 0x0D
CMD_GET_KB_WHITE_LEDS = 0x3D
CMD_GET_BIOS_FEATURES_1 = 0x52
CMD_GET_FANINFO1 = 0x63
CMD_GET_FANINFO2 = 0x64
CMD_GET_FANINFO3 = 0x6E
CMD_GET_BIOS_FEATURES_2 = 0x7A

CMD_SET_FLIGHTMODE_SW = 0x20
CMD_SET_WEBCAM_SW = 0x22
CMD_SET_KB_WHITE_LEDS = 0x27
CMD_SET_TOUCHPAD_SW = 0x2A
CMD_SET_EVENTS_ENABLED = 0x46
CMD_SET_KB_RGB_LEDS = 0x67
CMD_SET_FANSPEED_VALUE = 0x68
CMD_SET_FANSPEED_AUTO = 0x69
CMD_OPT = 0x79

# feature bits of GET_BIOS_FEATURES_1
BIOS_F1_WHITE_ONLY_KB = 0x40000000
BIOS_F1_3_ZONE_RGB_KB = 0x00400000
# feature bits of GET_BIOS_FEATURES_2
BIOS_F2_WHITE_KB_MAX_5 = 0x4000

# keyboard RGB sub-commands
ZONE_SUB = [0xF0000000, 0xF1000000, 0xF2000000, 0xF3000000]
SUB_RGB_BRIGHTNESS = 0xF4000000
KB_STATUS_ON = 0xE0000000 | 0x07F001
KB_STATUS_OFF = 0xE0000000 | 0x003001

# keyboard backlight types (from GET_SPECS buffer[0x0f])
KB_TYPE_NONE = 0x00
KB_TYPE_FIXED = 0x01        # white only
KB_TYPE_3_ZONE_RGB = 0x02
KB_TYPE_1_ZONE_RGB = 0x06
KB_TYPE_PER_KEY_RGB = 0xF3

# hardware effect mode values (CLEVO_CMD_SET_KB_RGB_LEDS argument)
EFFECT_MODES = {
    "static": 0x00000000,
    "breathe": 0x1002A000,
    "cycle": 0x33010000,
    "dance": 0x80000000,
    "flash": 0xA0000000,
    "random": 0x70000000,
    "tempo": 0x90000000,
    "wave": 0xB0000000,
}

# magic commands of the kernel bridge
MAGIC_VERSION = 0xFFFF0001
MAGIC_GET_IF = 0xFFFF0002
MAGIC_REPROBE = 0xFFFF0003
MAGIC_FORCE_IF = 0xFFFF0004

IF_NAMES = {0: "none", 1: "acpi", 2: "wmi"}

FAN_MIN_DUTY = 64  # 25% of 255 - EC will not go below this in manual mode
FAN_MAX_DUTY = 255

PERF_PROFILES = {
    "powersave": 0,
    "quiet": 1,
    "performance": 2,
    "entertainment": 3,
}


class ClevoError(OSError):
    """Raised when a firmware call fails."""


class ClevoBridge:
    """Thread-safe client of /dev/clevo_cc."""

    def __init__(self, dev: str = DEV, fake: bool = False):
        self.dev = dev
        self.fake = fake
        self.lock = threading.RLock()
        self.last_error: str | None = None

    # -- low level -------------------------------------------------------
    def present(self) -> bool:
        return self.fake or os.path.exists(self.dev)

    def call(self, cmd: int, arg: int = 0, want_buf: bool = False):
        """Returns (status, result, raw_buffer_or_None)."""
        if self.fake:
            return self._fake_call(cmd, arg, want_buf)
        if not os.path.exists(self.dev):
            self.last_error = "内核模块未加载 (/dev/clevo_cc 不存在)"
            raise ClevoError(self.last_error)
        with self.lock:
            try:
                fd = os.open(self.dev, os.O_RDWR)
            except OSError as e:
                self.last_error = f"无法打开 {self.dev}: {e}"
                raise ClevoError(self.last_error) from e
            try:
                os.write(fd, struct.pack("<II", cmd, arg))
                data = os.read(fd, 16)
                status, result, via, version = struct.unpack("<iIII", data)
                buf = None
                if want_buf:
                    try:
                        buf = os.read(fd, 64)
                    except OSError:
                        buf = None
                self.last_error = None if status == 0 else f"固件返回错误 {status}"
                return status, result, buf
            except OSError as e:
                self.last_error = f"固件调用失败 cmd={cmd:#x}: {e}"
                raise ClevoError(self.last_error) from e
            finally:
                os.close(fd)

    def _fake_call(self, cmd, arg, want_buf):
        if cmd == MAGIC_VERSION:
            return 0, 0x00010000, None
        if cmd == MAGIC_GET_IF:
            return 0, 1, None
        if cmd == MAGIC_REPROBE or cmd == MAGIC_FORCE_IF:
            return 0, 1, None
        if cmd == CMD_GET_SPECS:
            buf = bytes([0] * 16 + [KB_TYPE_3_ZONE_RGB] + [0] * 47)
            return 0, 0, buf if want_buf else None
        if cmd == CMD_GET_BIOS_FEATURES_1:
            return 0, BIOS_F1_3_ZONE_RGB_KB, None
        if cmd in (CMD_GET_FANINFO1, CMD_GET_FANINFO2, CMD_GET_FANINFO3):
            return 0, (2800 << 16) | 64, None
        if cmd in (CMD_SET_FANSPEED_VALUE, CMD_SET_FANSPEED_AUTO,
                   CMD_SET_KB_RGB_LEDS, CMD_SET_KB_WHITE_LEDS, CMD_OPT,
                   CMD_SET_EVENTS_ENABLED):
            return 0, 0xFFFFFFFE, None
        return 0, 0, None

    # -- info ------------------------------------------------------------
    def module_version(self):
        return self.call(MAGIC_VERSION)[1]

    def active_interface(self):
        return self.call(MAGIC_GET_IF)[1]

    def reprobe(self):
        return self.call(MAGIC_REPROBE)[1]

    def force_interface(self, iface: int):
        return self.call(MAGIC_FORCE_IF, iface)[1]

    # -- keyboard specs --------------------------------------------------
    def kb_specs(self) -> dict:
        """Detect keyboard backlight type. Returns dict(type, raw_specs, features1, white_max_5)."""
        info = {
            "type": KB_TYPE_NONE,
            "raw_specs": None,
            "features1": None,
            "white_max_5": False,
        }
        # primary: GET_SPECS buffer, offset 0x0f
        for _ in range(3):
            try:
                st, res, buf = self.call(CMD_GET_SPECS, 0, want_buf=True)
            except ClevoError:
                break
            if st == 0 and buf and len(buf) > 0x0F:
                info["raw_specs"] = buf.hex()
                t = buf[0x0F]
                if t != 0:
                    info["type"] = t
                    break
            time.sleep(0.05)
        # fallback / secondary: BIOS features
        try:
            st, res, _ = self.call(CMD_GET_BIOS_FEATURES_1, 0)
            if st == 0:
                info["features1"] = res
                if info["type"] == KB_TYPE_NONE:
                    if res & BIOS_F1_3_ZONE_RGB_KB:
                        info["type"] = KB_TYPE_3_ZONE_RGB
                    elif res & BIOS_F1_WHITE_ONLY_KB:
                        info["type"] = KB_TYPE_FIXED
        except ClevoError:
            pass
        try:
            st, res, _ = self.call(CMD_GET_BIOS_FEATURES_2, 0)
            if st == 0 and (res & BIOS_F2_WHITE_KB_MAX_5):
                info["white_max_5"] = True
        except ClevoError:
            pass
        return info

    def kb_type_name(self, t: int) -> str:
        return {
            KB_TYPE_NONE: "无",
            KB_TYPE_FIXED: "单色白光",
            KB_TYPE_3_ZONE_RGB: "三区 RGB",
            KB_TYPE_1_ZONE_RGB: "单区 RGB",
            KB_TYPE_PER_KEY_RGB: "单键 RGB",
        }.get(t, f"未知({t:#x})")

    # -- fan ops ---------------------------------------------------------
    def set_fan_speed(self, duties) -> tuple:
        """duties: iterable of up to 3 duty values (0-255). Returns (status, result)."""
        d = list(duties) + [0] * (3 - len(duties))
        value = (d[0] & 0xFF) | ((d[1] & 0xFF) << 8) | ((d[2] & 0xFF) << 16)
        st, res, _ = self.call(CMD_SET_FANSPEED_VALUE, value)
        return st, res

    def fan_auto(self) -> tuple:
        st, res, _ = self.call(CMD_SET_FANSPEED_AUTO, 0)
        return st, res

    def fan_info(self, index: int) -> dict:
        cmd = [CMD_GET_FANINFO1, CMD_GET_FANINFO2, CMD_GET_FANINFO3][index]
        st, res, _ = self.call(cmd, 0)
        return {
            "status": st,
            "raw": res,
            "rpm": (res >> 16) & 0xFFFF if st == 0 else None,
            "duty": res & 0xFF if st == 0 else None,
        }

    # -- keyboard ops ----------------------------------------------------
    def kb_set_brightness(self, value: int) -> tuple:
        st, res, _ = self.call(CMD_SET_KB_RGB_LEDS,
                               SUB_RGB_BRIGHTNESS | (value & 0xFF))
        return st, res

    def kb_set_white_brightness(self, value: int) -> tuple:
        st, res, _ = self.call(CMD_SET_KB_WHITE_LEDS, value & 0xFF)
        return st, res

    def kb_set_zone_color(self, zone: int, rgb, kb_type: int = KB_TYPE_3_ZONE_RGB) -> tuple:
        """rgb: (r, g, b) 0-255 each. Wire order is B,R,G per Clevo protocol."""
        r, g, b = (max(0, min(255, int(x))) for x in rgb)
        if kb_type == KB_TYPE_1_ZONE_RGB:
            r = (180 * r) // 255
            b = (200 * b) // 255
        cset = (b << 16) | (r << 8) | g
        arg = ZONE_SUB[zone & 0x3] | cset
        st, res, _ = self.call(CMD_SET_KB_RGB_LEDS, arg)
        return st, res

    def kb_set_mode(self, mode_value: int) -> tuple:
        st, res, _ = self.call(CMD_SET_KB_RGB_LEDS, mode_value)
        return st, res

    def kb_enable(self, on: bool) -> tuple:
        st, res, _ = self.call(CMD_SET_KB_RGB_LEDS,
                               KB_STATUS_ON if on else KB_STATUS_OFF)
        return st, res

    def kb_get_white(self):
        st, res, _ = self.call(CMD_GET_KB_WHITE_LEDS, 0)
        return res if st == 0 else None

    # -- misc ------------------------------------------------------------
    def set_perf_profile(self, profile: int) -> tuple:
        st, res, _ = self.call(CMD_OPT, 0x19000000 | (profile & 0xFF))
        return st, res

    def events_enabled(self, on: bool) -> tuple:
        st, res, _ = self.call(CMD_SET_EVENTS_ENABLED, 1 if on else 0)
        return st, res
