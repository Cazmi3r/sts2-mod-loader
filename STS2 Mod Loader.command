#!/bin/sh
# macOS launcher: double-click in Finder to open STS2 Mod Loader.
cd "$(dirname "$0")" || exit 1
for py in /opt/homebrew/bin/python3 /usr/local/bin/python3 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "import tkinter" 2>/dev/null; then
        exec "$py" ./sts2-mod-loader "$@"
    fi
done
osascript -e 'display alert "STS2 Mod Loader needs Python 3 with Tk" message "Install it with Homebrew: brew install python-tk (or Python from python.org), then open this again."'
