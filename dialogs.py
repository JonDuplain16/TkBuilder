"""
Editor dialogs and small reusable editor widgets for TkBuilder.

* ask_string(parent, title, prompt, initial)          -> str or None
* edit_action(parent, project, action=None, ...)      -> action dict or None
* edit_menu(parent, project, window_node, app=None)   -> menu list or None
* ColorPicker      swatch + Choose… + reset (+ optional quick colours)
* ShortcutEntry    click, then press keys ("Ctrl+R")
* ColumnsEditor    Table columns (title / width / alignment)
* RowsEditor       Table starting rows (one row per line, comma separated)
* WidgetListPicker ordered multi-pick of widgets
* ActionListEditor readable list of actions with edit / reorder / delete / add

Helpers shared with the inspector: hook_stub(), value_info(), suggest_widget(),
return_shape(), backend_dirs().

All colours are read from `theme` when a widget is built (the editor has a
light and a dark mode), never stored in module-level constants.
"""

import ast
import copy
import csv
import io
import os
import re
import sys
import textwrap
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox, filedialog

import model
from model import ACTIONS, ACTION_CATEGORIES, WIDGETS
import theme

try:
    import introspect
except ImportError:  # pragma: no cover - shared foundation file
    introspect = None

MONO = ("Consolas", 10) if sys.platform == "win32" else ("Courier", 10)

QUICK_COLORS = [
    ("#ffffff", "White"), ("#e5e7eb", "Light grey"), ("#6b7280", "Grey"),
    ("#000000", "Black"), ("#dc2626", "Red"), ("#f97316", "Orange"),
    ("#facc15", "Yellow"), ("#16a34a", "Green"), ("#2563eb", "Blue"),
    ("#7c3aed", "Purple"), ("#db2777", "Pink"), ("#fef3c7", "Cream"),
]

# Short explanations shown in the action editor.
ACTION_HELP = {
    "call_function": "Runs a function from your own Python file. Pick the file and the "
                     "function, say which widget gives each value, and where the result "
                     "should be shown. TkBuilder only reads your file – nothing is run now.",
    "run_command": "Runs a program, script or command line and shows its output live, "
                   "line by line, while it runs.",
    "run_code": "For people who know Python: runs your own lines of code. 'self' is the "
                "app: self.get('name'), self.set('name', value), self.values() …",
    "show_message": "Pops up a small message box with an OK button.",
    "confirm": "Asks a Yes/No question. If the user answers No, the actions "
               "after this one are skipped.",
    "ask_text": "Pops up a box where the user can type an answer. The answer is "
                "put into the widget you choose.",
    "log": "Adds one line of text to an Output Console (or a text area / list). "
           "Great for progress messages.",
    "check": "Checks something before going on. If the check fails, the "
             "message is shown and the actions after this one are skipped. "
             "Great for checking that a form is filled in.",
    "set_text": "Replaces the text (or value) of a widget, for example to show a "
                "result in a label.",
    "append_text": "Adds text after what a widget already shows – handy for logs.",
    "change_number": "Adds a number to the value shown in a widget. Use 1 for a "
                     "counter, or -1 to count down.",
    "clear": "Empties a widget, for example a text box after the user pressed Send.",
    "reset_form": "Puts every input of a window back to the value it had at the start.",
    "set_checked": "Ticks, unticks or switches a checkbox or option button.",
    "add_item": "Adds a new line to a list or a new choice to a dropdown.",
    "remove_selected": "Removes the item(s) or row(s) the user has selected.",
    "add_row": "Adds a row to a table. Separate the values of the columns with |.",
    "export_table": "Saves all rows of a table to a CSV file (opens in Excel).",
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
    "timer_control": "Starts or stops the timer of a window (set the timer interval in "
                     "the window's properties).",
    "choose_file": "Opens the usual 'Open file' / 'Save as' box and puts the "
                   "chosen path into a widget.",
    "load_file": "Reads a text file and shows its contents in a widget.",
    "save_file": "Writes the text of a widget into a file.",
    "open_url": "Opens a web page in the browser, or a file with its usual program.",
    "quit_app": "Closes the whole app.",
}

PLACEHOLDER_TIP = ("Tip: use “Insert value of…” to include what the user typed or "
                   "chose. For example {textbox1} becomes the text in textbox1.")

# event key -> event name of the generated self.on(name, event, callback) API
ON_EVENT_NAMES = {"command": "click", "on_click": "click", "on_enter": "enter",
                  "on_change": "change", "on_select": "select",
                  "on_double_click": "double_click", "on_tab_change": "tab_change"}


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


def _fmt_num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return str(int(f)) if f.is_integer() else repr(f)


def px(n):
    """Scale a 96-DPI pixel distance for the current screen."""
    f = getattr(theme, "px", None)
    return f(n) if callable(f) else int(n)


def field_bg():
    return getattr(theme, "FIELD", None) or theme.PANEL


def font(size=9, *styles):
    """Editor UI font (family read at call time)."""
    return (theme.UI_FAMILY, size) + ((" ".join(styles),) if styles else ())


def small_button(parent, text, command, tip=None, bg=None, fg=None, width=2):
    """A flat little icon button (✎ ↑ ↓ ✕ …)."""
    bg = theme.PANEL if bg is None else bg
    b = tk.Button(parent, text=text, command=command, relief="flat", bd=0, bg=bg,
                  fg=theme.TEXT if fg is None else fg,
                  activebackground=theme.ACCENT_LIGHT, activeforeground=theme.TEXT,
                  font=font(10), width=width, cursor="hand2",
                  highlightthickness=0, padx=1, pady=0)
    if tip:
        theme.tooltip(b, tip)
    return b


def link_button(parent, text, command, bg=None, tip=None, size=10):
    """A label that behaves like a link ("＋ Add action")."""
    b = tk.Label(parent, text=text, fg=theme.ACCENT, bg=theme.BG if bg is None else bg,
                 cursor="hand2", font=font(size, "bold"))
    b.bind("<Button-1>", lambda e: command())
    b.bind("<Enter>", lambda e: b.configure(font=font(size, "bold", "underline")))
    b.bind("<Leave>", lambda e: b.configure(font=font(size, "bold")))
    if tip:
        theme.tooltip(b, tip)
    return b


def muted(parent, text, bg=None, size=8, wrap=280, **kw):
    """A small grey explanation label."""
    return tk.Label(parent, text=text, bg=theme.BG if bg is None else bg, fg=theme.MUTED,
                    anchor="w", justify="left", wraplength=px(wrap), font=font(size), **kw)


def text_widget(parent, height=3, width=30, mono=False, **kw):
    """A tk.Text styled like the editor's entry fields."""
    return tk.Text(parent, height=height, width=width, font=MONO if mono else font(10),
                   relief="flat", bd=0, undo=True, highlightthickness=1,
                   highlightcolor=theme.ACCENT, highlightbackground=theme.BORDER,
                   bg=theme.PANEL, fg=theme.TEXT, insertbackground=theme.TEXT,
                   selectbackground=theme.ACCENT, selectforeground="#ffffff",
                   padx=5, pady=4, **kw)


def copy_to_clipboard(widget, text, app=None, what="Copied to the clipboard."):
    try:
        widget.clipboard_clear()
        widget.clipboard_append(text)
    except tk.TclError:
        return
    toast = getattr(app, "toast", None) if app is not None else None
    if callable(toast):
        try:
            toast(what)
            return
        except Exception:  # noqa: BLE001 - optional hub feature
            pass
    status = getattr(app, "status", None) if app is not None else None
    if callable(status):
        status(what)


def insert_value_menu(parent, project, insert, text="Insert value of… ▾"):
    """A menubutton listing widgets; picking one calls insert('{id}').
    The list is filled when the menu opens, so it is always up to date."""
    mb = ttk.Menubutton(parent, text=text, style="Small.TButton")
    menu = tk.Menu(mb, tearoff=False)

    def fill():
        menu.delete(0, "end")
        nodes = project.nodes_of_types(model.VALUE_WIDGETS + ["Button"])
        if not nodes:
            menu.add_command(label="(no widgets yet – add a Text Box first)", state="disabled")
        for n in nodes:
            menu.add_command(label="%s  %s" % (WIDGETS[n["type"]]["icon"],
                                               project.display_name(n)),
                             command=lambda i=n["id"]: insert("{%s}" % i))
    menu.configure(postcommand=fill)
    mb["menu"] = menu
    theme.tooltip(mb, "Put the current value of another widget into this text. "
                      "It shows up as {name} and is replaced when the action runs.")
    return mb


def no_wheel(widget, scroll_canvas=None):
    """Stop the mouse wheel changing a combobox/spinbox - scroll the panel instead."""
    def wheel(event):
        c = scroll_canvas
        if c is not None:
            try:
                box = c.bbox("all")
                if box and c.winfo_height() < box[3]:
                    c.yview_scroll(int(-event.delta / 120), "units")
            except tk.TclError:
                pass
        return "break"
    widget.bind("<MouseWheel>", wheel)


# ----------------------------------------------------------------------------
# knowledge about values / hooks (used by the inspector's Code tab too)
# ----------------------------------------------------------------------------

def value_info(node):
    """(python type text, friendly text, example value) of self.get(node) - or
    None when the widget has no value.  Follows SPEC2 §2.2."""
    t = node["type"]
    p = node["props"]
    vt = p.get("value_type", "text")
    typed = {"int": ("int", "a whole number", "42"), "float": ("float", "a decimal number", "3.5"),
             "text": ("str", "the text", '"Hello"')}
    if t in ("Entry", "Combobox"):
        return typed.get(vt, typed["text"])
    if t == "Spinbox":
        return typed["int"] if int(p.get("decimals") or 0) == 0 else typed["float"]
    if t == "Scale":
        res = p.get("resolution") or 1
        try:
            whole = float(res).is_integer()
        except (TypeError, ValueError):
            whole = True
        return ("int", "the number", "50") if whole else ("float", "the number", "2.5")
    if t == "Checkbutton":
        return ("bool", "True when ticked", "True")
    if t == "Radiobutton":
        return ("str", "the chosen option of group “%s”" % (p.get("group") or "group1"),
                '"%s"' % (p.get("value") or p.get("text") or "Option"))
    if t == "Listbox":
        if p.get("selectmode") == "extended":
            return ("list[str]", "the selected items", '["Item 1", "Item 2"]')
        return ("str", "the selected item", '"Item 1"')
    if t == "Table":
        cols = [c.get("title", "") if isinstance(c, dict) else str(c)
                for c in (p.get("columns") or [])] or ["Value"]
        example = "{%s}" % ", ".join('"%s": …' % c for c in cols[:3])
        if p.get("selectmode") == "extended":
            return ("list[dict]", "the selected rows", "[%s]" % example)
        return ("dict | None", "the selected row (None if nothing is selected)", example)
    if t in ("Text", "Console"):
        return ("str", "all its text", '"Some text"')
    if t in ("Label", "Button"):
        return ("str", "its text", '"%s"' % (p.get("text") or "").split("\n")[0][:30])
    if t == "Progressbar":
        return ("float", "the value", "75")
    if t == "FilePicker":
        return ("str", "the chosen path", 'r"C:\\data\\input.csv"')
    if t == "Notebook":
        return ("str", "the title of the visible tab", '"Tab 1"')
    return None


def get_name(node):
    """Name to pass to self.get/set: radio buttons are read by group name."""
    if node["type"] == "Radiobutton":
        return node["props"].get("group") or "group1"
    return node["id"]


def _nearby_inputs(project, node, limit=3):
    win = project.window_of(node["id"]) if node else None
    if win is None:
        return []
    out = []
    for n in project.walk(win):
        if n["type"] in model.INPUT_TYPES and n is not node:
            out.append(n)
        if len(out) >= limit:
            break
    return out


def hook_stub(project, node, event_key):
    """Source of the user's own method for an event, indented for a class body."""
    name = model.hook_name(node, event_key)
    label = next((e[1] for e in WIDGETS[node["type"]]["events"] if e[0] == event_key),
                 event_key)
    lines = ["    def %s(self):" % name,
             '        """%s: %s."""' % (node["id"], label.lower())]
    examples = _nearby_inputs(project, node)
    if examples:
        lines.append("        # Your code here. For example:")
        for n in examples:
            info = value_info(n)
            lines.append('        # %s = self.get("%s")   # %s' % (
                n["id"], get_name(n), info[0] if info else "value"))
    else:
        lines.append("        # Your code here. Read a widget with self.get(\"name\"),")
        lines.append("        # change one with self.set(\"name\", value).")
    lines.append("        pass")
    return "\n".join(lines) + "\n"


