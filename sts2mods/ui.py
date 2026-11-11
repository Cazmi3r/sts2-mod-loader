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
                            "TkFixedFont")
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
    root.option_add("*TCombobox*Listbox.background", C["surface"])
    root.option_add("*TCombobox*Listbox.foreground", C["fg"])
    root.option_add("*TCombobox*Listbox.selectBackground", C["accent"])
    root.option_add("*TCombobox*Listbox.selectForeground", C["accent_fg"])
    root.option_add("*TCombobox*Listbox.font", F["base"])
    root.option_add("*TCombobox*Listbox.borderWidth", 0)

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
    st.configure("TCombobox", **field, arrowcolor=C["dim"], arrowsize=13)
    st.map("TCombobox",
           fieldbackground=[("readonly", C["input"])], foreground=[("readonly", C["fg"])],
           selectbackground=[("readonly", C["input"])], selectforeground=[("readonly", C["fg"])],
           background=[("active", C["hover"]), ("readonly", C["input"])],
           bordercolor=[("focus", C["accent"]), ("active", C["faint"])],
           lightcolor=[("focus", C["accent"])], arrowcolor=[("active", C["fg"])])
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
            self.configure(cursor="hand2")
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
        self.configure(cursor="hand2" if on and self.command else "")
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
                         bg=parent_bg(parent), cursor="hand2", takefocus=0)
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
    def __init__(self, widget, text):
        self.widget, self.text, self.tip, self.job = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e):
        self.job = self.widget.after(550, self._show)

    def _show(self):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.configure(bg=C["toast_bg"])
        tk.Label(self.tip, text=self.text, bg=C["toast_bg"], fg=C["toast_fg"], font=F["small"],
                 padx=8, pady=4, justify="left", wraplength=360).pack()
        self.tip.wm_geometry(f"+{x}+{y}")

    def _hide(self, _e=None):
        if self.job:
            self.widget.after_cancel(self.job)
            self.job = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


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


class Select(ttk.Combobox):
    """A read-only dropdown of (key, label) choices."""

    def __init__(self, parent, choices, value=None, on_change=None, width=None):
        self.choices = list(choices)
        super().__init__(parent, state="readonly", values=[t for _, t in self.choices], font=F["base"],
                         width=width or max(len(t) for _, t in self.choices) + 1)
        self.on_change = on_change
        if value is not None:
            self.set_key(value, quiet=True)
        self.bind("<<ComboboxSelected>>", lambda e: (self.selection_clear(), on_change and on_change()))

    def key(self):
        i = self.current()
        return self.choices[i][0] if i >= 0 else None

    def set_key(self, key, quiet=False):
        for i, (k, _t) in enumerate(self.choices):
            if k == key:
                self.current(i)
                if not quiet and self.on_change:
                    self.on_change()
                return

    def set_choices(self, choices, value=None):
        self.choices = list(choices)
        self.configure(values=[t for _, t in self.choices])
        if value is not None:
            self.set_key(value, quiet=True)


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


class Dialog(tk.Toplevel):
    """A themed modal dialog. Build content in .body, add buttons with .button(); .run() returns
    the value of the button pressed (or None when closed)."""

    def __init__(self, parent, title, width=460):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=C["surface"])
        self.transient(parent)
        self.resizable(True, True)
        self.result = None
        self.minsize(width, 10)
        self.width = width
        self.body = tk.Frame(self, bg=C["surface"], padx=22, pady=18)
        self.body.pack(fill="both", expand=True)
        self.footer = tk.Frame(self, bg=C["surface2"], padx=16, pady=10)
        self.footer.pack(fill="x", side="bottom")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda e: self.close())
        self.default = None

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
            self.bind("<Return>", lambda e: self.close(value))
        return b

    def close(self, value=None):
        self.result = value
        self.grab_release()
        self.destroy()

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
        max_share of the screen height, scrolling beyond that."""
        self._wrap_labels()
        self.update_idletasks()
        need = scroll.body.winfo_reqheight()
        room = int(self.winfo_screenheight() * max_share)
        bar = scroll.vsb.winfo_reqwidth() if need > room else 0     # leave room for the scroll bar
        scroll.canvas.configure(width=self.width - 2 * 22 - bar, height=max(min_h, min(need, room)))

    def run(self):
        self._wrap_labels()
        self.update_idletasks()
        p = self.master.winfo_toplevel()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = p.winfo_rootx() + (p.winfo_width() - w) // 2
        y = p.winfo_rooty() + max(40, (p.winfo_height() - h) // 3)
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.deiconify()
        self.lift()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        self.wait_window()
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
