"""Workshop metadata (title, preview image, tags, update time) from Steam's public API, and
browsing/searching the Workshop through its public browse page (no account or API key)."""

import json
import os
import re
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
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                items = json.load(resp)["response"]["publishedfiledetails"]
        except (OSError, ValueError, KeyError):
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
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            html = resp.read(16 * 1024 * 1024).decode("utf-8", "replace")
    except (OSError, ValueError):
        return None
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


def preview_path(wid, url, thumb=False):
    """Blocking download of a preview image into the cache. Returns a local path or None.
    thumb asks Steam's image server for a small version (for list rows)."""
    if not url:
        return None
    os.makedirs(PREVIEW_DIR, exist_ok=True)
    path = os.path.join(PREVIEW_DIR, f"{wid}.thumb" if thumb else f"{wid}.img")
    if thumb:
        url += ("&" if "?" in url else "?") + "imw=96&imh=96&ima=fit&impolicy=Letterbox&imcolor=%23000000&letterbox=true"
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 7 * 86400:
        return path
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "sts2-mod-loader"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = resp.read(8 * 1024 * 1024)
        with open(path, "wb") as f:
            f.write(data)
        return path
    except OSError:
        return path if os.path.exists(path) else None


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
