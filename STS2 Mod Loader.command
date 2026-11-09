#!/bin/sh
# macOS launcher: double-click in Finder to open STS2 Mod Loader.
cd "$(dirname "$0")" || exit 1
for py in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c "import tkinter" 2>/dev/null; then
        exec "$py" ./sts2-mod-loader "$@"
    fi
done
osascript -e 'display alert "STS2 Mod Loader needs Python 3" message "Install Python from python.org (it includes the window toolkit this app uses), then open this again."'
