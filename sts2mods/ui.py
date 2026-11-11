"""Theme and widgets for the Tk front end: one palette (light and dark), drawn controls with
rounded corners, scrollable panes and dialogs that look the same on Linux, Windows and macOS."""

import base64
import io
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

from . import osenv

LIGHT = dict(
    bg="#f3f4f7", surface="#ffffff", surface2="#eceef3", hover="#f2f4f8", select="#e7ecff",
    border="#dcdfe6", fg="#1b1f27", dim="#5d6574", faint="#8d95a3",
    accent="#4c6ef5", accent_hover="#3b5bdb", accent_fg="#ffffff", accent_soft="#e3e9ff",
    error="#d14343", warn="#c3850a", stale="#8a8266", ok="#2a9a54", info="#7b8394",
    toast_bg="#1f232b", toast_fg="#f3f4f6", input="#ffffff",
)
DARK = dict(
    bg="#131519", surface="#1a1d23", surface2="#23272f", hover="#20242c", select="#252f4c",
    border="#2d323c", fg="#e7e9ee", dim="#a2a9b6", faint="#747c8a",
    accent="#6a85ff", accent_hover="#8199ff", accent_fg="#ffffff", accent_soft="#27325a",
    error="#f0665f", warn="#e2ae3c", stale="#a69e80", ok="#4bc27a", info="#8a92a3",
    toast_bg="#e9ebf0", toast_fg="#14161b", input="#1f232a",
)
C = dict(LIGHT)      # the active palette; filled in by apply_theme()
F = {}               # named fonts
SCALE = [1.0]        # display scaling (1.5 at 150%); set by apply_theme()


def px(n):
    """A layout size in pixels, grown with the display's scaling. On Windows and Linux text
    gets bigger at 125%/150%/200% while plain pixel sizes don't; macOS scales both itself."""
    return round(n * SCALE[0])


def blend(color, other, amount):
    """Mix `amount` of color into other (both #rrggbb)."""
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(other[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x * amount + y * (1 - amount)):02x}" for x, y in zip(a, b))


def soft(name):
    return blend(C[name], C["surface"], 0.14)


def _pick_family(root, wanted, fallback):
    have = {f.lower() for f in tkfont.families(root)}
    for w in wanted:
        if w.lower() in have:
            return w
    return fallback


def apply_theme(root, dark=None):
    dark = osenv.prefers_dark() if dark is None else dark
    C.clear()
    C.update(DARK if dark else LIGHT)

    try:
        SCALE[0] = 1.0 if osenv.IS_MAC else max(1.0, root.winfo_fpixels("1i") / 96)
    except tk.TclError:
        SCALE[0] = 1.0
    default = tkfont.nametofont("TkDefaultFont")
    if osenv.IS_WINDOWS:
        family, size, mono = "Segoe UI", 10, _pick_family(root, ["Cascadia Mono", "Consolas"], "Courier New")
    elif osenv.IS_MAC:
        family, size, mono = default.actual("family"), 13, _pick_family(root, ["SF Mono", "Menlo"], "Monaco")
    else:
        family = _pick_family(root, ["Inter", "Cantarell", "Ubuntu", "Noto Sans", "Open Sans", "DejaVu Sans"],
                              default.actual("family"))
        size = 10
        mono = _pick_family(root, ["JetBrains Mono", "Ubuntu Mono", "DejaVu Sans Mono", "Noto Sans Mono"],
                            "monospace")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        tkfont.nametofont(name).configure(family=family, size=size)
    F.update(
        base=tkfont.Font(root, family=family, size=size),
        bold=tkfont.Font(root, family=family, size=size, weight="bold"),
        small=tkfont.Font(root, family=family, size=size - 1),
        small_bold=tkfont.Font(root, family=family, size=size - 1, weight="bold"),
        title=tkfont.Font(root, family=family, size=size + 7, weight="bold"),
        app=tkfont.Font(root, family=family, size=size + 2, weight="bold"),
        section=tkfont.Font(root, family=family, size=size + 1, weight="bold"),
        verdict=tkfont.Font(root, family=family, size=size + 1, weight="bold"),
        dot=tkfont.Font(root, family=family, size=size + 2),
        mono=tkfont.Font(root, family=mono, size=size - 1),
        hint=tkfont.Font(root, family=family, size=size + 2),
    )

    root.configure(bg=C["bg"])
    root.option_add("*Font", F["base"])
    root.option_add("*Background", C["surface"])
    root.option_add("*Foreground", C["fg"])
    root.option_add("*selectBackground", C["accent"])
    root.option_add("*selectForeground", C["accent_fg"])
    root.option_add("*Menu.background", C["surface"])
    root.option_add("*Menu.foreground", C["fg"])
    root.option_add("*Menu.activeBackground", C["accent"])
    root.option_add("*Menu.activeForeground", C["accent_fg"])
    root.option_add("*Menu.relief", "flat")
    root.option_add("*Menu.borderWidth", 1)

    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=C["surface"], foreground=C["fg"], font=F["base"],
                 bordercolor=C["border"], lightcolor=C["surface"], darkcolor=C["surface"],
                 troughcolor=C["surface"], focuscolor=C["accent"], selectbackground=C["accent"],
                 selectforeground=C["accent_fg"], insertcolor=C["fg"])
    field = dict(fieldbackground=C["input"], background=C["input"], foreground=C["fg"],
                 bordercolor=C["border"], lightcolor=C["input"], darkcolor=C["input"],
                 insertcolor=C["fg"], padding=(8, 6))
    st.configure("TEntry", **field)
    st.map("TEntry", bordercolor=[("focus", C["accent"])], lightcolor=[("focus", C["accent"])])
    for orient in ("Vertical", "Horizontal"):
        track = blend(C["fg"], C["surface"], 0.06)     # no grip lines: gripcount on Tk 8.6, gripsize on Tk 9
        st.configure(f"{orient}.TScrollbar", background=blend(C["fg"], C["surface"], 0.55),
                     troughcolor=track, bordercolor=track, lightcolor=track,
                     darkcolor=track, arrowcolor=track, gripcount=0, gripsize=0, arrowsize=0)
        st.map(f"{orient}.TScrollbar", background=[("active", blend(C["fg"], C["surface"], 0.75))])
    try:   # slim scrollbars without arrow buttons
        st.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
    except tk.TclError:
        pass
    # clam sizes the bar from arrowsize; with 0 it was 1px wide and effectively invisible.
    # The layout above has no arrow buttons, so none are drawn.
    st.configure("Vertical.TScrollbar", width=10, arrowsize=10)


# ---- drawing helpers ----------------------------------------------------------------------

def _round_rect(canvas, x1, y1, x2, y2, r, **kw):
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def parent_bg(widget):
    try:
        return widget.cget("bg")
    except tk.TclError:
        return C["surface"]


