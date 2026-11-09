#!/bin/sh
# Adds "STS2 Mod Loader" to your applications menu and puts `sts2-mod-loader` on your PATH.
# Run with --uninstall to remove both. Nothing outside your home directory is touched.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DESKTOP="$HOME/.local/share/applications/sts2-mod-loader.desktop"
BIN="$HOME/.local/bin/sts2-mod-loader"

if [ "$1" = "--uninstall" ]; then
    rm -f "$DESKTOP" "$BIN"
    echo "Removed menu entry and launcher. Your settings backups and profiles are kept in"
    echo "  ~/.local/share/sts2-mod-loader and ~/.config/sts2-mod-loader"
    exit 0
fi

if ! python3 -c "import tkinter" 2>/dev/null; then
    echo "Python's Tk support is missing. Install it first:"
    echo "  sudo apt install python3-tk      (Mint, Ubuntu, Debian)"
    echo "  sudo dnf install python3-tkinter (Fedora)"
    echo "  sudo pacman -S tk                (Arch)"
    exit 1
fi

mkdir -p "$(dirname "$DESKTOP")" "$(dirname "$BIN")"
ln -sf "$HERE/sts2-mod-loader" "$BIN"
cat > "$DESKTOP" <<DESK
[Desktop Entry]
Type=Application
Name=STS2 Mod Loader
GenericName=Mod Manager
Comment=Manage and health-check Slay the Spire 2 mods
Exec=$HERE/sts2-mod-loader
Icon=applications-games
Terminal=false
Categories=Game;
Keywords=slay;spire;mods;workshop;
StartupWMClass=sts2-mod-loader
DESK
command -v update-desktop-database >/dev/null && update-desktop-database "$(dirname "$DESKTOP")" 2>/dev/null || true
echo "Installed. Find \"STS2 Mod Loader\" in your applications menu, or run: sts2-mod-loader"
