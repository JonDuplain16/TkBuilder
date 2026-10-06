"""
App settings page of the inspector (shown when nothing is selected, or via
"App settings").  Edits project.settings (model.SETTINGS_SPECS) live.

The theme gallery is ONE canvas: a card per theme with a tiny mock-up of a
window drawn in that theme's colours.  Clicking a card applies the theme.
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog

import model
import theme
import dialogs
from dialogs import font, px, muted
import inspector as _insp

try:
    import apptheme
except ImportError:  # pragma: no cover
    apptheme = None

# Representative colours for the stock ttk themes (they have no palette).
#   raised: draw 3D button edges
STOCK_LOOKS = {
    "native": dict(bg="#f0f0f0", title="#ffffff", fg="#1a1a1a", field="#ffffff",
                   border="#a0a0a0", accent="#0078d4", button="#e1e1e1", raised=False),
    "clam": dict(bg="#dcdad5", title="#eeedeb", fg="#1a1a1a", field="#ffffff",
                 border="#9e9a91", accent="#4a6984", button="#dcdad5", raised=False),
    "alt": dict(bg="#d9d9d9", title="#ececec", fg="#000000", field="#ffffff",
                border="#888888", accent="#4a6984", button="#d9d9d9", raised=True),
    "default": dict(bg="#d9d9d9", title="#ececec", fg="#000000", field="#ffffff",
                    border="#8c8c8c", accent="#4a6984", button="#d9d9d9", raised=True),
    "classic": dict(bg="#d9d9d9", title="#c3c3c3", fg="#000000", field="#d9d9d9",
                    border="#828282", accent="#b03060", button="#d9d9d9", raised=True),
    "winnative": dict(bg="#f0f0f0", title="#000080", fg="#000000", field="#ffffff",
                      border="#808080", accent="#000080", button="#f0f0f0", raised=True),
    "xpnative": dict(bg="#ece9d8", title="#0a246a", fg="#000000", field="#ffffff",
                     border="#7f9db9", accent="#316ac5", button="#f4f3ee", raised=False),
}


def look_of(key):
    """Colours used to draw a theme's mock-up."""
    p = apptheme.palette(key) if apptheme else None
    if p:
        return dict(bg=p["bg"], title=p.get("heading", p["surface"]), fg=p["fg"],
                    field=p["field"], border=p["border"], accent=p["accent"],
                    button=p.get("button", p["surface"]), accent_fg=p.get("accent_fg", "#fff"),
                    trough=p.get("trough", p["border"]), raised=False)
    look = dict(STOCK_LOOKS.get(key, STOCK_LOOKS["native"]))
    look.setdefault("accent_fg", "#ffffff")
    look.setdefault("trough", "#e6e6e6")
    return look


def available_themes():
    """(modern, classic) lists of theme keys that work on this computer."""
    if apptheme is None:
        return [], []
    try:
        names = set(ttk.Style().theme_names())
    except tk.TclError:
        names = set()
    modern, classic = [], []
    for key in apptheme.THEME_ORDER:
        t = apptheme.THEMES.get(key)
        if t is None:
            continue
        if t["kind"] == "builtin" and key != "native":
            if names and t["base"] not in names:
                continue
            classic.append(key)
        else:
            modern.append(key)
    return modern, classic


