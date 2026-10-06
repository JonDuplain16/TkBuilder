"""
Workplane – the design surface of TkBuilder.

The current window of the project is shown as a *fake window* (title bar,
optional menu bar, content area of exactly width x height pixels) drawn on a
scrollable canvas.  The widgets inside are REAL tkinter widgets created with
the same options the generated program uses (tkmap.widget_options /
initial_state, model.sticky_for / grid_weights), so what you see is what you
get.  They are made inert by replacing their bindtags with a private tag; all
mouse handling (select, drag & drop, context menu, inline editing) goes
through that tag.

Drag & drop ("snapping"):
  * over a normal widget the pointer position picks an edge (left/right 25 %,
    otherwise upper/lower half) and a green insertion line is shown;
  * over a container its *drop slots* (empty cells, an extra row below and an
    extra column to the right) are used – the nearest one lights up;
  * Tabs are dropped onto a Tabs widget (between its tab labels);
  * hovering a tab label for a moment switches to that tab;
  * Esc cancels.

Faithful preview notes:
  * The editor's theme (theme.apply) changes TkDefaultFont to Segoe UI 10 and
    uses the "clam" ttk theme.  A freshly generated app uses the platform
    defaults instead (Windows: Segoe UI 9 and the "vista" ttk theme).  So
    rendered widgets without a font get an explicit copy of the platform
    default font, and ttk widgets use private styles whose elements are
    cloned from the native ttk theme.
"""

import json
import sys
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import tkinter.font as tkfont

import model
from model import WIDGETS
import tkmap
import theme

DRAG_THRESHOLD = 5          # pixels before a press becomes a drag
TAB_HOVER_MS = 400          # hover time on a tab label to switch tab while dragging
EDGE = 8                    # px near a container's border = insert next to it
MARGIN = 36                 # space around the fake window on the canvas
TITLE_H = 31                # fake title bar height
MENU_H = 20                 # fake menu bar height
SNAP = 10                   # window resize snapping
TEXT_EDITABLE = ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame", "Tab")
FONT_TYPES = ("Label", "Button", "Entry", "Checkbutton", "Radiobutton", "Listbox",
              "Spinbox", "Scale", "LabelFrame", "Combobox")
HOVER_COLOR = "#93c5fd"
TARGET_COLOR = "#86efac"
SIDE_WORDS = {"above": "above", "below": "below", "left": "to the left of",
              "right": "to the right of"}


# --------------------------------------------------------------------------
# Faithful preview helpers
# --------------------------------------------------------------------------

def native_font_spec(root):
    """(family, size) of TkDefaultFont in a *fresh* Tk app on this platform.
    (theme.apply changes the editor's TkDefaultFont, so we can't just ask.)"""
    families = set(tkfont.families(root))
    ws = root.tk.call("tk", "windowingsystem")
    if ws == "win32":
        if "Segoe UI" in families:
            return ("Segoe UI", 9)
        return ("Tahoma", 8)
    if ws == "aqua":
        return (".AppleSystemUIFont", 13)
    for fam in ("DejaVu Sans", "Liberation Sans", "Helvetica"):
        if fam in families:
            return (fam, 10)
    return (tkfont.nametofont("TkDefaultFont").actual("family"), 10)


def native_ttk_theme(root):
    names = ttk.Style(root).theme_names()
    for name in ("vista", "aqua"):
        if name in names:
            return name
    return "default"


def install_preview_styles(root, font):
    """Create private ttk styles (prefix 'Tkbv.') that look like the ttk
    widgets of a fresh app (native theme), even though the editor runs the
    'clam' theme.  Returns the style prefix ('' if this failed)."""
    cached = getattr(root, "_tkb_preview_prefix", None)
    if cached is not None:
        return cached
    prefix = "Tkbv."
    tcl = root.tk
    style = ttk.Style(root)
    src = native_ttk_theme(root)
    if src == style.theme_use():
        root._tkb_preview_prefix = ""
        return ""

    def query(cmd):
        tcl.eval("ttk::style theme settings %s { set ::tkb_q [%s] }" % (src, cmd))
        return tcl.eval("set ::tkb_q")

    created = set()

    def relayout(spec):
        items = tcl.splitlist(spec)
        out, i = [], 0
        while i < len(items):
            name = str(items[i])
            i += 1
            new = prefix + name
            if new not in created:
                tcl.call("ttk::style", "element", "create", new, "from", src, name)
                created.add(new)
            out.append(new)
            while i < len(items) and str(items[i]).startswith("-"):
                opt, val = str(items[i]), items[i + 1]
                i += 2
                if opt == "-children":
                    val = relayout(val)
                out += [opt, val]
        return tcl.call("list", *out)

    try:
        base = tcl.splitlist(query("ttk::style configure ."))
        for cls in ("TNotebook", "TNotebook.Tab", "TCombobox", "Horizontal.TProgressbar",
                    "Vertical.TProgressbar", "TSeparator"):
            name = prefix + cls
            tcl.call("ttk::style", "layout", name, relayout(query("ttk::style layout %s" % cls)))
            if base:
                tcl.call("ttk::style", "configure", name, *base)
            cfg = tcl.splitlist(query("ttk::style configure %s" % cls))
            if cfg:
                tcl.call("ttk::style", "configure", name, *cfg)
            m = tcl.splitlist(query("ttk::style map %s" % cls))
            if m:
                tcl.call("ttk::style", "map", name, *m)
            tcl.call("ttk::style", "configure", name, "-font", font)
        # the editor theme pads notebook tabs; the native theme doesn't
        style.configure(prefix + "TNotebook.Tab", padding=0)
        style.map(prefix + "TNotebook.Tab", padding=[("selected", 0), ("!selected", 0)])
    except tk.TclError:
        prefix = ""
    root._tkb_preview_prefix = prefix
    return prefix


# --------------------------------------------------------------------------
# Small overlay helper: an outline made of 4 thin frames
# --------------------------------------------------------------------------

class _Box:
    def __init__(self, parent, color):
        self.parts = [tk.Frame(parent, bg=color, bd=0, highlightthickness=0) for _ in range(4)]
        self.target = None

    def show(self, w, out=2, t=2):
        top, bottom, left, right = self.parts
        try:
            top.place(in_=w, x=-out, y=-out, relwidth=1, width=2 * out, height=t)
            bottom.place(in_=w, x=-out, rely=1, y=out - t, relwidth=1, width=2 * out, height=t)
            left.place(in_=w, x=-out, y=-out, relheight=1, height=2 * out, width=t)
            right.place(in_=w, relx=1, x=out - t, y=-out, relheight=1, height=2 * out, width=t)
            for p in self.parts:
                p.lift()
            self.target = w
        except tk.TclError:
            self.hide()

    def hide(self):
        for p in self.parts:
            try:
                p.place_forget()
            except tk.TclError:
                pass
        self.target = None


# --------------------------------------------------------------------------
# Workplane
# --------------------------------------------------------------------------

