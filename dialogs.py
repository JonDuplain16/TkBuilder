"""
Editor dialogs and small reusable editor widgets for TkBuilder.

* ask_string(parent, title, prompt, initial)       -> str or None
* edit_action(parent, project, action=None, ...)   -> action dict or None
* edit_menu(parent, project, window_node)          -> menu list or None
* ColorPicker      swatch + Choose… + reset (+ optional quick colours)
* ActionListEditor readable list of actions with edit / reorder / delete / add
"""

import copy
import os
import sys
import textwrap
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox

import model
from model import ACTIONS, ACTION_CATEGORIES, WIDGETS
import theme

MONO = ("Consolas", 10)

QUICK_COLORS = [
    ("#ffffff", "White"), ("#e5e7eb", "Light grey"), ("#6b7280", "Grey"),
    ("#000000", "Black"), ("#dc2626", "Red"), ("#f97316", "Orange"),
    ("#facc15", "Yellow"), ("#16a34a", "Green"), ("#2563eb", "Blue"),
    ("#7c3aed", "Purple"), ("#db2777", "Pink"), ("#fef3c7", "Cream"),
]

# Short explanations shown in the action editor.
ACTION_HELP = {
    "show_message": "Pops up a small message box with an OK button.",
    "confirm": "Asks a Yes/No question. If the user answers No, the actions "
               "after this one are skipped.",
    "ask_text": "Pops up a box where the user can type an answer. The answer is "
                "put into the widget you choose.",
    "check": "Checks something before going on. If the check fails, the "
             "message is shown and the actions after this one are skipped. "
             "Great for checking that a form is filled in.",
    "set_text": "Replaces the text (or value) of a widget, for example to show a "
                "result in a label.",
    "append_text": "Adds text after what a widget already shows – handy for logs "
                   "or chat-style windows.",
    "change_number": "Adds a number to the value shown in a widget. Use 1 for a "
                     "counter, or -1 to count down.",
    "clear": "Empties a widget, for example a text box after the user pressed Send.",
    "add_item": "Adds a new line to a list or a new choice to a dropdown.",
    "remove_selected": "Removes the item(s) the user has selected in a list.",
    "show_widget": "Makes a widget that was hidden appear again.",
    "hide_widget": "Hides a widget. Use 'Show a hidden widget' to bring it back.",
    "enable_widget": "Makes a widget usable again after it was disabled.",
    "disable_widget": "Greys out a widget so the user can't use it.",
    "set_color": "Changes the text colour and/or background colour of a widget. "
                 "Leave a colour empty to keep it as it is.",
    "open_window": "Opens one of your other windows. If it is already open, it is "
                   "brought to the front.",
    "close_window": "Closes a window. Closing the main window ends the app.",
    "switch_tab": "Shows a particular tab of a Tabs widget.",
    "set_title": "Changes the text in a window's title bar.",
    "choose_file": "Opens the usual 'Open file' / 'Save as' box and puts the "
                   "chosen path into a widget.",
    "load_file": "Reads a text file and shows its contents in a widget.",
    "save_file": "Writes the text of a widget into a file.",
    "quit_app": "Closes the whole app.",
    "run_code": "For people who know Python: runs your own code. "
                "'self' is the app, so self.label1 is the widget called label1.",
}

PLACEHOLDER_TIP = ("Tip: use “Insert value of…” to include what the user typed or "
                   "chose. For example {textbox1} becomes the text in textbox1.")


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------

def _is_color(widget, value):
    try:
        widget.winfo_rgb(value)
        return True
    except tk.TclError:
        return False