class ThemeGallery(tk.Canvas):
    """Cards with mini mock-ups, drawn on one canvas.  on_pick(key)."""

    COLS = 2

    def __init__(self, parent, on_pick, on_hover=None):
        super().__init__(parent, bg=theme.BG, highlightthickness=0, bd=0, height=px(200),
                         cursor="hand2")
        self.on_pick = on_pick
        self.on_hover = on_hover
        self.current = None
        self.cards = []          # (x0, y0, x1, y1, key)
        self.hot = None
        self._width = 0
        self.modern, self.classic = available_themes()
        self.bind("<Configure>", self._on_resize)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hot(None))
        self.bind("<Button-1>", self._click)

    def set_current(self, key):
        if key != self.current:
            self.current = key
            self.redraw()

    def _on_resize(self, e):
        if abs(e.width - self._width) > 1:
            self._width = e.width
            self.redraw()

    # ---- drawing ----
    def redraw(self):
        self.delete("all")
        self.cards = []
        self.hot = None
        w = self._width or self.winfo_width()
        if w < 50:
            w = px(300)
        gap = px(8)
        cw = (w - gap * (self.COLS - 1)) / self.COLS
        mock_h = px(58)
        ch = mock_h + px(24)
        y = 0
        for title, keys in (("Modern", self.modern), ("Classic Tk looks", self.classic)):
            if not keys:
                continue
            self.create_text(0, y + px(2), text=title, anchor="nw", fill=theme.MUTED,
                             font=font(8, "bold"))
            y += px(20)
            for i, key in enumerate(keys):
                col = i % self.COLS
                if i and col == 0:
                    y += ch + gap
                x0 = col * (cw + gap)
                self._card(key, x0, y, x0 + cw, y + ch, mock_h)
            y += ch + px(14)
        self.configure(height=max(px(40), int(y)))

    def _card(self, key, x0, y0, x1, y1, mock_h):
        sel = key == self.current
        tag = "card_" + key
        self.create_rectangle(x0, y0, x1, y1, fill=theme.PANEL,
                              outline=theme.ACCENT if sel else theme.BORDER,
                              width=2 if sel else 1, tags=(tag, tag + "_frame"))
        L = look_of(key)
        m = px(7)
        wx0, wy0, wx1, wy1 = x0 + m, y0 + m, x1 - m, y0 + mock_h
        ww = wx1 - wx0
        # window + title bar
        self.create_rectangle(wx0, wy0, wx1, wy1, fill=L["bg"], outline=L["border"], tags=tag)
        self.create_rectangle(wx0, wy0, wx1, wy0 + px(9), fill=L["title"], outline=L["border"],
                              tags=tag)
        for k in range(3):
            cx = wx1 - px(5) - k * px(6)
            self.create_oval(cx - 1.5, wy0 + px(4.5) - 1.5, cx + 1.5, wy0 + px(4.5) + 1.5,
                             fill=L["border"], outline="", tags=tag)
        # label line
        ty = wy0 + px(15)
        self.create_rectangle(wx0 + px(6), ty, wx0 + px(6) + ww * 0.32, ty + px(3),
                              fill=L["fg"], outline="", tags=tag)
        # entry + button
        ey = ty + px(8)
        eh = px(12)
        ex1 = wx0 + px(6) + ww * 0.50
        self.create_rectangle(wx0 + px(6), ey, ex1, ey + eh, fill=L["field"], outline=L["border"],
                              tags=tag)
        self.create_rectangle(wx0 + px(9), ey + eh / 2 - 1, wx0 + px(9) + ww * 0.18,
                              ey + eh / 2 + 1, fill=L["border"], outline="", tags=tag)
        bx0, bx1 = ex1 + px(5), wx1 - px(6)
        self.create_rectangle(bx0, ey, bx1, ey + eh, fill=L["accent"], outline=L["accent"],
                              tags=tag)
        mid = (bx0 + bx1) / 2
        self.create_rectangle(mid - (bx1 - bx0) * 0.25, ey + eh / 2 - 1,
                              mid + (bx1 - bx0) * 0.25, ey + eh / 2 + 1,
                              fill=L["accent_fg"], outline="", tags=tag)
        if L.get("raised"):
            self.create_line(bx0, ey + eh, bx0, ey, bx1, ey, fill="#ffffff", tags=tag)
            self.create_line(bx1, ey, bx1, ey + eh, bx0, ey + eh, fill="#404040", tags=tag)
        # checkbox + progress
        cy = ey + eh + px(6)
        s = px(7)
        self.create_rectangle(wx0 + px(6), cy, wx0 + px(6) + s, cy + s, fill=L["accent"],
                              outline=L["accent"], tags=tag)
        self.create_rectangle(wx0 + px(6) + s + px(4), cy + s / 2 - 1,
                              wx0 + px(6) + s + px(4) + ww * 0.16, cy + s / 2 + 1,
                              fill=L["fg"], outline="", tags=tag)
        px0 = wx0 + ww * 0.50
        self.create_rectangle(px0, cy + 1, wx1 - px(6), cy + s - 1, fill=L["trough"],
                              outline="", tags=tag)
        self.create_rectangle(px0, cy + 1, px0 + (wx1 - px(6) - px0) * 0.6, cy + s - 1,
                              fill=L["accent"], outline="", tags=tag)
        # name
        name = apptheme.THEMES[key]["name"] if apptheme else key
        self.create_text(x0 + px(8), y1 - px(11), anchor="w",
                         text=("✓ " if sel else "") + name,
                         fill=theme.ACCENT if sel else theme.TEXT,
                         font=font(9, "bold") if sel else font(9), tags=tag)
        if apptheme and apptheme.is_dark(key):
            self.create_text(x1 - px(7), y1 - px(11), anchor="e", text="dark", fill=theme.MUTED,
                             font=font(7), tags=tag)
        self.cards.append((x0, y0, x1, y1, key))

    # ---- interaction ----
    def _key_at(self, x, y):
        y = self.canvasy(y)
        for x0, y0, x1, y1, key in self.cards:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return key
        return None

    def _set_hot(self, key):
        if key == self.hot:
            return
        if self.hot is not None and self.hot != self.current:
            self.itemconfigure("card_%s_frame" % self.hot, outline=theme.BORDER)
        self.hot = key
        if key is not None and key != self.current:
            self.itemconfigure("card_%s_frame" % key, outline=theme.MUTED)
        if self.on_hover:
            self.on_hover(key)

    def _motion(self, e):
        self._set_hot(self._key_at(e.x, e.y))

    def _click(self, e):
        key = self._key_at(e.x, e.y)
        if key is not None and key != self.current:
            self.on_pick(key)