class Pill(tk.Canvas):
    """A rounded button or badge. With `command` it reacts to hover and clicks."""

    VARIANTS = {
        "primary": lambda: (C["accent"], C["accent_fg"], C["accent_hover"], None),
        "secondary": lambda: (C["surface2"], C["fg"], blend(C["fg"], C["surface2"], 0.10), None),
        "ghost": lambda: (None, C["fg"], C["surface2"], None),
        "danger": lambda: (soft("error"), C["error"], blend(C["error"], C["surface"], 0.24), None),
        "outline": lambda: (C["surface"], C["fg"], C["hover"], C["border"]),
    }

    def __init__(self, parent, text, command=None, variant="secondary", font=None, padx=14, pady=6,
                 radius=8, bg=None, fg=None, tooltip=None, width=None):
        self.pbg = parent_bg(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self.pbg, takefocus=0)
        self.command, self.variant = command, variant
        self.font = font or (F["bold"] if command and variant == "primary" else F["base"])
        self.padx, self.pady, self.radius, self.fixed_width = padx, pady, radius, width
        self.custom = (bg, fg)
        self.enabled, self.hover = True, False
        self.text = text
        self._draw()
        if command:
            self.bind("<Enter>", lambda e: self._set_hover(True))
            self.bind("<Leave>", lambda e: self._set_hover(False))
            self.bind("<ButtonRelease-1>", self._click)
        if tooltip:
            Tooltip(self, tooltip)

    def _colors(self):
        if self.command is None:
            return self.custom[0] or C["surface2"], self.custom[1] or C["dim"], None
        bg, fg, hover, border = self.VARIANTS[self.variant]()
        bg = self.custom[0] or bg
        fg = self.custom[1] or fg
        if not self.enabled:
            return (bg and blend(bg, self.pbg, 0.5)), blend(fg, self.pbg, 0.45), border
        if self.hover:
            bg = hover
        return bg, fg, border

    def _draw(self):
        self.delete("all")
        tw = self.font.measure(self.text)
        w = self.fixed_width or tw + 2 * self.padx
        h = self.font.metrics("linespace") + 2 * self.pady
        self.configure(width=w, height=h)
        bg, fg, border = self._colors()
        if bg or border:
            _round_rect(self, 1, 1, w - 1, h - 1, self.radius, fill=bg or self.pbg,
                        outline=border or bg or self.pbg, width=1)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)

    def _set_hover(self, on):
        self.hover = on
        self._draw()

    def _click(self, e):
        if self.enabled and 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height():
            self.command()

    def set_text(self, text):
        self.text = text
        self._draw()

    def set_enabled(self, on):
        self.enabled = bool(on)
        self._draw()

    def restyle(self, bg=None):
        self.pbg = bg or parent_bg(self.master)
        self.configure(bg=self.pbg)
        self._draw()


def badge(parent, text, kind="neutral"):
    colors = {"neutral": (C["surface2"], C["dim"]), "accent": (C["accent_soft"], C["accent"]),
              "ok": (soft("ok"), C["ok"]), "warn": (soft("warn"), C["warn"]),
              "error": (soft("error"), C["error"])}[kind]
    return Pill(parent, text, font=F["small"], padx=8, pady=2, radius=9, bg=colors[0], fg=colors[1])


class Toggle(tk.Canvas):
    """A drawn checkbox (style="check") or switch (style="switch")."""

    def __init__(self, parent, value=False, command=None, style="check", tooltip=None):
        self.style = style
        self.w, self.h = (18, 18) if style == "check" else (38, 22)
        super().__init__(parent, width=self.w, height=self.h, highlightthickness=0, bd=0,
                         bg=parent_bg(parent), takefocus=0)
        self.value, self.command, self.hover = bool(value), command, False
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        if tooltip:
            Tooltip(self, tooltip)
        self._draw()

    def _hover(self, on):
        self.hover = on
        self._draw()

    def _click(self, _e):
        self.value = not self.value
        self._draw()
        if self.command:
            self.command(self.value)
        return "break"

    def set(self, value):
        self.value = bool(value)
        self._draw()

    def restyle(self, bg):
        self.configure(bg=bg)
        self._draw()

    def _draw(self):
        self.delete("all")
        on = self.value
        if self.style == "check":
            fill = C["accent"] if on else C["input"]
            outline = C["accent"] if on else (C["accent"] if self.hover else blend(C["fg"], C["surface"], 0.35))
            _round_rect(self, 1, 1, self.w - 1, self.h - 1, 5, fill=fill, outline=outline, width=1.5)
            if on:
                self.create_line(4.5, 9.5, 7.5, 12.5, 13.5, 5.5, fill=C["accent_fg"], width=2,
                                 capstyle="round", joinstyle="round")
        else:
            track = C["accent"] if on else blend(C["fg"], C["surface"], 0.25)
            if self.hover:
                track = C["accent_hover"] if on else blend(C["fg"], C["surface"], 0.35)
            _round_rect(self, 1, 1, self.w - 1, self.h - 1, self.h / 2, fill=track, outline=track)
            r = self.h / 2 - 3
            cx = self.w - 3 - r if on else 3 + r
            self.create_oval(cx - r, self.h / 2 - r, cx + r, self.h / 2 + r, fill="#ffffff", outline="")


class Spinner(tk.Canvas):
    def __init__(self, parent, size=16):
        super().__init__(parent, width=size, height=size, highlightthickness=0, bd=0, bg=parent_bg(parent))
        self.size, self.angle, self.job = size, 0, None

    def start(self):
        if self.job is None:
            self._tick()

    def stop(self):
        if self.job is not None:
            self.after_cancel(self.job)
            self.job = None
        self.delete("all")

    def _tick(self):
        self.delete("all")
        s = self.size
        self.create_arc(2, 2, s - 2, s - 2, start=self.angle, extent=270, style="arc",
                        outline=C["accent"], width=2)
        self.angle = (self.angle - 24) % 360
        self.job = self.after(45, self._tick)


class Tooltip:
    """A hint shown under a widget after the pointer rests on it. Drawn inside the window
    (not a separate OS window), in the theme's colors."""

    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _e):
        self._hide()
        self.job = self.widget.after(550, self._show)

    def _show(self):
        self.job = None
        if self.tip or not self.text:
            return
        w = self.widget
        win = w.winfo_toplevel()
        tip = self.tip = tk.Frame(win, bg=blend(C["fg"], C["surface2"], 0.22), padx=1, pady=1)
        tk.Label(tip, text=self.text, bg=C["surface2"], fg=C["fg"], font=F["small"], padx=9, pady=5,
                 justify="left", wraplength=max(120, min(360, win.winfo_width() - 40))).pack()
        tip.update_idletasks()
        tw, th = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = w.winfo_rootx() - win.winfo_rootx() + 8
        top = w.winfo_rooty() - win.winfo_rooty()
        y = top + w.winfo_height() + 6
        if y + th > win.winfo_height() - 8:          # no room below: show above
            y = top - th - 6
        tip.place(x=max(8, min(x, win.winfo_width() - tw - 8)), y=max(8, y))
        tip.lift()
        self.job = w.after(400, self._watch)

    def _watch(self):
        """Hide if the pointer left without a Leave event (the widget moved or was hidden)."""
        self.job = None
        try:
            over = self.widget.winfo_containing(*self.widget.winfo_pointerxy())
            shown = self.widget.winfo_viewable()
        except (tk.TclError, KeyError):
            over, shown = None, False
        while over is not None and over is not self.widget:
            over = getattr(over, "master", None)
        if over is None or not shown:
            self._hide()
        else:
            self.job = self.widget.after(400, self._watch)

    def _hide(self, _e=None):
        try:
            if self.job:
                self.widget.after_cancel(self.job)
            if self.tip:
                self.tip.destroy()
        except tk.TclError:
            pass
        self.job = self.tip = None


