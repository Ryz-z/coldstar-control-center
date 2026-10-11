#!/bin/bash
# 隐星控制中心 launcher - ensures daemon is running, then opens the UI window.
PORT=8632
URL="http://127.0.0.1:${PORT}/"

if ! curl -sf -o /dev/null --max-time 1 "${URL}api/ping"; then
  systemctl start colorful-control-center.service >/dev/null 2>&1 \
    || pkexec systemctl start colorful-control-center.service >/dev/null 2>&1 \
    || true
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null --max-time 1 "${URL}api/ping" && break
    sleep 0.25
  done
fi

if [ -x /usr/bin/python3 ] && /usr/bin/python3 -c 'import gi' >/dev/null 2>&1; then
  exec /usr/bin/python3 /usr/lib/colorful-control-center/launcher/window.py "$@"
else
  exec xdg-open "$URL"
fi
