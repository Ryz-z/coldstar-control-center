#!/bin/bash
# Sign a kernel module with a locally generated MOK key (for Secure Boot systems).
# Generates /var/lib/colorful-control-center/mok/MOK.{key,crt,der} on first run.
# Requires one-time enrollment at reboot (printed instructions / MOK manager).
set -u

MODULE="${1:-}"
[ -n "$MODULE" ] && [ -f "$MODULE" ] || exit 0

# Secure Boot state
SB="/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
if [ -e "$SB" ]; then
  val=$(od -An -tu1 -j4 -N1 "$SB" 2>/dev/null | tr -d ' ')
  [ "${val:-0}" = "1" ] || exit 0
fi

KVER="$(uname -r)"
SIGN_FILE="/usr/src/linux-headers-${KVER}/scripts/sign-file"
if [ ! -x "$SIGN_FILE" ]; then
  SIGN_FILE=$(ls /usr/src/linux-headers-*/scripts/sign-file 2>/dev/null | head -1)
fi
[ -n "${SIGN_FILE:-}" ] && [ -x "$SIGN_FILE" ] || { echo "sign-file 不可用，跳过签名"; exit 0; }

MOK_DIR=/var/lib/colorful-control-center/mok
mkdir -p "$MOK_DIR"; chmod 700 "$MOK_DIR"
KEY="$MOK_DIR/MOK.key"; CRT="$MOK_DIR/MOK.crt"; DER="$MOK_DIR/MOK.der"

if [ ! -f "$KEY" ]; then
  command -v openssl >/dev/null || { echo "openssl 不可用，跳过签名"; exit 0; }
  openssl req -new -x509 -newkey rsa:2048 -keyout "$KEY" -out "$CRT" -days 3650 -nodes \
    -subj "/CN=Colorful Control Center Module Signing/" >/dev/null 2>&1 || exit 0
  chmod 600 "$KEY"
  openssl x509 -in "$CRT" -outform DER -out "$DER" >/dev/null 2>&1 || true
  if command -v mokutil >/dev/null && [ -f "$DER" ]; then
    printf 'colorful\ncolorful\n' | mokutil --import "$DER" >/dev/null 2>&1 || true
    echo "已生成 MOK 密钥并申请注册：下次重启时在蓝色 MOK 界面选择 Enroll MOK（密码: colorful）"
  fi
fi

if [ -f "$KEY" ] && [ -f "$CRT" ]; then
  "$SIGN_FILE" sha256 "$KEY" "$CRT" "$MODULE" && echo "已签名 $MODULE"
fi
exit 0
