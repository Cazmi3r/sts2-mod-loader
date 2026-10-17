"""GTK 3 front end."""

import os
import queue
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango  # noqa: E402

from . import APP_NAME, __version__  # noqa: E402
from . import apicheck, logscan, profiles, workshop  # noqa: E402
from . import game as game_mod  # noqa: E402
from . import mods as mods_mod  # noqa: E402
from . import settings as settings_mod  # noqa: E402
from .logscan import S_ERROR, S_OK, S_STALE, S_UNTESTED, S_WARN, STATUS_RANK  # noqa: E402

S_CHECKING = "checking"
STATUS_TEXT = {
    S_ERROR: "Broken or likely broken",
    S_WARN: "Has warnings",
    S_STALE: "Had problems that look fixed now",
    S_OK: "No problems found",
    S_UNTESTED: "Not tested yet",
    S_CHECKING: "Checking…",
}
STATUS_COLOR = {S_ERROR: "#e5534b", S_WARN: "#d4a72c", S_STALE: "#9a8f6a", S_OK: "#3fb950"}
STATUS_DOT = {S_ERROR: "●", S_WARN: "●", S_STALE: "◐", S_OK: "●", S_UNTESTED: "○", S_CHECKING: "○"}

FILTERS = [("all", "All mods"), ("enabled", "Enabled"), ("disabled", "Disabled"),
           ("problems", "Problems"), ("changed", "Unapplied changes"),
           ("workshop", "Workshop"), ("local", "Local folder")]
SORTS = [("name", "Name"), ("status", "Status"), ("order", "Load order"),
         ("updated", "Recently updated"), ("size", "Size")]

CSS = b"""
.status-dot { font-size: 15px; }
.st-error { color: #e5534b; }
.st-warn { color: #d4a72c; }
.st-stale { color: #9a8f6a; }
.st-ok { color: #3fb950; }
.st-untested, .st-checking { color: alpha(@theme_fg_color, 0.45); }
.mod-sub { font-size: smaller; color: alpha(@theme_fg_color, 0.65); }
.mod-off .mod-name { color: alpha(@theme_fg_color, 0.55); }
.badge { border-radius: 9px; padding: 0 7px; font-size: smaller;
         background-color: alpha(@theme_fg_color, 0.10); }
.badge-pending { background-color: alpha(#3584e4, 0.30); }
.badge-update { background-color: alpha(#3fb950, 0.25); }
.title-big { font-size: 17pt; font-weight: bold; }
.section { font-weight: bold; margin-top: 10px; }
.dim { color: alpha(@theme_fg_color, 0.65); }
.issue-card { border-radius: 6px; padding: 8px 10px;
              background-color: alpha(@theme_fg_color, 0.05); }
.issue-error { border-left: 4px solid #e5534b; }
.issue-warn { border-left: 4px solid #d4a72c; }
.issue-info { border-left: 4px solid alpha(@theme_fg_color, 0.3); }
.issue-stale { opacity: 0.75; }
.issue-detail { font-family: monospace; font-size: smaller; }
.verdict { font-size: 11pt; font-weight: bold; }
.empty-hint { font-size: 13pt; color: alpha(@theme_fg_color, 0.55); }
.badge-installed { background-color: alpha(#3fb950, 0.25); }
.thumb { background-color: alpha(@theme_fg_color, 0.06); border-radius: 4px; }
"""


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


def esc(text):
    return GLib.markup_escape_text(str(text))


def label(text="", markup=False, xalign=0.0, wrap=False, css=None, selectable=False, ellipsize=False):
    lb = Gtk.Label(xalign=xalign)
    if markup:
        lb.set_markup(text)
    else:
        lb.set_text(text)
    if wrap:
        lb.set_line_wrap(True)
        lb.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if ellipsize:
        lb.set_ellipsize(Pango.EllipsizeMode.END)
    if selectable:
        lb.set_selectable(True)
    for c in (css or "").split():
        lb.get_style_context().add_class(c)
    return lb


class Worker:
    """One background thread with a job queue. Results come back on the GTK main loop."""

    closing = False

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
                GLib.idle_add(self._deliver, callback, result)

    @staticmethod
    def _deliver(callback, result):
        if not Worker.closing:
            callback(result)
        return False


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

    # enable state ---------------------------------------------------------------------
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

    # health ---------------------------------------------------------------------------
    def status(self, mod_id):
        if not self.report:
            return S_CHECKING
        st = self.report.status.get(mod_id, S_UNTESTED)
        # Dependency problems depend on what's enabled right now, so compute them live.
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


# ------------------------------------------------------------------------------------------
# List row
# ------------------------------------------------------------------------------------------

class ModRow(Gtk.ListBoxRow):
    def __init__(self, win, mod_id):
        super().__init__()
        self.win, self.mod_id = win, mod_id
        box = Gtk.Box(spacing=8, margin_start=8, margin_end=10, margin_top=5, margin_bottom=5)
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.check.set_tooltip_text("Enable or disable this mod")
        self._handler = self.check.connect("toggled", self._on_toggled)
        self.dot = label("", css="status-dot")
        self.dot.set_valign(Gtk.Align.CENTER)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.name = label("", ellipsize=True, css="mod-name")
        self.sub = label("", ellipsize=True, css="mod-sub")
        texts.pack_start(self.name, False, False, 0)
        texts.pack_start(self.sub, False, False, 0)
        self.badges = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        for w in (self.check, self.dot):
            box.pack_start(w, False, False, 0)
        box.pack_start(texts, True, True, 0)
        box.pack_end(self.badges, False, False, 0)
        self.add(box)
        self.refresh()

    def _on_toggled(self, btn):
        self.win.toggle_mod(self.mod_id, btn.get_active())

    def refresh(self):
        st, mod = self.win.state, self.win.state.mods[self.mod_id]
        on = st.enabled(self.mod_id)
        self.check.handler_block(self._handler)
        self.check.set_active(on)
        self.check.handler_unblock(self._handler)
        ctx = self.get_style_context()
        (ctx.remove_class if on else ctx.add_class)("mod-off")

        status = st.status(self.mod_id)
        self.dot.set_text(STATUS_DOT[status])
        dctx = self.dot.get_style_context()
        for s in STATUS_DOT:
            dctx.remove_class("st-" + s)
        dctx.add_class("st-" + status)
        self.dot.set_tooltip_text(STATUS_TEXT[status])

        self.name.set_markup(f"<b>{esc(mod.name)}</b>")
        parts = [f"v{clean_version(mod.version)}", mod.source_label, human_size(mod.size)]
        if mod.author:
            parts.append(f"by {mod.author}")
        self.sub.set_text(" · ".join(parts))
        self.set_tooltip_text(f"{mod.name}\nID: {mod.id}")

        for child in self.badges.get_children():
            self.badges.remove(child)
        if self.mod_id in st.pending:
            self._badge("will enable" if st.pending[self.mod_id] else "will disable", "badge-pending")
        if mod.update_pending:
            self._badge("update queued", "badge-update")
        if mods_mod.is_library(st.mods, self.mod_id):
            self._badge("library", "")
        self.badges.show_all()

    def _badge(self, text, cls):
        b = label(text, css="badge " + cls)
        self.badges.pack_start(b, False, False, 0)


