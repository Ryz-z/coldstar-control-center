# SPDX-License-Identifier: GPL-2.0+
"""Keyboard backlight controller: colors, brightness, hardware effects."""

from __future__ import annotations

import logging
import threading

from . import clevo
from .clevo import (
    ClevoBridge, ClevoError,
    KB_TYPE_FIXED, KB_TYPE_1_ZONE_RGB, KB_TYPE_3_ZONE_RGB, KB_TYPE_PER_KEY_RGB,
    EFFECT_MODES,
)

log = logging.getLogger("cccd.kbdctl")

RGB_TYPES = (KB_TYPE_1_ZONE_RGB, KB_TYPE_3_ZONE_RGB, KB_TYPE_PER_KEY_RGB)


class KeyboardController:
    def __init__(self, bridge: ClevoBridge, config):
        self.bridge = bridge
        self.config = config
        self.lock = threading.RLock()
        self.error = None
        self.specs = None
        self.kb_type = None

        k = config.get("kbd", default={}) or {}
        self.on = bool(k.get("on", True))
        self.brightness = int(k.get("brightness", 160))       # 0-255 (RGB)
        self.white_level = int(k.get("white_level", 2))       # 0-2 / 0-5 (white)
        self.effect = k.get("effect", "static")
        colors = k.get("colors") or [[255, 255, 255]] * 3
        self.colors = [list(map(int, c)) for c in colors]
        while len(self.colors) < 3:
            self.colors.append(list(self.colors[0]))
        self.linked = bool(k.get("linked", True))
        self._detect()

    # ---- detection -----------------------------------------------------
    def _detect(self):
        try:
            self.specs = self.bridge.kb_specs()
            self.kb_type = self.specs["type"]
        except ClevoError as e:
            self.specs = {"type": clevo.KB_TYPE_NONE, "error": str(e)}
            self.kb_type = clevo.KB_TYPE_NONE
        log.info("keyboard type: %s (%s)", self.kb_type_name(), self.specs)

    def kb_type_name(self):
        return self.bridge.kb_type_name(self.kb_type or clevo.KB_TYPE_NONE)

    def supported(self):
        return self.kb_type not in (None, clevo.KB_TYPE_NONE)

    def is_rgb(self):
        return self.kb_type in RGB_TYPES

    def zone_count(self):
        if self.kb_type == KB_TYPE_3_ZONE_RGB:
            return 3
        if self.kb_type in (KB_TYPE_1_ZONE_RGB, KB_TYPE_PER_KEY_RGB):
            return 1
        return 0

    # ---- state ---------------------------------------------------------
    def status(self):
        with self.lock:
            return {
                "supported": self.supported(),
                "type": self.kb_type,
                "type_name": self.kb_type_name(),
                "is_rgb": self.is_rgb(),
                "zones": self.zone_count(),
                "on": self.on,
                "brightness": self.brightness,
                "brightness_pct": round(self.brightness * 100 / 255),
                "white_level": self.white_level,
                "effect": self.effect,
                "colors": [list(c) for c in self.colors],
                "linked": self.linked,
                "error": self.error,
                "specs": self.specs,
            }

    def update(self, on=None, brightness=None, brightness_pct=None,
               effect=None, colors=None, color=None, linked=None,
               white_level=None, apply=True):
        with self.lock:
            if on is not None:
                self.on = bool(on)
            if brightness is not None:
                self.brightness = max(0, min(255, int(brightness)))
            if brightness_pct is not None:
                self.brightness = max(0, min(255, int(round(float(brightness_pct) * 2.55))))
            if effect is not None:
                if effect not in EFFECT_MODES:
                    raise ValueError(f"unknown effect: {effect}")
                self.effect = effect
            if white_level is not None:
                self.white_level = max(0, int(white_level))
            if linked is not None:
                self.linked = bool(linked)
            if color is not None:
                c = [max(0, min(255, int(x))) for x in color]
                self.colors = [list(c) for _ in range(3)]
            if colors is not None:
                cols = [[max(0, min(255, int(x))) for x in c] for c in colors]
                while len(cols) < 3:
                    cols.append(list(cols[0]))
                self.colors = cols[:3]

        self._save()
        if apply:
            self.apply()
        return self.status()

    def _save(self):
        with self.lock:
            self.config.set("kbd", "on", self.on)
            self.config.set("kbd", "brightness", self.brightness)
            self.config.set("kbd", "white_level", self.white_level)
            self.config.set("kbd", "effect", self.effect)
            self.config.set("kbd", "colors", self.colors)
            self.config.set("kbd", "linked", self.linked)

    # ---- apply ---------------------------------------------------------
    def apply(self):
        if not self.bridge.present():
            return
        with self.lock:
            on = self.on
            kind = self.kb_type
            eff = self.effect
            cols = [list(c) for c in self.colors]
            bright = self.brightness
            wl = self.white_level
        try:
            if kind == KB_TYPE_FIXED:
                self.bridge.kb_set_white_brightness(0 if not on else wl)
                self.error = None
                return
            if kind not in RGB_TYPES:
                # unknown keyboard - skip silently
                return
            if not on:
                self.bridge.kb_enable(False)
                # extra insurance: also zero the brightness so the LEDs go
                # dark even on firmware revisions that ignore the status bit
                try:
                    self.bridge.kb_set_brightness(0)
                except ClevoError:
                    pass
                self.error = None
                return
            self.bridge.kb_enable(True)
            # NOTE: brightness FIRST, effect/colors LAST. The firmware's
            # brightness command snaps the keyboard back to "custom" display,
            # so sending it after the effect silently killed every light
            # effect (breathe/cycle/wave all looked like static).
            self.bridge.kb_set_brightness(bright)
            if eff == "static":
                zones = self.zone_count()
                for z in range(zones):
                    self.bridge.kb_set_zone_color(z, cols[z], kind)
            else:
                self.bridge.kb_set_mode(EFFECT_MODES[eff])
            self.error = None
        except ClevoError as e:
            self.error = str(e)
            log.warning("keyboard apply failed: %s", e)

    def apply_on_start(self):
        if self.config.get("kbd", "apply_on_start", default=True):
            self.apply()
