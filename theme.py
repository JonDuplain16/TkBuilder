"""Look & feel of the TkBuilder editor itself (not of the apps it generates).

Two palettes, light and dark, share the same constant names.  Call
`set_mode("light"|"dark")` before building any UI, then `apply(root)`.
Panels must read `theme.X` while they build (never copy the colours into
module-level constants or default arguments), so switching the mode and
rebuilding the panels picks up the new colours.

High-DPI: the editor runs DPI-aware on Windows (crisp text).  Fonts are in
points and scale by themselves; for distances in pixels use `px(n)`, which
multiplies by `SCALE` (1.0 at 96 DPI, 2.5 on a 250 % screen).
"""

import sys
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

LIGHT = dict(
    BG="#f3f5f8",             # panel background
    PANEL="#ffffff",          # cards / lists
    FIELD="#ffffff",          # text boxes
    BORDER="#d5dbe3",
    TEXT="#1f2937",
    MUTED="#6b7280",
    DISABLED="#a0a7b4",
    ACCENT="#2563eb",         # selection, primary buttons
    ACCENT_HOVER="#1d4ed8",
    ACCENT_FG="#ffffff",      # text on ACCENT
    ACCENT_LIGHT="#dbeafe",   # hover / soft selection background
    HOVER="#eef2f7",          # neutral hover background
    RUN="#16a34a",
    RUN_HOVER="#15803d",
    DANGER="#dc2626",
    WARNING="#d97706",
    TOOLBAR="#ffffff",
    STATUS_BG="#e9edf2",
    TOOLTIP_BG="#111827",
    TOOLTIP_FG="#ffffff",
    WORKPLANE_BG="#e6eaf0",   # area behind the designed window
    DROP_OK="#22c55e",        # drop indicator
    DROP_SLOT="#cbd5e1",      # empty drop slot outline
    DROP_SLOT_HOVER="#bbf7d0",
    SELECT="#2563eb",         # selection outline in the workplane
)

DARK = dict(
    BG="#1f2125",
    PANEL="#2a2d33",
    FIELD="#24272c",
    BORDER="#3d424b",
    TEXT="#e5e7eb",
    MUTED="#9aa1ad",
    DISABLED="#5d636e",
    ACCENT="#3b82f6",
    ACCENT_HOVER="#2563eb",
    ACCENT_FG="#ffffff",
    ACCENT_LIGHT="#1f3654",
    HOVER="#33373e",
    RUN="#16a34a",
    RUN_HOVER="#15803d",
    DANGER="#f87171",
    WARNING="#fbbf24",
    TOOLBAR="#272a2f",
    STATUS_BG="#181a1d",
    TOOLTIP_BG="#3a3f47",
    TOOLTIP_FG="#f3f4f6",
    WORKPLANE_BG="#17191c",
    DROP_OK="#22c55e",
    DROP_SLOT="#64748b",
    DROP_SLOT_HOVER="#bbf7d0",
    SELECT="#3b82f6",
)

MODE = "light"
UI_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas" if sys.platform == "win32" else "Courier"
ICON_FAMILY = ""          # "Segoe Fluent Icons" / "Segoe MDL2 Assets" when available
SCALE = 1.0               # screen pixels per 1/96 inch (set by apply)

# module globals with the current palette (filled by set_mode)
BG = PANEL = FIELD = BORDER = TEXT = MUTED = DISABLED = ACCENT = ACCENT_HOVER = None
ACCENT_FG = ACCENT_LIGHT = HOVER = RUN = RUN_HOVER = DANGER = WARNING = TOOLBAR = None
STATUS_BG = TOOLTIP_BG = TOOLTIP_FG = WORKPLANE_BG = DROP_OK = DROP_SLOT = None
DROP_SLOT_HOVER = SELECT = None


def set_mode(mode):
    """Switch the module colours to the "light" or "dark" palette.
    Call before (re)building the UI, then call apply(root)."""
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    globals().update(DARK if MODE == "dark" else LIGHT)


set_mode("light")


def is_dark():
    return MODE == "dark"


def px(n):
    """Scale a distance in (96 DPI) pixels for the current screen."""
    return int(round(n * SCALE))


# ---------------------------------------------------------------- DPI
_dpi_done = False


def enable_dpi_awareness():
    """Make the process DPI-aware on Windows so text is sharp on high-
    resolution screens.  Must run before the first Tk window is created.
    Safe to call more than once and on other platforms."""
    global _dpi_done
    if _dpi_done or sys.platform != "win32":
        return
    _dpi_done = True
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001 - older Windows: fall back quietly
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


