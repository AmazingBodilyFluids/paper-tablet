#!/bin/sh
# One-time setup of Paper Tablet on Linux. Safe to run again.
#  1. system packages for the window (GTK WebKit) - Arch / Omarchy via pacman, others: printed
#  2. a private Python environment with paramiko and pywebview
#  3. access to /dev/uinput (udev rule + load the module at boot)   [sudo]
#  4. a menu entry
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
VENV="$DATA/paper-tablet/venv"

echo "== 1/4 System packages"
if command -v pacman >/dev/null 2>&1; then
  sudo pacman -S --needed python python-gobject webkit2gtk-4.1
else
  echo "Not an Arch system: install Python 3.10+, PyGObject and WebKitGTK yourself, e.g."
  echo "  Debian/Ubuntu: sudo apt install python3-venv python3-gi gir1.2-webkit2-4.1"
  echo "  Fedora:        sudo dnf install python3-gobject webkit2gtk4.1"
fi

echo "== 2/4 Python environment in $VENV"
python3 -m venv --system-site-packages "$VENV"   # system site: PyGObject comes from the OS
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$DIR/pc/requirements.txt"

echo "== 3/4 Permission to create the virtual pen (/dev/uinput)"
sudo install -m 644 "$DIR/linux/60-paper-tablet-uinput.rules" /etc/udev/rules.d/60-paper-tablet-uinput.rules
echo uinput | sudo tee /etc/modules-load.d/paper-tablet-uinput.conf >/dev/null
sudo modprobe uinput
sudo udevadm control --reload-rules
sudo udevadm trigger --name-match=uinput || true

echo "== 4/4 Menu entry"
mkdir -p "$DATA/applications"
chmod +x "$DIR/linux/paper-tablet.sh"
cat > "$DATA/applications/paper-tablet.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Paper Tablet
Comment=reMarkable Paper Pro as a pen tablet
Exec=$DIR/linux/paper-tablet.sh
Icon=$DIR/pc/icons/paper-tablet.png
Terminal=false
Categories=Graphics;Utility;
DESKTOP

echo
if [ -w /dev/uinput ]; then
  echo "Done. Start Paper Tablet from your app launcher, or run: $DIR/linux/paper-tablet.sh"
else
  echo "Done. Log out and back in once (so the new /dev/uinput permission applies),"
  echo "then start Paper Tablet from your app launcher, or run: $DIR/linux/paper-tablet.sh"
fi