class SettingsPage(tk.Frame):
    def __init__(self, insp, app):
        super().__init__(insp, bg=theme.BG)
        self.insp = insp
        self.app = app
        self.editors = []          # (spec, editor)

        # header: drawn on one canvas (icon badge, title, explanation)
        head = tk.Canvas(self, bg=theme.PANEL, highlightthickness=0, height=px(64))
        head.pack(side="top", fill="x")
        s = px(40)
        head.create_rectangle(px(12), px(12), px(12) + s, px(12) + s, fill=theme.ACCENT_LIGHT,
                              outline="")
        head.create_text(px(12) + s / 2, px(12) + s / 2, text="⚙", fill=theme.ACCENT,
                         font=font(18))
        head.create_text(px(64), px(14), text="App settings", anchor="nw", fill=theme.TEXT,
                         font=font(12, "bold"))
        head.create_text(px(64), px(36), anchor="nw", fill=theme.MUTED, font=font(9),
                         width=px(250), text="Name, look and code options of your whole app.")
        bottom = head.bbox("all")[3] + px(12)
        head.configure(height=bottom)
        head.create_line(0, bottom - 1, 4000, bottom - 1, fill=theme.BORDER)

        outer, inner = theme.scrollable(self, bg=theme.BG)
        outer.pack(fill="both", expand=True)
        self.outer = outer
        c = tk.Frame(inner, bg=theme.BG)
        c.pack(fill="both", expand=True, padx=px(12), pady=(px(10), px(16)))
        self.content = c
        page = _insp._Page(insp, c)          # for section()/field() helpers
        self._page = page
        groups = []
        for p in model.SETTINGS_SPECS:
            if p["group"] not in groups:
                groups.append(p["group"])
        first = True
        for g in groups:
            page.section(c, g, first=first)
            first = False
            for p in model.SETTINGS_SPECS:
                if p["group"] == g:
                    self._add(c, p)
            if g == "App":
                self.stats = muted(c, "", size=9)
                self.stats.pack(fill="x")
                dialogs.link_button(c, "▣  Main window: title, size, menu bar…",
                                    self._main_window, size=9).pack(anchor="w", pady=(px(4), 0))

    # ---- editors ----
    def _add(self, c, p):
        key = p["key"]
        kind = p["kind"]
        insp = self.insp

        def setter(v, k=key, lab=p["label"]):
            self.set_setting(k, v, lab)
        if kind == "theme":
            f = self._page.field(c, p["label"], p.get("help", ""))
            self.gallery = ThemeGallery(f, self._pick_theme, self._hover_theme)
            self.gallery.pack(fill="x")
            self.editors.append((p, None))
            return
        if kind == "bool":
            box = tk.Frame(c, bg=theme.BG)
            box.pack(fill="x", pady=(0, px(7)))
            ed = _insp.BoolEd(insp, box, p, setter)
            if p.get("help"):
                muted(box, p["help"], wrap=272).pack(fill="x")
        elif key == "code_style":
            f = self._page.field(c, p["label"], p.get("help", ""))
            ed = RadioEd(insp, f, p, setter)
        elif key == "backend_dir":
            f = self._page.field(c, p["label"], p.get("help", ""))
            ed = FolderEd(insp, f, p, setter)
        else:
            f = self._page.field(c, p["label"], p.get("help", ""))
            if kind == "int":
                ed = _insp.NumberEd(insp, f, p, setter, self.outer.canvas)
            elif kind == "choice":
                ed = _insp.ChoiceEd(insp, f, p, setter, scroll=self.outer.canvas)
            else:
                ed = _insp.TextEd(insp, f, p, setter)
        self.editors.append((p, ed))

    def load(self):
        s = self.app.project.settings
        for p, ed in self.editors:
            if ed is not None:
                ed.load(s.get(p["key"], p["default"]), None)
        self.gallery.set_current(s.get("theme") or "native")
        project = self.app.project
        nw, nwid = len(project.windows), len(project.nodes_of_types())
        self.stats.configure(text="Your app has %d window%s and %d widget%s. Files: %s.py" % (
            nw, "" if nw == 1 else "s", nwid, "" if nwid == 1 else "s",
            project.module_basename()))

    def scroll_top(self):
        try:
            self.outer.canvas.yview_moveto(0)
        except tk.TclError:
            pass

    # ---- changes ----
    def set_setting(self, key, value, label):
        s = self.app.project.settings
        if s.get(key) == value:
            return
        s[key] = value
        self.app.commit("Changed “%s”." % label, merge_key=("setting", key), skip=self.insp)
        if key == "app_name":
            self.load()

    def _pick_theme(self, key):
        name = apptheme.THEMES[key]["name"] if apptheme else key
        self.app.project.settings["theme"] = key
        self.gallery.set_current(key)
        self.app.commit("Theme changed to %s." % name, skip=self.insp)

    def _hover_theme(self, key):
        if key is None or apptheme is None:
            return
        t = apptheme.THEMES[key]
        self.app.status("%s – %s Click to use it." % (t["name"], t.get("desc", "")))

    def _main_window(self):
        self.app.select(self.app.project.main_window["id"])


class RadioEd(_insp._Ed):
    """Choice shown as radio buttons (for a few long options)."""

    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.var = tk.StringVar()
        for value, label in spec["options"]:
            ttk.Radiobutton(parent, text=label, value=value, variable=self.var,
                            command=lambda: self._emit(self.var.get())).pack(anchor="w")

    def _load(self, value, node):
        if self.var.get() != value:
            self.var.set(value)


class FolderEd(_insp._Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x")
        self.var = tk.StringVar()
        ttk.Entry(row, textvariable=self.var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", style="Small.TButton", command=self._browse).pack(
            side="left", padx=(px(4), 0))
        self.var.trace_add("write", lambda *a: self._emit(self.var.get().strip()))

    def _browse(self):
        app = self.insp.app
        cur = dialogs._abs_dir(self.var.get().strip(), app) or dialogs.project_dir(app)
        d = filedialog.askdirectory(parent=self.insp.winfo_toplevel(),
                                    title="Folder with your Python code",
                                    initialdir=cur if cur and os.path.isdir(cur) else None)
        if d:
            self.var.set(dialogs.store_dir(os.path.normpath(d), app))

    def _load(self, value, node):
        if self.var.get() != (value or ""):
            self.var.set(value or "")
