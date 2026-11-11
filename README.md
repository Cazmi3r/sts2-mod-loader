# STS2 Mod Loader

A desktop mod manager for **Slay the Spire 2** on Linux, Windows and macOS. It sits
outside the game, so game updates can't break it, and it never patches or injects
anything: it reads the game's own files and edits only the game's `settings.save`, the
same file the in-game mod menu writes.

Pure Python 3 with Tk (Python's built-in window toolkit). One codebase for every OS, no
.NET. [Pillow](https://pypi.org/project/pillow/) is optional, but without it some Workshop
pictures can't be shown: the *Get mods* page says so when that happens.

Dark theme by default. Set `STS2ML_THEME=light` for light, or `STS2ML_THEME=auto` to follow
the desktop.

## Install

**Linux**

```sh
sudo apt install python3-tk   # if it isn't there yet (Mint/Ubuntu/Debian)
./install.sh                  # adds "STS2 Mod Loader" to the applications menu
./install.sh --uninstall
```

Or run `./sts2-mod-loader` directly.

**Windows**

1. Install Python 3 from [python.org](https://www.python.org/downloads/) (keep "tcl/tk" ticked,
   it is by default).
2. Double-click `STS2 Mod Loader.pyw`.

**macOS**

1. Install Python with Tk from [Homebrew](https://brew.sh): `brew install python-tk`. Apple's
   built-in Python has an outdated Tk that draws badly.
2. Double-click `STS2 Mod Loader.command`. The first time, macOS may ask you to allow it:
   right-click it → Open.

Python from [python.org](https://www.python.org/downloads/) works too, but it starts without
trusted certificates, so the Workshop can't be reached: run **Install Certificates.command** in its
folder in Applications once. The app tells you if this is needed.

## What it does

- **One list of every mod**, from Steam Workshop and the game's local `mods/` folder,
  with search, filters (current mods, problems, unapplied changes…) and sorting. The view
  you used last is remembered.
- **Health check for every mod**, from three sources:
  - **Code check.** Each mod's compiled code is compared against *your installed*
    `sts2.dll` and library mods (BaseLib, RitsuLib…). If a mod calls a game function
    that was removed or changed in your game version, you see exactly which ones,
    before you play. This catches mods that load fine but crash the moment you use
    them.
  - **Game logs.** Startup failures, crashes, missing assets and in-game errors from
    recent sessions are traced back to the mod that caused them, including stack-trace
    attribution. Findings are remembered after the game deletes old logs.
  - **Files.** Missing or misnamed DLL/PCK files (with a one-click fix for the
    Linux-only capitalization problem), missing or disabled dependencies, mods that
    need a newer game.
- **Safe changes.** Toggles are staged until you press **Apply**. Enabling a mod
  enables what it needs. Disabling a library asks about the mods that depend on it.
  Apply refuses while the game is running (the game overwrites its settings on exit),
  and every write makes a backup you can restore from the menu.
- **Profiles.** Save and load named setups ("Solo", "Co-op with Sam"), including load
  order.
- **Load order.** Sort by load order and drag mods up or down the list, or use the ▲▼
  buttons on a mod (Alt+↑/↓, Option+↑/↓ on macOS).
- **Keyboard.** ↑/↓ move through the list (Page Up/Down, Home/End jump), Space enables or
  disables the selected mod, Enter applies, Esc clears the search or the selection,
  Ctrl+F (⌘F) searches, Ctrl+S (⌘S) applies, F5 rescans.
- **Co-op compare.** **Copy mod list** puts one line of text on your clipboard
  (`STS2:…`, about 100 characters for 8 mods, under 500 for 60). Paste it in Discord or wherever; your friend
  presses **Import mod list** (it picks the code up from the clipboard) to see
  the branch/version differences and the mods to subscribe to, enable or disable.
  *Subscribe to all* gets the missing ones and *Match their setup* stages the rest.
- **Get mods.** The *Get mods* tab browses and searches the Workshop (trending, most
  subscribed, newest, recently updated, or paste an item link/ID). **Subscribe** and
  **Unsubscribe** work right in the app: they ask your running Steam client, through the
  Steam library that ships with the game, exactly like the buttons on the Workshop page.
  When Steam finishes the download the app notices, rescans and health-checks the new
  mod. A missing dependency has a *Find on Workshop* button.
- **Encrypted home folders (Linux).** Encrypted home folders (ecryptfs, an install option
  on Mint/Ubuntu) only allow 143-character file names, so any mod that saves files with
  longer names breaks. The app detects this and offers to move the game's data folder,
  saves included, to `/srv/sts2-mod-loader/<you>/` on the normal disk, linked back so the
  game finds it as before. It asks for your password once to create that folder.
- **Workshop info.** Preview art, tags, subscriber counts and descriptions, plus a
  badge when Steam has a newer version queued.
- **Play** launches the game through Steam (offering to apply pending changes first).
  When the game exits, the logs are rescanned automatically.

## Where things live

| What | Linux | Windows | macOS |
|---|---|---|---|
| Settings backups (last 30), remembered log findings | `~/.local/share/sts2-mod-loader/` | `%LOCALAPPDATA%\sts2-mod-loader\` | `~/Library/Application Support/sts2-mod-loader/` |
| Profiles | `~/.config/sts2-mod-loader/profiles/` | `%APPDATA%\sts2-mod-loader\profiles\` | `~/Library/Application Support/sts2-mod-loader/profiles/` |
| Workshop info / code-check cache | `~/.cache/sts2-mod-loader/` | `%LOCALAPPDATA%\sts2-mod-loader\cache\` | `~/Library/Caches/sts2-mod-loader/` |

The game itself is found through Steam's library folders (Steam's registry entry on Windows).

## Network

The app contacts Steam's public Workshop services only, with no account or key involved:
the Workshop API (`api.steampowered.com`) for titles, preview images and descriptions,
and the Workshop browse page (`steamcommunity.com`) when you use *Get mods*. Everything
else is local.

## Command line

```sh
./sts2-mod-loader --check   # print the health report without opening the window
```

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests build fake game/Steam folders in a temp directory. They never touch your
real settings.

## Limits

- Changes take effect the next time the game starts (that's how the game loads mods).
- Subscribing needs Steam running and signed in. While a request is sent (a second or
  two), Steam shows you as playing Slay the Spire 2. If the game's Steam library can't be
  found, Subscribe falls back to opening the Workshop page in Steam.
- *Get mods* reads Steam's public browse page. If Steam redesigns that page, browsing may
  stop finding results until the app is updated; pasting an item link or ID still works.
- Some mods ship several builds for different game versions and pick one at runtime.
  The code check judges each set by its best-matching build.
- The code check finds calls into game code that no longer exist. It can't find
  logic bugs or problems in Harmony patches that target methods by name. The log
  check covers those once they happen.
