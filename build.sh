#!/bin/sh
set -eu
cd "$(dirname "$0")"
version=1.2.0
stage=$(mktemp -d)
chmod 0755 "$stage"
trap 'rm -rf "$stage"' EXIT
install -d "$stage/DEBIAN" "$stage/usr/bin" "$stage/usr/share/root-explorer" "$stage/usr/share/applications" "$stage/usr/share/icons/hicolor/256x256/apps" "$stage/usr/share/doc/root-explorer" dist
cat > "$stage/DEBIAN/control" <<EOF
Package: root-explorer
Version: $version
Architecture: all
Maintainer: ROOT project <root-explorer@localhost>
Section: utils
Priority: optional
Depends: python3 (>= 3.9), python3-pyqt5, python3-gi, gir1.2-glib-2.0, gvfs-backends, xdg-utils, gir1.2-gtk-3.0, gir1.2-vte-2.91, openssh-client, python3-libarchive-c, sudo
Recommends: gvfs-fuse
Description: ROOT file explorer for Chromebook and Debian
 Native multi-window file manager with SMB shares, recursive size reports
 and live transfer activity. Includes a tabbed terminal and saved SSH sessions.
EOF
cat > "$stage/usr/bin/root-explorer" <<'EOF'
#!/bin/sh
exec /usr/bin/python3 /usr/share/root-explorer/main.py "$@"
EOF
chmod 755 "$stage/usr/bin/root-explorer"
install -m644 root_explorer/*.py root_explorer/root.png "$stage/usr/share/root-explorer/"
install -m644 root_explorer/root.png "$stage/usr/share/icons/hicolor/256x256/apps/uk.systemadmin.RootExplorer.png"
install -m644 assets/*.desktop "$stage/usr/share/applications/"
install -m644 LICENSE "$stage/usr/share/doc/root-explorer/copyright"
install -m644 README.md "$stage/usr/share/doc/root-explorer/"
install -d "$stage/usr/share/doc/root-explorer/docs"
install -m644 docs/*.png "$stage/usr/share/doc/root-explorer/docs/"
dpkg-deb --root-owner-group -Zxz --build "$stage" "dist/root-explorer_${version}_all.deb"