def _hex(widget, value):
    """Normalise a colour name to #rrggbb (for the colour chooser)."""
    try:
        r, g, b = widget.winfo_rgb(value)
        return "#%02x%02x%02x" % (r // 256, g // 256, b // 256)
    except tk.TclError:
        return None


def _contrast(widget, colour):
    """Black or white, whichever is readable on `colour`."""
    try:
        r, g, b = (v / 65535 for v in widget.winfo_rgb(colour))
    except tk.TclError:
        return theme.TEXT
    return "#111111" if 0.299 * r + 0.587 * g + 0.114 * b > 0.55 else "#ffffff"


def small_button(parent, text, command, tip=None, bg=theme.PANEL, fg=theme.TEXT, width=2):
    """A flat little icon button (✎ ↑ ↓ ✕ …)."""
    b = tk.Button(parent, text=text, command=command, relief="flat", bd=0, bg=bg, fg=fg,
                  activebackground=theme.ACCENT_LIGHT, activeforeground=theme.TEXT,
                  font=(theme.UI_FAMILY, 10), width=width, cursor="hand2",
                  highlightthickness=0, padx=1, pady=0)
    if tip:
        theme.tooltip(b, tip)
    return b


def link_button(parent, text, command, bg=theme.BG, tip=None):
    """A button that looks like a blue link ("＋ Add action")."""
    b = tk.Label(parent, text=text, fg=theme.ACCENT, bg=bg, cursor="hand2",
                 font=(theme.UI_FAMILY, 10, "bold"))
    b.bind("<Button-1>", lambda e: command())
    b.bind("<Enter>", lambda e: b.configure(fg="#1d4ed8", font=(theme.UI_FAMILY, 10, "bold underline")))
    b.bind("<Leave>", lambda e: b.configure(fg=theme.ACCENT, font=(theme.UI_FAMILY, 10, "bold")))
    if tip:
        theme.tooltip(b, tip)
    return b


def insert_value_menu(parent, project, insert, text="Insert value of… ▾"):
    """A menubutton listing widgets; picking one calls insert('{id}')."""
    mb = ttk.Menubutton(parent, text=text, style="Small.TButton")
    menu = tk.Menu(mb, tearoff=False)
    mb["menu"] = menu
    nodes = project.nodes_of_types(model.INPUT_TYPES + ["Label", "Progressbar", "Button"])
    if not nodes:
        menu.add_command(label="(no widgets yet – add a Text Box first)", state="disabled")
    for n in nodes:
        menu.add_command(label="%s  %s" % (WIDGETS[n["type"]]["icon"], project.display_name(n)),
                         command=lambda i=n["id"]: insert("{%s}" % i))
    theme.tooltip(mb, "Put the current value of another widget into this text. "
                      "It shows up as {name} and is replaced when the action runs.")
    return mb


# ----------------------------------------------------------------------------
# Colour picker
# ----------------------------------------------------------------------------

class ColorPicker(tk.Frame):
    """Swatch + value + Choose… + ×(reset), optionally a row of quick colours.

    on_change(value) is called with "" (default) or "#rrggbb".
    """

    def __init__(self, parent, value="", on_change=None, default=None, quick=True,
                 empty_text="Default", bg=theme.BG, reset_value=""):
        super().__init__(parent, bg=bg)
        self.value = value or ""
        self.on_change = on_change
        self.default = default or ""        # colour used to draw the "auto" swatch
        self.empty_text = empty_text
        self.reset_value = reset_value or ""

        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.swatch = tk.Label(row, width=4, relief="solid", bd=1, cursor="hand2",
                               font=(theme.UI_FAMILY, 7))
        self.swatch.pack(side="left", ipady=2)
        self.swatch.bind("<Button-1>", lambda e: self.choose())
        theme.tooltip(self.swatch, "Click to pick a colour")
        self.text = tk.Label(row, bg=bg, fg=theme.TEXT, anchor="w", width=9,
                             font=(theme.UI_FAMILY, 9))
        self.text.pack(side="left", padx=(6, 4))
        ttk.Button(row, text="Choose…", style="Small.TButton",
                   command=self.choose).pack(side="left")
        rb = small_button(row, "×", self.reset, bg=bg, fg=theme.DANGER,
                          tip="Back to the default colour" if empty_text == "Default"
                          else "Clear (leave the colour as it is)")
        rb.pack(side="left", padx=(4, 0))

        if quick:
            # one canvas for all quick colours (far fewer windows = faster panel)
            size, gap = 17, 4
            qrow = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, cursor="hand2",
                             height=size + 1, width=len(QUICK_COLORS) * (size + gap))
            qrow.pack(anchor="w", pady=(5, 0))
            for i, (col, name) in enumerate(QUICK_COLORS):
                x = i * (size + gap)
                item = qrow.create_rectangle(x, 0, x + size, size, fill=col, outline=theme.BORDER)
                qrow.tag_bind(item, "<Button-1>", lambda e, c=col: self.set(c, notify=True))
            names = [n for _c, n in QUICK_COLORS]
            tip = theme.tooltip(qrow, "", delay=300)

            def hover(e):
                i = e.x // (size + gap)
                text = names[i] if 0 <= i < len(names) and e.x % (size + gap) < size else ""
                if text != tip.text:
                    tip._hide()
                    tip.text = text
                    if text:
                        tip._schedule()
            qrow.bind("<Motion>", hover, add="+")
        self._show()

    def _show(self):
        v = self.value
        if v and _is_color(self, v):
            self.swatch.configure(bg=v, text="")
            self.text.configure(text=v, fg=theme.TEXT)
        elif v:
            self.swatch.configure(bg=theme.PANEL, text="?", fg=theme.DANGER)
            self.text.configure(text=v, fg=theme.DANGER)
        else:
            shown = self.default if self.default and _is_color(self, self.default) else theme.PANEL
            self.swatch.configure(bg=shown, text="auto", fg=_contrast(self, shown))
            self.text.configure(text=self.empty_text, fg=theme.MUTED)

    def set(self, value, notify=False):
        self.value = value or ""
        self._show()
        if notify and self.on_change:
            self.on_change(self.value)

    def choose(self):
        init = _hex(self, self.value) if self.value else (_hex(self, self.default) if self.default else None)
        try:
            res = colorchooser.askcolor(color=init, parent=self.winfo_toplevel(),
                                        title="Pick a colour")
        except tk.TclError:
            return
        if res and res[1]:
            self.set(res[1].lower(), notify=True)

    def reset(self):
        self.set(self.reset_value, notify=True)


# ----------------------------------------------------------------------------
# Modal dialog base
# ----------------------------------------------------------------------------

