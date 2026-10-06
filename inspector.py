"""
Inspector panel: shows and edits the selected widget (or the App settings).

Speed matters: creating a native window costs several milliseconds, so this
panel never rebuilds itself when the selection changes.  Instead it keeps

  * one header (icon, kind, name, buttons) that is updated in place,
  * one notebook with the tabs Properties / Layout / Actions / Code,
  * one cached *page* per widget type for Properties and Actions (built the
    first time a widget of that type is selected), one Layout page and one
    Code page shared by all types,

and only *loads values* into the cached editors.  Only the visible tab is
loaded; the others are loaded when the user switches to them.

Edits are applied live with app.commit(..., merge_key=..., skip=self), so the
design surface updates while typing and this panel is not reloaded.
"""

import os
import re
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import model
from model import WIDGETS, LAYOUT_SPECS, FONT_FAMILIES
import theme
import dialogs
from dialogs import font, px, muted

try:
    import apptheme
except ImportError:  # pragma: no cover
    apptheme = None

_NOTHING = object()     # "no pending show()"

# What to say on the Actions tab of widgets that have no events.
NO_EVENTS_TEXT = {
    "Frame": "Panels only hold other widgets – they don't react to clicks.\n\nSelect a "
             "widget inside the panel to give it actions. To hide or show the whole "
             "panel, use “Hide a widget” / “Show a hidden widget” on a button.",
    "LabelFrame": "Group boxes only hold other widgets – they don't react to clicks.\n\n"
                  "Select a widget inside the group to give it actions.",
    "Tab": "A tab only holds other widgets.\n\nTo react when the user switches tabs, "
           "select the Tabs widget itself. To show this tab from a button, use the "
           "“Switch to a tab” action.",
    "Text": "A Text Area doesn't have actions of its own.\n\nUse a button's actions to "
            "fill it, clear it, or save it to a file. In a message you can show what "
            "the user typed with {%s}.",
    "Console": "An Output Console shows messages. Fill it with the “Write a line to a "
               "console” action, with “Run a program / command”, or from your own code "
               "with self.log(text).",
    "Progressbar": "Progress bars don't react to clicks.\n\nTo move the bar, use “Set the "
                   "text / value” or “Add to a number”, or choose it as “show progress on” "
                   "of “Call a Python function”.",
    "Separator": "A separator is just a line to tidy up your window. It has no actions.",
}

FONT_SIZE_HINT = "0 = normal size"


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else repr(f)


def _friendly_type(node, project):
    if node["type"] == "Window":
        return "Main window" if node is project.main_window else "Window"
    return WIDGETS[node["type"]]["name"]


def _after_rename(app, old, new):
    """Bring the app up to date after project.rename(old, new)."""
    app.selected_id = new
    wp = getattr(app, "workplane", None)
    if wp is not None and getattr(wp, "current_window_id", None) == old:
        wp.current_window_id = new
    tab_of = getattr(wp, "_tab_of", None)
    if isinstance(tab_of, dict):     # keep showing the same tab of each Tabs widget
        for k, v in list(tab_of.items()):
            if v == old:
                tab_of[k] = new
        if old in tab_of:
            tab_of[new] = tab_of.pop(old)
    app.commit("Renamed %s to %s." % (old, new))
    app.select(new)


def _wrap():
    return px(272)


class _Stack:
    """Pages stacked on top of each other; show() raises one.

    Raising a window is ~10 ms while pack_forget()/pack() of a page with many
    widgets costs hundreds of milliseconds on Windows (every child window is
    re-mapped), so cached pages are never unpacked.  The stack's height only
    ever grows: changing it makes Windows repaint every stacked page (~300 ms
    on slow machines), so instead the scroll area is clipped to the visible
    page (see fit_scroll)."""

    def __init__(self, parent, canvas=None):
        self.frame = tk.Frame(parent, bg=theme.BG, height=1)
        self.frame.pack(fill="x")
        self.canvas = canvas
        self.cur = None

    def add(self, page_frame):
        page_frame.place(in_=self.frame, x=0, y=0, relwidth=1)
        page_frame.bind("<Configure>", lambda e, f=page_frame: self._sized(f), add="+")

    def show(self, page_frame):
        if self.cur is page_frame:
            return False
        self.cur = page_frame
        page_frame.lift()
        self._sized(page_frame)
        return True

    def _sized(self, f):
        if f is not self.cur:
            return
        h = max(1, f.winfo_reqheight())
        if int(self.frame.cget("height")) < h:
            self.frame.configure(height=h)
        self.frame.after_idle(self.fit_scroll)

    def grow(self, f):
        """Make room for page f in advance (the stack never shrinks)."""
        try:
            h = f.winfo_reqheight()
            if int(self.frame.cget("height")) < h:
                self.frame.configure(height=h)
                self.frame.after_idle(self.fit_scroll)
        except tk.TclError:
            pass

    def fit_scroll(self):
        """Limit scrolling to the visible page (the stack may be taller)."""
        c = self.canvas
        if c is None or self.cur is None:
            return
        try:
            top = self.cur.winfo_rooty() - c.winfo_rooty() + int(c.canvasy(0))
            bottom = top + self.cur.winfo_reqheight() + px(16)
            c.configure(scrollregion=(0, 0, c.winfo_width(), max(bottom, c.winfo_height())))
        except tk.TclError:
            pass


# ============================================================================
# Property editors (built once per widget type, loaded many times)
# ============================================================================

class _Ed:
    """Base: an editor owns `self.frame`-less widgets inside `parent` and
    calls `on_change(value)` for user edits only (never while loading)."""

    def __init__(self, insp, parent, spec, on_change):
        self.insp = insp
        self.spec = spec
        self.on_change = on_change
        self.loading = False

    def _emit(self, value):
        if not self.loading:
            self.on_change(value)

    def load(self, value, node):
        self.loading = True
        try:
            self._load(value, node)
        finally:
            self.loading = False

    def _load(self, value, node):
        pass


class TextEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.var).pack(fill="x")
        self.var.trace_add("write", lambda *a: self._emit(self.var.get()))

    def _load(self, value, node):
        value = "" if value is None else str(value)
        if self.var.get() != value:
            self.var.set(value)


class MultiEd(_Ed):
    """multiline (str) or lines (list[str], one item per line)."""

    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        lines = spec["kind"] == "lines"
        self.txt = dialogs.text_widget(parent, height=4 if lines else 3, width=24, wrap="word")
        self.txt.pack(fill="x")
        self.txt.bind("<<Modified>>", self._mod)
        # let Tab move focus instead of inserting a tab character
        self.txt.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])

    def _text(self, value):
        if self.spec["kind"] == "lines":
            return "\n".join(value or [])
        return value or ""

    def _load(self, value, node):
        text = self._text(value)
        if self.txt.get("1.0", "end-1c") != text:
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", text)
            self.txt.edit_reset()
        self.txt.edit_modified(False)

    def _mod(self, _e=None):
        try:
            if not self.txt.edit_modified():
                return
            self.txt.edit_modified(False)
            s = self.txt.get("1.0", "end-1c")
        except tk.TclError:
            return
        if self.spec["kind"] == "lines":
            self._emit([ln for ln in s.split("\n") if ln.strip()])
        else:
            self._emit(s)


class NumberEd(_Ed):
    def __init__(self, insp, parent, spec, on_change, scroll=None):
        super().__init__(insp, parent, spec, on_change)
        opts = spec.get("options") or {}
        self.lo = opts.get("min", -1e9)
        self.hi = opts.get("max", 1e9)
        self.kind = spec["kind"]
        self.var = tk.StringVar()
        self.sp = ttk.Spinbox(parent, from_=self.lo, to=self.hi, increment=1,
                              textvariable=self.var, width=10)
        self.sp.pack(anchor="w")
        self.last = spec.get("default") or 0
        self.var.trace_add("write", self._changed)
        self.sp.bind("<FocusOut>", self._normalise)
        self.sp.bind("<Return>", self._normalise)
        dialogs.no_wheel(self.sp, scroll)

    def _parse(self):
        s = self.var.get().strip().replace(",", ".")
        try:
            v = float(s)
        except ValueError:
            return None
        if self.kind == "int":
            if not v.is_integer():
                return None
            v = int(v)
        return v

    def _changed(self, *_a):
        if self.loading:
            return
        v = self._parse()
        if v is None or v < self.lo or v > self.hi:
            return            # ignore until valid
        self.last = v
        self._emit(int(v) if self.kind == "int" else float(v))

    def _normalise(self, _e=None):
        v = self._parse()
        if v is None:
            v = self.last
        v = max(self.lo, min(self.hi, v))
        if self.kind == "int":
            v = int(v)
        try:
            if self.var.get() != _fmt_num(v):
                self.var.set(_fmt_num(v))
        except tk.TclError:
            pass

    def _load(self, value, node):
        self.last = value if value is not None else 0
        cur = self._parse()
        try:
            same = cur is not None and float(cur) == float(self.last)
        except (TypeError, ValueError):
            same = False
        if not same:
            self.var.set(_fmt_num(self.last))


class BoolEd(_Ed):
    def __init__(self, insp, parent, spec, on_change, pady=0):
        super().__init__(insp, parent, spec, on_change)
        self.var = tk.BooleanVar()
        ttk.Checkbutton(parent, text=spec["label"], variable=self.var,
                        command=lambda: self._emit(bool(self.var.get()))).pack(anchor="w",
                                                                              pady=pady)

    def _load(self, value, node):
        if bool(self.var.get()) != bool(value):
            self.var.set(bool(value))


