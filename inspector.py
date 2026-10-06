"""
Inspector panel: shows and edits the selected widget.

Header (icon, kind, Name) + tabs Properties / Layout / Actions.  Changes are
applied live through app.commit(..., merge_key=("prop", id, key), skip=self)
so the design surface updates while typing without rebuilding this panel.
"""

import os
import tkinter as tk
from tkinter import ttk, filedialog

import model
from model import WIDGETS, LAYOUT_SPECS, FONT_FAMILIES
import theme
import dialogs

PANEL_WIDTH = 340
_NOTHING = object()     # "no pending show()"
WRAP = 270          # wraplength for explanatory text

# What to say on the Actions tab of widgets that have no events.
NO_EVENTS_TEXT = {
    "Label": "Labels don't react to clicks.\n\nTo change what this label says, give a "
             "button (or another widget) a “Set the text / value of a widget” action "
             "and choose this label.",
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
    "Progressbar": "Progress bars don't react to clicks.\n\nTo move the bar, give a button a "
                   "“Set the text / value” or “Add to a number” action and choose this bar.",
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


class Inspector(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master, width=PANEL_WIDTH)
        self.pack_propagate(False)
        self.app = app
        self.node_id = None
        self._snap = None
        self._built = False
        self._tab = "props"            # key of the tab the user looks at
        self._tabs = {}                # key -> (tab frame, scroll outer)
        self.nb = None
        self._pending = _NOTHING
        self._after_id = None

        self.header = tk.Frame(self, bg=theme.PANEL, highlightthickness=0)
        self.header.pack(side="top", fill="x")
        tk.Frame(self, bg=theme.BORDER, height=1).pack(side="top", fill="x")
        self.body = ttk.Frame(self)
        self.body.pack(side="top", fill="both", expand=True)

    # ================================================================ public
    def show(self, node_id):
        """Rebuild the editors for node_id (None => getting-started page).

        The rebuild happens when Tk is idle, so several calls in a row (e.g.
        commit + select) only rebuild once.  Call flush() to force it now."""
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
        node_id, self._pending = self._pending, _NOTHING
        project = self.app.project
        node = project.find(node_id) if node_id else None
        if node is None:
            node_id = None
        snap = project.snapshot()
        if self._built and node_id == self.node_id and snap == self._snap:
            return
        same = self._built and node_id == self.node_id
        scroll = self._scroll_positions() if same else {}
        self.node_id = node_id
        self._snap = snap
        self._build(node)
        self._built = True
        self._restore(scroll)

    def show_actions(self, node_id):
        """Select node_id and switch to its Actions tab."""
        self._tab = "actions"
        if self.app.selected_id != node_id:
            self.app.select(node_id)
        else:
            self.show(node_id)
        self.flush()
        self._select_tab("actions")

    # ============================================================ internals
    def _node(self, nid=None):
        nid = nid or self.node_id
        return self.app.project.find(nid) if nid else None

    def _commit(self, desc, merge_key=None):
        """Commit an edit made here without rebuilding this panel."""
        self.app.commit(desc, merge_key=merge_key, skip=self)
        self._snap = self.app.project.snapshot()

    def _commit_rebuild(self, desc):
        """Commit a structural change; the inspector is rebuilt too."""
        self.app.commit(desc)

    def _scroll_positions(self):
        out = {}
        for key, (_frame, outer) in self._tabs.items():
            if outer is None:
                continue
            try:
                out[key] = outer.canvas.yview()[0]
            except tk.TclError:
                pass
        return out

    def _restore(self, scroll):
        self._select_tab(self._tab)
        if not scroll:
            return
        tabs = dict(self._tabs)

        def apply():
            try:
                self.update_idletasks()     # sizes must be known before scrolling
            except tk.TclError:
                return
            for key, frac in scroll.items():
                if key in tabs and tabs[key] is self._tabs.get(key) and tabs[key][1] is not None:
                    try:
                        c = tabs[key][1].canvas
                        c.configure(scrollregion=c.bbox("all"))
                        c.yview_moveto(frac)
                    except tk.TclError:
                        pass
        self.after_idle(apply)

    def _select_tab(self, key):
        if key in ("props", "layout", "actions"):
            self._tab = key         # remembered even if the page is rebuilt later
        if self.nb is not None and key in self._tabs:
            self._ensure_built(key)
            try:
                self.nb.select(self._tabs[key][0])
            except tk.TclError:
                pass

    def _on_tab_changed(self, _e=None):
        try:
            cur = self.nb.select()
        except tk.TclError:
            return
        for key, (frame, _o) in self._tabs.items():
            if str(frame) == cur:
                self._tab = key
                self._ensure_built(key)

    def _ensure_built(self, key):
        """Tabs are filled in the first time they are shown (faster)."""
        entry = self._tabs.get(key)
        if entry is None or entry[1] is not None or entry[0] is None:
            return
        builder, node = self._lazy.pop(key)
        if self.app.project.find(node["id"]) is not node:
            return
        outer, content = self._scroll_area(entry[0])
        outer.pack(fill="both", expand=True)
        entry[1] = outer
        self._cur_outer = outer
        builder(node, content)

    def _clear(self):
        for w in self.header.winfo_children():
            w.destroy()
        for w in self.body.winfo_children():
            w.destroy()
        self._tabs = {}
        self._lazy = {}
        self.nb = None

    def _scroll_area(self, parent):
        outer, inner = theme.scrollable(parent)
        outer.canvas.configure(width=PANEL_WIDTH - 30)
        content = tk.Frame(inner, bg=theme.BG)
        content.pack(fill="both", expand=True, padx=12, pady=(10, 16))
        return outer, content

    def _no_wheel(self, widget, outer):
        """Stop the mouse wheel changing comboboxes/spinboxes – scroll instead."""
        def wheel(event):
            c = outer.canvas
            box = c.bbox("all")
            if box and c.winfo_height() < box[3]:
                c.yview_scroll(int(-event.delta / 120), "units")
            return "break"
        widget.bind("<MouseWheel>", wheel)

    # ---------------------------------------------------------------- build
    def _build(self, node):
        self._clear()
        if node is None:
            self._build_welcome()
            return
        self._build_header(node)
        self.nb = ttk.Notebook(self.body)
        self.nb.pack(fill="both", expand=True, padx=4, pady=(6, 4))
        tabs = [("props", "Properties", self._build_properties)]
        if node["type"] not in ("Window", "Tab"):
            tabs.append(("layout", "Layout", self._build_layout))
        n_actions = sum(len(v) for v in node.get("events", {}).values())
        label = "Actions" + (" (%d)" % n_actions if n_actions else "")
        tabs.append(("actions", label, self._build_actions))
        self._lazy = {}
        for key, text, builder in tabs:
            frame = ttk.Frame(self.nb)
            self.nb.add(frame, text=text)
            self._tabs[key] = [frame, None]
            self._lazy[key] = (builder, node)
        if self._tab not in self._tabs:
            self._tab = "props"
        self._ensure_built(self._tab)
        self.nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ------------------------------------------------------------ welcome
    def _build_welcome(self):
        h = self.header
        tk.Label(h, text="👋  Welcome to TkBuilder", bg=theme.PANEL, fg=theme.TEXT,
                 font=(theme.UI_FAMILY, 13, "bold"), anchor="w").pack(fill="x", padx=14, pady=(12, 2))
        tk.Label(h, text="Build your own app – no programming needed.", bg=theme.PANEL,
                 fg=theme.MUTED, anchor="w", font=(theme.UI_FAMILY, 9)).pack(fill="x", padx=14, pady=(0, 12))

        outer, c = self._scroll_area(self.body)
        outer.pack(fill="both", expand=True)
        self._tabs = {"welcome": [None, outer]}
        steps = [
            ("➕", "Add widgets", "Drag a widget (Button, Label, Text Box…) from the list on "
                                 "the left onto your window in the middle. Or simply click it."),
            ("👆", "Select it", "Click a widget to select it. Its settings then appear here."),
            ("🎨", "Change it", "Change its text, colours and size on the Properties tab. "
                               "Use the Layout tab to stretch or align it."),
            ("⚡", "Make it do things", "On the Actions tab, add actions – for example "
                                       "“When clicked: show a message”."),
            ("▶", "Try it", "Click “▶ Run My App” (top right) to try your app for real."),
            ("📥", "Get the code", "Click “📥 Get Python Code” to save your app as a Python "
                                  "program you can run or share."),
        ]
        for i, (icon, title, text) in enumerate(steps, 1):
            card = tk.Frame(c, bg=theme.PANEL, highlightthickness=1, highlightbackground=theme.BORDER)
            card.pack(fill="x", pady=(0, 6))
            tk.Label(card, text=icon, bg=theme.ACCENT_LIGHT, fg=theme.ACCENT, width=3,
                     font=(theme.UI_FAMILY, 13)).pack(side="left", fill="y")
            right = tk.Frame(card, bg=theme.PANEL)
            right.pack(side="left", fill="both", expand=True, padx=8, pady=6)
            tk.Label(right, text="%d. %s" % (i, title), bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                     font=(theme.UI_FAMILY, 10, "bold")).pack(fill="x")
            tk.Label(right, text=text, bg=theme.PANEL, fg=theme.MUTED, anchor="w", justify="left",
                     wraplength=WRAP - 50, font=(theme.UI_FAMILY, 9)).pack(fill="x")

        tips = ("Tips:\n•  Made a mistake? Ctrl+Z undoes it.\n"
                "•  Right-click a widget for more options.\n"
                "•  Double-click a button or label to change its text.")
        tk.Label(c, text=tips, bg=theme.BG, fg=theme.MUTED, justify="left", anchor="w",
                 wraplength=WRAP, font=(theme.UI_FAMILY, 9)).pack(fill="x", pady=(8, 10))

        project = self.app.project
        n_widgets = len(project.nodes_of_types())
        n_windows = len(project.windows)
        tk.Label(c, text="Your app has %d window%s and %d widget%s." % (
            n_windows, "" if n_windows == 1 else "s", n_widgets, "" if n_widgets == 1 else "s"),
            bg=theme.BG, fg=theme.TEXT, anchor="w", font=(theme.UI_FAMILY, 9)).pack(fill="x")
        ttk.Button(c, text="▣  Window settings (title, size…)",
                   command=lambda: self.app.select(project.main_window["id"])).pack(anchor="w", pady=(6, 0))

    # ------------------------------------------------------------- header
    def _build_header(self, node):
        project = self.app.project
        h = self.header
        spec = WIDGETS[node["type"]]
        top = tk.Frame(h, bg=theme.PANEL)
        top.pack(fill="x", padx=12, pady=(10, 4))
        tk.Label(top, text=spec["icon"], bg=theme.ACCENT_LIGHT, fg=theme.ACCENT, width=2,
                 font=(theme.UI_FAMILY, 18)).pack(side="left", anchor="n")
        info = tk.Frame(top, bg=theme.PANEL)
        info.pack(side="left", fill="x", expand=True, padx=(10, 0))
        tk.Label(info, text=_friendly_type(node, project), bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                 font=(theme.UI_FAMILY, 12, "bold")).pack(fill="x")
        tk.Label(info, text=spec["help"], bg=theme.PANEL, fg=theme.MUTED, anchor="w", justify="left",
                 wraplength=WRAP - 40, font=(theme.UI_FAMILY, 9)).pack(fill="x")

        # where is it?
        parent = project.parent_of(node["id"])
        if parent is not None and parent["type"] != "Window":
            loc = tk.Frame(h, bg=theme.PANEL)
            loc.pack(fill="x", padx=12)
            tk.Label(loc, text="Inside:", bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.UI_FAMILY, 9)).pack(side="left")
            dialogs.link_button(loc, "%s  ↰" % project.display_name(parent),
                                lambda pid=parent["id"]: self.app.select(pid), bg=theme.PANEL,
                                tip="Select the container this widget is in").pack(side="left", padx=(4, 0))

        # name
        row = tk.Frame(h, bg=theme.PANEL)
        row.pack(fill="x", padx=12, pady=(8, 0))
        name_lbl = tk.Label(row, text="Name", bg=theme.PANEL, fg=theme.TEXT,
                            font=(theme.UI_FAMILY, 9, "bold"))
        name_lbl.pack(side="left")
        tip = ("The name is how actions (and the Python code) refer to this %s. "
               "Use letters, numbers and _ only. Press Enter to apply." %
               ("window" if node["type"] == "Window" else "widget"))
        theme.tooltip(name_lbl, tip)
        nid = node["id"]
        var = tk.StringVar(value=nid)
        ent = ttk.Entry(row, textvariable=var, width=22)
        ent.pack(side="left", fill="x", expand=True, padx=(8, 0))
        theme.tooltip(ent, tip)
        err = tk.Label(h, text="", bg=theme.PANEL, fg=theme.DANGER, anchor="w", justify="left",
                       wraplength=WRAP, font=(theme.UI_FAMILY, 9))

        def show_error(text):
            err.configure(text=text)
            if text:
                err.pack(fill="x", padx=12, pady=(4, 0), after=row)
            else:
                err.pack_forget()

        renamed = []

        def apply(_e=None):
            if renamed:
                return          # already renamed (this header is about to be rebuilt)
            try:
                new = var.get().strip()
            except tk.TclError:
                return
            if nid != self.node_id or new == nid:
                if new == nid and var.get() != nid:
                    var.set(nid)
                return
            project = self.app.project
            try:
                project.rename(nid, new)
            except ValueError as e:
                show_error("⚠ " + str(e))
                var.set(nid)
                return
            except Exception as e:  # noqa: BLE001 - never crash the editor
                show_error("⚠ Couldn't rename: %s" % e)
                var.set(nid)
                return
            renamed.append(new)
            _after_rename(self.app, nid, new)

        def revert(_e=None):
            var.set(nid)
            show_error("")
            return "break"

        ent.bind("<Return>", apply)
        ent.bind("<FocusOut>", apply)
        self.pending_name_apply = apply   # app.select() calls this before switching
        ent.bind("<Escape>", revert)
        ent.bind("<Key>", lambda e: show_error("") if e.keysym not in ("Return", "Tab") else None,
                 add="+")

        # buttons
        btns = tk.Frame(h, bg=theme.PANEL)
        btns.pack(fill="x", padx=12, pady=(4, 10))
        if node["type"] == "Window":
            if node is project.main_window:
                tk.Label(btns, text="This window opens when your app starts.", bg=theme.PANEL,
                         fg=theme.MUTED, font=(theme.UI_FAMILY, 9), anchor="w").pack(fill="x")
            else:
                tk.Label(btns, text="Opened by an “Open another window” action.", bg=theme.PANEL,
                         fg=theme.MUTED, font=(theme.UI_FAMILY, 9), anchor="w", wraplength=WRAP,
                         justify="left").pack(fill="x")
                ttk.Button(btns, text="🗑 Delete this window", style="Danger.TButton",
                           command=lambda: self._delete(nid)).pack(anchor="w", pady=(4, 0))
        else:
            ttk.Button(btns, text="⧉ Duplicate", style="Small.TButton",
                       command=lambda: self._duplicate(nid)).pack(side="left")
            ttk.Button(btns, text="🗑 Delete", style="Small.TButton",
                       command=lambda: self._delete(nid)).pack(side="left", padx=(6, 0))

    def _delete(self, nid):
        if self.app.selected_id != nid:
            self.app.select(nid)
        self.app.delete_selected()

    def _duplicate(self, nid):
        if self.app.selected_id != nid:
            self.app.select(nid)
        self.app.duplicate_selected()

    # ---------------------------------------------------------- sections
    def _section(self, parent, title, first=False):
        f = tk.Frame(parent, bg=theme.BG)
        f.pack(fill="x", pady=(0 if first else 14, 6))
        tk.Label(f, text=title.upper(), bg=theme.BG, fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 8, "bold")).pack(side="left")
        tk.Frame(f, bg=theme.BORDER, height=1).pack(side="left", fill="x", expand=True, padx=(8, 0))
        return f

    def _field(self, parent, label, help_text=""):
        """A labelled field box; returns the frame to put the editor in."""
        box = tk.Frame(parent, bg=theme.BG)
        box.pack(fill="x", pady=(0, 9))
        if label:
            tk.Label(box, text=label, bg=theme.BG, fg=theme.TEXT, anchor="w", justify="left",
                     wraplength=WRAP, font=(theme.UI_FAMILY, 9)).pack(fill="x", pady=(0, 2))
        holder = tk.Frame(box, bg=theme.BG)
        holder.pack(fill="x")
        if help_text:
            tk.Label(box, text=help_text, bg=theme.BG, fg=theme.MUTED, anchor="w", justify="left",
                     wraplength=WRAP, font=(theme.UI_FAMILY, 8)).pack(fill="x", pady=(2, 0))
        return holder

    # ------------------------------------------------------- properties
    def _build_properties(self, node, c):
        nid = node["id"]
        spec = WIDGETS[node["type"]]
        first = True
        if node["type"] == "Notebook":
            self._section(c, "Tabs", first=True)
            self._build_tab_manager(node, c)
            first = False
        if node["type"] == "Tab":
            self._section(c, "Position", first=True)
            self._build_tab_position(node, c)
            first = False

        groups = []
        for p in spec["props"]:
            if p["group"] not in groups:
                groups.append(p["group"])
        for g in groups:
            title = "Menu bar" if g == "Menu" else g
            self._section(c, title, first=first)
            first = False
            for p in spec["props"]:
                if p["group"] != g:
                    continue
                value = node["props"].get(p["key"], p["default"])
                setter = (lambda v, k=p["key"], lab=p["label"]:
                          self._set_prop(nid, k, v, lab))
                self._editor(c, p, value, setter, node)

    def _set_prop(self, nid, key, value, label):
        if nid != self.node_id:
            return
        node = self._node(nid)
        if node is None or node["props"].get(key) == value:
            return
        node["props"][key] = value
        self._commit("Changed “%s” of %s." % (label, nid), ("prop", nid, key))

    def _set_layout(self, nid, key, value, label):
        if nid != self.node_id:
            return
        node = self._node(nid)
        if node is None or node["layout"].get(key) == value:
            return
        node["layout"][key] = value
        if key in ("rowspan", "colspan"):
            parent = self.app.project.parent_of(nid)
            if parent is not None:
                self.app.project.compact(parent)
        self._commit("Changed “%s” of %s." % (label, nid), ("layout", nid, key))
        if key in ("rowspan", "colspan"):
            self._redraw_diagram()
            parent = self.app.project.parent_of(nid)
            if parent is not None:
                lay = node["layout"]
                occ = self.app.project.occupancy(parent, exclude_id=nid)
                clash = sorted({occ[(r, c)] for r in range(lay["row"], lay["row"] + lay["rowspan"])
                                for c in range(lay["col"], lay["col"] + lay["colspan"])
                                if (r, c) in occ})
                if clash:
                    self.app.status("Careful: %s %s in the way, so the widgets overlap. Move %s "
                                    "somewhere else (drag it, or use the Move arrows)."
                                    % (", ".join(clash), "is" if len(clash) == 1 else "are",
                                       "it" if len(clash) == 1 else "them"))

    # ------------------------------------------------------------- editors
    def _editor(self, c, p, value, setter, node=None):
        kind = p["kind"]
        outer = self._cur_outer
        if kind == "bool":
            box = tk.Frame(c, bg=theme.BG)
            box.pack(fill="x", pady=(0, 7))
            var = tk.BooleanVar(value=bool(value))
            cb = ttk.Checkbutton(box, text=p["label"], variable=var,
                                 command=lambda: setter(bool(var.get())))
            cb.pack(anchor="w")
            if p.get("help"):
                tk.Label(box, text=p["help"], bg=theme.BG, fg=theme.MUTED, anchor="w",
                         justify="left", wraplength=WRAP, font=(theme.UI_FAMILY, 8)).pack(fill="x")
            return
        f = self._field(c, "" if kind == "menu" else p["label"], p.get("help", ""))
        if kind == "text":
            var = tk.StringVar(value=value if value is not None else "")
            ent = ttk.Entry(f, textvariable=var)
            ent.pack(fill="x")
            var.trace_add("write", lambda *a: setter(var.get()))
        elif kind in ("multiline", "lines"):
            txt = tk.Text(f, height=3 if kind == "multiline" else 4, width=24, wrap="word",
                          font=(theme.UI_FAMILY, 10), relief="solid", bd=1, undo=True,
                          highlightthickness=1, highlightcolor=theme.ACCENT,
                          highlightbackground=theme.BORDER, padx=4, pady=3)
            if kind == "lines":
                txt.insert("1.0", "\n".join(value or []))
            else:
                txt.insert("1.0", value or "")
            txt.edit_modified(False)
            txt.pack(fill="x")

            def on_mod(_e=None, t=txt, k=kind):
                try:
                    if not t.edit_modified():
                        return
                    t.edit_modified(False)
                    s = t.get("1.0", "end-1c")
                except tk.TclError:
                    return
                if k == "lines":
                    setter([ln for ln in s.split("\n") if ln.strip()])
                else:
                    setter(s)
            txt.bind("<<Modified>>", on_mod)
            # let Tab move focus instead of inserting a tab character
            txt.bind("<Tab>", lambda e: (e.widget.tk_focusNext().focus_set(), "break")[1])
        elif kind in ("int", "float"):
            self._number_editor(f, p, value, setter, kind, outer)
        elif kind == "choice":
            opts = p["options"]
            values = [o[0] for o in opts]
            labels = [o[1] for o in opts]
            cb = ttk.Combobox(f, state="readonly", values=labels,
                              width=max(12, min(28, max(len(s) for s in labels) + 2)))
            cb.pack(anchor="w")
            cb.current(values.index(value) if value in values else 0)
            cb.bind("<<ComboboxSelected>>", lambda e: (setter(values[cb.current()]),
                                                       cb.selection_clear()))
            self._no_wheel(cb, outer)
        elif kind == "color":
            key = p["key"]
            shown_default = "#000000" if key == "fg" else (p["default"] or "SystemButtonFace")
            try:
                self.winfo_rgb(shown_default)
            except tk.TclError:
                shown_default = "#f0f0f0"
            dialogs.ColorPicker(f, value or "", on_change=setter, default=shown_default,
                                quick=True, reset_value=p["default"] or "").pack(anchor="w", fill="x")
        elif kind == "font":
            self._font_editor(f, value, setter, outer)
        elif kind == "image":
            self._image_editor(f, value, setter)
        elif kind == "menu":
            self._menu_summary(f, node)
        else:
            tk.Label(f, text=str(value), bg=theme.BG).pack(anchor="w")

    def _number_editor(self, f, p, value, setter, kind, outer):
        opts = p.get("options") or {}
        lo = opts.get("min", -1e9)
        hi = opts.get("max", 1e9)
        conv = int if kind == "int" else float
        var = tk.StringVar(value=_fmt_num(value))
        sp = ttk.Spinbox(f, from_=lo, to=hi, increment=1, textvariable=var, width=10)
        sp.pack(anchor="w")
        state = {"last": value}

        def parse():
            s = var.get().strip().replace(",", ".")
            try:
                v = float(s)
            except ValueError:
                return None
            if kind == "int":
                if not v.is_integer():
                    return None
                v = int(v)
            return v

        def changed(*_a):
            v = parse()
            if v is None or v < lo or v > hi:
                return            # ignore until valid
            state["last"] = v
            setter(conv(v))

        def normalise(_e=None):
            v = parse()
            if v is None:
                v = state["last"]
            v = max(lo, min(hi, v))
            if kind == "int":
                v = int(v)
            try:
                if var.get() != _fmt_num(v):
                    var.set(_fmt_num(v))
            except tk.TclError:
                pass

        var.trace_add("write", changed)
        sp.bind("<FocusOut>", normalise)
        sp.bind("<Return>", normalise)
        self._no_wheel(sp, outer)

    def _font_editor(self, f, value, setter, outer):
        font = dict(model.default_font())
        font.update(value or {})
        fams = ["(normal)"] + [x for x in FONT_FAMILIES if x]
        if font["family"] and font["family"] not in fams:
            fams.append(font["family"])
        fam = ttk.Combobox(f, state="readonly", values=fams, width=24)
        fam.pack(anchor="w")
        fam.set(font["family"] or "(normal)")
        self._no_wheel(fam, outer)

        row = tk.Frame(f, bg=theme.BG)
        row.pack(fill="x", pady=(5, 0))
        tk.Label(row, text="Size", bg=theme.BG, fg=theme.TEXT, font=(theme.UI_FAMILY, 9)).pack(side="left")
        size_var = tk.StringVar(value=str(int(font.get("size") or 0)))
        sp = ttk.Spinbox(row, from_=0, to=200, increment=1, textvariable=size_var, width=4)
        sp.pack(side="left", padx=(4, 10))
        self._no_wheel(sp, outer)
        theme.tooltip(sp, "Text size in points. 0 = the normal size.")
        style_vars = {}
        for key, text, fnt in (("bold", "B", (theme.UI_FAMILY, 10, "bold")),
                               ("italic", "I", (theme.UI_FAMILY, 10, "italic")),
                               ("underline", "U", (theme.UI_FAMILY, 10, "underline"))):
            v = tk.BooleanVar(value=bool(font.get(key)))
            style_vars[key] = v
            b = tk.Checkbutton(row, text=text, variable=v, indicatoron=False, width=3, font=fnt,
                               relief="flat", offrelief="flat", overrelief="groove", bd=1,
                               bg=theme.PANEL, selectcolor=theme.ACCENT_LIGHT,
                               activebackground=theme.ACCENT_LIGHT, cursor="hand2",
                               command=lambda: push())
            b.pack(side="left", padx=(0, 3))
            theme.tooltip(b, {"bold": "Bold", "italic": "Italic", "underline": "Underline"}[key])
        tk.Label(f, text=FONT_SIZE_HINT, bg=theme.BG, fg=theme.MUTED, anchor="w",
                 font=(theme.UI_FAMILY, 8)).pack(fill="x")

        def push(*_a):
            fam_v = fam.get()
            try:
                size = int(size_var.get())
            except ValueError:
                return
            if size < 0 or size > 200:
                return
            new = {"family": "" if fam_v == "(normal)" else fam_v, "size": size}
            for k, v in style_vars.items():
                new[k] = bool(v.get())
            setter(new)

        fam.bind("<<ComboboxSelected>>", lambda e: (push(), fam.selection_clear()))
        size_var.trace_add("write", push)

    def _image_editor(self, f, value, setter):
        row = tk.Frame(f, bg=theme.BG)
        row.pack(fill="x")
        var = tk.StringVar(value=value or "")
        ent = ttk.Entry(row, textvariable=var)
        ent.pack(side="left", fill="x", expand=True)
        warn = tk.Label(f, text="", bg=theme.BG, fg=theme.DANGER, anchor="w", justify="left",
                        wraplength=WRAP, font=(theme.UI_FAMILY, 8))

        def check():
            path = var.get().strip()
            if path and not os.path.isfile(path):
                warn.configure(text="⚠ This file can't be found.")
                warn.pack(fill="x")
            elif path and not path.lower().endswith((".png", ".gif")):
                warn.configure(text="⚠ Only PNG and GIF pictures work.")
                warn.pack(fill="x")
            else:
                warn.pack_forget()

        def browse():
            init = os.path.dirname(var.get()) if var.get() else None
            path = filedialog.askopenfilename(
                parent=self.winfo_toplevel(), title="Choose a picture", initialdir=init,
                filetypes=[("Pictures", "*.png *.gif"), ("PNG", "*.png"), ("GIF", "*.gif")])
            if path:
                var.set(path)

        def on_write(*_a):
            check()
            setter(var.get().strip())
        var.trace_add("write", on_write)
        ttk.Button(row, text="Browse…", style="Small.TButton", command=browse).pack(side="left", padx=(4, 0))
        dialogs.small_button(row, "×", lambda: var.set(""), "Remove the picture",
                             bg=theme.BG, fg=theme.DANGER).pack(side="left", padx=(2, 0))
        tk.Label(f, text="A PNG or GIF picture. Keep it next to your saved program.",
                 bg=theme.BG, fg=theme.MUTED, anchor="w", justify="left", wraplength=WRAP,
                 font=(theme.UI_FAMILY, 8)).pack(fill="x", pady=(2, 0))
        check()

    # --------------------------------------------------------------- menu
    def _menu_summary(self, f, node):
        menus = node["props"].get("menu") or []
        if menus:
            parts = []
            for m in menus:
                n = sum(1 for it in m.get("items", []) if not it.get("separator"))
                parts.append("%s (%d item%s)" % (m.get("label") or "?", n, "" if n == 1 else "s"))
            text = "Menus: " + ", ".join(parts)
        else:
            text = "This window has no menu bar yet."
        tk.Label(f, text=text, bg=theme.BG, fg=theme.TEXT if menus else theme.MUTED, anchor="w",
                 justify="left", wraplength=WRAP, font=(theme.UI_FAMILY, 9)).pack(fill="x")
        ttk.Button(f, text="☰  Edit menu bar…",
                   command=lambda nid=node["id"]: self._edit_menu(nid)).pack(anchor="w", pady=(4, 0))

    def _edit_menu(self, nid):
        node = self._node(nid)
        if node is None:
            return
        result = dialogs.edit_menu(self, self.app.project, node)
        node = self._node(nid)
        if result is None or node is None:
            return
        if result == (node["props"].get("menu") or []):
            return
        node["props"]["menu"] = result
        self._commit_rebuild("Changed the menu bar of %s." % nid)

    # -------------------------------------------------------- tab manager
    def _build_tab_manager(self, node, c):
        nid = node["id"]
        tabs = node["children"]
        box = tk.Frame(c, bg=theme.BG)
        box.pack(fill="x", pady=(0, 6))
        lb = tk.Listbox(box, height=max(3, min(7, len(tabs))), activestyle="none",
                        relief="solid", bd=1, highlightthickness=0, exportselection=False,
                        selectbackground=theme.ACCENT, selectforeground="white",
                        font=(theme.UI_FAMILY, 10))
        lb.pack(side="left", fill="both", expand=True)
        for t in tabs:
            lb.insert("end", "  %s" % (t["props"].get("text") or t["id"]))
        if tabs:
            cur = None
            wp = getattr(self.app, "workplane", None)
            if wp is not None and hasattr(wp, "current_tab_of"):
                try:
                    cur = wp.current_tab_of(nid)
                except Exception:  # noqa: BLE001
                    cur = None
            idx = next((i for i, t in enumerate(tabs) if t["id"] == cur), 0)
            idx = getattr(self, "_tab_mgr_index", {}).get(nid, idx)
            idx = max(0, min(idx, len(tabs) - 1))
            lb.selection_set(idx)
            lb.see(idx)

        side = tk.Frame(box, bg=theme.BG)
        side.pack(side="left", fill="y", padx=(6, 0))

        def sel_index():
            s = lb.curselection()
            return s[0] if s else None

        def remember(i):
            if not hasattr(self, "_tab_mgr_index"):
                self._tab_mgr_index = {}
            self._tab_mgr_index[nid] = i

        def add():
            t = self.app.project.add_tab(nid)
            remember(len(self._node(nid)["children"]) - 1)
            tab_of = getattr(self.app.workplane, "_tab_of", None)
            if isinstance(tab_of, dict):
                tab_of[nid] = t["id"]       # show the new (empty) tab
            self._commit_rebuild("Added tab “%s”." % t["props"]["text"])

        def rename():
            i = sel_index()
            if i is None:
                return
            tab = self._node(nid)["children"][i]
            new = dialogs.ask_string(self, "Rename tab", "New title for this tab:",
                                     tab["props"].get("text", ""))
            if new is None or new == tab["props"].get("text"):
                return
            tab["props"]["text"] = new
            remember(i)
            self._commit_rebuild("Renamed tab to “%s”." % new)

        def move(delta):
            i = sel_index()
            kids = self._node(nid)["children"]
            if i is None or not (0 <= i + delta < len(kids)):
                return
            self.app.project.move_tab(kids[i]["id"], delta)
            remember(i + delta)
            self._commit_rebuild("Moved a tab.")

        def remove():
            i = sel_index()
            kids = self._node(nid)["children"]
            if i is None:
                return
            tab = kids[i]
            if len(kids) <= 1:
                self.app.status("Tabs need at least one tab.")
                return
            if tab.get("children"):
                from tkinter import messagebox
                if not messagebox.askyesno("Remove tab", "Remove the tab “%s” and everything on it?"
                                           % tab["props"].get("text"), parent=self.winfo_toplevel()):
                    return
            try:
                self.app.project.remove(tab["id"])
            except ValueError as e:
                self.app.status(str(e))
                return
            remember(max(0, i - 1))
            self._commit_rebuild("Removed tab “%s”." % tab["props"].get("text"))

        def open_tab(_e=None):
            i = sel_index()
            if i is not None:
                self.app.select(self._node(nid)["children"][i]["id"])

        def mk(text, cmd, tip):
            b = ttk.Button(side, text=text, style="Small.TButton", command=cmd, width=9)
            b.pack(fill="x", pady=(0, 3))
            theme.tooltip(b, tip)
            return b

        mk("＋ Add", add, "Add a new tab at the end")
        mk("✎ Rename", rename, "Change the title of the selected tab")
        mk("↑ Earlier", lambda: move(-1), "Move the selected tab to the left")
        mk("↓ Later", lambda: move(1), "Move the selected tab to the right")
        mk("✕ Remove", remove, "Delete the selected tab")
        lb.bind("<Double-Button-1>", open_tab)
        lb.bind("<<ListboxSelect>>", lambda e: remember(sel_index() or 0))
        tk.Label(c, text="Double-click a tab to edit it and its contents.", bg=theme.BG,
                 fg=theme.MUTED, anchor="w", font=(theme.UI_FAMILY, 8)).pack(fill="x")

    def _build_tab_position(self, node, c):
        project = self.app.project
        nb = project.parent_of(node["id"])
        if nb is None:
            return
        kids = nb["children"]
        i = next(k for k, t in enumerate(kids) if t["id"] == node["id"])
        tk.Label(c, text="Tab %d of %d in %s." % (i + 1, len(kids), project.display_name(nb)),
                 bg=theme.BG, fg=theme.TEXT, anchor="w", font=(theme.UI_FAMILY, 9)).pack(fill="x")
        row = tk.Frame(c, bg=theme.BG)
        row.pack(fill="x", pady=(4, 0))

        def move(delta):
            project.move_tab(node["id"], delta)
            self._commit_rebuild("Moved tab “%s”." % node["props"].get("text"))

        b1 = ttk.Button(row, text="◀ Move left", style="Small.TButton", command=lambda: move(-1))
        b1.pack(side="left")
        b2 = ttk.Button(row, text="Move right ▶", style="Small.TButton", command=lambda: move(1))
        b2.pack(side="left", padx=(6, 0))
        if i == 0:
            b1.state(["disabled"])
        if i == len(kids) - 1:
            b2.state(["disabled"])
        ttk.Button(row, text="＋ Add tab", style="Small.TButton",
                   command=lambda: self._add_sibling_tab(nb["id"])).pack(side="left", padx=(6, 0))

    def _add_sibling_tab(self, nb_id):
        t = self.app.project.add_tab(nb_id)
        self.app.commit("Added tab “%s”." % t["props"]["text"])
        self.app.select(t["id"])

    # ------------------------------------------------------------- layout
    def _build_layout(self, node, c):
        project = self.app.project
        nid = node["id"]
        parent = project.parent_of(nid)
        if parent is None or parent["type"] == "Notebook":
            tk.Label(c, text="This item has no layout settings.", bg=theme.BG, fg=theme.MUTED).pack(anchor="w")
            return
        lay = node["layout"]
        self._section(c, "Where is it?", first=True)
        tk.Label(c, text="Row %d, column %d of %s." % (lay["row"] + 1, lay["col"] + 1,
                                                       "the window" if parent["type"] == "Window"
                                                       else project.display_name(parent)),
                 bg=theme.BG, fg=theme.TEXT, anchor="w", justify="left", wraplength=WRAP,
                 font=(theme.UI_FAMILY, 9)).pack(fill="x", pady=(0, 6))

        row = tk.Frame(c, bg=theme.BG)
        row.pack(fill="x")
        self._diagram = tk.Canvas(row, width=180, height=110, bg=theme.PANEL, highlightthickness=1,
                                  highlightbackground=theme.BORDER)
        self._diagram.pack(side="left")
        self._diagram_node = nid
        self._draw_diagram()

        pad = tk.Frame(row, bg=theme.BG)
        pad.pack(side="left", padx=(12, 0))
        tk.Label(pad, text="Move", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 8, "bold")).grid(row=0, column=0, columnspan=3)

        def arrow(text, r, col, dr, dc, tip):
            b = tk.Button(pad, text=text, width=3, relief="flat", bg=theme.PANEL, fg=theme.TEXT,
                          activebackground=theme.ACCENT_LIGHT, bd=1, cursor="hand2",
                          font=(theme.UI_FAMILY, 11), highlightthickness=1,
                          highlightbackground=theme.BORDER,
                          command=lambda: self._move(nid, dr, dc))
            b.grid(row=r, column=col, padx=1, pady=1)
            theme.tooltip(b, tip)
        arrow("↑", 1, 1, -1, 0, "Move up (swap with the widget above)")
        arrow("←", 2, 0, 0, -1, "Move left (swap with the widget on the left)")
        arrow("→", 2, 2, 0, 1, "Move right (swap with the widget on the right)")
        arrow("↓", 3, 1, 1, 0, "Move down (swap with the widget below)")

        tk.Label(c, text="Click another box in the picture to select that widget.",
                 bg=theme.BG, fg=theme.MUTED, anchor="w", font=(theme.UI_FAMILY, 8)).pack(fill="x", pady=(3, 0))

        self._section(c, "Size and position in its cell")
        for p in LAYOUT_SPECS:
            setter = (lambda v, k=p["key"], lab=p["label"]: self._set_layout(nid, k, v, lab))
            self._editor(c, p, lay.get(p["key"], p["default"]), setter)

        self._section(c, "How layout works")
        tk.Label(c, justify="left", anchor="w", wraplength=WRAP, bg=theme.BG, fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 9),
                 text="Widgets sit in an invisible grid of rows and columns, a bit like a "
                      "spreadsheet. Drag a widget next to another one to put it beside or "
                      "below it, or use the arrows above.\n\n"
                      "“Stretch” lets a widget grow to fill its cell (and makes that row or "
                      "column grow when the window is resized). “Columns wide” lets one widget "
                      "span several columns, for example a long text box under two buttons.\n\n"
                      "To group widgets, put them in a Panel or Group Box.").pack(fill="x")

    def _draw_diagram(self):
        cv = getattr(self, "_diagram", None)
        if cv is None:
            return
        try:
            cv.delete("all")
        except tk.TclError:
            return
        project = self.app.project
        nid = self._diagram_node
        parent = project.parent_of(nid)
        if parent is None:
            return
        rows, cols = project.grid_size(parent)
        rows, cols = max(rows, 1), max(cols, 1)
        W, H, m = 180, 110, 6
        cw = min(48, (W - 2 * m) / cols)
        ch = min(30, (H - 2 * m) / rows)
        ox = (W - cw * cols) / 2
        oy = (H - ch * rows) / 2
        # empty grid
        for r in range(rows):
            for col in range(cols):
                cv.create_rectangle(ox + col * cw + 1, oy + r * ch + 1, ox + (col + 1) * cw - 1,
                                    oy + (r + 1) * ch - 1, outline="#e5e7eb", dash=(2, 2))
        for chn in parent["children"]:
            lay = chn["layout"]
            x0 = ox + lay["col"] * cw + 2
            y0 = oy + lay["row"] * ch + 2
            x1 = ox + (lay["col"] + lay["colspan"]) * cw - 2
            y1 = oy + (lay["row"] + lay["rowspan"]) * ch - 2
            me = chn["id"] == nid
            tag = "n_" + chn["id"]
            cv.create_rectangle(x0, y0, x1, y1, fill=theme.ACCENT if me else "#e5e7eb",
                                outline=theme.ACCENT if me else "#cbd5e1", tags=(tag,))
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=WIDGETS[chn["type"]]["icon"],
                           fill="white" if me else theme.MUTED, font=(theme.UI_FAMILY, 9),
                           tags=(tag,))
            if not me:
                cv.tag_bind(tag, "<Button-1>", lambda e, i=chn["id"]: self.app.select(i))
                cv.tag_bind(tag, "<Enter>", lambda e: cv.configure(cursor="hand2"))
                cv.tag_bind(tag, "<Leave>", lambda e: cv.configure(cursor=""))

    def _redraw_diagram(self):
        self._draw_diagram()

    def _move(self, nid, dr, dc):
        """Move a widget one cell: swap with the neighbour or shift into a new row/column."""
        project = self.app.project
        node = project.find(nid)
        parent = project.parent_of(nid)
        if node is None or parent is None or parent["type"] == "Notebook":
            return
        before = project.snapshot()
        lay = node["layout"]
        r, c = lay["row"] + dr, lay["col"] + dc
        if r < 0 or c < 0:
            # make a new first row/column
            mode = "above" if r < 0 else "left"
            project.move(nid, parent["id"], max(r, 0), max(c, 0), mode)
        else:
            other = project.child_at(parent, r, c, exclude_id=nid)
            if other is not None:
                # swap places with the neighbour
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
        self._commit_rebuild("Moved %s %s." % (nid, names[(dr, dc)]))

    # ------------------------------------------------------------ actions
    def _build_actions(self, node, c):
        project = self.app.project
        nid = node["id"]
        spec = WIDGETS[node["type"]]
        events = spec["events"]
        if events:
            tk.Label(c, text="Actions make your app do things. They run one after the other, "
                             "from top to bottom.", bg=theme.BG, fg=theme.MUTED, anchor="w",
                     justify="left", wraplength=WRAP, font=(theme.UI_FAMILY, 9)).pack(fill="x", pady=(0, 8))
        else:
            text = NO_EVENTS_TEXT.get(node["type"], "This widget doesn't react to anything by itself.")
            if "%s" in text:
                text = text % nid
            card = tk.Frame(c, bg=theme.PANEL, highlightthickness=1, highlightbackground=theme.BORDER)
            card.pack(fill="x", pady=(0, 8))
            tk.Label(card, text="💡", bg=theme.PANEL, font=(theme.UI_FAMILY, 14)).pack(
                side="left", anchor="n", padx=(8, 0), pady=8)
            tk.Label(card, text=text, bg=theme.PANEL, fg=theme.TEXT, anchor="w", justify="left",
                     wraplength=WRAP - 50, font=(theme.UI_FAMILY, 9)).pack(
                side="left", fill="x", expand=True, padx=8, pady=8)

        for key, label in events:
            lst = node["events"].setdefault(key, [])
            head = tk.Frame(c, bg=theme.BG)
            head.pack(fill="x", pady=(6, 4))
            tk.Label(head, text="⚡ " + label + ":", bg=theme.BG, fg=theme.TEXT, anchor="w",
                     font=(theme.UI_FAMILY, 10, "bold")).pack(side="left")
            dialogs.ActionListEditor(
                c, project, lst,
                on_change=lambda desc, n=nid: self._actions_changed(n, desc),
                context="%s: %s" % (project.display_name(node), label.lower()),
                wraplength=WRAP - 110).pack(fill="x", pady=(0, 8))

        if node["type"] == "Window":
            self._section(c, "Menu bar")
            f = self._field(c, "", "")
            self._menu_summary(f, node)

        refs = [n for n in project.references_to(nid) if n["id"] != nid]
        if refs:
            self._section(c, "Used by")
            tk.Label(c, text="Actions of these widgets change or use this one:", bg=theme.BG,
                     fg=theme.MUTED, anchor="w", font=(theme.UI_FAMILY, 9), wraplength=WRAP,
                     justify="left").pack(fill="x")
            for n in refs:
                dialogs.link_button(c, "%s  %s" % (WIDGETS[n["type"]]["icon"], project.display_name(n)),
                                    lambda i=n["id"]: self.show_actions(i)).pack(anchor="w", pady=(2, 0))

    def _actions_changed(self, nid, desc):
        if nid != self.node_id:
            return
        self._commit(desc)
        # keep the tab title count up to date
        node = self._node(nid)
        if node is not None and "actions" in self._tabs and self.nb is not None:
            n = sum(len(v) for v in node.get("events", {}).values())
            try:
                self.nb.tab(self._tabs["actions"][0], text="Actions" + (" (%d)" % n if n else ""))
            except tk.TclError:
                pass
