"""
Palette – the list of widgets the user can add.

Each item is a small card (icon + friendly name).  Press and drag an item onto
the window in the middle (app.workplane.begin_drag_new) or simply click it to
add it to the selected container (app.add_widget).  A search box filters the
list.
"""

import tkinter as tk
from tkinter import ttk

import model
from model import WIDGETS
import theme

DRAG_THRESHOLD = 5
TILE_H = 34

# accent colour per section (icon badge background, icon colour)
CATEGORY_COLORS = {
    "Basic": ("#dbeafe", "#1d4ed8"),
    "Input": ("#dcfce7", "#15803d"),
    "Display": ("#fef3c7", "#b45309"),
    "Containers": ("#ede9fe", "#6d28d9"),
    "Windows": ("#e0f2fe", "#0369a1"),
}
CATEGORY_TITLES = {
    "Basic": "Basic",
    "Input": "Input – things the user fills in",
    "Display": "Display",
    "Containers": "Containers – hold other widgets",
}


def _round_rect(c, x0, y0, x1, y1, r, **kw):
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
           x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
    return c.create_polygon(pts, smooth=True, **kw)


class _Tile(tk.Canvas):
    """One palette card, drawn on a canvas so it is a single widget."""

    def __init__(self, master, palette, wtype, name, icon, category, help_text):
        super().__init__(master, height=TILE_H, width=110, bg=theme.BG, highlightthickness=0,
                         bd=0, cursor="hand2", takefocus=0)
        self.palette, self.wtype = palette, wtype
        self.name, self.icon, self.category = name, icon, category
        self.state_ = "normal"
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        hint = "Drag it onto your window, or click to add it." if wtype else "Click to add a window."
        self.tip = theme.tooltip(self, "%s\n%s" % (help_text, hint))
        self.help_text = help_text
        self._p = None

    def draw(self):
        self.delete("all")
        w, h = max(self.winfo_width(), 40), TILE_H
        fill, outline = {"normal": (theme.PANEL, theme.BORDER),
                         "hover": (theme.ACCENT_LIGHT, theme.ACCENT),
                         "pressed": ("#bfdbfe", theme.ACCENT)}[self.state_]
        _round_rect(self, 1, 1, w - 2, h - 2, 7, fill=fill, outline=outline)
        badge_bg, badge_fg = CATEGORY_COLORS.get(self.category, ("#e5e7eb", theme.TEXT))
        _round_rect(self, 6, 6, 28, h - 6, 5, fill=badge_bg, outline="")
        self.create_text(17, h / 2, text=self.icon, fill=badge_fg,
                         font=(theme.UI_FAMILY, 10, "bold"))
        name = self.name
        font = (theme.UI_FAMILY, 9)
        self.create_text(35, h / 2, text=name, anchor="w", fill=theme.TEXT, font=font,
                         width=max(20, w - 40))

    def _set(self, state):
        if state != self.state_:
            self.state_ = state
            self.draw()

    def _enter(self, _e):
        if self._p is None:
            self._set("hover")
        self.palette.app.status("%s: %s %s" % (
            self.name, self.help_text,
            "Drag it onto your window, or click to add it." if self.wtype else ""))

    def _leave(self, _e):
        if self._p is None:
            self._set("normal")

    def _press(self, e):
        self._p = {"x": e.x_root, "y": e.y_root, "drag": False}
        self._set("pressed")

    def _motion(self, e):
        p = self._p
        if p is None or p["drag"] or self.wtype is None:
            return
        if abs(e.x_root - p["x"]) > DRAG_THRESHOLD or abs(e.y_root - p["y"]) > DRAG_THRESHOLD:
            # from now on the workplane receives the mouse events (and the
            # release never reaches us), so forget the press right away
            self._p = None
            self.tip._hide()
            self._set("normal")
            wp = getattr(self.palette.app, "workplane", None)
            if wp is not None and hasattr(wp, "begin_drag_new"):
                wp.begin_drag_new(self.wtype, e)

    def _release(self, e):
        p, self._p = self._p, None
        inside = (0 <= e.x < self.winfo_width() and 0 <= e.y < self.winfo_height())
        self._set("hover" if inside else "normal")
        if p is None or p["drag"] or not inside:
            return
        if self.wtype is None:
            self.palette.app.add_window()
        else:
            self.palette.app.add_widget(self.wtype)


