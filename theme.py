"""Shared look & feel for the TkBuilder editor (not for generated apps)."""

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

# Palette of the editor itself
BG = "#f3f5f8"           # panel background
PANEL = "#ffffff"        # cards / lists
BORDER = "#d5dbe3"
TEXT = "#1f2937"
MUTED = "#6b7280"
ACCENT = "#2563eb"       # selection, primary buttons
ACCENT_LIGHT = "#dbeafe"
RUN = "#16a34a"
DANGER = "#dc2626"
WORKPLANE_BG = "#e6eaf0"  # area behind the designed window
DROP_OK = "#22c55e"      # drop indicator
DROP_SLOT = "#cbd5e1"    # empty drop slot outline
DROP_SLOT_HOVER = "#bbf7d0"
SELECT = "#2563eb"       # selection outline in the workplane

UI_FAMILY = "Segoe UI"


def apply(root):
    """Configure ttk styles and default fonts for the editor."""
    families = set(tkfont.families(root))
    global UI_FAMILY
    if UI_FAMILY not in families:
        UI_FAMILY = tkfont.nametofont("TkDefaultFont").actual("family")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        try:
            tkfont.nametofont(name).configure(family=UI_FAMILY, size=10)
        except tk.TclError:
            pass

    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    root.configure(bg=BG)
    style.configure(".", background=BG, foreground=TEXT, font=(UI_FAMILY, 10),
                    bordercolor=BORDER, focuscolor=ACCENT)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=PANEL)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TLabel", background=PANEL)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Heading.TLabel", font=(UI_FAMILY, 11, "bold"))
    style.configure("Section.TLabel", font=(UI_FAMILY, 9, "bold"), foreground=MUTED)
    style.configure("Status.TLabel", background="#e5e9f0", foreground=TEXT, padding=(4, 3))
    style.configure("Toolbar.TFrame", background="#ffffff")
    style.configure("Toolbar.TButton", padding=(8, 4), background="#ffffff", borderwidth=0)
    style.map("Toolbar.TButton", background=[("active", ACCENT_LIGHT), ("disabled", "#ffffff")],
              foreground=[("disabled", "#a0a7b4")])
    style.configure("Accent.TButton", padding=(10, 4), background=ACCENT, foreground="white",
                    font=(UI_FAMILY, 10, "bold"), borderwidth=0)
    style.map("Accent.TButton", background=[("active", "#1d4ed8")])
    style.configure("Run.TButton", padding=(10, 4), background=RUN, foreground="white",
                    font=(UI_FAMILY, 10, "bold"), borderwidth=0)
    style.map("Run.TButton", background=[("active", "#15803d")])
    style.configure("Danger.TButton", foreground=DANGER)
    style.configure("Small.TButton", padding=(4, 1))
    style.configure("Treeview", rowheight=24, background=PANEL, fieldbackground=PANEL)
    style.map("Treeview", background=[("selected", ACCENT)])
    style.configure("TNotebook", background=BG)
    style.configure("TNotebook.Tab", padding=(10, 4))
    style.configure("Palette.TButton", anchor="w", padding=(8, 5), background=PANEL)
    style.map("Palette.TButton", background=[("active", ACCENT_LIGHT)])
    style.configure("TEntry", fieldbackground="white")
    style.configure("TCheckbutton", background=BG)
    style.configure("Card.TCheckbutton", background=PANEL)
    style.configure("TLabelframe", background=BG)
    style.configure("TLabelframe.Label", background=BG, font=(UI_FAMILY, 9, "bold"), foreground=MUTED)
    return style


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
        self.after_id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self.after_id:
            self.widget.after_cancel(self.after_id)
            self.after_id = None

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        except tk.TclError:
            return
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self.tip, text=self.text, bg="#111827", fg="white", padx=8, pady=4,
                 font=(UI_FAMILY, 9), justify="left", wraplength=320).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            self.tip.destroy()
            self.tip = None


def tooltip(widget, text, delay=600):
    """Attach a hover tooltip to a widget. Returns the tooltip object; set
    `.text` on it later to change the message."""
    return _Tooltip(widget, text, delay)


def scrollable(parent, bg=BG):
    """Create a vertically scrollable area. Returns (outer_frame, inner_frame).
    Pack/grid `outer_frame`; put content into `inner_frame`."""
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