# ------------------------------------------------------------------------------------------
# Workshop browser
# ------------------------------------------------------------------------------------------

class ResultRow(Gtk.ListBoxRow):
    def __init__(self, page, wid):
        super().__init__()
        self.page, self.wid = page, wid
        box = Gtk.Box(spacing=10, margin_start=8, margin_end=10, margin_top=5, margin_bottom=5)
        self.thumb = Gtk.Image(valign=Gtk.Align.CENTER)
        self.thumb.set_size_request(48, 48)
        self.thumb.get_style_context().add_class("thumb")
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = label("", ellipsize=True, css="mod-name")
        self.sub = label("", ellipsize=True, css="mod-sub")
        texts.pack_start(self.name, False, False, 0)
        texts.pack_start(self.sub, False, False, 0)
        self.badges = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        box.pack_start(self.thumb, False, False, 0)
        box.pack_start(texts, True, True, 0)
        box.pack_end(self.badges, False, False, 0)
        self.add(box)
        self.refresh()

    def refresh(self):
        info = self.page.info.get(self.wid, {})
        self.name.set_markup(f"<b>{esc(info.get('title') or self.wid)}</b>")
        parts = []
        if info.get("subscriptions"):
            parts.append(f"{info['subscriptions']:,} subscribers")
        if info.get("time_updated"):
            parts.append("updated " + fmt_time(info["time_updated"]))
        if info.get("tags"):
            parts.append(", ".join(info["tags"][:3]))
        self.sub.set_text(" · ".join(parts))
        for child in self.badges.get_children():
            self.badges.remove(child)
        if self.page.is_installed(self.wid):
            b = label("installed", css="badge badge-installed")
            self.badges.pack_start(b, False, False, 0)
        self.badges.show_all()


