#!/bin/bash
# Build the colorful-control-center .deb package
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE" && pwd)"
STAGE="$ROOT/deb-stage/colorful-control-center"
OUT="$ROOT/out"
NAME="colorful-control-center"
VER="1.0.5"

rm -rf "$ROOT/deb-stage"
mkdir -p "$STAGE"

# ---- payload: UI ----
install -d "$STAGE/usr/share/colorful-control-center/ui"
cp "$HERE"/ui/index.html "$HERE"/ui/style.css "$HERE"/ui/app.js \
   "$STAGE/usr/share/colorful-control-center/ui/"

# ---- payload: daemon ----
install -d "$STAGE/usr/lib/colorful-control-center/daemon"
cp -r "$HERE/daemon/ccc" "$STAGE/usr/lib/colorful-control-center/daemon/"
cp "$HERE/daemon/cccd.py" "$STAGE/usr/lib/colorful-control-center/daemon/"
find "$STAGE/usr/lib/colorful-control-center/daemon" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# ---- payload: launcher ----
install -d "$STAGE/usr/lib/colorful-control-center/launcher"
cp "$HERE/launcher/window.py" "$STAGE/usr/lib/colorful-control-center/launcher/"
install -d "$STAGE/usr/bin"
cp "$HERE/launcher/colorful-control-center.sh" "$STAGE/usr/bin/colorful-control-center"
cp "$HERE/launcher/colorful-cc-cli.sh" "$STAGE/usr/bin/colorful-cc-cli"
chmod 755 "$STAGE/usr/bin/colorful-control-center" "$STAGE/usr/bin/colorful-cc-cli"

# ---- payload: kernel module sources ----
install -d "$STAGE/usr/lib/colorful-control-center/kmod"
cp "$HERE/kmod/clevo_cc.c" "$HERE/kmod/Makefile" \
   "$HERE/kmod/build.sh" "$HERE/kmod/secure-boot-sign.sh" \
   "$STAGE/usr/lib/colorful-control-center/kmod/"
chmod 755 "$STAGE/usr/lib/colorful-control-center/kmod/build.sh" \
          "$STAGE/usr/lib/colorful-control-center/kmod/secure-boot-sign.sh"

# ---- payload: system integration ----
install -d "$STAGE/lib/systemd/system"
cp "$HERE/extras/colorful-control-center.service" "$STAGE/lib/systemd/system/"
install -d "$STAGE/lib/systemd/system-sleep"
cp "$HERE/extras/colorful-control-center-sleep" "$STAGE/lib/systemd/system-sleep/colorful-control-center"
chmod 755 "$STAGE/lib/systemd/system-sleep/colorful-control-center"
install -d "$STAGE/etc/kernel/postinst.d"
cp "$HERE/extras/zz-colorful-control-center" "$STAGE/etc/kernel/postinst.d/zz-colorful-control-center"
chmod 755 "$STAGE/etc/kernel/postinst.d/zz-colorful-control-center"
install -d "$STAGE/usr/share/polkit-1/rules.d"
cp "$HERE/extras/49-colorful-control-center.rules" "$STAGE/usr/share/polkit-1/rules.d/"
install -d "$STAGE/usr/share/applications"
cp "$HERE/extras/colorful-control-center.desktop" "$STAGE/usr/share/applications/"
install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
cp "$HERE/extras/colorful-control-center.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"
install -d "$STAGE/usr/share/doc/$NAME"
cp "$HERE/extras/copyright" "$STAGE/usr/share/doc/$NAME/copyright"

# ---- control ----
install -d "$STAGE/DEBIAN"
cp "$HERE/debian/control" "$STAGE/DEBIAN/control"
for s in postinst prerm postrm; do
  cp "$HERE/debian/$s" "$STAGE/DEBIAN/$s"
  chmod 755 "$STAGE/DEBIAN/$s"
done
SIZE=$(du -sk "$STAGE" | cut -f1)
sed -i "s/^Installed-Size:.*/Installed-Size: $SIZE/" "$STAGE/DEBIAN/control"

# ---- md5sums ----
( cd "$STAGE" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' \
    | xargs md5sum > DEBIAN/md5sums )

# ---- build ----
mkdir -p "$OUT"
dpkg-deb --root-owner-group -Zxz -b "$STAGE" "$OUT/${NAME}_${VER}_amd64.deb"
echo "✓ built: $OUT/${NAME}_${VER}_amd64.deb"
ls -l "$OUT"