class WrapLabel(tk.Label):
    """A label that wraps to whatever width it is given."""

    def __init__(self, parent, text="", **kw):
        kw.setdefault("bg", parent_bg(parent))
        kw.setdefault("fg", C["fg"])
        kw.setdefault("justify", "left")
        kw.setdefault("anchor", "w")
        super().__init__(parent, text=text, **kw)
        self.bind("<Configure>", lambda e: self.configure(wraplength=max(50, e.width - 4)))


class ReadOnlyText(tk.Text):
    """Selectable, wrapped, read-only text whose height follows its content."""

    def __init__(self, parent, text, font=None, fg=None, bg=None, max_lines=None):
        bg = bg or parent_bg(parent)
        super().__init__(parent, wrap="word", font=font or F["base"], fg=fg or C["fg"], bg=bg,
                         relief="flat", bd=0, highlightthickness=0, padx=0, pady=0, height=1,
                         cursor="xterm", insertwidth=0, selectbackground=C["accent"],
                         selectforeground=C["accent_fg"], spacing1=1, spacing3=1, takefocus=0)
        self.max_lines = max_lines
        self.insert("1.0", text)
        self.configure(state="disabled")
        self.bind("<Configure>", lambda e: self.after_idle(self._fit))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):   # let the pane scroll instead
            self.bind(seq, lambda e: None)

    def _fit(self):
        try:
            lines = self.count("1.0", "end", "displaylines")
        except tk.TclError:
            return
        n = (lines[0] if isinstance(lines, tuple) else lines) or 1
        if self.max_lines:
            n = min(n, self.max_lines)
        if int(self.cget("height")) != n:
            self.configure(height=n)


class ScrollFrame(tk.Frame):
    """A vertically scrolling container. Put children in .body."""

    def __init__(self, parent, bg=None, padx=0, pady=0):
        bg = bg or C["surface"]
        super().__init__(parent, bg=bg)
        # yscrollincrement=1 lets the view move by single pixels, so scrolling glides
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=1)
        self._target = None       # pixel offset a smooth scroll is heading to
        self._anim = None
        self.vsb = ttk.Scrollbar(self, orient="vertical",
                                 command=lambda *a: (self._stop(), self.canvas.yview(*a)))
        self.body = tk.Frame(self.canvas, bg=bg, padx=padx, pady=pady)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda e: self._update_region())
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(self._win, width=e.width),
                                                   self._update_region()))

    def _update_region(self):
        self.canvas.configure(scrollregion=(0, 0, self.body.winfo_reqwidth(), self.body.winfo_reqheight()))

    def _on_scroll(self, first, last):
        self.vsb.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            self.vsb.pack_forget()
        elif not self.vsb.winfo_ismapped():
            # before= the canvas: packed after it, the bar got whatever width was left (1px)
            self.vsb.pack(side="right", fill="y", before=self.canvas)

    def _max_offset(self):
        return max(0, self.body.winfo_reqheight() - self.canvas.winfo_height())

    def scroll(self, pixels):
        """Scroll smoothly by `pixels` (positive = down). Repeated calls add up, so a fast flick
        or a run of trackpad events keeps gliding instead of jumping in steps."""
        top = self._max_offset()
        if top <= 0:
            return
        start = self._target if self._target is not None else self.canvas.canvasy(0)
        self._target = max(0.0, min(float(top), start + pixels))
        if self._anim is None:
            self._step()

    def _step(self):
        self._anim = None
        if self._target is None:
            return
        cur = self.canvas.canvasy(0)
        gap = self._target - cur
        if abs(gap) < 1:
            self._target = None
            return
        move = gap * 0.35
        move = int(move) if abs(move) >= 1 else (1 if gap > 0 else -1)
        self.canvas.yview_scroll(move, "units")
        if self.canvas.canvasy(0) == cur:      # hit an end
            self._target = None
            return
        self._anim = self.after(12, self._step)

    def _stop(self):
        self._target = None
        if self._anim is not None:
            self.after_cancel(self._anim)
            self._anim = None

    def to_top(self):
        self._stop()
        self.canvas.yview_moveto(0)

    def see(self, widget):
        """Scroll so widget (a child of body) is visible."""
        self.update_idletasks()
        total = max(1, self.body.winfo_reqheight())
        self._stop()
        y, h = widget.winfo_y(), widget.winfo_height()
        top = self.canvas.canvasy(0)
        view = self.canvas.winfo_height()
        if y < top:
            self.canvas.yview_moveto(y / total)
        elif y + h > top + view:
            self.canvas.yview_moveto((y + h - view) / total)

    def clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.to_top()


WHEEL_STEP = 60       # pixels per mouse wheel notch
MAC_DELTA_PX = 14     # pixels per unit of macOS wheel/trackpad delta


def install_wheel(root):
    """Route the mouse wheel to the ScrollFrame under the pointer, on every platform."""
    # Tk 8.7+ (Tk 9, as in Homebrew's Python) reports wheel notches as 120 on every system and
    # sends trackpad scrolling as separate <TouchpadScroll> events with pixel deltas.
    tk_version = tuple(int(x) for x in str(root.tk.call("info", "patchlevel")).split(".")[:2])
    per_notch_120 = tk_version >= (8, 7) or not osenv.IS_MAC

    def handler(e, touch=False):
        w = root.winfo_containing(e.x_root, e.y_root)
        while w is not None and not isinstance(w, ScrollFrame):
            w = getattr(w, "master", None)
        if w is None:
            return
        try:
            grab = root.grab_current()
        except (tk.TclError, KeyError):
            grab = None
        if grab is not None and grab is not root:     # a dialog is open: only scroll inside it
            inside = w
            while inside is not None and inside is not grab:
                inside = getattr(inside, "master", None)
            if inside is None:
                return
        # One wheel notch scrolls the same distance on every system (WHEEL_STEP pixels);
        # trackpads send many small events and scroll proportionally.
        if e.num == 4:
            pixels = -WHEEL_STEP
        elif e.num == 5:
            pixels = WHEEL_STEP
        elif touch:
            pixels = -float(root.tk.call("tk::PreciseScrollDeltas", e.delta)[1])   # (dx, dy)
        elif per_notch_120:
            pixels = -e.delta / 120 * WHEEL_STEP  # Windows, and every system on Tk 8.7+
        else:
            pixels = -e.delta * MAC_DELTA_PX      # Tk 8.6 on macOS: delta is ±1 per line
        w.scroll(pixels)
    root.bind_all("<MouseWheel>", handler, add="+")
    root.bind_all("<Button-4>", handler, add="+")
    root.bind_all("<Button-5>", handler, add="+")
    if tk_version >= (8, 7):
        try:
            root.bind_all("<TouchpadScroll>", lambda e: handler(e, touch=True), add="+")
        except tk.TclError:
            pass