def menu_hook_stub(menu_label, item_label):
    name = model.menu_hook_name(menu_label, item_label)
    return ("    def %s(self):\n"
            '        """Menu %s ▸ %s was chosen."""\n'
            "        pass\n" % (name, menu_label, item_label))


# ----------------------------------------------------------------------------
# backend helpers (call_function)
# ----------------------------------------------------------------------------

def project_dir(app):
    """Folder of the saved project file (None while unsaved)."""
    path = getattr(app, "file_path", None) if app is not None else None
    return os.path.dirname(os.path.abspath(path)) if path else None


def store_dir(path, app):
    """A folder for the settings: relative to the project file when tidy."""
    base = project_dir(app)
    if not base:
        return path
    try:
        rel = os.path.relpath(path, base)
    except ValueError:              # another drive
        return path
    return path if rel.startswith(".." + os.sep + "..") else rel.replace("\\", "/")


def _abs_dir(value, app):
    if not value:
        return None
    if os.path.isabs(value):
        return value
    base = project_dir(app) or os.getcwd()
    return os.path.normpath(os.path.join(base, value))


def backend_dirs(project, app=None):
    """Folders where the user's modules are looked for, best first."""
    s = project.settings
    out = []
    for d in (_abs_dir(s.get("backend_dir"), app), project_dir(app),
              _abs_dir(s.get("export_dir"), app)):
        if d and d not in out:
            out.append(d)
    return out


