#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0+
"""colorful-control-center daemon (runs as root via systemd).

Serves the local UI + JSON API on 127.0.0.1 and drives the Clevo fan and
keyboard hardware through /dev/clevo_cc.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ccc.clevo import ClevoBridge            # noqa: E402
from ccc.config import Config, CONFIG_PATH   # noqa: E402
from ccc.fanctl import FanController         # noqa: E402
from ccc.hwinfo import SystemMonitor         # noqa: E402
from ccc.kbdctl import KeyboardController    # noqa: E402
from ccc import server as ccserver           # noqa: E402

log = logging.getLogger("cccd")


def main(argv=None):
    ap = argparse.ArgumentParser(description="colorful-control-center daemon")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("CCC_PORT", "8632")))
    ap.add_argument("--config", default=os.environ.get("CCC_CONFIG", CONFIG_PATH))
    ap.add_argument("--fake", action="store_true",
                    default=os.environ.get("CCC_FAKE", "") == "1")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s")
    log.info("starting colorful-control-center daemon (fake=%s)", args.fake)

    config = Config(args.config)
    bridge = ClevoBridge(fake=args.fake)

    # best-effort: load the kernel module if the device node is missing
    if not bridge.present() and not args.fake:
        try:
            subprocess.run(["modprobe", "clevo_cc"],
                           capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if bridge.present():
            log.info("/dev/clevo_cc available")
        else:
            log.warning("/dev/clevo_cc missing - hardware control unavailable")

    monitor = SystemMonitor(fake=args.fake)
    fans = FanController(bridge, monitor, config)
    kbd = KeyboardController(bridge, config)

    # apply saved state on start
    try:
        kbd.apply()
    except Exception as e:
        log.warning("keyboard apply on start failed: %s", e)
    if config.get("perf", "apply_on_start", default=False):
        p = config.get("perf", "profile", default=None)
        if p is not None:
            try:
                bridge.set_perf_profile(int(p))
            except Exception as e:
                log.warning("perf profile apply failed: %s", e)
    fans.start()

    state = ccserver.State(bridge, monitor, fans, kbd, config)
    httpd = ccserver.make_server(state, args.port)
    log.info("HTTP UI on http://127.0.0.1:%d/", args.port)

    stop = threading.Event()

    def _handle_signal(signum, frame):
        log.info("signal %s received, shutting down", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    t = threading.Thread(target=httpd.serve_forever, name="http", daemon=True)
    t.start()

    while not stop.is_set():
        stop.wait(1.0)

    httpd.shutdown()
    fans.stop(restore_auto=True)
    log.info("goodbye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
