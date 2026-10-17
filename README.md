# STS2 Mod Loader

A desktop mod manager for **Slay the Spire 2** on Linux. It sits outside the game, so
game updates can't break it, and it never patches or injects anything: it reads the
game's own files and edits only the game's `settings.save`, the same file the in-game
mod menu writes.

Pure Python 3 + GTK 3 (preinstalled on Linux Mint / Ubuntu / most GNOME and Cinnamon
desktops). No other dependencies, no .NET.

## Install

```sh
./install.sh            # adds "STS2 Mod Loader" to the applications menu
./install.sh --uninstall
```

Or run `./sts2-mod-loader` directly.

## What it does

- **One list of every mod**, from Steam Workshop and the game's local `mods/` folder,
  with search, filters (problems, enabled, unapplied changes…) and sorting.
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
- **Co-op compare.** *Export my mod list* produces a small JSON file. Your friend
  opens it with *Compare with a friend's mod list* to see the branch/version
  differences and the mods to subscribe to, enable or disable. *Match their setup*
  stages the changes.
- **Get mods.** The *Get mods* tab browses and searches the Workshop (trending, most
  subscribed, newest, recently updated, or paste an item link/ID). *Subscribe in Steam…*
  opens the item in Steam; when Steam finishes the download the app notices, rescans and
  health-checks the new mod. A missing dependency has a *Find on Workshop* button.
- **Workshop info.** Preview art, tags, subscriber counts and descriptions, plus a
  badge when Steam has a newer version queued.
- **Play** launches the game through Steam (offering to apply pending changes first).
  When the game exits, the logs are rescanned automatically.

## Where things live

| What | Where |
|---|---|
| Settings backups (last 30) | `~/.local/share/sts2-mod-loader/backups/` |
| Remembered log findings | `~/.local/share/sts2-mod-loader/history.json` |
| Profiles | `~/.config/sts2-mod-loader/profiles/` |
| Workshop info / code-check cache | `~/.cache/sts2-mod-loader/` |

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
- Subscribing and unsubscribing happen in Steam (Steam offers no way for other apps to
  subscribe for you). The app opens the Workshop page and picks up the download itself.
- *Get mods* reads Steam's public browse page. If Steam redesigns that page, browsing may
  stop finding results until the app is updated; pasting an item link or ID still works.
- Some mods ship several builds for different game versions and pick one at runtime.
  The code check judges each set by its best-matching build.
- The code check finds calls into game code that no longer exist. It can't find
  logic bugs or problems in Harmony patches that target methods by name. The log
  check covers those once they happen.
