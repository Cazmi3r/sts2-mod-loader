"""Tk front end. Runs the same on Linux, Windows and macOS with only Python's standard library
(Pillow, if installed, adds JPEG previews)."""

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

from . import APP_NAME, __version__, osenv
from . import apicheck, logscan, profiles, steamugc, workshop
from . import game as game_mod
from . import mods as mods_mod
from . import settings as settings_mod
from .logscan import S_ERROR, S_OK, S_STALE, S_UNTESTED, S_WARN, STATUS_RANK
from .ui import (C, F, Dialog, InfoBar, Pill, ReadOnlyText, ScrollFrame, SearchEntry, Select, Spinner,
                 Toggle, Tooltip, WrapLabel, apply_theme, app_icon, badge, blend, install_wheel,
                 load_image, soft)

S_CHECKING = "checking"
STATUS_TEXT = {
    S_ERROR: "Broken or likely broken",
    S_WARN: "Has warnings",
    S_STALE: "Had problems that look fixed now",
    S_OK: "No problems found",
    S_UNTESTED: "Not tested yet",
    S_CHECKING: "Checking…",
}
STATUS_DOT = {S_ERROR: "●", S_WARN: "●", S_STALE: "◐", S_OK: "●", S_UNTESTED: "○", S_CHECKING: "○"}


def status_color(status):
    return {S_ERROR: C["error"], S_WARN: C["warn"], S_STALE: C["stale"], S_OK: C["ok"]}.get(status, C["faint"])


FILTERS = [("all", "All mods"), ("enabled", "Enabled"), ("disabled", "Disabled"),
           ("problems", "Problems"), ("changed", "Unapplied changes"),
           ("workshop", "Workshop"), ("local", "Local folder")]
SORTS = [("name", "Sort: Name"), ("status", "Sort: Status"), ("order", "Sort: Load order"),
         ("updated", "Sort: Recently updated"), ("size", "Sort: Size")]
MOD = "Command" if osenv.IS_MAC else "Control"


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(ts):
    return time.strftime("%b %d, %Y", time.localtime(ts)) if ts else "?"


def clean_version(v):
    v = (v or "").strip()
    return v[1:] if v[:1] in "vV" and v[1:2].isdigit() else v or "?"


def plural(n, word, suffix="s"):
    return f"{n} {word}{suffix if n != 1 else ''}"


class Worker:
    """One background thread with a job queue. Results come back on the Tk main loop."""

    closing = False
    results = queue.Queue()

    def __init__(self, name):
        self.jobs = queue.Queue()
        threading.Thread(target=self._run, name=name, daemon=True).start()

    def submit(self, fn, callback):
        self.jobs.put((fn, callback))

    def _run(self):
        while True:
            fn, callback = self.jobs.get()
            if Worker.closing:
                continue
            try:
                result = fn()
            except Exception as e:   # a bug in one job must not kill the worker
                print(f"background job failed: {e!r}", file=sys.stderr)
                continue
            if not Worker.closing:
                Worker.results.put((callback, result))

    @classmethod
    def pump(cls, root):
        try:
            while True:
                callback, result = cls.results.get_nowait()
                try:
                    callback(result)
                except Exception as e:   # keep the UI alive
                    import traceback
                    traceback.print_exc()
                    print(f"callback failed: {e!r}", file=sys.stderr)
        except queue.Empty:
            pass
        if not cls.closing:
            root.after(40, cls.pump, root)


class WorkerPool:
    """Several Workers sharing jobs round-robin, so independent downloads (Workshop
    pictures) run side by side instead of one after another."""

    def __init__(self, name, size):
        self.workers = [Worker(f"{name}-{i}") for i in range(size)]
        self.next = 0

    def submit(self, fn, callback):
        self.workers[self.next].submit(fn, callback)
        self.next = (self.next + 1) % len(self.workers)


# ------------------------------------------------------------------------------------------
# State (no widgets)
# ------------------------------------------------------------------------------------------

class State:
    def __init__(self):
        self.pending = {}
        self.order = []
        self.report = None
        self.api = {}
        self.workshop = workshop.load_cache()
        self.load()

    def load(self):
        self.game = game_mod.locate()
        self.mods = mods_mod.scan(self.game)
        self.settings = None
        self.settings_error = None
        if self.game.settings_path:
            try:
                self.settings = settings_mod.Settings(self.game.settings_path)
            except (OSError, ValueError) as e:
                self.settings_error = str(e)
        saved = self.settings.enabled_map() if self.settings else {}
        self.saved = {mid: saved.get(mid, True) for mid in self.mods}
        order = [i for i in (self.settings.order() if self.settings else []) if i in self.mods]
        order += sorted((m for m in self.mods if m not in order), key=lambda i: self.mods[i].name.lower())
        old_custom = self.order if self.order_changed() else None
        self.saved_order = order
        self.order = [i for i in old_custom if i in self.mods] + [i for i in order if i not in old_custom] \
            if old_custom else list(order)
        self.pending = {k: v for k, v in self.pending.items() if k in self.mods and v != self.saved[k]}

    def enabled(self, mod_id):
        return self.pending.get(mod_id, self.saved.get(mod_id, True))

    def set_pending(self, mod_id, value):
        if value == self.saved.get(mod_id, True):
            self.pending.pop(mod_id, None)
        else:
            self.pending[mod_id] = value

    def order_changed(self):
        return bool(getattr(self, "saved_order", None)) and self.order != self.saved_order

    def change_count(self):
        return len(self.pending) + (1 if self.order_changed() else 0)

    def states(self):
        return {mid: self.enabled(mid) for mid in self.mods}

    def status(self, mod_id):
        if not self.report:
            return S_CHECKING
        st = self.report.status.get(mod_id, S_UNTESTED)
        if st in (S_OK, S_UNTESTED, S_STALE) and self.enabled(mod_id) and self.dep_problems(mod_id):
            return S_WARN
        return st

    def issues(self, mod_id):
        base = list(self.report.by_mod.get(mod_id, [])) if self.report else \
            list(self.mods[mod_id].static_issues)
        return self.dep_problems(mod_id) + base

    def dep_problems(self, mod_id):
        out = []
        if not self.enabled(mod_id):
            return out
        for dep_id, _ in self.mods[mod_id].dependencies:
            dep = self.mods.get(dep_id)
            if dep and not self.enabled(dep_id):
                out.append(mods_mod.Issue(mods_mod.WARN, f"Requires {dep.name}, which is disabled",
                                          "It will fail to load. Enable the dependency.",
                                          key=f"dep-off-{dep_id}"))
        return out

    def workshop_info(self, mod):
        return self.workshop.get(mod.workshop_id) if mod.workshop_id else None

    def mod_for_workshop(self, wid):
        return next((m for m in self.mods.values() if m.workshop_id == wid), None)

    def global_issues(self):
        if self.report:
            return list(self.report.global_issues)
        fs = mods_mod.filesystem_issue(self.game)
        return [fs] if fs else []


# ------------------------------------------------------------------------------------------
# Shared building blocks
# ------------------------------------------------------------------------------------------

class Row(tk.Frame):
    """A selectable list row with hover and selection colors."""

    def __init__(self, parent, on_select):
        super().__init__(parent, bg=C["surface"], cursor="hand2")
        self.on_select, self.selected, self.hovered = on_select, False, False
        self.stripe = tk.Frame(self, bg=C["surface"], width=3)
        self.stripe.pack(side="left", fill="y")
        self.inner = tk.Frame(self, bg=C["surface"], padx=10, pady=8)
        self.inner.pack(side="left", fill="both", expand=True)
        tk.Frame(self, bg=C["border"], height=1).place(relx=0, rely=1.0, relwidth=1, anchor="sw")

    def bind_all_children(self):
        bound = self.__dict__.setdefault("_bound", set())
        for w in [self, self.inner] + self._descendants(self.inner):
            if isinstance(w, Toggle) or getattr(w, "command", None) or str(w) in bound:
                continue
            bound.add(str(w))
            w.bind("<Button-1>", lambda e: self.on_select(), add="+")
            w.bind("<Enter>", lambda e: self._hover(True), add="+")
            w.bind("<Leave>", lambda e: self._hover(False), add="+")

    def _descendants(self, w):
        out = []
        for c in w.winfo_children():
            out.append(c)
            out += self._descendants(c)
        return out

    def _hover(self, on):
        if on != self.hovered:
            self.hovered = on
            self.paint()

    def set_selected(self, on):
        self.selected = on
        self.paint()

    def paint(self):
        bg = C["select"] if self.selected else (C["hover"] if self.hovered else C["surface"])
        self.configure(bg=bg)
        self.stripe.configure(bg=C["accent"] if self.selected else bg)
        for w in [self.inner] + self._descendants(self.inner):
            if isinstance(w, (Toggle,)):
                w.restyle(bg)
            elif isinstance(w, Pill):
                w.restyle(bg)
            elif isinstance(w, (tk.Frame, tk.Label)) and not getattr(w, "keep_bg", False):
                w.configure(bg=bg)