class ChoiceEd(_Ed):
    def __init__(self, insp, parent, spec, on_change, options=None, scroll=None):
        super().__init__(insp, parent, spec, on_change)
        opts = options if options is not None else spec["options"]
        self.values = [o[0] for o in opts]
        labels = [o[1] for o in opts]
        self.cb = ttk.Combobox(parent, state="readonly", values=labels,
                               width=max(12, min(30, max(len(s) for s in labels) + 2)))
        self.cb.pack(anchor="w")
        self.cb.bind("<<ComboboxSelected>>", self._picked)
        dialogs.no_wheel(self.cb, scroll)

    def _picked(self, _e=None):
        self.cb.selection_clear()
        i = self.cb.current()
        if i >= 0:
            self._emit(self.values[i])

    def _load(self, value, node):
        i = self.values.index(value) if value in self.values else 0
        if self.cb.current() != i:
            self.cb.current(i)


class ColorEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        key = spec["key"]
        shown_default = theme.TEXT if key == "fg" else theme.PANEL
        self.picker = dialogs.ColorPicker(parent, "", on_change=self._emit, default=shown_default,
                                          quick=True, reset_value=spec["default"] or "")
        self.picker.pack(anchor="w", fill="x")

    def _load(self, value, node):
        if self.picker.value != (value or ""):
            self.picker.set(value or "")


class FontEd(_Ed):
    def __init__(self, insp, parent, spec, on_change, scroll=None):
        super().__init__(insp, parent, spec, on_change)
        fams = ["(normal)"] + [x for x in FONT_FAMILIES if x]
        self.fam = ttk.Combobox(parent, state="readonly", values=fams, width=24)
        self.fam.pack(anchor="w")
        dialogs.no_wheel(self.fam, scroll)
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x", pady=(px(5), 0))
        tk.Label(row, text="Size", bg=theme.BG, fg=theme.TEXT, font=font(9)).pack(side="left")
        self.size_var = tk.StringVar(value="0")
        sp = ttk.Spinbox(row, from_=0, to=200, increment=1, textvariable=self.size_var, width=4)
        sp.pack(side="left", padx=(px(4), px(10)))
        dialogs.no_wheel(sp, scroll)
        theme.tooltip(sp, "Text size in points. 0 = the normal size.")
        # B / I / U toggles drawn on one canvas
        self.styles = {"bold": False, "italic": False, "underline": False}
        s = px(26)
        self.biu = tk.Canvas(row, width=s * 3 + px(4), height=s, bg=theme.BG,
                             highlightthickness=0, cursor="hand2")
        self.biu.pack(side="left")
        for i, (key, text, fnt) in enumerate((("bold", "B", font(10, "bold")),
                                              ("italic", "I", font(10, "italic")),
                                              ("underline", "U", font(10, "underline")))):
            x = i * (s + px(2))
            self.biu.create_rectangle(x, 0, x + s - 1, s - 1, outline=theme.BORDER,
                                      fill=theme.PANEL, tags=(key, key + "_bg"))
            self.biu.create_text(x + s / 2, s / 2, text=text, font=fnt, fill=theme.TEXT,
                                 tags=(key,))
            self.biu.tag_bind(key, "<Button-1>", lambda e, k=key: self._toggle(k))
        theme.tooltip(self.biu, "Bold, italic, underline", delay=400)
        self.fam.bind("<<ComboboxSelected>>", lambda e: (self._push(), self.fam.selection_clear()))
        self.size_var.trace_add("write", self._push)

    def _push(self, *_a):
        if self.loading:
            return
        fam_v = self.fam.get()
        try:
            size = int(self.size_var.get())
        except ValueError:
            return
        if size < 0 or size > 200:
            return
        new = {"family": "" if fam_v == "(normal)" else fam_v, "size": size}
        new.update(self.styles)
        self._emit(new)

    def _toggle(self, key):
        self.styles[key] = not self.styles[key]
        self._draw_styles()
        self._push()

    def _draw_styles(self):
        for k, on in self.styles.items():
            self.biu.itemconfigure(k + "_bg", fill=theme.ACCENT_LIGHT if on else theme.PANEL,
                                   outline=theme.ACCENT if on else theme.BORDER)

    def _load(self, value, node):
        f = dict(model.default_font())
        f.update(value or {})
        fam = f["family"] or "(normal)"
        if fam not in self.fam["values"]:
            self.fam["values"] = tuple(self.fam["values"]) + (fam,)
        self.fam.set(fam)
        self.size_var.set(str(int(f.get("size") or 0)))
        for k in self.styles:
            self.styles[k] = bool(f.get(k))
        self._draw_styles()


class ImageEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.ico = spec["key"] == "icon"       # window icons may be .ico
        row = tk.Frame(parent, bg=theme.BG)
        row.pack(fill="x")
        self.var = tk.StringVar()
        ttk.Entry(row, textvariable=self.var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", style="Small.TButton", command=self._browse).pack(
            side="left", padx=(px(4), 0))
        dialogs.small_button(row, "×", lambda: self.var.set(""), "Remove the picture",
                             bg=theme.BG, fg=theme.DANGER).pack(side="left", padx=(px(2), 0))
        self.warn = tk.Label(parent, text="", bg=theme.BG, fg=theme.DANGER, anchor="w",
                             justify="left", wraplength=_wrap(), font=font(8))
        muted(parent, ("A PNG, GIF or ICO picture." if self.ico else "A PNG or GIF picture.") +
              " Keep it next to your saved program.").pack(fill="x", pady=(px(2), 0))
        self.var.trace_add("write", self._written)

    def _exts(self):
        return (".png", ".gif", ".ico") if self.ico else (".png", ".gif")

    def _check(self):
        path = self.var.get().strip()
        if path and not os.path.isfile(path) and not os.path.isabs(path):
            base = dialogs.project_dir(self.insp.app)
            if base and os.path.isfile(os.path.join(base, path)):
                path = os.path.join(base, path)
        if path and not os.path.isfile(path):
            text = "⚠ This file can't be found."
        elif path and not path.lower().endswith(self._exts()):
            text = "⚠ Only %s pictures work." % "/".join(e[1:].upper() for e in self._exts())
        else:
            text = ""
        if self.warn.cget("text") != text:
            self.warn.configure(text=text)
        if text and not self.warn.winfo_manager():
            self.warn.pack(fill="x")
        elif not text and self.warn.winfo_manager():
            self.warn.pack_forget()

    def _browse(self):
        init = os.path.dirname(self.var.get()) if self.var.get() else \
            dialogs.project_dir(self.insp.app)
        types = [("Pictures", " ".join("*" + e for e in self._exts()))] + \
            [(e[1:].upper(), "*" + e) for e in self._exts()]
        path = filedialog.askopenfilename(parent=self.insp.winfo_toplevel(),
                                          title="Choose a picture", initialdir=init,
                                          filetypes=types)
        if path:
            self.var.set(path)

    def _written(self, *_a):
        self._check()
        self._emit(self.var.get().strip())

    def _load(self, value, node):
        if self.var.get() != (value or ""):
            self.var.set(value or "")
        self._check()


class ShortcutEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.sc = dialogs.ShortcutEntry(parent, "", on_change=self._emit)
        self.sc.pack(anchor="w", fill="x")

    def _load(self, value, node):
        self.sc.load(value or "")


class ColumnsEd(_Ed):
    def __init__(self, insp, parent, spec, on_change, scroll=None):
        super().__init__(insp, parent, spec, on_change)
        self.ed = dialogs.ColumnsEditor(parent, on_change=self._emit, scroll_canvas=scroll)
        self.ed.pack(fill="x")

    def _load(self, value, node):
        self.ed.load(value)


class RowsEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.ed = dialogs.RowsEditor(parent, on_change=self._emit)
        self.ed.pack(fill="x")

    def _load(self, value, node):
        ncols = len(node["props"].get("columns") or []) if node else 0
        self.ed.load(value or [], ncols)


class VariantEd(ChoiceEd):
    def __init__(self, insp, parent, spec, on_change, scroll=None):
        cls = spec.get("options") or ""
        opts = (apptheme.VARIANTS.get(cls) if apptheme else None) or [["", "Normal"]]
        super().__init__(insp, parent, spec, on_change, options=opts, scroll=scroll)


class MenuSummaryEd(_Ed):
    def __init__(self, insp, parent, spec, on_change):
        super().__init__(insp, parent, spec, on_change)
        self.lbl = tk.Label(parent, text="", bg=theme.BG, fg=theme.TEXT, anchor="w",
                            justify="left", wraplength=_wrap(), font=font(9))
        self.lbl.pack(fill="x")
        ttk.Button(parent, text="☰  Edit menu bar…", command=insp.edit_menu).pack(
            anchor="w", pady=(px(4), 0))

    def _load(self, value, node):
        menus = value or []
        if menus:
            parts = []
            for m in menus:
                n = sum(1 for it in m.get("items", []) if not it.get("separator"))
                parts.append("%s (%d item%s)" % (m.get("label") or "?", n, "" if n == 1 else "s"))
            self.lbl.configure(text="Menus: " + ", ".join(parts), fg=theme.TEXT)
        else:
            self.lbl.configure(text="This window has no menu bar yet.", fg=theme.MUTED)


# ============================================================================
# Pages
# ============================================================================

class _Page:
    """Common helpers for the cached pages."""

    def __init__(self, insp, parent):
        self.insp = insp
        self.frame = tk.Frame(parent, bg=theme.BG)

    def section(self, parent, title, first=False):
        """A section heading (one label: every native window costs time)."""
        lbl = tk.Label(parent, text=title.upper(), bg=theme.BG, fg=theme.MUTED, anchor="w",
                       font=font(8, "bold"))
        lbl.pack(fill="x", pady=(0 if first else px(14), px(6)))
        return lbl

    def field(self, parent, label, help_text=""):
        """A labelled field; returns the frame to put the editor in.  The help
        text is packed at the bottom, so editors can be packed after it."""
        box = tk.Frame(parent, bg=theme.BG)
        box.pack(fill="x", pady=(0, px(9)))
        if label:
            tk.Label(box, text=label, bg=theme.BG, fg=theme.TEXT, anchor="w", justify="left",
                     wraplength=_wrap(), font=font(9)).pack(fill="x", pady=(0, px(2)))
        if help_text:
            muted(box, help_text, wrap=272).pack(side="bottom", fill="x", pady=(px(2), 0))
        return box


