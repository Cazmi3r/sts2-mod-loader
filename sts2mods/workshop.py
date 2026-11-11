"""Workshop metadata (title, preview image, tags, update time) from Steam's public API, and
browsing/searching the Workshop through its public browse page (no account or API key)."""

import json
import os
import re
import ssl
import subprocess
import threading
import time
import urllib.parse
import urllib.request

from . import APP_ID, osenv

API = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"
CACHE_DIR = osenv.cache_dir()
CACHE_PATH = os.path.join(CACHE_DIR, "workshop.json")
PREVIEW_DIR = os.path.join(CACHE_DIR, "previews")
TTL = 6 * 3600
TIMEOUT = 15
MIN_ENGLISH = 0.65   # browse results with fewer English letters than this are hidden
BROWSE = "https://steamcommunity.com/workshop/browse/"
BROWSE_SORTS = [("trend", "Trending this week"), ("totaluniquesubscribers", "Most subscribed"),
                ("mostrecent", "Newest"), ("lastupdated", "Recently updated")]


def load_cache():
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = CACHE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)
    os.replace(tmp, CACHE_PATH)


_certificate_failed = [False]


def certificate_failed():
    """True if the last request to Steam failed certificate verification."""
    return _certificate_failed[0]


def _note_result(error=None):
    """Remember whether a request to Steam failed because its certificate couldn't be verified."""
    reason = getattr(error, "reason", error)
    _certificate_failed[0] = isinstance(reason, ssl.SSLCertVerificationError)