class Details:
    """Helpers for building a details pane inside a ScrollFrame."""

    def __init__(self, scroll):
        self.scroll = scroll
        self.d = scroll.body

    def clear(self):
        self.scroll.clear()

    def title(self, text, parent=None):
        lb = WrapLabel(parent or self.d, text, font=F["title"])
        lb.pack(fill="x", anchor="w")
        return lb

    def section(self, text):
        tk.Label(self.d, text=text, font=F["section"], bg=C["surface"], fg=C["fg"], anchor="w") \
            .pack(fill="x", pady=(20, 6))

    def text(self, text, dim=False, font=None, parent=None, pady=(0, 0), color=None):
        lb = WrapLabel(parent or self.d, text, fg=color or (C["dim"] if dim else C["fg"]), font=font or F["base"])
        lb.pack(fill="x", anchor="w", pady=pady)
        return lb

    def verdict(self, text, status, parent=None):
        return self.text(text, font=F["verdict"], color=status_color(status), parent=parent)

    def facts(self, items):
        grid = tk.Frame(self.d, bg=C["surface"])
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        for r, (k, v) in enumerate(items):
            tk.Label(grid, text=k, fg=C["dim"], bg=C["surface"], anchor="nw").grid(row=r, column=0, sticky="nw",
                                                                                   padx=(0, 18), pady=2)
            WrapLabel(grid, v).grid(row=r, column=1, sticky="ew", pady=2)
        return grid

    def buttons(self, pady=(12, 0), parent=None):
        box = tk.Frame(parent or self.d, bg=C["surface"])
        box.pack(fill="x", pady=pady, anchor="w")
        return box

    def description(self, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        ReadOnlyText(self.d, text[:4000].strip()).pack(fill="x")

    def tags(self, parent, tags):
        box = tk.Frame(parent, bg=C["surface"])
        box.pack(fill="x", pady=(8, 0), anchor="w")
        for t in tags[:8]:
            badge(box, t).pack(side="left", padx=(0, 5))


# ------------------------------------------------------------------------------------------
# Workshop browser
# ------------------------------------------------------------------------------------------

class ResultRow(Row):
    def __init__(self, page, parent, wid):
        super().__init__(parent, lambda: page.select(wid))
        self.page, self.wid, self.photo = page, wid, None
        self.blank = tk.PhotoImage(width=52, height=52)
        self.thumb = tk.Label(self.inner, image=self.blank, bg=C["surface2"], bd=0)
        self.thumb.keep_bg = True
        self.thumb.pack(side="left", padx=(0, 12))
        self.badges = tk.Frame(self.inner, bg=C["surface"])
        self.badges.pack(side="right")
        texts = tk.Frame(self.inner, bg=C["surface"])
        texts.pack(side="left", fill="both", expand=True)
        self.name = tk.Label(texts, font=F["bold"], anchor="w", bg=C["surface"], fg=C["fg"])
        self.name.pack(fill="x")
        self.sub = tk.Label(texts, font=F["small"], anchor="w", bg=C["surface"], fg=C["dim"])
        self.sub.pack(fill="x")
        self.refresh()

    def set_thumb(self, photo):
        self.photo = photo
        self.thumb.configure(image=photo, width=52, height=52)

    def refresh(self):
        info = self.page.info.get(self.wid, {})
        self.name.configure(text=info.get("title") or self.wid)
        parts = []
        if info.get("subscriptions"):
            parts.append(f"{info['subscriptions']:,} subscribers")
        if info.get("time_updated"):
            parts.append("updated " + fmt_time(info["time_updated"]))
        if info.get("tags"):
            parts.append(", ".join(info["tags"][:3]))
        self.sub.configure(text=" · ".join(parts))
        for c in self.badges.winfo_children():
            c.destroy()
        win = self.page.win
        if win.is_installed(self.wid):
            badge(self.badges, "installed", "ok").pack(side="left")
        elif self.wid in win.subscribing or self.wid in win.downloading:
            badge(self.badges, "downloading…", "accent").pack(side="left")
        self.bind_all_children()
        self.paint()


class BrowsePage(tk.Frame):
    """Search and browse the Workshop, subscribe and unsubscribe."""

    def __init__(self, win, parent):
        super().__init__(parent, bg=C["surface"])
        self.win = win
        self.info = {}
        self.rows = {}
        self.order = []
        self.page_no = 0
        self.total_pages = None
        self.gen = 0
        self.started = False
        self.selected = None

        bar = tk.Frame(self, bg=C["surface"], padx=14, pady=10)
        bar.pack(fill="x")
        self.search = SearchEntry(bar, "Search the Workshop, or paste an item link or ID",
                                  on_change=self._on_search_changed, on_enter=self.start)
        self.search.pack(side="left", fill="x", expand=True)
        self.sort = Select(bar, workshop.BROWSE_SORTS, "trend", on_change=self.start)
        self.sort.pack(side="left", padx=(8, 0))
        Tooltip(self.sort, "Order of results when not searching (searches are ranked by relevance)")
        self.spinner = Spinner(bar)
        self.spinner.pack(side="left", padx=(10, 0))
        self.status = tk.Label(bar, text="", fg=C["dim"], bg=C["surface"], font=F["small"])
        self.status.pack(side="left", padx=(6, 0))
        tk.Frame(self, bg=C["border"], height=1).pack(fill="x")

        paned = tk.PanedWindow(self, orient="horizontal", sashwidth=5, bg=C["border"], bd=0,
                               sashrelief="flat", showhandle=False, opaqueresize=True)
        paned.pack(fill="both", expand=True)
        self.list = ScrollFrame(paned)
        self.placeholder = tk.Label(self.list.body, text="", fg=C["faint"], bg=C["surface"], font=F["hint"],
                                    wraplength=360, justify="center")
        self.more = Pill(self.list.body, "Load more", lambda: self.load_page(self.page_no + 1))
        self.details = ScrollFrame(paned, padx=28, pady=22)
        self.dt = Details(self.details)
        paned.add(self.list, minsize=320, width=460, stretch="never")
        paned.add(self.details, minsize=360, stretch="always")
        self.show_intro()

    # ---- searching -----------------------------------------------------------------------

    def activate(self):
        if not self.started:
            self.start()
        self.search.focus_set()

    def search_for(self, text):
        self.search.set(text)
        self.start()

    def _on_search_changed(self):
        if not self.search.value() and self.started:
            self.start()

    def start(self):
        self.started = True
        self.gen += 1
        for r in self.rows.values():
            r.destroy()
        self.rows, self.order = {}, []
        self.selected = None
        self.total_pages = None
        self.more.pack_forget()
        self.show_intro()
        self.load_page(1)

    def load_page(self, page_no):
        gen = self.gen
        query = self.search.value()
        sort = self.sort.key()
        self.sort.configure(state="disabled" if query else "readonly")
        self.more.set_enabled(False)
        self.spinner.start()
        self._placeholder("Searching the Workshop…" if query else "Loading the Workshop…")
        self.status.configure(text="")

        def work():
            res = workshop.browse(query, sort, page_no)
            if res is None:
                return None
            ids, total = res
            info = workshop.fetch_details(ids)
            ids = [w for w in ids if info.get(w, {}).get("app_id") in (None, "", game_mod.APP_ID)]
            shown = ids if workshop.query_item_id(query) else \
                [w for w in ids if w not in info or workshop.looks_english(info[w])]
            return shown, bool(ids), total, info

        def done(result):
            if gen != self.gen:
                return
            self.spinner.stop()
            self.more.set_enabled(True)
            if result is None:
                self._placeholder(osenv.CERTIFICATES_HELP if osenv.certificates_missing() else
                                  "Couldn't reach the Steam Workshop. Check your connection, then press "
                                  "Enter in the search box to try again.")
                return
            ids, got_any, total, info = result
            self.info.update(info)
            self.page_no, self.total_pages = page_no, total
            self.more.pack_forget()
            for wid in ids:
                if wid in self.rows:
                    continue
                row = ResultRow(self, self.list.body, wid)
                row.pack(fill="x")
                self.rows[wid] = row
                self.order.append(wid)
                self._load_thumb(row)
            self._placeholder(None if self.rows else ("Nothing found." if query else
                                                      "The Workshop returned no items."))
            more = got_any and (total is None or page_no < total)
            if more:
                self.more.pack(pady=14)
            n = len(self.rows)
            self.status.configure(text=f"{n} shown" + (f" of ~{total * 30:,}" if total and more else ""))

        self.win.net.submit(work, done)

    def _placeholder(self, text):
        if text:
            self.placeholder.configure(text=text)
            self.placeholder.pack(fill="x", pady=40, padx=20)
        else:
            self.placeholder.pack_forget()

    def _load_thumb(self, row):
        url = self.info.get(row.wid, {}).get("preview_url")
        if not url:
            return

        def done(path):
            if path and row.winfo_exists():
                img = load_image(path, 52, 52)
                if img:
                    row.set_thumb(img)
        self.win.pictures.submit(lambda: workshop.preview_path(row.wid, url, thumb=True), done)

    def refresh_installed(self):
        for row in self.rows.values():
            row.refresh()
        if self.selected:
            self.show_item(self.selected)

    # ---- details -------------------------------------------------------------------------

    def select(self, wid):
        if self.selected in self.rows:
            self.rows[self.selected].set_selected(False)
        self.selected = wid
        if wid in self.rows:
            self.rows[wid].set_selected(True)
        self.show_item(wid)

    def show_intro(self):
        dt = self.dt
        dt.clear()
        dt.title("Get mods from the Workshop")
        dt.text("Browse or search Slay the Spire 2 mods here, and pick one to see its details.",
                dim=True, pady=(8, 0))
        if steamugc.available(self.win.state.game):
            dt.text("Subscribe asks Steam to download the mod, the same as clicking Subscribe on its "
                    "Workshop page. It shows up under My mods when the download finishes, already "
                    "checked against your game version. Unsubscribe removes it the same way.",
                    dim=True, pady=(10, 0))
        else:
            dt.text("Subscribe opens the mod's page in Steam: click Subscribe there and Steam downloads "
                    "it. This window notices when the download finishes and adds it to My mods.",
                    dim=True, pady=(10, 0))

    def show_item(self, wid):
        dt, win = self.dt, self.win
        dt.clear()
        info = self.info.get(wid, {})
        head = tk.Frame(dt.d, bg=C["surface"])
        head.pack(fill="x")
        preview = tk.Label(head, bg=C["surface"])
        tbox = tk.Frame(head, bg=C["surface"])
        tbox.pack(side="left", fill="x", expand=True)
        dt.title(info.get("title") or wid, parent=tbox)
        if info.get("tags"):
            dt.tags(tbox, info["tags"])
        btns = dt.buttons(parent=tbox)
        mod = win.state.mod_for_workshop(wid)
        installed = win.is_installed(wid)
        if installed:
            if mod:
                Pill(btns, "Show in My mods", lambda: win.show_installed(mod.id), "primary") \
                    .pack(side="left", padx=(0, 8))
            if steamugc.available(win.state.game):
                Pill(btns, "Unsubscribe", lambda: win.unsubscribe(wid, info.get("title") or wid), "danger") \
                    .pack(side="left", padx=(0, 8))
            Pill(btns, "Workshop page", lambda: game_mod.open_workshop_page(wid)).pack(side="left")
        else:
            busy = wid in win.subscribing or wid in win.downloading
            label = "Subscribing…" if wid in win.subscribing else "Downloading…" if busy else \
                "Subscribe" if steamugc.available(win.state.game) else "Subscribe in Steam…"
            b = Pill(btns, label, lambda: win.subscribe([wid]), "primary")
            b.set_enabled(not busy)
            b.pack(side="left", padx=(0, 8))
            Pill(btns, "Workshop page", lambda: game_mod.open_workshop_page(wid)).pack(side="left")
        if info.get("preview_url"):
            self._load_preview(wid, info["preview_url"], preview, head)

        if installed:
            if mod:
                st = win.state.status(mod.id)
                dt.verdict(f"Installed: v{clean_version(mod.version)} · {STATUS_TEXT[st]}", st).pack_configure(pady=(18, 0))
            else:
                dt.text("Downloaded, but no mod manifest found yet. If Steam is still downloading, it will "
                        "appear in My mods when done.", dim=True, pady=(18, 0))
        if info.get("banned"):
            dt.verdict("Steam has banned this item.", S_ERROR).pack_configure(pady=(10, 0))

        dt.section("Details")
        facts = []
        if info.get("subscriptions"):
            facts.append(("Subscribers", f"{info['subscriptions']:,}"))
        if info.get("file_size"):
            facts.append(("Size", human_size(info["file_size"])))
        if info.get("time_created"):
            facts.append(("Published", fmt_time(info["time_created"])))
        if info.get("time_updated"):
            facts.append(("Updated", fmt_time(info["time_updated"])))
        facts.append(("Workshop ID", wid))
        dt.facts(facts)
        desc = workshop.strip_bbcode(info.get("description", ""))
        if desc:
            dt.section("Description")
            dt.description(desc)
        dt.text("Requirements are listed on the Workshop page. Once installed, My mods shows any that "
                "are missing, with a button to get them.", dim=True, font=F["small"], pady=(18, 0))
        self.details.to_top()

    def _load_preview(self, wid, url, target, head):
        self.win.show_preview(wid, url, target, head)


# ------------------------------------------------------------------------------------------
# Installed mods
# ------------------------------------------------------------------------------------------

class ModRow(Row):
    def __init__(self, win, parent, mod_id):
        super().__init__(parent, lambda: win.select(mod_id))
        self.win, self.mod_id = win, mod_id
        self.check = Toggle(self.inner, command=lambda v: win.toggle_mod(mod_id, v),
                            tooltip="Enable or disable this mod")
        self.check.pack(side="left", padx=(0, 10))
        self.dot = tk.Label(self.inner, font=F["dot"], bg=C["surface"])
        self.dot.pack(side="left", padx=(0, 8))
        self.badges = tk.Frame(self.inner, bg=C["surface"])
        self.badges.pack(side="right")
        texts = tk.Frame(self.inner, bg=C["surface"])
        texts.pack(side="left", fill="both", expand=True)
        self.name = tk.Label(texts, font=F["bold"], anchor="w", bg=C["surface"])
        self.name.pack(fill="x")
        self.sub = tk.Label(texts, font=F["small"], anchor="w", bg=C["surface"], fg=C["dim"])
        self.sub.pack(fill="x")
        self.refresh()

    def refresh(self):
        st, mod = self.win.state, self.win.state.mods[self.mod_id]
        on = st.enabled(self.mod_id)
        self.check.set(on)
        status = st.status(self.mod_id)
        self.dot.configure(text=STATUS_DOT[status], fg=status_color(status))
        self.name.configure(text=mod.name, fg=C["fg"] if on else C["faint"])
        parts = [f"v{clean_version(mod.version)}", mod.source_label, human_size(mod.size)]
        if mod.author:
            parts.append(f"by {mod.author}")
        self.sub.configure(text=" · ".join(parts))
        for c in self.badges.winfo_children():
            c.destroy()
        if self.mod_id in st.pending:
            badge(self.badges, "will enable" if st.pending[self.mod_id] else "will disable", "accent") \
                .pack(side="left", padx=(4, 0))
        if mod.update_pending:
            badge(self.badges, "update queued", "ok").pack(side="left", padx=(4, 0))
        if mods_mod.is_library(st.mods, self.mod_id):
            badge(self.badges, "library").pack(side="left", padx=(4, 0))
        self.bind_all_children()
        self.paint()


class Window:
    def __init__(self, root):
        self.root = root
        root.title(APP_NAME)
        root.geometry("1240x800")
        root.minsize(900, 560)
        self.icon = app_icon(root)
        try:
            root.iconphoto(True, self.icon)
        except tk.TclError:
            pass
        self.state = State()
        self.rows = {}
        self.selected = None
        self.health_gen = 0
        self.running = game_mod.is_running()
        self.subscribing, self.downloading = set(), set()
        self._toast_job = None
        self.cpu = Worker("health-check")
        self.net = Worker("workshop")
        self.pictures = WorkerPool("pictures", 6)   # Workshop previews, several at a time
        self.steam = Worker("steam")
        self.workshop_sig = self._workshop_signature()

        install_wheel(root)
        self._build()
        self._bind_keys()
        self.populate()
        self.start_health_check()
        self.start_workshop_fetch()
        root.after(2000, self._poll)
        # tidy the picture cache once things have settled (background thread)
        root.after(5000, lambda: self.cpu.submit(workshop.prune_previews, lambda _r: None))
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        Worker.pump(root)

    # ---- construction --------------------------------------------------------------------

    def _build(self):
        root = self.root
        top = tk.Frame(root, bg=C["surface"], padx=16, pady=10)
        top.pack(fill="x")
        titles = tk.Frame(top, bg=C["surface"])
        titles.pack(side="left")
        tk.Label(titles, text="Slay the Spire 2 Mods", font=F["app"], bg=C["surface"], fg=C["fg"]) \
            .pack(anchor="w")
        self.subtitle = tk.Label(titles, text="", font=F["small"], bg=C["surface"], fg=C["dim"])
        self.subtitle.pack(anchor="w")

        tabs = tk.Frame(top, bg=C["surface2"], padx=3, pady=3)
        tabs.pack(side="left", padx=(28, 0))
        self.tab_buttons = {}
        for key, text in (("installed", "My mods"), ("browse", "Get mods")):
            b = Pill(tabs, text, lambda k=key: self.show_page(k), "ghost", pady=5, padx=16, radius=7)
            b.pack(side="left")
            self.tab_buttons[key] = b

        right = tk.Frame(top, bg=C["surface"])
        right.pack(side="right")
        self.spinner = Spinner(right)
        self.spinner.pack(side="left", padx=(0, 12))
        Tooltip(self.spinner, "Checking mods…")
        self.profile = Select(right, [("", "Profiles…")], "", on_change=self._on_profile_chosen, width=16)
        self.profile.pack(side="left")
        Pill(right, "Save as…", self.save_profile_dialog, tooltip="Save the current setup as a profile") \
            .pack(side="left", padx=(6, 0))
        self.menu_btn = Pill(right, "•••", self._open_menu, "ghost", padx=12, tooltip="More")
        self.menu_btn.pack(side="left", padx=(6, 0))
        self.play_btn = Pill(right, "▶  Play", self.play, "primary", padx=18)
        self.play_btn.pack(side="left", padx=(10, 0))
        self._build_menu()
        tk.Frame(root, bg=C["border"], height=1).pack(fill="x")

        self.bars = tk.Frame(root, bg=C["bg"])
        self.bars.pack(fill="x")
        self.bar_error = InfoBar(self.bars, "error")
        self.bar_running = InfoBar(self.bars, "warn")
        self.bar_running.label.configure(text="Slay the Spire 2 is running. Close the game to apply changes: "
                                              "it overwrites its settings when it exits.")
        self.bar_global = InfoBar(self.bars, "info")
        self.bar_fs = InfoBar(self.bars, "error")
        self.bar_pending = InfoBar(self.bars, "accent")
        self.apply_btn = self.bar_pending.add_button("Apply changes", self.apply, "primary")
        self.bar_pending.add_button("Revert", self.revert)
        self.apply_btn.pack_configure(side="right")

        self.pages = tk.Frame(root, bg=C["surface"])
        self.pages.pack(fill="both", expand=True)
        self.installed = tk.Frame(self.pages, bg=C["surface"])
        self.browse = BrowsePage(self, self.pages)
        self._build_installed()

        self.toast_frame = tk.Frame(root, bg=C["toast_bg"], padx=16, pady=10)
        self.toast_label = tk.Label(self.toast_frame, bg=C["toast_bg"], fg=C["toast_fg"], wraplength=620,
                                    justify="left")
        self.toast_label.pack(side="left")
        close = tk.Label(self.toast_frame, text="✕", bg=C["toast_bg"], fg=C["toast_fg"], cursor="hand2",
                         padx=6)
        close.pack(side="left", padx=(12, 0))
        close.bind("<Button-1>", lambda e: self.toast_frame.place_forget())
        self.show_page("installed")

    def _build_installed(self):
        page = self.installed
        bar = tk.Frame(page, bg=C["surface"], padx=14, pady=10)
        bar.pack(fill="x")
        self.search = SearchEntry(bar, "Search mods by name, ID, author or tag", on_change=self.refilter)
        self.search.pack(side="left", fill="x", expand=True)
        self.filter = Select(bar, FILTERS, "all", on_change=self.refilter)
        self.filter.pack(side="left", padx=(8, 0))
        self.sort = Select(bar, SORTS, "name", on_change=self.refilter)
        self.sort.pack(side="left", padx=(8, 0))
        self.summary = tk.Label(bar, text="", fg=C["dim"], bg=C["surface"], font=F["small"])
        self.summary.pack(side="left", padx=(12, 0))
        tk.Frame(page, bg=C["border"], height=1).pack(fill="x")

        paned = tk.PanedWindow(page, orient="horizontal", sashwidth=5, bg=C["border"], bd=0,
                               sashrelief="flat", showhandle=False, opaqueresize=True)
        paned.pack(fill="both", expand=True)
        self.list = ScrollFrame(paned)
        self.empty = tk.Label(self.list.body, text="No mods match.", fg=C["faint"], bg=C["surface"],
                              font=F["hint"])
        self.details = ScrollFrame(paned, padx=28, pady=22)
        self.dt = Details(self.details)
        paned.add(self.list, minsize=340, width=470, stretch="never")
        paned.add(self.details, minsize=360, stretch="always")

    def _build_menu(self):
        m = tk.Menu(self.root, tearoff=False)
        m.add_command(label="Copy my mod list code", command=self.copy_share_code)
        m.add_command(label="Compare with a friend's code…", command=self.compare_dialog)
        m.add_separator()
        m.add_command(label="Disable all broken mods", command=self.disable_broken)
        m.add_command(label="Apply automatic fixes", command=self.fix_all)
        m.add_command(label="Delete a profile…", command=self.delete_profile_dialog)
        m.add_separator()
        m.add_command(label="Restore settings backup…", command=self.restore_dialog)
        m.add_command(label="Open game logs folder", command=lambda: self.state.game.logs_dir and
                      game_mod.open_path(self.state.game.logs_dir))
        m.add_command(label="Open local mods folder", command=self.open_mods_folder)
        m.add_command(label="Refresh", accelerator="F5", command=self.reload)
        m.add_command(label="About", command=self.about)
        self.menu = m

    def _open_menu(self):
        b = self.menu_btn
        self.menu.tk_popup(b.winfo_rootx(), b.winfo_rooty() + b.winfo_height() + 4)

    def _bind_keys(self):
        r = self.root
        for seq, fn in ((f"<{MOD}-f>", self.focus_search), (f"<{MOD}-r>", self.reload), ("<F5>", self.reload),
                        (f"<{MOD}-s>", self.apply)):
            r.bind_all(seq, lambda e, fn=fn: (fn(), "break")[1])

    def focus_search(self):
        (self.browse.search if self.page == "browse" else self.search).focus_set()

    def show_page(self, key):
        self.page = key
        for k, b in self.tab_buttons.items():
            active = k == key
            b.variant = "outline" if active else "ghost"
            b.font = F["bold"] if active else F["base"]
            b.custom = (C["surface"] if active else None, C["fg"] if active else C["dim"])
            b._draw()
        self.installed.pack_forget()
        self.browse.pack_forget()
        (self.browse if key == "browse" else self.installed).pack(fill="both", expand=True)
        if key == "browse":
            self.browse.activate()

    # ---- population / refresh ------------------------------------------------------------

    def populate(self):
        for r in self.rows.values():
            r.destroy()
        self.rows = {mid: ModRow(self, self.list.body, mid) for mid in self.state.mods}
        self.refilter()
        self.refresh_chrome()
        if self.selected in self.rows:
            self.select(self.selected)
        else:
            self.selected = None
            self.show_overview()

    def refresh_rows(self, ids=None):
        for mid in (ids or list(self.rows)):
            if mid in self.rows:
                self.rows[mid].refresh()
        self.refilter()
        self.refresh_chrome()
        if self.selected:
            self.show_details(self.selected, keep_scroll=True)
        else:
            self.show_overview()

    def refresh_chrome(self):
        st, g = self.state, self.state.game
        enabled = sum(1 for m in st.mods if st.enabled(m))
        self.subtitle.configure(text=f"Game {g.version_text} · {g.branch} branch · {enabled} of "
                                     f"{len(st.mods)} enabled")
        counts = {}
        for mid in st.mods:
            if st.enabled(mid):
                s = st.status(mid)
                counts[s] = counts.get(s, 0) + 1
        bits = []
        if counts.get(S_ERROR):
            bits.append(f"{counts[S_ERROR]} broken")
        if counts.get(S_WARN):
            bits.append(f"{counts[S_WARN]} with warnings")
        self.summary.configure(text=("Enabled: " + ", ".join(bits)) if bits else "",
                               fg=C["error"] if counts.get(S_ERROR) else C["dim"])

        problems = list(g.problems) + ([st.settings_error] if st.settings_error else [])
        self._bar(self.bar_error, "\n".join(problems))
        self._bar(self.bar_running, "x" if self.running else "")

        gissues = st.global_issues()
        fs = next((i for i in gissues if i.key == "short-file-names"), None)
        self.bar_fs.clear_buttons()
        if fs:
            self.bar_fs.add_button("Fix it…", lambda: self.fix_filesystem(fs), "primary")
            self.bar_fs.add_button("Details", lambda: self.info_dialog(fs.title, fs.detail))
        self._bar(self.bar_fs, fs.title + "." if fs else "")
        gtexts = []
        if st.settings and not st.settings.mods_enabled:
            gtexts.append("Mods are switched off globally in the game's settings.")
        gtexts += [f"{i.title}. {i.detail}" for i in gissues if i is not fs]
        if osenv.certificates_missing():
            gtexts.append(osenv.CERTIFICATES_HELP)
        self._bar(self.bar_global, "\n".join(gtexts))

        n = st.change_count()
        if n:
            parts = []
            on = sum(1 for v in st.pending.values() if v)
            off = len(st.pending) - on
            if on:
                parts.append(f"enable {on}")
            if off:
                parts.append(f"disable {off}")
            if st.order_changed():
                parts.append("change load order")
            self.bar_pending.label.configure(
                text=f"Unapplied changes: {', '.join(parts)}. They take effect next time the game starts.")
        self._bar(self.bar_pending, "x" if n else "")
        self.apply_btn.set_enabled(not self.running and st.settings is not None)

    def _bar(self, bar, text):
        if text:
            if text != "x":
                bar.label.configure(text=text)
            if not bar.winfo_ismapped():
                # keep a fixed order
                order = [self.bar_error, self.bar_fs, self.bar_running, self.bar_global, self.bar_pending]
                bar.pack(fill="x")
                for b in order:
                    if b.winfo_ismapped() or b is bar:
                        b.pack_forget()
                        b.pack(fill="x")
        else:
            bar.pack_forget()

    def reload(self, quiet=False):
        self.state.load()
        self.workshop_sig = self._workshop_signature()
        self.populate()
        self.start_health_check()
        self.browse.refresh_installed()
        if not quiet:
            self.toast("Rescanned mods and logs.")

    # ---- background work -----------------------------------------------------------------

    def start_health_check(self):
        self.health_gen += 1
        gen = self.health_gen
        st = self.state
        mods, game, live = st.mods, st.game, self.running
        self.spinner.start()

        def work():
            try:
                api = apicheck.check_mods(mods, game)
            except Exception as e:   # never let a parser bug kill the app
                api = {}
                print("api check failed:", e, file=sys.stderr)
            report = logscan.analyze(mods, game.logs_dir, live=live, api_results=api,
                                     game_version_text=game.version_text, game=game)
            return api, report

        def done(result):
            if gen != self.health_gen:
                return
            st.api, st.report = result
            self.spinner.stop()
            self.refresh_rows()

        self.cpu.submit(work, done)

    def start_workshop_fetch(self, force=False):
        ids = [m.workshop_id for m in self.state.mods.values() if m.workshop_id]

        def done(info):
            self.state.workshop.update(info)
            if self.selected:
                self.show_details(self.selected, keep_scroll=True)

        self.net.submit(lambda: workshop.fetch_details(ids, force=force), done)

    def _poll(self):
        running = game_mod.is_running()
        if running != self.running:
            self.running = running
            if not running:
                self.reload(quiet=True)
                self.toast("Game closed. Rescanned logs for new problems.")
            else:
                self.refresh_chrome()
        sig = self._workshop_signature()
        if sig != self.workshop_sig:
            self._on_workshop_changed(self.workshop_sig[0], sig[0])
        self.root.after(2000, self._poll)

    def _workshop_signature(self):
        g = self.state.game
        try:
            items = frozenset(os.listdir(g.workshop_dir)) if g.workshop_dir else frozenset()
        except OSError:
            items = frozenset()
        try:
            mtime = os.path.getmtime(g.workshop_acf) if g.workshop_acf else 0
        except OSError:
            mtime = 0
        return items, mtime

    def _on_workshop_changed(self, before, after):
        old_ids = {m.workshop_id for m in self.state.mods.values() if m.workshop_id}
        self.reload(quiet=True)
        self.start_workshop_fetch()
        new = [m for m in self.state.mods.values() if m.workshop_id and m.workshop_id not in old_ids]
        self.downloading -= {m.workshop_id for m in new}
        self.browse.refresh_installed()
        gone = before - after
        if new:
            self.toast("Installed from the Workshop: " + ", ".join(m.name for m in new) +
                       ". New mods are enabled; press Play or check them under My mods.")
        elif gone:
            self.toast(f"{plural(len(gone), 'Workshop item')} removed by Steam. Rescanned.")

    def is_installed(self, wid):
        g = self.state.game
        return bool(g.workshop_dir) and os.path.isdir(os.path.join(g.workshop_dir, wid))

    def show_installed(self, mod_id):
        self.show_page("installed")
        if mod_id in self.rows:
            self.search.set("")
            self.filter.set_key("all")
            self.select(mod_id)
            self.list.see(self.rows[mod_id])

    def find_on_workshop(self, text):
        self.show_page("browse")
        self.browse.search_for(text)

    # ---- subscribing ---------------------------------------------------------------------

    def subscribe(self, wids, names=None):
        wids = [w for w in wids if w and w not in self.subscribing and not self.is_installed(w)]
        if not wids:
            return
        game = self.state.game
        if not steamugc.available(game):
            for w in wids:
                game_mod.open_workshop_page(w)
            self.toast("Opened in Steam. After you click Subscribe there, the mod appears here once "
                       "Steam has downloaded it.")
            return
        self.subscribing |= set(wids)
        self.browse.refresh_installed()

        def done(result):
            self.subscribing -= set(wids)
            if isinstance(result, Exception):
                self.browse.refresh_installed()
                resp = self.ask("Couldn't subscribe", f"Steam said: {result}.\n\nYou can subscribe on the "
                                "Workshop page in Steam instead.",
                                [("Close", 0), ("Open in Steam", 1)], default=1)
                if resp == 1:
                    for w in wids:
                        game_mod.open_workshop_page(w)
                return
            ok = [w for w, r in result.items() if r.get("ok")]
            bad = {w: r.get("error", "failed") for w, r in result.items() if not r.get("ok")}
            self.downloading |= {w for w in ok if not self.is_installed(w)}
            self.browse.refresh_installed()
            title = (names or {}).get
            label = lambda w: title(w) or self.browse.info.get(w, {}).get("title") or w  # noqa: E731
            if ok:
                self.toast(("Subscribed to " + ", ".join(label(w) for w in ok) if len(ok) <= 3 else
                            f"Subscribed to {len(ok)} mods") +
                           ". Steam is downloading; they'll appear under My mods when done.")
            if bad:
                self.error("Some subscriptions failed",
                           "\n".join(f"• {label(w)}: {e}" for w, e in bad.items()))

        def work():
            try:
                return steamugc.subscribe(game, wids)
            except steamugc.SteamError as e:
                return e
        self.steam.submit(work, done)

    def unsubscribe(self, wid, name):
        mod = self.state.mod_for_workshop(wid)
        users = [m for m in mods_mod.dependents(self.state.mods, mod.id)] if mod else []
        extra = ""
        if users:
            extra = "\n\nThese installed mods need it:\n" + "\n".join("• " + u.name for u in users[:12])
        if self.ask(f"Unsubscribe from {name}?", "Steam removes the mod's files. You can subscribe "
                    "again any time." + extra, [("Cancel", 0), ("Unsubscribe", 1)], default=0,
                    danger=1) != 1:
            return
        game = self.state.game

        def done(result):
            if isinstance(result, Exception) or not result.get(wid, {}).get("ok"):
                err = result if isinstance(result, Exception) else result.get(wid, {}).get("error")
                self.error("Couldn't unsubscribe", f"Steam said: {err}.")
                return
            self.toast(f"Unsubscribed from {name}. Steam removes it shortly"
                       + (" (after the game closes)." if self.running else "."))

        def work():
            try:
                return steamugc.unsubscribe(game, [wid])
            except steamugc.SteamError as e:
                return e
        self.steam.submit(work, done)

    # ---- filtering / sorting -------------------------------------------------------------

    def _visible(self, mod_id):
        st, mod = self.state, self.state.mods.get(mod_id)
        if mod is None:
            return False
        f = self.filter.key()
        on = st.enabled(mod_id)
        if f == "enabled" and not on or f == "disabled" and on:
            return False
        if f == "problems" and st.status(mod_id) not in (S_ERROR, S_WARN):
            return False
        if f == "changed" and mod_id not in st.pending:
            return False
        if f == "workshop" and mod.source != mods_mod.WORKSHOP or f == "local" and mod.source != mods_mod.LOCAL:
            return False
        q = self.search.value().lower()
        if q:
            info = st.workshop_info(mod) or {}
            hay = " ".join([mod.name, mod.id, mod.author, info.get("title", ""),
                            " ".join(info.get("tags", []))]).lower()
            return all(word in hay for word in q.split())
        return True

    def _sort_key(self, mod_id):
        st, m = self.state, self.state.mods[mod_id]
        key, name = self.sort.key(), m.name.lower()
        if key == "status":
            return (STATUS_RANK.get(st.status(m.id), 9), not st.enabled(m.id), name)
        if key == "order":
            return (st.order.index(m.id) if m.id in st.order else 1e9, name)
        if key == "updated":
            return (-(m.installed_time or 0), name)
        if key == "size":
            return (-m.size, name)
        return (name,)

    def refilter(self):
        if not hasattr(self, "rows"):
            return
        ids = sorted((m for m in self.rows if self._visible(m)), key=self._sort_key)
        for r in self.rows.values():
            r.pack_forget()
        self.empty.pack_forget()
        for mid in ids:
            self.rows[mid].pack(fill="x")
        if not ids:
            self.empty.pack(pady=40)

    # ---- details pane --------------------------------------------------------------------

    def select(self, mod_id):
        if self.selected in self.rows:
            self.rows[self.selected].set_selected(False)
        self.selected = mod_id
        if mod_id in self.rows:
            self.rows[mod_id].set_selected(True)
        self.show_details(mod_id)

    def show_overview(self):
        dt, st = self.dt, self.state
        dt.clear()
        dt.title("Overview")
        if not st.report:
            dt.text("Checking your mods: reading game logs and each mod's code…", dim=True, pady=(8, 0))
            return
        broken = sorted((m for m in st.mods if st.enabled(m) and st.status(m) == S_ERROR),
                        key=lambda i: st.mods[i].name.lower())
        warns = sorted((m for m in st.mods if st.enabled(m) and st.status(m) == S_WARN),
                       key=lambda i: st.mods[i].name.lower())
        if not broken and not warns:
            dt.verdict("All enabled mods look healthy.", S_OK).pack_configure(pady=(10, 0))
        if broken:
            dt.verdict(f"{len(broken)} enabled mod{'s look' if len(broken) != 1 else ' looks'} broken on "
                       "your game version:", S_ERROR) \
                .pack_configure(pady=(10, 4))
            for mid in broken:
                self._link(dt.d, mid).pack(anchor="w")
            b = dt.buttons(pady=(10, 0))
            Pill(b, f"Disable these {len(broken)}", self.disable_broken, "danger").pack(side="left")
        if warns:
            dt.section("With warnings")
            for mid in warns:
                self._link(dt.d, mid).pack(anchor="w")
        dt.section("How health is checked")
        dt.text("• Code check: each mod's compiled code is compared against your installed game, so mods "
                "that call game functions that changed are caught before you play.\n"
                "• Game logs: startup failures, crashes and in-game errors from your recent sessions are "
                "matched to the mod that caused them. Results are remembered after the game deletes old "
                "logs.\n"
                "• Files: missing or misnamed files and missing dependencies.", dim=True)
        dt.text("Select a mod on the left for details. Changes are only written when you press Apply.",
                dim=True, pady=(10, 0))

    def _link(self, parent, mod_id, suffix=""):
        mod = self.state.mods[mod_id]
        status = self.state.status(mod_id)
        box = tk.Frame(parent, bg=parent.cget("bg"), cursor="hand2")
        dot = tk.Label(box, text=STATUS_DOT[status], fg=status_color(status), bg=box.cget("bg"), font=F["dot"])
        dot.pack(side="left")
        name = tk.Label(box, text=mod.name, fg=C["accent"], bg=box.cget("bg"))
        name.pack(side="left", padx=(6, 0))
        if suffix:
            tk.Label(box, text=suffix, fg=C["dim"], bg=box.cget("bg")).pack(side="left", padx=(6, 0))
        for w in (box, dot, name):
            w.bind("<Button-1>", lambda e: self.show_installed(mod_id))
            w.bind("<Enter>", lambda e: name.configure(font=F["bold"]))
            w.bind("<Leave>", lambda e: name.configure(font=F["base"]))
        return box

    def show_details(self, mod_id, keep_scroll=False):
        st, dt = self.state, self.dt
        mod = st.mods.get(mod_id)
        if mod is None:
            return
        y = self.details.canvas.yview()[0] if keep_scroll else 0
        dt.clear()
        info = st.workshop_info(mod) or {}

        head = tk.Frame(dt.d, bg=C["surface"])
        head.pack(fill="x")
        preview = tk.Label(head, bg=C["surface"])
        tbox = tk.Frame(head, bg=C["surface"])
        tbox.pack(side="left", fill="x", expand=True)
        dt.title(mod.name, parent=tbox)
        sub = [f"v{clean_version(mod.version)}"] + ([f"by {mod.author}"] if mod.author else []) + [mod.id]
        dt.text(" · ".join(sub), dim=True, parent=tbox, pady=(2, 0))
        if info.get("tags"):
            dt.tags(tbox, info["tags"])
        sw = tk.Frame(tbox, bg=C["surface"])
        sw.pack(anchor="w", pady=(12, 0))
        Toggle(sw, st.enabled(mod_id), lambda v: self.toggle_mod(mod_id, v), style="switch").pack(side="left")
        tk.Label(sw, text=("Enabled" if st.enabled(mod_id) else "Disabled") +
                 ("  (not applied yet)" if mod_id in st.pending else ""), bg=C["surface"],
                 fg=C["fg"]).pack(side="left", padx=(10, 0))
        if mod.workshop_id and info.get("preview_url"):
            self._load_preview(mod.workshop_id, info["preview_url"], preview, head)

        status = st.status(mod_id)
        tested = st.report.tested.get(mod_id) if st.report else None
        verdict = {
            S_ERROR: "Problems found: this mod is broken or likely broken on your setup.",
            S_WARN: "Works, but with warnings.",
            S_STALE: "Had problems before that look fixed now. Try it again.",
            S_OK: f"No problems found (last loaded {tested[0]})." if tested else "No problems found.",
            S_UNTESTED: "No problems found in its files, but the game hasn't loaded it in any session "
                        "I can see.",
            S_CHECKING: "Checking…",
        }[status]
        dt.section("Health")
        dt.verdict(verdict, status)
        for iss in st.issues(mod_id):
            self._issue_card(dt.d, mod, iss)

        dt.section("Details")
        facts = [
            ("Version", clean_version(mod.version)),
            ("Needs game", clean_version(mod.min_game_version) + "+" if mod.min_game_version else "not stated"),
            ("Source", "Steam Workshop" if mod.source == mods_mod.WORKSHOP else "Local mods folder"),
            ("Size", human_size(mod.size)),
            ("Co-op", "Affects gameplay (everyone needs it)" if mod.affects_gameplay
             else "Client-side only (others don't need it)"),
        ]
        if mod.installed_time:
            facts.append(("Installed update", fmt_time(mod.installed_time) +
                          ("  (newer version queued in Steam)" if mod.update_pending else "")))
        if info.get("subscriptions"):
            facts.append(("Subscribers", f"{info['subscriptions']:,}"))
        facts.append(("Load position", f"{st.order.index(mod_id) + 1} of {len(st.order)}"
                      if mod_id in st.order else "?"))
        dt.facts(facts)

        btns = dt.buttons(pady=(14, 0))
        Pill(btns, "Open folder", lambda: game_mod.open_path(mod.dir)).pack(side="left", padx=(0, 8))
        if mod.workshop_id:
            Pill(btns, "Workshop page", lambda: game_mod.open_workshop_page(mod.workshop_id)) \
                .pack(side="left", padx=(0, 8))
            if steamugc.available(st.game):
                Pill(btns, "Unsubscribe", lambda: self.unsubscribe(mod.workshop_id, mod.name), "danger") \
                    .pack(side="left", padx=(0, 8))
        Pill(btns, "▼", lambda: self.move(mod_id, 1), padx=10, tooltip="Load later").pack(side="right")
        Pill(btns, "▲", lambda: self.move(mod_id, -1), padx=10, tooltip="Load earlier") \
            .pack(side="right", padx=(0, 4))
        tk.Label(btns, text="Load order", fg=C["dim"], bg=C["surface"]).pack(side="right", padx=(0, 8))

        if mod.dependencies:
            dt.section("Requires")
            for dep_id, min_v in mod.dependencies:
                self._dep_row(dt.d, dep_id, min_v)
        users = mods_mod.dependents(st.mods, mod_id)
        if users:
            dt.section(f"Required by ({len(users)})")
            flow = tk.Frame(dt.d, bg=C["surface"])
            flow.pack(fill="x")
            for i, u in enumerate(sorted(users, key=lambda m: m.name.lower())):
                self._link(flow, u.id).grid(row=i // 2, column=i % 2, sticky="w", padx=(0, 24), pady=1)

        desc = workshop.strip_bbcode(info.get("description", "")) or mod.description
        if desc:
            dt.section("Description")
            dt.description(desc)
        if keep_scroll:
            self.root.after_idle(lambda: self.details.canvas.yview_moveto(y))

    def _issue_card(self, parent, mod, iss):
        sev = {"error": "error", "warn": "warn"}.get(iss.severity, "info")
        bg = soft(sev) if not iss.stale else C["surface2"]
        card = tk.Frame(parent, bg=bg)
        card.pack(fill="x", pady=(8, 0))
        tk.Frame(card, bg=C[sev] if not iss.stale else C["faint"], width=4).pack(side="left", fill="y")
        body = tk.Frame(card, bg=bg, padx=14, pady=10)
        body.pack(side="left", fill="both", expand=True)
        title = iss.title + (f"   ×{iss.count}" if iss.count > 1 else "")
        WrapLabel(body, title, bg=bg, font=F["bold"],
                  fg=C["fg"] if not iss.stale else C["dim"]).pack(fill="x")
        if iss.detail:
            detail = iss.detail.replace(logscan.SAMPLE_SEP, "\n\n")
            prose, _, listing = detail.partition("\n• ")
            looks_raw = any(tok in prose for tok in ("Exception", "res://", "/home/", "/Users/", ":\\",
                                                     "  at ", "ERROR"))
            if looks_raw:
                prose, listing = "", detail
            elif listing:
                listing = "• " + listing
            if prose:
                WrapLabel(body, prose, bg=bg, fg=C["dim"]).pack(fill="x", pady=(4, 0))
            if listing:
                code_bg = blend(C["fg"], bg, 0.05)
                box = tk.Frame(body, bg=code_bg, padx=10, pady=8)
                txt = ReadOnlyText(box, listing, font=F["mono"], bg=code_bg, fg=C["fg"])
                txt.pack(fill="x")
                if len(listing) > 700:
                    def toggle(btn=None):
                        if box.winfo_ismapped():
                            box.pack_forget()
                            more.set_text("Show details")
                        else:
                            box.pack(fill="x", pady=(6, 0), before=more)
                            more.set_text("Hide details")
                    more = Pill(body, "Show details", toggle, "ghost", padx=8, pady=3, font=F["small"])
                    more.pack(anchor="w", pady=(4, 0))
                else:
                    box.pack(fill="x", pady=(6, 0))
        meta = []
        if iss.source == "code check":
            meta.append("found by checking the mod's code against your game")
        elif iss.source not in ("static", "", None):
            meta.append(f"from the game log of {time.strftime('%b %d %H:%M', time.localtime(iss.when))}"
                        if iss.when else "from the game log")
        if iss.stale:
            meta.append(iss.stale_note or "this may be fixed")
        if meta:
            WrapLabel(body, " · ".join(meta), bg=bg, fg=C["faint"], font=F["small"]).pack(fill="x", pady=(6, 0))
        if iss.fix:
            fix = iss.fix
            cmd = (lambda: self.fix_filesystem(iss)) if fix[0] == "relocate-user-data" else \
                (lambda: self.apply_fix(mod, fix))
            Pill(body, "Fix it", cmd, "primary", pady=4).pack(anchor="w", pady=(8, 0))

    def _dep_row(self, parent, dep_id, min_v):
        st = self.state
        dep = st.mods.get(dep_id)
        row = tk.Frame(parent, bg=C["surface"])
        row.pack(fill="x", pady=2)
        if dep is None:
            tk.Label(row, text="●", fg=C["error"], bg=C["surface"], font=F["dot"]).pack(side="left")
            tk.Label(row, text=f"{dep_id}: not installed", bg=C["surface"]).pack(side="left", padx=(6, 10))
            Pill(row, "Find on Workshop", lambda: self.find_on_workshop(dep_id), pady=3, padx=10) \
                .pack(side="left")
            return
        on = st.enabled(dep_id)
        suffix = f"v{clean_version(dep.version)}" + (f" (needs {min_v}+)" if min_v else "") + \
            (" · enabled" if on else " · disabled")
        self._link(row, dep_id, suffix).pack(side="left")
        if not on:
            Pill(row, "Enable", lambda: self.toggle_mod(dep_id, True), "primary", pady=3, padx=10) \
                .pack(side="left", padx=(10, 0))

    def _load_preview(self, wid, url, target, head):
        self.show_preview(wid, url, target, head, anchor="n")

    PREVIEW_PX = 168

    def show_preview(self, wid, url, target, head, **pack):
        """Put a Workshop item's preview in a details pane: the small list thumbnail at once
        (scaled up, so a little soft) if it's cached, then the sharp version when it's ready."""
        state = {"sharp": False}

        def put(img, sharp):
            if not img or not target.winfo_exists() or state["sharp"]:
                return
            state["sharp"] = sharp
            target.image = img
            target.configure(image=img)
            if not target.winfo_manager():
                target.pack(side="left", padx=(0, 20), before=head.winfo_children()[1], **pack)

        thumb = workshop.thumb_cache_path(wid)
        if os.path.exists(thumb):
            small = load_image(thumb, 96, 96)
            if small and small.width():
                # integer zoom/subsample: about 1.75x, up to PREVIEW_PX
                z = max(1, round(self.PREVIEW_PX / small.width() * 4))
                put(small.zoom(z).subsample(4) if z > 4 else small, False)
        self.pictures.submit(lambda: workshop.preview_path(wid, url),
                             lambda path: put(load_image(path, self.PREVIEW_PX, self.PREVIEW_PX), True)
                             if path else None)

    # ---- actions -------------------------------------------------------------------------

    def toggle_mod(self, mod_id, value):
        st = self.state
        if st.enabled(mod_id) == value:
            return
        mod = st.mods[mod_id]
        changed = {mod_id}
        if value:
            needed = [d for d in mods_mod.dependency_closure(st.mods, mod_id) if not st.enabled(d)]
            st.set_pending(mod_id, True)
            for d in needed:
                st.set_pending(d, True)
                changed.add(d)
            if needed:
                self.toast(f"Also enabled what {mod.name} needs: " + ", ".join(st.mods[d].name for d in needed))
            missing = [d for d, _ in mod.dependencies if d not in st.mods]
            if missing:
                self.toast(f"{mod.name} needs mods you don't have: {', '.join(missing)}")
        else:
            users = [d for d in mods_mod.dependent_closure(st.mods, mod_id) if st.enabled(d)]
            if users:
                names = "\n".join("• " + st.mods[u].name for u in sorted(users, key=lambda u: st.mods[u].name.lower()))
                resp = self.ask(f"Other mods need {mod.name}", f"These enabled mods won't load without it:\n{names}",
                                [("Cancel", 0), (f"Disable only {mod.name}", 1),
                                 (f"Disable all {len(users) + 1}", 2)], default=2)
                if not resp:
                    self.refresh_rows([mod_id])
                    return
                if resp == 2:
                    for u in users:
                        st.set_pending(u, False)
                        changed.add(u)
            st.set_pending(mod_id, False)
        self.refresh_rows(changed)

    def move(self, mod_id, delta):
        order = self.state.order
        i = order.index(mod_id)
        j = max(0, min(len(order) - 1, i + delta))
        if i != j:
            order[i], order[j] = order[j], order[i]
            if self.sort.key() != "order":
                self.sort.set_key("order", quiet=True)
                self.toast("Sorted by load order. Note: the game always loads a mod's requirements before it.")
            self.refresh_rows([mod_id, order[i]])

    def revert(self):
        self.state.pending.clear()
        self.state.order = list(self.state.saved_order)
        self.refresh_rows()

    def apply(self):
        st = self.state
        if not st.change_count():
            return
        if not st.settings:
            self.error("Can't apply", "The game's settings.save wasn't found. Launch the game once first.")
            return
        try:
            backup = st.settings.save(st.states(), st.order, st.mods)
        except settings_mod.GameRunningError as e:
            self.error("Close the game first", str(e))
            return
        except OSError as e:
            self.error("Couldn't write settings", str(e))
            return
        n = st.change_count()
        st.pending.clear()
        st.load()
        self.refresh_rows()
        self.toast(f"Applied {plural(n, 'change')}. Backup saved: {os.path.basename(backup)}")

    def play(self):
        if self.state.change_count():
            resp = self.ask("Apply your changes first?",
                            "You have unapplied changes. The game only reads mod settings when it starts.",
                            [("Cancel", 0), ("Play without them", 1), ("Apply and play", 2)], default=2)
            if not resp:
                return
            if resp == 2:
                self.apply()
                if self.state.change_count():
                    return
        game_mod.launch()
        self.toast("Starting Slay the Spire 2 through Steam…")

    def disable_broken(self):
        st = self.state
        broken = [m for m in st.mods if st.enabled(m) and st.status(m) == S_ERROR]
        if not broken:
            self.toast("No enabled mods are marked broken.")
            return
        for m in broken:
            st.set_pending(m, False)
        self.refresh_rows(broken)
        self.toast(f"Marked {plural(len(broken), 'broken mod')} to disable. Review, then Apply.")

    def apply_fix(self, mod, fix):
        if game_mod.is_running():
            self.error("Close the game first", "The game may be using these files right now.")
            return
        try:
            mods_mod.apply_fix(fix)
        except OSError as e:
            self.error("Fix failed", str(e))
            return
        self.toast(f"Fixed {mod.name}: linked {os.path.basename(fix[2])} → {fix[1]}. "
                   "If a mod update undoes this, the Fix button will come back.")
        self.reload(quiet=True)

    def fix_filesystem(self, iss):
        if game_mod.is_running():
            self.error("Close the game first", "The game is using its data folder right now.")
            return
        _, source, target = iss.fix
        if self.ask("Move the game's data folder?",
                    f"{iss.detail}\n\nFrom: {source}\nTo: {target}",
                    [("Cancel", 0), ("Move it", 1)], default=1) != 1:
            return
        self.toast("Moving the game's data folder…")
        self.root.update()
        try:
            mods_mod.apply_fix(iss.fix)
        except OSError as e:
            self.error("Couldn't move the folder", f"{e}\n\nNothing was changed.")
            return
        self.reload(quiet=True)
        self.toast(f"Done. The game's data now lives in {target}; mods can save long file names again.")

    def fix_all(self):
        if game_mod.is_running():
            self.error("Close the game first", "The game may be using these files right now.")
            return
        fixed = 0
        for mod in self.state.mods.values():
            for iss in mod.static_issues:
                if iss.fix:
                    try:
                        mods_mod.apply_fix(iss.fix)
                        fixed += 1
                    except OSError as e:
                        print("fix failed:", e, file=sys.stderr)
        self.reload(quiet=True)
        fs = mods_mod.filesystem_issue(self.state.game)
        msg = f"Applied {plural(fixed, 'automatic fix', 'es')}." if fixed else "Nothing to fix automatically."
        if fs:
            msg += " The encrypted-home-folder fix needs your confirmation: use Fix it in the red bar."
        self.toast(msg)

    def open_mods_folder(self):
        path = self.state.game.mods_dir
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        game_mod.open_path(path)

    # ---- profiles ------------------------------------------------------------------------

    def refresh_profiles(self):
        self.profile.set_choices([("", "Profiles…")] + [(n, n) for n in profiles.list_profiles()], "")

    def _on_profile_chosen(self):
        name = self.profile.key()
        if not name:
            return
        try:
            data = profiles.load_profile(name)
        except (OSError, ValueError) as e:
            self.error("Couldn't load profile", str(e))
            return
        st = self.state
        wanted = {m["id"]: m.get("enabled", True) for m in data.get("mods", [])}
        for mid in st.mods:
            st.set_pending(mid, wanted.get(mid, False))
        order = [i for i in data.get("order", []) if i in st.mods]
        st.order = order + [i for i in st.order if i not in order]
        missing = [m for m in data.get("mods", []) if m.get("enabled") and m["id"] not in st.mods]
        self.refresh_rows()
        msg = f"Loaded profile “{name}”. Review, then Apply."
        if missing:
            msg += " Not installed: " + ", ".join(m.get("name", m["id"]) for m in missing)
        self.toast(msg)
        self.root.after_idle(self.refresh_profiles)

    def save_profile_dialog(self):
        dlg = Dialog(self.root, "Save profile")
        dlg.heading("Save profile", "Save which mods are enabled (and the load order) under a name, e.g. "
                                    "“Solo” or “Co-op with Sam”. Unapplied changes are included.")
        var = tk.StringVar(dlg)
        entry = ttk.Entry(dlg.body, font=F["base"], textvariable=var)
        entry.pack(fill="x", pady=(14, 0))
        entry.focus_set()
        dlg.button("Save", 1, "primary", default=True)
        dlg.button("Cancel", 0)
        typed = {}
        var.trace_add("write", lambda *_: typed.update(text=var.get()))
        if dlg.run() != 1:
            return
        text = typed.get("text", "").strip()
        if not text:
            return
        st = self.state
        profiles.save_profile(text, st.states(), st.order, st.mods)
        self.refresh_profiles()
        self.toast(f"Saved profile “{text}”.")

    def delete_profile_dialog(self):
        names = profiles.list_profiles()
        if not names:
            self.toast("No saved profiles.")
            return
        dlg = Dialog(self.root, "Delete profile")
        dlg.heading("Delete a profile")
        sel = Select(dlg.body, [(n, n) for n in names], names[0])
        sel.pack(fill="x", pady=(14, 0))
        dlg.button("Delete", 1, "danger")
        dlg.button("Cancel", 0)
        chosen = {}
        sel.bind("<<ComboboxSelected>>", lambda e: chosen.update(k=sel.key()), add="+")
        if dlg.run() == 1:
            profiles.delete_profile(chosen.get("k", names[0]))
            self.refresh_profiles()

    # ---- co-op ---------------------------------------------------------------------------

    def copy_share_code(self):
        st = self.state
        code = profiles.share_code(st.states(), st.mods, st.game)
        self.root.clipboard_clear()
        self.root.clipboard_append(code)
        n = sum(1 for m in st.mods if st.enabled(m))
        note = " (includes unapplied changes)" if st.change_count() else ""
        self.toast(f"Copied a code for your {plural(n, 'enabled mod')}{note}. Paste it to your friend; they "
                   "use Compare with a friend's code.")

    def compare_dialog(self):
        try:
            clip = self.root.clipboard_get()
        except tk.TclError:
            clip = ""
        found = profiles.find_share_code(clip)
        if found and found == profiles.share_code(self.state.states(), self.state.mods, self.state.game):
            found = None            # that's our own code, not the friend's
        dlg = Dialog(self.root, "Compare with a friend", width=520)
        dlg.heading("Paste your friend's code",
                    "They get it from ••• → Copy my mod list code. Pasting their whole message works too.")
        box = tk.Frame(dlg.body, bg=C["border"], padx=1, pady=1)
        box.pack(fill="both", expand=True, pady=(14, 0))
        text = tk.Text(box, height=5, wrap="char", font=F["mono"], bg=C["input"], fg=C["fg"], relief="flat",
                       bd=0, highlightthickness=0, insertbackground=C["fg"], padx=8, pady=6)
        text.pack(fill="both", expand=True)
        if found:
            text.insert("1.0", found)
        text.focus_set()
        pasted = {"text": found or ""}
        text.bind("<KeyRelease>", lambda e: pasted.update(text=text.get("1.0", "end")))
        text.bind("<<Paste>>", lambda e: self.root.after_idle(lambda: pasted.update(text=text.get("1.0", "end"))))
        dlg.button("Compare", 1, "primary", default=True)
        dlg.button("Cancel", 0)
        if dlg.run() != 1:
            return
        try:
            theirs = profiles.parse_share_code(pasted["text"])
        except ValueError as e:
            self.error("Couldn't read that code", str(e))
            return
        # Workshop mods come without names; look them up before showing the comparison.
        wids = [m["workshop_id"] for m in theirs["mods"] if m.get("workshop_id")]

        def done(info):
            self.state.workshop.update(info or {})
            for m in theirs["mods"]:
                title = (self.state.workshop.get(m.get("workshop_id") or "") or {}).get("title")
                if title:
                    m["name"] = title
            self.show_comparison(theirs)
        self.toast("Comparing…")
        self.net.submit(lambda: workshop.fetch_details(wids), done)

    def show_comparison(self, theirs):
        st = self.state
        diff = profiles.compare(theirs, st.states(), st.mods, st.game)

        dlg = Dialog(self.root, "Compare mod lists", width=640)
        dlg.geometry("660x600")
        scroll = ScrollFrame(dlg.body)
        scroll.pack(fill="both", expand=True)
        dt = Details(scroll)
        if profiles.diff_is_clean(diff):
            dt.verdict("Your gameplay mods match. You should be able to play together.", S_OK)
        else:
            dt.verdict("Differences that will block co-op:", S_ERROR)
        if diff["branch"]:
            dt.text(f"Different game branch: you're on {diff['branch'][0]}, they're on {diff['branch'][1]}. "
                    "Switch in Steam: game Properties → Betas.", pady=(8, 0))
        elif diff["game_version"]:
            dt.text(f"Different game version: {diff['game_version'][0]} vs {diff['game_version'][1]}. "
                    "One of you needs to update.", pady=(8, 0))

        def section(title, items, make):
            if items:
                dt.section(title)
                for it in items:
                    make(it)

        missing_ids = [t["workshop_id"] for t in diff["missing"] if t.get("workshop_id")]

        def missing_row(t):
            row = tk.Frame(dt.d, bg=C["surface"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=t.get("name") or t.get("id"), bg=C["surface"]).pack(side="left")
            if t.get("version"):
                tk.Label(row, text=f"v{clean_version(t['version'])}", fg=C["dim"], bg=C["surface"]) \
                    .pack(side="left", padx=(6, 0))
            if t.get("workshop_id"):
                Pill(row, "Subscribe", lambda: self.subscribe([t["workshop_id"]], {t["workshop_id"]: t.get("name")}),
                     pady=3, padx=10).pack(side="right")

        section("They have, you don't have installed", diff["missing"], missing_row)
        if len(missing_ids) > 1:
            b = dt.buttons(pady=(8, 0))
            names = {t["workshop_id"]: t.get("name") for t in diff["missing"] if t.get("workshop_id")}
            Pill(b, f"Subscribe to all {len(missing_ids)}", lambda: self.subscribe(missing_ids, names),
                 "primary", pady=4).pack(side="left")
        section("They have it enabled, you have it disabled", diff["disabled"],
                lambda t: dt.text(t.get("name") or t.get("id")))
        section("You have enabled, they don't", diff["extra"], lambda m: dt.text(m.name))
        section("Different versions (one of you needs the Workshop update)", diff["version"],
                lambda pair: dt.text(f"{pair[0].name}: you have v{clean_version(pair[0].version)}, theirs is "
                                     + (f"v{clean_version(pair[1]['version'])}" if pair[1].get("version")
                                        else "a different version")))
        if diff["optional_only"]:
            dt.text(f"{plural(len(diff['optional_only']), 'client-side mod')} differ too, which doesn't "
                    "matter for co-op.", dim=True, pady=(14, 0))
        match = dlg.button("Match their setup", 1, "primary")
        match.set_enabled(bool(diff["disabled"] or diff["extra"]))
        Tooltip(match, "Enable the mods they use that you have, and disable gameplay mods they don't have. "
                       "Nothing is written until you Apply.")
        dlg.button("Close", 0)
        if dlg.run() == 1:
            for t in diff["disabled"]:
                st.set_pending(t["id"], True)
            for m in diff["extra"]:
                st.set_pending(m.id, False)
            self.refresh_rows()
            extra = f" You still need {plural(len(diff['missing']), 'mod')} they have." if diff["missing"] else ""
            self.toast("Matched their setup. Review, then Apply." + extra)

    # ---- backups -------------------------------------------------------------------------

    def restore_dialog(self):
        backups = settings_mod.list_backups()
        if not backups:
            self.toast("No backups yet. One is made every time you apply changes.")
            return
        dlg = Dialog(self.root, "Restore settings backup")
        dlg.heading("Restore settings backup",
                    "Restores the game's settings.save (including which mods are enabled) to how it was "
                    "before an earlier Apply. Your current file is backed up first.")
        sel = Select(dlg.body, [(b, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(b))))
                                for b in backups], backups[0])
        sel.pack(fill="x", pady=(14, 0))
        chosen = {"k": backups[0]}
        sel.bind("<<ComboboxSelected>>", lambda e: chosen.update(k=sel.key()), add="+")
        dlg.button("Restore", 1, "primary")
        dlg.button("Cancel", 0)
        if dlg.run() != 1:
            return
        try:
            settings_mod.restore_backup(chosen["k"], self.state.game.settings_path)
        except (settings_mod.GameRunningError, OSError) as e:
            self.error("Restore failed", str(e))
            return
        self.state.pending.clear()
        self.reload(quiet=True)
        self.toast("Settings restored.")

    # ---- small dialogs -------------------------------------------------------------------

    def toast(self, text):
        self.toast_label.configure(text=text)
        self.toast_frame.place(relx=0.5, y=74, anchor="n")
        self.toast_frame.lift()
        if self._toast_job:
            self.root.after_cancel(self._toast_job)
        self._toast_job = self.root.after(7000, self.toast_frame.place_forget)

    def ask(self, title, text, buttons, default=None, danger=None):
        dlg = Dialog(self.root, title)
        dlg.heading(title, text)
        for name, value in reversed(buttons):
            variant = "danger" if value == danger else "primary" if value == default else "secondary"
            dlg.button(name, value, variant, default=value == default)
        return dlg.run()

    def error(self, title, text):
        dlg = Dialog(self.root, title)
        tk.Label(dlg.body, text=title, font=F["section"], fg=C["error"], bg=C["surface"], anchor="w") \
            .pack(fill="x")
        WrapLabel(dlg.body, text, fg=C["dim"]).pack(fill="x", pady=(6, 0))
        dlg.button("Close", 0, "primary", default=True)
        dlg.run()

    def info_dialog(self, title, text):
        dlg = Dialog(self.root, title)
        dlg.heading(title, text)
        dlg.button("Close", 0, "primary", default=True)
        dlg.run()

    def about(self):
        dlg = Dialog(self.root, "About")
        row = tk.Frame(dlg.body, bg=C["surface"])
        row.pack(fill="x")
        tk.Label(row, image=self.icon, bg=C["surface"]).pack(side="left", padx=(0, 16))
        t = tk.Frame(row, bg=C["surface"])
        t.pack(side="left", fill="x")
        tk.Label(t, text=APP_NAME, font=F["section"], bg=C["surface"]).pack(anchor="w")
        tk.Label(t, text=f"Version {__version__}", fg=C["dim"], bg=C["surface"]).pack(anchor="w")
        WrapLabel(dlg.body, "An external mod manager for Slay the Spire 2. Reads the game's own files; "
                            "never patches the game.", fg=C["dim"]).pack(fill="x", pady=(14, 0))
        dlg.button("Close", 0, "primary", default=True)
        dlg.run()

    def _on_close(self):
        if self.state.change_count():
            resp = self.ask("Apply changes before closing?",
                            "You have changes that haven't been written to the game's settings.",
                            [("Cancel", 0), ("Discard", 1), ("Apply", 2)], default=2)
            if not resp:
                return
            if resp == 2:
                self.apply()
                if self.state.change_count():
                    return
        Worker.closing = True
        self.root.destroy()


def run():
    if osenv.IS_WINDOWS:
        try:   # crisp text on high-DPI screens instead of blurry bitmap scaling
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk(className="sts2-mod-loader")
    # Dark by default everywhere; STS2ML_THEME=light (or =auto to follow the desktop) overrides it.
    theme = os.environ.get("STS2ML_THEME", "dark").lower()
    apply_theme(root, dark=None if theme == "auto" else theme != "light")
    win = Window(root)
    win.refresh_profiles()
    root.mainloop()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)   # background threads may be blocked on network I/O