def style_titlebar(win):
    """Dark title bar on Windows 10/11 when the editor is dark (no-op elsewhere)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        value = ctypes.c_int(1 if MODE == "dark" else 0)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new, old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - purely cosmetic
        pass


def _on_map(event):
    w = event.widget
    if isinstance(w, (tk.Tk, tk.Toplevel)) and not w.wm_overrideredirect():
        if getattr(w, "_tkb_titlebar", None) != MODE:
            w._tkb_titlebar = MODE
            style_titlebar(w)


# ---------------------------------------------------------------- styles
def apply(root):
    """Configure ttk styles, fonts and classic-widget defaults for the editor
    in the current mode.  Can be called again after set_mode()."""
    global UI_FAMILY, ICON_FAMILY, SCALE
    try:
        SCALE = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except tk.TclError:
        SCALE = 1.0
    families = set(tkfont.families(root))
    if "Segoe UI" in families:
        UI_FAMILY = "Segoe UI"
    elif UI_FAMILY not in families:
        UI_FAMILY = tkfont.nametofont("TkDefaultFont").actual("family")
    ICON_FAMILY = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets")
                        if f in families), "")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FAMILY, size=10)
        except tk.TclError:
            pass
    linespace = tkfont.Font(root, family=UI_FAMILY, size=10).metrics("linespace")

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(bg=BG)

    # classic tk widgets (Text, Listbox, Menu, Entry, ...) via the option db
    root.option_clear()
    for pattern, value in (
            ("*Background", BG), ("*Foreground", TEXT),
            ("*Text.background", FIELD), ("*Text.foreground", TEXT),
            ("*Text.insertBackground", TEXT), ("*Text.selectBackground", ACCENT),
            ("*Text.selectForeground", ACCENT_FG),
            ("*Listbox.background", FIELD), ("*Listbox.foreground", TEXT),
            ("*Listbox.selectBackground", ACCENT), ("*Listbox.selectForeground", ACCENT_FG),
            ("*Entry.background", FIELD), ("*Entry.foreground", TEXT),
            ("*Entry.insertBackground", TEXT),
            ("*Spinbox.background", FIELD), ("*Spinbox.foreground", TEXT),
            ("*Canvas.highlightThickness", 0),
            ("*TCombobox*Listbox.background", FIELD), ("*TCombobox*Listbox.foreground", TEXT),
            ("*TCombobox*Listbox.selectBackground", ACCENT),
            ("*TCombobox*Listbox.selectForeground", ACCENT_FG),
            ("*Menu.background", PANEL), ("*Menu.foreground", TEXT),
            ("*Menu.activeBackground", ACCENT), ("*Menu.activeForeground", ACCENT_FG),
            ("*Menu.relief", "flat"), ("*Menu.borderWidth", 1)):
        root.option_add(pattern, value)

    p = px
    edges = dict(bordercolor=BORDER, lightcolor=PANEL, darkcolor=PANEL)
    style.configure(".", background=BG, foreground=TEXT, font=(UI_FAMILY, 10),
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG, troughcolor=BG,
                    fieldbackground=FIELD, selectbackground=ACCENT, selectforeground=ACCENT_FG,
                    insertcolor=TEXT, focuscolor=ACCENT, arrowcolor=TEXT)
    style.map(".", foreground=[("disabled", DISABLED)])
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=PANEL)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Heading.TLabel", font=(UI_FAMILY, 11, "bold"))
    style.configure("Section.TLabel", font=(UI_FAMILY, 9, "bold"), foreground=MUTED)
    style.configure("Status.TLabel", background=STATUS_BG, foreground=MUTED,
                    padding=(p(8), p(3)), font=(UI_FAMILY, 9))
    style.configure("Toolbar.TFrame", background=TOOLBAR)
    style.configure("Toolbar.TLabel", background=TOOLBAR, foreground=MUTED)
    style.configure("TButton", background=PANEL, foreground=TEXT, padding=(p(10), p(4)),
                    **edges)
    style.map("TButton", background=[("disabled", BG), ("pressed", BORDER), ("active", HOVER)],
              lightcolor=[("active", HOVER)], darkcolor=[("active", HOVER)],
              bordercolor=[("focus", ACCENT)])
    style.configure("Toolbar.TButton", padding=(p(8), p(5)), background=TOOLBAR,
                    bordercolor=TOOLBAR, lightcolor=TOOLBAR, darkcolor=TOOLBAR, borderwidth=0,
                    focuscolor=TOOLBAR)
    style.map("Toolbar.TButton",
              background=[("disabled", TOOLBAR), ("pressed", ACCENT_LIGHT), ("active", HOVER)],
              lightcolor=[("active", HOVER)], darkcolor=[("active", HOVER)],
              bordercolor=[("active", HOVER)],
              foreground=[("disabled", DISABLED)])
    style.configure("Toolbar.TButton", width=0)
    style.configure("Icon.Toolbar.TButton", font=(ICON_FAMILY or UI_FAMILY, 12),
                    padding=(p(8), p(5)), width=0)
    for name, color, hover in (("Accent.TButton", ACCENT, ACCENT_HOVER),
                               ("Run.TButton", RUN, RUN_HOVER)):
        style.configure(name, padding=(p(12), p(5)), background=color, foreground="#ffffff",
                        font=(UI_FAMILY, 10, "bold"), borderwidth=0, bordercolor=color, width=0,
                        lightcolor=color, darkcolor=color, focuscolor=color)
        style.map(name, background=[("pressed", hover), ("active", hover)],
                  lightcolor=[("active", hover)], darkcolor=[("active", hover)],
                  bordercolor=[("active", hover)], foreground=[("disabled", "#e5e7eb")])
    style.configure("Danger.TButton", foreground=DANGER)
    style.configure("Small.TButton", padding=(p(4), p(1)))
    style.configure("Link.TButton", background=BG, foreground=ACCENT, borderwidth=0,
                    bordercolor=BG, lightcolor=BG, darkcolor=BG, padding=(p(2), p(1)))
    style.map("Link.TButton", background=[("active", BG)], foreground=[("active", ACCENT_HOVER)])

    for name in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(name, fieldbackground=FIELD, foreground=TEXT, background=PANEL,
                        insertcolor=TEXT, arrowcolor=TEXT, padding=p(3), arrowsize=p(12),
                        **edges)
        style.map(name, bordercolor=[("focus", ACCENT)], lightcolor=[("focus", FIELD)],
                  fieldbackground=[("readonly", FIELD), ("disabled", BG)],
                  foreground=[("disabled", DISABLED)], arrowcolor=[("disabled", DISABLED)],
                  background=[("active", HOVER)],
                  selectbackground=[("!focus", ACCENT)], selectforeground=[("!focus", ACCENT_FG)])
    for name, bg in (("TCheckbutton", BG), ("TRadiobutton", BG),
                     ("Card.TCheckbutton", PANEL), ("Card.TRadiobutton", PANEL)):
        style.configure(name, background=bg, foreground=TEXT, indicatorbackground=FIELD,
                        indicatorforeground=ACCENT_FG, upperbordercolor=BORDER,
                        lowerbordercolor=BORDER, focuscolor=bg, indicatorsize=p(13),
                        indicatormargin=(p(1), p(1), p(6), p(1)))
        style.map(name, background=[("active", bg)],
                  indicatorbackground=[("disabled", BG), ("selected", ACCENT),
                                       ("pressed", BORDER)],
                  upperbordercolor=[("selected", ACCENT)], lowerbordercolor=[("selected", ACCENT)])
    for name in ("TRadiobutton", "Card.TRadiobutton"):
        # clam draws the radio dot almost as big as the circle: make the dot the accent
        style.configure(name, indicatorforeground=ACCENT)
        style.map(name, indicatorbackground=[("disabled", BG), ("selected", FIELD),
                                             ("pressed", BORDER)])

    style.configure("Treeview", rowheight=max(p(24), linespace + p(8)), background=PANEL,
                    fieldbackground=PANEL, foreground=TEXT, bordercolor=BORDER,
                    lightcolor=PANEL, darkcolor=PANEL, indicatorsize=p(12))
    style.map("Treeview", background=[("selected", ACCENT)],
              foreground=[("selected", ACCENT_FG)])
    style.configure("Treeview.Heading", background=BG, foreground=MUTED, relief="flat",
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG,
                    font=(UI_FAMILY, 9, "bold"))
    style.map("Treeview.Heading", background=[("active", HOVER)])
    style.configure("TNotebook", background=BG, bordercolor=BORDER, lightcolor=BG,
                    darkcolor=BG, tabmargins=(p(2), p(4), p(2), 0))
    style.configure("TNotebook.Tab", padding=(p(12), p(4)), background=BG, foreground=MUTED,
                    bordercolor=BORDER, lightcolor=BG, darkcolor=BG)
    style.map("TNotebook.Tab", background=[("selected", PANEL), ("active", HOVER)],
              foreground=[("selected", TEXT)], lightcolor=[("selected", PANEL)],
              expand=[("selected", (1, 1, 1, 0))])
    style.configure("Palette.TButton", anchor="w", padding=(p(8), p(5)), background=PANEL,
                    bordercolor=PANEL, lightcolor=PANEL, darkcolor=PANEL)
    style.map("Palette.TButton", background=[("active", ACCENT_LIGHT)],
              lightcolor=[("active", ACCENT_LIGHT)], darkcolor=[("active", ACCENT_LIGHT)])
    style.configure("TLabelframe", background=BG, bordercolor=BORDER, lightcolor=BG,
                    darkcolor=BG)
    style.configure("TLabelframe.Label", background=BG, font=(UI_FAMILY, 9, "bold"),
                    foreground=MUTED)
    style.configure("TMenubutton", background=PANEL, foreground=TEXT, arrowcolor=TEXT,
                    padding=(p(10), p(4)), **edges)
    style.map("TMenubutton", background=[("active", HOVER)])
    for o in ("Vertical", "Horizontal"):
        style.configure(o + ".TScrollbar", background=BG, troughcolor=BG, bordercolor=BG,
                        lightcolor=BG, darkcolor=BG, arrowcolor=MUTED, gripcount=0,
                        arrowsize=p(12))
        style.map(o + ".TScrollbar", background=[("active", HOVER), ("pressed", BORDER)])
        style.configure(o + ".TScale", background=PANEL, troughcolor=BORDER, bordercolor=BORDER,
                        lightcolor=PANEL, darkcolor=PANEL)
        style.configure(o + ".TProgressbar", background=ACCENT, troughcolor=BORDER,
                        bordercolor=BORDER, lightcolor=ACCENT, darkcolor=ACCENT)
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BORDER)
    style.configure("Sash", sashthickness=p(5), gripcount=0, background=BORDER,
                    lightcolor=BORDER, bordercolor=BORDER)

    # title bars follow the mode (every Toplevel, including other panels' dialogs)
    if not getattr(root, "_tkb_map_bound", False):
        root._tkb_map_bound = True
        root.bind_class("Toplevel", "<Map>", _on_map, add="+")
        root.bind("<Map>", _on_map, add="+")
    if root.winfo_ismapped() and getattr(root, "_tkb_titlebar", None) != MODE:
        root._tkb_titlebar = MODE
        style_titlebar(root)
    return style


# ---------------------------------------------------------------- helpers
class _Tooltip:
    def __init__(self, widget, text, delay=600):
        self.widget, self.text, self.delay = widget, text, delay
        self.tip = None
        self.after_id = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        try:
            self.after_id = self.widget.after(self.delay, self._show)
        except tk.TclError:
            self.after_id = None

    def _cancel(self):
        if self.after_id:
            try:
                self.widget.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None

    def _show(self):
        self.after_id = None
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + px(12)
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + px(4)
            self.tip = tk.Toplevel(self.widget)
        except tk.TclError:
            return
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=self.text, bg=TOOLTIP_BG, fg=TOOLTIP_FG, padx=px(8), pady=px(4),
                 font=(UI_FAMILY, 9), justify="left", wraplength=px(320)).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except tk.TclError:
                pass
            self.tip = None


def tooltip(widget, text, delay=600):
    """Attach a hover tooltip to a widget. Returns the tooltip object; set
    `.text` on it later to change the message."""
    return _Tooltip(widget, text, delay)


def scrollable(parent, bg=None):
    """Create a vertically scrollable area. Returns (outer_frame, inner_frame).
    Pack/grid `outer_frame`; put content into `inner_frame`.  `bg` defaults
    to the current BG."""
    bg = bg or BG
    outer = ttk.Frame(parent)
    canvas = tk.Canvas(outer, bg=bg, highlightthickness=0, borderwidth=0)
    vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=bg)
    win = canvas.create_window(0, 0, window=inner, anchor="nw")
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

    def _wheel(event):
        try:
            if canvas.winfo_height() < inner.winfo_reqheight():
                canvas.yview_scroll(int(-event.delta / 120), "units")
        except tk.TclError:      # this area was rebuilt while the pointer was over it
            canvas.unbind_all("<MouseWheel>")

    def _bind(_e):
        canvas.bind_all("<MouseWheel>", _wheel)

    def _unbind(_e):
        canvas.unbind_all("<MouseWheel>")

    outer.bind("<Enter>", _bind)
    outer.bind("<Leave>", _unbind)
    outer.canvas = canvas
    return outer, inner