def fetch_details(workshop_ids, force=False):
    """Blocking. Returns {wid: details}. Uses the cache when fresh; never raises on network errors."""
    cache = load_cache()
    now = time.time()
    # Entries without app_id predate the browse fields (size, created...), so refresh those too.
    todo = [w for w in workshop_ids if force or now - cache.get(w, {}).get("_fetched", 0) > TTL
            or "app_id" not in cache.get(w, {})]
    for start in range(0, len(todo), 50):
        chunk = todo[start:start + 50]
        form = {"itemcount": str(len(chunk))}
        for i, wid in enumerate(chunk):
            form[f"publishedfileids[{i}]"] = wid
        req = urllib.request.Request(API, data=urllib.parse.urlencode(form).encode(),
                                     headers={"User-Agent": "sts2-mod-loader"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT, context=osenv.ssl_context()) as resp:
                items = json.load(resp)["response"]["publishedfiledetails"]
            _note_result()
        except (OSError, ValueError, KeyError) as e:
            _note_result(e)
            break
        for item in items:
            if str(item.get("result")) != "1":
                continue
            wid = str(item["publishedfileid"])
            cache[wid] = {
                "_fetched": now,
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "preview_url": item.get("preview_url", ""),
                "time_updated": int(item.get("time_updated") or 0),
                "subscriptions": int(item.get("subscriptions") or 0),
                "file_size": int(item.get("file_size") or 0),
                "time_created": int(item.get("time_created") or 0),
                "app_id": str(item.get("consumer_app_id") or ""),
                "banned": bool(item.get("banned")),
                "tags": [t.get("tag") for t in item.get("tags", []) if isinstance(t, dict)],
            }
    try:
        _save_cache(cache)
    except OSError:
        pass
    return {w: cache[w] for w in workshop_ids if w in cache}


_ID_IN_QUERY = re.compile(r"^(?:.*[?&]id=)?(\d{6,12})(?:&.*)?$")
_RESULT_LINK = re.compile(r"sharedfiles/filedetails/\?id=(\d+)")
_TOTAL_PAGES = re.compile(r"total_pages\W{1,12}(\d+)")


def query_item_id(query):
    """A Workshop ID or item URL typed into the search box -> the ID, else None."""
    m = _ID_IN_QUERY.match(query.strip())
    return m.group(1) if m else None


def parse_browse(html):
    """Browse page HTML -> (item ids in page order, total pages or None)."""
    ids, seen = [], set()
    for wid in _RESULT_LINK.findall(html):
        if wid not in seen:
            seen.add(wid)
            ids.append(wid)
    m = _TOTAL_PAGES.search(html)
    return ids, int(m.group(1)) if m else None


def browse(query="", sort="trend", page=1):
    """Blocking. One page (30 items) of Workshop results as (ids, total_pages), or None if Steam
    can't be reached. Details for the ids come from fetch_details()."""
    wid = query_item_id(query)
    if wid:
        return [wid], 1
    params = {"appid": APP_ID, "section": "readytouseitems", "p": str(page)}
    if query.strip():
        params.update(browsesort="textsearch", searchtext=query.strip())   # ranked by relevance
    else:
        params["browsesort"] = sort
        if sort == "trend":
            params["days"] = "7"
    req = urllib.request.Request(BROWSE + "?" + urllib.parse.urlencode(params),
                                 headers={"User-Agent": "sts2-mod-loader", "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=osenv.ssl_context()) as resp:
            html = resp.read(16 * 1024 * 1024).decode("utf-8", "replace")
    except (OSError, ValueError) as e:
        _note_result(e)
        return None
    _note_result()
    return parse_browse(html)


# Words that show up in titles of any language and say nothing about whether a reader of
# English can use the mod ("【CN】角色 Mod Beta" is still a Chinese listing).
_FILLER = re.compile(r"\b(?:mods?|beta|alpha|test|cn|en|zh|jp|kr|sts2?|v?\d[\w.]*)\b", re.I)
MIN_TITLE_LETTERS = 6


def looks_english(details):
    """Whether to show a browse result to an English reader. Bilingual titles count: one Chinese
    character carries about a word, so "Better Map 更好的地图模组" would fail a plain letter ratio
    even though the English half is readable. A title with real English words (at least
    MIN_TITLE_LETTERS letters once filler like "Mod" or "Beta" is removed) passes; otherwise the
    letter ratio of title + description decides."""
    title = _FILLER.sub(" ", strip_bbcode(details.get("title", "")))
    if sum(c.isascii() and c.isalpha() for c in title) >= MIN_TITLE_LETTERS:
        return True
    return english_ratio(details) >= MIN_ENGLISH


def english_ratio(details):
    """Share of the letters in an item's title and description that are English (ASCII) letters.
    1.0 when there are no letters at all, so symbol-only or empty listings aren't penalised."""
    text = strip_bbcode(f"{details.get('title', '')}\n{details.get('description', '')}")
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    return sum(c.isascii() for c in letters) / len(letters)


def _have_pillow():
    try:
        import PIL.Image  # noqa: F401
        return True
    except ImportError:
        return False


def _is_jpeg(path):
    try:
        with open(path, "rb") as f:
            return f.read(3) == b"\xff\xd8\xff"
    except OSError:
        return False


def _download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "sts2-mod-loader"})
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=osenv.ssl_context()) as resp:
        return resp.read(8 * 1024 * 1024)


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _convert_with_os(jpeg, out, max_side):
    """Turn JPEG bytes into a PNG file with an image converter the system already has.
    macOS always has `sips`; elsewhere there's none to rely on (the Windows build bundles
    Pillow). Returns True on success."""
    if not osenv.IS_MAC:
        return False
    src = out + ".src.jpg"
    try:
        with open(src, "wb") as f:
            f.write(jpeg)
        subprocess.run(["sips", "-s", "format", "png", "-Z", str(max_side), src, "--out", out],
                       capture_output=True, timeout=20)
        with open(out, "rb") as f:
            return f.read(8) == PNG_MAGIC
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        try:
            os.remove(src)
        except OSError:
            pass


THUMB_PX = 96        # list rows show 52px
PREVIEW_PX = 168     # details panes show 168px
# The size Steam's own Workshop pages ask for, so the image server nearly always has it ready
# (~0.1s, ~120KB). Other sizes are made on demand and can take seconds; plain originals are
# often 1000px+ and several hundred KB.
STEAM_SIZE = "ima=fit&impolicy=Letterbox&imcolor=%23000000&imw=322&imh=322&letterbox=true"
CACHE_DAYS = 7


def _fresh(path):
    return os.path.exists(path) and time.time() - os.path.getmtime(path) < CACHE_DAYS * 86400


def thumb_cache_path(wid):
    return os.path.join(PREVIEW_DIR, f"{wid}.thumb")


def _source(wid, url):
    """One download per item: Steam's 322px version, kept as is. Returns the path."""
    path = os.path.join(PREVIEW_DIR, f"{wid}.322")
    if not _fresh(path):
        data = _download(url + ("&" if "?" in url else "?") + STEAM_SIZE)
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "wb") as f:      # several downloads run at once: never expose half a file
            f.write(data)
        _replace(tmp, path)
    return path