def return_shape(path, func_name):
    """Guess what a function returns by reading its source (never runs it):
    ("dict", [keys]) / ("tuple", n) / ("single", None) / (None, None)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError, ValueError):
        return None, None
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == func_name), None)
    if fn is None:
        return None, None
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
    for r in rets:
        v = r.value
        if isinstance(v, ast.Dict) and v.keys and all(
                isinstance(k, ast.Constant) and isinstance(k.value, str) for k in v.keys):
            return "dict", [k.value for k in v.keys]
        if isinstance(v, ast.Tuple) and len(v.elts) > 1:
            return "tuple", len(v.elts)
    ann = ast.unparse(fn.returns) if fn.returns is not None else ""
    m = re.match(r"(?:typing\.)?[Tt]uple\[(.*)\]$", ann)
    if m and "..." not in m.group(1):
        n = len([x for x in m.group(1).split(",") if x.strip()])
        if n > 1:
            return "tuple", n
    if rets or ann:
        return "single", None
    return None, None


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


_ID_NOISE = re.compile(r"^(txt|text|entry|input|in|box|field|ent|spin|num|cb|combo|"
                       r"dropdown|textbox|numberbox|slider|check|checkbox|chk)_|"
                       r"_(txt|text|entry|input|in|box|field|ent|spin|num|cb|combo|"
                       r"dropdown|textbox|numberbox|slider|check|checkbox|chk)$|\d+$")


def _label_beside(project, node):
    """Text of a Label just left of (or above) node in the same container."""
    parent = project.parent_of(node["id"])
    if parent is None or model.layout_mode(parent) != "grid":
        return ""
    lay = node["layout"]
    for ch in parent["children"]:
        if ch["type"] != "Label":
            continue
        cl = ch["layout"]
        if (cl["row"] == lay["row"] and cl["col"] + cl["colspan"] == lay["col"]) or \
                (cl["col"] == lay["col"] and cl["row"] + cl["rowspan"] == lay["row"]):
            return ch["props"].get("text", "")
    return ""


def suggest_widget(project, arg_name, candidates):
    """Best widget for a function argument, matched by name.
    Returns (widget id or None, reason)."""
    a = _norm(arg_name)
    if not a:
        return None, ""
    best, best_score, reason = None, 0, ""
    for n in candidates:
        nid = n["id"]
        score, why = 0, ""
        stripped = _norm(_ID_NOISE.sub("", nid.lower()))
        label = _norm(re.sub(r"\(.*?\)|\[.*?\]", "", _label_beside(project, n)))
        if nid == arg_name:
            score, why = 100, "same name"
        elif _norm(nid) == a:
            score, why = 95, "same name"
        elif stripped and stripped == a:
            score, why = 90, "similar name"
        elif label and (label == a or label == _norm(arg_name.split("_")[0])
                        and len(arg_name.split("_")[0]) > 2):
            score, why = 80, "its label says “%s”" % _label_beside(project, n).strip()
        elif label and len(a) >= 3 and (label.startswith(a) or a.startswith(label)
                                        and len(label) >= 3):
            score, why = 70, "its label says “%s”" % _label_beside(project, n).strip()
        elif len(a) >= 3 and a in _norm(nid):
            score, why = 60, "similar name"
        if score > best_score:
            best, best_score, reason = nid, score, why
    return best, reason


def humanize(name):
    """'load_n' -> 'Load n'."""
    s = re.sub(r"_+", " ", name).strip()
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    return (s[:1].upper() + s[1:]) if s else name


def _fresh_id(project, base, reserved):
    base = re.sub(r"\W", "_", base).strip("_").lower() or "value"
    if base[0].isdigit():
        base = "v_" + base
    cand = base
    if not model.valid_identifier(cand) or cand in reserved:
        cand = base + "_value" if model.valid_identifier(base + "_value") else "value"
        i = 1
        while not model.valid_identifier(cand) or cand in reserved:
            i += 1
            cand = "%s_%d" % (base, i)
    reserved.add(cand)
    return cand


def _host_spot(project, host_id):
    """(container id, row, mode) for widgets created next to host_id."""
    host = project.find(host_id) if host_id else None
    if host is None or host["type"] == "Window":
        win = host if host is not None else project.main_window
        rows, _c = project.grid_size(win)
        return win["id"], rows, "cell"
    parent = project.parent_of(host["id"])
    while parent is not None and model.layout_mode(parent) != "grid":
        host = parent
        parent = project.parent_of(host["id"])
    if parent is None:
        win = project.main_window
        return win["id"], project.grid_size(win)[0], "cell"
    return parent["id"], host["layout"]["row"], "host"


def create_input_group(project, host_id, fn_name, args):
    """Add a Group Box with a Label + input per argument (above host_id).
    args: list of introspect arg dicts.  Returns (group node, {arg: widget id})."""
    reserved = set(project.all_ids())
    group = project.new_node("LabelFrame", reserved)
    reserved.add(group["id"])
    group["props"]["text"] = "Inputs – %s" % humanize(fn_name)
    group["layout"]["fill"] = "x"
    mapping = {}
    row = 0
    for a in args:
        if a.get("kind") in ("varargs", "varkw"):
            continue
        hint = introspect.param_widget_hint(a) if introspect else ("Entry", {})
        wtype, props = hint
        wid = _fresh_id(project, a["name"], reserved)
        w = model.make_node(wtype, wid)
        for k, v in props.items():
            if k in w["props"]:
                w["props"][k] = v
        if wtype == "Checkbutton":
            w["layout"].update(row=row, col=0, colspan=2)
        else:
            lab = model.make_node("Label", _fresh_id(project, a["name"] + "_label", reserved))
            lab["props"]["text"] = humanize(a["name"]) + ":"
            lab["layout"].update(row=row, col=0)
            group["children"].append(lab)
            w["layout"].update(row=row, col=1, fill="x")
        group["children"].append(w)
        mapping[a["name"]] = wid
        row += 1
    cid, r, mode = _host_spot(project, host_id)
    project.place(group, cid, r, 0, "above" if mode == "host" else "cell")
    return group, mapping


def create_result_group(project, host_id, shape, count_or_keys):
    """Add a Group Box 'Results' with caption + value labels (below host_id).
    Returns (group node or None, [value label ids])."""
    reserved = set(project.all_ids())
    if shape == "dict":
        names = list(count_or_keys)
    elif shape == "tuple":
        names = ["result_%d" % (i + 1) for i in range(int(count_or_keys))]
    else:
        names = ["result"]
    group = project.new_node("LabelFrame", reserved)
    reserved.add(group["id"])
    group["props"]["text"] = "Results"
    group["layout"]["fill"] = "x"
    ids = []
    row = 0
    for name in names:
        if shape == "dict" and project.find(name) is not None:
            ids.append(name)          # a widget with that name exists: it gets the value
            continue
        wid = _fresh_id(project, name, reserved) if shape != "dict" or not \
            model.valid_identifier(name) or name in reserved else name
        reserved.add(wid)
        cap = model.make_node("Label", _fresh_id(project, wid + "_caption", reserved))
        cap["props"]["text"] = humanize(name) + ":"
        cap["layout"].update(row=row, col=0)
        val = model.make_node("Label", wid)
        val["props"]["text"] = "–"
        val["props"]["variant"] = "Mono"
        val["layout"].update(row=row, col=1, fill="x")
        group["children"] += [cap, val]
        ids.append(wid)
        row += 1
    if not group["children"]:
        return None, ids
    cid, r, mode = _host_spot(project, host_id)
    container = project.find(cid)
    if mode == "host":
        project.place(group, cid, r, 0, "below")
    else:
        project.place(group, cid, project.grid_size(container)[0], 0, "cell")
    return group, ids


# ----------------------------------------------------------------------------
# Colour picker
# ----------------------------------------------------------------------------

class ColorPicker(tk.Canvas):
    """Swatch + value + reset x, optionally a row of quick colours - all drawn
    on ONE canvas (a single native window: fast to create).

    Click the swatch or the text to open the colour chooser.
    on_change(value) is called with "" (default) or "#rrggbb".
    """

    def __init__(self, parent, value="", on_change=None, default=None, quick=True,
                 empty_text="Default", bg=None, reset_value=""):
        bg = theme.BG if bg is None else bg
        self.size, self.gap = px(17), px(4)
        self.row_h = px(24)
        width = max(len(QUICK_COLORS) * (self.size + self.gap), px(200))
        height = self.row_h + (self.size + px(6) if quick else 0)
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0, width=width, height=height)
        self.value = value or ""
        self.on_change = on_change
        self.default = default or ""        # colour used to draw the "auto" swatch
        self.empty_text = empty_text
        self.reset_value = reset_value or ""
        self.quick = quick
        h = self.row_h
        self.create_rectangle(0, px(2), px(38), h - px(2), outline=theme.BORDER, tags=("swatch",))
        self.create_text(px(19), h / 2, text="", font=font(7), tags=("autotxt",))
        self.create_text(px(46), h / 2, text="", anchor="w", font=font(9), tags=("value",))
        self.create_text(px(140), h / 2, text="\u2715", anchor="w", font=font(10),
                         fill=theme.DANGER, tags=("reset",))
        for tag in ("swatch", "autotxt", "value"):
            self.tag_bind(tag, "<Button-1>", lambda e: self.choose())
        self.tag_bind("reset", "<Button-1>", lambda e: self.reset())
        self.names = []
        if quick:
            y = h + px(4)
            for i, (col, _name) in enumerate(QUICK_COLORS):
                x = i * (self.size + self.gap)
                item = self.create_rectangle(x, y, x + self.size, y + self.size, fill=col,
                                             outline=theme.BORDER)
                self.tag_bind(item, "<Button-1>", lambda e, c=col: self.set(c, notify=True))
            self.names = [n for _c, n in QUICK_COLORS]
        self.tip = theme.tooltip(self, "", delay=300)
        self.bind("<Motion>", self._hover, add="+")
        self._show()

    def _hover(self, e):
        text, cursor = "", ""
        if e.y < self.row_h:
            if e.x < px(130):
                text, cursor = "Click to pick a colour", "hand2"
            elif e.x < px(156) and self.itemcget("reset", "state") != "hidden":
                text = ("Back to the default colour" if self.empty_text == "Default"
                        else "Clear (leave the colour as it is)")
                cursor = "hand2"
        elif self.quick:
            i = int(e.x // (self.size + self.gap))
            if 0 <= i < len(self.names) and e.x % (self.size + self.gap) < self.size:
                text, cursor = self.names[i], "hand2"
        if self.cget("cursor") != cursor:
            self.configure(cursor=cursor)
        if text != self.tip.text:
            self.tip._hide()
            self.tip.text = text
            if text:
                self.tip._schedule()

    def _show(self):
        v = self.value
        if v and _is_color(self, v):
            self.itemconfigure("swatch", fill=v)
            self.itemconfigure("autotxt", text="")
            self.itemconfigure("value", text=v, fill=theme.TEXT)
        elif v:
            self.itemconfigure("swatch", fill=theme.PANEL)
            self.itemconfigure("autotxt", text="?", fill=theme.DANGER)
            self.itemconfigure("value", text=v, fill=theme.DANGER)
        else:
            shown = self.default if self.default and _is_color(self, self.default) else theme.PANEL
            self.itemconfigure("swatch", fill=shown)
            self.itemconfigure("autotxt", text="auto", fill=_contrast(self, shown))
            self.itemconfigure("value", text=self.empty_text, fill=theme.MUTED)
        self.itemconfigure("reset", state="normal" if v != self.reset_value else "hidden")

    def set(self, value, notify=False):
        self.value = value or ""
        self._show()
        if notify and self.on_change:
            self.on_change(self.value)

    def choose(self):
        init = _hex(self, self.value) if self.value else (
            _hex(self, self.default) if self.default else None)
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
# Keyboard shortcut capture
# ----------------------------------------------------------------------------

_MODIFIER_KEYS = {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
                  "Meta_L", "Meta_R", "Win_L", "Win_R", "Caps_Lock", "Num_Lock",
                  "Super_L", "Super_R", "ISO_Level3_Shift", "App"}
_KEY_NAMES = {"Return": "Enter", "Escape": "Esc", "space": "Space", "Delete": "Delete",
              "BackSpace": "Backspace", "Prior": "PageUp", "Next": "PageDown",
              "Insert": "Insert", "Home": "Home", "End": "End", "Up": "Up", "Down": "Down",
              "Left": "Left", "Right": "Right"}
_EDIT_KEYS = {"Ctrl+C": "Copy", "Ctrl+V": "Paste", "Ctrl+X": "Cut", "Ctrl+A": "Select all",
              "Ctrl+Z": "Undo"}


def shortcut_from_event(event):
    """Return 'Ctrl+Shift+R' for a key event, '' for 'clear', or None to ignore."""
    ks = event.keysym
    if ks in _MODIFIER_KEYS:
        return None
    st = event.state
    ctrl = bool(st & 0x4)
    shift = bool(st & 0x1)
    alt = bool(st & 0x20000) if sys.platform == "win32" else bool(st & 0x8)
    if ks in ("BackSpace", "Delete") and not (ctrl or alt or shift):
        return ""
    if len(ks) == 1:
        key = ks.upper()
    elif re.match(r"^F\d{1,2}$", ks):
        key = ks
    elif ks in _KEY_NAMES:
        key = _KEY_NAMES[ks]
    elif ks.startswith("KP_") and len(ks) == 4:
        key = ks[3:]
    else:
        return None
    parts = (["Ctrl"] if ctrl else []) + (["Alt"] if alt else []) + (["Shift"] if shift else [])
    return "+".join(parts + [key])


class ShortcutEntry(tk.Frame):
    """Click the box, then press the keys of a shortcut (e.g. Ctrl+R)."""

    HINT = "Click, then press keys"

    def __init__(self, parent, value="", on_change=None, bg=None):
        bg = theme.BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.value = value or ""
        self.on_change = on_change
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.box = tk.Label(row, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                            font=font(10), padx=6, pady=3, cursor="hand2", takefocus=1,
                            highlightthickness=1, highlightbackground=theme.BORDER,
                            highlightcolor=theme.ACCENT, width=18)
        self.box.pack(side="left")
        small_button(row, "×", lambda: self.set("", notify=True), "No shortcut",
                     bg=bg, fg=theme.DANGER).pack(side="left", padx=(4, 0))
        self.note = muted(self, "", bg=bg)
        self.box.bind("<Button-1>", lambda e: self.box.focus_set())
        self.box.bind("<FocusIn>", lambda e: self._show(focused=True))
        self.box.bind("<FocusOut>", lambda e: self._show())
        self.box.bind("<KeyPress>", self._key)
        theme.tooltip(self.box, "Click here and press the keys you want, for example Ctrl+R "
                                "or F5. Backspace removes the shortcut.")
        self._show()

    def _show(self, focused=False, note=""):
        if focused:
            self.box.configure(text=self.value or "Press keys…", fg=theme.ACCENT,
                               bg=theme.ACCENT_LIGHT)
        else:
            self.box.configure(text=self.value or self.HINT,
                               fg=theme.TEXT if self.value else theme.MUTED, bg=theme.PANEL)
        if not note and self.value in _EDIT_KEYS:
            note = "%s also means “%s” in text boxes of your app." % (self.value,
                                                                    _EDIT_KEYS[self.value])
        self.note.configure(text=note)
        if note:
            self.note.pack(fill="x", pady=(2, 0))
        else:
            self.note.pack_forget()

    def _key(self, event):
        if event.keysym == "Tab":
            return None          # let Tab move the focus
        sc = shortcut_from_event(event)
        if sc is None:
            return "break"
        if sc == "Esc":
            self.winfo_toplevel().focus_set()
            return "break"
        if sc and "+" not in sc and not re.match(r"^F\d", sc):
            self._show(focused=True, note="Add Ctrl or Alt – a single key would also fire "
                                          "while the user types.")
            return "break"
        self.set(sc, notify=True)
        self._show(focused=True)
        return "break"

    def set(self, value, notify=False):
        value = value or ""
        changed = value != self.value
        self.value = value
        self._show(focused=self.focus_get() is self.box)
        if notify and changed and self.on_change:
            self.on_change(value)

    def load(self, value):
        self.value = value or ""
        self._show()


# ----------------------------------------------------------------------------
# Table columns / rows editors
# ----------------------------------------------------------------------------

ALIGN_CHOICES = [["w", "Left"], ["center", "Center"], ["e", "Right"]]


class ColumnsEditor(tk.Frame):
    """Edit Table columns: a list with Add / Remove / ↑ / ↓ and fields for the
    selected column.  on_change(columns) gets a fresh list of dicts."""

    def __init__(self, parent, on_change=None, bg=None, scroll_canvas=None):
        bg = theme.BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.on_change = on_change
        self.columns = []
        self._loading = False
        self.tree = ttk.Treeview(self, columns=("title", "width", "align"), show="headings",
                                 height=4, selectmode="browse")
        self.tree.heading("title", text="Column title")
        self.tree.heading("width", text="Width")
        self.tree.heading("align", text="Align")
        self.tree.column("title", width=px(130), stretch=True)
        self.tree.column("width", width=px(54), stretch=False, anchor="e")
        self.tree.column("align", width=px(60), stretch=False)
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_selected())

        bar = tk.Frame(self, bg=bg)
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="＋ Add column", style="Small.TButton",
                   command=self.add).pack(side="left")
        for txt, cmd, tip in (("✕", self.remove, "Remove the selected column"),
                              ("↓", lambda: self.move(1), "Move the column to the right"),
                              ("↑", lambda: self.move(-1), "Move the column to the left")):
            small_button(bar, txt, cmd, tip, bg=bg,
                         fg=theme.DANGER if txt == "✕" else None).pack(side="right")

        f = tk.Frame(self, bg=bg)
        f.pack(fill="x", pady=(6, 0))
        tk.Label(f, text="Title", bg=bg, fg=theme.TEXT, font=font(9)).grid(row=0, column=0,
                                                                         sticky="w")
        self.title_var = tk.StringVar()
        self.title_ent = ttk.Entry(f, textvariable=self.title_var, width=16)
        self.title_ent.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(6, 0))
        tk.Label(f, text="Width", bg=bg, fg=theme.TEXT, font=font(9)).grid(row=1, column=0,
                                                                         sticky="w", pady=(4, 0))
        self.width_var = tk.StringVar()
        sp = ttk.Spinbox(f, from_=20, to=2000, increment=10, textvariable=self.width_var, width=6)
        sp.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(4, 0))
        self.align = ttk.Combobox(f, state="readonly", width=8,
                                  values=[a[1] for a in ALIGN_CHOICES])
        self.align.grid(row=1, column=3, sticky="e", pady=(4, 0))
        tk.Label(f, text="Align", bg=bg, fg=theme.TEXT, font=font(9)).grid(row=1, column=2,
                                                                         sticky="e", padx=(8, 4),
                                                                         pady=(4, 0))
        f.columnconfigure(1, weight=1)
        self.fields = (self.title_ent, sp, self.align)
        self.title_var.trace_add("write", lambda *a: self._field_changed())
        self.width_var.trace_add("write", lambda *a: self._field_changed())
        self.align.bind("<<ComboboxSelected>>", lambda e: (self._field_changed(),
                                                           self.align.selection_clear()))
        no_wheel(sp, scroll_canvas)
        no_wheel(self.align, scroll_canvas)

    def load(self, columns):
        cols = []
        for c in columns or []:
            if isinstance(c, str):
                c = {"title": c}
            cols.append({"title": c.get("title", ""), "width": int(c.get("width") or 100),
                         "anchor": c.get("anchor") or "w"})
        idx = self._index()
        self.columns = cols
        self._fill(idx if idx is not None else 0)

    def _fill(self, select=None):
        t = self.tree
        t.delete(*t.get_children(""))
        for i, c in enumerate(self.columns):
            t.insert("", "end", iid=str(i), values=(c["title"], c["width"], dict(
                (a, b) for a, b in ALIGN_CHOICES).get(c["anchor"], "Left")))
        if self.columns:
            i = max(0, min(select or 0, len(self.columns) - 1))
            t.selection_set(str(i))
            t.see(str(i))
        self._show_selected()

    def _index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def _show_selected(self):
        i = self._index()
        self._loading = True
        try:
            if i is None or i >= len(self.columns):
                self.title_var.set("")
                self.width_var.set("")
                self.align.set("")
                for w in self.fields:
                    w.state(["disabled"])
                return
            for w in self.fields:
                w.state(["!disabled"])
            self.align.state(["readonly"])
            c = self.columns[i]
            self.title_var.set(c["title"])
            self.width_var.set(str(c["width"]))
            self.align.set(dict((a, b) for a, b in ALIGN_CHOICES).get(c["anchor"], "Left"))
        finally:
            self._loading = False

    def _field_changed(self):
        if self._loading:
            return
        i = self._index()
        if i is None or i >= len(self.columns):
            return
        c = self.columns[i]
        c["title"] = self.title_var.get()
        try:
            w = int(float(self.width_var.get()))
            if 10 <= w <= 4000:
                c["width"] = w
        except ValueError:
            pass
        if self.align.current() >= 0:
            c["anchor"] = ALIGN_CHOICES[self.align.current()][0]
        self.tree.item(str(i), values=(c["title"], c["width"], dict(
            (a, b) for a, b in ALIGN_CHOICES).get(c["anchor"], "Left")))
        self._notify()

    def _notify(self):
        if self.on_change:
            self.on_change(copy.deepcopy(self.columns))

    def add(self):
        self.columns.append({"title": "Column %d" % (len(self.columns) + 1), "width": 100,
                             "anchor": "w"})
        self._fill(len(self.columns) - 1)
        self._notify()
        self.title_ent.focus_set()
        self.title_ent.select_range(0, "end")

    def remove(self):
        i = self._index()
        if i is None:
            return
        if len(self.columns) <= 1:
            return
        del self.columns[i]
        self._fill(max(0, i - 1))
        self._notify()

    def move(self, delta):
        i = self._index()
        if i is None or not 0 <= i + delta < len(self.columns):
            return
        c = self.columns
        c[i], c[i + delta] = c[i + delta], c[i]
        self._fill(i + delta)
        self._notify()


def rows_to_text(rows):
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    for r in rows or []:
        w.writerow(["" if v is None else str(v) for v in (r if isinstance(r, (list, tuple))
                                                         else [r])])
    return out.getvalue().rstrip("\n")


def text_to_rows(text):
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:                         # pasted from a spreadsheet
            rows.append([v.strip() for v in line.split("\t")])
        else:
            rows.append([v.strip() for v in next(csv.reader([line], skipinitialspace=True))])
    return rows


class RowsEditor(tk.Frame):
    """Starting rows of a Table: one row per line, values separated by commas
    (rows copied from Excel can be pasted too)."""

    def __init__(self, parent, on_change=None, bg=None):
        bg = theme.BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.on_change = on_change
        self.txt = text_widget(self, height=4, width=24, mono=True, wrap="none")
        self.txt.pack(fill="x")
        self.info = muted(self, "", bg=bg)
        self.info.pack(fill="x", pady=(2, 0))
        self._ncols = 0
        self.txt.bind("<<Modified>>", self._modified)

    def load(self, rows, ncols):
        self._ncols = ncols
        text = rows_to_text(rows)
        if self.txt.get("1.0", "end-1c") != text:
            self.txt.delete("1.0", "end")
            self.txt.insert("1.0", text)
            self.txt.edit_reset()
        self.txt.edit_modified(False)
        self._info(rows)

    def _info(self, rows):
        bad = [i + 1 for i, r in enumerate(rows) if len(r) != self._ncols]
        text = "One row per line, values separated by commas. You can also paste rows " \
               "copied from Excel."
        if bad:
            text = "⚠ Row %s has a different number of values than there are columns (%d)." % (
                ", ".join(map(str, bad[:3])), self._ncols)
        self.info.configure(text=text, fg=theme.DANGER if bad else theme.MUTED)

    def _modified(self, _e=None):
        try:
            if not self.txt.edit_modified():
                return
            self.txt.edit_modified(False)
            rows = text_to_rows(self.txt.get("1.0", "end-1c"))
        except (tk.TclError, csv.Error):
            return
        self._info(rows)
        if self.on_change:
            self.on_change(rows)


# ----------------------------------------------------------------------------
# Ordered multi-pick of widgets
# ----------------------------------------------------------------------------

class WidgetListPicker(tk.Frame):
    """Pick several widgets in order (e.g. where each item of a result goes)."""

    def __init__(self, parent, project, choices, value, on_change=None, bg=None):
        bg = theme.BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.project = project
        self.choices = choices          # [(id, label)]
        self.value = list(value or [])
        self.on_change = on_change
        row = tk.Frame(self, bg=bg)
        row.pack(fill="x")
        self.lb = tk.Listbox(row, height=4, activestyle="none", exportselection=False,
                             relief="flat", bd=0, highlightthickness=1,
                             highlightbackground=theme.BORDER, bg=theme.PANEL, fg=theme.TEXT,
                             selectbackground=theme.ACCENT, selectforeground="#ffffff",
                             font=font(10), width=40)
        self.lb.pack(side="left", fill="x", expand=True)
        side = tk.Frame(row, bg=bg)
        side.pack(side="left", fill="y", padx=(4, 0))
        small_button(side, "↑", lambda: self._move(-1), "Earlier", bg=bg).pack()
        small_button(side, "↓", lambda: self._move(1), "Later", bg=bg).pack()
        small_button(side, "✕", self._remove, "Remove", bg=bg, fg=theme.DANGER).pack()
        add_row = tk.Frame(self, bg=bg)
        add_row.pack(fill="x", pady=(4, 0))
        tk.Label(add_row, text="Add:", bg=bg, fg=theme.TEXT, font=font(9)).pack(side="left")
        self.add_cb = ttk.Combobox(add_row, state="readonly", width=36,
                                   values=[c[1] for c in choices])
        self.add_cb.pack(side="left", padx=(6, 0))
        self.add_cb.set("— choose a widget to add —")
        self.add_cb.bind("<<ComboboxSelected>>", self._add)
        self._fill()

    def _label(self, wid):
        for i, lab in self.choices:
            if i == wid:
                return lab
        return "%s (missing!)" % wid

    def _fill(self, sel=None):
        self.lb.delete(0, "end")
        for k, wid in enumerate(self.value):
            self.lb.insert("end", " %d.  %s" % (k + 1, self._label(wid)))
        if sel is not None and self.value:
            sel = max(0, min(sel, len(self.value) - 1))
            self.lb.selection_set(sel)

    def _sel(self):
        s = self.lb.curselection()
        return s[0] if s else None

    def _changed(self, sel=None):
        self._fill(sel)
        if self.on_change:
            self.on_change(list(self.value))

    def _add(self, _e=None):
        i = self.add_cb.current()
        self.add_cb.set("— choose a widget to add —")
        self.add_cb.selection_clear()
        if i < 0:
            return
        self.value.append(self.choices[i][0])
        self._changed(len(self.value) - 1)

    def _move(self, d):
        i = self._sel()
        if i is None or not 0 <= i + d < len(self.value):
            return
        v = self.value
        v[i], v[i + d] = v[i + d], v[i]
        self._changed(i + d)

    def _remove(self):
        i = self._sel()
        if i is None:
            return
        del self.value[i]
        self._changed(i)


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
        w = min(w, self.winfo_screenwidth() - 40)
        h = min(h, self.winfo_screenheight() - 80)
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
        bar = ttk.Frame(self, padding=(16, 10, 16, 14))
        bar.pack(side="bottom", fill="x")
        self.cancel_btn = ttk.Button(bar, text=cancel_text, command=self.cancel)
        self.cancel_btn.pack(side="right")
        self.ok_btn = ttk.Button(bar, text=ok_text, style="Accent.TButton", command=self.ok)
        self.ok_btn.pack(side="right", padx=(0, 8))
        tk.Frame(self, bg=theme.BORDER, height=1).pack(side="bottom", fill="x")
        return bar


# ----------------------------------------------------------------------------
# Ask for a string
# ----------------------------------------------------------------------------

class _AskString(_Dialog):
    def __init__(self, parent, title, prompt, initial=""):
        super().__init__(parent, title, resizable=False)
        self.button_bar()
        body = ttk.Frame(self, padding=(16, 14, 16, 4))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=prompt, wraplength=px(320), justify="left").pack(anchor="w")
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
    if kind in ("widget", "widgets"):
        nodes = project.nodes_of_types(opts.get("types"))
        return [(n["id"], "%s  %s" % (WIDGETS[n["type"]]["icon"], project.display_name(n)))
                for n in nodes]
    if kind == "window":
        out = []
        for i, w in enumerate(project.windows):
            if i == 0 and opts.get("main") is False:
                continue
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
        return ("You don't have another window yet. Add one with “New Window” "
                "(toolbar or Insert menu).")
    if kind == "tab":
        return "There are no tabs yet. Add a “Tabs” widget from the palette first."
    types = opts.get("types")
    if types:
        names = sorted({WIDGETS[t]["name"] for t in types})
        if len(names) > 4:
            names = names[:4] + ["…"]
        return "No suitable widget yet. Add one of these first: %s." % ", ".join(names)
    return "There are no widgets yet."


NONE_LABEL = "(none)"


class ActionEditor(_Dialog):
    def __init__(self, parent, project, action=None, context="", app=None, host_id=None):
        super().__init__(parent, "Edit action" if action else "Add an action", size=(px(980), px(680)))
        self.project = project
        self.app = app
        self.host_id = host_id
        self.cache = {}          # action type -> params being edited
        self.atype = None
        self.pending_settings = {}
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
                     font=font(10, "bold"), padx=16, pady=7).pack(fill="x")

        main = ttk.Frame(self, padding=(16, 12, 16, 0))
        main.pack(fill="both", expand=True)

        # ---- left: action types ----
        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        ttk.Label(left, text="What should happen?", style="Heading.TLabel").pack(
            anchor="w", pady=(0, 6))
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.types = ttk.Treeview(tf, show="tree", selectmode="browse", height=18)
        self.types.column("#0", width=px(270))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.types.yview)
        self.types.configure(yscrollcommand=vsb.set)
        self.types.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.types.tag_configure("cat", font=font(9, "bold"), foreground=theme.MUTED)
        for cat in ACTION_CATEGORIES:
            keys = [k for k, a in ACTIONS.items() if a["category"] == cat]
            if not keys:
                continue
            self.types.insert("", "end", iid="cat:" + cat, text=cat.upper(), open=True,
                              tags=("cat",))
            for k in keys:
                self.types.insert("cat:" + cat, "end", iid=k, text=ACTIONS[k]["name"])
        self.types.bind("<<TreeviewSelect>>", self._on_type)

        # ---- right: form ----
        right = ttk.Frame(main, padding=(18, 0, 0, 0))
        right.pack(side="left", fill="both", expand=True)
        self.title_lbl = ttk.Label(right, text="", style="Heading.TLabel")
        self.title_lbl.pack(anchor="w")
        self.help_lbl = ttk.Label(right, text="", style="Muted.TLabel", wraplength=px(600),
                                  justify="left")
        self.help_lbl.pack(anchor="w", pady=(2, 8), fill="x")
        ttk.Separator(right).pack(fill="x")

        prev = tk.Frame(right, bg=theme.ACCENT_LIGHT, highlightthickness=1,
                        highlightbackground=theme.BORDER)
        prev.pack(side="bottom", fill="x", pady=(8, 0))
        self.error = tk.Label(right, text="", bg=theme.BG, fg=theme.DANGER, anchor="w",
                              justify="left", wraplength=px(600), font=font(9))
        self.error.pack(side="bottom", fill="x", pady=(4, 0))
        tk.Label(prev, text="This action will:", bg=theme.ACCENT_LIGHT, fg=theme.MUTED,
                 font=font(8, "bold"), anchor="w", padx=10).pack(fill="x", pady=(5, 0))
        self.preview = tk.Label(prev, text="", bg=theme.ACCENT_LIGHT, fg=theme.TEXT, anchor="w",
                                justify="left", wraplength=px(600), font=font(10), padx=10)
        self.preview.pack(fill="x", pady=(0, 6))

        outer, inner = theme.scrollable(right, bg=theme.BG)
        outer.pack(fill="both", expand=True, pady=(8, 0))
        self.scroll_canvas = outer.canvas
        self.form = tk.Frame(inner, bg=theme.BG)
        self.form.pack(fill="both", expand=True, padx=(0, 10))

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

    def _clear_form(self):
        for w in self.form.winfo_children():
            w.destroy()
        try:
            self.scroll_canvas.yview_moveto(0)
        except tk.TclError:
            pass

    def _build_form(self):
        self._clear_form()
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
            params = model.new_action(self.atype)["params"]
            if self.atype == "call_function":
                params["background"] = bool(self.project.settings.get("threads", True))
            # keep the chosen target when switching between similar actions
            for other in self.cache.values():
                for p in spec["params"]:
                    if p["kind"] == "widget" and p["key"] == "target" and other.get("target"):
                        ids = [i for i, _ in _choices_for(self.project, p)]
                        if other["target"] in ids:
                            params["target"] = other["target"]
            self.cache[self.atype] = params
        params = self.cache[self.atype]
        self.title_lbl.configure(text=spec["name"])
        self.help_lbl.configure(text=ACTION_HELP.get(self.atype, spec.get("help", "")))
        if self.atype == "call_function":
            FunctionForm(self, self.form, params)
        elif self.atype == "run_command":
            self._build_run_command(params)
        else:
            if not spec["params"]:
                ttk.Label(self.form, text="Nothing else to set up – just click OK.",
                          style="Muted.TLabel").pack(anchor="w", pady=4)
            has_text = False
            for p in spec["params"]:
                self.param_editor(self.form, p, params)
                has_text = has_text or p["kind"] in ("text", "multiline")
            if has_text:
                muted(self.form, PLACEHOLDER_TIP, wrap=580).pack(anchor="w", pady=(6, 0))
        self._update_preview()

    def changed(self, params, key, value):
        params[key] = value
        self.error.configure(text="")
        self._update_preview()

    def field(self, parent, label, help_text=""):
        box = tk.Frame(parent, bg=theme.BG)
        box.pack(fill="x", pady=(0, 10))
        if label:
            tk.Label(box, text=label, bg=theme.BG, fg=theme.TEXT, font=font(9, "bold"),
                     anchor="w").pack(fill="x")
        holder = tk.Frame(box, bg=theme.BG)
        holder.pack(fill="x", pady=(2, 0))
        if help_text:
            muted(box, help_text, wrap=580).pack(fill="x", pady=(2, 0))
        return holder

    def widget_combo(self, parent, p, params, key=None, on_pick=None, width=44):
        """Combobox for a widget/window/tab param. Optional params get '(none)'."""
        key = key or p["key"]
        opts = p.get("options") or {}
        choices = _choices_for(self.project, p)
        ids = [c[0] for c in choices]
        labels = [c[1] for c in choices]
        if opts.get("optional"):
            ids.insert(0, "")
            labels.insert(0, NONE_LABEL)
        cur = params.get(key) or ""
        if cur and cur not in ids:
            node = self.project.find(cur)
            ids.append(cur)
            labels.append("%s (doesn't fit here)" % cur if node else "%s (deleted)" % cur)
        cb = ttk.Combobox(parent, state="readonly", values=labels, width=width, height=14)
        if cur in ids:
            cb.current(ids.index(cur))
        else:
            cb.set("— choose —")

        def pick(_e=None):
            cb.selection_clear()
            self.changed(params, key, ids[cb.current()])
            if on_pick:
                on_pick(ids[cb.current()])
        cb.bind("<<ComboboxSelected>>", pick)
        no_wheel(cb, self.scroll_canvas)
        return cb, choices

    def param_editor(self, parent, p, params):
        key, kind = p["key"], p["kind"]
        if kind == "bool":
            var = tk.BooleanVar(value=bool(params.get(key)))
            ttk.Checkbutton(parent, text=p["label"], variable=var,
                            command=lambda: self.changed(params, key, bool(var.get()))).pack(
                anchor="w", pady=(0, 8))
            return
        box = self.field(parent, p["label"], p.get("help", ""))
        if kind in ("widget", "window", "tab"):
            cb, choices = self.widget_combo(box, p, params)
            cb.pack(anchor="w")
            if not choices:
                tk.Label(box, text=_empty_choice_hint(p), bg=theme.BG, fg=theme.DANGER,
                         wraplength=px(560), justify="left", anchor="w",
                         font=font(9)).pack(anchor="w", pady=(2, 0))
        elif kind == "widgets":
            WidgetListPicker(box, self.project, _choices_for(self.project, p),
                             params.get(key) or [],
                             on_change=lambda v: self.changed(params, key, v)).pack(fill="x")
        elif kind == "text":
            row = tk.Frame(box, bg=theme.BG)
            row.pack(fill="x")
            var = tk.StringVar(value=params.get(key, ""))
            ent = ttk.Entry(row, textvariable=var)
            ent.pack(side="left", fill="x", expand=True)
            var.trace_add("write", lambda *a: self.changed(params, key, var.get()))
            insert_value_menu(row, self.project,
                              lambda s: (ent.insert("insert", s), ent.focus_set())).pack(
                side="left", padx=(6, 0))
            return ent
        elif kind in ("multiline", "code"):
            row = tk.Frame(box, bg=theme.BG)
            row.pack(fill="both", expand=True)
            txt = text_widget(row, height=10 if kind == "code" else 3, width=40,
                              mono=kind == "code", wrap="none" if kind == "code" else "word")
            txt.insert("1.0", params.get(key, ""))
            txt.edit_modified(False)

            def on_mod(_e=None, t=txt):
                if t.edit_modified():
                    t.edit_modified(False)
                    self.changed(params, key, t.get("1.0", "end-1c"))
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
            sp.pack(anchor="w")
            no_wheel(sp, self.scroll_canvas)

            def num_changed(*_a):
                try:
                    self.changed(params, key, float(var.get().replace(",", ".")))
                except ValueError:
                    pass
            var.trace_add("write", num_changed)
        elif kind == "choice":
            opts = p["options"]
            values = [o[0] for o in opts]
            cb = ttk.Combobox(box, state="readonly", values=[o[1] for o in opts], width=44)
            cb.pack(anchor="w")
            cur = params.get(key)
            cb.current(values.index(cur) if cur in values else 0)
            if cur not in values:
                params[key] = values[0]
            cb.bind("<<ComboboxSelected>>", lambda e: (self.changed(params, key,
                                                                    values[cb.current()]),
                                                       cb.selection_clear()))
            no_wheel(cb, self.scroll_canvas)
            return cb
        elif kind == "color":
            ColorPicker(box, params.get(key, ""), on_change=lambda v: self.changed(params, key, v),
                        quick=True, empty_text="Keep as is").pack(anchor="w")
        else:
            ttk.Label(box, text="(can't edit this)", style="Muted.TLabel").pack(anchor="w")
        return None

    # ---- run_command: friendly form ----
    def _build_run_command(self, params):
        spec = {p["key"]: p for p in ACTIONS["run_command"]["params"]}
        box = self.field(self.form, "Command line",
                         "Exactly what you would type in a terminal. Use “Insert value of…” "
                         "to pass what the user typed, e.g. a file name.")
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x")
        var = tk.StringVar(value=params.get("command", ""))
        ent = ttk.Entry(row, textvariable=var, font=MONO)
        ent.pack(side="left", fill="x", expand=True)
        insert_value_menu(row, self.project,
                          lambda s: (ent.insert("insert", s), ent.focus_set())).pack(
            side="left", padx=(6, 0))

        def browse_program():
            path = filedialog.askopenfilename(
                parent=self, title="Choose a program or script",
                filetypes=[("Programs and scripts", "*.py *.exe *.bat *.cmd *.ps1 *.sh"),
                           ("All files", "*.*")])
            if not path:
                return
            path = os.path.normpath(path)
            q = '"%s"' % path if " " in path else path
            if path.lower().endswith(".py"):
                q = "python " + q
            elif path.lower().endswith(".ps1"):
                q = "powershell -ExecutionPolicy Bypass -File " + q
            ent.delete(0, "end")
            ent.insert(0, q)
        ttk.Button(row, text="Browse…", style="Small.TButton", command=browse_program).pack(
            side="left", padx=(6, 0))

        pv = tk.Frame(self.form, bg=theme.PANEL, highlightthickness=1,
                      highlightbackground=theme.BORDER)
        pv.pack(fill="x", pady=(0, 12))
        tk.Label(pv, text="It will run (values in « » come from your widgets):", bg=theme.PANEL,
                 fg=theme.MUTED, font=font(8, "bold"), anchor="w").pack(fill="x", padx=8,
                                                                      pady=(5, 0))
        cmd_lbl = tk.Label(pv, text="", bg=theme.PANEL, fg=theme.TEXT, font=MONO, anchor="w",
                           justify="left", wraplength=px(580))
        cmd_lbl.pack(fill="x", padx=8, pady=(0, 6))

        def show_cmd(*_a):
            text = var.get()
            self.changed(params, "command", text)

            def repl(m):
                node = self.project.find(m.group(1))
                if node is None:
                    return "«%s – missing!»" % m.group(1)
                return "«%s»" % m.group(1)
            shown = model.PLACEHOLDER_RE.sub(repl, text).replace("{{", "{").replace("}}", "}")
            cwd = params.get("cwd") or ""
            cmd_lbl.configure(text=("%s> " % cwd if cwd else "> ") + (shown or "(empty)"))
        var.trace_add("write", show_cmd)

        box = self.field(self.form, spec["cwd"]["label"])
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x")
        cwd_var = tk.StringVar(value=params.get("cwd", ""))
        ttk.Entry(row, textvariable=cwd_var).pack(side="left", fill="x", expand=True)

        def browse_dir():
            d = filedialog.askdirectory(parent=self, title="Working folder")
            if d:
                cwd_var.set(os.path.normpath(d))
        ttk.Button(row, text="Browse…", style="Small.TButton", command=browse_dir).pack(
            side="left", padx=(6, 0))
        cwd_var.trace_add("write", lambda *a: (self.changed(params, "cwd", cwd_var.get()),
                                               show_cmd()))
        for k in ("output", "busy"):
            b = self.field(self.form, spec[k]["label"])
            cb, choices = self.widget_combo(b, spec[k], params)
            cb.pack(anchor="w")
            if k == "output" and len(choices) == 0:
                muted(b, "Tip: add an Output Console to see what the command prints.",
                      wrap=560).pack(anchor="w", pady=(2, 0))
        self.param_editor(self.form, spec["show_errors"], params)
        show_cmd()

    # ---- results ----
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
        problem = _validate(act, self.project)
        if problem:
            self.error.configure(text=problem)
            return
        if self.atype == "call_function":
            for a in act["params"].get("args") or []:
                if a.get("source") == "value":
                    a["value"] = _literal_text(a.get("value", ""))
        if self.pending_settings:
            self.project.settings.update(self.pending_settings)
        self.result = act
        self._close()


def _literal_text(text):
    """Keep valid Python literals; quote plain words so they become text."""
    text = (text or "").strip()
    if not text:
        return "None"
    try:
        ast.literal_eval(text)
        return text
    except (ValueError, SyntaxError):
        pass
    if model.PLACEHOLDER_RE.search(text):
        return text
    return repr(text)


def _validate(act, project):
    """Problem with an action that is about to be saved, or None."""
    spec = ACTIONS[act["type"]]
    params = act["params"]
    if act["type"] == "call_function":
        if not params.get("module"):
            return "Please choose the Python file that contains your function."
        if not params.get("function"):
            return "Please choose a function."
        for a in params.get("args") or []:
            if a.get("source") == "widget" and not a.get("widget"):
                return "Please choose a widget for “%s” (or give it a fixed value)." % a["name"]
        mode = params.get("result")
        if mode == "widget" and not params.get("result_widget"):
            return "Please choose the widget that shows the result."
        if mode == "tuple" and not params.get("result_widgets"):
            return "Please add the widgets that show the results, in order."
        return None
    if act["type"] == "run_command" and not (params.get("command") or "").strip():
        return "Please type the command to run."
    for p in spec["params"]:
        opts = p.get("options") or {}
        if p["kind"] in ("widget", "window", "tab") and not params.get(p["key"]) \
                and not opts.get("optional"):
            what = {"widget": "a widget", "window": "a window", "tab": "a tab"}[p["kind"]]
            return "Please choose %s for “%s”." % (what, p["label"])
        if p["kind"] == "code":
            # checked the way it is used: as the body of an event handler
            code = textwrap.dedent((params.get(p["key"]) or "").expandtabs(4))
            lines = code.strip("\n").splitlines() or ["pass"]
            try:
                compile("def _check(self, event=None):\n" +
                        "\n".join("    " + ln for ln in lines), "<your code>", "exec")
            except SyntaxError as e:
                return "There is a mistake in the Python code (line %s): %s" % (
                    (e.lineno or 2) - 1, e.msg)
    return None


# ----------------------------------------------------------------------------
# "Call a Python function" form (function picker)
# ----------------------------------------------------------------------------

SOURCE_CHOICES = [("widget", "From a widget"), ("value", "A fixed value"),
                  ("all", "All values (a dict)"), ("skip", "Leave out (use its default)")]


class FunctionForm:
    """The call_function form inside the ActionEditor: file -> function ->
    arguments -> result -> options.  Edits `params` in place."""

    def __init__(self, editor, parent, params):
        self.ed = editor
        self.project = editor.project
        self.params = params
        self.parent = parent
        self.path = None           # .py file of the chosen module
        self.functions = []
        self.fn = None
        self.arg_rows = {}         # name -> row state
        self._build()

    # ---- helpers ----
    def _dirs(self):
        dirs = backend_dirs(self.project, self.ed.app)
        if self.ed.pending_settings.get("backend_dir"):
            dirs.insert(0, _abs_dir(self.ed.pending_settings["backend_dir"], self.ed.app))
        return dirs

    def _backend_dir(self):
        d = self.ed.pending_settings.get("backend_dir") or self.project.settings.get("backend_dir")
        return _abs_dir(d, self.ed.app)

    def _find_file(self):
        mod = self.params.get("module")
        if not mod or introspect is None:
            return None
        return introspect.find_module_file(mod, self._dirs())

    def _build(self):
        f = self.parent
        ed = self.ed
        # ---- 1. file ----
        box = ed.field(f, "1.  Python file")
        row = tk.Frame(box, bg=theme.BG)
        row.pack(fill="x")
        self.file_lbl = tk.Label(row, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                                 font=font(10), padx=8, pady=4, highlightthickness=1,
                                 highlightbackground=theme.BORDER)
        self.file_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Choose file…", command=self.choose_file).pack(side="left",
                                                                          padx=(6, 0))
        self.file_note = muted(box, "", wrap=580)
        self.file_note.pack(fill="x", pady=(2, 0))

        # ---- 2. function ----
        box = ed.field(f, "2.  Function")
        tf = tk.Frame(box, bg=theme.BG)
        tf.pack(fill="x")
        self.fn_tree = ttk.Treeview(tf, show="tree", selectmode="browse", height=5)
        self.fn_tree.column("#0", width=px(520))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.fn_tree.yview)
        self.fn_tree.configure(yscrollcommand=vsb.set)
        self.fn_tree.pack(side="left", fill="x", expand=True)
        vsb.pack(side="left", fill="y")
        self.fn_tree.bind("<<TreeviewSelect>>", self._on_function)
        self.fn_manual = tk.Frame(box, bg=theme.BG)
        tk.Label(self.fn_manual, text="Function name:", bg=theme.BG, fg=theme.TEXT,
                 font=font(9)).pack(side="left")
        self.fn_var = tk.StringVar(value=self.params.get("function", ""))
        ttk.Entry(self.fn_manual, textvariable=self.fn_var, width=30).pack(side="left",
                                                                           padx=(6, 0))
        self.fn_var.trace_add("write", lambda *a: self._manual_function())
        self.doc = tk.Label(box, text="", bg=theme.BG, fg=theme.MUTED, anchor="w",
                            justify="left", wraplength=px(580), font=font(9, "italic"))
        self.doc.pack(fill="x", pady=(4, 0))

        # ---- 3. arguments ----
        self.args_box = ed.field(f, "3.  Give it these values")
        self.args_rows = tk.Frame(self.args_box, bg=theme.BG)
        self.args_rows.pack(fill="x")
        brow = tk.Frame(self.args_box, bg=theme.BG)
        brow.pack(fill="x", pady=(6, 0))
        self.create_inputs_btn = ttk.Button(brow, text="✚ Create inputs for me",
                                            command=self.create_inputs)
        self.create_inputs_btn.pack(side="left")
        theme.tooltip(self.create_inputs_btn,
                      "Adds a Group Box with a label and a matching input (text box, "
                      "checkbox, file picker…) for every value, already connected.")
        self.args_note = muted(brow, "", wrap=360)
        self.args_note.pack(side="left", padx=(10, 0))

        # ---- 4. result ----
        rbox = ed.field(f, "4.  What to do with the result")
        spec = {p["key"]: p for p in ACTIONS["call_function"]["params"]}
        top = tk.Frame(rbox, bg=theme.BG)
        top.pack(fill="x")
        modes = spec["result"]["options"]
        self.mode_values = [o[0] for o in modes]
        self.mode_cb = ttk.Combobox(top, state="readonly", values=[o[1] for o in modes],
                                    width=46)
        self.mode_cb.pack(side="left")
        cur = self.params.get("result") or "none"
        self.mode_cb.current(self.mode_values.index(cur) if cur in self.mode_values else 0)
        self.mode_cb.bind("<<ComboboxSelected>>", self._on_mode)
        no_wheel(self.mode_cb, ed.scroll_canvas)
        self.create_results_btn = ttk.Button(top, text="✚ Create result labels",
                                             command=self.create_results)
        self.create_results_btn.pack(side="left", padx=(8, 0))
        theme.tooltip(self.create_results_btn,
                      "Adds labels that show what the function returns, already connected.")
        self.result_box = tk.Frame(rbox, bg=theme.BG)
        self.result_box.pack(fill="x", pady=(6, 0))

        # ---- 5. options ----
        obox = ed.field(f, "5.  Options")
        ed.param_editor(obox, spec["background"], self.params)
        b = tk.Frame(obox, bg=theme.BG)
        b.pack(fill="x", pady=(0, 8))
        tk.Label(b, text=spec["busy"]["label"], bg=theme.BG, fg=theme.TEXT,
                 font=font(9)).pack(side="left")
        cb, _c = ed.widget_combo(b, spec["busy"], self.params, width=30)
        cb.pack(side="left", padx=(6, 0))
        ed.param_editor(obox, spec["show_errors"], self.params)

        self._load_file()
        self._show_result()

    # ---- file / function ----
    def _load_file(self):
        self.path = self._find_file()
        mod = self.params.get("module")
        self.functions = []
        note = ""
        if self.path:
            try:
                self.functions = introspect.list_functions(self.path)
            except (OSError, SyntaxError, ValueError) as e:
                note = "⚠ Couldn't read this file: %s" % e
            self.file_lbl.configure(text="%s   (%s)" % (mod, self.path), fg=theme.TEXT)
            if not note:
                note = "TkBuilder only reads this file to list its functions – nothing is run."
        elif mod:
            self.file_lbl.configure(text=mod, fg=theme.DANGER)
            note = ("⚠ Can't find %s.py in the folder with your code. Choose the file again, "
                    "or type the function name below." % mod.replace(".", os.sep))
        else:
            self.file_lbl.configure(text="(no file chosen yet)", fg=theme.MUTED)
            note = "Choose the .py file that contains your function."
        self.file_note.configure(text=note)
        t = self.fn_tree
        t.delete(*t.get_children(""))
        for fn in self.functions:
            t.insert("", "end", iid=fn["name"], text="  " + introspect.signature_text(fn))
        if self.functions:
            self.fn_manual.pack_forget()
            t.master.pack(fill="x")
            name = self.params.get("function")
            if name and t.exists(name):
                t.selection_set(name)
                t.see(name)
                self.fn = next(fn for fn in self.functions if fn["name"] == name)
            else:
                self.fn = None
        else:
            t.master.pack_forget()
            self.fn = None
            if mod:
                self.fn_manual.pack(fill="x", before=self.doc)
            else:
                self.fn_manual.pack_forget()
        self._show_function()

    def choose_file(self):
        dirs = self._dirs()
        init = dirs[0] if dirs else os.getcwd()
        path = filedialog.askopenfilename(parent=self.ed, title="Choose your Python file",
                                          initialdir=init,
                                          filetypes=[("Python files", "*.py"),
                                                     ("All files", "*.*")])
        if not path:
            return
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        base = self._backend_dir()
        inside = False
        if base:
            try:
                inside = not os.path.relpath(path, base).startswith("..")
            except ValueError:
                inside = False
        if not base:
            self.ed.pending_settings["backend_dir"] = store_dir(folder, self.ed.app)
            base = folder
        elif not inside:
            if messagebox.askyesno(
                    "Folder with your code",
                    "This file is not inside the folder with your code:\n%s\n\n"
                    "Use its folder instead?\n%s\n\n(Other “Call a Python function” "
                    "actions should use files from the same folder.)" % (base, folder),
                    parent=self.ed):
                self.ed.pending_settings["backend_dir"] = store_dir(folder, self.ed.app)
                base = folder
        mod = introspect.module_name_for(path, base) if introspect else \
            os.path.splitext(os.path.basename(path))[0]
        if mod != self.params.get("module"):
            self.params["function"] = ""
        self.ed.changed(self.params, "module", mod)
        self._load_file()
        if self.functions and not self.params.get("function") and len(self.functions) == 1:
            self.fn_tree.selection_set(self.functions[0]["name"])

    def _on_function(self, _e=None):
        sel = self.fn_tree.selection()
        if not sel:
            return
        fn = next((x for x in self.functions if x["name"] == sel[0]), None)
        if fn is None or fn is self.fn:
            return
        self.fn = fn
        self.ed.changed(self.params, "function", fn["name"])
        self._show_function()

    def _manual_function(self):
        name = self.fn_var.get().strip()
        if name != self.params.get("function"):
            self.ed.changed(self.params, "function", name)

    def _show_function(self):
        fn = self.fn
        if fn is not None:
            doc = fn.get("doc") or ""
            first = doc.split("\n\n")[0].replace("\n", " ") if doc else "(no description)"
            self.doc.configure(text=first)
        else:
            self.doc.configure(text="")
        self._build_args()
        self._update_buttons()

    # ---- arguments ----
    def _fn_args(self):
        """Arguments to show: from the function, else the stored ones."""
        if self.fn is not None:
            return [a for a in self.fn["args"] if a["kind"] not in ("varargs", "varkw")]
        return [{"name": a.get("name", ""), "default": None, "annotation": None,
                 "kind": "keyword"} for a in self.params.get("args") or []]

    def _build_args(self):
        for w in self.args_rows.winfo_children():
            w.destroy()
        self.arg_rows = {}
        args = self._fn_args()
        stored = {a.get("name"): a for a in self.params.get("args") or []}
        candidates = self.project.nodes_of_types(model.VALUE_WIDGETS)
        choices = [(n["id"], "%s  %s" % (WIDGETS[n["type"]]["icon"], self.project.display_name(n)))
                   for n in candidates]
        if not args:
            muted(self.args_rows, "This function takes no values." if self.fn is not None
                  else "Choose a function first.", wrap=560, size=9).pack(anchor="w")
            self.params["args"] = []
            return
        self.args_rows.columnconfigure(2, weight=1)
        for r, a in enumerate(args):
            name = a["name"]
            st = copy.deepcopy(stored.get(name))
            reason = ""
            if st is None:                         # new argument: suggest something
                wid, reason = suggest_widget(self.project, name, candidates)
                if wid:
                    st = {"name": name, "source": "widget", "widget": wid, "value": ""}
                elif a.get("default") is not None:
                    st = {"name": name, "source": "skip", "widget": "", "value": a["default"]}
                else:
                    st = {"name": name, "source": "widget", "widget": "", "value": ""}
            st.setdefault("widget", "")
            st.setdefault("value", "")
            cap = tk.Frame(self.args_rows, bg=theme.BG)
            cap.grid(row=r, column=0, sticky="nw", pady=(0, 6))
            tk.Label(cap, text=name, bg=theme.BG, fg=theme.TEXT, font=MONO).pack(anchor="w")
            extra = []
            if a.get("annotation"):
                extra.append(a["annotation"])
            if a.get("default") is not None:
                extra.append("default %s" % a["default"])
            if extra:
                muted(cap, ", ".join(extra), wrap=150).pack(anchor="w")
            src_values = [s for s in SOURCE_CHOICES
                          if s[0] != "skip" or a.get("default") is not None]
            src = ttk.Combobox(self.args_rows, state="readonly", width=20,
                               values=[s[1] for s in src_values])
            src.grid(row=r, column=1, sticky="nw", padx=(10, 6), pady=(0, 6))
            keys = [s[0] for s in src_values]
            if st["source"] not in keys:
                st["source"] = "widget"
            src.current(keys.index(st["source"]))
            no_wheel(src, self.ed.scroll_canvas)
            holder = tk.Frame(self.args_rows, bg=theme.BG)
            holder.grid(row=r, column=2, sticky="new", pady=(0, 6))
            row = {"state": st, "holder": holder, "src": src, "keys": keys, "arg": a,
                   "reason": reason, "choices": choices}
            self.arg_rows[name] = row
            src.bind("<<ComboboxSelected>>",
                     lambda e, n=name: self._on_source(n))
            self._build_arg_value(name)
        self._save_args()

    def _on_source(self, name):
        row = self.arg_rows[name]
        row["src"].selection_clear()
        row["state"]["source"] = row["keys"][row["src"].current()]
        row["reason"] = ""
        self._build_arg_value(name)
        self._save_args()

    def _build_arg_value(self, name):
        row = self.arg_rows[name]
        h = row["holder"]
        for w in h.winfo_children():
            w.destroy()
        st = row["state"]
        src = st["source"]
        if src == "widget":
            ids = [c[0] for c in row["choices"]]
            labels = [c[1] for c in row["choices"]]
            cb = ttk.Combobox(h, state="readonly", values=labels, width=34, height=14)
            cb.pack(anchor="w", fill="x")
            no_wheel(cb, self.ed.scroll_canvas)
            if st.get("widget") in ids:
                cb.current(ids.index(st["widget"]))
            elif st.get("widget"):
                cb.set("%s (missing!)" % st["widget"])
            else:
                cb.set("— choose a widget —")
            note = muted(h, "✓ suggested: %s" % row["reason"] if row["reason"] else "",
                         wrap=300)
            if row["reason"]:
                note.configure(fg=theme.RUN)
                note.pack(anchor="w")

            def pick(_e=None):
                cb.selection_clear()
                st["widget"] = ids[cb.current()]
                note.pack_forget()
                self._save_args()
            cb.bind("<<ComboboxSelected>>", pick)
        elif src == "value":
            var = tk.StringVar(value=st.get("value") if st.get("value") not in (None, "None")
                               else "")
            ent = ttk.Entry(h, textvariable=var, width=30, font=MONO)
            ent.pack(anchor="w", fill="x")
            muted(h, "A number, True/False, or text. Text may use {widget} values.",
                  wrap=300).pack(anchor="w")

            def typed(*_a):
                st["value"] = var.get()
                self._save_args()
            var.trace_add("write", typed)
        elif src == "all":
            muted(h, "Passes every input of the window as one dict {name: value}.",
                  wrap=300, size=9).pack(anchor="w")
        else:
            muted(h, "Not passed – the function uses its default (%s)." % row["arg"].get(
                "default"), wrap=300, size=9).pack(anchor="w")

    def _save_args(self):
        out = []
        for name, row in self.arg_rows.items():
            st = row["state"]
            if st["source"] == "skip":
                continue
            item = {"name": name, "source": st["source"]}
            if st["source"] == "widget":
                item["widget"] = st.get("widget", "")
            elif st["source"] == "value":
                item["value"] = st.get("value", "")
            out.append(item)
        self.ed.changed(self.params, "args", out)
        missing = [n for n, r in self.arg_rows.items()
                   if r["state"]["source"] == "widget" and not r["state"].get("widget")]
        self.args_note.configure(
            text="%d value%s still need%s a widget." % (
                len(missing), "" if len(missing) == 1 else "s", "s" if len(missing) == 1
                else "") if missing else "")

    # ---- result ----
    def _on_mode(self, _e=None):
        self.mode_cb.selection_clear()
        self.ed.changed(self.params, "result", self.mode_values[self.mode_cb.current()])
        self._show_result()

    def _show_result(self):
        for w in self.result_box.winfo_children():
            w.destroy()
        mode = self.params.get("result") or "none"
        spec = {p["key"]: p for p in ACTIONS["call_function"]["params"]}
        b = self.result_box
        if mode == "widget":
            cb, choices = self.ed.widget_combo(b, spec["result_widget"], self.params)
            cb.pack(anchor="w")
            muted(b, "The returned value is shown in this widget (text, number, list for a "
                     "list/table…).", wrap=560).pack(anchor="w", pady=(2, 0))
        elif mode == "tuple":
            WidgetListPicker(b, self.project, _choices_for(self.project, spec["result_widgets"]),
                             self.params.get("result_widgets") or [],
                             on_change=lambda v: self.ed.changed(self.params, "result_widgets",
                                                                 v)).pack(fill="x")
            muted(b, "e.g. return area, volume  →  the first widget gets area, the second "
                     "volume.", wrap=560).pack(anchor="w", pady=(2, 0))
        elif mode == "dict":
            text = "Each key of the returned dict goes into the widget with that name, " \
                   "e.g. return {\"area\": 12.5} sets the widget called area."
            shape, keys = self._shape()
            if shape == "dict":
                found = ["%s %s" % ("✓" if self.project.find(k) else "✗", k) for k in keys]
                text += "\nKeys found in your function:  " + "   ".join(found)
            muted(b, text, wrap=560, size=9).pack(anchor="w")
        else:
            muted(b, "The result is not used.", wrap=560, size=9).pack(anchor="w")
        self._update_buttons()

    def _shape(self):
        if self.path and self.params.get("function"):
            return return_shape(self.path, self.params["function"])
        return None, None

    def _update_buttons(self):
        ok_inputs = self.ed.app is not None and self.fn is not None and bool(self._fn_args())
        ok_results = self.ed.app is not None and bool(self.params.get("function"))
        self.create_inputs_btn.state(["!disabled"] if ok_inputs else ["disabled"])
        self.create_results_btn.state(["!disabled"] if ok_results else ["disabled"])
        if self.ed.app is None:
            self.args_note.configure(text="")

    # ---- create widgets ----
    def create_inputs(self):
        if self.fn is None or self.ed.app is None:
            return
        wanted = [a for a in self._fn_args()
                  if self.arg_rows.get(a["name"], {}).get("state", {}).get("source") != "all"]
        if not wanted:
            return
        group, mapping = create_input_group(self.project, self.ed.host_id, self.fn["name"],
                                            wanted)
        for name, wid in mapping.items():
            row = self.arg_rows.get(name)
            if row is not None:
                row["state"].update(source="widget", widget=wid)
        self._save_args()
        self.ed.app.commit("Created inputs for %s()." % self.fn["name"])
        self._build_args()
        self.ed.error.configure(text="")
        self.args_note.configure(text="✓ Added “%s” with %d input%s." % (
            group["props"]["text"], len(mapping), "" if len(mapping) == 1 else "s"),
            fg=theme.RUN)

    def create_results(self):
        if self.ed.app is None or not self.params.get("function"):
            return
        shape, data = self._shape()
        if shape is None or shape == "single":
            shape, data = "single", None
        group, ids = create_result_group(self.project, self.ed.host_id, shape, data)
        if shape == "dict":
            self.params["result"] = "dict"
        elif shape == "tuple":
            self.params["result"] = "tuple"
            self.params["result_widgets"] = ids
        else:
            self.params["result"] = "widget"
            self.params["result_widget"] = ids[0] if ids else ""
        self.mode_cb.current(self.mode_values.index(self.params["result"]))
        if group is not None:
            self.ed.app.commit("Created result labels for %s()." % self.params["function"])
        self._show_result()
        self.ed._update_preview()


def action_problem(action, project):
    """A friendly description of what is wrong with an action, or None."""
    spec = ACTIONS.get(action.get("type"))
    if spec is None:
        return "This action is unknown and will be skipped."
    params = action.get("params", {})
    for p in spec["params"]:
        opts = p.get("options") or {}
        v = params.get(p["key"])
        if p["kind"] in ("widget", "window", "tab"):
            if action["type"] == "call_function" and p["key"] == "result_widget" and \
                    params.get("result") != "widget":
                continue
            if not v:
                if opts.get("optional"):
                    continue
                return "“%s” is not chosen yet – click ✎ to fix it." % p["label"]
            if project.find(v) is None:
                return "“%s” no longer exists, so this action will be skipped. " \
                       "Click ✎ to choose another one." % v
        if p["kind"] == "widgets" and isinstance(v, list):
            if action["type"] == "call_function" and params.get("result") != "tuple":
                continue
            gone = [x for x in v if project.find(x) is None]
            if gone:
                return "“%s” no longer exists. Click ✎ to fix it." % gone[0]
        if p["kind"] == "args" and isinstance(v, list):
            for a in v:
                if a.get("source") == "widget":
                    if not a.get("widget"):
                        return "No widget is chosen for “%s”. Click ✎ to fix it." % a.get("name")
                    if project.find(a["widget"]) is None:
                        return "“%s” no longer exists (used for %s). Click ✎ to fix it." % (
                            a["widget"], a.get("name"))
        if p["kind"] in ("module", "function") and not v:
            return "No function is chosen yet – click ✎ to pick one."
    for p in spec["params"]:
        v = params.get(p["key"])
        if p["kind"] in ("text", "multiline") and isinstance(v, str):
            for name in model.PLACEHOLDER_RE.findall(v):
                if project.find(name) is None:
                    return "There is no widget called “%s” any more, so {%s} will be shown " \
                           "as plain text. Click ✎ to fix it." % (name, name)
    return None


def edit_action(parent, project, action=None, context="", app=None, host_id=None):
    """Open the action editor. Returns a new action dict, or None if cancelled."""
    return ActionEditor(parent, project, action, context, app=app, host_id=host_id).run()


# ----------------------------------------------------------------------------
# Action list editor (used by the inspector and the menu editor)
# ----------------------------------------------------------------------------

class _IconBar(tk.Canvas):
    """✎ ↑ ↓ ✕ drawn on one canvas (one native window instead of four)."""

    ICONS = (("edit", "✎", "Change this action"), ("up", "↑", "Move up (run earlier)"),
             ("down", "↓", "Move down (run later)"), ("del", "✕", "Remove this action"))
    def __init__(self, parent, command, bg):
        self.STEP = st = px(22)
        super().__init__(parent, width=st * 4, height=st, bg=bg, highlightthickness=0,
                         bd=0, cursor="hand2")
        self.command = command
        self.enabled = {k: True for k, _t, _h in self.ICONS}
        self.items = {}
        for i, (key, glyph, _tip) in enumerate(self.ICONS):
            x = i * st + st // 2
            bgid = self.create_rectangle(x - st // 2 + 1, 1, x + st // 2 - 1, st - 1,
                                         outline="", fill="")
            tid = self.create_text(x, st // 2, text=glyph, font=font(10),
                                   fill=theme.DANGER if key == "del" else theme.TEXT)
            self.items[key] = (bgid, tid)
        self.tip = theme.tooltip(self, "", delay=400)
        self.bind("<Motion>", self._hover)
        self.bind("<Leave>", lambda e: self._hover(None))
        self.bind("<Button-1>", self._click)
        self._hot = None

    def _key_at(self, x):
        i = int(x // self.STEP)
        return self.ICONS[i][0] if 0 <= i < len(self.ICONS) else None

    def _hover(self, e):
        key = self._key_at(e.x) if e is not None else None
        if key is not None and not self.enabled[key]:
            key = None
        if key == self._hot:
            return
        if self._hot:
            self.itemconfigure(self.items[self._hot][0], fill="")
        self._hot = key
        if key:
            self.itemconfigure(self.items[key][0], fill=theme.ACCENT_LIGHT)
        text = next((t for k, _g, t in self.ICONS if k == key), "")
        self.tip._hide()
        self.tip.text = text
        if text:
            self.tip._schedule()

    def _click(self, e):
        key = self._key_at(e.x)
        if key and self.enabled[key]:
            self.tip._hide()
            self.command(key)

    def set_enabled(self, key, on):
        if self.enabled[key] == on:
            return
        self.enabled[key] = on
        self.itemconfigure(self.items[key][1], fill=(theme.DANGER if key == "del" else theme.TEXT)
                           if on else theme.BORDER)


class _Card:
    """One action row (pooled and reused)."""

    def __init__(self, owner):
        self.owner = owner
        self.index = 0
        self.frame = tk.Frame(owner, bg=theme.PANEL, highlightthickness=1,
                              highlightbackground=theme.BORDER)
        self.bar = _IconBar(self.frame, self._cmd, theme.PANEL)
        self.bar.pack(side="right", anchor="n", padx=(0, 3), pady=3)
        self.label = tk.Label(self.frame, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                              justify="left", cursor="hand2", font=font(9))
        self.label.pack(side="left", fill="x", expand=True, padx=(8, 2), pady=5)
        self.label.bind("<Double-Button-1>", lambda e: owner.edit(self.index))
        self.tip = theme.tooltip(self.label, "")

    def _cmd(self, key):
        o, i = self.owner, self.index
        if key == "edit":
            o.edit(i)
        elif key == "up":
            o.move(i, -1)
        elif key == "down":
            o.move(i, 1)
        else:
            o.delete(i)

    def show(self, i, n, act, project, wrap):
        self.index = i
        summary = "%d.  %s" % (i + 1, model.action_summary(act, project))
        problem = action_problem(act, project)
        if problem:
            summary = "%d.  ⚠ %s" % (i + 1, model.action_summary(act, project))
        self.label.configure(text=summary, wraplength=wrap,
                             fg=theme.DANGER if problem else theme.TEXT)
        self.tip.text = problem or "Double-click to change this action"
        self.bar.set_enabled("up", i > 0)
        self.bar.set_enabled("down", i < n - 1)


class ActionListEditor(tk.Frame):
    """Shows a list of actions as sentences with ✎ ↑ ↓ ✕ and “＋ Add action”.

    The list is edited IN PLACE; on_change(description) is called after every
    change.  Rows are pooled: set_actions() on another list reuses them, so
    switching the selected widget doesn't create windows."""

    def __init__(self, parent, project, actions, on_change=None, context="",
                 wraplength=px(190), bg=None, empty_text="Nothing happens yet.", app=None,
                 host_id=None):
        bg = theme.BG if bg is None else bg
        super().__init__(parent, bg=bg)
        self.project = project
        self.actions = actions
        self.on_change = on_change
        self.context = context
        self.wraplength = wraplength
        self.bgc = bg
        self.app = app
        self.host_id = host_id
        self._cards = []
        self.empty = tk.Label(self, text=empty_text, bg=bg, fg=theme.MUTED, anchor="w",
                              font=font(9, "italic"))
        self.add_link = link_button(self, "＋ Add action", self.add, bg=bg,
                                    tip="Choose something that should happen")
        self.add_link.pack(anchor="w", pady=(2, 0))
        self.render()

    def set_actions(self, project, actions, context=None, host_id=None):
        self.project = project
        self.actions = actions
        if context is not None:
            self.context = context
        self.host_id = host_id
        self.render()

    def render(self):
        n = len(self.actions)
        if n == 0:
            if not self.empty.winfo_ismapped():
                self.empty.pack(fill="x", pady=(0, 4), before=self.add_link)
        else:
            self.empty.pack_forget()
        while len(self._cards) < n:
            self._cards.append(_Card(self))
        for i, card in enumerate(self._cards):
            if i < n:
                card.show(i, n, self.actions[i], self.project, self.wraplength)
                if not card.frame.winfo_manager():
                    card.frame.pack(fill="x", pady=(0, 4), before=self.add_link)
            elif card.frame.winfo_manager():
                card.frame.pack_forget()

    def _changed(self, desc):
        self.render()
        if self.on_change:
            self.on_change(desc)

    def _edit(self, action):
        return edit_action(self, self.project, action, self.context, app=self.app,
                           host_id=self.host_id)

    def add(self):
        act = self._edit(None)
        if act is not None:
            self.actions.append(act)
            self._changed("Added action: %s" % model.action_summary(act, self.project))

    def edit(self, i):
        if not 0 <= i < len(self.actions):
            return
        act = self._edit(self.actions[i])
        if act is not None and i < len(self.actions):
            self.actions[i] = act
            self._changed("Changed action: %s" % model.action_summary(act, self.project))

    def move(self, i, delta):
        j = i + delta
        if 0 <= i < len(self.actions) and 0 <= j < len(self.actions):
            self.actions[i], self.actions[j] = self.actions[j], self.actions[i]
            self._changed("Moved an action.")

    def delete(self, i):
        if 0 <= i < len(self.actions):
            act = self.actions.pop(i)
            self._changed("Removed action: %s" % model.action_summary(act, self.project))