class PropPage(_Page):
    """Properties of one widget type."""

    def __init__(self, insp, parent, wtype, scroll):
        super().__init__(insp, parent)
        self.wtype = wtype
        self.scroll = scroll
        self.editors = []           # (spec, editor)
        self.tab_mgr = None
        self.tab_pos = None
        self.node = None
        c = self.frame
        spec = WIDGETS[wtype]
        first = True
        if wtype == "Notebook":
            self.section(c, "Tabs", first=True)
            self.tab_mgr = TabManager(insp, c)
            first = False
        if wtype == "Tab":
            self.section(c, "Position", first=True)
            self.tab_pos = TabPosition(insp, c)
            first = False
        normal = [p for p in spec["props"] if not p.get("advanced")]
        advanced = [p for p in spec["props"] if p.get("advanced")]
        groups = []
        for p in normal:
            if p["group"] not in groups:
                groups.append(p["group"])
        for g in groups:
            self.section(c, "Menu bar" if g == "Menu" else g, first=first)
            first = False
            for p in normal:
                if p["group"] == g:
                    self._add(c, p)
        self.more = None
        if advanced:
            self.more_link = tk.Label(c, text="", bg=theme.BG, fg=theme.ACCENT, cursor="hand2",
                                      font=font(9, "bold"), anchor="w")
            self.more_link.pack(fill="x", pady=(px(14), px(6)))
            self.more_link.bind("<Button-1>", lambda e: insp.toggle_more())
            self.more = tk.Frame(c, bg=theme.BG)
            self.n_adv = len(advanced)
            self.advanced = advanced
            self.more_built = False
            self.show_more(insp.more_open)

    def show_more(self, on):
        if self.more is None:
            return
        if on and not self.more_built:          # built the first time it is opened
            self.more_built = True
            start = len(self.editors)
            for p in self.advanced:
                self._add(self.more, p)
            node = self.insp.app.project.find(self.node) if self.node else None
            if node is not None:
                for p, ed in self.editors[start:]:
                    ed.load(node["props"].get(p["key"], p["default"]), node)
        self.more_link.configure(text=("▾  Fewer options" if on else
                                       "▸  More options (%d)" % self.n_adv))
        if on and not self.more.winfo_manager():
            self.more.pack(fill="x", after=self.more_link)
        elif not on and self.more.winfo_manager():
            self.more.pack_forget()

    def _add(self, parent, p):
        insp = self.insp
        kind = p["kind"]

        def setter(v, k=p["key"], lab=p["label"]):
            insp.set_prop(k, v, lab)
        if kind == "bool":
            if p.get("help"):
                box = tk.Frame(parent, bg=theme.BG)
                box.pack(fill="x", pady=(0, px(7)))
                ed = BoolEd(insp, box, p, setter)
                muted(box, p["help"], wrap=272).pack(fill="x")
            else:
                ed = BoolEd(insp, parent, p, setter, pady=(0, px(7)))
        else:
            f = self.field(parent, "" if kind == "menu" else p["label"], p.get("help", ""))
            if kind == "text":
                ed = TextEd(insp, f, p, setter)
            elif kind in ("multiline", "lines"):
                ed = MultiEd(insp, f, p, setter)
            elif kind in ("int", "float"):
                ed = NumberEd(insp, f, p, setter, self.scroll)
            elif kind == "choice":
                ed = ChoiceEd(insp, f, p, setter, scroll=self.scroll)
            elif kind == "variant":
                ed = VariantEd(insp, f, p, setter, scroll=self.scroll)
            elif kind == "color":
                ed = ColorEd(insp, f, p, setter)
            elif kind == "font":
                ed = FontEd(insp, f, p, setter, self.scroll)
            elif kind == "image":
                ed = ImageEd(insp, f, p, setter)
            elif kind == "shortcut":
                ed = ShortcutEd(insp, f, p, setter)
            elif kind == "columns":
                ed = ColumnsEd(insp, f, p, setter, self.scroll)
            elif kind == "rows":
                ed = RowsEd(insp, f, p, setter)
            elif kind == "menu":
                ed = MenuSummaryEd(insp, f, p, setter)
            else:
                ed = TextEd(insp, f, p, setter)
        self.editors.append((p, ed))

    def load(self, node):
        self.node = node["id"]
        props = node["props"]
        for p, ed in self.editors:
            ed.load(props.get(p["key"], p["default"]), node)
        if self.tab_mgr is not None:
            self.tab_mgr.load(node)
        if self.tab_pos is not None:
            self.tab_pos.load(node)

    def after_change(self, key, node):
        """Keep dependent editors in sync (e.g. rows know the column count)."""
        if key == "columns":
            for p, ed in self.editors:
                if p["kind"] == "rows":
                    ed.load(node["props"].get("rows"), node)


class TabManager:
    """List of the tabs of a Tabs widget with add / rename / move / remove."""

    def __init__(self, insp, c):
        self.insp = insp
        self.nid = None
        self.index = {}
        box = tk.Frame(c, bg=theme.BG)
        box.pack(fill="x", pady=(0, px(6)))
        self.lb = tk.Listbox(box, height=5, activestyle="none", relief="flat", bd=0,
                             highlightthickness=1, highlightbackground=theme.BORDER,
                             exportselection=False, bg=dialogs.field_bg(), fg=theme.TEXT,
                             selectbackground=theme.ACCENT, selectforeground="#ffffff",
                             font=font(10))
        self.lb.pack(side="left", fill="both", expand=True)
        side = tk.Frame(box, bg=theme.BG)
        side.pack(side="left", fill="y", padx=(px(6), 0))
        for text, cmd, tip in (("＋ Add", self.add, "Add a new tab at the end"),
                               ("✎ Rename", self.rename, "Change the title of the selected tab"),
                               ("↑ Earlier", lambda: self.move(-1), "Move the tab to the left"),
                               ("↓ Later", lambda: self.move(1), "Move the tab to the right"),
                               ("✕ Remove", self.remove, "Delete the selected tab")):
            b = ttk.Button(side, text=text, style="Small.TButton", command=cmd, width=9)
            b.pack(fill="x", pady=(0, px(3)))
            theme.tooltip(b, tip)
        self.lb.bind("<Double-Button-1>", self.open_tab)
        self.lb.bind("<<ListboxSelect>>", lambda e: self._remember(self._sel() or 0))
        muted(c, "Double-click a tab to edit it and its contents.").pack(fill="x")

    def _node(self):
        return self.insp.app.project.find(self.nid)

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def _remember(self, i):
        self.index[self.nid] = i

    def load(self, node):
        self.nid = node["id"]
        tabs = node["children"]
        self.lb.delete(0, "end")
        for t in tabs:
            self.lb.insert("end", "  %s" % (t["props"].get("text") or t["id"]))
        self.lb.configure(height=max(3, min(7, len(tabs))))
        if tabs:
            cur = None
            wp = getattr(self.insp.app, "workplane", None)
            if wp is not None and hasattr(wp, "current_tab_of"):
                try:
                    cur = wp.current_tab_of(self.nid)
                except Exception:  # noqa: BLE001
                    cur = None
            idx = next((i for i, t in enumerate(tabs) if t["id"] == cur), 0)
            idx = max(0, min(self.index.get(self.nid, idx), len(tabs) - 1))
            self.lb.selection_set(idx)
            self.lb.see(idx)

    def add(self):
        app = self.insp.app
        t = app.project.add_tab(self.nid)
        self._remember(len(self._node()["children"]) - 1)
        tab_of = getattr(app.workplane, "_tab_of", None)
        if isinstance(tab_of, dict):
            tab_of[self.nid] = t["id"]       # show the new (empty) tab
        app.commit("Added tab “%s”." % t["props"]["text"])

    def rename(self):
        i = self._sel()
        if i is None:
            return
        tab = self._node()["children"][i]
        new = dialogs.ask_string(self.insp, "Rename tab", "New title for this tab:",
                                 tab["props"].get("text", ""))
        if new is None or new == tab["props"].get("text"):
            return
        tab["props"]["text"] = new
        self._remember(i)
        self.insp.app.commit("Renamed tab to “%s”." % new)

    def move(self, delta):
        i = self._sel()
        kids = self._node()["children"]
        if i is None or not 0 <= i + delta < len(kids):
            return
        self.insp.app.project.move_tab(kids[i]["id"], delta)
        self._remember(i + delta)
        self.insp.app.commit("Moved a tab.")

    def remove(self):
        i = self._sel()
        kids = self._node()["children"]
        if i is None:
            return
        tab = kids[i]
        if len(kids) <= 1:
            self.insp.app.status("Tabs need at least one tab.")
            return
        if tab.get("children") and not messagebox.askyesno(
                "Remove tab", "Remove the tab “%s” and everything on it?" % tab["props"].get(
                    "text"), parent=self.insp.winfo_toplevel()):
            return
        try:
            self.insp.app.project.remove(tab["id"])
        except ValueError as e:
            self.insp.app.status(str(e))
            return
        self._remember(max(0, i - 1))
        self.insp.app.commit("Removed tab “%s”." % tab["props"].get("text"))

    def open_tab(self, _e=None):
        i = self._sel()
        if i is not None:
            self.insp.app.select(self._node()["children"][i]["id"])