class _Dialog(tk.Toplevel):
    def __init__(self, parent, title, size=None, resizable=True):
        top = parent.winfo_toplevel()
        super().__init__(top)
        self.withdraw()
        self.title(title)
        self.configure(bg=theme.BG)
        self.transient(top)
        self.resizable(resizable, resizable)
        self._parent_top = top
        self._size = size
        self.result = None
        self._prev_grab = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda e: self.cancel())

    def run(self):
        """Show modally and wait. Returns self.result."""
        self.update_idletasks()
        top = self._parent_top
        w = self._size[0] if self._size else self.winfo_reqwidth()
        h = self._size[1] if self._size else self.winfo_reqheight()
        try:
            px, py = top.winfo_rootx(), top.winfo_rooty()
            pw, ph = top.winfo_width(), top.winfo_height()
            x = px + max(0, (pw - w) // 2)
            y = py + max(0, (ph - h) // 3)
        except tk.TclError:
            x = y = 100
        x = max(0, min(x, self.winfo_screenwidth() - w))
        y = max(0, min(y, self.winfo_screenheight() - h - 40))
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.minsize(min(w, 360), min(h, 200))
        self.deiconify()
        self.lift()
        try:
            self._prev_grab = self.grab_current()
        except (tk.TclError, KeyError):
            self._prev_grab = None
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        self.on_shown()
        if os.environ.get("TKBUILDER_NO_DIALOGS"):
            # test mode: never wait forever for a click. Scripts that drive the
            # dialog with after() still work; anything left open is cancelled.
            secs = float(os.environ.get("TKBUILDER_DIALOG_TIMEOUT", "8"))
            self.after(int(secs * 1000), self._auto_cancel)
        self.wait_window(self)
        return self.result

    def _auto_cancel(self):
        try:
            if self.winfo_exists():
                sys.stderr.write("[silent dialog] auto-cancelled %r\n" % self.title())
                self.cancel()
        except tk.TclError:
            pass

    def on_shown(self):
        pass

    def _close(self):
        prev = self._prev_grab
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
        if prev is not None and prev is not self:
            try:
                if prev.winfo_exists():
                    prev.grab_set()
            except tk.TclError:
                pass

    def ok(self):
        self._close()

    def cancel(self):
        self.result = None
        self._close()

    def button_bar(self, ok_text="OK", cancel_text="Cancel"):
        bar = ttk.Frame(self, padding=(12, 8, 12, 12))
        bar.pack(side="bottom", fill="x")
        self.cancel_btn = ttk.Button(bar, text=cancel_text, command=self.cancel)
        self.cancel_btn.pack(side="right")
        self.ok_btn = ttk.Button(bar, text=ok_text, style="Accent.TButton", command=self.ok)
        self.ok_btn.pack(side="right", padx=(0, 8))
        return bar


# ----------------------------------------------------------------------------
# Ask for a string
# ----------------------------------------------------------------------------

class _AskString(_Dialog):
    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent, title, resizable=False)
        self.button_bar()
        body = ttk.Frame(self, padding=(16, 14, 16, 0))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=prompt, wraplength=320, justify="left").pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        self.entry = ttk.Entry(body, textvariable=self.var, width=40)
        self.entry.pack(fill="x", pady=(8, 0))
        self.bind("<Return>", lambda e: self.ok())

    def on_shown(self):
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def ok(self):
        self.result = self.var.get()
        self._close()


def ask_string(parent, title, prompt, initial=""):
    """Ask the user for one line of text. Returns the text or None."""
    return _AskString(parent, title, prompt, initial).run()


# ----------------------------------------------------------------------------
# Action editor
# ----------------------------------------------------------------------------

def _choices_for(project, param):
    """List of (id, label) a widget/window/tab parameter can refer to."""
    kind, opts = param["kind"], param.get("options") or {}
    if kind == "widget":
        nodes = project.nodes_of_types(opts.get("types"))
        return [(n["id"], "%s  %s" % (WIDGETS[n["type"]]["icon"], project.display_name(n)))
                for n in nodes]
    if kind == "window":
        wins = project.windows if opts.get("main", True) else project.windows[1:]
        out = []
        for i, w in enumerate(project.windows):
            if w in wins:
                label = project.display_name(w)
                if i == 0:
                    label += " – main window"
                out.append((w["id"], label))
        return out
    if kind == "tab":
        return [(n["id"], project.display_name(n)) for n in project.nodes_of_types(["Tab"])]
    return []


def _empty_choice_hint(param):
    kind, opts = param["kind"], param.get("options") or {}
    if kind == "window":
        return ("You don't have another window yet. Click “➕ New Window” in the "
                "toolbar to add one.")
    if kind == "tab":
        return "There are no tabs yet. Add a “Tabs” widget from the palette first."
    types = opts.get("types")
    if types:
        names = sorted({WIDGETS[t]["name"] for t in types})
        if len(names) > 4:
            names = names[:4] + ["…"]
        return "No suitable widget yet. Add one of these first: %s." % ", ".join(names)
    return "There are no widgets yet."


