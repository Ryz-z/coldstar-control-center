#!/bin/bash
# Build & install the clevo_cc kernel module for a given (or the running) kernel.
# Usage: build.sh [kernel-version]
set -u

KVER="${1:-$(uname -r)}"
SRC_DIR="/usr/lib/colorful-control-center/kmod"
BUILD_DIR="/usr/src/colorful-control-center-${KVER}"
LOG="/var/log/colorful-control-center-kmod.log"
KDIR="/lib/modules/${KVER}/build"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG" 2>/dev/null || true; }

if [ ! -d "$KDIR" ]; then
  echo "缺少内核头文件: linux-headers-${KVER}（先执行 sudo apt install linux-headers-${KVER}）"
  log "missing kernel headers $KDIR"
  exit 2
fi
if ! command -v make >/dev/null || ! command -v gcc >/dev/null; then
  echo "缺少编译工具: make / gcc（先执行 sudo apt install build-essential）"
  log "gcc/make missing"
  exit 3
fi

mkdir -p "$BUILD_DIR"
cp -f "$SRC_DIR/clevo_cc.c" "$SRC_DIR/Makefile" "$BUILD_DIR/" 2>/dev/null || true

make -C "$KDIR" M="$BUILD_DIR" clean >/dev/null 2>&1 || true
if ! make -C "$KDIR" M="$BUILD_DIR" modules >> "$LOG" 2>&1; then
  echo "内核模块编译失败，详见 $LOG"
  log "build failed for $KVER"
  exit 4
fi

install -D -m 644 "$BUILD_DIR/clevo_cc.ko" "/lib/modules/${KVER}/extra/clevo_cc.ko"
depmod -a "$KVER" >/dev/null 2>&1 || true

# Secure Boot: sign with local MOK key (best effort)
if [ -x "$SRC_DIR/secure-boot-sign.sh" ]; then
  "$SRC_DIR/secure-boot-sign.sh" "/lib/modules/${KVER}/extra/clevo_cc.ko" >> "$LOG" 2>&1 || true
fi

if [ "$KVER" = "$(uname -r)" ]; then
  modprobe clevo_cc >> "$LOG" 2>&1 || true
fi

log "build ok for $KVER"
exit 0