class BrowsePage(Gtk.Box):
    """Search and browse the Workshop. Subscribing happens in Steam (it owns subscriptions and
    downloads); the window notices the download and rescans."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.info = {}            # wid -> workshop details
        self.rows = {}
        self.page_no = 0
        self.total_pages = None
        self.gen = 0              # bumps on every new search; stale results are dropped
        self.loading = False
        self.started = False
        self.selected = None
        self.thumbs = Worker("thumbnails")

        bar = Gtk.Box(spacing=8, margin_start=10, margin_end=10, margin_top=8, margin_bottom=8)
        self.search = Gtk.SearchEntry(placeholder_text="Search the Workshop, or paste an item link or ID")
        self.search.set_hexpand(True)
        self.search.connect("activate", lambda *_: self.start())
        self.search.connect("search-changed", self._on_search_changed)
        self.sort_combo = Gtk.ComboBoxText()
        for key, text in workshop.BROWSE_SORTS:
            self.sort_combo.append(key, text)
        self.sort_combo.set_active_id("trend")
        self.sort_combo.connect("changed", lambda *_: self.start())
        self.sort_combo.set_tooltip_text("Order of results when not searching (searches are ranked by relevance)")
        self.status = label("", css="dim")
        self.spinner = Gtk.Spinner()
        bar.pack_start(self.search, True, True, 0)
        bar.pack_start(self.sort_combo, False, False, 0)
        bar.pack_start(self.spinner, False, False, 0)
        bar.pack_start(self.status, False, False, 6)
        self.pack_start(bar, False, False, 0)
        self.pack_start(Gtk.Separator(), False, False, 0)

        paned = Gtk.Paned()
        self.pack_start(paned, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.connect("row-selected", self._on_row_selected)
        self.placeholder = label("", xalign=0.5, wrap=True, css="empty-hint")
        self.placeholder.set_margin_top(30)
        self.placeholder.show()
        self.listbox.set_placeholder(self.placeholder)
        self.more_btn = Gtk.Button(label="Load more", margin_top=6, margin_bottom=10, halign=Gtk.Align.CENTER)
        self.more_btn.set_no_show_all(True)
        self.more_btn.connect("clicked", lambda *_: self.load_page(self.page_no + 1))
        left.pack_start(self.listbox, False, False, 0)
        left.pack_start(self.more_btn, False, False, 0)
        self.lscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.lscroll.set_size_request(420, -1)
        self.lscroll.add(left)
        paned.pack1(self.lscroll, False, False)

        self.details_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                               margin_start=20, margin_end=20, margin_top=16, margin_bottom=20)
        self.details_scroll.add(self.details)
        paned.pack2(self.details_scroll, True, False)
        paned.set_position(470)
        self.preview = None
        self.show_intro()

    # ---- searching -----------------------------------------------------------------------

    def activate(self):
        if not self.started:
            self.start()
        self.search.grab_focus()

    def search_for(self, text):
        self.search.set_text(text)
        self.start()

    def _on_search_changed(self, entry):
        # Clearing the box goes back to browsing; typing waits for Enter (each search is a web request).
        if not entry.get_text().strip() and self.started:
            self.start()

    def start(self):
        self.started = True
        self.gen += 1
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.rows = {}
        self.selected = None
        self.total_pages = None
        self.more_btn.hide()
        self.show_intro()
        self.load_page(1)

    def load_page(self, page_no):
        gen = self.gen
        query = self.search.get_text().strip()
        sort = self.sort_combo.get_active_id()
        self.sort_combo.set_sensitive(not query)
        self.loading = True
        self.more_btn.set_sensitive(False)
        self.spinner.start()
        self.placeholder.set_text("Searching the Workshop…" if query else "Loading the Workshop…")
        self.status.set_text("")

        def work():
            res = workshop.browse(query, sort, page_no)
            if res is None:
                return None
            ids, total = res
            info = workshop.fetch_details(ids)
            # Drop anything that isn't a Slay the Spire 2 item (e.g. a pasted ID from another game).
            ids = [w for w in ids if info.get(w, {}).get("app_id") in (None, "", game_mod.APP_ID)]
            # Hide mostly non-English listings, unless the user pasted that exact item's ID or link.
            shown = ids if workshop.query_item_id(query) else \
                [w for w in ids if w not in info or workshop.english_ratio(info[w]) >= workshop.MIN_ENGLISH]
            return shown, bool(ids), total, info

        def done(result):
            if gen != self.gen:
                return
            self.loading = False
            self.spinner.stop()
            self.more_btn.set_sensitive(True)
            if result is None:
                self.placeholder.set_text("Couldn't reach the Steam Workshop. Check your connection, then "
                                          "press Enter in the search box to try again.")
                return
            ids, got_any, total, info = result
            self.info.update(info)
            self.page_no = page_no
            self.total_pages = total
            new = [w for w in ids if w not in self.rows]
            for wid in new:
                row = ResultRow(self, wid)
                self.rows[wid] = row
                self.listbox.add(row)
                self._load_thumb(row)
            self.listbox.show_all()
            if not self.rows:
                self.placeholder.set_text("Nothing found." if query else "The Workshop returned no items.")
            more = got_any and (total is None or page_no < total)
            self.more_btn.set_visible(more)
            shown = len(self.rows)
            self.status.set_text(f"{shown} shown" + (f" of ~{total * 30:,}" if total and more else ""))

        self.win.net.submit(work, done)

    def _load_thumb(self, row):
        url = self.info.get(row.wid, {}).get("preview_url")
        if not url:
            return

        def done(path):
            if not path or row.get_parent() is None:
                return
            try:
                row.thumb.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 48, 48, True))
            except GLib.Error:
                pass
        self.thumbs.submit(lambda: workshop.preview_path(row.wid, url, thumb=True), done)

    # ---- installed state -----------------------------------------------------------------

    def installed_mod(self, wid):
        for m in self.win.state.mods.values():
            if m.workshop_id == wid:
                return m
        return None

    def is_installed(self, wid):
        g = self.win.state.game
        return bool(g.workshop_dir) and os.path.isdir(os.path.join(g.workshop_dir, wid))

    def refresh_installed(self):
        for row in self.rows.values():
            row.refresh()
        if self.selected:
            self.show_item(self.selected)

    # ---- details -------------------------------------------------------------------------

    def _on_row_selected(self, _lb, row):
        if row is None:
            return
        self.selected = row.wid
        self.show_item(row.wid)

    def _clear(self):
        for child in self.details.get_children():
            self.details.remove(child)
        self.preview = None

    def show_intro(self):
        self._clear()
        d = self.details
        d.pack_start(label("Get mods from the Workshop", css="title-big"), False, False, 0)
        d.pack_start(label(
            "Browse or search Slay the Spire 2 mods here. Pick one to see its details.\n\n"
            "Subscribe opens the mod's page in Steam: click Subscribe there and Steam downloads it. "
            "This window notices when the download finishes and adds the mod to My mods, already checked "
            "against your game version.\n\n"
            "Steam handles subscriptions and downloads (that keeps updates and co-op working). "
            "Unsubscribing is done on the mod's Workshop page too.",
            wrap=True, css="dim"), False, False, 0)
        d.show_all()

    def show_item(self, wid):
        self._clear()
        info = self.info.get(wid, {})
        d = self.details
        head = Gtk.Box(spacing=16)
        self.preview = Gtk.Image()
        self.preview.set_no_show_all(True)
        head.pack_start(self.preview, False, False, 0)
        tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        tbox.pack_start(label(info.get("title") or wid, wrap=True, css="title-big", selectable=True),
                        False, False, 0)
        if info.get("tags"):
            tags = Gtk.Box(spacing=4)
            for t in info["tags"][:8]:
                tags.pack_start(label(t, css="badge"), False, False, 0)
            tbox.pack_start(tags, False, False, 0)

        btns = Gtk.Box(spacing=6, margin_top=4)
        mod = self.installed_mod(wid)
        if self.is_installed(wid):
            if mod:
                b = Gtk.Button(label="Show in My mods")
                b.get_style_context().add_class("suggested-action")
                b.connect("clicked", lambda *_: self.win.show_installed(mod.id))
                btns.pack_start(b, False, False, 0)
            b = Gtk.Button(label="Workshop page")
            b.set_tooltip_text("Opens in Steam, where you can unsubscribe")
        else:
            b = Gtk.Button(label="Subscribe in Steam…")
            b.get_style_context().add_class("suggested-action")
            b.set_tooltip_text("Opens the mod in Steam. Click Subscribe there; it shows up here once downloaded.")
        b.connect("clicked", lambda *_: self._open(wid))
        btns.pack_start(b, False, False, 0)
        tbox.pack_start(btns, False, False, 0)
        head.pack_start(tbox, True, True, 0)
        d.pack_start(head, False, False, 0)
        if info.get("preview_url"):
            self._load_preview(wid, info["preview_url"])

        if self.is_installed(wid):
            if mod:
                st = self.win.state.status(mod.id)
                d.pack_start(label(f"Installed: v{clean_version(mod.version)} · {STATUS_TEXT[st]}",
                                   css=f"verdict st-{st}"), False, False, 0)
            else:
                d.pack_start(label("Downloaded, but no mod manifest found yet. If Steam is still "
                                   "downloading, it will appear in My mods when done.", wrap=True, css="dim"),
                             False, False, 0)
        if info.get("banned"):
            d.pack_start(label("Steam has banned this item.", css="verdict st-error"), False, False, 0)

        d.pack_start(label("Details", css="section"), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=4)
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
        for r, (k, v) in enumerate(facts):
            grid.attach(label(k, css="dim"), 0, r, 1, 1)
            grid.attach(label(v, selectable=True, wrap=True), 1, r, 1, 1)
        d.pack_start(grid, False, False, 0)

        desc = workshop.strip_bbcode(info.get("description", ""))
        if desc:
            d.pack_start(label("Description", css="section"), False, False, 0)
            d.pack_start(label(desc[:4000], wrap=True, selectable=True), False, False, 0)
        d.pack_start(label("Requirements are listed on the Workshop page. Once installed, My mods shows "
                           "any that are missing, with a button to find them.", wrap=True, css="mod-sub"),
                     False, False, 6)
        d.show_all()
        self.details_scroll.get_vadjustment().set_value(0)

    def _open(self, wid):
        game_mod.open_workshop_page(wid)
        if not self.is_installed(wid):
            self.win.toast("Opened in Steam. After you click Subscribe, the mod appears here once "
                           "Steam has downloaded it.")

    def _load_preview(self, wid, url):
        target = self.preview

        def done(path):
            if target is not self.preview or not path:
                return
            try:
                target.set_from_pixbuf(GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 160, 160, True))
                target.show()
            except GLib.Error:
                target.hide()
        self.win.net.submit(lambda: workshop.preview_path(wid, url) if target is self.preview else None, done)


# ------------------------------------------------------------------------------------------
# Main window
# ------------------------------------------------------------------------------------------

class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.set_default_size(1240, 800)
        self.set_icon_name("applications-games")
        self.state = State()
        self.rows = {}
        self.selected = None
        self.health_gen = 0
        self.running = game_mod.is_running()
        self._toast_timer = None
        self.cpu = Worker("health-check")
        self.net = Worker("workshop")
        self.workshop_sig = self._workshop_signature()

        self._build_header()
        self._build_body()
        self._build_actions(app)
        self.show_all()
        self.populate()
        self.start_health_check()
        self.start_workshop_fetch()
        GLib.timeout_add_seconds(2, self._poll)
        self.connect("delete-event", self._on_close)

    # ---- construction --------------------------------------------------------------------

    def _build_header(self):
        hb = Gtk.HeaderBar(show_close_button=True)
        self.set_titlebar(hb)
        self.header = hb

        self.profile_combo = Gtk.ComboBoxText()
        self.profile_combo.set_tooltip_text("Load a saved setup")
        self._profile_handler = self.profile_combo.connect("changed", self._on_profile_chosen)
        hb.pack_start(self.profile_combo)
        save_btn = Gtk.Button.new_from_icon_name("document-save-as-symbolic", Gtk.IconSize.BUTTON)
        save_btn.set_tooltip_text("Save the current setup as a profile")
        save_btn.connect("clicked", lambda *_: self.save_profile_dialog())
        hb.pack_start(save_btn)

        menu = Gio.Menu()
        sec = Gio.Menu()
        sec.append("Compare with a friend's mod list…", "win.compare")
        sec.append("Export my mod list…", "win.export")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Disable all broken mods", "win.disable-broken")
        sec.append("Apply automatic fixes", "win.fix-all")
        sec.append("Delete a profile…", "win.delete-profile")
        menu.append_section(None, sec)
        sec = Gio.Menu()
        sec.append("Restore settings backup…", "win.restore")
        sec.append("Open game logs folder", "win.open-logs")
        sec.append("Open local mods folder", "win.open-mods")
        sec.append("Refresh", "win.refresh")
        sec.append("About", "win.about")
        menu.append_section(None, sec)
        menu_btn = Gtk.MenuButton(menu_model=menu)
        menu_btn.add(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        hb.pack_end(menu_btn)

        self.play_btn = Gtk.Button(label="Play")
        self.play_btn.get_style_context().add_class("suggested-action")
        self.play_btn.connect("clicked", lambda *_: self.play())
        hb.pack_end(self.play_btn)

        self.spinner = Gtk.Spinner()
        self.spinner.set_tooltip_text("Checking mods…")
        hb.pack_end(self.spinner)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        hb.pack_start(Gtk.StackSwitcher(stack=self.stack))
        self.refresh_profiles()

    def _build_body(self):
        overlay = Gtk.Overlay()
        self.add(overlay)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.add(outer)

        # Toast
        self.toast_rev = Gtk.Revealer(halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        toast_box = Gtk.Box(spacing=12)
        toast_box.get_style_context().add_class("app-notification")
        self.toast_label = label("", wrap=True)
        self.toast_label.set_max_width_chars(80)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.connect("clicked", lambda *_: self.toast_rev.set_reveal_child(False))
        toast_box.pack_start(self.toast_label, True, True, 0)
        toast_box.pack_start(close, False, False, 0)
        self.toast_rev.add(toast_box)
        overlay.add_overlay(self.toast_rev)

        # Info bars
        self.bar_running = self._infobar(Gtk.MessageType.WARNING,
                                         "Slay the Spire 2 is running. Close the game to apply changes: "
                                         "it overwrites its settings when it exits.")
        self.bar_global = self._infobar(Gtk.MessageType.INFO, "")
        self.bar_global_btn = None
        self.bar_pending = self._infobar(Gtk.MessageType.QUESTION, "")
        self.bar_pending.add_button("Revert", 1)
        self.apply_btn = self.bar_pending.add_button("Apply changes", 2)
        self.apply_btn.get_style_context().add_class("suggested-action")
        self.bar_pending.connect("response", self._on_pending_response)
        self.bar_error = self._infobar(Gtk.MessageType.ERROR, "")
        for b in (self.bar_error, self.bar_running, self.bar_global, self.bar_pending):
            outer.pack_start(b, False, False, 0)

        outer.pack_start(self.stack, True, True, 0)
        installed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack.add_titled(installed, "installed", "My mods")
        self.browse = BrowsePage(self)
        self.stack.add_titled(self.browse, "browse", "Get mods")
        self.stack.connect("notify::visible-child-name", lambda *_: self._on_page_changed())

        # Filter bar
        fbar = Gtk.Box(spacing=8, margin_start=10, margin_end=10, margin_top=8, margin_bottom=8)
        self.search = Gtk.SearchEntry(placeholder_text="Search mods by name, ID, author or tag")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self._refilter())
        self.filter_combo = Gtk.ComboBoxText()
        for key, text in FILTERS:
            self.filter_combo.append(key, text)
        self.filter_combo.set_active_id("all")
        self.filter_combo.connect("changed", lambda *_: self._refilter())
        self.sort_combo = Gtk.ComboBoxText()
        for key, text in SORTS:
            self.sort_combo.append(key, "Sort: " + text)
        self.sort_combo.set_active_id("name")
        self.sort_combo.connect("changed", lambda *_: self.listbox.invalidate_sort())
        self.summary = label("", css="dim")
        fbar.pack_start(self.search, True, True, 0)
        fbar.pack_start(self.filter_combo, False, False, 0)
        fbar.pack_start(self.sort_combo, False, False, 0)
        fbar.pack_start(self.summary, False, False, 6)
        installed.pack_start(fbar, False, False, 0)
        installed.pack_start(Gtk.Separator(), False, False, 0)

        # List + details
        paned = Gtk.Paned()
        installed.pack_start(paned, True, True, 0)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.listbox.set_filter_func(self._filter_row)
        self.listbox.set_sort_func(self._sort_rows)
        self.listbox.connect("row-selected", self._on_row_selected)
        placeholder = label("No mods match.", xalign=0.5, css="empty-hint")
        placeholder.show()
        self.listbox.set_placeholder(placeholder)
        lscroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        lscroll.set_size_request(420, -1)
        lscroll.add(self.listbox)
        paned.pack1(lscroll, False, False)

        self.details_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                               margin_start=20, margin_end=20, margin_top=16, margin_bottom=20)
        self.details_scroll.add(self.details)
        paned.pack2(self.details_scroll, True, False)
        paned.set_position(470)

    def _infobar(self, kind, text):
        bar = Gtk.InfoBar(message_type=kind)
        bar.set_no_show_all(True)
        lb = label(text, wrap=True)
        lb.show()
        bar.get_content_area().add(lb)
        bar.text_label = lb
        return bar

    def _build_actions(self, app):
        def add(name, cb, accels=None):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_: cb())
            self.add_action(act)
            if accels:
                app.set_accels_for_action(f"win.{name}", accels)
        add("refresh", self.reload, ["F5", "<Control>r"])
        add("compare", self.compare_dialog)
        add("export", self.export_dialog)
        add("disable-broken", self.disable_broken)
        add("fix-all", self.fix_all)
        add("delete-profile", self.delete_profile_dialog)
        add("restore", self.restore_dialog)
        add("open-logs", lambda: self.state.game.logs_dir and game_mod.open_path(self.state.game.logs_dir))
        add("open-mods", self.open_mods_folder)
        add("about", self.about)
        add("search", lambda: (self.browse.search if self.stack.get_visible_child_name() == "browse"
                               else self.search).grab_focus(), ["<Control>f"])
        add("apply", self.apply, ["<Control>s"])

    # ---- population / refresh ------------------------------------------------------------

    def populate(self):
        for child in self.listbox.get_children():
            self.listbox.remove(child)
        self.rows = {}
        for mod_id in self.state.mods:
            row = ModRow(self, mod_id)
            self.rows[mod_id] = row
            self.listbox.add(row)
        self.listbox.show_all()
        self.refresh_chrome()
        if self.selected in self.rows:
            self.listbox.select_row(self.rows[self.selected])
        else:
            self.selected = None
            self.show_overview()

    def refresh_rows(self, ids=None):
        for mid in (ids or self.rows):
            if mid in self.rows:
                self.rows[mid].refresh()
        self.listbox.invalidate_filter()
        self.listbox.invalidate_sort()
        self.refresh_chrome()
        if self.selected:
            self.show_details(self.selected)

    def refresh_chrome(self):
        st, g = self.state, self.state.game
        enabled = sum(1 for m in st.mods if st.enabled(m))
        self.header.set_title("Slay the Spire 2 Mods")
        self.header.set_subtitle(f"Game {g.version_text} · {g.branch} branch · {enabled} of {len(st.mods)} enabled")
        counts = {}
        for mid in st.mods:
            s = st.status(mid)
            if st.enabled(mid):
                counts[s] = counts.get(s, 0) + 1
        bits = []
        if counts.get(S_ERROR):
            bits.append(f"{counts[S_ERROR]} broken")
        if counts.get(S_WARN):
            bits.append(f"{counts[S_WARN]} with warnings")
        self.summary.set_text(("Enabled: " + ", ".join(bits)) if bits else "")

        # Error bar
        problems = list(g.problems) + ([st.settings_error] if st.settings_error else [])
        self._set_bar(self.bar_error, "\n".join(problems))
        # Running bar
        self._set_bar(self.bar_running, self.bar_running.text_label.get_text() if self.running else "")
        # Global bar
        gtexts = []
        if st.settings and not st.settings.mods_enabled:
            gtexts.append("Mods are switched off globally in the game's settings.")
        if st.report:
            gtexts += [f"{i.title}. {i.detail}" for i in st.report.global_issues]
        self._set_bar(self.bar_global, "\n".join(gtexts))
        # Pending bar
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
            self.bar_pending.text_label.set_markup(
                f"<b>Unapplied changes:</b> {', '.join(parts)}. Changes take effect next time the game starts.")
        self._set_bar(self.bar_pending, "x" if n else "")
        self.apply_btn.set_sensitive(not self.running and st.settings is not None)
        self.apply_btn.set_tooltip_text("Close the game first" if self.running else "Write changes to the game's settings")

    def _set_bar(self, bar, text):
        if text:
            if bar is not self.bar_pending:
                bar.text_label.set_text(text)
            bar.show()
        else:
            bar.hide()

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
                                     game_version_text=game.version_text)
            return api, report

        def done(result):
            if gen != self.health_gen:
                return
            st.api, st.report = result
            self.spinner.stop()
            self.refresh_rows()
            if self.selected is None:
                self.show_overview()

        self.cpu.submit(work, done)

    def start_workshop_fetch(self, force=False):
        ids = [m.workshop_id for m in self.state.mods.values() if m.workshop_id]

        def done(info):
            self.state.workshop.update(info)
            if self.selected:
                self.show_details(self.selected)

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
        return True

    def _workshop_signature(self):
        """(item folders, Steam's workshop manifest mtime). Steam rewrites the manifest when a
        download finishes, so a change means something was installed, updated or removed."""
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
        gone = before - after
        if new:
            self.toast("Installed from the Workshop: " + ", ".join(m.name for m in new) +
                       ". New mods are enabled; press Play or check them under My mods.")
        elif gone:
            self.toast(f"{len(gone)} Workshop item{'s' if len(gone) != 1 else ''} removed by Steam. Rescanned.")

    def _on_page_changed(self):
        if self.stack.get_visible_child_name() == "browse":
            self.browse.activate()

    def show_installed(self, mod_id):
        self.stack.set_visible_child_name("installed")
        if mod_id in self.rows:
            self.search.set_text("")
            self.filter_combo.set_active_id("all")
            self.listbox.select_row(self.rows[mod_id])

    def find_on_workshop(self, text):
        self.stack.set_visible_child_name("browse")
        self.browse.search_for(text)

    # ---- filtering / sorting -------------------------------------------------------------

    def _refilter(self):
        self.listbox.invalidate_filter()

    def _filter_row(self, row):
        st, mod = self.state, self.state.mods.get(row.mod_id)
        if mod is None:
            return False
        f = self.filter_combo.get_active_id()
        on = st.enabled(row.mod_id)
        if f == "enabled" and not on or f == "disabled" and on:
            return False
        if f == "problems" and st.status(row.mod_id) not in (S_ERROR, S_WARN):
            return False
        if f == "changed" and row.mod_id not in st.pending:
            return False
        if f == "workshop" and mod.source != mods_mod.WORKSHOP or f == "local" and mod.source != mods_mod.LOCAL:
            return False
        q = self.search.get_text().strip().lower()
        if q:
            info = st.workshop_info(mod) or {}
            hay = " ".join([mod.name, mod.id, mod.author, info.get("title", ""), " ".join(info.get("tags", []))]).lower()
            return all(word in hay for word in q.split())
        return True

    def _sort_rows(self, a, b):
        st = self.state
        key = self.sort_combo.get_active_id()
        ma, mb = st.mods[a.mod_id], st.mods[b.mod_id]

        def k(m):
            name = m.name.lower()
            if key == "status":
                return (STATUS_RANK.get(st.status(m.id), 9), not st.enabled(m.id), name)
            if key == "order":
                return (st.order.index(m.id) if m.id in st.order else 1e9, name)
            if key == "updated":
                return (-(m.installed_time or 0), name)
            if key == "size":
                return (-m.size, name)
            return (name,)
        ka, kb = k(ma), k(mb)
        return (ka > kb) - (ka < kb)

    # ---- details pane --------------------------------------------------------------------

    def _on_row_selected(self, _lb, row):
        if row is None:
            self.selected = None
            self.show_overview()
            return
        self.selected = row.mod_id
        self.show_details(row.mod_id)

    def _clear_details(self):
        for child in self.details.get_children():
            self.details.remove(child)

    def show_overview(self):
        self._clear_details()
        st = self.state
        d = self.details
        d.pack_start(label("Overview", css="title-big"), False, False, 0)
        if not st.report:
            d.pack_start(label("Checking your mods: reading game logs and each mod's code…", css="dim"), False, False, 0)
            d.show_all()
            return
        broken = [m for m in st.mods if st.enabled(m) and st.status(m) == S_ERROR]
        warns = [m for m in st.mods if st.enabled(m) and st.status(m) == S_WARN]
        if not broken and not warns:
            d.pack_start(label("All enabled mods look healthy.", css="verdict st-ok"), False, False, 0)
        if broken:
            d.pack_start(label(f"{len(broken)} enabled mod{'s' if len(broken) != 1 else ''} "
                               "look broken on your game version:", css="verdict st-error"), False, False, 0)
            for mid in sorted(broken, key=lambda i: st.mods[i].name.lower()):
                d.pack_start(self._link_row(mid), False, False, 0)
            btn = Gtk.Button(label=f"Disable these {len(broken)}", halign=Gtk.Align.START)
            btn.connect("clicked", lambda *_: self.disable_broken())
            d.pack_start(btn, False, False, 4)
        if warns:
            d.pack_start(label("With warnings:", css="section"), False, False, 0)
            for mid in sorted(warns, key=lambda i: st.mods[i].name.lower()):
                d.pack_start(self._link_row(mid), False, False, 0)
        d.pack_start(label("How health is checked", css="section"), False, False, 0)
        d.pack_start(label(
            "• Code check: each mod's compiled code is compared against your installed game, so mods that "
            "call game functions that changed are caught before you play.\n"
            "• Game logs: startup failures, crashes and in-game errors from your recent sessions are "
            "matched to the mod that caused them. Results are remembered after the game deletes old logs.\n"
            "• Files: missing or misnamed files and missing dependencies.\n\n"
            "Select a mod on the left for details. Changes are only written when you press Apply.",
            wrap=True, css="dim"), False, False, 0)
        d.show_all()

    def _link_row(self, mod_id):
        mod = self.state.mods[mod_id]
        btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE, halign=Gtk.Align.START)
        status = self.state.status(mod_id)
        lb = label(f"<span foreground='{STATUS_COLOR.get(status, '#888888')}'>{STATUS_DOT[status]}</span>"
                   f"  {esc(mod.name)}", markup=True)
        btn.add(lb)
        btn.connect("clicked", lambda *_: self.listbox.select_row(self.rows[mod_id]))
        return btn

    def show_details(self, mod_id):
        st = self.state
        mod = st.mods.get(mod_id)
        self._clear_details()
        if mod is None:
            return
        d = self.details
        info = st.workshop_info(mod) or {}

        # Header: preview + title + switch
        head = Gtk.Box(spacing=16)
        self.preview = Gtk.Image()
        self.preview.set_no_show_all(True)   # shown once the image has loaded
        head.pack_start(self.preview, False, False, 0)
        tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        tbox.pack_start(label(mod.name, wrap=True, css="title-big", selectable=True), False, False, 0)
        sub = [f"v{clean_version(mod.version)}"]
        if mod.author:
            sub.append(f"by {mod.author}")
        sub.append(mod.id)
        tbox.pack_start(label(" · ".join(sub), css="dim", selectable=True, wrap=True), False, False, 0)
        if info.get("tags"):
            tags = Gtk.Box(spacing=4)
            for t in info["tags"][:8]:
                tags.pack_start(label(t, css="badge"), False, False, 0)
            tbox.pack_start(tags, False, False, 0)
        sw_box = Gtk.Box(spacing=8)
        switch = Gtk.Switch(active=st.enabled(mod_id), valign=Gtk.Align.CENTER)
        switch.connect("notify::active", lambda s, _p: self.toggle_mod(mod_id, s.get_active()))
        sw_box.pack_start(switch, False, False, 0)
        pend = ""
        if mod_id in st.pending:
            pend = "  (unapplied)"
        sw_box.pack_start(label(("Enabled" if st.enabled(mod_id) else "Disabled") + pend), False, False, 0)
        tbox.pack_start(sw_box, False, False, 4)
        head.pack_start(tbox, True, True, 0)
        d.pack_start(head, False, False, 0)
        if mod.workshop_id and info.get("preview_url"):
            self._load_preview(mod.workshop_id, info["preview_url"])

        # Health
        status = st.status(mod_id)
        tested = st.report.tested.get(mod_id) if st.report else None
        verdict = {
            S_ERROR: "Problems found: this mod is broken or likely broken on your setup.",
            S_WARN: "Works, but with warnings.",
            S_STALE: "Had problems before that look fixed now. Try it again.",
            S_OK: f"No problems found (last loaded {tested[0]})." if tested else "No problems found.",
            S_UNTESTED: "No problems found in its files, but the game hasn't loaded it in any session I can see.",
            S_CHECKING: "Checking…",
        }[status]
        d.pack_start(label("Health", css="section"), False, False, 0)
        d.pack_start(label(verdict, wrap=True, css=f"verdict st-{status}"), False, False, 0)
        for iss in st.issues(mod_id):
            d.pack_start(self._issue_card(mod, iss), False, False, 0)

        # Facts
        d.pack_start(label("Details", css="section"), False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=4)
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
        for r, (k, v) in enumerate(facts):
            grid.attach(label(k, css="dim"), 0, r, 1, 1)
            grid.attach(label(v, selectable=True, wrap=True), 1, r, 1, 1)
        d.pack_start(grid, False, False, 0)

        btns = Gtk.Box(spacing=6, margin_top=6)
        b = Gtk.Button(label="Open folder")
        b.connect("clicked", lambda *_: game_mod.open_path(mod.dir))
        btns.pack_start(b, False, False, 0)
        if mod.workshop_id:
            b = Gtk.Button(label="Workshop page")
            b.connect("clicked", lambda *_: game_mod.open_workshop_page(mod.workshop_id))
            btns.pack_start(b, False, False, 0)
        up = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
        up.set_tooltip_text("Load earlier")
        up.connect("clicked", lambda *_: self.move(mod_id, -1))
        down = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
        down.set_tooltip_text("Load later")
        down.connect("clicked", lambda *_: self.move(mod_id, 1))
        btns.pack_end(down, False, False, 0)
        btns.pack_end(up, False, False, 0)
        btns.pack_end(label("Load order", css="dim"), False, False, 4)
        d.pack_start(btns, False, False, 0)

        # Dependencies
        if mod.dependencies:
            d.pack_start(label("Requires", css="section"), False, False, 0)
            for dep_id, min_v in mod.dependencies:
                d.pack_start(self._dep_row(dep_id, min_v), False, False, 0)
        users = [m for m in mods_mod.dependents(st.mods, mod_id)]
        if users:
            d.pack_start(label(f"Required by ({len(users)})", css="section"), False, False, 0)
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                               column_spacing=4, row_spacing=2)
            for u in sorted(users, key=lambda m: m.name.lower()):
                flow.add(self._link_row(u.id))
            d.pack_start(flow, False, False, 0)

        # Description
        desc = workshop.strip_bbcode(info.get("description", "")) or mod.description
        if desc:
            d.pack_start(label("Description", css="section"), False, False, 0)
            d.pack_start(label(desc[:4000], wrap=True, selectable=True), False, False, 0)

        d.show_all()
        self.details_scroll.get_vadjustment().set_value(0)

    def _issue_card(self, mod, iss):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        ctx = card.get_style_context()
        ctx.add_class("issue-card")
        ctx.add_class("issue-" + iss.severity)
        if iss.stale:
            ctx.add_class("issue-stale")
        title = esc(iss.title)
        if iss.count > 1:
            title += f"  <span alpha='60%'>×{iss.count}</span>"
        card.pack_start(label(f"<b>{title}</b>", markup=True, wrap=True), False, False, 0)
        if iss.detail:
            detail = iss.detail.replace(logscan.SAMPLE_SEP, "\n\n")
            prose, _, listing = detail.partition("\n• ")
            looks_raw = any(tok in prose for tok in ("Exception", "res://", "/home/", "  at ", "ERROR"))
            if looks_raw:
                prose, listing = "", detail
            elif listing:
                listing = "• " + listing
            if prose:
                card.pack_start(label(prose, wrap=True, selectable=True, css="dim"), False, False, 0)
            if listing:
                code = label(listing, wrap=True, selectable=True, css="issue-detail")
                if len(listing) > 700:
                    exp = Gtk.Expander(label="Show details")
                    exp.add(code)
                    card.pack_start(exp, False, False, 0)
                else:
                    card.pack_start(code, False, False, 0)
        meta = []
        if iss.source == "code check":
            meta.append("found by checking the mod's code against your game")
        elif iss.source not in ("static", "", None):
            meta.append(f"from the game log of {time.strftime('%b %d %H:%M', time.localtime(iss.when))}"
                        if iss.when else "from the game log")
        if iss.stale:
            meta.append(iss.stale_note or "this may be fixed")
        if meta:
            card.pack_start(label(" · ".join(meta), wrap=True, css="mod-sub"), False, False, 0)
        if iss.fix:
            btn = Gtk.Button(label="Fix it", halign=Gtk.Align.START)
            btn.connect("clicked", lambda *_: self.apply_fix(mod, iss.fix))
            card.pack_start(btn, False, False, 2)
        return card

    def _dep_row(self, dep_id, min_v):
        st = self.state
        box = Gtk.Box(spacing=8)
        dep = st.mods.get(dep_id)
        if dep is None:
            box.pack_start(label(f"<span foreground='#e5534b'>●</span>  {esc(dep_id)}: not installed",
                                 markup=True), False, False, 0)
            find = Gtk.Button(label="Find on Workshop")
            find.connect("clicked", lambda *_: self.find_on_workshop(dep_id))
            box.pack_start(find, False, False, 0)
            return box
        on = st.enabled(dep_id)
        state_txt = "enabled" if on else "disabled"
        color = "#3fb950" if on else "#d4a72c"
        btn = Gtk.Button(relief=Gtk.ReliefStyle.NONE)
        btn.add(label(f"<span foreground='{color}'>●</span>  {esc(dep.name)}"
                      f"  <span alpha='60%'>v{esc(clean_version(dep.version))}"
                      f"{' (needs ' + esc(min_v) + '+)' if min_v else ''} · {state_txt}</span>", markup=True))
        btn.connect("clicked", lambda *_: self.listbox.select_row(self.rows[dep_id]))
        box.pack_start(btn, False, False, 0)
        if not on:
            en = Gtk.Button(label="Enable")
            en.connect("clicked", lambda *_: self.toggle_mod(dep_id, True))
            box.pack_start(en, False, False, 0)
        return box

    def _load_preview(self, wid, url):
        target = self.preview

        def done(path):
            if target is not self.preview or not path:
                return
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, 160, 160, True)
                target.set_from_pixbuf(pb)
                target.show()
            except GLib.Error:
                target.hide()

        def work():
            # Skip downloads for mods the user already clicked past.
            return workshop.preview_path(wid, url) if target is self.preview else None
        self.net.submit(work, done)

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
                self.toast(f"Also enabled what {mod.name} needs: " +
                           ", ".join(st.mods[d].name for d in needed))
            missing = [d for d, _ in mod.dependencies if d not in st.mods]
            if missing:
                self.toast(f"{mod.name} needs mods you don't have: {', '.join(missing)}")
        else:
            users = [d for d in mods_mod.dependent_closure(st.mods, mod_id) if st.enabled(d)]
            if users:
                names = "\n".join("• " + st.mods[u].name for u in sorted(users, key=lambda u: st.mods[u].name.lower()))
                resp = self.ask(f"Other mods need {mod.name}",
                                f"These enabled mods won't load without it:\n{names}",
                                [("Cancel", 0), (f"Disable only {mod.name}", 1),
                                 (f"Disable all {len(users) + 1}", 2)], default=2)
                if resp == 0 or resp < 0:
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
            if self.sort_combo.get_active_id() != "order":
                self.sort_combo.set_active_id("order")
                self.toast("Sorted by load order. Note: the game always loads a mod's requirements before it.")
            self.refresh_rows([mod_id, order[i]])

    def _on_pending_response(self, _bar, resp):
        if resp == 1:
            self.state.pending.clear()
            self.state.order = list(self.state.saved_order)
            self.refresh_rows()
        elif resp == 2:
            self.apply()

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
        self.toast(f"Applied {n} change{'s' if n != 1 else ''}. Backup saved: {os.path.basename(backup)}")

    def play(self):
        if self.state.change_count():
            resp = self.ask("Apply your changes first?",
                            "You have unapplied changes. The game only reads mod settings when it starts.",
                            [("Cancel", 0), ("Play without them", 1), ("Apply and play", 2)], default=2)
            if resp <= 0:
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
        self.toast(f"Marked {len(broken)} broken mod{'s' if len(broken) != 1 else ''} to disable. "
                   "Review, then Apply.")

    def apply_fix(self, mod, fix):
        if game_mod.is_running():
            self.error("Close the game first", "The game may be using these files right now.")
            return
        try:
            mods_mod.apply_fix(fix)
        except OSError as e:
            self.error("Fix failed", str(e))
            return
        if fix[0] == "symlink":
            self.toast(f"Fixed {mod.name}: linked {os.path.basename(fix[2])} → {fix[1]}. "
                       "If a mod update undoes this, the Fix button will come back.")
        else:
            self.toast(f"Fixed {mod.name}: its cache now lives in {fix[2]}.")
        self.reload(quiet=True)

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
        self.toast(f"Applied {fixed} automatic fix{'es' if fixed != 1 else ''}." if fixed
                   else "Nothing to fix automatically.")

    def open_mods_folder(self):
        path = self.state.game.mods_dir
        if not path:
            return
        os.makedirs(path, exist_ok=True)
        game_mod.open_path(path)

    # ---- profiles ------------------------------------------------------------------------

    def refresh_profiles(self):
        self.profile_combo.handler_block(self._profile_handler)
        self.profile_combo.remove_all()
        self.profile_combo.append("", "Profiles…")
        for name in profiles.list_profiles():
            self.profile_combo.append(name, name)
        self.profile_combo.set_active_id("")
        self.profile_combo.handler_unblock(self._profile_handler)

    def _on_profile_chosen(self, combo):
        name = combo.get_active_id()
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
        GLib.idle_add(self.refresh_profiles)

    def save_profile_dialog(self):
        dlg = Gtk.Dialog(title="Save profile", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        box.add(label("Save which mods are enabled (and the load order) under a name, e.g. "
                      "“Solo” or “Co-op with Sam”. Unapplied changes are included.", wrap=True))
        entry = Gtk.Entry(activates_default=True, placeholder_text="Profile name")
        box.add(entry)
        dlg.show_all()
        resp = dlg.run()
        name = entry.get_text().strip()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not name:
            return
        st = self.state
        profiles.save_profile(name, st.states(), st.order, st.mods)
        self.refresh_profiles()
        self.toast(f"Saved profile “{name}”.")

    def delete_profile_dialog(self):
        names = profiles.list_profiles()
        if not names:
            self.toast("No saved profiles.")
            return
        dlg = Gtk.Dialog(title="Delete profile", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Delete", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText()
        for n in names:
            combo.append(n, n)
        combo.set_active(0)
        area = dlg.get_content_area()
        area.set_border_width(12)
        area.add(combo)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            profiles.delete_profile(combo.get_active_id())
            self.refresh_profiles()
        dlg.destroy()

    # ---- co-op ---------------------------------------------------------------------------

    def export_dialog(self):
        dlg = Gtk.FileChooserNative(title="Export mod list", transient_for=self,
                                    action=Gtk.FileChooserAction.SAVE, accept_label="Export")
        dlg.set_current_name(f"sts2-mods-{time.strftime('%Y-%m-%d')}.json")
        dlg.set_do_overwrite_confirmation(True)
        if dlg.run() == Gtk.ResponseType.ACCEPT:
            path = dlg.get_filename()
            try:
                profiles.export_modlist(path, self.state.states(), self.state.mods, self.state.game)
                self.toast(f"Exported to {path}. Send it to your friend; they can open it with "
                           "Compare with a friend's mod list.")
            except OSError as e:
                self.error("Export failed", str(e))
        dlg.destroy()

    def compare_dialog(self):
        chooser = Gtk.FileChooserNative(title="Open your friend's mod list", transient_for=self,
                                        action=Gtk.FileChooserAction.OPEN, accept_label="Compare")
        flt = Gtk.FileFilter()
        flt.set_name("Mod lists (*.json)")
        flt.add_pattern("*.json")
        chooser.add_filter(flt)
        ok = chooser.run() == Gtk.ResponseType.ACCEPT
        path = chooser.get_filename()
        chooser.destroy()
        if not ok:
            return
        try:
            theirs = profiles.import_modlist(path)
        except (OSError, ValueError) as e:
            self.error("Couldn't read that file", str(e))
            return
        st = self.state
        diff = profiles.compare(theirs, st.states(), st.mods, st.game)

        dlg = Gtk.Dialog(title="Compare mod lists", transient_for=self, modal=True)
        dlg.set_default_size(620, 560)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        match_btn = dlg.add_button("Match their setup", Gtk.ResponseType.APPLY)
        match_btn.get_style_context().add_class("suggested-action")
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=14, margin_end=14,
                       margin_top=10, margin_bottom=10)
        scroll.add(body)
        dlg.get_content_area().pack_start(scroll, True, True, 0)

        if profiles.diff_is_clean(diff):
            body.pack_start(label("Your gameplay mods match. You should be able to play together.",
                                  wrap=True, css="verdict st-ok"), False, False, 0)
        else:
            body.pack_start(label("Differences that will block co-op:", css="verdict st-error"), False, False, 0)
        if diff["branch"]:
            body.pack_start(label(f"Different game branch: you're on {diff['branch'][0]}, they're on "
                                  f"{diff['branch'][1]}. Switch in Steam: game Properties → Betas.",
                                  wrap=True), False, False, 0)
        elif diff["game_version"]:
            body.pack_start(label(f"Different game version: {diff['game_version'][0]} vs "
                                  f"{diff['game_version'][1]}. One of you needs to update.", wrap=True),
                            False, False, 0)

        def section(title, items, make):
            if not items:
                return
            body.pack_start(label(title, css="section"), False, False, 0)
            for it in items:
                body.pack_start(make(it), False, False, 0)

        def missing_row(t):
            box = Gtk.Box(spacing=8)
            box.pack_start(label(f"{t.get('name', t['id'])}  (v{clean_version(t.get('version'))})",
                                 ellipsize=True), True, True, 0)
            if t.get("workshop_id"):
                b = Gtk.Button(label="Subscribe…")
                b.connect("clicked", lambda *_: game_mod.open_workshop_page(t["workshop_id"]))
                box.pack_start(b, False, False, 0)
            return box

        section("They have, you don't have installed", diff["missing"], missing_row)
        section("They have it enabled, you have it disabled", diff["disabled"],
                lambda t: label(t.get("name", t["id"]), xalign=0))
        section("You have enabled, they don't", diff["extra"], lambda m: label(m.name, xalign=0))
        section("Different versions (one of you needs the Workshop update)", diff["version"],
                lambda pair: label(f"{pair[0].name}: you v{clean_version(pair[0].version)}, they "
                                   f"v{clean_version(pair[1].get('version'))}", xalign=0, wrap=True))
        if diff["optional_only"]:
            body.pack_start(label(f"{len(diff['optional_only'])} client-side mods differ too, which "
                                  "doesn't matter for co-op.", wrap=True, css="dim"), False, False, 8)
        match_btn.set_sensitive(bool(diff["disabled"] or diff["extra"]))
        match_btn.set_tooltip_text("Enable the mods they use that you have, and disable gameplay mods "
                                   "they don't have. Nothing is written until you Apply.")
        dlg.show_all()
        resp = dlg.run()
        dlg.destroy()
        if resp == Gtk.ResponseType.APPLY:
            for t in diff["disabled"]:
                st.set_pending(t["id"], True)
            for m in diff["extra"]:
                st.set_pending(m.id, False)
            self.refresh_rows()
            extra = f" You still need to subscribe to {len(diff['missing'])}." if diff["missing"] else ""
            self.toast("Matched their setup. Review, then Apply." + extra)

    # ---- backups -------------------------------------------------------------------------

    def restore_dialog(self):
        backups = settings_mod.list_backups()
        if not backups:
            self.toast("No backups yet. One is made every time you apply changes.")
            return
        dlg = Gtk.Dialog(title="Restore settings backup", transient_for=self, modal=True)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Restore", Gtk.ResponseType.OK)
        area = dlg.get_content_area()
        area.set_border_width(12)
        area.set_spacing(8)
        area.add(label("Restores the game's settings.save (including which mods are enabled) to how it "
                       "was before an earlier Apply. Your current file is backed up first.", wrap=True))
        combo = Gtk.ComboBoxText()
        for b in backups:
            combo.append(b, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(b))))
        combo.set_active(0)
        area.add(combo)
        dlg.show_all()
        resp = dlg.run()
        choice = combo.get_active_id()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK:
            return
        try:
            settings_mod.restore_backup(choice, self.state.game.settings_path)
        except (settings_mod.GameRunningError, OSError) as e:
            self.error("Restore failed", str(e))
            return
        self.state.pending.clear()
        self.reload(quiet=True)
        self.toast("Settings restored.")

    # ---- small dialogs -------------------------------------------------------------------

    def toast(self, text):
        self.toast_label.set_text(text)
        self.toast_rev.set_reveal_child(True)
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)

        def hide():
            self.toast_rev.set_reveal_child(False)
            self._toast_timer = None
            return False
        self._toast_timer = GLib.timeout_add_seconds(6, hide)

    def ask(self, title, text, buttons, default=None):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.QUESTION,
                                buttons=Gtk.ButtonsType.NONE, text=title)
        dlg.format_secondary_text(text)
        for name, rid in buttons:
            dlg.add_button(name, rid)
        if default is not None:
            dlg.set_default_response(default)
        resp = dlg.run()
        dlg.destroy()
        return resp

    def error(self, title, text):
        dlg = Gtk.MessageDialog(transient_for=self, modal=True, message_type=Gtk.MessageType.ERROR,
                                buttons=Gtk.ButtonsType.CLOSE, text=title)
        dlg.format_secondary_text(text)
        dlg.run()
        dlg.destroy()

    def about(self):
        dlg = Gtk.AboutDialog(transient_for=self, modal=True, program_name=APP_NAME, version=__version__,
                              comments="An external mod manager for Slay the Spire 2.\n"
                                       "Reads the game's own files; never patches the game.",
                              logo_icon_name="applications-games")
        dlg.run()
        dlg.destroy()

    def _on_close(self, *_):
        if self.state.change_count():
            resp = self.ask("Apply changes before closing?",
                            "You have changes that haven't been written to the game's settings.",
                            [("Cancel", 0), ("Discard", 1), ("Apply", 2)], default=2)
            if resp <= 0:
                return True
            if resp == 2:
                self.apply()
                return bool(self.state.change_count())
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.sts2modloader", flags=Gio.ApplicationFlags.FLAGS_NONE)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                 Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        GLib.set_application_name(APP_NAME)
        GLib.set_prgname("sts2-mod-loader")

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()

    def do_shutdown(self):
        Worker.closing = True
        Gtk.Application.do_shutdown(self)


def run():
    status = App().run([])
    # Background threads may still be blocked on network I/O. Letting the interpreter
    # tear down around them can crash PyGObject, so leave directly (all files are closed).
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(status)