class Workplane(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.current_window_id = app.project.main_window["id"]
        self._tag = "TkbDesign%d" % id(self)
        self._srctag = "TkbDragSrc%d" % id(self)
        fam, size = native_font_spec(self)
        self._family = fam
        self.preview_font = tkfont.Font(self, family=fam, size=size)
        self._ttk = install_preview_styles(self.winfo_toplevel(), (fam, size))

        self._tab_of = {}        # notebook id -> tab id shown
        self._node_of = {}       # tk path -> node id
        self._widget_of = {}     # node id -> outer tk widget
        self._inner_of = {}      # container id -> frame its children are gridded in
        self._slot_of = {}       # tk path -> slot dict
        self._slots = []         # temporary slots while dragging
        self._chrome = {}        # tk path -> "title" | "menu" (fake window decoration)
        self._images = []
        self._vars = []
        self._span_cache = {}
        self._outer = None
        self._win_item = None
        self._press = None
        self._drag = None
        self._editor = None
        self._resize = None
        self._winbar_sig = None
        self._win_xy = (MARGIN, MARGIN)
        self._win_size = (0, 0)
        self._menu_h = 0
        self._sel_id = None
        self._groups = {}
        self._overlays = set()

        self._build_chrome()
        self._bind_tags()
        self.refresh()

    # ================================================================ chrome
    def _build_chrome(self):
        self.winbar = tk.Frame(self, bg=theme.BG, highlightthickness=0)
        self.winbar.pack(side="top", fill="x")
        tk.Frame(self, bg=theme.BORDER, height=1).pack(side="top", fill="x")

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=theme.WORKPLANE_BG, highlightthickness=0, bd=0,
                                takefocus=1)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<Configure>", lambda e: self._layout_canvas())
        c.bind("<ButtonPress-1>", self._on_canvas_press)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        c.bind("<Escape>", self._on_escape)
        for key, d in (("Left", "left"), ("Right", "right"), ("Up", "up"), ("Down", "down")):
            c.bind("<%s>" % key, lambda e, d=d: (self._key_move(d), "break")[1])
        c.bind("<F2>", lambda e: (self.edit_text(self.app.selected_id), "break")[1])
        c.bind("<Return>", lambda e: (self.edit_text(self.app.selected_id), "break")[1])

        # resize grip + size caption (canvas items)
        c.create_rectangle(0, 0, 0, 0, fill="#cdd4de", outline="", tags=("shadow2",))
        c.create_rectangle(0, 0, 0, 0, fill="#c3cad5", outline="", tags=("shadow1",))
        c.create_rectangle(0, 0, 18, 18, fill=theme.WORKPLANE_BG, outline="", tags=("grip", "griparea"))
        for i in range(3):
            c.create_line(0, 0, 0, 0, fill="#7b8494", width=2, tags=("grip", "gripline%d" % i))
        c.create_text(0, 0, anchor="ne", fill=theme.MUTED, font=(theme.UI_FAMILY, 9),
                      tags=("sizetext",))
        c.tag_bind("grip", "<Enter>", lambda e: c.configure(cursor="size_nw_se"))
        c.tag_bind("grip", "<Leave>", lambda e: self._resize or c.configure(cursor=""))
        c.tag_bind("grip", "<ButtonPress-1>", self._grip_press)
        c.tag_bind("grip", "<B1-Motion>", self._grip_motion)
        c.tag_bind("grip", "<ButtonRelease-1>", self._grip_release)

    def _bind_tags(self):
        t = self._tag
        self.bind_class(t, "<ButtonPress-1>", self._on_press)
        self.bind_class(t, "<B1-Motion>", self._on_b1_motion)
        self.bind_class(t, "<ButtonRelease-1>", self._on_release)
        self.bind_class(t, "<Double-Button-1>", self._on_double)
        self.bind_class(t, "<ButtonPress-3>", self._on_right)
        self.bind_class(t, "<Motion>", self._on_hover)
        self.bind_class(t, "<Leave>", self._on_leave)
        self.bind_class(t, "<MouseWheel>", self._on_wheel)
        self.bind_class(t, "<Shift-MouseWheel>", self._on_shift_wheel)
        for seq in ("<ButtonPress-2>", "<ButtonRelease-2>", "<ButtonRelease-3>", "<Key>",
                    "<B2-Motion>", "<B3-Motion>", "<Enter>"):
            self.bind_class(t, seq, lambda e: "break")
        s = self._srctag
        self.bind_class(s, "<B1-Motion>", self._src_motion)
        self.bind_class(s, "<ButtonRelease-1>", self._src_release)
        self.bind_class(s, "<Escape>", self._on_escape)

    # ------------------------------------------------------------ window bar
    def _refresh_winbar(self):
        project = self.app.project
        sig = tuple((w["id"], w["props"].get("title", "")) for w in project.windows) + (
            self.current_window_id,)
        if sig == self._winbar_sig:
            return
        self._winbar_sig = sig
        for ch in self.winbar.winfo_children():
            ch.destroy()
        for i, w in enumerate(project.windows):
            title = w["props"].get("title", "") or "(no title)"
            text = ("Main window: %s" if i == 0 else w["id"] + ": %s") % title
            if len(text) > 34:
                text = text[:33] + "…"
            cur = w["id"] == self.current_window_id
            holder = tk.Frame(self.winbar, bg=theme.BG)
            holder.pack(side="left", padx=(6 if i == 0 else 2, 0), pady=(6, 0))
            lab = tk.Label(holder, text="▣  " + text, padx=12, pady=5, cursor="hand2",
                           bg=theme.PANEL if cur else theme.BG,
                           fg=theme.TEXT if cur else theme.MUTED,
                           font=(theme.UI_FAMILY, 9, "bold" if cur else "normal"))
            lab.pack(side="top")
            tk.Frame(holder, height=3, bg=theme.ACCENT if cur else theme.BG).pack(side="top", fill="x")

            def click(_e, wid=w["id"]):
                self.set_window(wid)
                self.app.select(wid)
            lab.bind("<Button-1>", click)
            if not cur:
                lab.bind("<Enter>", lambda e, l=lab: l.configure(bg=theme.ACCENT_LIGHT))
                lab.bind("<Leave>", lambda e, l=lab: l.configure(bg=theme.BG))
            theme.tooltip(lab, "Show and edit this window" if not cur else
                          "This is the window you are editing")
        add = tk.Label(self.winbar, text="＋ Window", padx=10, pady=5, cursor="hand2",
                       bg=theme.BG, fg=theme.ACCENT, font=(theme.UI_FAMILY, 9, "bold"))
        add.pack(side="left", padx=6, pady=(6, 3))
        add.bind("<Button-1>", lambda e: self.app.add_window())
        add.bind("<Enter>", lambda e: add.configure(bg=theme.ACCENT_LIGHT))
        add.bind("<Leave>", lambda e: add.configure(bg=theme.BG))
        theme.tooltip(add, "Add another window to your app (for example a Settings window)")

    # ================================================================ public
    def set_window(self, window_id):
        node = self.app.project.find(window_id)
        if node is None or node["type"] != "Window":
            return
        if window_id != self.current_window_id:
            self.current_window_id = window_id
            self.refresh()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)

    def current_tab_of(self, notebook_id):
        nb_node = self.app.project.find(notebook_id)
        if nb_node is None or not nb_node.get("children"):
            return None
        ids = [t["id"] for t in nb_node["children"]]
        nb = self._widget_of.get(notebook_id)
        if nb is not None:
            try:
                tid = self._node_of.get(str(nb.select()))
                if tid in ids:
                    return tid
            except tk.TclError:
                pass
        tid = self._tab_of.get(notebook_id)
        return tid if tid in ids else ids[0]

    def show_selection(self, node_id):
        self._sel_id = node_id
        if self._outer is None:
            return
        self._sel_box.hide()
        self._badge.place_forget()
        project = self.app.project
        node = project.find(node_id) if node_id else None
        if node is None or node_id not in self._widget_of:
            return
        self._reveal(node_id)
        w = self._widget_of[node_id]
        if node["type"] in ("Window", "Tab"):
            self._sel_box.show(w, out=0, t=2)
        else:
            self._sel_box.show(w, out=2, t=2)
            self._badge.configure(text=" %s " % node_id)
            try:
                if node["type"] == "LabelFrame":   # don't hide the group's title
                    self._badge.place(in_=w, relx=1, x=2, y=-2, anchor="se")
                else:
                    self._badge.place(in_=w, x=-2, y=-2, anchor="sw")
                self._badge.lift()
            except tk.TclError:
                pass
        if self._hover_box.target is w:
            self._hover_box.hide()

    def begin_drag_new(self, wtype, event):
        """Start dragging a new widget of type wtype (called by the palette
        when an item is pressed and dragged).  The palette widget that got the
        press keeps receiving the mouse events; we temporarily put our own
        bindtag in front of its tags to receive them."""
        if wtype not in WIDGETS:
            return
        self._cancel_drag(quiet=True)
        src = event.widget
        try:
            tags = src.bindtags()
            if self._srctag not in tags:
                src.bindtags((self._srctag,) + tuple(tags))
        except (tk.TclError, AttributeError):
            src = None
        self._start_drag("new", wtype, None, event.x_root, event.y_root, src=src)

    # ================================================================ refresh
    def refresh(self):
        project = self.app.project
        win = project.find(self.current_window_id)
        if win is None or win["type"] != "Window":
            win = project.main_window
            self.current_window_id = win["id"]
        # Live editing (typing in the properties panel) usually changes one
        # widget only: update just that widget instead of rebuilding them all
        # (creating many native windows is slow on some machines).
        if self._refresh_changed_only(win):
            return
        if self._drag:
            self._cancel_drag(quiet=True)
        self._close_editor(commit=False)
        xv, yv = self.canvas.xview()[0], self.canvas.yview()[0]

        old = self._outer
        self._node_of = {}
        self._widget_of = {}
        self._inner_of = {}
        self._slot_of = {}
        self._chrome = {}
        self._images = []
        self._vars = []
        self._groups = {}
        self._span_cache = {}

        outer = self._build_window(win)
        if self._win_item is None:
            self._win_item = self.canvas.create_window(MARGIN, MARGIN, window=outer, anchor="nw")
        else:
            self.canvas.itemconfigure(self._win_item, window=outer)
        if old is not None:
            old.destroy()
        self._outer = outer
        self._refresh_winbar()
        self._layout_canvas()
        self.canvas.xview_moveto(xv)
        self.canvas.yview_moveto(yv)
        self.show_selection(self.app.selected_id)
        self._rendered = {"win": win["id"], "struct": self._structure(win),
                          "sigs": {n["id"]: self._signature(n) for n in project.walk(win)}}

    # ---------------------------------------------------------- partial refresh
    _rendered = None
    # options a container may have; reset to the default when no longer set
    CONTAINER_OPTS = ("bg", "relief", "bd", "padx", "pady", "text", "font", "fg", "width", "height")

    @staticmethod
    def _structure(node):
        return (node["id"], node["type"],
                tuple(Workplane._structure(ch) for ch in node.get("children", [])))

    @staticmethod
    def _signature(node):
        return json.dumps([node.get("props"), node.get("layout")], sort_keys=True)

    def _refresh_changed_only(self, win):
        """Update only the widgets whose properties/layout changed since the
        last full refresh.  Returns False when a full rebuild is needed."""
        r = self._rendered
        if (r is None or self._outer is None or self._drag or self._editor or self._resize
                or r["win"] != win["id"] or self._structure(win) != r["struct"]):
            return False
        project = self.app.project
        changed = [n for n in project.walk(win) if self._signature(n) != r["sigs"].get(n["id"])]
        if len(changed) > 4:
            return False
        try:
            for n in changed:
                if not self._update_node(n, json.loads(r["sigs"][n["id"]])):
                    return False
        except (tk.TclError, KeyError, ValueError, TypeError):
            return False
        for n in changed:
            r["sigs"][n["id"]] = self._signature(n)
        self._span_cache = {}
        self._hover_box.hide()
        self._refresh_winbar()
        self._layout_canvas(keep_x=True)
        self.show_selection(self.app.selected_id)
        return True

    def _update_node(self, node, old):
        """Bring one rendered widget up to date. old = [props, layout] as
        rendered.  Returns False if that can't be done in place."""
        project = self.app.project
        t, nid = node["type"], node["id"]
        old_props, old_layout = old
        keys = {k for k in set(old_props) | set(node["props"])
                if old_props.get(k) != node["props"].get(k)}
        if t == "Window":
            if keys - {"title", "width", "height", "bg", "padding"}:
                return False
            if keys & {"bg", "padding"} and not node.get("children"):
                return False      # the "drop widgets here" hint would keep the old colour
            p = node["props"]
            content = self._widget_of[nid]
            self._title_label.configure(text=p.get("title", ""))
            W, H = int(p.get("width") or 420), int(p.get("height") or 320)
            content.configure(width=W, height=H)
            self._configure_container(content, node)
            self._win_size = (W + 2, H + TITLE_H + self._menu_h + 2)
            return True
        parent = project.parent_of(nid)
        if parent is None:
            return False
        if keys:
            if model.is_container(t):
                if not node.get("children") and t != "Notebook":
                    return False
                w = self._inner_of[nid]
                self._configure_container(w, node)
                if t == "Tab":
                    self._widget_of[parent["id"]].tab(w, text=node["props"].get("text", ""))
            else:
                if t == "Radiobutton" and keys & {"selected", "group"}:
                    return False
                frame = self._inner_of[parent["id"]]
                old_w = self._widget_of[nid]
                for path in [k for k, v in self._node_of.items() if v == nid]:
                    del self._node_of[path]
                try:
                    new_w = self._build_node(node, frame)
                except tk.TclError as e:
                    new_w = tk.Label(frame, text="[%s: %s]" % (nid, e), fg=theme.DANGER,
                                     font=self.preview_font)
                    self._reg(new_w, nid)
                    self._widget_of[nid] = new_w
                self._grid_node(new_w, node["layout"])
                old_w.destroy()
        if old_layout != node["layout"] and parent["type"] != "Notebook":
            self._grid_node(self._widget_of[nid], node["layout"])
            frame = self._inner_of[parent["id"]]
            cols, rows = frame.grid_size()
            for i in range(rows):
                frame.rowconfigure(i, weight=0)
            for i in range(cols):
                frame.columnconfigure(i, weight=0)
            wrows, wcols = model.grid_weights(parent)
            for i, wt in wrows.items():
                frame.rowconfigure(i, weight=wt)
            for i, wt in wcols.items():
                frame.columnconfigure(i, weight=wt)
        return True

    def _configure_container(self, w, node):
        opts = tkmap.resolve_font_family(tkmap.widget_options(node), self._family)
        if node["type"] in FONT_TYPES and "font" not in opts:
            opts["font"] = self.preview_font
        if node["type"] == "Window":
            opts.pop("width", None)
            opts.pop("height", None)
        supported = set(w.keys())
        for k in self.CONTAINER_OPTS:
            if k not in opts and k in supported and not (node["type"] == "Window"
                                                         and k in ("width", "height")):
                opts[k] = w.configure(k)[3]      # the option's default value
        w.configure(**opts)

    @staticmethod
    def _grid_node(w, lay):
        w.grid(row=lay.get("row", 0), column=lay.get("col", 0),
               rowspan=max(1, lay.get("rowspan", 1)), columnspan=max(1, lay.get("colspan", 1)),
               sticky=model.sticky_for(lay), padx=lay.get("padx", 0), pady=lay.get("pady", 0))

    def _build_window(self, win):
        p = win["props"]
        W, H = int(p.get("width") or 420), int(p.get("height") or 320)
        menus = [m for m in (p.get("menu") or []) if isinstance(m, dict)]
        outer = tk.Frame(self.canvas, bg="#8b919c", padx=1, pady=1, highlightthickness=0)
        wid = win["id"]
        self._reg(outer, wid)
        self._chrome[str(outer)] = "frame"

        # ---- title bar
        bar = tk.Frame(outer, bg="#ffffff", height=TITLE_H)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        self._reg(bar, wid)
        self._chrome[str(bar)] = "title"
        icon = tk.Canvas(bar, width=16, height=16, bg="#ffffff", highlightthickness=0)
        icon.create_polygon(3, 14, 6, 6, 13, 2, 11, 9, 4, 14, fill="#3b82f6", outline="#1d4ed8")
        icon.create_line(3, 15, 10, 6, fill="#1e3a8a")
        icon.pack(side="left", padx=(9, 6))
        self._reg(icon, wid)
        self._chrome[str(icon)] = "title"
        self._title_label = tk.Label(bar, text=p.get("title", ""), bg="#ffffff", fg="#000000",
                                     font=self.preview_font, anchor="w")
        self._title_label.pack(side="left", fill="x", expand=True)
        self._reg(self._title_label, wid)
        self._chrome[str(self._title_label)] = "title"
        fams = set(tkfont.families(self))
        if "Segoe Fluent Icons" in fams or "Segoe MDL2 Assets" in fams:
            gfont = ("Segoe Fluent Icons" if "Segoe Fluent Icons" in fams else "Segoe MDL2 Assets", 7)
            glyphs = ("", "", "")
        else:
            gfont, glyphs = (theme.UI_FAMILY, 9), ("─", "□", "✕")
        grey = () if p.get("resizable", True) else (glyphs[1],)
        for g in reversed(glyphs):
            b = tk.Label(bar, text=g, bg="#ffffff", fg="#b4b4b4" if g in grey else "#1f1f1f",
                         font=gfont, width=6)
            b.pack(side="right", fill="y")
            self._reg(b, wid)
            self._chrome[str(b)] = "title"

        # ---- menu bar
        menu_h = 0
        if menus:
            mb = tk.Frame(outer, bg="#ffffff", height=MENU_H)
            mb.pack(side="top", fill="x")
            mb.pack_propagate(False)
            self._reg(mb, wid)
            self._chrome[str(mb)] = "menu"
            for m in menus:
                lab = tk.Label(mb, text=m.get("label", "") or "?", bg="#ffffff", fg="#000000",
                               font=self.preview_font, padx=6)
                lab.pack(side="left", fill="y")
                self._reg(lab, wid)
                self._chrome[str(lab)] = "menu"
            menu_h = MENU_H

        # ---- content (the real Toplevel area)
        opts = tkmap.resolve_font_family(tkmap.widget_options(win), self._family)
        content = tk.Frame(outer, width=W, height=H, **opts)
        content.pack(side="top")
        content.pack_propagate(False)
        content.grid_propagate(False)
        self._reg(content, wid)
        self._widget_of[wid] = content
        self._inner_of[wid] = content
        self._build_children(win, content)

        self._menu_h = menu_h
        self._win_size = (W + 2, H + TITLE_H + menu_h + 2)

        # ---- overlays (created last so they are stacked on top)
        self._hover_box = _Box(outer, HOVER_COLOR)
        self._target_box = _Box(outer, TARGET_COLOR)
        self._sel_box = _Box(outer, theme.SELECT)
        for box, nid in ((self._hover_box, None), (self._target_box, None), (self._sel_box, None)):
            for part in box.parts:
                self._reg(part, None)
        self._badge = tk.Label(outer, bg=theme.SELECT, fg="white", font=(theme.UI_FAMILY, 8),
                               bd=0, padx=2, pady=0)
        self._reg(self._badge, None)
        self._indicator = tk.Frame(outer, bg=theme.DROP_OK, bd=0, highlightthickness=0)
        self._reg(self._indicator, None)
        self._overlays = {str(x) for box in (self._hover_box, self._target_box, self._sel_box)
                          for x in box.parts} | {str(self._badge), str(self._indicator)}
        return outer

    # ---------------------------------------------------------- build widgets
    def _reg(self, w, node_id, own=False):
        """Map a tk widget to a node and make it inert (our tag only)."""
        if node_id is not None:
            self._node_of[str(w)] = node_id
        tags = (self._tag, str(w.winfo_toplevel()), "all")
        if own:
            tags = (str(w),) + tags
        w.bindtags(tags)
        try:
            w.configure(takefocus=0)
        except tk.TclError:
            pass

    def _build_children(self, node, frame):
        kids = node.get("children", [])
        if node["type"] == "Notebook":
            return
        if not kids:
            self._make_hint(node, frame)
            return
        rows, cols = model.grid_weights(node)
        for r, w in rows.items():
            frame.rowconfigure(r, weight=w)
        for c, w in cols.items():
            frame.columnconfigure(c, weight=w)
        for ch in kids:
            try:
                w = self._build_node(ch, frame)
            except tk.TclError as e:  # a bad property value must not kill the editor
                w = tk.Label(frame, text="[%s: %s]" % (ch["id"], e), fg=theme.DANGER,
                             font=self.preview_font)
                self._reg(w, ch["id"])
                self._widget_of[ch["id"]] = w
            lay = ch.get("layout", {})
            w.grid(row=lay.get("row", 0), column=lay.get("col", 0),
                   rowspan=max(1, lay.get("rowspan", 1)), columnspan=max(1, lay.get("colspan", 1)),
                   sticky=model.sticky_for(lay), padx=lay.get("padx", 0), pady=lay.get("pady", 0))

    def _build_node(self, node, parent):
        t = node["type"]
        nid = node["id"]
        opts = tkmap.resolve_font_family(tkmap.widget_options(node), self._family)
        st = tkmap.initial_state(node)
        if t in FONT_TYPES and "font" not in opts:
            opts["font"] = self.preview_font
        parts = []
        outer = None

        if t in ("Frame", "LabelFrame"):
            w = (tk.Frame if t == "Frame" else tk.LabelFrame)(parent, **opts)
            self._inner_of[nid] = w
            self._build_children(node, w)
        elif t == "Notebook":
            w = ttk.Notebook(parent, style=self._ttk + "TNotebook", **opts)
            self._inner_of[nid] = w
            want = self._tab_of.get(nid)
            for tab in node.get("children", []):
                topts = tkmap.resolve_font_family(tkmap.widget_options(tab), self._family)
                page = tk.Frame(w, **topts)
                w.add(page, text=tab["props"].get("text", ""))
                self._reg(page, tab["id"])
                self._widget_of[tab["id"]] = page
                self._inner_of[tab["id"]] = page
                self._build_children(tab, page)
                if tab["id"] == want:
                    w.select(page)
        elif t in tkmap.SCROLLABLE and st.get("scrollbar"):
            outer = tk.Frame(parent)
            cls = tk.Text if t == "Text" else tk.Listbox
            w = cls(outer, **opts)
            sb = tk.Scrollbar(outer, orient="vertical", command=w.yview)
            w.configure(yscrollcommand=sb.set)
            # packed exactly like the generated code (so it shrinks the same way)
            w.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            parts = [sb]
        elif t == "Text":
            w = tk.Text(parent, **opts)
        elif t == "Listbox":
            w = tk.Listbox(parent, **opts)
        elif t == "Combobox":
            w = ttk.Combobox(parent, style=self._ttk + "TCombobox", **opts)
        elif t == "Progressbar":
            orient = opts.get("orient", "horizontal")
            style = self._ttk + ("Vertical." if orient == "vertical" else "Horizontal.") + "TProgressbar"
            w = ttk.Progressbar(parent, style=style, **opts)
        elif t == "Separator":
            w = ttk.Separator(parent, style=self._ttk + "TSeparator", **opts)
        elif t == "Checkbutton":
            var = tk.BooleanVar(self, value=bool(st.get("checked")))
            self._vars.append(var)
            w = tk.Checkbutton(parent, variable=var, **opts)
        elif t == "Radiobutton":
            group = node["props"].get("group") or ""
            var = self._groups.get(group)
            if var is None:
                var = self._groups[group] = tk.StringVar(self, value="")
                self._vars.append(var)
            # like the generated code: an empty group variable must not show
            # the "tri-state" look (only matters when nothing is chosen)
            w = tk.Radiobutton(parent, variable=var, tristatevalue="(nothing chosen)", **opts)
            if st.get("selected"):
                var.set(opts.get("value", ""))
        elif t in ("Label", "Button"):
            img = None
            if st.get("image"):
                try:
                    img = tk.PhotoImage(master=self, file=st["image"])
                    self._images.append(img)
                except (tk.TclError, OSError):
                    img = None
                    opts["text"] = "[picture]" + ((" " + opts["text"]) if opts.get("text") else "")
            if img is not None:
                opts["image"] = img
            w = (tk.Label if t == "Label" else tk.Button)(parent, **opts)
        else:
            module, clsname = tkmap.WIDGET_CLASS.get(t, ("tk", "Label"))
            cls = getattr(tk if module == "tk" else ttk, clsname)
            w = cls(parent, **opts)

        # ---- initial state (same order as the generated code)
        if "text" in st:
            try:
                if t == "Entry":
                    w.insert(0, st["text"])
                elif t == "Text":
                    w.insert("1.0", st["text"])
                elif t == "Combobox":
                    w.set(st["text"])
            except tk.TclError:
                pass
        if "items" in st:
            for it in st["items"]:
                w.insert("end", it)
        if "value" in st:
            try:
                if t == "Spinbox":
                    w.delete(0, "end")
                    w.insert(0, st["value"])
                elif t == "Scale":
                    w.set(st["value"])
            except tk.TclError:
                pass
        if st.get("disable_after_fill"):
            try:
                w.configure(state="disabled")
            except tk.TclError:
                pass
        for x in [w] + parts + ([outer] if outer is not None else []):
            self._reg(x, nid)
            try:
                x.configure(cursor="arrow")
            except tk.TclError:
                pass
        outer = outer if outer is not None else w
        self._widget_of[nid] = outer
        return outer

    def _make_hint(self, node, frame):
        """The always-visible 'Drop widgets here' box of an empty container."""
        big = node["type"] in ("Window", "Tab")
        try:
            bg = frame.cget("bg")
        except tk.TclError:
            bg = "SystemButtonFace"
        c = tk.Canvas(frame, bg=bg, highlightthickness=0, bd=0,
                      width=150 if not big else 60, height=46 if not big else 40)
        if big:
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            c.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        else:
            c.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        slot = {"w": c, "cid": node["id"], "row": 0, "col": 0, "hint": True, "big": big,
                "hover": False}
        self._reg(c, node["id"], own=True)
        self._slot_of[str(c)] = slot
        c.bind("<Configure>", lambda e, s=slot: self._draw_slot(s))

    def _draw_slot(self, slot):
        c = slot["w"]
        try:
            c.delete("all")
            w, h = c.winfo_width(), c.winfo_height()
        except tk.TclError:
            return
        if w < 4 or h < 4:
            w, h = int(c.cget("width")), int(c.cget("height"))
        hover = slot.get("hover")
        if hover:
            c.create_rectangle(1, 1, w - 2, h - 2, outline=theme.DROP_OK, width=2,
                               fill=theme.DROP_SLOT_HOVER)
        else:
            c.create_rectangle(1, 1, w - 2, h - 2, outline="#94a3b8" if slot.get("hint") else
                               theme.DROP_SLOT, dash=(4, 3), width=1,
                               fill=slot.get("fill", ""))
        fg = "#15803d" if hover else "#8a94a6"
        if slot.get("hint"):
            if slot.get("big") and h > 60:
                c.create_text(w / 2, h / 2 - 10, text="Drop widgets here", fill=fg,
                              font=(theme.UI_FAMILY, 12, "bold"))
                c.create_text(w / 2, h / 2 + 14, fill=fg, font=(theme.UI_FAMILY, 9),
                              text="Drag a widget from the list on the left onto this area")
            else:
                c.create_text(w / 2, h / 2, text="Drop widgets here", fill=fg,
                              font=(theme.UI_FAMILY, 9))
        elif hover and w > 70 and h > 18:
            c.create_text(w / 2, h / 2, text="Drop here", fill=fg, font=(theme.UI_FAMILY, 8, "bold"))
        elif w > 14 and h > 14:
            c.create_text(w / 2, h / 2, text="+", fill="#94a3b8" if not hover else fg,
                          font=(theme.UI_FAMILY, 10))

    # ---------------------------------------------------------- canvas layout
    def _layout_canvas(self, keep_x=False):
        c = self.canvas
        ow, oh = self._win_size
        cw = max(c.winfo_width(), 1)
        if keep_x:
            x0 = self._win_xy[0]
        else:
            x0 = max(MARGIN, (cw - ow) // 2)
        y0 = MARGIN
        self._win_xy = (x0, y0)
        if self._win_item is not None:
            c.coords(self._win_item, x0, y0)
        x1, y1 = x0 + ow, y0 + oh
        c.coords("shadow2", x0 + 2, y0 + 4, x1 + 4, y1 + 5)
        c.coords("shadow1", x0 + 1, y0 + 2, x1 + 2, y1 + 2)
        c.coords("griparea", x1 - 4, y1 - 4, x1 + 14, y1 + 14)
        for i, d in enumerate((4, 8, 12)):
            c.coords("gripline%d" % i, x1 + d, y1 + 2, x1 + 2, y1 + d)
        win = self.app.project.find(self.current_window_id)
        if win is not None:
            p = win["props"]
            w, h = (self._resize["w"], self._resize["h"]) if self._resize else (
                p.get("width"), p.get("height"))
            c.itemconfigure("sizetext", text="%s × %s pixels   (drag the corner to resize)" % (w, h))
        c.coords("sizetext", x1 - 2, y1 + 16)
        c.tag_raise("grip")
        c.configure(scrollregion=(0, 0, max(cw, x1 + MARGIN), y1 + MARGIN + 24))

    # ---------------------------------------------------------- resize grip
    def _grip_press(self, event):
        win = self.app.project.find(self.current_window_id)
        if win is None:
            return
        self._resize = {"x": event.x_root, "y": event.y_root,
                        "w0": int(win["props"]["width"]), "h0": int(win["props"]["height"]),
                        "w": int(win["props"]["width"]), "h": int(win["props"]["height"])}
        self.app.status("Drag to change the size of the window. Release to keep it.")

    def _grip_motion(self, event):
        r = self._resize
        if not r:
            return
        w = r["w0"] + event.x_root - r["x"]
        h = r["h0"] + event.y_root - r["y"]
        w = max(100, min(4000, int(round(w / SNAP)) * SNAP))
        h = max(80, min(4000, int(round(h / SNAP)) * SNAP))
        if (w, h) == (r["w"], r["h"]):
            return
        r["w"], r["h"] = w, h
        content = self._widget_of.get(self.current_window_id)
        if content is not None:
            content.configure(width=w, height=h)
        self._win_size = (w + 2, h + TITLE_H + self._menu_h + 2)
        self._layout_canvas(keep_x=True)

    def _grip_release(self, event):
        r, self._resize = self._resize, None
        self.canvas.configure(cursor="")
        if not r:
            return
        win = self.app.project.find(self.current_window_id)
        if win is None or (r["w"], r["h"]) == (r["w0"], r["h0"]):
            self.refresh()
            return
        win["props"]["width"], win["props"]["height"] = r["w"], r["h"]
        self.app.commit("Window size is now %d × %d pixels." % (r["w"], r["h"]))

    # ================================================================ events
    def _node_for_widget(self, w):
        return self._node_of.get(str(w))

    def _on_canvas_press(self, event):
        self._close_editor(commit=True)
        self.canvas.focus_set()
        if self.canvas.find_withtag("current") and "grip" in self.canvas.gettags("current"):
            return
        self.app.select(None)

    def _tab_at(self, nb, x, y):
        """Index of the notebook tab label at widget coords x, y (or None)."""
        try:
            s = nb.tk.call(str(nb), "index", "@%d,%d" % (x, y))
        except tk.TclError:
            return None
        if s == "" or s is None:
            return None
        try:
            return int(s)
        except (TypeError, ValueError):
            return None

    def _resolve_click(self, event):
        """Node id for a click; clicking a notebook tab label switches tab and
        returns the Tab's id."""
        w = event.widget
        nid = self._node_for_widget(w)
        if nid is None:
            return None
        if isinstance(w, ttk.Notebook):
            idx = self._tab_at(w, event.x, event.y)
            nb_node = self.app.project.find(nid)
            if idx is not None and nb_node and idx < len(nb_node.get("children", [])):
                tab = nb_node["children"][idx]
                self._switch_tab(nid, tab["id"])
                return tab["id"]
        return nid

    def _switch_tab(self, nb_id, tab_id):
        nb = self._widget_of.get(nb_id)
        page = self._widget_of.get(tab_id)
        self._tab_of[nb_id] = tab_id
        if nb is not None and page is not None:
            try:
                nb.select(page)
            except tk.TclError:
                pass

    def _reveal(self, node_id):
        """Switch every notebook on the way so node_id is visible."""
        project = self.app.project
        chain = project.ancestors(node_id) + [project.find(node_id)]
        for n in chain:
            if n is not None and n["type"] == "Tab":
                nb = project.parent_of(n["id"])
                if nb is not None:
                    self._switch_tab(nb["id"], n["id"])

    def _on_press(self, event):
        self._hover_box.hide()
        if self._drag:
            return "break"
        nid = self._resolve_click(event)
        if self._editor is not None:
            self._close_editor(commit=True)
        self.canvas.focus_set()
        if nid is None:
            return "break"
        self._press = {"id": nid, "x": event.x_root, "y": event.y_root,
                       "chrome": str(event.widget) in self._chrome}
        if self.app.project.find(nid) is not None:
            self.app.select(nid, source=self)
            self.show_selection(nid)
            if str(event.widget) in self._chrome and self._chrome[str(event.widget)] == "menu":
                self.app.status("This is the menu bar. Change it in the properties panel "
                                "(Menu bar → Edit menu bar…).")
        return "break"

    def _on_b1_motion(self, event):
        if self._drag:
            self._drag_motion(event.x_root, event.y_root)
            return "break"
        p = self._press
        if p and not p["chrome"]:
            if abs(event.x_root - p["x"]) > DRAG_THRESHOLD or abs(event.y_root - p["y"]) > DRAG_THRESHOLD:
                node = self.app.project.find(p["id"])
                self._press = None
                if node is not None and node["type"] != "Window":
                    self._start_drag("move", node["type"], node["id"], event.x_root, event.y_root)
        return "break"

    def _on_release(self, event):
        if self._drag:
            self._drop()
        self._press = None
        return "break"

    def _on_double(self, event):
        nid = self._resolve_click(event)
        if nid is None:
            return "break"
        self._press = None
        if str(event.widget) in self._chrome and self._chrome[str(event.widget)] == "title":
            self.app.select(nid, source=self)
            self.show_selection(nid)
            self._edit_title(nid)
            return "break"
        node = self.app.project.find(nid)
        if node is not None and node["type"] in TEXT_EDITABLE:
            self.app.select(nid, source=self)
            self.show_selection(nid)
            self.edit_text(nid)
        return "break"

    def _on_right(self, event):
        if self._drag:
            return "break"
        nid = self._resolve_click(event)
        self._close_editor(commit=True)
        if nid is None or self.app.project.find(nid) is None:
            return "break"
        self.app.select(nid, source=self)
        self.show_selection(nid)
        self._context_menu(nid, event.x_root, event.y_root)
        return "break"

    def _on_hover(self, event):
        if self._drag or self._outer is None:
            return "break"
        nid = self._node_for_widget(event.widget)
        node = self.app.project.find(nid) if nid else None
        if (node is None or node["type"] in ("Window", "Tab") or nid == self._sel_id
                or str(event.widget) in self._chrome):
            self._hover_box.hide()
            return "break"
        w = self._widget_of.get(nid)
        if w is not None and self._hover_box.target is not w:
            self._hover_box.show(w, out=1, t=1)
        return "break"

    def _on_leave(self, event):
        if not self._drag and self._hover_box.target is not None:
            # hide only when the pointer really left the design
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
            if under is None or str(under) not in self._node_of:
                self._hover_box.hide()
        return "break"

    def _on_wheel(self, event):
        if self._editor is None:
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def _on_shift_wheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def _on_escape(self, event=None):
        if self._drag:
            self._cancel_drag()
            return "break"
        self.app.select(None)
        return "break"

    # ================================================================ drag & drop
    def _start_drag(self, kind, wtype, node_id, x, y, src=None):
        self._close_editor(commit=True)
        self._hover_box.hide()
        self._sel_box.hide()     # so the outline never catches the pointer
        self._drag = {"kind": kind, "wtype": wtype, "id": node_id, "src": src,
                      "target": None, "hover_tab": None, "after": None, "x": x, "y": y,
                      "valid": None}
        self._make_ghost()
        self._create_slots()
        self._prev_focus = self.focus_get()
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass
        what = ("a new " + WIDGETS[wtype]["name"]) if kind == "new" else node_id
        self.app.status("Dragging %s – drop it where you want it (Esc to cancel)." % what)
        self._drag_motion(x, y)

    def _src_motion(self, event):
        if self._drag:
            self._drag_motion(event.x_root, event.y_root)
        return "break"

    def _src_release(self, event):
        if self._drag:
            self._drop()
        self._remove_srctag(event.widget)
        return "break"

    def _remove_srctag(self, w):
        try:
            tags = tuple(t for t in w.bindtags() if t != self._srctag)
            w.bindtags(tags)
        except (tk.TclError, AttributeError):
            pass

    def _make_ghost(self):
        d = self._drag
        spec = WIDGETS[d["wtype"]]
        g = tk.Toplevel(self)
        g.withdraw()
        g.overrideredirect(True)
        try:
            g.attributes("-topmost", True)
            g.attributes("-alpha", 0.93)
        except tk.TclError:
            pass
        f = tk.Frame(g, bg=theme.ACCENT, padx=8, pady=4)
        f.pack()
        label = spec["name"] if d["kind"] == "new" else "%s (%s)" % (d["id"], spec["name"])
        ic = tk.Label(f, text=spec["icon"], bg=theme.ACCENT, fg="white",
                      font=(theme.UI_FAMILY, 11, "bold"))
        ic.pack(side="left")
        tx = tk.Label(f, text=label, bg=theme.ACCENT, fg="white", font=(theme.UI_FAMILY, 9, "bold"))
        tx.pack(side="left", padx=(6, 0))
        d["ghost"], d["ghost_parts"] = g, (f, ic, tx)

    def _ghost_state(self, ok):
        d = self._drag
        if d.get("valid") == ok:
            return
        d["valid"] = ok
        color = theme.ACCENT if ok else "#64748b"
        for part in d["ghost_parts"]:
            part.configure(bg=color)

    def _create_slots(self):
        """Show drop slots in every container of the current window."""
        d = self._drag
        project = self.app.project
        moving = d["id"]
        wtype = d["wtype"]
        for cid, frame in list(self._inner_of.items()):
            node = project.find(cid)
            if node is None or node["type"] == "Notebook" or not node.get("children"):
                continue
            if moving and (cid == moving or project.is_descendant(cid, moving)):
                continue
            if not project.can_contain(node, wtype):
                continue
            rows, cols = project.grid_size(node)
            occ = project.occupancy(node)
            cells = [(r, c) for r in range(rows) for c in range(cols) if (r, c) not in occ]
            cells += [(rows, c) for c in range(cols)]
            cells += [(r, cols) for r in range(rows)]
            try:
                bg = frame.cget("bg")
            except tk.TclError:
                bg = "white"
            for r, c in cells:
                extra_row, extra_col = r == rows, c == cols
                cv = tk.Canvas(frame, bg=bg, highlightthickness=0, bd=0,
                               width=26 if extra_col else 40, height=24 if extra_row else 20)
                cv.grid(row=r, column=c, sticky="nsew", padx=3, pady=3)
                slot = {"w": cv, "cid": cid, "row": r, "col": c, "hint": False, "hover": False}
                self._reg(cv, cid, own=True)
                self._slot_of[str(cv)] = slot
                self._slots.append(slot)
                cv.bind("<Configure>", lambda e, s=slot: self._draw_slot(s))
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

    def _destroy_slots(self):
        for s in self._slots:
            self._slot_of.pop(str(s["w"]), None)
            self._node_of.pop(str(s["w"]), None)
            try:
                s["w"].destroy()
            except tk.TclError:
                pass
        self._slots = []
        for s in self._slot_of.values():
            if s.get("hover"):
                s["hover"] = False
                self._draw_slot(s)

    def _drag_motion(self, x, y):
        d = self._drag
        d["x"], d["y"] = x, y
        g = d.get("ghost")
        if g is not None:
            try:
                g.geometry("+%d+%d" % (x + 16, y + 14))
                if g.state() == "withdrawn":
                    g.deiconify()
            except tk.TclError:
                pass
        self._autoscroll(x, y)
        target, reason = self._find_target(x, y)
        self._show_target(target, reason)

    def _autoscroll(self, x, y):
        c = self.canvas
        try:
            cx, cy = c.winfo_rootx(), c.winfo_rooty()
            cw, ch = c.winfo_width(), c.winfo_height()
        except tk.TclError:
            return
        if not (cx <= x <= cx + cw):
            return
        if cy <= y < cy + 18:
            c.yview_scroll(-1, "units")
        elif cy + ch - 18 < y <= cy + ch:
            c.yview_scroll(1, "units")

    # ---------------------------------------------------------- targeting
    def _find_target(self, x, y):
        """Return (target, reason).  target is a dict:
             {"kind": "cell"|"side"|"tab", "cid", "row", "col", "mode",
              "ref": node id (side/tab), "slot": slot (cell)}
        or None with a reason string for the status bar."""
        d = self._drag
        project = self.app.project
        wtype, moving = d["wtype"], d["id"]
        try:
            w = self.winfo_containing(x, y)
        except (tk.TclError, KeyError):
            w = None
        if w is None:
            return None, ""
        path = str(w)
        if path in self._overlays:
            # pointer is on our own indicator/outline: keep the current target
            return d.get("target"), ""
        if path not in self._node_of and path not in self._slot_of:
            return None, ""     # outside the design

        if wtype == "Tab":
            return self._find_tab_target(w, x, y)

        slot = self._slot_of.get(path)
        if slot is not None:
            return self._slot_target(slot)

        nid = self._node_of.get(path)
        node = project.find(nid)
        if node is None or path in self._chrome:
            return None, ""
        if moving and (nid == moving or project.is_descendant(nid, moving)):
            return None, "That is where it already is."

        # notebook tab strip: hover to switch tabs
        if isinstance(w, ttk.Notebook):
            idx = self._tab_at(w, x - w.winfo_rootx(), y - w.winfo_rooty())
            if idx is not None:
                self._hover_tab(nid, idx)
                return None, "Keep the pointer on a tab to open it, then drop onto the page."
        self._hover_tab(None, None)

        t = node["type"]
        ow = self._widget_of.get(nid)
        if ow is None:
            return None, ""
        if t in ("Window", "Tab", "Frame", "LabelFrame"):
            # near the border of a panel/group box: insert next to it
            if t in ("Frame", "LabelFrame"):
                side = self._edge_side(ow, x, y)
                if side:
                    tgt = self._side_target(node, side)
                    if tgt[0] is not None:
                        return tgt
            return self._nearest_slot(nid, x, y)
        # a normal widget (or a notebook's border): left/right 25 %, else above/below
        try:
            rx = (x - ow.winfo_rootx()) / max(1, ow.winfo_width())
            ry = (y - ow.winfo_rooty()) / max(1, ow.winfo_height())
        except tk.TclError:
            return None, ""
        if rx < 0.25:
            side = "left"
        elif rx > 0.75:
            side = "right"
        else:
            side = "above" if ry < 0.5 else "below"
        return self._side_target(node, side)

    def _edge_side(self, w, x, y):
        try:
            lx, ly = x - w.winfo_rootx(), y - w.winfo_rooty()
            ww, wh = w.winfo_width(), w.winfo_height()
        except tk.TclError:
            return None
        if ww < 4 * EDGE or wh < 4 * EDGE:
            return None
        dists = {"left": lx, "right": ww - lx, "above": ly, "below": wh - ly}
        side = min(dists, key=dists.get)
        return side if dists[side] < EDGE else None

    def _side_target(self, ref, side):
        project = self.app.project
        d = self._drag
        parent = project.parent_of(ref["id"])
        if parent is None or parent["type"] == "Notebook":
            return None, ""
        ok, reason = self._can_drop_into(parent)
        if not ok:
            return None, reason
        lay = ref["layout"]
        return {"kind": "side", "cid": parent["id"], "row": lay["row"], "col": lay["col"],
                "mode": side, "ref": ref["id"]}, ""

    def _can_drop_into(self, container):
        project = self.app.project
        d = self._drag
        if d["id"] and (container["id"] == d["id"] or project.is_descendant(container["id"], d["id"])):
            return False, "A widget can't go inside itself."
        if not project.can_contain(container, d["wtype"]):
            if d["wtype"] == "Tab":
                return False, "A Tab can only be dropped onto a Tabs widget."
            if container["type"] == "Notebook":
                return False, "Drop it onto the page of a tab, not onto the tab labels."
            return False, "A %s can't go there." % WIDGETS[d["wtype"]]["name"]
        return True, ""

    def _slot_target(self, slot):
        project = self.app.project
        cont = project.find(slot["cid"])
        if cont is None:
            return None, ""
        ok, reason = self._can_drop_into(cont)
        if not ok:
            return None, reason
        return {"kind": "cell", "cid": slot["cid"], "row": slot["row"], "col": slot["col"],
                "mode": "cell", "slot": slot}, ""

    def _nearest_slot(self, cid, x, y):
        best, bd = None, None
        for s in self._slot_of.values():
            if s["cid"] != cid:
                continue
            w = s["w"]
            try:
                if not w.winfo_ismapped():
                    continue
                x0, y0 = w.winfo_rootx(), w.winfo_rooty()
                x1, y1 = x0 + w.winfo_width(), y0 + w.winfo_height()
            except tk.TclError:
                continue
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            dist = dx * dx + dy * dy
            if bd is None or dist < bd:
                best, bd = s, dist
        if best is None:
            cont = self.app.project.find(cid)
            if cont is not None:
                ok, reason = self._can_drop_into(cont)
                if not ok:
                    return None, reason
            return None, ""
        return self._slot_target(best)

    def _find_tab_target(self, w, x, y):
        """Targets for dragging a Tab: between the labels of a Tabs widget."""
        project = self.app.project
        d = self._drag
        nid = self._node_of.get(str(w))
        node = project.find(nid)
        if node is None or str(w) in self._chrome:
            return None, "A Tab can only be dropped onto a Tabs widget."
        nb = node if node["type"] == "Notebook" else None
        if nb is None:
            for a in reversed(project.ancestors(nid)):
                if a["type"] == "Notebook":
                    nb = a
                    break
        if nb is None:
            return None, "A Tab can only be dropped onto a Tabs widget."
        if d["id"]:
            if project.is_descendant(nb["id"], d["id"]):
                return None, "A widget can't go inside itself."
            old = project.parent_of(d["id"])
            if old is not None and old["id"] != nb["id"] and len(old["children"]) <= 1:
                return None, "Tabs need at least one tab, so this one can't be moved away."
        nbw = self._widget_of.get(nb["id"])
        spans, strip_h = self._tab_spans(nbw)
        kids = nb["children"]
        ref, side = (kids[-1]["id"] if kids else None), "after"
        try:
            lx, ly = x - nbw.winfo_rootx(), y - nbw.winfo_rooty()
        except tk.TclError:
            lx = ly = -1
        if spans and 0 <= ly <= strip_h + 6:
            for i, (a, b) in enumerate(spans):
                if lx < (a + b) / 2:
                    ref, side = kids[i]["id"], "before"
                    break
        if ref is not None and d["id"] == ref:
            return None, "That is where it already is."
        if d["id"] and ref is not None:
            # dropping right next to itself = no change
            ids = [k["id"] for k in kids]
            if d["id"] in ids:
                i, j = ids.index(d["id"]), ids.index(ref)
                if (side == "before" and j == i + 1) or (side == "after" and j == i - 1):
                    return None, "That is where it already is."
        return {"kind": "tab", "cid": nb["id"], "row": 0, "col": 0, "mode": "append",
                "ref": ref, "side": side}, ""

    def _tab_spans(self, nb):
        """[(x0, x1) per tab label], strip height – measured by probing."""
        key = str(nb)
        if key in self._span_cache:
            return self._span_cache[key]
        spans, strip_h = [], 0
        try:
            width = nb.winfo_width()
            for yy in (4, 8, 12, 16):
                cur, start = None, 0
                spans = []
                for xx in range(0, width + 2, 2):
                    i = self._tab_at(nb, xx, yy)
                    if i != cur:
                        if cur is not None:
                            spans.append((start, xx))
                        cur, start = i, xx
                if cur is not None:
                    spans.append((start, width))
                if spans:
                    break
            if spans:
                mid = (spans[0][0] + spans[0][1]) // 2
                for yy in range(0, 60):
                    if self._tab_at(nb, mid, yy) is not None:
                        strip_h = yy + 1
        except tk.TclError:
            spans = []
        self._span_cache[key] = (spans, strip_h)
        return spans, strip_h

    def _hover_tab(self, nb_id, idx):
        d = self._drag
        cur = d.get("hover_tab")
        if cur == (nb_id, idx):
            return
        if d.get("after"):
            self.after_cancel(d["after"])
            d["after"] = None
        d["hover_tab"] = (nb_id, idx) if nb_id else None
        if nb_id is None:
            return
        nb_node = self.app.project.find(nb_id)
        if nb_node is None or idx >= len(nb_node["children"]):
            return
        tab_id = nb_node["children"][idx]["id"]
        if self.current_tab_of(nb_id) == tab_id:
            return

        def switch():
            if not self._drag:
                return
            self._drag["after"] = None
            self._switch_tab(nb_id, tab_id)
            try:
                self.update_idletasks()
            except tk.TclError:
                pass
            self._drag_motion(self._drag["x"], self._drag["y"])
        d["after"] = self.after(TAB_HOVER_MS, switch)

    # ---------------------------------------------------------- feedback
    def _show_target(self, target, reason):
        d = self._drag
        old = d.get("target")
        d["target"] = target
        # slot highlight
        old_slot = old.get("slot") if old else None
        new_slot = target.get("slot") if target else None
        if old_slot is not new_slot:
            if old_slot is not None:
                old_slot["hover"] = False
                self._draw_slot(old_slot)
            if new_slot is not None:
                new_slot["hover"] = True
                self._draw_slot(new_slot)
        # insertion line
        self._indicator.place_forget()
        if target and target["kind"] == "side":
            ref = self._widget_of.get(target["ref"])
            if ref is not None:
                m, T = target["mode"], 4
                try:
                    if m == "left":
                        self._indicator.place(in_=ref, x=-T - 1, y=-2, width=T, relheight=1, height=4)
                    elif m == "right":
                        self._indicator.place(in_=ref, relx=1, x=1, y=-2, width=T, relheight=1, height=4)
                    elif m == "above":
                        self._indicator.place(in_=ref, x=-2, y=-T - 1, relwidth=1, width=4, height=T)
                    else:
                        self._indicator.place(in_=ref, x=-2, rely=1, y=1, relwidth=1, width=4, height=T)
                    self._indicator.lift()
                except tk.TclError:
                    pass
        elif target and target["kind"] == "tab":
            nbw = self._widget_of.get(target["cid"])
            spans, strip_h = self._tab_spans(nbw) if nbw is not None else ([], 0)
            ids = [k["id"] for k in self.app.project.find(target["cid"])["children"]]
            if spans and target["ref"] in ids:
                i = ids.index(target["ref"])
                if i < len(spans):
                    xx = spans[i][0] if target["side"] == "before" else spans[i][1]
                    try:
                        self._indicator.place(in_=nbw, x=max(0, xx - 2), y=0, width=4,
                                              height=max(strip_h, 16))
                        self._indicator.lift()
                    except tk.TclError:
                        pass
        # target container outline (not for the whole window)
        cid = target["cid"] if target else None
        cw = self._widget_of.get(cid) if cid and cid != self.current_window_id else None
        if cw is not None:
            if self._target_box.target is not cw:
                self._target_box.show(cw, out=1, t=2)
        else:
            self._target_box.hide()
        self._ghost_state(target is not None)
        # status text
        if target is None:
            if reason:
                self.app.status(reason)
            else:
                self.app.status("Move over your window to choose a place. Release outside to cancel.")
        else:
            self.app.status(self._describe(target))

    def _describe(self, target):
        project = self.app.project
        d = self._drag
        what = ("the new " + WIDGETS[d["wtype"]]["name"]) if d["kind"] == "new" else d["id"]
        cont = project.find(target["cid"])
        where = "the window" if cont["type"] == "Window" else cont["id"]
        if target["kind"] == "side":
            return "Release to put %s %s %s." % (what, SIDE_WORDS[target["mode"]], target["ref"])
        if target["kind"] == "tab":
            return "Release to put %s %s %s." % (what, target["side"], target["ref"] or "the tabs")
        if target["slot"].get("hint"):
            return "Release to put %s into %s." % (what, where)
        return "Release to put %s into %s (row %d, column %d)." % (
            what, where, target["row"] + 1, target["col"] + 1)

    def _clear_drag_visuals(self):
        d = self._drag
        if d is None:
            return
        if d.get("after"):
            try:
                self.after_cancel(d["after"])
            except tk.TclError:
                pass
        g = d.get("ghost")
        if g is not None:
            try:
                g.destroy()
            except tk.TclError:
                pass
        try:
            self._indicator.place_forget()
            self._target_box.hide()
        except tk.TclError:
            pass
        self._destroy_slots()
        if d.get("src") is not None:
            self._remove_srctag(d["src"])

    def _cancel_drag(self, quiet=False):
        if not self._drag:
            return
        self._clear_drag_visuals()
        self._drag = None
        self._press = None
        self.show_selection(self.app.selected_id)
        if not quiet:
            self.app.status("Cancelled – nothing was changed.")

    def _drop(self):
        d = self._drag
        target = d.get("target")
        self._clear_drag_visuals()
        self._drag = None
        self._press = None
        if target is None:
            self.app.status("Dropped outside the window – nothing was changed.")
            self.show_selection(self.app.selected_id)
            return
        project = self.app.project
        snap = project.snapshot()
        try:
            if d["kind"] == "new":
                node = project.new_node(d["wtype"])
                project.place(node, target["cid"], target["row"], target["col"], target["mode"])
                node_id = node["id"]
                msg = "Added %s." % project.display_name(node)
            else:
                node = project.move(d["id"], target["cid"], target["row"], target["col"],
                                    target["mode"])
                if node is None:
                    raise ValueError("That can't be moved.")
                node_id = node["id"]
                msg = "Moved %s." % project.display_name(node)
            if target["kind"] == "tab":
                kids = project.find(target["cid"])["children"]
                me = next(k for k in kids if k["id"] == node_id)
                kids.remove(me)
                ids = [k["id"] for k in kids]
                i = ids.index(target["ref"]) if target["ref"] in ids else len(kids) - 1
                kids.insert(i if target["side"] == "before" else i + 1, me)
                self._tab_of[target["cid"]] = node_id
        except (ValueError, KeyError, StopIteration) as e:
            project.restore(snap)
            self.refresh()
            self.app.status(str(e) or "That didn't work.")
            return
        self.app.commit(msg)
        self.app.select(node_id)
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass

    # ================================================================ editing
    def edit_text(self, node_id):
        """Small in-place editor for the text of a Label/Button/... or Tab."""
        project = self.app.project
        node = project.find(node_id) if node_id else None
        if node is None:
            return
        if node["type"] == "Window":
            self._edit_title(node_id)
            return
        if node["type"] not in TEXT_EDITABLE:
            self.app.status("This widget has no text to edit here – use the properties panel.")
            return
        self._close_editor(commit=True)
        self._reveal(node_id)
        w = self._widget_of.get(node_id)
        if w is None:
            return
        text = node["props"].get("text", "") or ""
        try:
            self.update_idletasks()
            if node["type"] == "Tab":
                nb_id = project.parent_of(node_id)["id"]
                nbw = self._widget_of[nb_id]
                spans, strip_h = self._tab_spans(nbw)
                idx = [k["id"] for k in project.parent_of(node_id)["children"]].index(node_id)
                x0, x1 = spans[idx] if idx < len(spans) else (0, 80)
                place = dict(in_=nbw, x=x0, y=0, width=max(x1 - x0, 90), height=max(strip_h, 20))
            elif node["type"] == "LabelFrame":
                place = dict(in_=w, x=4, y=-2, width=max(120, min(w.winfo_width() - 8, 260)), height=22)
            else:
                place = dict(in_=w, x=0, y=0, relwidth=1, relheight=1)
                if w.winfo_width() < 120:
                    # small widget: make the editor wider, but keep it inside the window
                    o = self._outer
                    room = (o.winfo_rootx() + o.winfo_width() - 4) - (w.winfo_rootx() + 120)
                    place = dict(in_=w, x=min(0, room), y=0, width=120, relheight=1)
        except (tk.TclError, KeyError, ValueError, AttributeError):
            return
        self._open_editor(node_id, "text", text, place, multiline=("\n" in text))

    def _edit_title(self, window_id):
        win = self.app.project.find(window_id)
        if win is None or window_id != self.current_window_id:
            return
        self._close_editor(commit=True)
        place = dict(in_=self._title_label, x=0, y=3, relwidth=1, width=-8, relheight=1, height=-6)
        self._open_editor(window_id, "title", win["props"].get("title", ""), place)

    def _open_editor(self, node_id, key, value, place, multiline=False):
        font = self.preview_font
        if multiline:
            e = tk.Text(self._outer, font=font, wrap="word", bd=1, relief="solid",
                        highlightthickness=2, highlightcolor=theme.ACCENT,
                        highlightbackground=theme.ACCENT, undo=True)
            e.insert("1.0", value)
            e.tag_add("sel", "1.0", "end-1c")
        else:
            e = tk.Entry(self._outer, font=font, bd=1, relief="solid", highlightthickness=2,
                         highlightcolor=theme.ACCENT, highlightbackground=theme.ACCENT)
            e.insert(0, value)
            e.select_range(0, "end")
            e.icursor("end")
        e.place(**place)
        e.lift()
        e.focus_set()
        self._editor = {"w": e, "id": node_id, "key": key, "old": value, "multi": multiline}

        def commit(_e=None):
            self._close_editor(commit=True)
            return "break"

        def cancel(_e=None):
            self._close_editor(commit=False)
            return "break"
        e.bind("<Return>", commit)
        e.bind("<KP_Enter>", commit)
        if multiline:
            e.bind("<Shift-Return>", lambda ev: None)
        e.bind("<Escape>", cancel)
        e.bind("<FocusOut>", commit)
        self.app.status("Type the new text. Enter = done, Esc = cancel."
                        + (" Shift+Enter = new line." if multiline else ""))

    def _close_editor(self, commit=True):
        ed, self._editor = self._editor, None
        if not ed:
            return
        w = ed["w"]
        try:
            value = w.get("1.0", "end-1c") if ed["multi"] else w.get()
        except tk.TclError:
            value = ed["old"]
        try:
            w.destroy()
        except tk.TclError:
            pass
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass
        if not commit or value == ed["old"]:
            return
        node = self.app.project.find(ed["id"])
        if node is None:
            return
        node["props"][ed["key"]] = value
        what = "title" if ed["key"] == "title" else "text"
        self.app.commit("Changed the %s of %s." % (what, ed["id"]))
        self.app.select(ed["id"])

    # ---------------------------------------------------------- context menu
    def _context_menu(self, nid, x, y):
        project = self.app.project
        node = project.find(nid)
        t = node["type"]
        parent = project.parent_of(nid)
        m = tk.Menu(self, tearoff=0)
        if t in TEXT_EDITABLE or t == "Window":
            m.add_command(label="Edit title…" if t in ("Window", "LabelFrame", "Tab") else "Edit text…",
                          command=lambda: self.edit_text(nid))
        if WIDGETS[t]["events"] or (t == "Window"):
            m.add_command(label="Actions…", command=lambda: self._show_actions(nid))
        m.add_command(label="Rename…", command=lambda: self._rename(nid))
        if t != "Window":
            m.add_separator()
            m.add_command(label="Duplicate", accelerator="Ctrl+D",
                          command=lambda: self._do_selected(nid, self.app.duplicate_selected))
            m.add_command(label="Delete", accelerator="Del",
                          command=lambda: self._do_selected(nid, self.app.delete_selected))
        if t in ("Notebook", "Tab"):
            m.add_separator()
            m.add_command(label="Add tab", command=lambda: self._add_tab(nid))
        if t != "Window" and parent is not None:
            m.add_separator()
            if parent["type"] == "Notebook":
                m.add_command(label="Move tab left", command=lambda: self.move_node(nid, "left"))
                m.add_command(label="Move tab right", command=lambda: self.move_node(nid, "right"))
            else:
                for label, dname in (("Move up", "up"), ("Move down", "down"),
                                     ("Move left", "left"), ("Move right", "right")):
                    m.add_command(label=label, command=lambda dn=dname: self.move_node(nid, dn))
            m.add_separator()
            m.add_command(label="Select parent (%s)" % parent["id"],
                          command=lambda: self.app.select(parent["id"]))
        if t == "Window" and nid != project.main_window["id"]:
            m.add_separator()
            m.add_command(label="Delete this window",
                          command=lambda: self._do_selected(nid, self.app.delete_selected))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _do_selected(self, nid, fn):
        self.app.select(nid)
        fn()

    def _show_actions(self, nid):
        insp = getattr(self.app, "inspector", None)
        if insp is not None and hasattr(insp, "show_actions"):
            self.app.select(nid)
            insp.show_actions(nid)
        else:
            self.app.select(nid)
            self.app.status("Use the Actions part of the properties panel to choose what happens.")

    def _rename(self, nid):
        project = self.app.project
        name = simpledialog.askstring(
            "Rename", "New name for %s\n(letters, numbers and _ only):" % nid,
            initialvalue=nid, parent=self.winfo_toplevel())
        if not name or name.strip() == nid:
            return
        name = name.strip()
        try:
            project.rename(nid, name)
        except ValueError as e:
            messagebox.showinfo("Rename", str(e), parent=self.winfo_toplevel())
            return
        from inspector import _after_rename     # updates selection, tabs, window
        _after_rename(self.app, nid, name)

    def _add_tab(self, nid):
        project = self.app.project
        node = project.find(nid)
        nb = node if node["type"] == "Notebook" else project.parent_of(nid)
        tab = project.add_tab(nb["id"])
        self._tab_of[nb["id"]] = tab["id"]
        self.app.commit("Added a tab.")
        self.app.select(tab["id"])

    # ---------------------------------------------------------- moving by keys
    def _key_move(self, direction):
        sel = self.app.selected_id
        if sel:
            self.move_node(sel, direction)

    def move_node(self, nid, direction):
        """Move a widget one cell (swapping with the neighbour)."""
        project = self.app.project
        node = project.find(nid)
        if node is None or node["type"] == "Window":
            return
        parent = project.parent_of(nid)
        if parent is None:
            return
        if parent["type"] == "Notebook":
            delta = -1 if direction in ("left", "up") else 1
            before = [k["id"] for k in parent["children"]]
            project.move_tab(nid, delta)
            if [k["id"] for k in parent["children"]] == before:
                self.app.status("This tab is already at the end.")
                return
            self.app.commit("Moved tab %s." % nid)
            self.app.select(nid)
            return
        lay = node["layout"]
        r, c, rs, cs = lay["row"], lay["col"], lay["rowspan"], lay["colspan"]
        cell = {"up": (r - 1, c), "down": (r + rs, c), "left": (r, c - 1), "right": (r, c + cs)}[direction]
        rows, cols = project.grid_size(parent)
        occ = project.occupancy(parent, exclude_id=nid)
        mode = {"up": "above", "down": "below", "left": "left", "right": "right"}[direction]
        snap = project.snapshot()
        try:
            if 0 <= cell[0] < rows and 0 <= cell[1] < cols:
                other_id = occ.get(cell)
                if other_id is None:
                    lay["row"], lay["col"] = cell
                    project.compact(parent)
                else:
                    other = project.find(other_id)
                    ol = other["layout"]
                    if rs == cs == ol["rowspan"] == ol["colspan"] == 1:
                        ol["row"], ol["col"], lay["row"], lay["col"] = r, c, ol["row"], ol["col"]
                    else:
                        project.move(nid, parent["id"], ol["row"], ol["col"], mode)
            else:
                # at the edge: only useful if it shares its row/column with others
                if direction in ("up", "down"):
                    alone = not any(ch["id"] != nid and ch["layout"]["row"] <= r < ch["layout"]["row"] + ch["layout"]["rowspan"]
                                    for ch in parent["children"])
                else:
                    alone = not any(ch["id"] != nid and ch["layout"]["col"] <= c < ch["layout"]["col"] + ch["layout"]["colspan"]
                                    for ch in parent["children"])
                if alone:
                    self.app.status("It is already at the edge.")
                    return
                project.move(nid, parent["id"], r, c, mode)
        except ValueError as e:
            project.restore(snap)
            self.app.status(str(e))
            return
        self.app.commit("Moved %s %s." % (nid, direction))
        self.app.select(nid)