class ActionEditor(_Dialog):
    def __init__(self, parent, project, action=None, context=""):
        super().__init__(parent, "Edit action" if action else "Add an action", size=(820, 540))
        self.project = project
        self.cache = {}          # action type -> params being edited
        self.atype = None
        if action and action.get("type") in ACTIONS:
            self.atype = action["type"]
            params = model.new_action(self.atype)["params"]
            params.update(copy.deepcopy(action.get("params", {})))
            self.cache[self.atype] = params

        self.button_bar()
        if context:
            head = tk.Frame(self, bg=theme.ACCENT_LIGHT)
            head.pack(side="top", fill="x")
            tk.Label(head, text=context, bg=theme.ACCENT_LIGHT, fg=theme.TEXT, anchor="w",
                     font=(theme.UI_FAMILY, 10, "bold"), padx=14, pady=6).pack(fill="x")

        main = ttk.Frame(self, padding=(12, 10, 12, 0))
        main.pack(fill="both", expand=True)

        # ---- left: action types ----
        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="What should happen?", style="Heading.TLabel").pack(anchor="w", pady=(0, 6))
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.types = ttk.Treeview(tf, show="tree", selectmode="browse", height=18)
        self.types.column("#0", width=270)
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.types.yview)
        self.types.configure(yscrollcommand=vsb.set)
        self.types.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.types.tag_configure("cat", font=(theme.UI_FAMILY, 9, "bold"), foreground=theme.MUTED)
        for cat in ACTION_CATEGORIES:
            keys = [k for k, a in ACTIONS.items() if a["category"] == cat]
            if not keys:
                continue
            self.types.insert("", "end", iid="cat:" + cat, text=cat.upper(), open=True, tags=("cat",))
            for k in keys:
                self.types.insert("cat:" + cat, "end", iid=k, text=ACTIONS[k]["name"])
        self.types.bind("<<TreeviewSelect>>", self._on_type)

        # ---- right: form ----
        right = ttk.Frame(main, padding=(16, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.title_lbl = ttk.Label(right, text="", style="Heading.TLabel")
        self.title_lbl.pack(anchor="w")
        self.help_lbl = ttk.Label(right, text="", style="Muted.TLabel", wraplength=470,
                                  justify="left")
        self.help_lbl.pack(anchor="w", pady=(2, 8), fill="x")
        ttk.Separator(right).pack(fill="x")
        self.form = ttk.Frame(right, padding=(0, 8, 0, 0))
        self.form.pack(fill="both", expand=True)

        prev = tk.Frame(right, bg=theme.ACCENT_LIGHT, highlightthickness=1,
                        highlightbackground="#bfdbfe")
        prev.pack(fill="x", pady=(8, 0))
        tk.Label(prev, text="This action will:", bg=theme.ACCENT_LIGHT, fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 8, "bold"), anchor="w", padx=10).pack(fill="x", pady=(5, 0))
        self.preview = tk.Label(prev, text="", bg=theme.ACCENT_LIGHT, fg=theme.TEXT, anchor="w",
                                justify="left", wraplength=460, font=(theme.UI_FAMILY, 10),
                                padx=10)
        self.preview.pack(fill="x", pady=(0, 6))
        self.error = ttk.Label(right, text="", foreground=theme.DANGER, wraplength=470)
        self.error.pack(anchor="w", pady=(4, 0))

        if self.atype:
            self.types.selection_set(self.atype)
            self.types.see(self.atype)
        else:
            self._build_form()

    def on_shown(self):
        if self.atype:
            self.types.see(self.atype)
        self.types.focus_set()

    # ---- form ----
    def _on_type(self, _e=None):
        sel = self.types.selection()
        if not sel:
            return
        iid = sel[0]
        if iid.startswith("cat:"):
            kids = self.types.get_children(iid)
            if kids:
                self.types.selection_set(kids[0])
            return
        if iid != self.atype or not self.form.winfo_children():
            self.atype = iid
            self._build_form()

    def _build_form(self):
        for w in self.form.winfo_children():
            w.destroy()
        self.error.configure(text="")
        if not self.atype:
            self.title_lbl.configure(text="Pick an action")
            self.help_lbl.configure(text="Choose from the list on the left what should happen. "
                                         "You can add several actions; they run one after "
                                         "the other, from top to bottom.")
            self.ok_btn.state(["disabled"])
            self.preview.configure(text="(nothing chosen yet)")
            return
        self.ok_btn.state(["!disabled"])
        spec = ACTIONS[self.atype]
        if self.atype not in self.cache:
            self.cache[self.atype] = model.new_action(self.atype)["params"]
            # keep the chosen target when switching between similar actions
            for other in self.cache.values():
                if other is self.cache[self.atype]:
                    continue
                for p in spec["params"]:
                    if p["kind"] == "widget" and other.get("target") and p["key"] == "target":
                        ids = [i for i, _ in _choices_for(self.project, p)]
                        if other["target"] in ids:
                            self.cache[self.atype]["target"] = other["target"]
        params = self.cache[self.atype]
        self.title_lbl.configure(text=spec["name"])
        self.help_lbl.configure(text=ACTION_HELP.get(self.atype, ""))
        if not spec["params"]:
            ttk.Label(self.form, text="Nothing else to set up – just click OK.",
                      style="Muted.TLabel").pack(anchor="w", pady=4)
        has_text = False
        for p in spec["params"]:
            self._param_editor(p, params)
            has_text = has_text or p["kind"] in ("text", "multiline")
        if has_text:
            ttk.Label(self.form, text=PLACEHOLDER_TIP, style="Muted.TLabel", wraplength=470,
                      justify="left", font=(theme.UI_FAMILY, 8)).pack(anchor="w", pady=(6, 0))
        self._update_preview()

    def _param_editor(self, p, params):
        key, kind = p["key"], p["kind"]
        box = ttk.Frame(self.form)
        box.pack(fill="x", pady=(0, 8))
        ttk.Label(box, text=p["label"], font=(theme.UI_FAMILY, 9, "bold")).pack(anchor="w")

        def changed(value):
            params[key] = value
            self.error.configure(text="")
            self._update_preview()

        if kind in ("widget", "window", "tab"):
            choices = _choices_for(self.project, p)
            ids = [c[0] for c in choices]
            labels = [c[1] for c in choices]
            cur = params.get(key) or ""
            if cur and cur not in ids:
                node = self.project.find(cur)
                ids.append(cur)
                labels.append("%s (doesn't fit here)" % cur if node else "%s (deleted)" % cur)
            cb = ttk.Combobox(box, state="readonly", values=labels, width=42, height=14)
            cb.pack(anchor="w", pady=(2, 0))
            if cur in ids:
                cb.current(ids.index(cur))
            else:
                cb.set("— choose —")
            cb.bind("<<ComboboxSelected>>", lambda e: changed(ids[cb.current()]))
            if not choices:
                ttk.Label(box, text=_empty_choice_hint(p), foreground=theme.DANGER,
                          wraplength=470, justify="left").pack(anchor="w", pady=(2, 0))
        elif kind == "text":
            row = ttk.Frame(box)
            row.pack(fill="x", pady=(2, 0))
            var = tk.StringVar(value=params.get(key, ""))
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True)
            var.trace_add("write", lambda *a: changed(var.get()))
            insert_value_menu(row, self.project,
                              lambda s: (ent.insert("insert", s), ent.focus_set())).pack(
                side="left", padx=(6, 0))
        elif kind in ("multiline", "code"):
            row = ttk.Frame(box)
            row.pack(fill="both", expand=True, pady=(2, 0))
            txt = tk.Text(row, height=10 if kind == "code" else 3, width=40, wrap="word" if kind != "code" else "none",
                          font=MONO if kind == "code" else (theme.UI_FAMILY, 10), relief="solid", bd=1,
                          highlightthickness=1, highlightcolor=theme.ACCENT,
                          highlightbackground=theme.BORDER, undo=True, padx=4, pady=3)
            txt.insert("1.0", params.get(key, ""))
            txt.edit_modified(False)

            def on_mod(_e=None, t=txt):
                if t.edit_modified():
                    t.edit_modified(False)
                    changed(t.get("1.0", "end-1c"))
            txt.bind("<<Modified>>", on_mod)
            if kind == "code":
                txt.bind("<Tab>", lambda e, t=txt: (t.insert("insert", "    "), "break")[1])
                txt.pack(fill="both", expand=True)
            else:
                txt.pack(side="left", fill="x", expand=True)
                insert_value_menu(row, self.project,
                                  lambda s, t=txt: (t.insert("insert", s), t.focus_set())).pack(
                    side="left", padx=(6, 0), anchor="n")
        elif kind == "number":
            var = tk.StringVar(value=_fmt_num(params.get(key, 0)))
            sp = ttk.Spinbox(box, from_=-1e9, to=1e9, increment=1, textvariable=var, width=12)
            sp.pack(anchor="w", pady=(2, 0))

            def num_changed(*_a):
                try:
                    changed(float(var.get()))
                except ValueError:
                    pass
            var.trace_add("write", num_changed)
        elif kind == "choice":
            opts = p["options"]
            values = [o[0] for o in opts]
            cb = ttk.Combobox(box, state="readonly", values=[o[1] for o in opts], width=30)
            cb.pack(anchor="w", pady=(2, 0))
            cur = params.get(key)
            cb.current(values.index(cur) if cur in values else 0)
            if cur not in values:
                params[key] = values[0]
            cb.bind("<<ComboboxSelected>>", lambda e: changed(values[cb.current()]))
        elif kind == "color":
            ColorPicker(box, params.get(key, ""), on_change=changed, quick=True,
                        empty_text="Keep as is").pack(anchor="w", pady=(2, 0))
        else:
            ttk.Label(box, text="(can't edit this)", style="Muted.TLabel").pack(anchor="w")

    def _current(self):
        return {"type": self.atype, "params": copy.deepcopy(self.cache.get(self.atype, {}))}

    def _update_preview(self):
        if not self.atype:
            return
        self.preview.configure(text=model.action_summary(self._current(), self.project))

    def ok(self):
        if not self.atype:
            self.error.configure(text="Please pick an action from the list on the left.")
            return
        act = self._current()
        for p in ACTIONS[self.atype]["params"]:
            if p["kind"] in ("widget", "window", "tab") and not act["params"].get(p["key"]):
                what = {"widget": "a widget", "window": "a window", "tab": "a tab"}[p["kind"]]
                self.error.configure(text="Please choose %s for “%s”." % (what, p["label"]))
                return
            if p["kind"] == "code":
                # checked the way it is used: as the body of an event handler
                # (so e.g. "return" is fine), like codegen.act_run_code does
                code = textwrap.dedent((act["params"].get(p["key"]) or "").expandtabs(4))
                lines = code.strip("\n").splitlines() or ["pass"]
                try:
                    compile("def _check(self, event=None):\n" +
                            "\n".join("    " + l for l in lines), "<your code>", "exec")
                except SyntaxError as e:
                    self.error.configure(text="There is a mistake in the Python code "
                                              "(line %s): %s" % ((e.lineno or 2) - 1, e.msg))
                    return
        self.result = act
        self._close()


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else repr(f)