class TabPosition:
    def __init__(self, insp, c):
        self.insp = insp
        self.nid = None
        self.lbl = tk.Label(c, text="", bg=theme.BG, fg=theme.TEXT, anchor="w", font=font(9))
        self.lbl.pack(fill="x")
        row = tk.Frame(c, bg=theme.BG)
        row.pack(fill="x", pady=(px(4), 0))
        self.b1 = ttk.Button(row, text="◀ Move left", style="Small.TButton",
                             command=lambda: self.move(-1))
        self.b1.pack(side="left")
        self.b2 = ttk.Button(row, text="Move right ▶", style="Small.TButton",
                             command=lambda: self.move(1))
        self.b2.pack(side="left", padx=(px(6), 0))
        ttk.Button(row, text="＋ Add tab", style="Small.TButton", command=self.add).pack(
            side="left", padx=(px(6), 0))

    def load(self, node):
        project = self.insp.app.project
        self.nid = node["id"]
        nb = project.parent_of(node["id"])
        if nb is None:
            return
        kids = nb["children"]
        i = next(k for k, t in enumerate(kids) if t["id"] == node["id"])
        self.lbl.configure(text="Tab %d of %d in %s." % (i + 1, len(kids),
                                                         project.display_name(nb)))
        self.b1.state(["disabled"] if i == 0 else ["!disabled"])
        self.b2.state(["disabled"] if i == len(kids) - 1 else ["!disabled"])

    def move(self, delta):
        node = self.insp.app.project.find(self.nid)
        self.insp.app.project.move_tab(self.nid, delta)
        self.insp.app.commit("Moved tab “%s”." % node["props"].get("text"))

    def add(self):
        app = self.insp.app
        nb = app.project.parent_of(self.nid)
        t = app.project.add_tab(nb["id"])
        app.commit("Added tab “%s”." % t["props"]["text"])
        app.select(t["id"])


# ----------------------------------------------------------------------------
class LayoutPage(_Page):
    """Where the widget sits in its container's grid (shared by all types)."""

    DW, DH = 176, 104       # diagram size (96 dpi pixels)

    def __init__(self, insp, parent, scroll):
        super().__init__(insp, parent)
        self.nid = None
        self.specs = {p["key"]: p for p in LAYOUT_SPECS}
        c = self.frame
        self.none_lbl = muted(c, "This item is placed automatically – it has no layout "
                                 "settings.", size=9)
        self.body = tk.Frame(c, bg=theme.BG)

        b = self.body
        self.section(b, "Where is it?", first=True)
        self.where = tk.Label(b, text="", bg=theme.BG, fg=theme.TEXT, anchor="w",
                              justify="left", wraplength=_wrap(), font=font(9))
        self.where.pack(fill="x", pady=(0, px(6)))
        row = tk.Frame(b, bg=theme.BG)
        row.pack(fill="x")
        self.diagram = tk.Canvas(row, width=px(self.DW), height=px(self.DH), bg=theme.PANEL,
                                 highlightthickness=1, highlightbackground=theme.BORDER)
        self.diagram.pack(side="left")
        self.pad = tk.Canvas(row, width=px(84), height=px(84), bg=theme.BG,
                             highlightthickness=0, cursor="hand2")
        self.pad.pack(side="left", padx=(px(12), 0))
        self._draw_pad()
        muted(b, "Click another box in the picture to select that widget. "
                 "The arrows move this one.").pack(fill="x", pady=(px(3), 0))

        self.section(b, "Stretch")
        self.stretch_x = tk.BooleanVar()
        self.stretch_y = tk.BooleanVar()
        sr = tk.Frame(b, bg=theme.BG)
        sr.pack(fill="x")
        ttk.Checkbutton(sr, text="↔  Sideways", variable=self.stretch_x,
                        command=self._stretch).pack(side="left")
        ttk.Checkbutton(sr, text="↕  Up/down", variable=self.stretch_y,
                        command=self._stretch).pack(side="left", padx=(px(16), 0))
        muted(b, "A stretched widget fills its cell and grows when the window is resized.",
              wrap=272).pack(fill="x", pady=(px(2), 0))

        self.section(b, "Position in its cell")
        ar = tk.Frame(b, bg=theme.BG)
        ar.pack(fill="x")
        self.align = tk.Canvas(ar, width=px(78), height=px(54), bg=theme.PANEL,
                               highlightthickness=1, highlightbackground=theme.BORDER,
                               cursor="hand2")
        self.align.pack(side="left")
        self.align.bind("<Button-1>", self._align_click)
        self.align_lbl = tk.Label(ar, text="", bg=theme.BG, fg=theme.TEXT, anchor="w",
                                  justify="left", font=font(9), wraplength=px(170))
        self.align_lbl.pack(side="left", padx=(px(10), 0), fill="x")

        self.section(b, "Size and spacing")
        grid = tk.Frame(b, bg=theme.BG)
        grid.pack(fill="x")
        self.numbers = {}
        for i, key in enumerate(("colspan", "rowspan", "padx", "pady")):
            p = self.specs[key]
            cell = tk.Frame(grid, bg=theme.BG)
            cell.grid(row=i // 2, column=i % 2, sticky="nw", padx=(0, px(12)), pady=(0, px(8)))
            tk.Label(cell, text=p["label"], bg=theme.BG, fg=theme.TEXT, font=font(9),
                     anchor="w").pack(fill="x")
            self.numbers[key] = NumberEd(insp, cell, p,
                                         lambda v, k=key, lab=p["label"]:
                                         insp.set_layout(k, v, lab), scroll)
        self.section(b, "How layout works")
        muted(b, "Widgets sit in an invisible grid of rows and columns, like a spreadsheet. "
                 "Drag a widget next to another one to put it beside or below it. "
                 "“Columns wide” lets one widget span several columns, for example a long "
                 "text box under two buttons. To group widgets, put them in a Panel or "
                 "Group Box.", size=9, wrap=272).pack(fill="x")

    # ---- move pad ----
    def _draw_pad(self):
        c = self.pad
        s = px(28)
        for glyph, r, col, dr, dc, tip in (("↑", 0, 1, -1, 0, "up"), ("←", 1, 0, 0, -1, "left"),
                                           ("→", 1, 2, 0, 1, "right"), ("↓", 2, 1, 1, 0, "down")):
            x0, y0 = col * s, r * s
            tag = "a_" + tip
            c.create_rectangle(x0 + 1, y0 + 1, x0 + s - 2, y0 + s - 2, fill=theme.PANEL,
                               outline=theme.BORDER, tags=(tag, tag + "_bg"))
            c.create_text(x0 + s / 2 - 1, y0 + s / 2 - 1, text=glyph, fill=theme.TEXT,
                          font=font(11), tags=(tag,))
            c.tag_bind(tag, "<Button-1>", lambda e, a=dr, b=dc: self.insp.move_widget(a, b))
            c.tag_bind(tag, "<Enter>", lambda e, t=tag: c.itemconfigure(
                t + "_bg", fill=theme.ACCENT_LIGHT))
            c.tag_bind(tag, "<Leave>", lambda e, t=tag: c.itemconfigure(t + "_bg",
                                                                        fill=theme.PANEL))
        c.create_text(s * 1.5 - 1, s * 1.5 - 1, text="Move", fill=theme.MUTED, font=font(7))

    # ---- load ----
    def load(self, node):
        project = self.insp.app.project
        self.nid = node["id"]
        parent = project.parent_of(node["id"])
        if parent is None or model.layout_mode(parent) != "grid":
            self.body.pack_forget()
            self.none_lbl.pack(fill="x")
            return
        self.none_lbl.pack_forget()
        if not self.body.winfo_manager():
            self.body.pack(fill="x")
        lay = node["layout"]
        self.where.configure(text="Row %d, column %d of %s." % (
            lay["row"] + 1, lay["col"] + 1,
            "the window" if parent["type"] == "Window" else project.display_name(parent)))
        fill = lay.get("fill", "none")
        self.stretch_x.set(fill in ("x", "both"))
        self.stretch_y.set(fill in ("y", "both"))
        for key, ed in self.numbers.items():
            ed.load(lay.get(key, self.specs[key]["default"]), node)
        self.draw_diagram()
        self._draw_align(lay)

    def _stretch(self):
        x, y = self.stretch_x.get(), self.stretch_y.get()
        fill = "both" if x and y else ("x" if x else ("y" if y else "none"))
        self.insp.set_layout("fill", fill, "Stretch")
        node = self.insp.node()
        if node is not None:
            self._draw_align(node["layout"])

    # ---- alignment picker ----
    def _draw_align(self, lay):
        c = self.align
        c.delete("all")
        w, h = px(78), px(54)
        cw, ch = w / 3, h / 3
        fill = lay.get("fill", "none")
        hi = {"left": 0, "center": 1, "right": 2}.get(lay.get("halign", "left"), 0)
        vi = {"top": 0, "center": 1, "bottom": 2}.get(lay.get("valign", "center"), 1)
        for r in range(3):
            for col in range(3):
                x, y = col * cw + cw / 2, r * ch + ch / 2
                c.create_oval(x - 2, y - 2, x + 2, y + 2, fill=theme.BORDER, outline="")
        sx = fill in ("x", "both")
        sy = fill in ("y", "both")
        x0 = 3 if sx else hi * cw + 4
        x1 = w - 3 if sx else (hi + 1) * cw - 4
        y0 = 3 if sy else vi * ch + 4
        y1 = h - 3 if sy else (vi + 1) * ch - 4
        c.create_rectangle(x0, y0, x1, y1, fill=theme.ACCENT, outline="")
        hname = "stretched sideways" if sx else ["left", "centre", "right"][hi]
        vname = "stretched up/down" if sy else ["top", "middle", "bottom"][vi]
        text = "%s, %s" % (vname.capitalize(), hname)
        if sx and sy:
            text = "Fills the whole cell."
        else:
            text += ".\nClick a spot to move it there."
        self.align_lbl.configure(text=text)

    def _align_click(self, e):
        w, h = px(78), px(54)
        col = max(0, min(2, int(e.x / (w / 3))))
        row = max(0, min(2, int(e.y / (h / 3))))
        node = self.insp.node()
        if node is None:
            return
        lay = node["layout"]
        fill = lay.get("fill", "none")
        if fill not in ("x", "both"):
            self.insp.set_layout("halign", ["left", "center", "right"][col], "Horizontal position")
        if fill not in ("y", "both"):
            self.insp.set_layout("valign", ["top", "center", "bottom"][row], "Vertical position")
        self._draw_align(lay)

    # ---- grid diagram ----
    def draw_diagram(self):
        cv = self.diagram
        cv.delete("all")
        project = self.insp.app.project
        parent = project.parent_of(self.nid) if self.nid else None
        if parent is None:
            return
        rows, cols = project.grid_size(parent)
        rows, cols = max(rows, 1), max(cols, 1)
        W, H, m = px(self.DW), px(self.DH), px(6)
        cw = min(px(48), (W - 2 * m) / cols)
        ch = min(px(30), (H - 2 * m) / rows)
        ox = (W - cw * cols) / 2
        oy = (H - ch * rows) / 2
        for r in range(rows):
            for col in range(cols):
                cv.create_rectangle(ox + col * cw + 1, oy + r * ch + 1, ox + (col + 1) * cw - 1,
                                    oy + (r + 1) * ch - 1, outline=theme.BORDER, dash=(2, 2))
        for chn in parent["children"]:
            lay = chn["layout"]
            x0 = ox + lay["col"] * cw + 2
            y0 = oy + lay["row"] * ch + 2
            x1 = ox + (lay["col"] + lay["colspan"]) * cw - 2
            y1 = oy + (lay["row"] + lay["rowspan"]) * ch - 2
            me = chn["id"] == self.nid
            tag = "n_" + chn["id"]
            cv.create_rectangle(x0, y0, x1, y1, fill=theme.ACCENT if me else theme.BG,
                                outline=theme.ACCENT if me else theme.BORDER, tags=(tag,))
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=WIDGETS[chn["type"]]["icon"],
                           fill="#ffffff" if me else theme.MUTED, font=font(9), tags=(tag,))
            if not me:
                cv.tag_bind(tag, "<Button-1>", lambda e, i=chn["id"]: self.insp.app.select(i))
                cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
                cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))


