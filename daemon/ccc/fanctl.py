# SPDX-License-Identifier: GPL-2.0+
"""Fan control engine: modes, curves, safety, manual control.

Anti-oscillation logic (fixes the "revving" / 一惊一乍 behaviour):
- the temperature used for curve lookup is smoothed with a fast-attack /
  slow-release EMA, so a brief spike (50 -> 70 -> 50) only nudges the fan;
- the duty target is rate-limited both upwards and downwards, so the fans
  ramp and release gradually instead of jumping up and straight back down;
- the raw (unsmoothed) temperature still drives the hard safety override
  (>= safety_temp => full speed immediately) and the UI display.
"""

from __future__ import annotations

import logging
import threading
import time

from . import clevo
from .clevo import ClevoBridge, ClevoError, FAN_MIN_DUTY, FAN_MAX_DUTY

log = logging.getLogger("cccd.fanctl")

TICK = 1.5           # evaluation period (s)
RESEND_EVERY = 20    # re-send unchanged duty every N ticks
CHANGE_EPS = 4       # minimum duty value change to trigger a write

# anti-oscillation tuning
SMOOTH_UP = 0.35     # EMA factor while temperature rises (higher = faster)
SMOOTH_DOWN = 0.10   # EMA factor while temperature falls (slow release)
RATE_UP = 7.0        # max duty increase per tick (percentage points)
RATE_DOWN = 3.5      # max duty decrease per tick (percentage points)

MODES = ("auto", "silent", "balanced", "performance", "full", "custom", "manual")
CURVE_MODES = ("silent", "balanced", "performance", "custom")


def interp_curve(points, temp_c):
    """Piecewise-linear interpolation of [[temp, duty_pct], ...]."""
    pts = sorted(([float(t), float(d)] for t, d in points), key=lambda p: p[0])
    if not pts:
        return 50.0
    if temp_c <= pts[0][0]:
        return pts[0][1]
    if temp_c >= pts[-1][0]:
        return pts[-1][1]
    for (t0, d0), (t1, d1) in zip(pts, pts[1:]):
        if t0 <= temp_c <= t1:
            if t1 == t0:
                return d1
            k = (temp_c - t0) / (t1 - t0)
            return d0 + k * (d1 - d0)
    return pts[-1][1]


def pct_to_value(pct: float) -> int:
    pct = max(0.0, min(100.0, float(pct)))
    v = int(round(pct * FAN_MAX_DUTY / 100.0))
    if v <= 0:
        return 0
    return max(FAN_MIN_DUTY, min(FAN_MAX_DUTY, v))