# ----------------------------------------------------------------------------
# Menu bar editor
# ----------------------------------------------------------------------------

class MenuEditor(_Dialog):
    def __init__(self, parent, project, window, app=None):
        super().__init__(parent, "Menu bar of %s" % window["id"], size=(px(860), px(600)))
        self.project = project
        self.app = app
        self.window_id = window["id"]
        self.menus = copy.deepcopy(window["props"].get("menu") or [])
        for m in self.menus:
            m.setdefault("label", "Menu")
            m.setdefault("items", [])
            for it in m["items"]:
                if not it.get("separator"):
                    it.setdefault("label", "Item")
                    it.setdefault("actions", [])
                    it.setdefault("shortcut", "")
                    it.setdefault("hook", False)

        self.button_bar()
        intro = tk.Frame(self, bg=theme.ACCENT_LIGHT)
        intro.pack(side="top", fill="x")
        tk.Label(intro, bg=theme.ACCENT_LIGHT, fg=theme.TEXT, justify="left", anchor="w",
                 padx=16, pady=7, wraplength=px(820), font=font(9),
                 text="The menu bar sits at the top of the window (File, Edit, Help…). "
                      "Each menu has items; choose what each item does on the right.").pack(
            fill="x")

        # preview of the bar
        pv = tk.Frame(self, bg=theme.BG)
        pv.pack(side="top", fill="x", padx=16, pady=(10, 0))
        tk.Label(pv, text="Preview:", bg=theme.BG, fg=theme.MUTED, font=font(9)).pack(side="left")
        self.bar = tk.Label(pv, text="", bg=theme.PANEL, fg=theme.TEXT, anchor="w",
                            font=font(9), padx=8, pady=2, highlightthickness=1,
                            highlightbackground=theme.BORDER)
        self.bar.pack(side="left", fill="x", expand=True, padx=(6, 0))

        main = ttk.Frame(self, padding=(16, 8, 16, 0))
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="y")
        tf = ttk.Frame(left)
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, show="tree", selectmode="browse", height=14)
        self.tree.column("#0", width=px(260))
        vsb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.tag_configure("menu", font=font(10, "bold"))
        self.tree.tag_configure("sep", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_details())

        b1 = ttk.Frame(left)
        b1.pack(fill="x", pady=(6, 0))
        ttk.Button(b1, text="＋ Menu", style="Small.TButton", command=self.add_menu).pack(
            side="left")
        ttk.Button(b1, text="＋ Item", style="Small.TButton", command=self.add_item).pack(
            side="left", padx=4)
        ttk.Button(b1, text="＋ Separator", style="Small.TButton",
                   command=self.add_separator).pack(side="left")
        b2 = ttk.Frame(left)
        b2.pack(fill="x", pady=(4, 0))
        self.up_btn = ttk.Button(b2, text="↑ Up", style="Small.TButton",
                                 command=lambda: self.move(-1))
        self.up_btn.pack(side="left")
        self.down_btn = ttk.Button(b2, text="↓ Down", style="Small.TButton",
                                   command=lambda: self.move(1))
        self.down_btn.pack(side="left", padx=4)
        self.del_btn = ttk.Button(b2, text="✕ Remove", style="Small.TButton",
                                  command=self.remove)
        self.del_btn.pack(side="left")

        self.details = ttk.Frame(main, padding=(18, 0, 0, 0))
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

    def _item_text(self, it):
        if it.get("separator"):
            return "─────────────"
        n = len(it.get("actions", []))
        extra = ("   " + it["shortcut"]) if it.get("shortcut") else ""
        busy = n or it.get("hook")
        return "%s%s%s" % (it.get("label") or "(no name)", extra,
                           "" if busy else "   (does nothing yet)")

    def _rebuild_tree(self):
        t = self.tree
        t.delete(*t.get_children(""))
        for mi, m in enumerate(self.menus):
            t.insert("", "end", iid=self._iid(("m", mi)),
                     text="☰  " + (m.get("label") or "(no name)"), open=True, tags=("menu",))
            for ii, it in enumerate(m["items"]):
                t.insert(self._iid(("m", mi)), "end", iid=self._iid(("i", mi, ii)),
                         text=self._item_text(it), tags=("sep",) if it.get("separator") else ())
        self._update_bar()

    def _update_bar(self):
        if not self.menus:
            self.bar.configure(text="(no menu bar)", fg=theme.MUTED)
        else:
            self.bar.configure(text="    ".join(m.get("label") or "?" for m in self.menus),
                               fg=theme.TEXT)

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
                      style="Muted.TLabel", wraplength=px(480), justify="left").pack(
                anchor="w", pady=(4, 0))
            return
        if path[0] == "m":
            m = self.menus[path[1]]
            ttk.Label(d, text="Menu", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(d, text="Title shown in the menu bar", font=font(9, "bold")).pack(
                anchor="w", pady=(10, 2))
            var = tk.StringVar(value=m.get("label", ""))
            ent = ttk.Entry(d, textvariable=var, width=34)
            ent.pack(anchor="w")

            def on_label(*_a):
                m["label"] = var.get()
                self.tree.item(self._iid(path), text="☰  " + (var.get() or "(no name)"))
                self._update_bar()
            var.trace_add("write", on_label)
            n = len(m["items"])
            ttk.Label(d, text="This menu has %d item%s. “＋ Item” adds one." % (
                n, "" if n == 1 else "s"), style="Muted.TLabel").pack(anchor="w", pady=(10, 0))
            return
        it = self.menus[path[1]]["items"][path[2]]
        if it.get("separator"):
            ttk.Label(d, text="Separator", style="Heading.TLabel").pack(anchor="w")
            ttk.Label(d, text="A thin line that splits a menu into groups. It can't be clicked.",
                      style="Muted.TLabel", wraplength=px(480)).pack(anchor="w", pady=(4, 0))
            return
        menu = self.menus[path[1]]
        ttk.Label(d, text="Menu item", style="Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(d)
        grid.pack(fill="x", pady=(8, 0))
        ttk.Label(grid, text="Text", font=font(9, "bold")).grid(row=0, column=0, sticky="w")
        var = tk.StringVar(value=it.get("label", ""))
        ttk.Entry(grid, textvariable=var, width=30).grid(row=0, column=1, sticky="w",
                                                         padx=(10, 0))
        ttk.Label(grid, text="Keyboard shortcut", font=font(9, "bold")).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        sc = ShortcutEntry(grid, it.get("shortcut", ""),
                           on_change=lambda v: (it.__setitem__("shortcut", v),
                                                self._refresh_item_text(path, it)))
        sc.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(8, 0))

        hook_row = ttk.Frame(d)
        hook_row.pack(fill="x", pady=(10, 0))
        hook_var = tk.BooleanVar(value=bool(it.get("hook")))
        hook_cb = ttk.Checkbutton(hook_row, variable=hook_var)
        hook_cb.pack(side="left")

        def hook_text():
            return "Also call my code:  %s()" % model.menu_hook_name(menu.get("label", ""),
                                                                   it.get("label", ""))

        def on_hook():
            it["hook"] = bool(hook_var.get())
            self._refresh_item_text(path, it)
        hook_cb.configure(text=hook_text(), command=on_hook)
        ttk.Button(hook_row, text="Copy method", style="Small.TButton",
                   command=lambda: copy_to_clipboard(
                       self, menu_hook_stub(menu.get("label", ""), it.get("label", "")),
                       self.app, "Method copied – paste it into your code file.")).pack(
            side="left", padx=(10, 0))

        def on_item_label(*_a):
            it["label"] = var.get()
            hook_cb.configure(text=hook_text())
            self._refresh_item_text(path, it)
        var.trace_add("write", on_item_label)

        ttk.Label(d, text="When this item is chosen:", font=font(10, "bold")).pack(
            anchor="w", pady=(14, 4))
        outer, inner = theme.scrollable(d, bg=theme.BG)
        outer.pack(fill="both", expand=True)
        outer.canvas.configure(height=200)
        ActionListEditor(inner, self.project, it.setdefault("actions", []),
                         on_change=lambda desc: self._refresh_item_text(path, it),
                         context="Menu item “%s”: when chosen" % (it.get("label") or ""),
                         wraplength=px(360), app=self.app, host_id=self.window_id).pack(
            fill="x", padx=(0, 6))

    def _refresh_item_text(self, path, it):
        self.tree.item(self._iid(path), text=self._item_text(it))

    # ---- operations ----
    def add_menu(self):
        names = ["File", "Edit", "View", "Help"]
        name = next((n for n in names if n not in [m.get("label") for m in self.menus]), "Menu")
        path = self._selected_path()
        at = path[1] + 1 if path else len(self.menus)
        self.menus.insert(at, {"label": name, "items": []})
        self._rebuild_tree()
        self._select(("m", at))

    def _target_menu(self):
        path = self._selected_path()
        if path is None:
            if not self.menus:
                self.menus.append({"label": "File", "items": []})
                return 0, 0
            return len(self.menus) - 1, len(self.menus[-1]["items"])
        if path[0] == "m":
            return path[1], len(self.menus[path[1]]["items"])
        return path[1], path[2] + 1

    def add_item(self):
        mi, at = self._target_menu()
        n = sum(1 for it in self.menus[mi]["items"] if not it.get("separator")) + 1
        self.menus[mi]["items"].insert(at, {"label": "Item %d" % n, "actions": [],
                                            "shortcut": "", "hook": False})
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


def edit_menu(parent, project, window, app=None):
    """Open the menu bar editor for a window. Returns the new menu list or None."""
    return MenuEditor(parent, project, window, app=app).run()