# ----------------------------------------------------------------------------
class ActionsPage(_Page):
    """Events of one widget type: action lists + 'also call my code'."""

    def __init__(self, insp, parent, wtype):
        super().__init__(insp, parent)
        self.wtype = wtype
        self.events = WIDGETS[wtype]["events"]
        self.blocks = []
        c = self.frame
        self.tip = None
        if self.events:
            muted(c, "Actions make your app do things. They run one after the other, "
                     "from top to bottom.", size=9, wrap=272).pack(fill="x", pady=(0, px(8)))
        else:
            card = tk.Frame(c, bg=theme.PANEL, highlightthickness=1,
                            highlightbackground=theme.BORDER)
            card.pack(fill="x", pady=(0, px(8)))
            tk.Label(card, text="💡", bg=theme.PANEL, fg=theme.TEXT, font=font(14)).pack(
                side="left", anchor="n", padx=(px(8), 0), pady=px(8))
            self.tip = tk.Label(card, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                                justify="left", wraplength=px(222), font=font(9))
            self.tip.pack(side="left", fill="x", expand=True, padx=px(8), pady=px(8))
        for key, label, _verb in self.events:
            blk = {"key": key, "label": label}
            head = tk.Frame(c, bg=theme.BG)
            head.pack(fill="x", pady=(px(8), px(4)))
            tk.Label(head, text="⚡ " + label + ":", bg=theme.BG, fg=theme.TEXT, anchor="w",
                     font=font(10, "bold")).pack(side="left")
            blk["note"] = muted(c, "", size=8)
            blk["list"] = dialogs.ActionListEditor(
                c, insp.app.project, [], on_change=insp.actions_changed,
                wraplength=px(196), app=insp.app)
            blk["list"].pack(fill="x", pady=(0, px(4)))
            hook = tk.Frame(c, bg=theme.BG)
            hook.pack(fill="x", pady=(0, px(6)))
            blk["hook_var"] = tk.BooleanVar()
            blk["hook_cb"] = ttk.Checkbutton(hook, text="", variable=blk["hook_var"],
                                             command=lambda k=key: self._hook(k))
            blk["hook_cb"].pack(side="left")
            theme.tooltip(blk["hook_cb"], "Your own Python method runs after the actions "
                                          "above. Write it in your code file – “Copy” puts "
                                          "an empty one on the clipboard.")
            cp = dialogs.small_button(hook, "⧉", lambda k=key: self._copy(k),
                                      "Copy the method to the clipboard", bg=theme.BG)
            cp.pack(side="left", padx=(px(4), 0))
            self.blocks.append(blk)
        self.menu_box = None
        if wtype == "Window":
            self.section(c, "Menu bar")
            self.menu_box = tk.Frame(c, bg=theme.BG)
            self.menu_box.pack(fill="x")
            self.menu_ed = MenuSummaryEd(insp, self.menu_box, {"key": "menu"}, lambda v: None)
        self.used_head = self.section(c, "Used by")
        self.used = tk.Text(c, height=2, wrap="word", relief="flat", bd=0, bg=theme.BG,
                            fg=theme.TEXT, font=font(9), cursor="arrow", highlightthickness=0,
                            padx=0, pady=0)
        self.used.pack(fill="x")
        self.used.tag_configure("muted", foreground=theme.MUTED)
        self.used.tag_configure("link", foreground=theme.ACCENT, font=font(9, "bold"))
        self.used.tag_bind("link", "<Enter>", lambda e: self.used.configure(cursor="hand2"))
        self.used.tag_bind("link", "<Leave>", lambda e: self.used.configure(cursor="arrow"))

    def _hook(self, key):
        node = self.insp.node()
        if node is None:
            return
        blk = next(b for b in self.blocks if b["key"] == key)
        hooks = node.setdefault("hooks", [])
        on = bool(blk["hook_var"].get())
        if on and key not in hooks:
            hooks.append(key)
        elif not on and key in hooks:
            hooks.remove(key)
        name = model.hook_name(node, key)
        self.insp.commit("%s %s() for %s." % ("Will call" if on else "Won't call", name,
                                              node["id"]))

    def _copy(self, key):
        node = self.insp.node()
        if node is None:
            return
        dialogs.copy_to_clipboard(self.frame, dialogs.hook_stub(self.insp.app.project, node, key),
                                  self.insp.app, "Copied %s() – paste it into the class in "
                                                 "your code file." % model.hook_name(node, key))

    def load(self, node):
        project = self.insp.app.project
        nid = node["id"]
        if self.tip is not None:
            text = NO_EVENTS_TEXT.get(self.wtype, "This widget doesn't react to anything by "
                                                  "itself. Other widgets' actions can change it.")
            if "%s" in text:
                text = text % nid
            self.tip.configure(text=text)
        hooks = node.get("hooks", [])
        for blk in self.blocks:
            lst = node["events"].setdefault(blk["key"], [])
            blk["list"].set_actions(project, lst,
                                    context="%s: %s" % (project.display_name(node),
                                                        blk["label"].lower()),
                                    host_id=nid)
            blk["hook_var"].set(blk["key"] in hooks)
            blk["hook_cb"].configure(text="Also call my code:  %s()" % model.hook_name(
                node, blk["key"]))
            note = ""
            if blk["key"] == "on_timer" and not node["props"].get("timer_ms"):
                note = "The timer is off. Set “Timer every” on the Properties tab to use it."
            blk["note"].configure(text=note)
            if note and not blk["note"].winfo_manager():
                blk["note"].pack(fill="x", before=blk["list"], pady=(0, px(4)))
            elif not note and blk["note"].winfo_manager():
                blk["note"].pack_forget()
        if self.menu_box is not None:
            self.menu_ed.load(node["props"].get("menu"), node)
        refs = [n for n in project.references_to(nid) if n["id"] != nid]
        t = self.used
        t.configure(state="normal")
        t.delete("1.0", "end")
        if refs:
            t.insert("end", "Actions of these widgets change or use this one:\n", "muted")
            for n in refs:
                tag = "ref_" + n["id"]
                t.insert("end", "%s  %s" % (WIDGETS[n["type"]]["icon"], project.display_name(n)),
                         ("link", tag))
                t.insert("end", "\n")
                t.tag_bind(tag, "<Button-1>", lambda e, i=n["id"]: self.insp.show_actions(i))
            t.configure(height=len(refs) + 1)
        else:
            t.insert("end", "No other widget's actions use this one.", "muted")
            t.configure(height=1)
        t.configure(state="disabled")