class Palette(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._tiles = []          # (category, tile)
        self._sections = {}       # category -> (header, grid frame)

        head = ttk.Frame(self)
        head.pack(side="top", fill="x", padx=10, pady=(8, 2))
        ttk.Label(head, text="Widgets", style="Heading.TLabel").pack(side="top", anchor="w")
        ttk.Label(head, text="Drag one onto your window, or click to add it.",
                  style="Muted.TLabel", font=(theme.UI_FAMILY, 9)).pack(side="top", anchor="w")

        # ---- search box
        box = tk.Frame(self, bg="white", highlightthickness=1, highlightbackground=theme.BORDER,
                       highlightcolor=theme.ACCENT)
        box.pack(side="top", fill="x", padx=10, pady=(6, 6))
        tk.Label(box, text="⌕", bg="white", fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 12)).pack(side="left", padx=(6, 0))
        self.search_var = tk.StringVar(self)
        self.search = tk.Entry(box, textvariable=self.search_var, bd=0, bg="white", fg=theme.TEXT,
                               font=(theme.UI_FAMILY, 10), highlightthickness=0,
                               insertbackground=theme.TEXT)
        self.search.pack(side="left", fill="x", expand=True, padx=4, ipady=4)
        self._clear = tk.Label(box, text="✕", bg="white", fg=theme.MUTED, cursor="hand2",
                               font=(theme.UI_FAMILY, 9))
        self._clear.bind("<Button-1>", lambda e: (self.search_var.set(""), self.search.focus_set()))
        self._placeholder = tk.Label(box, text="Search widgets…", bg="white", fg="#9ca3af",
                                     font=(theme.UI_FAMILY, 10), cursor="xterm")
        self._placeholder.bind("<Button-1>", lambda e: self.search.focus_set())
        self.search_var.trace_add("write", lambda *a: self._filter())
        self.search.bind("<Return>", self._add_first)
        self.search.bind("<Escape>", lambda e: (self.search_var.set(""), "break")[1])
        box.bind("<Configure>", lambda e: self._update_placeholder())

        # ---- scrollable list
        outer, self.inner = theme.scrollable(self, bg=theme.BG)
        outer.pack(side="top", fill="both", expand=True)
        self.inner.columnconfigure(0, weight=1)
        self._build()
        self._update_placeholder()

    # ------------------------------------------------------------ building
    def _build(self):
        row = 0
        cats = list(model.PALETTE_CATEGORIES)
        for cat in cats:
            items = [(k, v) for k, v in WIDGETS.items()
                     if v.get("category") == cat and v.get("palette", True) is not False]
            if not items:
                continue
            row = self._section(row, cat, CATEGORY_TITLES.get(cat, cat), items)
        # extra: windows are added from here too (the window bar also has a button)
        row = self._section(row, "Windows", "Windows",
                            [(None, {"name": "New Window", "icon": "▣",
                                     "help": "Add another window to your app, for example a "
                                             "Settings window."})])
        self._empty = ttk.Label(self.inner, text="No widgets match your search.",
                                style="Muted.TLabel")
        self._empty_row = row

    def _section(self, row, cat, title, items):
        header = tk.Label(self.inner, text=title.upper(), bg=theme.BG, fg=theme.MUTED,
                          font=(theme.UI_FAMILY, 8, "bold"), anchor="w")
        header.grid(row=row, column=0, sticky="ew", padx=12, pady=(10, 3))
        grid = tk.Frame(self.inner, bg=theme.BG)
        grid.grid(row=row + 1, column=0, sticky="ew", padx=8)
        grid.columnconfigure(0, weight=1, uniform="tiles")
        grid.columnconfigure(1, weight=1, uniform="tiles")
        tiles = []
        for wtype, spec in items:
            t = _Tile(grid, self, wtype, spec["name"], spec["icon"], cat, spec.get("help", ""))
            tiles.append(t)
            self._tiles.append((cat, t))
        self._sections[cat] = (header, grid, tiles, row)
        self._layout_section(cat, tiles)
        return row + 2

    def _layout_section(self, cat, visible):
        header, grid, tiles, row = self._sections[cat]
        for t in tiles:
            t.grid_forget()
        for i, t in enumerate(visible):
            t.grid(row=i // 2, column=i % 2, sticky="ew", padx=3, pady=3)
        if visible:
            header.grid()
            grid.grid()
        else:
            header.grid_remove()
            grid.grid_remove()

    # ------------------------------------------------------------ search
    def _matches(self, tile, q, deep=False):
        """Search names (and with deep=True also the help text)."""
        if not q:
            return True
        parts = [tile.name, tile.wtype or "window", tile.category]
        if deep:
            parts.append(tile.help_text)
        hay = " ".join(parts).lower()
        return all(word in hay for word in q.split())

    def _deep(self, q):
        # look in the descriptions only when no name matches
        return not any(self._matches(t, q) for _c, t in self._tiles)

    def _filter(self):
        q = self.search_var.get().strip().lower()
        total = 0
        deep = self._deep(q)
        for cat, (header, grid, tiles, row) in self._sections.items():
            vis = [t for t in tiles if self._matches(t, q, deep)]
            total += len(vis)
            self._layout_section(cat, vis)
        if total == 0:
            self._empty.grid(row=self._empty_row, column=0, padx=12, pady=12, sticky="w")
        else:
            self._empty.grid_forget()
        self._update_placeholder()

    def _update_placeholder(self):
        if self.search_var.get():
            self._placeholder.place_forget()
            self._clear.pack(side="right", padx=6)
        else:
            self._clear.pack_forget()
            self._placeholder.place(in_=self.search, x=2, rely=0.5, anchor="w")

    def _add_first(self, _e=None):
        q = self.search_var.get().strip().lower()
        if not q:
            return "break"
        deep = self._deep(q)
        for _cat, t in self._tiles:
            if self._matches(t, q, deep):
                if t.wtype is None:
                    self.app.add_window()
                else:
                    self.app.add_widget(t.wtype)
                break
        return "break"