class SearchEntry(ttk.Entry):
    """An entry with placeholder text. .value() returns the typed text."""

    def __init__(self, parent, placeholder, on_change=None, on_enter=None):
        self.var = tk.StringVar()
        super().__init__(parent, textvariable=self.var, font=F["base"])
        self.hint = tk.Label(self, text=placeholder, bg=C["input"], fg=C["faint"], font=F["base"],
                             cursor="xterm")
        self.hint.bind("<Button-1>", lambda e: self.focus_set())
        self.var.trace_add("write", lambda *_: self._changed(on_change))
        if on_enter:
            self.bind("<Return>", lambda e: on_enter())
        self.bind("<Escape>", lambda e: self.var.set(""))
        self._changed(None)

    def _changed(self, cb):
        if self.var.get():
            self.hint.place_forget()
        else:
            self.hint.place(x=9, rely=0.5, anchor="w")
        if cb:
            cb()

    def value(self):
        return self.var.get().strip()

    def set(self, text):
        self.var.set(text)


class Select(tk.Canvas):
    """A dropdown drawn as one rounded box (text and arrow together) with a themed list that
    opens below it. Looks the same on every system. Same interface as before: choices are
    (key, label) pairs; key(), set_key(), set_choices(), get(); fires on_change and
    <<ComboboxSelected>> when the user picks an item."""

    PADX, ARROW_W, MAX_ROWS = 10, 22, 12
    _open = None                       # the Select whose list is showing, if any

    def __init__(self, parent, choices, value=None, on_change=None, width=None):
        self.pbg = parent_bg(parent)
        super().__init__(parent, highlightthickness=0, bd=0, bg=self.pbg, takefocus=1)
        self.choices = list(choices)
        self.on_change = on_change
        self.index = 0 if self.choices else -1
        self.enabled = True
        self.hover = self.focused = False
        self.popup = None
        self.hl = -1                   # highlighted row in the open list
        chars = width or max((len(t) for _, t in self.choices), default=8) + 1
        font = F["base"]
        self.req_w = font.measure("0") * chars + 2 * self.PADX + self.ARROW_W
        self.req_h = font.metrics("linespace") + 14
        self.configure(width=self.req_w, height=self.req_h)
        if value is not None:
            self.set_key(value, quiet=True)
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", lambda e: (self.focus_set(), self.toggle()))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        for seq, fn in (("<Down>", lambda: self._key_step(1)), ("<Up>", lambda: self._key_step(-1)),
                        ("<Prior>", lambda: self._key_step(-self.MAX_ROWS)),
                        ("<Next>", lambda: self._key_step(self.MAX_ROWS)),
                        ("<space>", self._key_enter), ("<Return>", self._key_enter),
                        ("<KP_Enter>", self._key_enter), ("<Escape>", self._key_escape),
                        ("<Tab>", lambda: (self.close(), None)[1])):
            self.bind(seq, lambda e, fn=fn: fn())
        self._draw()

    # ---- value ---------------------------------------------------------------------------

    def key(self):
        return self.choices[self.index][0] if 0 <= self.index < len(self.choices) else None

    def get(self):
        return self.choices[self.index][1] if 0 <= self.index < len(self.choices) else ""

    def current(self):
        return self.index

    def set_key(self, key, quiet=False):
        for i, (k, _t) in enumerate(self.choices):
            if k == key:
                self.index = i
                self._draw()
                if not quiet and self.on_change:
                    self.on_change()
                return

    def set_choices(self, choices, value=None):
        old = self.key()
        self.choices = list(choices)
        keys = [k for k, _ in self.choices]
        want = value if value is not None else old
        self.index = keys.index(want) if want in keys else (0 if self.choices else -1)
        if self.popup:
            self.close()
        self._draw()

    def set_enabled(self, on):
        self.enabled = bool(on)
        if not on:
            self.close()
        self.configure(takefocus=1 if on else 0)
        self._draw()

    def configure(self, cnf=None, **kw):
        # Accept the old dropdown's state option: "disabled", or "normal"/"readonly".
        if "state" in kw:
            self.set_enabled(kw.pop("state") != "disabled")
        return super().configure(cnf, **kw) if (cnf or kw) else None

    config = configure

    def _pick(self, i):
        self.close()
        if not 0 <= i < len(self.choices):
            return
        self.index = i
        self._draw()
        if self.on_change:
            self.on_change()
        self.event_generate("<<ComboboxSelected>>")

    # ---- drawing -------------------------------------------------------------------------

    def _set_hover(self, on):
        self.hover = on
        self._draw()

    def _set_focus(self, on):
        self.focused = on
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 2) if self.winfo_ismapped() else self.req_w
        h = self.req_h
        on = getattr(self, "enabled", True)
        active = on and (self.hover or self.popup is not None)
        fill = C["hover"] if active else (C["input"] if on else blend(C["input"], self.pbg, 0.5))
        edge = C["accent"] if on and (self.focused or self.popup) else \
            (C["faint"] if active else C["border"])
        _round_rect(self, 1, 1, w - 1, h - 1, 7, fill=fill, outline=edge, width=1)
        font = F["base"]
        text, room = self.get(), w - 2 * self.PADX - self.ARROW_W + 6
        if font.measure(text) > room:
            while text and font.measure(text + "…") > room:
                text = text[:-1]
            text += "…"
        self.create_text(self.PADX, h / 2, text=text, anchor="w", font=font,
                         fill=C["fg"] if on else C["faint"])
        cx, cy = w - self.PADX - 5, h / 2
        up = self.popup is not None
        pts = (cx - 4, cy + 2, cx, cy - 2, cx + 4, cy + 2) if up else (cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2)
        self.create_line(*pts, fill=C["fg"] if active else (C["dim"] if on else C["faint"]), width=1.6,
                         capstyle="round",
                         joinstyle="round")

    def restyle(self, bg=None):
        self.pbg = bg or parent_bg(self.master)
        self.configure(bg=self.pbg)
        self._draw()

    # ---- the list ------------------------------------------------------------------------

    def toggle(self):
        self.close() if self.popup else self.open()

    def open(self):
        """Show the list as an overlay inside this window: no separate OS window and no grab,
        so it behaves the same everywhere (dialogs included) and can never lock the app."""
        if not self.enabled:
            return
        if Select._open is not None and Select._open is not self:
            Select._open.close()
        if self.popup or not self.choices:
            return
        win = self._overlay_parent()
        win.update_idletasks()
        font = F["base"]
        # Measure a real row: label borders and padding differ by system and font.
        probe = self._row(win, "Ag", False)
        probe.update_idletasks()
        row_h = probe.winfo_reqheight()
        probe.destroy()
        content_h = len(self.choices) * row_h + 8 + 2
        # Room below and above the box, inside the window.
        top_y = self.winfo_rooty() - win.winfo_rooty()
        below_y = top_y + self.winfo_height() + 4
        room_below = win.winfo_height() - below_y - 8
        room_above = top_y - 4 - 8
        # When it has to scroll, show half of the next row: a clear "there's more" cue.
        want_h = min(content_h, self.MAX_ROWS * row_h + row_h // 2 + 4 + 2)
        if want_h <= room_below or room_below >= room_above:
            h, y = min(want_h, max(room_below, row_h + 10)), below_y
        else:
            h = min(want_h, room_above)
            y = top_y - 4 - h
        width = max(self.winfo_width(), max(font.measure(t) for _, t in self.choices) + 2 * 14 + 24)
        width = min(width, win.winfo_width() - 16)
        x = self.winfo_rootx() - win.winfo_rootx()
        x = max(8, min(x, win.winfo_width() - width - 8))

        frame = self.popup = tk.Frame(win, bg=C["border"])
        if h < content_h:                                   # doesn't all fit: scroll
            holder = ScrollFrame(frame, bg=C["surface"], pady=4)
            body = holder.body
        else:
            holder = body = tk.Frame(frame, bg=C["surface"], pady=4)
        holder.pack(padx=1, pady=1, fill="both", expand=True)
        self.rows = []
        for i, (_k, t) in enumerate(self.choices):
            lb = self._row(body, t, i == self.index)
            lb.pack(fill="x")
            lb.bind("<Enter>", lambda e, i=i: self._highlight(i))
            lb.bind("<ButtonRelease-1>", lambda e, i=i: self._pick(i))
            self.rows.append(lb)
        frame.place(x=x, y=y, width=width, height=h)
        frame.lift()
        Select._open = self
        self._win_size = (win.winfo_width(), win.winfo_height())
        _guard_clicks(win, frame)
        self._highlight(self.index)
        if isinstance(holder, ScrollFrame) and self.index >= 0:
            holder.see(self.rows[self.index])
        self._watch = self.after(200, self._watch_parent)
        self._draw()

    @staticmethod
    def _row(parent, text, current):
        return tk.Label(parent, text=text, anchor="w", padx=14, pady=5, font=F["base"], bd=0,
                        highlightthickness=0, bg=C["surface"], fg=C["accent"] if current else C["fg"])

    def _overlay_parent(self):
        """Where the list is drawn: the dialog this dropdown is in, if any, else its window.
        An open dialog takes the mouse for itself, so a list drawn on the window behind it
        couldn't be clicked."""
        w = self.master
        while w is not None:
            if isinstance(w, Dialog):
                return w
            w = getattr(w, "master", None)
        return self.winfo_toplevel()

    def close(self, keep_guard=False):
        if self.popup is None:
            return
        if getattr(self, "_watch", None):
            self.after_cancel(self._watch)
            self._watch = None
        self.popup.destroy()
        self.popup, self.rows, self.hl = None, [], -1
        if Select._open is self:
            Select._open = None
        if not keep_guard:
            _unguard_clicks()
        if self.winfo_exists():
            self._draw()

    def _watch_parent(self):
        """Close the list if the window was resized or the dropdown disappeared."""
        self._watch = None
        if self.popup is None:
            return
        try:
            win = self._overlay_parent()
            changed = (win.winfo_width(), win.winfo_height()) != self._win_size
            gone = not self.winfo_viewable()
        except tk.TclError:
            changed = gone = True
        if changed or gone:
            self.close()
        else:
            self._watch = self.after(200, self._watch_parent)

    def _highlight(self, i):
        self.hl = i
        for j, lb in enumerate(self.rows):
            lb.configure(bg=C["select"] if j == i else C["surface"])

    # ---- keyboard ------------------------------------------------------------------------

    def _key_step(self, d):
        if self.popup:
            self._highlight(max(0, min(len(self.rows) - 1, self.hl + d)))
            w = self.rows[self.hl].master
            while w is not None and not isinstance(w, ScrollFrame):
                w = w.master
            if w is not None:
                w.see(self.rows[self.hl])
        else:
            self.open()
        return "break"

    def _key_enter(self):
        if self.popup:
            self._pick(self.hl)
        else:
            self.open()
        return "break"

    def _key_escape(self):
        if self.popup:
            self.close()
            return "break"
        return None


# While a dropdown list is open, a click anywhere else in its window closes the list and is
# swallowed (press and release), so the button or row behind it doesn't also react. Done with
# a bindtag put in front of every other widget in the window, removed again afterwards.
_GUARD = "SelectClickGuard"
_guarded = []          # [(widget, original bindtags)]
_swallow_release = [False]


def _guard_clicks(win, popup):
    root = win._root()
    if not getattr(root, "_select_guard_bound", False):
        root._select_guard_bound = True
        for b in (1, 2, 3):           # real buttons only (on Linux 4/5 are the wheel)
            root.bind_class(_GUARD, f"<ButtonPress-{b}>", _guard_press)
            root.bind_class(_GUARD, f"<ButtonRelease-{b}>", _guard_release)
    _unguard_clicks()
    stack = [win]
    while stack:
        w = stack.pop()
        if w is popup:
            continue                  # clicks in the list itself work normally
        if not isinstance(w, tk.Toplevel) or w is win:
            tags = w.bindtags()
            if _GUARD not in tags:
                _guarded.append((w, tags))
                w.bindtags((_GUARD,) + tags)
            stack.extend(w.winfo_children())


def _unguard_clicks():
    for w, tags in _guarded:
        try:
            if w.winfo_exists():
                w.bindtags(tuple(t for t in w.bindtags() if t != _GUARD))
        except tk.TclError:
            pass
    _guarded.clear()


def _guard_press(e):
    """A click outside the open list: close it, and keep the guard until this click's
    release so nothing behind reacts to either half of the click."""
    sel = Select._open
    if sel is not None:
        _swallow_release[0] = True
        sel.close(keep_guard=True)
        try:
            e.widget.after(1000, _release_guard_late)    # in case the release never comes
        except (tk.TclError, AttributeError):
            pass
    else:
        _unguard_clicks()
    return "break"


def _guard_release(e):
    _swallow_release[0] = False
    if Select._open is None:
        _unguard_clicks()
    return "break"


def _release_guard_late():
    if _swallow_release[0] and Select._open is None:
        _swallow_release[0] = False
        _unguard_clicks()


class ChoiceList(tk.Frame):
    """A list of choices shown in place (for dialogs, where a dropdown's list has no room):
    a bordered box of rows, scrolling when there are more than `rows`. Click a row or use
    Up/Down/Home/End to pick. Same interface as Select (key, get, set_key, current, choices,
    on_change, <<ComboboxSelected>>), so callers can use either."""

    def __init__(self, parent, choices, value=None, on_change=None, rows=6):
        super().__init__(parent, bg=C["border"], padx=1, pady=1, takefocus=1)
        self.choices = list(choices)
        self.on_change = on_change
        self.index = 0 if self.choices else -1
        probe = Select._row(self, "Ag", False)
        probe.update_idletasks()
        row_h = probe.winfo_reqheight()
        probe.destroy()
        if len(self.choices) > rows:
            self.scroll = ScrollFrame(self, bg=C["surface"], pady=4)
            self.scroll.configure(height=rows * row_h + row_h // 2 + 4)   # half a row peeks out
            self.scroll.pack_propagate(False)
            body = self.scroll.body
        else:
            self.scroll = None
            body = tk.Frame(self, bg=C["surface"], pady=4)
        (self.scroll or body).pack(fill="both", expand=True)
        self.rows = []
        for i, (_k, t) in enumerate(self.choices):
            lb = Select._row(body, t, False)
            lb.pack(fill="x")
            lb.bind("<Button-1>", lambda e, i=i: (self.focus_set(), self._pick(i)))
            lb.bind("<Enter>", lambda e, i=i: self._paint(hover=i))
            lb.bind("<Leave>", lambda e: self._paint())
            self.rows.append(lb)
        for seq, d in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -rows), ("<Next>", rows),
                       ("<Home>", -10 ** 6), ("<End>", 10 ** 6)):
            self.bind(seq, lambda e, d=d: (self._pick(max(0, min(len(self.rows) - 1, self.index + d))), "break")[1])
        self.bind("<FocusIn>", lambda e: self.configure(bg=C["accent"]))
        self.bind("<FocusOut>", lambda e: self.configure(bg=C["border"]))
        if value is not None:
            self.set_key(value, quiet=True)
        self._paint()

    def key(self):
        return self.choices[self.index][0] if 0 <= self.index < len(self.choices) else None

    def get(self):
        return self.choices[self.index][1] if 0 <= self.index < len(self.choices) else ""

    def current(self):
        return self.index

    def set_key(self, key, quiet=False):
        for i, (k, _t) in enumerate(self.choices):
            if k == key:
                self._pick(i, quiet=quiet)
                return

    def _pick(self, i, quiet=False):
        if not 0 <= i < len(self.rows):
            return
        self.index = i
        self._paint()
        if self.scroll is not None:
            self.after_idle(lambda: self.scroll.see(self.rows[i]) if self.winfo_exists() else None)
        if not quiet:
            if self.on_change:
                self.on_change()
            self.event_generate("<<ComboboxSelected>>")

    def _paint(self, hover=None):
        for j, lb in enumerate(self.rows):
            picked = j == self.index
            lb.configure(bg=C["select"] if picked else (C["hover"] if j == hover else C["surface"]),
                         fg=C["accent"] if picked else C["fg"])