# ----------------------------------------------------------------------------
class CodePage(_Page):
    """Copyable snippets showing how to use the selected widget from Python."""

    def __init__(self, insp, parent):
        super().__init__(insp, parent)
        c = self.frame
        self.intro = muted(c, "", size=9, wrap=272)
        self.intro.pack(fill="x", pady=(0, px(8)))
        mono = (getattr(theme, "MONO_FAMILY", None) or dialogs.MONO[0], 9)
        self.txt = tk.Text(c, wrap="word", relief="flat", bd=0, font=mono, height=20, width=10,
                           bg=dialogs.field_bg(), fg=theme.TEXT, padx=px(8), pady=px(6),
                           highlightthickness=1, highlightbackground=theme.BORDER,
                           insertbackground=theme.TEXT, cursor="arrow",
                           selectbackground=theme.ACCENT, selectforeground="#ffffff")
        self.txt.pack(fill="x")
        dark = getattr(theme, "is_dark", lambda: False)()
        self.txt.tag_configure("comment", foreground=theme.MUTED)
        self.txt.tag_configure("string", foreground="#86efac" if dark else "#15803d")
        self.txt.tag_configure("kw", foreground="#c4b5fd" if dark else "#7c3aed")
        self.txt.tag_configure("head", font=font(9, "bold"), foreground=theme.TEXT,
                               spacing1=px(4))
        self.txt.tag_configure("copy", foreground=theme.ACCENT, font=font(8, "bold"))
        self.txt.tag_bind("copy", "<Enter>", lambda e: self.txt.configure(cursor="hand2"))
        self.txt.tag_bind("copy", "<Leave>", lambda e: self.txt.configure(cursor="arrow"))
        bar = tk.Frame(c, bg=theme.BG)
        bar.pack(fill="x", pady=(px(8), 0))
        ttk.Button(bar, text="⧉ Copy all", style="Small.TButton", command=self.copy_all).pack(
            side="left")
        self.blocks = []

    def load(self, node):
        project = self.insp.app.project
        base = project.module_basename()
        if project.settings.get("code_style", "split") == "split":
            where = "%s.py" % base
            self.intro.configure(text="How to use %s from your own Python code. Put your code "
                                      "in %s (it is never overwritten) – inside the class %s, "
                                      "so “self” is your app." % (node["id"], where,
                                                                  project.class_name()))
        else:
            self.intro.configure(text="How to use %s from Python code inside the app class "
                                      "(“self” is your app)." % node["id"])
        self.blocks = code_snippets(project, node)
        t = self.txt
        t.configure(state="normal")
        t.delete("1.0", "end")
        for i, (title, code) in enumerate(self.blocks):
            if i:
                t.insert("end", "\n")
            t.insert("end", title + "   ", "head")
            tag = "copy%d" % i
            t.insert("end", "Copy", ("copy", tag))
            t.tag_bind(tag, "<Button-1>", lambda e, k=i: self.copy(k))
            t.insert("end", "\n")
            start = t.index("end-1c")
            t.insert("end", code.rstrip("\n") + "\n")
            self._highlight(start)
        lines = int(t.index("end-1c").split(".")[0])
        t.configure(height=min(40, lines), state="disabled")

    def _highlight(self, start):
        t = self.txt
        text = t.get(start, "end-1c")
        base_line = int(start.split(".")[0])
        for ln, line in enumerate(text.split("\n")):
            row = base_line + ln
            for m in re.finditer(r"\b(def|self|return|lambda|import|from|for|in|if|None|True|"
                                 r"False|pass)\b", line):
                t.tag_add("kw", "%d.%d" % (row, m.start()), "%d.%d" % (row, m.end()))
            for m in re.finditer(r"(\"[^\"]*\"|'[^']*')", line):
                t.tag_add("string", "%d.%d" % (row, m.start()), "%d.%d" % (row, m.end()))
            i = _comment_start(line)
            if i is not None:
                t.tag_add("comment", "%d.%d" % (row, i), "%d.end" % row)

    def copy(self, i):
        if 0 <= i < len(self.blocks):
            dialogs.copy_to_clipboard(self.frame, self.blocks[i][1], self.insp.app,
                                      "Copied: %s" % self.blocks[i][0].lower())

    def copy_all(self):
        text = "\n".join("# %s\n%s" % (t, c) for t, c in self.blocks)
        dialogs.copy_to_clipboard(self.frame, text, self.insp.app, "Copied all code examples.")


def _comment_start(line):
    """Index of a # comment outside of strings, or None."""
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return i
    return None


def _set_example(node):
    t = node["type"]
    p = node["props"]
    name = dialogs.get_name(node)
    info = dialogs.value_info(node)
    if t == "Table":
        cols = [c.get("title", "") if isinstance(c, dict) else str(c)
                for c in (p.get("columns") or [])] or ["Value"]
        row1 = ", ".join('"%s"' % (c[:1] or "a") if i == 0 else str(i * 10)
                         for i, c in enumerate(cols))
        return ('self.set("%s", [[%s], [%s]])   # list of rows\n'
                '# rows can also be dicts: [{"%s": …}]' % (
                    name, row1, row1.replace("10", "20"), cols[0]))
    if t in ("Listbox", "Combobox"):
        return ('self.set("%s", ["A", "B", "C"])   # replaces the items\n'
                'self.set("%s", "B")               # selects an item' % (name, name))
    if t == "Console":
        return ('self.log("Started…", "%s")   # add a line\n'
                'self.set("%s", "")            # clear it' % (name, name))
    if t == "Label":
        return 'self.set("%s", f"Result: {value}")' % name
    if t == "Button":
        return 'self.set("%s", "Stop")   # change its text' % name
    if t == "Notebook":
        return 'self.set("%s", "Tab 2")   # show the tab with this title' % name
    if t == "Progressbar":
        return 'self.set("%s", 75)' % name
    if info is None:
        return None
    return 'self.set("%s", %s)' % (name, info[2])


def code_snippets(project, node):
    """[(title, code)] for the Code tab (follows the runtime API of SPEC2 §2.2)."""
    t = node["type"]
    out = []
    nid = node["id"]
    if t == "Window":
        main = node is project.main_window
        if not main:
            out.append(("Open and close it",
                        'self.open_window("%s")\nself.close_window("%s")' % (nid, nid)))
        out.append(("Read all inputs of this window",
                    "data = self.values(%s)   # {name: value}" % (
                        "" if main else 'window="%s"' % nid)))
        out.append(("Show messages",
                    'self.show_info("Done!", "My App")\n'
                    'if self.ask_yes_no("Delete it?", "Please confirm"):\n    ...'))
    info = dialogs.value_info(node)
    name = dialogs.get_name(node)
    if info is not None:
        typ, friendly, _ex = info
        comment = "# %s – %s" % (typ, friendly)
        code = 'value = self.get("%s")   %s' % (name, comment)
        if node["type"] == "Entry" and node["props"].get("value_type") in ("int", "float"):
            code += "\n# (a ValueError with a friendly message if it isn't a number)"
        out.append(("Read its value", code))
    ex = _set_example(node)
    if ex:
        out.append(("Change it", ex))
    for key, label, _verb in WIDGETS[t]["events"]:
        stub = dialogs.hook_stub(project, node, key)
        on = key in node.get("hooks", [])
        out.append(("%s – your method%s" % (label, " (switched on)" if on else ""),
                    "\n".join(ln[4:] for ln in stub.splitlines())))
        ev = dialogs.ON_EVENT_NAMES.get(key)
        if ev and t != "Window":
            out.append(("%s – connect any function" % label,
                        'self.on("%s", "%s", self.my_function)' % (nid, ev)))
    if t == "Button":
        out.append(("Run slow code without freezing the window",
                    "self.run_in_background(\n    my_function, self.get(\"x\"),\n"
                    "    on_done=lambda result: self.set(\"label1\", result),\n"
                    "    sender=\"%s\")   # disabled while it runs" % nid))
    if t != "Window":
        import tkmap
        mod, cls, _style = tkmap.WIDGET_CLASS.get(t, ("tk", "Widget", None))
        real = ("%s: a frame with .entry and .button" % cls) if mod == "composite"             else "%s.%s" % (mod, cls)
        out.append(("The real tkinter widget (advanced)",
                    'w = self.widget("%s")   # %s' % (nid, real)))
    return out


# ============================================================================
# The panel
# ============================================================================

