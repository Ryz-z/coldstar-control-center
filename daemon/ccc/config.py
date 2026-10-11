# SPDX-License-Identifier: GPL-2.0+
"""Persistent configuration for the Control Center daemon."""

from __future__ import annotations

import copy
import json
import logging
import os
import tempfile
import threading

log = logging.getLogger("cccd.config")

CONFIG_DIR = "/var/lib/colorful-control-center"
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "fan": {
        "mode": "auto",          # auto|silent|balanced|performance|full|custom
        "curve": [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]],
        "manual": [50, 50, 50],
        "linked": True,
        "safety_temp": 90,
    },
    "kbd": {
        "apply_on_start": True,
        "on": True,
        "brightness": 160,       # 0-255
        "white_level": 2,        # 0-2 (or 0-5 on older keyboards)
        "effect": "static",
        "colors": [[255, 255, 255], [255, 255, 255], [255, 255, 255]],
        "linked": True,
    },
    "perf": {
        "apply_on_start": False,
        "profile": None,         # 0-3 or None
    },
    "daemon": {
        "poll_ms": 1000,
        "restore_auto_on_stop": True,
    },
}

PRESET_CURVES = {
    "silent": [[45, 25], [58, 28], [68, 36], [78, 52], [88, 78], [95, 100]],
    "balanced": [[35, 25], [50, 32], [60, 45], [70, 60], [80, 80], [90, 100]],
    "performance": [[30, 35], [45, 50], [58, 65], [68, 80], [78, 95], [88, 100]],
}


def _deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: str = CONFIG_PATH):
        self.path = path
        self.lock = threading.RLock()
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        with self.lock:
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    disk = json.load(f)
                self.data = _deep_merge(DEFAULTS, disk)
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as e:
                log.warning("config load failed: %s (using defaults)", e)

    def save(self):
        with self.lock:
            data = copy.deepcopy(self.data)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path),
                                       prefix=".config-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError as e:
            log.warning("config save failed: %s", e)

    def get(self, *keys, default=None):
        with self.lock:
            node = self.data
            for k in keys:
                if not isinstance(node, dict) or k not in node:
                    return default
                node = node[k]
            return copy.deepcopy(node)

    def set(self, *keys_and_value):
        """set("fan", "mode", "auto") - last argument is the value."""
        *keys, value = keys_and_value
        with self.lock:
            node = self.data
            for k in keys[:-1]:
                node = node.setdefault(k, {})
            node[keys[-1]] = value
            self.save()