class FanController(threading.Thread):
    daemon = True

    def __init__(self, bridge: ClevoBridge, monitor, config):
        super().__init__(name="fan-engine")
        self.bridge = bridge
        self.monitor = monitor
        self.config = config
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.mode = config.get("fan", "mode", default="auto") or "auto"
        self.curve = config.get("fan", "curve", default=[])
        self.manual = config.get("fan", "manual", default=[128, 128, 128])
        self.linked = bool(config.get("fan", "linked", default=True))
        self.safety_temp = int(config.get("fan", "safety_temp", default=90))
        self.duty_value = None       # last written duty value (0-255)
        self.duty_pct = None
        self.source_temp = None
        self.error = None
        self._ticks = 0
        # anti-oscillation state
        self.smooth_temp = None
        self.duty_target = None

    # ---- public API ----------------------------------------------------
    def status(self):
        with self.lock:
            return {
                "mode": self.mode,
                "duty_value": self.duty_value,
                "duty_pct": self.duty_pct,
                "curve": list(self.curve),
                "manual": list(self.manual),
                "linked": self.linked,
                "safety_temp": self.safety_temp,
                "source_temp": self.source_temp,
                "error": self.error,
                "active": self.mode != "auto",
            }

    def set_mode(self, mode: str, curve=None, manual=None, linked=None):
        if mode not in MODES:
            raise ValueError(f"unknown fan mode: {mode}")
        with self.lock:
            self.mode = mode
            if curve is not None:
                self.curve = [[float(t), float(d)] for t, d in curve]
            if manual is not None:
                self.manual = [int(x) for x in manual][:3]
            if linked is not None:
                self.linked = bool(linked)
            self.config.set("fan", "mode", self.mode)
            self.config.set("fan", "curve", self.curve)
            self.config.set("fan", "manual", self.manual)
            self.config.set("fan", "linked", self.linked)
            # fresh start for the anti-oscillation state on explicit mode switches
            self.smooth_temp = None
            self.duty_target = None
        self._ticks = 0
        self._apply_now(force=True)

    # ---- engine --------------------------------------------------------
    def run(self):
        self._apply_now(force=True)
        while not self.stop_event.wait(TICK):
            self._ticks += 1
            try:
                self._apply_now(force=False)
            except Exception as e:  # never die
                log.exception("fan engine error: %s", e)

    def stop(self, restore_auto=True):
        self.stop_event.set()
        if restore_auto and self.config.get("daemon", "restore_auto_on_stop", default=True):
            try:
                self.bridge.fan_auto()
            except Exception:
                pass

    def _temps(self):
        cpu = self.monitor.cpu_temp()
        gpu = (self.monitor.gpu_info() or {}).get("temp")
        vals = [t for t in (cpu, gpu) if isinstance(t, (int, float))]
        return cpu, gpu, (max(vals) if vals else None)

    def _curve_duty(self, curve, tmax: float) -> float:
        """Duty for curve modes with temperature smoothing + rate limiting."""
        if self.smooth_temp is None:
            self.smooth_temp = float(tmax)
        else:
            alpha = SMOOTH_UP if tmax > self.smooth_temp else SMOOTH_DOWN
            self.smooth_temp += alpha * (float(tmax) - self.smooth_temp)

        duty = interp_curve(curve, self.smooth_temp)

        # hard safety override: raw temperature wins, no damping
        if tmax >= self.safety_temp:
            self.duty_target = 100.0
            return 100.0

        if self.duty_target is None:
            self.duty_target = float(duty)
        else:
            delta = float(duty) - self.duty_target
            if delta > RATE_UP:
                delta = RATE_UP
            elif delta < -RATE_DOWN:
                delta = -RATE_DOWN
            self.duty_target = self.duty_target + delta
        return self.duty_target

    def _apply_now(self, force=False):
        with self.lock:
            mode = self.mode
            curve = list(self.curve)
            manual = list(self.manual)

        if mode == "auto":
            if force or self.duty_value is not None:
                try:
                    self.bridge.fan_auto()
                    self.error = None
                    self.duty_value = None
                    self.duty_pct = None
                except ClevoError as e:
                    self.error = str(e)
            return

        cpu, gpu, tmax = self._temps()
        self.source_temp = tmax

        if mode == "full":
            duty_pct = 100.0
        elif mode == "manual":
            vals = [pct_to_value(x) for x in manual]
            duty_pct = max(manual)
        elif mode in CURVE_MODES:
            if tmax is None:
                duty_pct = 60.0  # sensor read failure: stay moderately high
            else:
                duty_pct = self._curve_duty(curve, tmax)
            val = pct_to_value(duty_pct)
            vals = [val, val, val]
        else:
            return

        if mode == "full":
            vals = [FAN_MAX_DUTY] * 3

        current = self.duty_value
        need_send = (
            force
            or current is None
            or abs(max(vals) - current) >= CHANGE_EPS
            or (self._ticks % RESEND_EVERY == 0)
        )
        if not need_send:
            self.duty_pct = round(duty_pct, 1)
            return

        try:
            self.bridge.set_fan_speed(vals)
            self.error = None
            self.duty_value = max(vals)
            self.duty_pct = round(duty_pct, 1)
        except ClevoError as e:
            self.error = str(e)