class Inspector(ttk.Frame):
    TABS = (("props", "Properties"), ("layout", "Layout"), ("actions", "Actions"),
            ("code", "Code"))

    def __init__(self, master, app):
        super().__init__(master, width=px(340))
        self.pack_propagate(False)
        self.app = app
        self.node_id = None
        self.more_open = False
        self._tab = "props"
        self._pending = _NOTHING
        self._after_id = None
        self._mode = None           # "node" / "settings"
        self._chrome = None         # header + notebook (built on first node)
        self._settings = None
        self._prop_pages = {}
        self._action_pages = {}
        self._layout_page = None
        self._code_page = None
        self._cur_prop = None
        self._cur_actions = None
        self._stale = set()
        self._loading = False
        self.last_flush_ms = 0.0
        self.pending_name_apply = None

    # ================================================================ public
    def show(self, node_id):
        """Show node_id (None => the App settings page).

        Applied when Tk is idle, so several calls in a row (e.g. commit +
        select) only load once.  Call flush() to force it now."""
        self._pending = node_id
        if self._after_id is None:
            self._after_id = self.after_idle(self.flush)

    def flush(self):
        """Apply a pending show() immediately."""
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._pending is _NOTHING:
            return
        import time
        t0 = time.perf_counter()
        node_id, self._pending = self._pending, _NOTHING
        node = self.app.project.find(node_id) if node_id else None
        if node is None:
            self._show_settings_page()
        else:
            self._show_node(node)
        self.last_flush_ms = (time.perf_counter() - t0) * 1000

    def show_settings(self):
        """Show the App settings page (used by app.open_settings)."""
        self.show(None)
        self.flush()
        if self._settings is not None:
            self._settings.scroll_top()

    def show_actions(self, node_id):
        """Select node_id and switch to its Actions tab."""
        self._tab = "actions"
        if self.app.selected_id != node_id:
            self.app.select(node_id)
        else:
            self.show(node_id)
        self.flush()
        self._select_tab("actions")

    def show_code(self, node_id):
        """Select node_id and switch to its Code tab."""
        self._tab = "code"
        if self.app.selected_id != node_id:
            self.app.select(node_id)
        else:
            self.show(node_id)
        self.flush()
        self._select_tab("code")

    def reset_cache(self):
        """Forget every cached page (e.g. after the editor colours changed)."""
        for w in self.winfo_children():
            w.destroy()
        self._mode = None
        self._chrome = None
        self._settings = None
        self._prop_pages = {}
        self._action_pages = {}
        self._layout_page = None
        self._code_page = None
        self._cur_prop = None
        self._cur_actions = None
        self._stale = set()
        self.node_id = None
        self.pending_name_apply = None

    # ============================================================ helpers
    def node(self):
        return self.app.project.find(self.node_id) if self.node_id else None

    def commit(self, desc, merge_key=None):
        """Commit an edit made here without reloading this panel."""
        self.app.commit(desc, merge_key=merge_key, skip=self)

    def set_prop(self, key, value, label):
        node = self.node()
        if node is None or self._loading or node["props"].get(key) == value:
            return
        node["props"][key] = value
        self.commit("Changed “%s” of %s." % (label, node["id"]), ("prop", node["id"], key))
        if self._cur_prop is not None:
            self._cur_prop.after_change(key, node)
        self._stale.update(("code", "actions"))

    def set_layout(self, key, value, label):
        node = self.node()
        if node is None or self._loading or node["layout"].get(key) == value:
            return
        nid = node["id"]
        node["layout"][key] = value
        project = self.app.project
        parent = project.parent_of(nid)
        if key in ("rowspan", "colspan") and parent is not None:
            project.compact(parent)
        self.commit("Changed “%s” of %s." % (label, nid), ("layout", nid, key))
        if key in ("rowspan", "colspan") and parent is not None:
            if self._layout_page is not None:
                self._layout_page.draw_diagram()
            lay = node["layout"]
            occ = project.occupancy(parent, exclude_id=nid)
            clash = sorted({occ[(r, c)] for r in range(lay["row"], lay["row"] + lay["rowspan"])
                            for c in range(lay["col"], lay["col"] + lay["colspan"])
                            if (r, c) in occ})
            if clash:
                self.app.status("Careful: %s %s in the way, so the widgets overlap. Move %s "
                                "somewhere else (drag it, or use the Move arrows)."
                                % (", ".join(clash), "is" if len(clash) == 1 else "are",
                                   "it" if len(clash) == 1 else "them"))

    def move_widget(self, dr, dc):
        """Move the widget one cell: swap with the neighbour or make a new row/column."""
        project = self.app.project
        node = self.node()
        if node is None:
            return
        nid = node["id"]
        parent = project.parent_of(nid)
        if parent is None or model.layout_mode(parent) != "grid":
            return
        before = project.snapshot()
        lay = node["layout"]
        r, c = lay["row"] + dr, lay["col"] + dc
        if r < 0 or c < 0:
            project.move(nid, parent["id"], max(r, 0), max(c, 0), "above" if r < 0 else "left")
        else:
            other = project.child_at(parent, r, c, exclude_id=nid)
            if other is not None:
                olay = other["layout"]
                lay["row"], olay["row"] = olay["row"], lay["row"]
                lay["col"], olay["col"] = olay["col"], lay["col"]
            else:
                lay["row"], lay["col"] = r, c
            project.compact(parent)
        if project.snapshot() == before:
            self.app.status("%s can't move further that way." % nid)
            return
        names = {(-1, 0): "up", (1, 0): "down", (0, -1): "left", (0, 1): "right"}
        self.app.commit("Moved %s %s." % (nid, names[(dr, dc)]))

    def actions_changed(self, desc):
        node = self.node()
        if node is None:
            return
        self.commit(desc)
        self._stale.add("code")
        self._update_action_count(node)

    def edit_menu(self):
        node = self.node()
        if node is None or node["type"] != "Window":
            return
        nid = node["id"]
        result = dialogs.edit_menu(self, self.app.project, node, app=self.app)
        node = self.app.project.find(nid)
        if result is None or node is None or result == (node["props"].get("menu") or []):
            return
        node["props"]["menu"] = result
        self.app.commit("Changed the menu bar of %s." % nid)

    def toggle_more(self):
        self.more_open = not self.more_open
        for page in self._prop_pages.values():
            page.show_more(self.more_open)

    # ============================================================ settings
    def _show_settings_page(self):
        self.pending_name_apply = None
        self.node_id = None
        if self._settings is None and not self.winfo_ismapped():
            # app start-up: show the window first, build the page right after
            if getattr(self, "_late_settings", None) is None:
                self._late_settings = self.after(120, self._settings_late)
            return
        if self._settings is None:
            import settingspage
            self._settings = settingspage.SettingsPage(self, self.app)
            self._settings.place(x=0, y=0, relwidth=1, relheight=1)
        self._settings.load()
        if self._mode != "settings":
            self._settings.lift()
        self._mode = "settings"

    def _settings_late(self):
        self._late_settings = None
        if self._mode != "node" and self._pending is _NOTHING:
            if not self.winfo_ismapped():
                self._late_settings = self.after(120, self._settings_late)
                return
            self._show_settings_page()

    # ============================================================ node view
    def _build_chrome(self):
        ch = {}
        ch["frame"] = frame = tk.Frame(self, bg=theme.BG)
        head = tk.Frame(frame, bg=theme.PANEL)
        head.pack(side="top", fill="x")
        ch["head"] = head
        tk.Frame(frame, bg=theme.BORDER, height=1).pack(side="top", fill="x")
        top = tk.Frame(head, bg=theme.PANEL)
        top.pack(fill="x", padx=px(12), pady=(px(10), px(4)))
        ch["icon"] = tk.Label(top, text="", bg=theme.ACCENT_LIGHT, fg=theme.ACCENT, width=2,
                              font=font(18))
        ch["icon"].pack(side="left", anchor="n")
        info = tk.Frame(top, bg=theme.PANEL)
        info.pack(side="left", fill="x", expand=True, padx=(px(10), 0))
        ch["kind"] = tk.Label(info, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                              font=font(12, "bold"))
        ch["kind"].pack(fill="x")
        # fixed two-line height: the header must never change size when the
        # selection changes (a size change repaints the whole panel - slow)
        ch["help"] = tk.Label(info, text="", bg=theme.PANEL, fg=theme.MUTED, anchor="nw",
                              justify="left", wraplength=px(232), font=font(9), height=2)
        ch["help"].pack(fill="x")

        loc = tk.Frame(head, bg=theme.PANEL)
        ch["loc"] = loc
        tk.Label(loc, text="Inside:", bg=theme.PANEL, fg=theme.MUTED, font=font(9)).pack(
            side="left")
        ch["parent_link"] = dialogs.link_button(loc, "", lambda: self._select_parent(),
                                                bg=theme.PANEL, size=9,
                                                tip="Select the container this widget is in")
        ch["parent_link"].pack(side="left", padx=(px(4), 0))
        loc.pack(fill="x", padx=px(12))

        row = tk.Frame(head, bg=theme.PANEL)
        row.pack(fill="x", padx=px(12), pady=(px(8), 0))
        ch["name_row"] = row
        name_lbl = tk.Label(row, text="Name", bg=theme.PANEL, fg=theme.TEXT, font=font(9, "bold"))
        name_lbl.pack(side="left")
        ch["name_var"] = tk.StringVar()
        ent = ttk.Entry(row, textvariable=ch["name_var"], width=22)
        ent.pack(side="left", fill="x", expand=True, padx=(px(8), 0))
        tip = ("The name is how actions (and your Python code) refer to this item. "
               "Use letters, numbers and _ only. Press Enter to apply.")
        theme.tooltip(name_lbl, tip)
        theme.tooltip(ent, tip)
        ch["name_ent"] = ent
        ch["err"] = tk.Label(head, text="", bg=theme.PANEL, fg=theme.DANGER, anchor="w",
                             justify="left", wraplength=_wrap(), font=font(9))
        ent.bind("<Return>", self._apply_name)
        ent.bind("<FocusOut>", self._apply_name)
        ent.bind("<Escape>", self._revert_name)
        ent.bind("<Key>", lambda e: self._name_error("") if e.keysym not in ("Return", "Tab")
                 else None, add="+")

        btns = tk.Frame(head, bg=theme.PANEL)
        btns.pack(fill="x", padx=px(12), pady=(px(6), px(10)))
        ch["btns"] = btns
        ch["note"] = tk.Label(btns, text="", bg=theme.PANEL, fg=theme.MUTED, anchor="w",
                              justify="left", wraplength=_wrap(), font=font(9))
        ch["dup"] = ttk.Button(btns, text="⧉ Duplicate", style="Small.TButton",
                               command=self._duplicate)
        ch["del"] = ttk.Button(btns, text="🗑 Delete", style="Small.TButton",
                               command=self._delete)
        ch["code"] = ttk.Button(btns, text="</> Code", style="Small.TButton",
                                command=lambda: self._select_tab("code"))
        theme.tooltip(ch["code"], "How to use this widget from your own Python code")
        ch["dup"].pack(side="left")
        ch["del"].pack(side="left", padx=(px(6), 0))
        ch["code"].pack(side="left", padx=(px(6), 0))

        nb = ttk.Notebook(frame)
        nb.pack(fill="both", expand=True, padx=px(4), pady=(px(6), px(4)))
        ch["nb"] = nb
        ch["tabs"] = {}
        for key, text in self.TABS:
            tab = ttk.Frame(nb)
            nb.add(tab, text=text)
            # the scrollable area is made the first time the tab is loaded
            ch["tabs"][key] = {"tab": tab, "outer": None, "content": None, "shown": True,
                               "stack": None, "key": key}
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        frame.place(x=0, y=0, relwidth=1, relheight=1)
        self._chrome = ch

    def _show_node(self, node):
        if self._chrome is None:
            self._build_chrome()
        ch = self._chrome
        if self._mode != "node":
            ch["frame"].lift()
        self._mode = "node"
        same = node["id"] == self.node_id
        self.node_id = node["id"]
        self._load_header(node)
        # which tabs make sense
        project = self.app.project
        parent = project.parent_of(node["id"])
        has_layout = parent is not None and model.layout_mode(parent) == "grid"
        self._set_tab_shown("layout", has_layout)
        if self._tab == "layout" and not has_layout:
            self._tab = "props"
        self._update_action_count(node)
        self._stale = {"props", "layout", "actions", "code"}
        if not same:
            for t in ch["tabs"].values():
                try:
                    if t["outer"] is not None:
                        t["outer"].canvas.yview_moveto(0)
                except tk.TclError:
                    pass
        self._select_tab(self._tab)
        self._load_tab(self._tab)

    def _set_tab_shown(self, key, on):
        t = self._chrome["tabs"][key]
        if t["shown"] == on:
            return
        t["shown"] = on
        if on:
            self._chrome["nb"].add(t["tab"])          # re-shows a hidden tab in place
        else:
            self._chrome["nb"].hide(t["tab"])

    def _update_action_count(self, node):
        n = sum(len(v) for v in node.get("events", {}).values())
        if node["type"] == "Window":
            n += sum(1 for m in node["props"].get("menu") or [] for it in m.get("items", [])
                     if it.get("actions"))
        text = "Actions" + (" (%d)" % n if n else "")
        try:
            if self._chrome["nb"].tab(self._chrome["tabs"]["actions"]["tab"], "text") != text:
                self._chrome["nb"].tab(self._chrome["tabs"]["actions"]["tab"], text=text)
        except tk.TclError:
            pass

    def _select_tab(self, key):
        if key in dict(self.TABS):
            self._tab = key
        if self._chrome is None:
            return
        t = self._chrome["tabs"].get(key)
        if t is None or not t["shown"]:
            return
        try:
            if self._chrome["nb"].select() != str(t["tab"]):
                self._chrome["nb"].select(t["tab"])
        except tk.TclError:
            pass

    def _on_tab_changed(self, _e=None):
        try:
            cur = self._chrome["nb"].select()
        except tk.TclError:
            return
        for key, t in self._chrome["tabs"].items():
            if str(t["tab"]) == cur:
                self._tab = key
                if key in self._stale:
                    self._load_tab(key)

    def _load_tab(self, key):
        node = self.node()
        if node is None:
            return
        self._stale.discard(key)
        tab = self._ensure_tab(key)
        self._loading = True
        try:
            if key in ("props", "actions"):
                page = self._page_for(key, node["type"])
                page.load(node)
                self._swap(tab, "_cur_prop" if key == "props" else "_cur_actions", page)
            elif key == "layout":
                if self._layout_page is None:
                    self._layout_page = LayoutPage(self, tab["content"], tab["outer"].canvas)
                    self._layout_page.frame.pack(fill="x")
                self._layout_page.load(node)
            elif key == "code":
                if self._code_page is None:
                    self._code_page = CodePage(self, tab["content"])
                    self._code_page.frame.pack(fill="x")
                self._code_page.load(node)
        finally:
            self._loading = False

    def _ensure_tab(self, key):
        """Create a tab's scrollable area the first time it's needed."""
        tab = self._chrome["tabs"][key]
        if tab["outer"] is None:
            outer, inner = theme.scrollable(tab["tab"], bg=theme.BG)
            outer.pack(fill="both", expand=True)
            content = tk.Frame(inner, bg=theme.BG)
            content.pack(fill="both", expand=True, padx=px(12), pady=(px(10), px(16)))
            tab.update(outer=outer, content=content,
                       stack=_Stack(content, outer.canvas) if key in ("props", "actions")
                       else None)
        return tab

    def _page_for(self, key, wtype, behind=False):
        """The cached Properties/Actions page for a widget type (built on demand).
        behind=True builds it underneath the visible page (pre-building)."""
        pages = self._prop_pages if key == "props" else self._action_pages
        page = pages.get(wtype)
        if page is None:
            tab = self._ensure_tab(key)
            if key == "props":
                page = PropPage(self, tab["stack"].frame, wtype, tab["outer"].canvas)
            else:
                page = ActionsPage(self, tab["stack"].frame, wtype)
            tab["stack"].add(page.frame)
            if behind:
                if tab["stack"].cur is not None:
                    page.frame.lower(tab["stack"].cur)
                # grow the stack now (idle time) rather than on first display
                page.frame.after_idle(lambda f=page.frame, st=tab["stack"]: st.grow(f))
            pages[wtype] = page
        return page

    # Building a page costs 0.3-1.5 s on slow machines, so the pages of the
    # most used widget types are built in the background while the user isn't
    # doing anything (checked every PREBUILD_TICK ms; one page per tick).
    PREBUILD_ORDER = ["Label", "Button", "Entry", "Window", "Frame", "LabelFrame",
                      "Combobox", "Checkbutton", "Spinbox", "Text", "Table", "Console",
                      "Radiobutton", "Listbox", "Scale", "FilePicker", "Notebook", "Tab",
                      "Progressbar", "Separator", "Canvas"]
    PREBUILD_TICK = 400
    PREBUILD_QUIET = 1.5     # seconds without mouse/keyboard input

    def start_prebuild(self):
        import time
        self._last_input = time.monotonic()

        def touched(_e=None):
            self._last_input = time.monotonic()
        for seq in ("<Motion>", "<KeyPress>", "<ButtonPress>"):
            self.bind_all(seq, touched, add="+")
        self.after(self.PREBUILD_TICK, self._prebuild_tick)

    def _prebuild_tick(self):
        import time
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        todo = [(k, t) for t in self.PREBUILD_ORDER for k in ("props", "actions")
                if t in WIDGETS and (k == "props" or WIDGETS[t]["events"])
                and t not in (self._prop_pages if k == "props" else self._action_pages)]
        if not todo:
            return
        quiet = time.monotonic() - getattr(self, "_last_input", 0) >= self.PREBUILD_QUIET
        if quiet and self.winfo_ismapped()                 and getattr(self.app.workplane, "_drag", None) is None:
            key, wtype = todo[0]
            try:
                if self._chrome is None:
                    self._build_chrome()
                    if self._mode == "settings" and self._settings is not None:
                        self._settings.lift()      # keep the settings page on top
                self._page_for(key, wtype, behind=True)
            except Exception:            # noqa: BLE001 - never disturb the user
                self.PREBUILD_ORDER = [t for t in self.PREBUILD_ORDER if t != wtype]
        self.after(self.PREBUILD_TICK, self._prebuild_tick)

    def _swap(self, tab, attr, page):
        setattr(self, attr, page)
        if tab["stack"].show(page.frame):
            try:
                tab["outer"].canvas.yview_moveto(0)
            except tk.TclError:
                pass

    # ---------------------------------------------------------------- header
    def _load_header(self, node):
        ch = self._chrome
        project = self.app.project
        spec = WIDGETS[node["type"]]
        ch["icon"].configure(text=spec["icon"])
        ch["kind"].configure(text=_friendly_type(node, project))
        ch["help"].configure(text=spec["help"])
        parent = project.parent_of(node["id"])
        # only texts and states change here - never the layout (see _build_chrome)
        if parent is not None:
            link = "%s  ↰" % project.display_name(parent)
        elif node is project.main_window:
            link = "nothing - this window opens when your app starts"
        else:
            link = "nothing - a separate window"
        if ch["parent_link"].cget("text") != link:
            ch["parent_link"].configure(text=link)
        self._name_for = node["id"]
        if ch["name_var"].get() != node["id"]:
            ch["name_var"].set(node["id"])
        self._name_error("")
        self.pending_name_apply = self._apply_name
        is_window = node["type"] == "Window"
        ch["dup"].state(["disabled"] if is_window else ["!disabled"])
        ch["del"].state(["disabled"] if node is project.main_window else ["!disabled"])
        del_text = "🗑 Delete window" if is_window else "🗑 Delete"
        if ch["del"].cget("text") != del_text:
            ch["del"].configure(text=del_text)

    def _select_parent(self):
        parent = self.app.project.parent_of(self.node_id) if self.node_id else None
        if parent is not None:
            self.app.select(parent["id"])

    def _name_error(self, text):
        err = self._chrome["err"]
        if err.cget("text") != text:
            err.configure(text=text)
        if text and not err.winfo_manager():
            err.pack(fill="x", padx=px(12), pady=(px(4), 0), after=self._chrome["name_row"])
        elif not text and err.winfo_manager():
            err.pack_forget()

    def _apply_name(self, _e=None):
        ch = self._chrome
        nid = getattr(self, "_name_for", None)
        if ch is None or nid is None or nid != self.node_id:
            return
        try:
            new = ch["name_var"].get().strip()
        except tk.TclError:
            return
        if new == nid:
            if ch["name_var"].get() != nid:
                ch["name_var"].set(nid)
            return
        project = self.app.project
        try:
            project.rename(nid, new)
        except ValueError as e:
            self._name_error("⚠ " + str(e))
            ch["name_var"].set(nid)
            return
        except Exception as e:  # noqa: BLE001 - never crash the editor
            self._name_error("⚠ Couldn't rename: %s" % e)
            ch["name_var"].set(nid)
            return
        self._name_for = new
        self.node_id = new
        _after_rename(self.app, nid, new)

    def _revert_name(self, _e=None):
        nid = getattr(self, "_name_for", None)
        if nid:
            self._chrome["name_var"].set(nid)
        self._name_error("")
        return "break"

    def _delete(self):
        nid = self.node_id
        if nid and self.app.selected_id != nid:
            self.app.select(nid)
        self.app.delete_selected()

    def _duplicate(self):
        nid = self.node_id
        if nid and self.app.selected_id != nid:
            self.app.select(nid)
        self.app.duplicate_selected()