def action_problem(action, project):
    """A friendly description of what is wrong with an action, or None."""
    spec = ACTIONS.get(action.get("type"))
    if spec is None:
        return "This action is unknown and will be skipped."
    params = action.get("params", {})
    for p in spec["params"]:
        if p["kind"] in ("widget", "window", "tab"):
            v = params.get(p["key"])
            if not v:
                return "“%s” is not chosen yet – click ✎ to fix it." % p["label"]
            if project.find(v) is None:
                return "“%s” no longer exists, so this action will be skipped. " \
                       "Click ✎ to choose another one." % v
    for p in spec["params"]:
        v = params.get(p["key"])
        if p["kind"] in ("text", "multiline") and isinstance(v, str):
            for name in model.PLACEHOLDER_RE.findall(v):
                if project.find(name) is None:
                    return "There is no widget called “%s” any more, so {%s} will be shown " \
                           "as plain text. Click ✎ to fix it." % (name, name)
    return None


def edit_action(parent, project, action=None, context=""):
    """Open the action editor. Returns a new action dict, or None if cancelled."""
    return ActionEditor(parent, project, action, context).run()


# ----------------------------------------------------------------------------
# Action list editor (used by the inspector and the menu editor)
# ----------------------------------------------------------------------------

class ActionListEditor(tk.Frame):
    """Shows a list of actions as sentences with ✎ ↑ ↓ ✕ buttons and
    “＋ Add action”.  The list is edited IN PLACE; on_change(description) is
    called after every change."""

    def __init__(self, parent, project, actions, on_change=None, context="",
                 wraplength=190, bg=theme.BG, empty_text="Nothing happens yet."):
        super().__init__(parent, bg=bg)
        self.project = project
        self.actions = actions
        self.on_change = on_change
        self.context = context
        self.wraplength = wraplength
        self.bgc = bg
        self.empty_text = empty_text
        self.render()

    def render(self):
        for w in self.winfo_children():
            w.destroy()
        if not self.actions:
            tk.Label(self, text=self.empty_text, bg=self.bgc, fg=theme.MUTED, anchor="w",
                     font=(theme.UI_FAMILY, 9, "italic")).pack(fill="x", pady=(0, 4))
        n = len(self.actions)
        for i, act in enumerate(self.actions):
            card = tk.Frame(self, bg=theme.PANEL, highlightthickness=1,
                            highlightbackground=theme.BORDER)
            card.pack(fill="x", pady=(0, 4))
            tk.Label(card, text="%d." % (i + 1), bg=theme.PANEL, fg=theme.MUTED,
                     font=(theme.UI_FAMILY, 9, "bold"), width=2, anchor="ne").pack(
                side="left", anchor="n", padx=(4, 0), pady=5)
            btns = tk.Frame(card, bg=theme.PANEL)
            btns.pack(side="right", anchor="n", padx=2, pady=3)
            small_button(btns, "✎", lambda i=i: self.edit(i), "Change this action").pack(side="left")
            up = small_button(btns, "↑", lambda i=i: self.move(i, -1), "Move up (run earlier)")
            up.pack(side="left")
            dn = small_button(btns, "↓", lambda i=i: self.move(i, 1), "Move down (run later)")
            dn.pack(side="left")
            if i == 0:
                up.configure(state="disabled")
            if i == n - 1:
                dn.configure(state="disabled")
            small_button(btns, "✕", lambda i=i: self.delete(i), "Remove this action",
                         fg=theme.DANGER).pack(side="left")
            summary = model.action_summary(act, self.project)
            lbl = tk.Label(card, text=summary, bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                           justify="left", wraplength=self.wraplength, cursor="hand2",
                           font=(theme.UI_FAMILY, 9))
            lbl.pack(side="left", fill="x", expand=True, padx=(2, 2), pady=5)
            lbl.bind("<Double-Button-1>", lambda e, i=i: self.edit(i))
            problem = action_problem(act, self.project)
            if problem:
                lbl.configure(fg=theme.DANGER, text="⚠ " + summary)
                theme.tooltip(lbl, problem)
        add = link_button(self, "＋ Add action", self.add, bg=self.bgc,
                          tip="Choose something that should happen")
        add.pack(anchor="w", pady=(2, 0))

    def _changed(self, desc):
        self.render()
        if self.on_change:
            self.on_change(desc)

    def add(self):
        act = edit_action(self, self.project, None, self.context)
        if act is not None:
            self.actions.append(act)
            self._changed("Added action: %s" % model.action_summary(act, self.project))

    def edit(self, i):
        act = edit_action(self, self.project, self.actions[i], self.context)
        if act is not None:
            self.actions[i] = act
            self._changed("Changed action: %s" % model.action_summary(act, self.project))

    def move(self, i, delta):
        j = i + delta
        if 0 <= j < len(self.actions):
            self.actions[i], self.actions[j] = self.actions[j], self.actions[i]
            self._changed("Moved an action.")

    def delete(self, i):
        act = self.actions.pop(i)
        self._changed("Removed action: %s" % model.action_summary(act, self.project))


