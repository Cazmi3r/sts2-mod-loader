"""Everything that differs between Linux, Windows and macOS: where this app keeps its own
files, where Steam and the game's user data live, how to tell whether the game is running,
and how to open folders and steam:// links."""

import os
import ssl
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not (IS_WINDOWS or IS_MAC)

APP_DIR_NAME = "sts2-mod-loader"
GAME_DATA_NAME = "SlayTheSpire2"      # Godot's user:// folder name
HOME = os.path.expanduser("~")


def _env_dir(var, fallback):
    value = os.environ.get(var)
    return value if value and os.path.isabs(value) else os.path.expanduser(fallback)


# ---- this app's own files -----------------------------------------------------------------

def data_dir():
    """Backups and remembered log findings."""
    if IS_WINDOWS:
        return os.path.join(_env_dir("LOCALAPPDATA", "~/AppData/Local"), APP_DIR_NAME)
    if IS_MAC:
        return os.path.join(HOME, "Library", "Application Support", APP_DIR_NAME)
    return os.path.join(_env_dir("XDG_DATA_HOME", "~/.local/share"), APP_DIR_NAME)


def config_dir():
    """Profiles."""
    if IS_WINDOWS:
        return os.path.join(_env_dir("APPDATA", "~/AppData/Roaming"), APP_DIR_NAME)
    if IS_MAC:
        return os.path.join(HOME, "Library", "Application Support", APP_DIR_NAME)
    return os.path.join(_env_dir("XDG_CONFIG_HOME", "~/.config"), APP_DIR_NAME)


def cache_dir():
    """Workshop info, preview images, code-check results."""
    if IS_WINDOWS:
        return os.path.join(_env_dir("LOCALAPPDATA", "~/AppData/Local"), APP_DIR_NAME, "cache")
    if IS_MAC:
        return os.path.join(HOME, "Library", "Caches", APP_DIR_NAME)
    return os.path.join(_env_dir("XDG_CACHE_HOME", "~/.cache"), APP_DIR_NAME)


# ---- Steam and the game -------------------------------------------------------------------

def _windows_registry_steam():
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
        try:
            with winreg.OpenKey(hive, key) as k:
                path, _ = winreg.QueryValueEx(k, value)
            if path:
                out.append(os.path.normpath(path))
        except OSError:
            continue
    return out


def steam_roots():
    """Candidate Steam install folders, most likely first."""
    if IS_WINDOWS:
        roots = _windows_registry_steam()
        for var in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(var):
                roots.append(os.path.join(os.environ[var], "Steam"))
        roots.append(r"C:\Program Files (x86)\Steam")
        return roots
    if IS_MAC:
        return [os.path.join(HOME, "Library", "Application Support", "Steam")]
    return [os.path.expanduser(p) for p in (
        "~/.steam/steam",
        "~/.steam/debian-installation",
        "~/.local/share/Steam",
        "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",
    )]


def game_user_data_candidates():
    """Where the game keeps settings.save and logs (Godot's user:// folder)."""
    if IS_WINDOWS:
        return [os.path.join(_env_dir("APPDATA", "~/AppData/Roaming"), GAME_DATA_NAME)]
    if IS_MAC:
        return [os.path.join(HOME, "Library", "Application Support", GAME_DATA_NAME)]
    return [os.path.expanduser(p) for p in (
        "~/.local/share/" + GAME_DATA_NAME,
        "~/.var/app/com.valvesoftware.Steam/.local/share/" + GAME_DATA_NAME,
    )]


def game_exe_dir(install_dir):
    """Folder holding the game executable, which is where the game looks for its mods/ folder.
    On macOS that is inside the .app bundle; elsewhere it is the install folder itself."""
    if IS_MAC and install_dir and os.path.isdir(install_dir):
        apps = sorted(f for f in os.listdir(install_dir) if f.endswith(".app"))
        if apps:
            return os.path.join(install_dir, apps[0], "Contents", "MacOS")
    return install_dir


def case_sensitive_files():
    """Whether file names that differ only in capitalization are different files. True on Linux;
    Windows and macOS (by default) ignore case, so mods with mis-capitalized files still load."""
    return IS_LINUX


# ---- processes ----------------------------------------------------------------------------

_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0     # CREATE_NO_WINDOW: no console flash on Windows


def _run_quiet(args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5,
                              creationflags=_NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def is_process_running(name):
    """True if a process called `name` (no .exe) is running. Never matches this app."""
    if IS_WINDOWS:
        out = _run_quiet(["tasklist", "/FI", f"IMAGENAME eq {name}.exe", "/NH", "/FO", "CSV"])
        return f'"{name.lower()}.exe"' in out.lower()
    if IS_MAC:
        # Match the executable inside any bundle, whatever the bundle is called.
        out = _run_quiet(["pgrep", "-f", f".app/Contents/MacOS/{name}"])
        return bool(out.strip())
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/comm") as f:
                if f.read().strip() == name:
                    return True
        except OSError:
            continue
    return False


def open_target(target):
    """Open a folder, file or URL (including steam://) with the system's default handler."""
    if IS_WINDOWS:
        os.startfile(target)   # noqa: S606 (Windows only)
        return
    cmd = ["open", target] if IS_MAC else ["xdg-open", target]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def prefers_dark():
    """The desktop's light/dark preference, best effort. Defaults to light."""
    try:
        if IS_WINDOWS:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
                return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
        if IS_MAC:
            return "dark" in _run_quiet(["defaults", "read", "-g", "AppleInterfaceStyle"]).lower()
        out = _run_quiet(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
        if "dark" in out:
            return True
        for schema in ("org.cinnamon.desktop.interface", "org.gnome.desktop.interface"):
            if "dark" in _run_quiet(["gsettings", "get", schema, "gtk-theme"]).lower():
                return True
        return "dark" in os.environ.get("GTK_THEME", "").lower()
    except OSError:
        return False


# ---- secure connections -------------------------------------------------------------------

_SSL_CONTEXT = None
CERTIFICATES_HELP = ("This Python can't check secure connections (it has no trusted certificates), so the "
                     "Steam Workshop can't be reached. Run \"Install Certificates.command\" from your Python "
                     "folder in Applications, or use Homebrew's Python: brew install python-tk.")
CERTIFICATE_FAILED_HELP = ("Steam's secure connection couldn't be verified, so the Workshop can't be reached. "
                           + ("If this Python came from python.org, run \"Install Certificates.command\" from its "
                              "folder in Applications. " if IS_MAC else "")
                           + "Check your computer's date and time, and any VPN or network filter.")


def _python_has_ca_certs():
    """False when this Python's OpenSSL has no trusted certificates at all, as with the
    python.org installer for macOS until 'Install Certificates.command' is run."""
    paths = ssl.get_default_verify_paths()
    for env in (paths.openssl_cafile_env, paths.openssl_capath_env):
        if os.environ.get(env):
            return True
    if paths.cafile and os.path.isfile(paths.cafile):
        return True
    try:
        return bool(paths.capath and os.listdir(paths.capath))
    except OSError:
        return False


def certificates_missing():
    """True when every secure request will fail because this Python has no certificates
    (python.org's macOS installer, before Install Certificates.command). The app says so
    rather than working around it."""
    return IS_MAC and not _python_has_ca_certs()


def ssl_context():
    """The SSL context for every web request this app makes: Python's own default."""
    global _SSL_CONTEXT
    if _SSL_CONTEXT is None:
        _SSL_CONTEXT = ssl.create_default_context()
    return _SSL_CONTEXT