class CheckList(tk.Frame):
    """A list where any number of rows can be ticked (for dialogs): a bordered box of rows,
    scrolling when there are more than `rows`. Click a row to tick or untick it, Shift-click
    to tick everything from the last row clicked to this one. Keys: Up/Down/Home/End move,
    Space ticks, Ctrl+A (Cmd+A on macOS) ticks all, or unticks all when all are ticked.
    keys() returns the ticked keys in list order; on_change fires whenever they change."""

    def __init__(self, parent, choices, on_change=None, rows=8):
        super().__init__(parent, bg=C["border"], padx=1, pady=1, takefocus=1)
        self.choices = list(choices)
        self.on_change = on_change
        self.checked = set()               # row indexes
        self.cursor = 0 if self.choices else -1
        self.anchor = None                 # last row clicked, for Shift-click
        self.hover = None
        self.focused = False
        probe = self._row(self, "Ag")[0]
        probe.update_idletasks()
        row_h = probe.winfo_reqheight()
        probe.destroy()
        if len(self.choices) > rows:
            self.scroll = ScrollFrame(self, bg=C["surface"], pady=4)
            self.scroll.configure(height=rows * row_h + row_h // 2 + 4)   # half a row peeks out
            self.scroll.pack_propagate(False)
            body = self.scroll.body
        else:
            self.scroll = None
            body = tk.Frame(self, bg=C["surface"], pady=4)
        (self.scroll or body).pack(fill="both", expand=True)
        self.rows = []
        for i, (_k, t) in enumerate(self.choices):
            frame, box, label = self._row(body, t, lambda v, i=i: self._box_clicked(i))
            frame.pack(fill="x")
            for w in (frame, label):
                w.bind("<Button-1>", lambda e, i=i: self._clicked(i, bool(e.state & 0x1)))
            for w in (frame, box, label):
                w.bind("<Enter>", lambda e, i=i: self._set_hover(i), add="+")
                w.bind("<Leave>", lambda e: self._set_hover(None), add="+")
            self.rows.append((frame, box, label))
        for seq, d in (("<Up>", -1), ("<Down>", 1), ("<Prior>", -rows), ("<Next>", rows),
                       ("<Home>", -10 ** 6), ("<End>", 10 ** 6)):
            self.bind(seq, lambda e, d=d: self._move(d))
        self.bind("<space>", lambda e: self._toggle(self.cursor))
        mod = "Command" if osenv.IS_MAC else "Control"
        for key in ("a", "A"):
            self.bind(f"<{mod}-{key}>", lambda e: self.set_all(len(self.checked) < len(self.rows)))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self._paint()

    @staticmethod
    def _row(parent, text, command=None):
        frame = tk.Frame(parent, bg=C["surface"], padx=12)
        box = Toggle(frame, command=command)
        box.pack(side="left", padx=(0, 10))
        label = tk.Label(frame, text=text, anchor="w", pady=6, font=F["base"], bd=0, highlightthickness=0,
                         bg=C["surface"], fg=C["fg"])
        label.pack(side="left", fill="x", expand=True)
        return frame, box, label

    def keys(self):
        return [self.choices[i][0] for i in sorted(self.checked)]

    def set_all(self, on):
        self.checked = set(range(len(self.rows))) if on else set()
        self._changed()
        return "break"

    def _changed(self):
        self._paint()
        if self.on_change:
            self.on_change()

    def _box_clicked(self, i):
        # The box has already flipped itself; _toggle sets it from our own record.
        self.focus_set()
        self.cursor = self.anchor = i
        self._toggle(i)

    def _clicked(self, i, shift):
        self.focus_set()
        self.cursor = i
        if shift and self.anchor is not None:
            lo, hi = sorted((self.anchor, i))
            self.checked |= set(range(lo, hi + 1))
            self._changed()
        else:
            self.anchor = i
            self._toggle(i)

    def _toggle(self, i):
        if 0 <= i < len(self.rows):
            self.checked ^= {i}
            self._changed()
        return "break"

    def _move(self, d):
        if self.rows:
            self.cursor = max(0, min(len(self.rows) - 1, self.cursor + d))
            self._paint()
            if self.scroll is not None:
                self.scroll.see(self.rows[self.cursor][0])
        return "break"

    def _set_hover(self, i):
        self.hover = i
        self._paint()

    def _set_focus(self, on):
        self.focused = on
        self.configure(bg=C["accent"] if on else C["border"])
        self._paint()

    def _paint(self):
        for j, (frame, box, label) in enumerate(self.rows):
            on = j in self.checked
            bg = C["select"] if on else C["hover"] if j == self.hover or (self.focused and j == self.cursor) \
                else C["surface"]
            frame.configure(bg=bg)
            label.configure(bg=bg)
            box.value = on
            box.restyle(bg)


class PopupMenu:
    """A themed menu that opens inside the window under `anchor` (a button), like a Select's
    list: no OS menu, so it looks the same on every system. items: (label, command) or
    (label, command, hint), or None for a dividing line. Click an item, or use Up/Down and
    Enter; Escape or a click elsewhere closes it."""

    def __init__(self, anchor, items):
        self.anchor, self.items = anchor, list(items)
        self.popup, self.rows, self.hl, self.prev_focus = None, [], -1, None

    def open(self):
        if Select._open is not None:
            Select._open.close()
        if self.popup:
            return
        a = self.anchor
        win = a.winfo_toplevel()
        try:
            self.prev_focus = win.focus_get()
        except KeyError:
            self.prev_focus = None
        frame = self.popup = tk.Frame(win, bg=blend(C["fg"], C["surface"], 0.22), padx=1, pady=1)
        body = tk.Frame(frame, bg=C["surface"], pady=4)
        body.pack(fill="both", expand=True)
        self.rows = []
        for item in self.items:
            if item is None:
                tk.Frame(body, bg=C["border"], height=1).pack(fill="x", pady=4)
                continue
            i = len(self.rows)
            row = tk.Frame(body, bg=C["surface"])
            row.pack(fill="x")
            parts = [row, tk.Label(row, text=item[0], anchor="w", padx=14, pady=6, font=F["base"],
                                   bg=C["surface"], fg=C["fg"])]
            parts[1].pack(side="left", fill="x", expand=True)
            if len(item) > 2:
                parts.append(tk.Label(row, text=item[2], padx=14, font=F["small"], bg=C["surface"], fg=C["faint"]))
                parts[2].pack(side="right")
            for w in parts:
                w.bind("<Enter>", lambda e, i=i: self._highlight(i))
                w.bind("<ButtonRelease-1>", lambda e, i=i: self._pick(i))
            self.rows.append((parts, item[1]))
        frame.update_idletasks()
        w, h = max(frame.winfo_reqwidth(), 220), frame.winfo_reqheight()
        x = a.winfo_rootx() - win.winfo_rootx() + a.winfo_width() - w       # right edges line up
        top = a.winfo_rooty() - win.winfo_rooty()
        y = top + a.winfo_height() + 4
        if y + h > win.winfo_height() - 8 and top - 4 - h >= 8:
            y = top - 4 - h
        frame.place(x=max(8, min(x, win.winfo_width() - w - 8)), y=y, width=w)
        frame.lift()
        Select._open = self
        _guard_clicks(win, frame)
        for seq, fn in (("<Down>", lambda: self._step(1)), ("<Up>", lambda: self._step(-1)),
                        ("<Return>", lambda: self._pick(self.hl)), ("<KP_Enter>", lambda: self._pick(self.hl)),
                        ("<space>", lambda: self._pick(self.hl)), ("<Escape>", self.close),
                        ("<Tab>", self.close)):
            frame.bind(seq, lambda e, fn=fn: (fn(), "break")[1])
        frame.bind("<FocusOut>", lambda e: self.close())
        frame.focus_set()

    def close(self, keep_guard=False):
        if self.popup is None:
            return
        popup, self.popup, self.rows, self.hl = self.popup, None, [], -1
        if Select._open is self:
            Select._open = None
        if not keep_guard:
            _unguard_clicks()
        prev, self.prev_focus = self.prev_focus, None
        try:
            had_focus = popup.focus_get() is popup
            popup.destroy()
            if had_focus and prev is not None and prev.winfo_exists():
                prev.focus_set()
        except (tk.TclError, KeyError):
            pass

    def _highlight(self, i):
        self.hl = i
        for j, (parts, _cmd) in enumerate(self.rows):
            for w in parts:
                w.configure(bg=C["select"] if j == i else C["surface"])

    def _step(self, d):
        if self.rows:
            self._highlight((self.hl + d) % len(self.rows) if self.hl >= 0 else (0 if d > 0 else len(self.rows) - 1))

    def _pick(self, i):
        if not 0 <= i < len(self.rows):
            return
        command = self.rows[i][1]
        self.close()
        command()


class InfoBar(tk.Frame):
    """A tinted strip with a message and optional buttons."""

    def __init__(self, parent, kind):
        super().__init__(parent, bg=soft(kind) if kind != "accent" else C["accent_soft"])
        bg = self.cget("bg")
        color = C[kind]
        tk.Frame(self, bg=color, width=4).pack(side="left", fill="y")
        self.buttons = tk.Frame(self, bg=bg)
        self.buttons.pack(side="right", padx=(8, 12), pady=6)
        self.label = WrapLabel(self, "", bg=bg, fg=C["fg"])
        self.label.pack(side="left", fill="x", expand=True, padx=12, pady=9)

    def add_button(self, text, command, variant="secondary"):
        b = Pill(self.buttons, text, command, variant=variant, pady=4, padx=12)
        b.pack(side="left", padx=(6, 0))
        return b

    def clear_buttons(self):
        for c in self.buttons.winfo_children():
            c.destroy()


def load_image(path, max_w, max_h):
    """A PhotoImage scaled to fit, or None. Uses Pillow when installed (needed for JPEG);
    otherwise Tk's own PNG/GIF support."""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        try:
            with Image.open(path) as im:
                im = im.convert("RGBA")
                im.thumbnail((max_w, max_h))
                buf = io.BytesIO()
                im.save(buf, "PNG")
            return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))
        except Exception:   # unreadable or unsupported image
            return None
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if not (head.startswith(b"\x89PNG") or head.startswith(b"GIF8")):
            return None
        # Shrinking a big image with Tk is slow, so keep the small copy next to the original.
        small = f"{path}.{max_w}x{max_h}.png"
        try:
            if os.path.getmtime(small) >= os.path.getmtime(path):
                return tk.PhotoImage(file=small)
        except (OSError, tk.TclError):
            pass
        img = tk.PhotoImage(file=path)
        factor = max(1, -(-img.width() // max_w), -(-img.height() // max_h))
        if factor == 1:
            return img
        img = img.subsample(factor)
        try:
            img.write(small, format="png")
        except tk.TclError:
            pass
        return img
    except (OSError, tk.TclError):
        return None


class Dialog(tk.Frame):
    """A themed modal dialog, drawn as a card inside the main window: no separate OS window
    or title bar, so it looks the same on every system. Build content in .body, add buttons
    with .button(); .run() returns the value of the button pressed (or None when closed).
    While it is open the rest of the window ignores the mouse, and Tab stays inside it."""

    stack = []          # open dialogs, the top one last

    def __init__(self, parent, title, width=460):
        self.win = parent.winfo_toplevel()
        # A dark band around a lighter edge sets the card off from what's behind it.
        dark = int(C["bg"][1:3], 16) < 128
        super().__init__(self.win, bg=blend("#000000", C["bg"], 0.45 if dark else 0.16), padx=3, pady=3)
        edge = tk.Frame(self, bg=blend(C["fg"], C["surface"], 0.24), padx=1, pady=1)
        edge.pack(fill="both", expand=True)
        self.title_text = title
        self.result = None
        self.width = px(width)
        self.footer = tk.Frame(edge, bg=C["surface2"], padx=16, pady=10)
        self.footer.pack(fill="x", side="bottom")       # packed first: stays when space is short
        self.body = tk.Frame(edge, bg=C["surface"], padx=22, pady=18)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.close())
        self.bind("<Tab>", lambda e: self._tab(1))
        for seq in ("<Shift-Tab>", "<ISO_Left_Tab>"):    # the second is Shift+Tab on Linux
            try:
                self.bind(seq, lambda e: self._tab(-1))
            except tk.TclError:
                pass
        self.default = None
        self.prev_focus = None

    def heading(self, text, sub=None):
        tk.Label(self.body, text=text, font=F["section"], bg=C["surface"], fg=C["fg"],
                 anchor="w", justify="left").pack(fill="x")
        if sub:
            WrapLabel(self.body, sub, fg=C["dim"]).pack(fill="x", pady=(6, 0))

    def button(self, text, value, variant="secondary", default=False):
        b = Pill(self.footer, text, lambda: self.close(value), variant=variant)
        b.pack(side="right", padx=(8, 0))
        if default:
            self.default = value
            for seq in ("<Return>", "<KP_Enter>"):
                self.bind(seq, lambda e: self.close(value))
        return b

    def close(self, value=None):
        if not self.winfo_exists():
            return
        self.result = value
        if self in Dialog.stack:
            Dialog.stack.remove(self)
        prev = self.prev_focus
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        try:
            if Dialog.stack:                             # hand the mouse back to the dialog below
                Dialog.stack[-1].grab_set()
            if prev is not None and prev.winfo_exists():
                prev.focus_set()
        except tk.TclError:
            pass

    def _owns(self, w):
        while w is not None and w is not self:
            w = getattr(w, "master", None)
        return w is self

    def _tab(self, step):
        """Move focus to the next (or previous) control inside this dialog."""
        try:
            w = self.focus_get() or self
            for _ in range(300):
                w = w.tk_focusNext() if step > 0 else w.tk_focusPrev()
                if w is None:
                    break
                if self._owns(w):
                    w.focus_set()
                    break
        except (tk.TclError, KeyError):
            pass
        return "break"

    def _adopt(self):
        """Let keys pressed in any control inside the dialog reach the dialog's own bindings
        (Escape, Enter, Tab), the way they would reach a window's."""
        me, top = str(self), str(self.win)
        stack = list(self.winfo_children())
        while stack:
            w = stack.pop()
            tags = w.bindtags()
            if me not in tags:
                i = tags.index(top) if top in tags else len(tags)
                w.bindtags(tags[:i] + (me,) + tags[i:])
            stack.extend(w.winfo_children())

    def _wrap_labels(self):
        # Wrapping text asks for room to fit on one line until it learns its width, which made
        # dialogs open far wider than intended. Wrap at the intended width from the start.
        stack = list(self.body.winfo_children())
        while stack:
            w = stack.pop()
            if isinstance(w, WrapLabel):
                w.configure(wraplength=self.width - 2 * 22 - 4)
            stack.extend(w.winfo_children())

    def fit_scroll(self, scroll, max_share=0.6, min_h=40):
        """Size a ScrollFrame in this dialog to its content: as tall as needed, up to
        max_share of the window's height, scrolling beyond that."""
        self._wrap_labels()
        self.update_idletasks()
        need = scroll.body.winfo_reqheight()
        room = int(self.win.winfo_height() * max_share)
        bar = scroll.vsb.winfo_reqwidth() if need > room else 0     # leave room for the scroll bar
        scroll.canvas.configure(width=self.width - 2 * 22 - bar, height=max(min_h, min(need, room)))

    def run(self):
        win = self.win
        if Select._open is not None:
            Select._open.close()
        self._wrap_labels()
        self._adopt()
        self.update_idletasks()
        win_w, win_h = win.winfo_width(), win.winfo_height()
        w = max(120, min(max(self.width, self.winfo_reqwidth()), win_w - 32))
        h = self.winfo_reqheight()
        if h > win_h - 48:                    # taller than the window: fill it, footer stays visible
            self.place(relx=0.5, anchor="n", y=24, width=w, height=max(80, win_h - 48))
        else:                                 # height follows the content
            self.place(relx=0.5, anchor="n", y=max(24, (win_h - h) // 3), width=w)
        self.lift()
        try:
            self.prev_focus = win.focus_get()
        except KeyError:
            self.prev_focus = None
        Dialog.stack.append(self)
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        self.wait_window(self)
        return self.result


def app_icon(root):
    """A small drawn icon (accent card with a light diamond) so the window isn't a blank square."""
    size = 64
    img = tk.PhotoImage(width=size, height=size)
    acc, light = C["accent"], "#ffffff"
    rows = []
    for y in range(size):
        row = []
        for x in range(size):
            r = 12
            cx = min(max(x, r), size - 1 - r)
            cy = min(max(y, r), size - 1 - r)
            inside = (x - cx) ** 2 + (y - cy) ** 2 <= r * r
            if not inside:
                row.append("{}")
                continue
            diamond = abs(x - size / 2 + 0.5) + abs(y - size / 2 + 0.5) <= 18
            row.append(light if diamond else acc)
        rows.append("{" + " ".join(c if c != "{}" else "#000000" for c in row) + "}")
    img.put(" ".join(rows))
    for y in range(size):          # make the corners transparent
        for x in range(size):
            r = 12
            cx = min(max(x, r), size - 1 - r)
            cy = min(max(y, r), size - 1 - r)
            if (x - cx) ** 2 + (y - cy) ** 2 > r * r:
                img.transparency_set(x, y, True)
    return img