def _replace(tmp, path):
    """os.replace, allowing for Windows: it refuses while another thread has `path` open (to
    show the picture, or finishing the same download). Retry briefly; if it's still in use,
    the copy that's already there is as good as ours."""
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    try:
        os.remove(tmp)
    except OSError:
        pass
    if not os.path.exists(path):
        raise PermissionError(f"couldn't write {path}")


PRUNE_DAYS = 30      # pictures not used for this long are removed from the cache


def prune_previews(max_age_days=PRUNE_DAYS):
    """Keep the picture cache small. Removes, from PREVIEW_DIR only (no subfolders):
    files left by older versions (<id>.img and their shrunk copies), icons and previews
    whose 322px source is gone, half-written temp files older than an hour, and anything
    not used for max_age_days. Returns (files removed, bytes freed)."""
    try:
        names = os.listdir(PREVIEW_DIR)
    except OSError:
        return 0, 0
    now = time.time()
    present = set(names)
    removed = freed = 0
    for name in names:
        path = os.path.join(PREVIEW_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        if not os.path.isfile(path):
            continue
        age = now - st.st_mtime
        base = name.split(".", 1)[0]
        rest = name[len(base):]                       # ".322", ".thumb.52x52.png", ...
        stem = name.rsplit(".", 2)[0] if name.endswith(".png") and rest.count(".") >= 2 else None
        if rest.startswith(".img"):
            stale = True                              # older versions' full-size pictures
        elif rest.endswith((".tmp", ".src.jpg")):
            stale = age > 3600                        # interrupted download or conversion
        elif rest.startswith((".thumb", ".preview")):
            stale = f"{base}.322" not in present      # made from a source that's gone
        elif stem and rest.startswith(".322."):
            stale = stem not in present               # shrunk copy of a source that's gone
        else:
            stale = False
        if stale or age > max_age_days * 86400:
            try:
                os.remove(path)
                removed += 1
                freed += st.st_size
            except OSError:
                pass
    return removed, freed


def preview_path(wid, url, thumb=False):
    """Blocking: a local image for a Workshop item's preview, for a list row (thumb) or a
    details pane. Returns a path load_image can show, or None.

    Both come from one small download (_source). Most are JPEG, which Tk can only show with
    Pillow: with Pillow (or when Steam sent a PNG) that file is used directly. Without it,
    macOS makes a PNG of the right size with sips; elsewhere ask the image server for a
    PNG (imformat=png, full size; load_image shrinks and caches it). The server refuses that
    for some images; then the JPEG itself is returned and needs_pillow() is true for it."""
    if not url:
        return None
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    try:
        src = _source(wid, url)
        if _have_pillow() or not _is_jpeg(src):
            return src
        out = thumb_cache_path(wid) if thumb else os.path.join(PREVIEW_DIR, f"{wid}.preview")
        if _fresh(out) and not _is_jpeg(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            return out
        with open(src, "rb") as f:
            jpeg = f.read()
        if _convert_with_os(jpeg, out, THUMB_PX if thumb else PREVIEW_PX):
            return out
        png = _download(url + ("&" if "?" in url else "?") + "imformat=png")
        if png[:8] == PNG_MAGIC:
            with open(out, "wb") as f:
                f.write(png)
            return out
        return src                     # a JPEG only Pillow can show: needs_pillow(src)
    except OSError:
        return None


def needs_pillow(path):
    """True for a downloaded picture that can't be shown because Pillow isn't installed."""
    return bool(path) and not _have_pillow() and _is_jpeg(path)


PILLOW_HINT = ("Some pictures can't be shown because Pillow isn't installed. Install it with "
               + ("py -m pip install pillow" if osenv.IS_WINDOWS else "python3 -m pip install pillow")
               + " and restart the app.")


_BB = [
    (re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.S | re.I), r"\2 (\1)"),
    (re.compile(r"\[\*\]"), "• "),
    (re.compile(r"\[h\d\](.*?)\[/h\d\]", re.S | re.I), r"\1"),
    (re.compile(r"\[img\].*?\[/img\]", re.S | re.I), ""),
    (re.compile(r"\[/?[a-z0-9]+(?:=[^\]]*)?\]", re.I), ""),
]


def strip_bbcode(text):
    for pat, rep in _BB:
        text = pat.sub(rep, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