# ----------------------------------------------------------------------------
# Menu bar editor
# ----------------------------------------------------------------------------

class MenuEditor(_Dialog):
    def __init__(self, parent, project, window):
        super().__init__(parent, "Menu bar of %s" % window["id"], size=(800, 540))
        self.project = project
        self.menus = copy.deepcopy(window["props"].get("menu") or [])
        for m in self.menus:
            m.setdefault("label", "Menu")
            m.setdefault("items", [])
            for it in m["items"]:
                if not it.get("separator"):
                    it.setdefault("label", "Item")
                    it.setdefault("actions", [])

        self.button_bar()
        intro = tk.Frame(self, bg=theme.ACCENT_LIGHT)
        intro.pack(side="top", fill="x")
        tk.Label(intro, bg=theme.ACCENT_LIGHT, fg=theme.TEXT, justify="left", anchor="w",
                 padx=14, pady=6, wraplength=760,
                 text="The menu bar sits at the top of the window (File, Edit, Help…). "
                      "Each menu has items; choose what each item does on the right.").pack(fill="x")

        # preview of the bar
        pv = tk.Frame(self, bg=theme.BG)
        pv.pack(side="top", fill="x", padx=12, pady=(10, 0))
        tk.Label(pv, text="Preview:", bg=theme.BG, fg=theme.MUTED,
                 font=(theme.UI_FAMILY, 9)).pack(side="left")
        self.bar = tk.Frame(pv, bg="#ffffff", highlightthickness=1, highlightbackground=theme.BORDER)
        self.bar.pack(side="left", fill="x", expand=True, padx=(6, 0))

        main = ttk.Frame(self, padding=(12, 8, 12, 0))
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", height=14)
        self.tree.column("#0", width=250)
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("menu", font=(theme.UI_FAMILY, 10, "bold"))
        self.tree.tag_configure("sep", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_details())

        b1 = ttk.Frame(left)
        b1.pack(fill="x", pady=(6, 0))
        ttk.Button(b1, text="＋ Menu", style="Small.TButton", command=self.add_menu).pack(side="left")
        ttk.Button(b1, text="＋ Item", style="Small.TButton", command=self.add_item).pack(side="left", padx=4)
        ttk.Button(b1, text="＋ Separator", style="Small.TButton",
                   command=self.add_separator).pack(side="left")
        b2 = ttk.Frame(left)
        b2.pack(fill="x", pady=(4, 0))
        self.up_btn = ttk.Button(b2, text="↑ Up", style="Small.TButton", command=lambda: self.move(-1))
        self.up_btn.pack(side="left")
        self.down_btn = ttk.Button(b2, text="↓ Down", style="Small.TButton", command=lambda: self.move(1))
        self.down_btn.pack(side="left", padx=4)
        self.del_btn = ttk.Button(b2, text="✕ Remove", style="Small.TButton", command=self.remove)
        self.del_btn.pack(side="left")

        self.details = ttk.Frame(main, padding=(16, 0, 0, 0))
        self.details.pack(side="left", fill="both", expand=True)

        self._rebuild_tree()
        if self.menus:
            self._select(("m", 0))
        else:
            self._show_details()

    # ---- tree helpers ----
    @staticmethod
    def _iid(path):
        return "m%d" % path[1] if path[0] == "m" else "m%d_i%d" % (path[1], path[2])

    @staticmethod
    def _path(iid):
        if "_i" in iid:
            a, b = iid[1:].split("_i")
            return ("i", int(a), int(b))
        return ("m", int(iid[1:]))

    def _rebuild_tree(self):
        t = self.tree
        t.delete(*t.get_children(""))
        for mi, m in enumerate(self.menus):
            t.insert("", "end", iid=self._iid(("m", mi)), text="☰  " + (m.get("label") or "(no name)"),
                     open=True, tags=("menu",))
            for ii, it in enumerate(m["items"]):
                if it.get("separator"):
                    text, tags = "─────────────", ("sep",)
                else:
                    n = len(it.get("actions", []))
                    text = "%s%s" % (it.get("label") or "(no name)",
                                     "" if n else "   (does nothing yet)")
                    tags = ()
                t.insert(self._iid(("m", mi)), "end", iid=self._iid(("i", mi, ii)), text=text, tags=tags)
        self._update_bar()

    def _update_bar(self):
        for w in self.bar.winfo_children():
            w.destroy()
        if not self.menus:
            tk.Label(self.bar, text="(no menu bar)", bg="#ffffff", fg=theme.MUTED,
                     padx=6, pady=2).pack(side="left")
        for m in self.menus:
            tk.Label(self.bar, text=m.get("label") or "?", bg="#ffffff", fg=theme.TEXT,
                     padx=8, pady=2).pack(side="left")

    def _select(self, path):
        iid = self._iid(path)
        if self.tree.exists(iid):
            self.tree.selection_set(iid)
            self.tree.see(iid)
            self.tree.focus(iid)
        self._show_details()

    def _selected_path(self):
        sel = self.tree.selection()
        return self._path(sel[0]) if sel else None

    # ---- details pane ----
    def _show_details(self):
        for w in self.details.winfo_children():
            w.destroy()
        path = self._selected_path()
        for b in (self.up_btn, self.down_btn, self.del_btn):
            b.state(["!disabled"] if path else ["disabled"])
        d = self.details
        if path is None:
            ttk.Label(d, text="No menus yet", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(d, text="Click “＋ Menu” to add your first menu (for example “File”), "
                              "then “＋ Item” to add things to click in it.",
                      style="Muted.TLabel", wraplength=440, justify="left").pack(anchor="w", pady=(4, 0))
            return
        if path[0] == "m":
            m = self.menus[path[1]]
            ttk.Label(d, text="Menu", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(d, text="Title shown in the menu bar", font=(theme.UI_FAMILY, 9, "bold")).pack(
                anchor="w", pady=(10, 2))
            var = tk.StringVar(value=m.get("label", ""))
            ent = ttk.Entry(d, textvariable=var, width=34)
            ent.pack(anchor="w")

            def on_label(*_a):
                m["label"] = var.get()
                self.tree.item(self._iid(path), text="☰  " + (var.get() or "(no name)"))
                self._update_bar()
            var.trace_add("write", on_label)
            ttk.Label(d, text="Tip: “＋ Item” adds an item to this menu.", style="Muted.TLabel").pack(
                anchor="w", pady=(10, 0))
            n = len(m["items"])
            ttk.Label(d, text="This menu has %d item%s." % (n, "" if n == 1 else "s"),
                      style="Muted.TLabel").pack(anchor="w")
            return
        it = self.menus[path[1]]["items"][path[2]]
        if it.get("separator"):
            ttk.Label(d, text="Separator", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(d, text="A thin line that splits a menu into groups. It can't be clicked.",
                      style="Muted.TLabel", wraplength=440).pack(anchor="w", pady=(4, 0))
            return
        ttk.Label(d, text="Menu item", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(d, text="Text", font=(theme.UI_FAMILY, 9, "bold")).pack(anchor="w", pady=(10, 2))
        var = tk.StringVar(value=it.get("label", ""))
        ent = ttk.Entry(d, textvariable=var, width=34)
        ent.pack(anchor="w")

        def on_item_label(*_a):
            it["label"] = var.get()
            self._refresh_item_text(path, it)
        var.trace_add("write", on_item_label)

        ttk.Label(d, text="When this item is chosen:", font=(theme.UI_FAMILY, 10, "bold")).pack(
            anchor="w", pady=(14, 4))
        outer, inner = theme.scrollable(d)
        outer.pack(fill="both", expand=True)
        outer.canvas.configure(height=200)
        ActionListEditor(inner, self.project, it.setdefault("actions", []),
                         on_change=lambda desc: self._refresh_item_text(path, it),
                         context="Menu item “%s”: when chosen" % (it.get("label") or ""),
                         wraplength=330).pack(fill="x", padx=(0, 6))

    def _refresh_item_text(self, path, it):
        n = len(it.get("actions", []))
        self.tree.item(self._iid(path), text="%s%s" % (it.get("label") or "(no name)",
                                                       "" if n else "   (does nothing yet)"))

    # ---- operations ----
    def add_menu(self):
        name = ["File", "Edit", "View", "Help"][len(self.menus)] if len(self.menus) < 4 else "Menu"
        path = self._selected_path()
        at = path[1] + 1 if path else len(self.menus)
        self.menus.insert(at, {"label": name, "items": []})
        self._rebuild_tree()
        self._select(("m", at))

    def _target_menu(self, create=True):
        path = self._selected_path()
        if path is None:
            if not self.menus and create:
                self.menus.append({"label": "File", "items": []})
                return 0, 0
            if not self.menus:
                return None
            return len(self.menus) - 1, len(self.menus[-1]["items"])
        if path[0] == "m":
            return path[1], len(self.menus[path[1]]["items"])
        return path[1], path[2] + 1

    def add_item(self):
        mi, at = self._target_menu()
        n = sum(1 for it in self.menus[mi]["items"] if not it.get("separator")) + 1
        self.menus[mi]["items"].insert(at, {"label": "Item %d" % n, "actions": []})
        self._rebuild_tree()
        self._select(("i", mi, at))

    def add_separator(self):
        mi, at = self._target_menu()
        self.menus[mi]["items"].insert(at, {"separator": True})
        self._rebuild_tree()
        self._select(("i", mi, at))

    def move(self, delta):
        path = self._selected_path()
        if path is None:
            return
        if path[0] == "m":
            i, j = path[1], path[1] + delta
            if 0 <= j < len(self.menus):
                self.menus[i], self.menus[j] = self.menus[j], self.menus[i]
                self._rebuild_tree()
                self._select(("m", j))
            return
        mi, ii = path[1], path[2]
        items = self.menus[mi]["items"]
        j = ii + delta
        if 0 <= j < len(items):
            items[ii], items[j] = items[j], items[ii]
            self._rebuild_tree()
            self._select(("i", mi, j))
        elif 0 <= mi + delta < len(self.menus):
            # move into the neighbouring menu
            it = items.pop(ii)
            nm = mi + delta
            dest = self.menus[nm]["items"]
            pos = len(dest) if delta < 0 else 0
            dest.insert(pos, it)
            self._rebuild_tree()
            self._select(("i", nm, pos))

    def remove(self):
        path = self._selected_path()
        if path is None:
            return
        if path[0] == "m":
            m = self.menus[path[1]]
            if m["items"] and not messagebox.askyesno(
                    "Remove menu", "Remove the menu “%s” and all its items?" % m.get("label"),
                    parent=self):
                return
            del self.menus[path[1]]
            self._rebuild_tree()
            if self.menus:
                self._select(("m", min(path[1], len(self.menus) - 1)))
            else:
                self._show_details()
        else:
            items = self.menus[path[1]]["items"]
            del items[path[2]]
            self._rebuild_tree()
            if items:
                self._select(("i", path[1], min(path[2], len(items) - 1)))
            else:
                self._select(("m", path[1]))

    def ok(self):
        self.result = self.menus
        self._close()


def edit_menu(parent, project, window):
    """Open the menu bar editor for a window. Returns the new menu list or None."""
    return MenuEditor(parent, project, window).run()
