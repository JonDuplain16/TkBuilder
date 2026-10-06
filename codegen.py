"""
Python code generator for TkBuilder (v2).

    generate(project)          -> {filename: source}   (split or single, per settings)
    generate_single(project)   -> source of one self-contained file
    export(project, folder)    -> writes the files, returns a report dict

Split mode (the default) produces two files:

    <base>_ui.py   regenerated on every export: class <Class>UI(tk.Tk) with the
                   widgets, the designed actions, a small runtime API
                   (get/set/values/on/log/run_in_background/...) and empty
                   "hook" methods;
    <base>.py      created once and never overwritten: class <Class>(<Class>UI)
                   where the engineer writes their own code.

Single mode produces one file with everything in class <Class>(tk.Tk).

The designer and this generator share tkmap (options, styles, initial state),
apptheme (theme style operations) and model (layout, actions), so the
generated program looks like the design.  Output is deterministic: the same
project always gives the same code.
"""

import ast
import keyword
import os
import re
import textwrap

import apptheme
import model
import tkmap
from model import ACTIONS, WIDGETS

IND = "    "
MAX_LINE = 99

# (widget type, model event key) -> event name used by the runtime's on()
API_EVENT = {
    ("Button", "command"): "click",
    ("Checkbutton", "command"): "click",
    ("Radiobutton", "command"): "click",
    ("Label", "on_click"): "click",
    ("Canvas", "on_click"): "click",
    ("Entry", "on_enter"): "enter",
    ("Entry", "on_change"): "change",
    ("Combobox", "on_select"): "select",
    ("Listbox", "on_select"): "select",
    ("Listbox", "on_double_click"): "double_click",
    ("Table", "on_select"): "select",
    ("Table", "on_double_click"): "double_click",
    ("Spinbox", "command"): "change",
    ("Scale", "command"): "change",
    ("FilePicker", "on_change"): "change",
    ("Notebook", "on_tab_change"): "tab_change",
    ("Window", "on_open"): "open",
    ("Window", "on_timer"): "timer",
}

# What get() returns, per widget type (for comments and the "How to use" list)
VALUE_TEXT = {
    "Entry": "str", "Text": "str", "Combobox": "str", "Spinbox": "int",
    "Scale": "number", "Checkbutton": "bool", "Radiobutton": "str (its group's value)",
    "Listbox": "str", "Table": "dict (selected row)", "Console": "str",
    "Label": "str", "Button": "str", "Progressbar": "float", "FilePicker": "str (path)",
    "Notebook": "str (visible tab)", "LabelFrame": "str", "Tab": "str",
}

# Option values that are pixels: scaled with px() on high-DPI screens
PIXEL_OPTIONS = {"length", "wraplength"}
PIXEL_SIZE_TYPES = ("Canvas", "Frame", "Notebook")
PIXEL_STYLE_OPTIONS = {"rowheight", "thickness", "sliderlength", "arrowsize", "padding"}

# Names of the runtime API: widget ids that clash get a "_widget" suffix
API_NAMES = {"get", "set", "values", "set_values", "widget", "on", "log",
             "run_in_background", "call_in_gui", "show_info", "show_warning",
             "show_error", "ask_yes_no", "open_window", "close_window", "destroy",
             "report_callback_exception"}

STD_IMPORTS = ["csv", "os", "queue", "subprocess", "sys", "threading", "time",
               "traceback", "webbrowser"]
TK_FROM = ["filedialog", "messagebox", "simpledialog", "ttk"]


# ---------------------------------------------------------------------------
# small string helpers
# ---------------------------------------------------------------------------

def py_str(s):
    """A Python string literal for s, preferring double quotes."""
    s = str(s)
    r = repr(s)
    if r.startswith("'") and '"' not in s:
        r = '"' + r[1:-1] + '"'
    return r


def _dq_body(s):
    """The inside of a double-quoted literal for s (escapes included)."""
    r = repr(str(s))
    body = r[1:-1]
    if r[0] == "'":
        body = body.replace("\\'", "'").replace('"', '\\"')
    return body


def _one_line(text):
    """Make text safe for a # comment (single line)."""
    return re.sub(r"\s*[\r\n]+\s*", " ", str(text)).strip()


def _doc(text):
    """Make text safe inside a \"\"\"docstring\"\"\"."""
    s = _one_line(text).replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    return s + " " if s.endswith('"') else s


def _ident(text, fallback="item"):
    s = re.sub(r"\W+", "_", str(text).strip().lower(), flags=re.ASCII).strip("_")
    if not s:
        s = fallback
    if s[0].isdigit():
        s = fallback + "_" + s
    if keyword.iskeyword(s):
        s += "_"
    return s


def _num(v):
    """int when whole, else float (for literals)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def _short(text, n=40):
    text = _one_line(text)
    return text[:n - 3] + "..." if len(text) > n else text


def block(src, ind=""):
    """Dedent a code template and indent every non-empty line with `ind`."""
    lines = textwrap.dedent(src).strip("\n").splitlines()
    return [(ind + line) if line.strip() else "" for line in lines]


def wrap_call(indent, prefix, args, suffix=")", cont=IND, extra=0):
    """prefix + ', '.join(args) + suffix, wrapped PEP 8 style when too long
    (continuation lines are indented by `cont`; `extra` = indentation that
    will be added later)."""
    limit = MAX_LINE - extra
    one = indent + prefix + ", ".join(args) + suffix
    if len(one) <= limit or not args:
        return [one]
    lines = [indent + prefix]
    cur = indent + cont
    for k, a in enumerate(args):
        piece = a + ("," if k < len(args) - 1 else suffix)
        if cur.strip() and len(cur) + 1 + len(piece) > limit:
            lines.append(cur.rstrip())
            cur = indent + cont
        cur += (" " if cur.strip() else "") + piece
    lines.append(cur.rstrip())
    return lines


class _Skip(Exception):
    """Raised by an action emitter when the action can't be generated."""


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------

class _Gen:
    def __init__(self, project, single, folder=None, project_dir=None):
        self.project = project
        self.single = single
        self.folder = folder
        self.project_dir = project_dir
        self.settings = project.settings
        self.ctx = tkmap.make_ctx(self.settings)
        self.theme = self.ctx["theme"]
        self.main = project.main_window
        self.main_id = self.main["id"]
        self.base = project.module_basename()
        self.cls = project.class_name()
        self.ui_cls = self.cls if single else self.cls + "UI"

        self.nodes, self.window_of, self.parent = {}, {}, {}
        for w in project.windows:
            for n in project.walk(w):
                self.nodes[n["id"]] = n
                self.window_of[n["id"]] = w["id"]
                for ch in n.get("children", []):
                    self.parent[ch["id"]] = n
        self.widgets = [n for n in project.walk() if n["type"] != "Window"]
        self.types = {n["type"] for n in self.widgets}
        self.need = set()            # helper features used by the generated code
        self.extra = 0               # indentation of the action code being generated
        self.dpi = bool(self.settings.get("dpi_aware", True))
        self.modules = {}            # backend module -> alias
        self.taken = set(API_NAMES)
        self.attr = {}
        for n in project.walk():
            nid = n["id"]
            self.attr[nid] = nid + "_widget" if nid in API_NAMES else nid
        self.taken |= set(self.attr.values())
        self._plan_radio_groups()
        self._plan_handlers()

    # ---- naming ------------------------------------------------------------
    def unique(self, base):
        prefix = "_" if base.startswith("_") else ""
        base = prefix + _ident(base, "item")
        name, i = base, 2
        while name in self.taken or keyword.iskeyword(name):
            name = "%s_%d" % (base, i)
            i += 1
        self.taken.add(name)
        return name

    def wexpr(self, node_or_id):
        """Code for the widget / window object."""
        nid = node_or_id if isinstance(node_or_id, str) else node_or_id["id"]
        if nid == self.main_id:
            return "self"
        return "self." + self.attr[nid]

    def _plan_radio_groups(self):
        self.groups = {}           # group name -> start value
        self.radio_group = {}      # radio id -> group name
        for n in self.widgets:
            if n["type"] == "Radiobutton":
                g = n["props"].get("group") or "group"
                self.groups.setdefault(g, "")
                self.radio_group[n["id"]] = g
                if n["props"].get("selected"):
                    self.groups[g] = tkmap.radio_value(n)

    def _plan_handlers(self):
        """Names for designed-action handlers and hooks (the user's methods)."""
        self.handlers = {}     # (node id, event) -> method name (designed actions)
        self.hooks = {}        # (node id, event) -> hook method name
        for n in self.project.walk():
            for ev, _label, verb in WIDGETS[n["type"]]["events"]:
                if ev in (n.get("hooks") or []):
                    self.hooks[(n["id"], ev)] = model.hook_name(n, ev)
        for name in self.hooks.values():
            self.taken.add(name)
        self.menu_hooks = {}
        for w in self.project.windows:
            for mi, menu in enumerate(w["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    if not item.get("separator") and item.get("hook"):
                        name = model.menu_hook_name(menu.get("label", ""), item.get("label", ""))
                        self.menu_hooks[(w["id"], mi, ii)] = name
                        self.taken.add(name)
        for n in self.project.walk():
            for ev, _label, verb in WIDGETS[n["type"]]["events"]:
                if n.get("events", {}).get(ev) or (n["type"] == "Window" and ev == "on_close"
                                                   and (n["id"], ev) in self.hooks):
                    self.handlers[(n["id"], ev)] = self.unique("_%s_%s" % (n["id"], verb))
        self.menu_handlers = {}
        for w in self.project.windows:
            prefix = "_menu" if w is self.main else "_%s_menu" % w["id"]
            for mi, menu in enumerate(w["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    if not item.get("separator") and item.get("actions"):
                        self.menu_handlers[(w["id"], mi, ii)] = self.unique("%s_%s_%s" % (
                            prefix, _ident(menu.get("label"), "menu"),
                            _ident(item.get("label"))))

    # ---- literals ------------------------------------------------------------
    def lit(self, v):
        if isinstance(v, tuple) and v and v[0] == apptheme.FAMILY:
            return "(" + ", ".join(["self._family"] + [self.lit(x) for x in v[1:]]) + ")"
        if isinstance(v, bool) or v is None:
            return repr(v)
        if isinstance(v, float) and v.is_integer():
            return repr(int(v))
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return py_str(v)
        if isinstance(v, tuple):
            items = [self.lit(x) for x in v]
            return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
        if isinstance(v, list):
            return "[" + ", ".join(self.lit(x) for x in v) + "]"
        if isinstance(v, dict):
            return "{" + ", ".join("%s: %s" % (self.lit(k), self.lit(x))
                                   for k, x in v.items()) + "}"
        return repr(v)

    def px(self, v):
        """A pixel size: px(n) when the app scales for high-DPI screens."""
        if isinstance(v, (tuple, list)):
            return "(" + ", ".join(self.px(x) for x in v) + ("," if len(v) == 1 else "") + ")"
        if not self.dpi or not _num(v):
            return self.lit(_num(v))
        return "px(%s)" % self.lit(_num(v))

    def opt(self, wtype, key, value):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and (
                key in PIXEL_OPTIONS or (key in ("width", "height")
                                         and wtype in PIXEL_SIZE_TYPES)):
            return "%s=%s" % (key, self.px(value))
        if key == "padding":
            return "padding=%s" % self.px(value)
        return "%s=%s" % (key, self.lit(value))

    def text_expr(self, template):
        """An action text with {widget_id} placeholders -> a Python expression
        (plain string, or an f-string reading the widgets with self.get)."""
        template = "" if template is None else str(template)
        parts = []          # ("lit", text) / ("get", widget_id)
        buf, i, n = [], 0, len(template)
        while i < n:
            if template.startswith("{{", i):
                buf.append("{")
                i += 2
                continue
            if template.startswith("}}", i):
                buf.append("}")
                i += 2
                continue
            m = re.match(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template[i:])
            if m and self.has_value(m.group(1)):
                if buf:
                    parts.append(("lit", "".join(buf)))
                    buf = []
                parts.append(("get", self.value_name(m.group(1))))
                i += m.end()
                continue
            buf.append(template[i])
            i += 1
        if buf:
            parts.append(("lit", "".join(buf)))
        if not any(kind == "get" for kind, _ in parts):
            return py_str("".join(t for _, t in parts))
        if len(parts) == 1:
            return 'self.get("%s")' % parts[0][1]
        out = []
        for kind, val in parts:
            if kind == "lit":
                out.append(_dq_body(val.replace("{", "{{").replace("}", "}}")))
            else:
                out.append("{self.get('%s')}" % val)
        return 'f"' + "".join(out) + '"'

    def path_expr(self, path):
        """A file path (may use placeholders); Windows backslashes become / ."""
        path = str(path or "").strip()
        if re.match(r"^[A-Za-z]:\\", path) or path.startswith("\\\\"):
            path = path.replace("\\", "/")
        return self.text_expr(path)

    def has_value(self, nid):
        node = self.nodes.get(nid)
        return node is not None and node["type"] in model.VALUE_WIDGETS + ["Button", "Tab",
                                                                           "LabelFrame"]

    def value_name(self, nid):
        """Name to pass to get(): radio buttons are read through their group."""
        if self.nodes[nid]["type"] == "Radiobutton":
            return self.radio_group[nid]
        return nid

    # ---- information tables --------------------------------------------------
    def number_info(self, node):
        """(int|float, title) for widgets whose get() converts to a number."""
        t, p = node["type"], node["props"]
        if t in ("Entry", "Combobox") and p.get("value_type") in ("int", "float"):
            return p["value_type"]
        if t == "Spinbox":
            return "float" if int(p.get("decimals") or 0) else "int"
        return None

    def field_title(self, node):
        """A friendly name for error messages: the label to the left, if any."""
        parent = self.parent.get(node["id"])
        if parent is not None and model.layout_mode(parent) == "grid":
            lay = node["layout"]
            for ch in parent["children"]:
                cl = ch["layout"]
                if ch["type"] == "Label" and cl["row"] == lay["row"] \
                        and cl["col"] + cl["colspan"] == lay["col"] \
                        and (ch["props"].get("text") or "").strip():
                    return _one_line(ch["props"]["text"]).rstrip(" :")
        return node["id"]

    def start_value(self, node):
        """The value set() needs to restore the widget's starting state."""
        t, p = node["type"], node["props"]
        state = tkmap.initial_state(node)
        if t in ("Entry", "Text", "Combobox", "FilePicker"):
            return state.get("text", "")
        if t == "Checkbutton":
            return bool(p.get("checked"))
        if t in ("Spinbox", "Scale"):
            return _num(p.get("value"))
        if t == "Listbox":
            return list(p.get("items") or [])
        if t == "Table":
            return state["rows"]
        return None

    def input_names(self, window):
        names = []
        for n in self.project.walk(window):
            if n["type"] == "Radiobutton":
                g = self.radio_group[n["id"]]
                if g not in names:
                    names.append(g)
            elif n["type"] in model.INPUT_TYPES:
                names.append(n["id"])
        return names

    def theme_color(self, key, fallback):
        pal = apptheme.palette(self.theme)
        return pal[key] if pal else fallback

    # =====================================================================
    # windows and widgets
    # =====================================================================
    def build_method(self, win):
        """Lines of _build_<window>()."""
        is_main = win is self.main
        wid = win["id"]
        p = win["props"]
        name = "_build_" + _ident(wid)
        out = [IND + "def %s(self):" % name,
               IND * 2 + '"""Create the %s "%s" and its widgets."""'
               % ("main window" if is_main else "window", _doc(p.get("title", "")))]
        ind = IND * 2
        w = self.wexpr(win)
        if is_main:
            out.append(ind + "window = self")
        else:
            out.append(ind + "window = %s = tk.Toplevel(self)" % w)
            out.append(ind + "window.withdraw()  # hidden until open_window(%s)" % py_str(wid))
        out.append(ind + "window.title(%s)" % py_str(p.get("title", "")))
        bg = dict(tkmap.widget_options(win, self.ctx)).get("bg")
        pad = int(p.get("padding") or 0)
        cfg = ["bg=%s" % (py_str(bg) if bg else 'self.style.lookup("TFrame", "background")')]
        if pad:
            cfg += ["padx=%s" % self.px(pad), "pady=%s" % self.px(pad)]
        out += wrap_call(ind, "window.configure(", cfg)
        self.need.add("place_window")
        start = p.get("start") or "center"
        out.append(ind + "self._place_window(window, %d, %d, %s)" % (
            int(p.get("width") or 480), int(p.get("height") or 360), py_str(start)))
        if int(p.get("min_width") or 0) or int(p.get("min_height") or 0):
            out.append(ind + "window.minsize(%s, %s)" % (self.px(p.get("min_width") or 0),
                                                         self.px(p.get("min_height") or 0)))
        if not p.get("resizable", True):
            out.append(ind + "window.resizable(False, False)")
        if p.get("topmost"):
            out.append(ind + 'window.attributes("-topmost", True)  # always on top')
        if p.get("icon"):
            self.need.add("icon")
            out.append(ind + "self._set_icon(window, %s)" % self.path_expr(p["icon"]))
        if not is_main:
            out.append(ind + 'window.protocol("WM_DELETE_WINDOW", lambda: self.close_window(%s))'
                       % py_str(wid))
        out += self.menu_lines(win, ind)
        out += self.weight_lines(win, "window", ind)
        for ch in self.sorted_children(win):
            out += self.widget_lines(ch, "window", ind)
        return name, out

    def menu_lines(self, win, ind):
        menus = win["props"].get("menu") or []
        if not menus:
            return []
        out = ["", ind + "# Menu bar", ind + "menubar = tk.Menu(window)"]
        used = {"menubar", "window", "self"}
        for mi, menu in enumerate(menus):
            var = _ident(menu.get("label"), "menu") + "_menu"
            base, k = var, 2
            while var in used:
                var = "%s%d" % (base, k)
                k += 1
            used.add(var)
            out.append(ind + "%s = tk.Menu(menubar, tearoff=False)" % var)
            for ii, item in enumerate(menu.get("items", [])):
                if item.get("separator"):
                    out.append(ind + "%s.add_separator()" % var)
                    continue
                args = ["label=%s" % py_str(item.get("label", ""))]
                command = self.menu_handlers.get((win["id"], mi, ii)) \
                    or self.menu_hooks.get((win["id"], mi, ii))
                if command:
                    args.append("command=self.%s" % command)
                seq = tkmap.shortcut_sequence(item.get("shortcut"))
                if seq:
                    args.append("accelerator=%s" % py_str(item["shortcut"]))
                out += wrap_call(ind, "%s.add_command(" % var, args)
                if seq and command:
                    out.append(ind + "window.bind(%s, lambda event: self.%s())"
                               % (py_str(seq), command))
            out.append(ind + "menubar.add_cascade(label=%s, menu=%s)"
                       % (py_str(menu.get("label", "")), var))
        out.append(ind + "window.configure(menu=menubar)")
        return out

    @staticmethod
    def sorted_children(container):
        kids = container.get("children", [])
        if model.layout_mode(container) == "grid":
            kids = sorted(kids, key=lambda ch: (ch["layout"].get("row", 0),
                                                ch["layout"].get("col", 0)))
        return kids

    def weight_lines(self, container, expr, ind):
        rows, cols = model.grid_weights(container)
        out = []
        if rows or cols:
            out.append(ind + "# These rows/columns grow when the window gets bigger")
        for r in sorted(rows):
            out.append(ind + "%s.rowconfigure(%d, weight=1)" % (expr, r))
        for c in sorted(cols):
            out.append(ind + "%s.columnconfigure(%d, weight=1)" % (expr, c))
        return out

    def grid_args(self, layout):
        args = ["row=%d" % int(layout.get("row", 0)), "column=%d" % int(layout.get("col", 0))]
        if int(layout.get("rowspan", 1)) > 1:
            args.append("rowspan=%d" % int(layout["rowspan"]))
        if int(layout.get("colspan", 1)) > 1:
            args.append("columnspan=%d" % int(layout["colspan"]))
        for k in ("padx", "pady"):
            if int(layout.get(k, 0) or 0):
                args.append("%s=%s" % (k, self.px(int(layout[k]))))
        sticky = model.sticky_for(layout)
        if sticky:
            args.append('sticky="%s"' % sticky)
        return args

    def comment(self, node):
        t = node["type"]
        text = node["props"].get("text")
        c = "%s (%s)" % (node["id"], WIDGETS[t]["name"])
        if t in ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame", "Tab") \
                and isinstance(text, str) and text.strip():
            c += ': "%s"' % _short(text)
        return "# " + c

    def widget_lines(self, node, parent, ind):
        """Code that creates one widget (and its children) inside `parent`."""
        t, nid = node["type"], node["id"]
        p = node["props"]
        me = self.wexpr(node)
        module, cls, _style = tkmap.WIDGET_CLASS[t]
        state = tkmap.initial_state(node)
        opts = tkmap.widget_options(node, self.ctx)
        out = ["", ind + self.comment(node)]
        if t == "Table" and not any(k == "selectmode" for k, _v in opts):
            opts.append(("selectmode", "browse"))  # (a Treeview's own default is "extended")
        items = [v for k, v in opts if k == "values"] if t == "Combobox" else []
        if items and len(self.lit(items[0])) > 50:
            opts = [(k, v) for k, v in opts if k != "values"]   # set below, wrapped
        else:
            items = []
        args = [self.opt(t, k, v) for k, v in opts]

        if t == "Tab":
            out += wrap_call(ind, "%s = ttk.Frame(" % me, [parent] + args)
            out.append(ind + "%s.add(%s, text=%s)" % (parent, me, py_str(p.get("text", ""))))
            out += self.weight_lines(node, me, ind)
            for ch in self.sorted_children(node):
                out += self.widget_lines(ch, me, ind)
            return out

        if state.get("image"):
            self.need.add("image")
            args.append("image=self._load_image(%s)" % self.path_expr(state["image"]))
        if t == "Checkbutton":
            self.need.add("vars")
            out.append(ind + 'self._vars["%s"] = tk.BooleanVar(self, value=%r)'
                       % (nid, bool(p.get("checked"))))
            args.append('variable=self._vars["%s"]' % nid)
        elif t == "Radiobutton":
            args.append('variable=self._groups[%s]' % py_str(self.radio_group[nid]))
        elif t == "Entry" and (state.get("placeholder") or node.get("events", {}).get("on_change")
                               or (nid, "on_change") in self.hooks):
            self.need.add("vars")
            out.append(ind + 'self._vars["%s"] = tk.StringVar(self)' % nid)
            args.append('textvariable=self._vars["%s"]' % nid)
        elif t == "FilePicker":
            types = tkmap.filetypes(node)
            args = ["mode=%s" % py_str(p.get("mode") or "open")]
            if types != [("All files", "*.*")]:
                args.append("filetypes=%s" % self.lit(types))
            if (p.get("button_text") or "Browse…") != "Browse…":
                args.append("button_text=%s" % py_str(p["button_text"]))
            args += [self.opt(t, k, v) for k, v in opts]
            self.need.add("FilePicker")

        cls_expr = "FilePicker" if t == "FilePicker" else "%s.%s" % (module, cls)
        target = parent
        wrapped = bool(state.get("scrollbar")) or (t == "Scale" and state.get("showvalue"))
        if wrapped:
            what = "its scrollbar" if t != "Scale" else "the number next to it"
            out.append(ind + "box = ttk.Frame(%s)  # holds the %s and %s"
                       % (parent, WIDGETS[t]["name"].lower(), what))
            out += wrap_call(ind, "box.grid(", self.grid_args(node["layout"]))
            target = "box"
        out += wrap_call(ind, "%s = %s(" % (me, cls_expr), [target] + args)
        if wrapped and t != "Scale":
            out.append(ind + 'scrollbar = ttk.Scrollbar(box, orient="vertical", command=%s.yview)'
                       % me)
            out.append(ind + "%s.configure(yscrollcommand=scrollbar.set)" % me)
            out.append(ind + 'scrollbar.pack(side="right", fill="y")')
            out.append(ind + '%s.pack(side="left", fill="both", expand=True)' % me)
        elif not wrapped:
            out += wrap_call(ind, "%s.grid(" % me, self.grid_args(node["layout"]))

        if items:
            out += wrap_call(ind, "%s.configure(values=[" % me, [py_str(x) for x in items[0]], "])")
        out += self.content_lines(node, state, me, ind)
        if model.is_container(t):
            out += self.weight_lines(node, me, ind)
            for ch in self.sorted_children(node):
                out += self.widget_lines(ch, me, ind)
        return out

    def content_lines(self, node, state, me, ind):
        """Starting content and extras (tooltips, shortcuts...) of a widget."""
        t, nid, p = node["type"], node["id"], node["props"]
        out = []
        if t == "Entry" and state.get("text"):
            out.append(ind + "%s.insert(0, %s)" % (me, py_str(state["text"])))
        elif t == "FilePicker" and state.get("text"):
            out.append(ind + "%s.set(%s)" % (me, py_str(state["text"])))
        elif t == "Text" and state.get("text"):
            out.append(ind + '%s.insert("1.0", %s)' % (me, py_str(state["text"])))
        elif t == "Combobox" and state.get("text"):
            out.append(ind + "%s.set(%s)" % (me, py_str(state["text"])))
        elif t == "Listbox" and state.get("items"):
            out += wrap_call(ind, "for item in [", [py_str(x) for x in state["items"]], "]:")
            out.append(ind + IND + '%s.insert("end", item)' % me)
        elif t == "Spinbox":
            dec = int(p.get("decimals") or 0)
            value = _num(state.get("value", 0))
            text = ("%%.%df" % dec) % value if dec else str(int(round(float(value))))
            out.append(ind + "%s.set(%s)" % (me, py_str(text)))
        elif t == "Table":
            cols = state["columns"]
            for k, c in enumerate(cols):
                out.append(ind + '%s.heading("c%d", text=%s, anchor=%s)'
                           % (me, k, py_str(c["title"]), py_str(c["anchor"])))
                out.append(ind + '%s.column("c%d", width=%s, anchor=%s)'
                           % (me, k, self.px(c["width"]), py_str(c["anchor"])))
            if state.get("striped"):
                self.need.add("stripes")
                out.append(ind + '%s.tag_configure("odd", background=%s)  # striped rows'
                           % (me, py_str(self.theme_color("heading", "#f3f4f6"))))
            if state.get("rows"):
                out += wrap_call(ind, "self.set(%s, [" % py_str(nid),
                                 [self.lit(r) for r in state["rows"]], "])")
        elif t == "Scale":
            self.need.add("scale")
            out.append(ind + "%s.set(%s)" % (me, self.lit(_num(state.get("value", 0)))))
            if state.get("showvalue"):
                vertical = p.get("orient") == "vertical"
                out.append(ind + 'self._value_labels["%s"] = label = ttk.Label(box, width=%d)'
                           % (nid, self.value_label_width(node)))
                if vertical:
                    out.append(ind + '%s.pack(side="top", fill="y", expand=True)' % me)
                    out.append(ind + 'label.pack(side="top")')
                else:
                    out.append(ind + '%s.pack(side="left", fill="x", expand=True)' % me)
                    out.append(ind + 'label.pack(side="left", padx=(%s, 0))' % self.px(6))
            out.append(ind + '%s.configure(command=lambda value: self._scale_moved("%s"))'
                       % (me, nid))
            out.append(ind + 'self._scale_moved("%s")  # snap to the step, show the number'
                       % nid)
        if state.get("readonly"):
            out.append(ind + '%s.configure(state="disabled")  # read only' % me)
        if state.get("disable_after_fill"):
            if t == "Entry":
                out.append(ind + "%s.state([%s])" % (me, py_str(p.get("state"))))
            elif t == "FilePicker":
                out.append(ind + "%s.set_enabled(False)" % me)
            else:
                out.append(ind + '%s.state(["disabled"])' % me)
        if t == "FilePicker" and p.get("state") == "disabled" and not state.get(
                "disable_after_fill"):
            out.append(ind + "%s.set_enabled(False)" % me)
        if state.get("placeholder"):
            self.need.add("Placeholder")
            out.append(ind + 'Placeholder(%s, self._vars["%s"], %s)'
                       % (me, nid, py_str(state["placeholder"])))
        if state.get("capture_print"):
            self.need.add("capture")
            out.append(ind + 'self._capture_print("%s")  # print() output appears here' % nid)
        if state.get("tooltip"):
            self.need.add("Tooltip")
            out.append(ind + "Tooltip(%s, %s)" % (me, py_str(state["tooltip"])))
        if t == "Button":
            seq = tkmap.shortcut_sequence(state.get("shortcut"))
            if seq:
                out.append(ind + "window.bind(%s, lambda event: %s.invoke())  # shortcut %s"
                           % (py_str(seq), me, _one_line(state["shortcut"])))
            if p.get("default"):
                out.append(ind + 'window.bind("<Return>", lambda event: %s.invoke())' % me)
        return out

    @staticmethod
    def value_label_width(node):
        p = node["props"]
        step = _num(p.get("resolution") or 1)
        dec = 0 if float(step).is_integer() else len(repr(float(step)).split(".")[1])
        width = max(len(("%%.%df" % dec) % float(p.get(k) or 0)) for k in ("from_", "to"))
        return max(3, width)

    # =====================================================================
    # event handlers and actions
    # =====================================================================
    def owner_sender(self, node):
        """Name of the widget to disable while a background call runs."""
        if node["type"] in model.ENABLE_TARGETS:
            return node["id"]
        if node["type"] == "Window":
            return node["id"]
        return None

    def handler_method(self, name, doc, actions, ctx, tail):
        ind = IND * 2
        out = [IND + "def %s(self):" % name, ind + '"""%s"""' % doc]
        body = self.actions(actions, ctx, ind, tail)
        out += body
        if not any(line.strip() and not line.strip().startswith("#") for line in body):
            out.append(ind + "pass")
        return out

    def actions(self, actions, ctx, ind, tail):
        """Lines for a list of actions; `tail` = lines to run after the last
        one (also after background calls finish)."""
        out = []
        level = ctx.get("base", 0) + len(ind)
        room = MAX_LINE - level - 2
        for k, act in enumerate(actions):
            self.extra = level
            spec = ACTIONS.get(act.get("type"))
            if spec is None:
                out.append(ind + "# skipped: unknown action %r" % act.get("type"))
                continue
            summary = _short(model.action_summary(act, self.project), room)
            params = {p["key"]: act.get("params", {}).get(p["key"], p["default"])
                      for p in spec["params"]}
            rest = actions[k + 1:]
            try:
                if act["type"] in ("call_function", "run_command"):
                    lines = getattr(self, "act_" + act["type"])(params, ctx, rest, tail)
                    out.append(ind + "# " + summary)
                    out += [(ind + line) if line else "" for line in lines]
                    return out
                lines = getattr(self, "act_" + act["type"])(params, ctx)
            except _Skip as e:
                out.append(ind + "# " + _short("skipped: %s (%s)" % (summary, e), room))
                continue
            out.append(ind + "# " + summary)
            out += [(ind + line) if line else "" for line in lines]
            if ctx.get("stop"):
                if rest:
                    out.append(ind + "# (the next actions don't run: %s)" % ctx["stop"])
                ctx["stop"] = None
                return out
        out += [ind + line for line in tail]
        return out

    # ---- action helpers --------------------------------------------------
    def w(self, indent, prefix, args, suffix=")"):
        """wrap_call() for action code (knows how deep it will be indented)."""
        return wrap_call(indent, prefix, args, suffix, extra=self.extra)

    def nested_actions(self, rest, ctx, tail):
        """The remaining actions, for the body of a 'when done' function."""
        level = self.extra
        saved = ctx.get("base", 0)
        ctx["base"] = level + len(IND)
        lines = self.actions(rest, ctx, "", tail)
        ctx["base"] = saved
        self.extra = level
        return lines

    def target(self, params, key="target", types=None):
        nid = params.get(key) or ""
        node = self.nodes.get(nid)
        if node is None or node["type"] == "Window":
            raise _Skip("no widget chosen" if not nid else "%r no longer exists" % nid)
        if types is not None and node["type"] not in types:
            raise _Skip("can't do that with a %s" % WIDGETS[node["type"]]["name"])
        return node

    def window_param(self, params):
        wid = params.get("window") or ""
        node = self.nodes.get(wid)
        if node is None or node["type"] != "Window":
            raise _Skip("no window chosen" if not wid else "window %r no longer exists" % wid)
        return node

    def gridded(self, node):
        """The object that is actually placed on the grid (box for wrapped widgets)."""
        state = tkmap.initial_state(node)
        if state.get("scrollbar") or (node["type"] == "Scale" and state.get("showvalue")):
            return self.wexpr(node) + ".master"
        return self.wexpr(node)

    # ---- the actions -------------------------------------------------------
    def act_call_function(self, p, ctx, rest, tail):
        module = (p.get("module") or "").strip()
        func = (p.get("function") or "").strip()
        if not module or not all(part.isidentifier() for part in module.split(".")):
            raise _Skip("no Python file chosen")
        if not func.isidentifier():
            raise _Skip("no function chosen")
        alias = self.module_alias(module)
        args = []
        for a in p.get("args") or []:
            name = (a.get("name") or "").strip()
            src = a.get("source") or "widget"
            if src == "all":
                expr = "self.values()"
            elif src == "widget":
                wid = a.get("widget") or ""
                if not self.has_value(wid):
                    raise _Skip("argument %s: widget %r doesn't exist" % (name or "?", wid))
                expr = 'self.get("%s")' % self.value_name(wid)
            else:
                expr = self.literal_expr(a.get("value"))
            args.append(("%s=%s" % (name, expr)) if name.isidentifier()
                        and not keyword.iskeyword(name) else expr)
        mode = p.get("result") or "none"
        background = bool(p.get("background"))
        if background:
            self.extra += len(IND)
        result_lines = self.result_lines(p, mode)
        if background:
            self.extra -= len(IND)
        busy = self.optional_widget(p.get("busy"), ["Progressbar"])
        fn = "%s.%s" % (alias, func)
        rest_lines = self.nested_actions(rest, ctx, tail) if background else             self.actions(rest, dict(ctx, base=self.extra), "", tail)
        has_rest = any(line.strip() and not line.strip().startswith("#") for line in rest_lines)
        out = []
        if not background:
            call = self.w("", ("result = " if mode != "none" else "") + fn + "(", args)
            if p.get("show_errors", True):
                out += call
            else:
                self.need.add("report_error")
                out += ["try:"] + [IND + line for line in call] + [
                    "except Exception as error:  # show it in the console / terminal only",
                    IND + "self._report_error(error, show=False)",
                    IND + "return"]
            return out + result_lines + rest_lines
        done = None
        if result_lines or has_rest:
            done = self.unique_local(ctx, "when_done")
            out.append("def %s(result):" % done)
            out.append(IND + '"""Runs when %s() has finished."""' % func)
            out += [(IND + line) if line else "" for line in result_lines + rest_lines]
            out.append("")
        kw = []
        if done:
            kw.append("on_done=" + done)
        if not p.get("show_errors", True):
            self.need.add("report_error")
            kw.append("on_error=lambda error: self._report_error(error, show=False)")
        if busy:
            kw.append("busy=%s" % py_str(busy))
        if ctx.get("sender"):
            kw.append("sender=%s" % py_str(ctx["sender"]))
        names = {a.split("=", 1)[0] for a in args if "=" in a}
        if names & {"on_done", "on_error", "busy", "sender", "func"}:
            # the function's own arguments clash with run_in_background's: wrap it
            out += ["def work():"] + self.w(IND, "return %s(" % fn, args)
            out += self.w("", "self.run_in_background(", ["work"] + kw)
        else:
            out += self.w("", "self.run_in_background(", [fn] + args + kw)
        return out

    def unique_local(self, ctx, base):
        ctx["locals"] = ctx.get("locals", 0) + 1
        return base if ctx["locals"] == 1 else "%s_%d" % (base, ctx["locals"])

    def literal_expr(self, value):
        text = "" if value is None else str(value).strip()
        if model.PLACEHOLDER_RE.search(text):
            return self.text_expr(text)
        if text:
            try:
                ast.literal_eval(text)
                return text
            except (ValueError, SyntaxError):
                pass
        return py_str(text)

    def optional_widget(self, nid, types):
        node = self.nodes.get(nid or "")
        if node is None or node["type"] not in types:
            return None
        return nid

    def result_lines(self, p, mode):
        if mode == "widget":
            node = self.nodes.get(p.get("result_widget") or "")
            if node is None or node["type"] == "Window":
                return ["# (no result widget chosen)"] if mode else []
            return ['self.set("%s", result)' % self.value_name(node["id"])]
        if mode == "tuple":
            names = [self.value_name(x) for x in p.get("result_widgets") or []
                     if self.nodes.get(x) and self.nodes[x]["type"] != "Window"]
            if not names:
                return ["# (no result widgets chosen)"]
            return ["# the result is a tuple/list: one value per widget, in order"] + \
                self.w("", "for name, value in zip([", [py_str(n) for n in names],
                          "], result):") + [IND + "self.set(name, value)"]
        if mode == "dict":
            self.need.add("set_matching")
            return ["self._set_matching(result)  # dict keys = widget names"]
        return []

    def module_alias(self, module):
        if module not in self.modules:
            alias = _ident(module.replace(".", "_"), "module")
            if alias in STD_IMPORTS + TK_FROM + ["tk", "tkfont", "FONT_FAMILY", "HERE"] \
                    or alias in self.modules.values() or alias in (self.cls, self.ui_cls):
                alias += "_module"
            self.modules[module] = alias
        return self.modules[module]

    def act_run_command(self, p, ctx, rest, tail):
        command = (p.get("command") or "").strip()
        if not command:
            raise _Skip("no command given")
        self.need.add("run_command")
        kw = []
        out_target = self.optional_widget(p.get("output"), model.LOG_TARGETS)
        if out_target:
            kw.append("output=%s" % py_str(out_target))
        if (p.get("cwd") or "").strip():
            kw.append("cwd=%s" % self.path_expr(p["cwd"]))
        busy = self.optional_widget(p.get("busy"), ["Progressbar"])
        if busy:
            kw.append("busy=%s" % py_str(busy))
        if ctx.get("sender"):
            kw.append("sender=%s" % py_str(ctx["sender"]))
        if not p.get("show_errors", True):
            kw.append("show_errors=False")
        rest_lines = self.nested_actions(rest, ctx, tail)
        out = []
        if any(line.strip() and not line.strip().startswith("#") for line in rest_lines):
            done = self.unique_local(ctx, "when_done")
            out.append("def %s(exit_code):" % done)
            out.append(IND + '"""Runs when the command has finished without errors."""')
            out += [(IND + line) if line else "" for line in rest_lines]
            out.append("")
            kw.append("on_done=" + done)
        return out + self.w("", "self._run_command(", [self.text_expr(command)] + kw)

    def act_run_code(self, p, ctx):
        code = textwrap.dedent((p.get("code") or "").expandtabs(4)).strip("\n")
        if not code.strip():
            return ["pass  # (no code was written)"]
        lines = [line.rstrip() for line in code.splitlines()]
        try:
            compile("def _check(self):\n" + "\n".join(IND + line for line in lines),
                    "<your code>", "exec")
        except SyntaxError as e:
            return ["# Your code has a mistake (%s on line %s), so it was turned"
                    % (_one_line(e.msg), e.lineno - 1 if e.lineno else "?"),
                    "# into comments. Fix it and remove the # signs:"] + \
                   ["# " + line for line in lines]
        return lines

    def act_show_message(self, p, ctx):
        func = {"warning": "show_warning", "error": "show_error"}.get(p.get("icon"), "show_info")
        return self.w("", "self.%s(" % func,
                         [self.text_expr(p.get("message")), self.text_expr(p.get("title"))])

    def act_confirm(self, p, ctx):
        lines = self.w("", "if not self.ask_yes_no(",
                          [self.text_expr(p.get("message")), self.text_expr(p.get("title"))], "):")
        return lines + [IND + "return  # the user said No: stop here"]

    def act_ask_text(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        lines = self.w("", "answer = simpledialog.askstring(",
                          [self.text_expr(p.get("title")), self.text_expr(p.get("message")),
                           "parent=self._dialog_parent()"])
        self.need.add("dialog_parent")
        return lines + ["if answer is None:", IND + "return  # the user pressed Cancel: stop here",
                        'self.set("%s", answer)' % self.value_name(node["id"])]

    def act_log(self, p, ctx):
        node = self.target(p, types=model.LOG_TARGETS)
        return ['self.log(%s, "%s")' % (self.text_expr(p.get("text")), node["id"])]

    def act_check(self, p, ctx):
        node = self.target(p, types=model.VALUE_WIDGETS)
        t, nid = node["type"], node["id"]
        cond = p.get("condition") or "not_empty"
        value = self.text_expr(p.get("value"))
        pre = []
        if t in ("Entry", "Combobox", "Spinbox"):
            text = 'self.widget("%s").get().strip()' % nid    # the typed text, as it is
        else:
            text = 'str(self.get("%s")).strip()' % self.value_name(nid)
        if cond in ("checked", "unchecked") and t == "Checkbutton":
            fail = ('not self.get("%s")' if cond == "checked" else 'self.get("%s")') % nid
        elif cond in ("checked", "unchecked") and t == "Radiobutton":
            op = "!=" if cond == "checked" else "=="
            fail = 'self.get("%s") %s %s' % (self.radio_group[nid], op,
                                             py_str(tkmap.radio_value(node)))
        elif cond in ("checked", "unchecked") and t in ("Listbox", "Table"):
            fail = ('not self.get("%s")' if cond == "checked" else 'self.get("%s")') % nid
        elif cond in ("not_empty", "checked"):
            fail = "not " + text
        elif cond in ("empty", "unchecked"):
            fail = text
        elif cond == "equals":
            fail = "%s != str(%s)" % (text, value)
        elif cond == "not_equals":
            fail = "%s == str(%s)" % (text, value)
        elif cond == "contains":
            fail = "str(%s) not in %s" % (value, text)
        elif cond in ("number", "greater", "less"):
            self.need.add("to_number")
            pre.append("number = self._to_number(%s)  # None if it isn't a number" % text)
            if cond == "number":
                fail = "number is None"
            else:
                op = "<=" if cond == "greater" else ">="
                raw = str(p.get("value") or "").strip()
                try:
                    fail = "number is None or number %s %s" % (op, self.lit(_num(float(raw))))
                except ValueError:
                    pre.append("limit = self._to_number(%s)" % value)
                    fail = "number is None or limit is None or number %s limit" % op
        else:
            raise _Skip("unknown condition %r" % cond)
        lines = pre + ["if %s:" % fail]
        if (p.get("message") or "").strip():
            lines += [IND + line for line in self.w("", "self.show_warning(", [
                self.text_expr(p.get("message"))])]
        lines.append(IND + "return  # the check failed: stop here")
        return lines

    def act_set_text(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        return self.w("", "self.set(", ['"%s"' % self.value_name(node["id"]),
                                         self.text_expr(p.get("text"))])

    def act_append_text(self, p, ctx):
        node = self.target(p, types=["Label", "Entry", "Text", "Console"])
        text = self.text_expr(p.get("text"))
        if node["type"] == "Console":
            return ['self.log(%s, "%s")' % (text, node["id"])]
        self.need.add("append")
        new_line = p.get("newline", "yes") != "no"
        return ['self._append_text("%s", %s, new_line=%r)' % (node["id"], text, new_line)]

    def act_change_number(self, p, ctx):
        node = self.target(p, types=model.NUMBER_TARGETS)
        self.need.update(("add_number", "to_number"))
        return ['self._add_to_number("%s", %s)' % (node["id"], self.lit(_num(p.get("amount", 1))))]

    def act_clear(self, p, ctx):
        node = self.target(p, types=["Entry", "Text", "Listbox", "Combobox", "Label", "Spinbox",
                                     "Console", "Table", "FilePicker"])
        value = "[]" if node["type"] in ("Listbox", "Table") else '""'
        return ['self.set("%s", %s)' % (node["id"], value)]

    def act_reset_form(self, p, ctx):
        win = self.window_param(p)
        items = []
        for n in self.project.walk(win):
            if n["type"] == "Radiobutton":
                g = self.radio_group[n["id"]]
                item = "%s: %s" % (py_str(g), py_str(self.groups[g]))
                if item not in items:
                    items.append(item)
            elif n["type"] in model.INPUT_TYPES:
                items.append("%s: %s" % (py_str(n["id"]), self.lit(self.start_value(n))))
        if not items:
            raise _Skip("that window has no inputs")
        return self.w("", "self.set_values({", items, "})")

    def act_set_checked(self, p, ctx):
        node = self.target(p, types=["Checkbutton", "Radiobutton"])
        state = p.get("state") or "tick"
        if node["type"] == "Checkbutton":
            nid = node["id"]
            if state == "toggle":
                return ['self.set("%s", not self.get("%s"))' % (nid, nid)]
            return ['self.set("%s", %r)' % (nid, state != "untick")]
        group = py_str(self.radio_group[node["id"]])
        value = py_str(tkmap.radio_value(node))
        if state == "tick":
            return ["self.set(%s, %s)" % (group, value)]
        lines = ["if self.get(%s) == %s:" % (group, value),
                 IND + 'self.set(%s, "")  # now no option is chosen' % group]
        if state == "toggle":
            lines += ["else:", IND + "self.set(%s, %s)" % (group, value)]
        return lines

    def act_show_widget(self, p, ctx):
        node = self.target(p)
        if node["type"] == "Tab":
            nb = self.parent[node["id"]]
            return ["%s.add(%s)  # show the tab again" % (self.wexpr(nb), self.wexpr(node))]
        return ["%s.grid()" % self.gridded(node)]

    def act_hide_widget(self, p, ctx):
        node = self.target(p)
        if node["type"] == "Tab":
            nb = self.parent[node["id"]]
            return ["%s.hide(%s)" % (self.wexpr(nb), self.wexpr(node))]
        return ["%s.grid_remove()  # .grid() shows it again" % self.gridded(node)]

    def act_enable_widget(self, p, ctx):
        node = self.target(p, types=model.ENABLE_TARGETS)
        self.need.add("enable")
        return ['self._set_enabled("%s", True)' % node["id"]]

    def act_disable_widget(self, p, ctx):
        node = self.target(p, types=model.ENABLE_TARGETS)
        self.need.add("enable")
        return ['self._set_enabled("%s", False)' % node["id"]]

    def act_set_color(self, p, ctx):
        node = self.target(p, types=model.COLOR_TARGETS)
        args = ['"%s"' % node["id"]]
        if p.get("fg") and node["type"] != "Canvas":
            args.append("fg=%s" % py_str(p["fg"]))
        if p.get("bg"):
            args.append("bg=%s" % py_str(p["bg"]))
        if len(args) == 1:
            raise _Skip("no color chosen")
        self.need.add("colors")
        return ["self._set_colors(%s)" % ", ".join(args)]

    def act_add_item(self, p, ctx):
        node = self.target(p, types=["Listbox", "Combobox"])
        w, text = self.wexpr(node), self.text_expr(p.get("text"))
        if node["type"] == "Listbox":
            return ['%s.insert("end", %s)' % (w, text), '%s.see("end")' % w]
        return ['%s.configure(values=[*%s.cget("values"), %s])' % (w, w, text)]

    def act_remove_selected(self, p, ctx):
        node = self.target(p, types=["Listbox", "Table"])
        w = self.wexpr(node)
        if node["type"] == "Table":
            lines = ["%s.delete(*%s.selection())" % (w, w)]
            if tkmap.initial_state(node).get("striped"):
                self.need.add("stripes")
                lines.append("self._stripe(%s)  # keep the stripes even" % w)
            return lines
        return ["# (from the last to the first, so the positions don't shift)",
                "for index in reversed(%s.curselection()):" % w,
                IND + "%s.delete(index)" % w]

    def act_add_row(self, p, ctx):
        node = self.target(p, types=["Table"])
        parts = [self.text_expr(x.strip()) for x in str(p.get("values") or "").split("|")]
        self.need.add("add_row")
        return self.w("", 'self._add_row("%s", [' % node["id"], parts, "])")

    def act_export_table(self, p, ctx):
        node = self.target(p, types=["Table"])
        self.need.add("export_table")
        path = (p.get("path") or "").strip()
        if not path:
            self.need.add("dialog_parent")
            return ["path = filedialog.asksaveasfilename(",
                    IND + 'parent=self._dialog_parent(), defaultextension=".csv",',
                    IND + 'filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])',
                    "if not path:",
                    IND + "return  # the user pressed Cancel: stop here",
                    'self._save_table("%s", path)' % node["id"]]
        return ['self._save_table("%s", %s)' % (node["id"], self.path_expr(path))]

    def act_open_window(self, p, ctx):
        win = self.window_param(p)
        if win["id"] == self.main_id:
            return ["self.deiconify()", "self.lift()"]
        if p.get("modal"):
            return ['self.open_window("%s", modal=True)' % win["id"]]
        return ['self.open_window("%s")' % win["id"]]

    def act_close_window(self, p, ctx):
        win = self.window_param(p)
        if ctx.get("closing") == win["id"]:
            raise _Skip("this window is already closing")
        if win["id"] == self.main_id:
            ctx["stop"] = "the app is closing"
        return ['self.close_window("%s")' % win["id"]]

    def act_switch_tab(self, p, ctx):
        nid = p.get("tab") or ""
        node = self.nodes.get(nid)
        if node is None or node["type"] != "Tab":
            raise _Skip("no tab chosen" if not nid else "tab %r no longer exists" % nid)
        return ["%s.select(%s)" % (self.wexpr(self.parent[nid]), self.wexpr(node))]

    def act_set_title(self, p, ctx):
        win = self.window_param(p)
        return ["%s.title(%s)" % (self.wexpr(win), self.text_expr(p.get("text")))]

    def act_timer_control(self, p, ctx):
        win = self.window_param(p)
        if not int(win["props"].get("timer_ms") or 0):
            raise _Skip("that window has no timer (set 'Timer every' first)")
        if p.get("state") == "stop":
            return ['self._stop_timer("%s")' % win["id"]]
        return ['self._start_timer("%s")' % win["id"]]

    def act_quit_app(self, p, ctx):
        ctx["stop"] = "the app has quit"
        return ["self.destroy()", "return"]

    def act_choose_file(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        func = {"save": "asksaveasfilename", "folder": "askdirectory"}.get(
            p.get("mode"), "askopenfilename")
        self.need.add("dialog_parent")
        return ["path = filedialog.%s(parent=self._dialog_parent())" % func,
                "if not path:",
                IND + "return  # the user pressed Cancel: stop here",
                'self.set("%s", path)' % self.value_name(node["id"])]

    def act_load_file(self, p, ctx):
        node = self.target(p, types=["Text", "Entry", "Label", "Console"])
        if not (p.get("path") or "").strip():
            raise _Skip("no file name given")
        return ["path = " + self.path_expr(p.get("path")),
                "try:",
                IND + 'with open(path, encoding="utf-8", errors="replace") as file:',
                IND * 2 + 'self.set("%s", file.read())' % node["id"],
                "except OSError as error:",
                IND + 'self.show_error(str(error), "Could not open the file")',
                IND + "return"]

    def act_save_file(self, p, ctx):
        node = self.target(p, "source", types=["Text", "Entry", "Label", "Listbox", "Console"])
        if not (p.get("path") or "").strip():
            raise _Skip("no file name given")
        if node["type"] == "Listbox":
            text = '"\\n".join(%s.get(0, "end"))  # all items, one per line' % self.wexpr(node)
        else:
            text = 'str(self.get("%s"))' % node["id"]
        return ["text = " + text,
                "path = " + self.path_expr(p.get("path")),
                "try:",
                IND + 'with open(path, "w", encoding="utf-8") as file:',
                IND * 2 + "file.write(text)",
                "except OSError as error:",
                IND + 'self.show_error(str(error), "Could not save the file")',
                IND + "return"]

    def act_open_url(self, p, ctx):
        if not (p.get("url") or "").strip():
            raise _Skip("no address given")
        return ["webbrowser.open(%s)" % self.text_expr(p.get("url").strip())]

    # =====================================================================
    # events: handlers, connections, hooks
    # =====================================================================
    def event_doc(self, node, ev):
        label = next(e[1] for e in WIDGETS[node["type"]]["events"] if e[0] == ev)
        if node["type"] == "Window":
            return 'Window "%s": %s.' % (_doc(node["props"].get("title", "")),
                                         _doc(label.lower()))
        return "%s: %s." % (_doc(self.project.display_name(node)), _doc(label.lower()))

    def handlers_and_connections(self):
        """(handler method lines, _connect_events body lines)."""
        methods, connect = [], []
        ind = IND * 2
        for n in self.project.walk():
            for ev, _label, verb in WIDGETS[n["type"]]["events"]:
                hname = self.handlers.get((n["id"], ev))
                hook = self.hooks.get((n["id"], ev))
                if not hname and not hook:
                    continue
                if n["type"] == "Window" and ev == "on_close":
                    methods += [""] + self.closing_method(n, hname, hook)
                    connect.append(ind + "self._on_close[%s] = self.%s" % (py_str(n["id"]), hname))
                    if n is self.main:
                        connect.append(ind + 'self.protocol("WM_DELETE_WINDOW", self.%s)' % hname)
                    continue
                if hname:
                    tail = ["self.%s()  # your own code" % hook] if hook else []
                    ctx = {"sender": self.owner_sender(n) if ev != "on_open" else None,
                           "window": self.window_of[n["id"]]}
                    if n["type"] == "Window" and ev == "on_open":
                        ctx["sender"] = None
                    methods += [""] + self.handler_method(hname, self.event_doc(n, ev),
                                                          n["events"][ev], ctx, tail)
                target = hname or hook
                connect.append(ind + 'self.on("%s", "%s", self.%s)'
                               % (n["id"], API_EVENT[(n["type"], ev)], target))
        for w in self.project.windows:
            for mi, menu in enumerate(w["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    hname = self.menu_handlers.get((w["id"], mi, ii))
                    if not hname:
                        continue
                    hook = self.menu_hooks.get((w["id"], mi, ii))
                    doc = "Menu %s > %s." % (_doc(menu.get("label", "")),
                                             _doc(item.get("label", "")))
                    tail = ["self.%s()  # your own code" % hook] if hook else []
                    methods += [""] + self.handler_method(hname, doc, item["actions"],
                                                          {"sender": None, "window": w["id"]},
                                                          tail)
        return methods, connect

    def closing_method(self, win, hname, hook):
        ind = IND * 2
        wid = win["id"]
        tail = []
        if hook:
            tail += ["if self.%s() is False:" % hook,
                     IND + "return  # your method said: keep the window open"]
        tail.append('self._close("%s")' % wid)
        ctx = {"sender": None, "window": wid, "closing": wid}
        out = [IND + "def %s(self):" % hname,
               ind + '"""%s"""' % self.event_doc(win, "on_close")]
        out += self.actions(win["events"].get("on_close") or [], ctx, ind, tail)
        return out

    def hook_list(self):
        """[(name, docstring, node or None)] of every hook, in project order."""
        out = []
        for n in self.project.walk():
            for ev, label, _verb in WIDGETS[n["type"]]["events"]:
                name = self.hooks.get((n["id"], ev))
                if name:
                    doc = self.event_doc(n, ev)
                    if n["type"] == "Window" and ev == "on_close":
                        doc += " Return False to keep it open."
                    out.append((name, doc, n))
        seen = set()
        for w in self.project.windows:
            for mi, menu in enumerate(w["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    name = self.menu_hooks.get((w["id"], mi, ii))
                    if name and name not in seen:
                        seen.add(name)
                        out.append((name, "Menu %s > %s." % (_doc(menu.get("label", "")),
                                                             _doc(item.get("label", ""))), w))
        return out

    def hook_example(self, node, ind):
        """Comment lines showing how to use self.get/self.set nearby."""
        if node is None:
            return []
        win = self.nodes[self.window_of[node["id"]]]
        container = self.parent.get(node["id"], win)
        inputs, outputs = [], []
        for scope in (container, win):
            for n in self.project.walk(scope):
                if n["id"] == node["id"]:
                    continue
                if n["type"] in model.INPUT_TYPES and n not in inputs:
                    inputs.append(n)
                if n["type"] in ("Label", "Console", "Table", "Progressbar") and n not in outputs:
                    outputs.append(n)
        lines = []
        for n in inputs[:3]:
            name = self.value_name(n["id"])
            var = _ident(name, "value")
            if var in ("self", "value"):
                var = "value"
            lines.append('%s = self.get("%s")  # %s' % (var, name, self.value_type_text(n)))
        for n in outputs[:2]:
            if n["type"] == "Console":
                lines.append('self.log("Working...", "%s")' % n["id"])
            elif n["type"] == "Progressbar":
                lines.append('self.set("%s", 50)' % n["id"])
            elif n["type"] == "Table":
                lines.append('self.set("%s", [["row 1", 1.5], ["row 2", 2.5]])' % n["id"])
            else:
                lines.append('self.set("%s", "Done!")' % n["id"])
        if not lines:
            lines = ['self.show_info("It works!")']
        return [ind + "# " + line for line in lines]

    def value_type_text(self, node):
        t = node["type"]
        if t == "Radiobutton":
            return "str (chosen option)"
        num = self.number_info(node)
        if num:
            return num
        if t == "Scale":
            step = _num(node["props"].get("resolution") or 1)
            return "int" if isinstance(step, int) else "float"
        if t == "Listbox" and node["props"].get("selectmode") == "extended":
            return "list of str"
        if t == "Table" and node["props"].get("selectmode") == "extended":
            return "list of dicts"
        return VALUE_TEXT.get(t, "")

    # =====================================================================
    # the runtime API and helpers
    # =====================================================================
    def has(self, *types):
        return any(t in self.types for t in types)

    def present(self, types):
        return tuple(t for t in types if t in self.types)

    def kind_test(self, types):
        types = self.present(types)
        if len(types) == 1:
            return 'kind == "%s"' % types[0]
        return "kind in (%s)" % ", ".join('"%s"' % t for t in types)

    def branches(self, spec, ind, first="if"):
        """spec: [(types, body lines)] -> if/elif chain over `kind` for the
        types that exist in this project."""
        out = []
        for types, body in spec:
            if not self.present(types):
                continue
            out.append(ind + "%s %s:" % (first if not out else "elif", self.kind_test(types)))
            out += [(ind + IND + line) if line else "" for line in body]
        return out

    def class_tables(self):
        ind = IND
        out = [ind + "# Every widget by name -> its type (used by get(), set() and on())"]
        items = ['"%s": "%s"' % (n["id"], n["type"]) for n in self.widgets]
        out += self.dict_lines(ind, "_TYPES", items)
        out.append(ind + "_WINDOWS = %s  # the first one is the main window"
                   % self.lit(tuple(w["id"] for w in self.project.windows)))
        nums = []
        for n in self.widgets:
            kind = self.number_info(n)
            if kind:
                nums.append('"%s": (%s, %s)' % (n["id"], kind, py_str(self.field_title(n))))
        if nums:
            out.append(ind + "# Inputs that give numbers -> (int or float, name used in messages)")
            out += self.dict_lines(ind, "_NUMBERS", nums)
        steps = ['"%s": %s' % (n["id"], self.lit(_num(n["props"].get("resolution") or 1)))
                 for n in self.widgets if n["type"] == "Scale"]
        if steps:
            out.append(ind + "# Sliders -> their step (values are rounded to it)")
            out += self.dict_lines(ind, "_STEPS", steps)
        if self.radio_group:
            out.append(ind + "# Option buttons -> the name of their group")
            out += self.dict_lines(ind, "_RADIO_GROUP", ['"%s": %s' % (r, py_str(g))
                                                         for r, g in self.radio_group.items()])
        inputs = []
        for w in self.project.windows:
            names = self.input_names(w)
            if names:
                inputs.append((py_str(w["id"]), [py_str(x) for x in names]))
        out.append(ind + "# The inputs of each window, for values()")
        out += self.dict_lines(ind, "_INPUTS", inputs)
        consoles = ['"%s": (%r, %d)' % (n["id"], bool(n["props"].get("timestamps")),
                                        int(n["props"].get("max_lines") or 0))
                    for n in self.widgets if n["type"] == "Console"]
        out.append(ind + "# Consoles -> (add the time to each line?, most lines kept or 0)")
        out += self.dict_lines(ind, "_CONSOLES", consoles)
        timers = ['"%s": %d' % (w["id"], int(w["props"].get("timer_ms") or 0))
                  for w in self.project.windows
                  if int(w["props"].get("timer_ms") or 0)
                  and ((w["id"], "on_timer") in self.handlers or (w["id"], "on_timer") in self.hooks)]
        if timers:
            self.need.add("timers")
            out.append(ind + "# Windows with a timer -> milliseconds between ticks")
            out += self.dict_lines(ind, "_TIMERS", timers)
        return out

    @staticmethod
    def dict_lines(ind, name, items):
        """name = {items} on one line, or one item per line. An item may be a
        (key, [values]) pair, written as key: (values...) and wrapped."""
        flat = []
        for it in items:
            if isinstance(it, tuple):
                key, values = it
                it = "%s: (%s)" % (key, ", ".join(values) + ("," if len(values) == 1 else ""))
            flat.append(it)
        one = ind + "%s = {%s}" % (name, ", ".join(flat))
        if len(one) <= MAX_LINE:
            return [one]
        out = [ind + "%s = {" % name]
        for it, text in zip(items, flat):
            if isinstance(it, tuple) and len(ind + IND + text) >= MAX_LINE:
                out += wrap_call(ind + IND, it[0] + ": (", it[1], "),")
            else:
                out.append(ind + IND + text + ",")
        return out + [ind + "}"]

    def init_method(self, build_names):
        ind = IND * 2
        out = [IND + "def __init__(self):"]
        if self.settings.get("dpi_aware", True):
            out.append(ind + "make_dpi_aware()  # sharp text on high-resolution screens")
        out.append(ind + "super().__init__()")
        if self.dpi:
            out.append(ind + "set_scale(self)  # sizes below are scaled to this screen")
        out += [ind + "self._gui_thread = threading.current_thread()",
                ind + "self._gui_calls = queue.Queue()  # work for the GUI thread, see call_in_gui()",
                ind + "self._listeners = {}  # (name, event) -> your callbacks, see on()",
                ind + "self._running = {}  # background tasks per sender, see run_in_background()",
                ind + "self._on_close = {}  # window -> its 'when closed' actions"]
        if "vars" in self.need or self.has("Entry", "Checkbutton"):
            out.append(ind + "self._vars = {}  # variables of checkboxes and text boxes")
        if self.groups:
            out.append(ind + "# Option buttons with the same group share one variable")
            out += wrap_call(ind, "self._groups = {", [
                "%s: tk.StringVar(self, value=%s)" % (py_str(g), py_str(v))
                for g, v in self.groups.items()], "}")
        if "scale" in self.need:
            out.append(ind + "self._value_labels = {}  # slider name -> label showing its number")
            out.append(ind + "self._scale_values = {}  # slider name -> its last value")
        if "image" in self.need:
            out.append(ind + "self._images = []  # pictures must be kept, or they disappear")
        if "timers" in self.need:
            out.append(ind + "self._timer_jobs = {}  # window -> its next scheduled tick")
        if "capture" in self.need:
            out.append(ind + "self._streams = (sys.stdout, sys.stderr)  # restored on close")
        out.append(ind + "self._setup_theme()")
        for name in build_names:
            out.append(ind + "self.%s()" % name)
        out.append(ind + "self._connect_events()")
        out.append(ind + "self._process_gui_calls()")
        if (self.main_id, "on_open") in self.handlers or (self.main_id, "on_open") in self.hooks:
            out.append(ind + 'self.after(0, self._fire, "%s", "open")  # once the window shows'
                       % self.main_id)
        if "timers" in self.need and self.main_id in self.timer_windows():
            out.append(ind + 'self._start_timer("%s")' % self.main_id)
        return out

    def timer_windows(self):
        return [w["id"] for w in self.project.windows if int(w["props"].get("timer_ms") or 0)
                and ((w["id"], "on_timer") in self.handlers or (w["id"], "on_timer") in self.hooks)]

    # ---- public API --------------------------------------------------------
    def api_get(self):
        ind = IND * 2
        out = block('''
            def get(self, name):
                """Return the current value of the widget (or option group) `name`.

                Text boxes give text - or a number if their 'Value type' is a number
                (a friendly ValueError if what was typed isn't one). Number boxes and
                sliders give numbers, checkboxes True/False, lists the selected item,
                tables the selected row as a dict {column title: value} (None if no
                row is selected). Safe to call from a background thread.
                """
                if threading.current_thread() is not self._gui_thread:
                    return self._wait_for_gui(self.get, name)
            ''', IND)
        if self.groups:
            out += [ind + "if name in self._groups:  # an option group: the chosen value",
                    ind + IND + "return self._groups[name].get()"]
        out += [ind + "widget = self.widget(name)", ind + "kind = self._TYPES[name]"]
        num = "_NUMBERS" in "\n".join(self.tables_text)
        entry_body = ["text = widget.get()"]
        if num:
            entry_body.append("return self._number(name, text) if name in self._NUMBERS else text")
        else:
            entry_body = ["return widget.get()"]
        spec = [
            (("Entry", "Combobox", "Spinbox"), entry_body),
            (("FilePicker",), ["return widget.get()"]),
            (("Text", "Console"), ['return widget.get("1.0", "end-1c")']),
            (("Checkbutton",), ["return self._vars[name].get()"]),
            (("Radiobutton",), ["return self._groups[self._RADIO_GROUP[name]].get()"]),
            (("Scale",), block('''
                step = self._STEPS[name]
                value = round(float(widget.get()) / step) * step
                return int(round(value)) if float(step).is_integer() else round(value, 10)
                ''')),
            (("Progressbar",), ['return float(widget.cget("value"))']),
            (("Listbox",), block('''
                items = [widget.get(index) for index in widget.curselection()]
                if widget.cget("selectmode") in ("extended", "multiple"):
                    return items
                return items[0] if items else ""
                ''')),
            (("Table",), block('''
                titles = [widget.heading(column, "text") for column in widget["columns"]]
                rows = [dict(zip(titles, widget.item(row, "values")))
                        for row in widget.selection()]
                if str(widget.cget("selectmode")) == "extended":
                    return rows
                return rows[0] if rows else None
                ''')),
            (("Notebook",), ['return widget.tab(widget.select(), "text") if widget.tabs() else ""']),
            (("Tab",), ['return widget.master.tab(widget, "text")']),
            (("Label", "Button", "LabelFrame"), ['return str(widget.cget("text"))']),
        ]
        out += self.branches(spec, ind)
        out.append(ind + "return None  # this kind of widget has no value")
        return out

    def api_set(self):
        ind = IND * 2
        out = block('''
            def set(self, name, value):
                """Show `value` in the widget (or option group) `name`.

                Accepts the same kinds of values that get() returns. Also: a list
                replaces the items of a list or dropdown (text selects one item), a
                list of rows fills a table (rows can be lists or dicts by column
                title), text replaces a console. Safe to call from any thread.
                """
                if threading.current_thread() is not self._gui_thread:
                    self.call_in_gui(self.set, name, value)
                    return
            ''', IND)
        if self.groups:
            out += [ind + "if name in self._groups:",
                    ind + IND + 'self._groups[name].set("" if value is None else str(value))',
                    ind + IND + "return"]
        out += [ind + "widget = self.widget(name)", ind + "kind = self._TYPES[name]"]
        text = "self._format(value)"
        entry = []
        if self.has("Combobox"):
            entry += ['if kind == "Combobox" and isinstance(value, (list, tuple)):' if
                      self.has("Entry", "Spinbox") else "if isinstance(value, (list, tuple)):",
                      IND + "widget.configure(values=[self._format(item) for item in value])",
                      IND + "return"]
        if self.has("Spinbox"):
            entry += ['if kind == "Spinbox" and isinstance(value, (int, float)):'
                      if self.has("Entry", "Combobox") else "if isinstance(value, (int, float)):",
                      IND + 'value = str(widget.cget("format") or "%g") % value']
        entry += ["self._replace_text(widget, %s)" % text]
        spec = [
            (("Entry", "Combobox", "Spinbox"), entry),
            (("FilePicker",), ["widget.set(%s)" % text]),
            (("Text", "Console"), block('''
                state = widget.cget("state")
                widget.configure(state="normal")  # a read-only box can't be changed
                widget.delete("1.0", "end")
                widget.insert("1.0", self._format(value))
                widget.configure(state=state)
                ''')),
            (("Checkbutton",), ["self._vars[name].set(bool(value))"]),
            (("Radiobutton",), block('''
                group = self._groups[self._RADIO_GROUP[name]]
                if isinstance(value, bool):  # True chooses this option, False clears it
                    if value or group.get() == widget.cget("value"):
                        group.set(widget.cget("value") if value else "")
                else:
                    group.set(self._format(value))
                ''')),
            (("Scale",), ['widget.set(float(value))  # also shows the number']),
            (("Progressbar",), block('''
                maximum = float(widget.cget("maximum"))
                widget.configure(value=min(max(float(value or 0), 0), maximum))
                ''')),
            (("Listbox",), block('''
                if isinstance(value, (list, tuple)):  # new items
                    widget.delete(0, "end")
                    widget.insert("end", *[self._format(item) for item in value])
                    return
                widget.selection_clear(0, "end")  # select the item with this text
                items = widget.get(0, "end")
                if self._format(value) in items:
                    index = items.index(self._format(value))
                    widget.selection_set(index)
                    widget.see(index)
                ''')),
            (("Table",), ["self._fill_table(widget, value)"]),
            (("Notebook",), block('''
                for tab in widget.tabs():  # show the tab with this title
                    if widget.tab(tab, "text") == str(value):
                        widget.select(tab)
                ''')),
            (("Tab",), ["widget.master.tab(widget, text=self._format(value))"]),
            (("Label", "Button", "LabelFrame"), ["widget.configure(text=self._format(value))"]),
        ]
        body = self.branches(spec, ind)
        if body:
            out += body
            out += [ind + "else:", ind + IND + 'raise ValueError(f"{name} has no value to set")']
        else:
            out.append(ind + 'raise ValueError(f"{name} has no value to set")')
        if self.has("Entry", "Combobox", "Spinbox"):
            self.need.add("replace_text")
        if self.has("Table"):
            self.need.add("fill_table")
        return out

    def api_rest(self):
        """values, set_values, widget, on, log, background work, messages, windows."""
        out = block('''
            def values(self, window=None):
                """Return {name: value} of every input (of one window, or of all).

                Option buttons appear once, under their group name.
                """
                if threading.current_thread() is not self._gui_thread:
                    return self._wait_for_gui(self.values, window)
                result = {}
                for window_name, names in self._INPUTS.items():
                    if window in (None, window_name):
                        for name in names:
                            result[name] = self.get(name)
                return result

            def set_values(self, mapping):
                """Set many widgets at once: set_values({"name": value, ...})."""
                for name, value in mapping.items():
                    self.set(name, value)

            def widget(self, name):
                """The real tkinter widget called `name` (for advanced use)."""
                if name not in self._TYPES:
                    raise KeyError(f'There is no widget called "{name}"')
                return getattr(self, %s)
            ''' % ("name" if all(v == k for k, v in self.attr.items())
                   else 'name + "_widget" if name in %s else name' % self.lit(
                       tuple(sorted(k for k, v in self.attr.items() if v != k)))), IND)
        alias_lines = []
        if self.has("Checkbutton", "Radiobutton"):
            alias_lines += ['if event == "change" and %s:' % self.kind_test(
                ("Checkbutton", "Radiobutton")), IND + 'event = "click"  # the same for these']
        if self.has("Combobox", "Listbox", "Table"):
            alias_lines += ['%s event == "change" and %s:' % (
                "elif" if alias_lines else "if", self.kind_test(("Combobox", "Listbox", "Table"))),
                IND + 'event = "select"  # the same for these']
        out += [""] + block('''
            def on(self, name, event, callback):
                """Call callback() (no arguments) when something happens to a widget.

                event: "click", "change", "select", "enter", "double_click" or
                "tab_change"; for windows "open" or "timer". Example:
                    self.on("run_button", "click", self.run_simulation)
                """
                if name in self._WINDOWS:
                    kind = "Window"
                else:
                    kind = self._TYPES.get(name)
                    if kind is None:
                        raise KeyError(f'There is no widget called "{name}"')
            ''', IND)
        out += [IND * 2 + line if line else "" for line in alias_lines]
        out += block('''
            if (name, event) not in self._listeners:
                self._listeners[name, event] = []
                self._connect(name, kind, event)
            self._listeners[name, event].append(callback)
            ''', IND * 2)
        out += [""] + block('''
            def log(self, text, console=None):
                """Add a line of text to a console (the first one if not named).

                Also works with text areas and lists. Safe to call from any thread.
                """
                if threading.current_thread() is not self._gui_thread:
                    self.call_in_gui(self.log, text, console)
                    return
                if console is None:
                    if not self._CONSOLES:
                        print(text)
                        return
                    console = next(iter(self._CONSOLES))
                widget = self.widget(console)
            ''', IND)
        if self.has("Listbox"):
            out += block('''
                if isinstance(widget, tk.Listbox):
                    widget.insert("end", str(text))
                    widget.see("end")
                    return
                ''', IND * 2)
        out += block('''
                timestamps, max_lines = self._CONSOLES.get(console, (False, 0))
                if timestamps:
                    text = time.strftime("%H:%M:%S  ") + str(text)
                state = widget.cget("state")
                widget.configure(state="normal")
                if widget.index("end-1c") != "1.0":
                    widget.insert("end", "\\n")
                widget.insert("end", str(text))
                if max_lines:  # forget the oldest lines
                    extra = int(widget.index("end-1c").split(".")[0]) - max_lines
                    if extra > 0:
                        widget.delete("1.0", f"{extra + 1}.0")
                widget.configure(state=state)
                widget.see("end")
            ''', IND * 2)
        out += [""] + block('''
            def run_in_background(self, func, *args, on_done=None, on_error=None, busy=None,
                                  sender=None, **kwargs):
                """Run func(*args, **kwargs) in a background thread.

                The window stays responsive meanwhile. When it finishes,
                on_done(result) - or on_error(error) - runs in the GUI thread
                (without on_error, errors are shown as a message). busy = name of a
                progress bar that moves while it runs; sender = name of a widget
                (e.g. the button) that is disabled while it runs.
                """
                self._task_started(busy, sender)

                def work():
                    try:
                        result = func(*args, **kwargs)
                    except Exception as error:  # handed to on_error in the GUI thread
                        self.call_in_gui(finish, None, error)
                    else:
                        self.call_in_gui(finish, result, None)

                def finish(result, error):
                    self._task_finished(busy, sender)
                    if error is None:
                        if on_done is not None:
                            on_done(result)
                    elif on_error is not None:
                        on_error(error)
                    else:
                        self._report_error(error)

                threading.Thread(target=work, daemon=True).start()

            def call_in_gui(self, func, *args):
                """Run func(*args) soon in the GUI thread (use it from other threads)."""
                self._gui_calls.put((func, args))

            def show_info(self, message, title=None):
                """Show a message box. Safe to call from any thread."""
                self._message(messagebox.showinfo, message, title)

            def show_warning(self, message, title=None):
                """Show a warning message box. Safe to call from any thread."""
                self._message(messagebox.showwarning, message, title)

            def show_error(self, message, title=None):
                """Show an error message box. Safe to call from any thread."""
                self._message(messagebox.showerror, message, title)

            def ask_yes_no(self, message, title=None):
                """Ask a Yes/No question; True if the user said Yes."""
                if threading.current_thread() is not self._gui_thread:
                    return self._wait_for_gui(self.ask_yes_no, message, title)
                return messagebox.askyesno(title or self.title(), str(message),
                                           parent=self._dialog_parent())

            def open_window(self, name, modal=False):
                """Show the window `name` (modal=True blocks the other windows)."""
                window = self._window(name)
                window.deiconify()
                window.lift()
                window.focus_set()
                if modal and window is not self:
                    window.grab_set()
            ''', IND)
        self.need.update(("dialog_parent", "report_error"))
        if "timers" in self.need:
            out += [IND * 2 + "if name in self._TIMERS:", IND * 3 + "self._start_timer(name)"]
        out += [IND * 2 + 'self._fire(name, "open")']
        out += [""] + block('''
            def close_window(self, name):
                """Close the window `name` (its 'when closed' actions run first).

                Closing the main window ends the app. Other windows are only
                hidden: their widgets keep their values and still work with
                get() and set().
                """
                if name in self._on_close:
                    self._on_close[name]()
                else:
                    self._close(name)
            ''', IND)
        return out

    # ---- internal helpers ----------------------------------------------------
    def helpers(self):
        """Private helper methods: always-needed ones plus those used."""
        h = self.need
        out = []

        def add(src):
            out.append("")
            out.extend(block(src, IND))

        add('''
            def _connect(self, name, kind, event):
                """Make the widget call self._fire(name, event) when `event` happens."""
                def fire(*args):
                    self._fire(name, event)

                widget = self.widget(name) if kind != "Window" else None
            ''')
        spec = []
        if self.has("Button", "Checkbutton", "Radiobutton"):
            spec.append(('%s and event == "click"' % self.kind_test(
                ("Button", "Checkbutton", "Radiobutton")), ["widget.configure(command=fire)"]))
        if self.has("Label", "Canvas"):
            spec.append(('%s and event == "click"' % self.kind_test(("Label", "Canvas")),
                         ['widget.bind("<Button-1>", fire, add="+")']))
        if self.has("Entry"):
            spec.append(('kind == "Entry" and event == "enter"',
                         ['widget.bind("<Return>", fire, add="+")']))
            self.need.add("vars")
            spec.append(('kind == "Entry" and event == "change"', block('''
                if name not in self._vars:  # a variable notices every change
                    self._vars[name] = tk.StringVar(self, value=widget.get())
                    widget.configure(textvariable=self._vars[name])
                self._vars[name].trace_add("write", fire)
                ''')))
        if self.has("Combobox"):
            spec.append(('kind == "Combobox" and event == "select"',
                         ['widget.bind("<<ComboboxSelected>>", fire, add="+")']))
        if self.has("Listbox"):
            spec.append(('kind == "Listbox" and event == "select"',
                         ['widget.bind("<<ListboxSelect>>", fire, add="+")']))
        if self.has("Table"):
            spec.append(('kind == "Table" and event == "select"',
                         ['widget.bind("<<TreeviewSelect>>", fire, add="+")']))
        if self.has("Listbox", "Table"):
            spec.append(('%s and event == "double_click"' % self.kind_test(("Listbox", "Table")),
                         ['widget.bind("<Double-Button-1>", fire, add="+")']))
        if self.has("Spinbox"):
            spec.append(('kind == "Spinbox" and event == "change"',
                         ["widget.configure(command=fire)",
                          'widget.bind("<Return>", fire, add="+")']))
        if self.has("FilePicker"):
            spec.append(('kind == "FilePicker" and event == "change"',
                         ["widget.command = fire  # called after Browse..."]))
        if self.has("Notebook"):
            spec.append(('kind == "Notebook" and event == "tab_change"', block('''
                shown = [widget.select()]

                def tab_changed(event):  # only real changes (not while starting up)
                    if widget.select() != shown[0]:
                        shown[0] = widget.select()
                        fire()

                widget.bind("<<NotebookTabChanged>>", tab_changed, add="+")
                ''')))
        quiet = ['("Window", "open")', '("Window", "timer")']
        if self.has("Scale"):
            quiet.append('("Scale", "change")')
        for k, (cond, body) in enumerate(spec):
            out.append(IND * 2 + ("if " if k == 0 else "elif ") + cond + ":")
            out += [IND * 3 + line if line else "" for line in body]
        out.append(IND * 2 + ("elif " if spec else "if ") + "(kind, event) not in (%s):"
                   % ", ".join(quiet))
        out.append(IND * 3 + "# (windows and sliders call _fire() themselves)")
        out.append(IND * 3 + 'raise ValueError(f\'"{name}" has no "{event}" event\')')

        add('''
            def _fire(self, name, event):
                """Run every callback connected to (name, event) with on()."""
                for callback in self._listeners.get((name, event), ()):
                    callback()

            def _process_gui_calls(self):
                """Run the work that other threads sent with call_in_gui()."""
                while True:
                    try:
                        func, args = self._gui_calls.get_nowait()
                    except queue.Empty:
                        break
                    try:
                        func(*args)
                    except Exception as error:  # show it, but keep the app running
                        self._report_error(error)
                self._poll_job = self.after(40, self._process_gui_calls)

            def _wait_for_gui(self, func, *args):
                """Run func(*args) in the GUI thread and wait for its result."""
                finished = threading.Event()
                box = {}

                def run():
                    try:
                        box["result"] = func(*args)
                    except Exception as error:  # raised again in the calling thread
                        box["error"] = error
                    finally:
                        finished.set()

                self.call_in_gui(run)
                finished.wait()
                if "error" in box:
                    raise box["error"]
                return box["result"]

            def _task_started(self, busy, sender):
                """A background task starts: count it, disable its sender, animate busy."""
                self._running[sender] = self._running.get(sender, 0) + 1
                if sender in self._TYPES:
                    self._set_enabled(sender, False)
                if busy:
                    bar = self.widget(busy)
                    bar.configure(mode="indeterminate")
                    bar.start(15)

            def _task_finished(self, busy, sender):
                self._running[sender] -= 1
                if sender in self._TYPES and not self._running[sender]:
                    self._set_enabled(sender, True)
                if busy:
                    bar = self.widget(busy)
                    bar.stop()
                    bar.configure(mode="determinate", value=0)

            def _report_error(self, error, show=True):
                """Print the full error (to the console, if there is one) and show it."""
                details = "".join(traceback.format_exception(type(error), error,
                                                             error.__traceback__))
                if self._CONSOLES:
                    self.log(details.rstrip())
                elif sys.stderr is not None:
                    sys.stderr.write(details)
                if show and isinstance(error, ValueError):
                    self.show_warning(str(error), "Please check")
                elif show:
                    self.show_error(f"{type(error).__name__}: {error}", "Something went wrong")

            def report_callback_exception(self, exc_type, error, tb):
                """Errors in event handlers are shown as a message, not hidden."""
                self._report_error(error)

            def _message(self, show, message, title):
                if threading.current_thread() is not self._gui_thread:
                    self.call_in_gui(self._message, show, message, title)
                    return
                show(title or self.title(), str(message), parent=self._dialog_parent())

            def _dialog_parent(self):
                """The window that message boxes belong to (the one in front)."""
                try:
                    focus = self.focus_get()
                except (KeyError, tk.TclError):
                    focus = None
                return focus.winfo_toplevel() if focus is not None else self

            def _window(self, name):
                if name == self._WINDOWS[0]:
                    return self
                if name not in self._WINDOWS:
                    raise KeyError(f'There is no window called "{name}"')
                return getattr(self, %s)

            def _close(self, name):
                """Really close a window: end the app, or hide another window."""
                if name == self._WINDOWS[0]:
                    self.destroy()
                    return
            ''' % ("name" if all(v == k for k, v in self.attr.items()) else
                   'name + "_widget" if name in %s else name' % self.lit(
                       tuple(sorted(k for k, v in self.attr.items() if v != k)))))
        if "timers" in h:
            out.append(IND * 2 + "self._stop_timer(name)")
        out += block('''
                window = self._window(name)
                window.grab_release()
                window.withdraw()

            def destroy(self):
                """Stop the background polling (and timers), then close the app."""
                self.after_cancel(self._poll_job)
            ''', IND)
        if "timers" in h:
            out += [IND * 2 + "for name in list(self._timer_jobs):",
                    IND * 3 + "self._stop_timer(name)"]
        if "capture" in h:
            out.append(IND * 2 + "sys.stdout, sys.stderr = self._streams  # print() as before")
        out.append(IND * 2 + "super().destroy()")

        h.add("enable")    # used by _task_started
        if "place_window" in h:
            add('''
                def _place_window(self, window, width, height, start):
                    """Give a window its size and put it where it should start."""%s
                    if start == "center":
                        x = (window.winfo_screenwidth() - width) // 2
                        y = (window.winfo_screenheight() - height) // 3
                        window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
                    else:
                        window.geometry(f"{width}x{height}")
                    if start == "maximized":
                        try:
                            window.state("zoomed")  # Windows and macOS
                        except tk.TclError:
                            window.attributes("-zoomed", True)  # Linux
                ''' % ("\n                    width, height = px(width), px(height)"
                       if self.dpi else ""))
        if "icon" in h:
            add('''
                def _set_icon(self, window, path):
                    """Use a .png/.gif/.ico file as the window icon (skipped if missing)."""
                    path = self._find_file(path)
                    try:
                        if path.lower().endswith(".ico"):
                            window.iconbitmap(path)
                        else:
                            image = tk.PhotoImage(master=self, file=path)
                            window.iconphoto(False, image)
                            window._icon = image  # keep the picture alive
                    except tk.TclError:
                        pass  # no icon is better than no app
                ''')
        if "image" in h:
            add('''
                def _load_image(self, path):
                    """Load a PNG or GIF picture (None if it can't be loaded)."""
                    try:
                        image = tk.PhotoImage(master=self, file=self._find_file(path))
                    except tk.TclError:
                        print("Could not load the picture:", path)
                        return None
                    self._images.append(image)
                    return image
                ''')
        if "icon" in h or "image" in h:
            add('''
                def _find_file(self, path):
                    """`path` as given, or next to this program if it isn't there."""
                    if os.path.exists(path):
                        return path
                    here = os.path.dirname(os.path.abspath(__file__))
                    return os.path.join(here, os.path.basename(path))
                ''')
        add('''
            def _format(self, value):
                """Show a value as text: 2.0 -> "2", 0.1 + 0.2 -> "0.3", None -> ""."""
                if value is None:
                    return ""
                if isinstance(value, float):
                    return f"{value:.10g}"
                return str(value)
            ''')
        if "_NUMBERS" in "\n".join(self.tables_text):
            add('''
                def _number(self, name, text):
                    """Turn what was typed into an int or float (ValueError if it isn't one)."""
                    number_type, title = self._NUMBERS[name]
                    text = text.strip()
                    if "," in text and "." not in text:
                        text = text.replace(",", ".")  # 2,5 means 2.5
                    if not text:
                        raise ValueError(f'Please enter a number for "{title}".')
                    try:
                        number = float(text)
                    except ValueError:
                        raise ValueError(f'"{title}" must be a number (not "{text}").') from None
                    if number_type is int:
                        if not number.is_integer():
                            raise ValueError(f'"{title}" must be a whole number (not "{text}").')
                        return int(number)
                    return number
                ''')
        if "to_number" in h:
            add('''
                def _to_number(self, value):
                    """value as a float, or None if it isn't a number."""
                    try:
                        return float(str(value).strip().replace(",", "."))
                    except ValueError:
                        return None
                ''')
        if "replace_text" in h:
            add('''
                def _replace_text(self, widget, text):
                    """Replace the text of a text box, even a read-only one."""
                    locks = [flag for flag in widget.state() if flag in ("disabled", "readonly")]
                    widget.state(["!disabled", "!readonly"])
                    widget.delete(0, "end")
                    widget.insert(0, text)
                    widget.state(locks)
                ''')
        if "fill_table" in h:
            stripe = "\n                    self._stripe(widget)" if self.any_striped() else ""
            if stripe:
                h.add("stripes")
            add('''
                def _fill_table(self, widget, rows):
                    """Replace all rows of a table. rows: lists, tuples or dicts by column
                    title; a single dict becomes (key, value) rows; None clears it."""
                    if isinstance(rows, dict):
                        rows = list(rows.items())
                    titles = [widget.heading(column, "text") for column in widget["columns"]]
                    widget.delete(*widget.get_children())
                    for row in rows or []:
                        if isinstance(row, dict):
                            row = [row.get(title, "") for title in titles]
                        elif not isinstance(row, (list, tuple)):
                            row = [row]
                        widget.insert("", "end", values=[self._format(cell) for cell in row])%s
                ''' % stripe)
        if "add_row" in h:
            stripe = "\n                    self._stripe(widget)" if self.any_striped() else ""
            if stripe:
                h.add("stripes")
            add('''
                def _add_row(self, name, values):
                    """Add one row at the end of a table and scroll to it."""
                    widget = self.widget(name)
                    row = widget.insert("", "end", values=[self._format(cell) for cell in values])
                    widget.see(row)%s
                ''' % stripe)
        if "stripes" in h:
            add('''
                def _stripe(self, widget):
                    """Give every other row of a striped table the "odd" colour."""
                    if not widget.tag_configure("odd", "background"):
                        return  # this table isn't striped
                    for index, row in enumerate(widget.get_children()):
                        widget.item(row, tags=("odd",) if index % 2 else ())
                ''')
        if "export_table" in h:
            add('''
                def _save_table(self, name, path):
                    """Save all rows of a table (with the column titles) as a CSV file."""
                    widget = self.widget(name)
                    try:
                        with open(path, "w", newline="", encoding="utf-8") as file:
                            writer = csv.writer(file)
                            writer.writerow([widget.heading(column, "text")
                                             for column in widget["columns"]])
                            for row in widget.get_children():
                                writer.writerow(widget.item(row, "values"))
                    except OSError as error:
                        self.show_error(str(error), "Could not save the table")
                ''')
        if "scale" in h:
            add('''
                def _scale_moved(self, name):
                    """A slider moved: snap it to its step, show the number, tell listeners."""
                    widget = self.widget(name)
                    value = self.get(name)
                    if float(widget.get()) != value:
                        widget.set(value)
                    if name in self._value_labels:
                        self._value_labels[name].configure(text=self._format(value))
                    if self._scale_values.get(name) != value:
                        self._scale_values[name] = value
                        self._fire(name, "change")
                ''')
        if "append" in h:
            spec = []
            if self.has("Text"):
                spec.append((("Text",), block('''
                    state = widget.cget("state")
                    widget.configure(state="normal")
                    if new_line and widget.get("1.0", "end-1c"):
                        text = "\\n" + text
                    widget.insert("end", text)
                    widget.see("end")
                    widget.configure(state=state)
                    ''')))
            if self.has("Entry"):
                spec.append((("Entry",), block('''
                    if new_line and widget.get():
                        text = " " + text  # a text box has only one line: use a space
                    self._replace_text(widget, widget.get() + text)
                    ''')))
                h.add("replace_text")
            if self.has("Label"):
                spec.append((("Label",), block('''
                    old = str(widget.cget("text"))
                    if new_line and old:
                        text = "\\n" + text
                    widget.configure(text=old + text)
                    ''')))
            out += [""] + block('''
                def _append_text(self, name, text, new_line=True):
                    """Add text to the end of a label, text box or text area."""
                    widget = self.widget(name)
                    kind = self._TYPES[name]
                    text = self._format(text)
                ''', IND)
            out += self.branches(spec, IND * 2)
        if "add_number" in h:
            add('''
                def _add_to_number(self, name, amount):
                    """Add `amount` to the number a widget shows (counting from 0)."""
                    try:
                        number = self._to_number(self.get(name)) or 0
                    except ValueError:  # e.g. an empty number box
                        number = 0
                    self.set(name, number + amount)
                ''')
            h.add("to_number")
        if "set_matching" in h:
            add('''
                def _set_matching(self, result):
                    """Show each item of a dict result in the widget with the same name."""
                    for name, value in dict(result).items():
                        if name in self._TYPES%s:
                            self.set(name, value)
                        else:
                            print(f"(No widget called {name!r} for this result)")
                ''' % (" or name in self._groups" if self.groups else ""))
        if "enable" in h:
            spec = []
            body = ['widget.configure(state="normal" if enabled else "disabled")']
            if self.has("Text", "Console", "Listbox", "Canvas"):
                spec.append((("Text", "Console", "Listbox", "Canvas"), body))
            if self.has("FilePicker"):
                spec.append((("FilePicker",), ["widget.set_enabled(enabled)"]))
            out += [""] + block('''
                def _set_enabled(self, name, enabled):
                    """Enable (True) or disable (False) a widget."""
                    widget = self.widget(name)
                ''', IND)
            if spec:
                out.append(IND * 2 + "kind = self._TYPES[name]")
                out += self.branches(spec, IND * 2)
                out.append(IND * 2 + "else:")
                out.append(IND * 3 + 'widget.state(["!disabled"] if enabled else ["disabled"])')
            else:
                out.append(IND * 2 + 'widget.state(["!disabled"] if enabled else ["disabled"])')
        if "colors" in h:
            add('''
                def _set_colors(self, name, fg=None, bg=None):
                    """Change the text (fg) and background (bg) colours of a widget."""
                    widget = self.widget(name)
                    if isinstance(widget, (tk.Text, tk.Listbox, tk.Canvas)):
                        if fg and not isinstance(widget, tk.Canvas):
                            widget.configure(fg=fg)
                        if bg:
                            widget.configure(bg=bg)
                        return
                    # themed widgets get their own style, based on their current one
                    style_name = str(widget.cget("style")) or widget.winfo_class()
                    if not style_name.startswith(name + "."):
                        style_name = f"{name}.{style_name}"
                    background = "fieldbackground" if isinstance(widget, ttk.Entry) else "background"
                    if fg:
                        self.style.configure(style_name, foreground=fg)
                    if bg:
                        self.style.configure(style_name, **{background: bg})
                    widget.configure(style=style_name)
                ''')
        if "timers" in h:
            add('''
                def _start_timer(self, name):
                    """Start (or restart) the timer of a window."""
                    self._stop_timer(name)
                    self._timer_jobs[name] = self.after(self._TIMERS[name], self._tick, name)

                def _stop_timer(self, name):
                    job = self._timer_jobs.pop(name, None)
                    if job is not None:
                        self.after_cancel(job)

                def _tick(self, name):
                    """One timer tick (skipped while the previous tick's work still runs)."""
                    self._timer_jobs[name] = self.after(self._TIMERS[name], self._tick, name)
                    if self._running.get(name):
                        return
                    try:
                        self._fire(name, "timer")
                    except Exception as error:  # stop, instead of an error every tick
                        self._stop_timer(name)
                        self._report_error(error)
                ''')
        if "run_command" in h:
            add('''
                def _run_command(self, command, output=None, cwd=None, busy=None, sender=None,
                                 show_errors=True, on_done=None):
                    """Run a command line in the background, showing its output line by
                    line in `output` (a console). on_done(exit_code) runs if it succeeds."""
                    def work():
                        process = subprocess.Popen(
                            command, shell=True, cwd=cwd or None, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        for line in process.stdout:
                            self.log(line.rstrip("\\r\\n"), output)
                        return process.wait()

                    def finished(exit_code):
                        if exit_code != 0:
                            if show_errors:
                                self.show_error(f"The command failed (exit code {exit_code}):\\n{command}")
                        elif on_done is not None:
                            on_done(exit_code)

                    self.run_in_background(work, on_done=finished, busy=busy, sender=sender)
                ''')
        if "capture" in h:
            add('''
                def _capture_print(self, console):
                    """Show everything print() writes (and errors) in a console."""
                    sys.stdout = ConsoleWriter(self, console, self._streams[0])
                    sys.stderr = ConsoleWriter(self, console, self._streams[1])
                ''')
        return out

    def any_striped(self):
        return any(n["type"] == "Table" and n["props"].get("striped", True) for n in self.widgets)

    # ---- theme ------------------------------------------------------------
    def theme_method(self):
        ind = IND * 2
        info = apptheme.THEMES.get(self.theme, apptheme.THEMES["native"])
        out = [IND + "def _setup_theme(self):",
               ind + '"""Apply the "%s" theme and fonts to every widget."""' % _doc(info["name"]),
               ind + "style = self.style = ttk.Style(self)"]
        base = apptheme.base_theme(self.theme)
        if base == "clam":
            out.append(ind + 'style.theme_use("clam")')
        elif info["base"] != "native":
            out += [ind + "if %s in style.theme_names():" % py_str(base),
                    ind + IND + "style.theme_use(%s)" % py_str(base),
                    ind + "else:  # not available on this computer: use a safe one",
                    ind + IND + 'style.theme_use("clam")']
        out += [ind + 'default = tkfont.nametofont("TkDefaultFont")',
                ind + 'self._family = FONT_FAMILY or default.actual("family")',
                ind + 'for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):',
                ind + IND + "tkfont.nametofont(name).configure(family=self._family, size=%d)"
                % self.ctx["size"]]
        ops = [op for op in apptheme.style_ops(self.theme, self.ctx["size"])
               if (self.option_needed(op[1]) if op[0] == "option"
                   else self.style_needed(op[1]))]
        custom = []
        for n in self.widgets:
            custom += tkmap.custom_style_ops(n, self.ctx)
        out += self.op_lines(ops, ind)
        if custom:
            out.append(ind + "# Widgets with their own colours / fonts")
            out += self.op_lines(custom, ind)
        return out

    # ttk style class -> widget types that use it (styles of absent widgets are left out)
    STYLE_USERS = {
        "TCheckbutton": ("Checkbutton",), "TRadiobutton": ("Radiobutton",),
        "TEntry": ("Entry", "FilePicker"), "TCombobox": ("Combobox",), "TSpinbox": ("Spinbox",),
        "TLabelframe": ("LabelFrame",), "TLabelframe.Label": ("LabelFrame",),
        "TNotebook": ("Notebook",), "TNotebook.Tab": ("Notebook",),
        "Horizontal.TScale": ("Scale",), "Vertical.TScale": ("Scale",),
        "Horizontal.TProgressbar": ("Progressbar",), "Vertical.TProgressbar": ("Progressbar",),
        "TSeparator": ("Separator",), "Treeview": ("Table",), "Treeview.Heading": ("Table",),
        "Vertical.TScrollbar": ("Text", "Listbox", "Table", "Console"),
        "Horizontal.TScrollbar": (), "TMenubutton": (), "TPanedwindow": (), "Sash": (),
    }

    def style_needed(self, name):
        """Is a theme style used by this app? (keeps the theme code short)"""
        if name in self.STYLE_USERS:
            return self.has(*self.STYLE_USERS[name])
        variants = {v + "." + cls if v != "Toolbutton" else v
                    for cls, options in apptheme.VARIANTS.items() for v, _l in options if v}
        if name in variants:
            used = set()
            for n in self.widgets:
                variant = n["props"].get("variant")
                if variant:
                    used.add(variant if variant == "Toolbutton"
                             else variant + "." + (tkmap.style_class(n) or ""))
            return name in used
        return True

    @staticmethod
    def kwargs_call(ind, prefix, args):
        """Like wrap_call, but an arg may be (key, [items]) meaning key=[items],
        which is split over several lines when it is too long."""
        flat = [a if isinstance(a, str) else "%s=[%s]" % (a[0], ", ".join(a[1])) for a in args]
        one = wrap_call(ind, prefix, flat)
        if all(len(line) <= MAX_LINE for line in one):
            return one
        out, cur = [ind + prefix], ind + IND
        for k, (a, text) in enumerate(zip(args, flat)):
            end = "," if k < len(args) - 1 else ")"
            if len(ind + IND + text + end) <= MAX_LINE:
                if cur.strip() and len(cur) + 1 + len(text + end) > MAX_LINE:
                    out.append(cur.rstrip())
                    cur = ind + IND
                cur += (" " if cur.strip() else "") + text + end
                continue
            if cur.strip():
                out.append(cur.rstrip())
                cur = ind + IND
            out += wrap_call(ind + IND, a[0] + "=[", a[1], "]" + end)
        if cur.strip():
            out.append(cur.rstrip())
        return out

    def option_needed(self, pattern):
        if "Combobox" in pattern:
            return self.has("Combobox")
        if "Menu" in pattern:
            return any(w["props"].get("menu") for w in self.project.windows)
        if "Text" in pattern:
            return self.has("Text", "Console")
        return True

    def op_lines(self, ops, ind):
        """Style operations as literal style.configure/map and option_add lines.
        Neighbouring operations with the same options become one loop."""
        groups = []
        for kind, name, opts in ops:
            if groups and kind != "option" and groups[-1][0] == kind                     and groups[-1][2] == opts:
                groups[-1][1].append(name)
            else:
                groups.append((kind, [name], opts))
        out = []
        for kind, names, opts in groups:
            if kind == "option":
                out.append(ind + "self.option_add(%s, %s)" % (py_str(names[0]), self.lit(opts)))
                continue
            args = []
            for k, v in opts.items():
                if k in PIXEL_STYLE_OPTIONS and isinstance(v, (int, float)):
                    args.append("%s=%s" % (k, self.px(v)))
                elif kind == "map":
                    args.append((k, ["(%s, %s)" % (self.lit(state), self.lit(val))
                                     for state, val in v]))
                else:
                    args.append("%s=%s" % (k, self.lit(v)))
            if len(names) == 1:
                out += self.kwargs_call(ind, "style.%s(" % kind, [py_str(names[0])] + args)
            else:
                out += wrap_call(ind, "for name in (", [py_str(n) for n in names], "):",
                                 cont=IND * 2)
                out += self.kwargs_call(ind + IND, "style.%s(" % kind, ["name"] + args)
        return out

    # ---- module level ------------------------------------------------------
    def helper_classes(self):
        out = []
        if self.modules:
            out += [""] + block('''
                class MissingModule:
                    """Stands in for one of your modules that could not be imported, so the
                    window still opens. Using it raises an error that explains why."""

                    def __init__(self, name, error):
                        self.name, self.error = name, error

                    def __getattr__(self, attribute):
                        raise ImportError(f'Could not import your module "{self.name}": {self.error}')
                ''') + [""]
        if "FilePicker" in self.need:
            out += [""] + block('''
                class FilePicker(ttk.Frame):
                    """A path box with a Browse... button (made by TkBuilder).

                    get() / set(path) read and change the path; command() is called
                    after the user picked something with the button.
                    """

                    def __init__(self, master, mode="open", filetypes=(("All files", "*.*"),),
                                 button_text="Browse…", width=30, command=None):
                        super().__init__(master)
                        self.mode, self.filetypes, self.command = mode, list(filetypes), command
                        self.entry = ttk.Entry(self, width=width)
                        self.entry.grid(row=0, column=0, sticky="ew")
                        self.button = ttk.Button(self, text=button_text, command=self.browse)
                        self.button.grid(row=0, column=1, padx=(%s, 0))
                        self.columnconfigure(0, weight=1)

                    def get(self):
                        return self.entry.get()

                    def set(self, path):
                        self.entry.delete(0, "end")
                        self.entry.insert(0, path)
                        self.entry.xview_moveto(1)  # show the end of long paths

                    def set_enabled(self, enabled):
                        for part in (self.entry, self.button):
                            part.state(["!disabled"] if enabled else ["disabled"])

                    def browse(self):
                        """Open the file dialog and put the chosen path in the box."""
                        start = self.get() if self.mode == "folder" else os.path.dirname(self.get())
                        if self.mode == "folder":
                            path = filedialog.askdirectory(parent=self, initialdir=start or None)
                        elif self.mode == "save":
                            path = filedialog.asksaveasfilename(
                                parent=self, initialdir=start or None, filetypes=self.filetypes)
                        else:
                            path = filedialog.askopenfilename(
                                parent=self, initialdir=start or None, filetypes=self.filetypes)
                        if path:
                            self.set(path)
                            if self.command:
                                self.command()
                ''' % self.px(6)) + [""]
        if "Placeholder" in self.need:
            out += [""] + block('''
                class Placeholder:
                    """Grey hint text inside an empty text box (made by TkBuilder)."""

                    def __init__(self, entry, variable, text):
                        self.entry = entry
                        background = ttk.Style(entry).lookup("TEntry", "fieldbackground")
                        self.label = tk.Label(entry, text=text, fg=%s, bg=background or "white",
                                              font=entry.cget("font"), bd=0, padx=0, pady=0,
                                              cursor="xterm")
                        self.label.bind("<Button-1>", lambda event: entry.focus_set())
                        entry.bind("<FocusIn>", self.update, add="+")
                        entry.bind("<FocusOut>", self.update, add="+")
                        variable.trace_add("write", self.update)
                        self.update()

                    def update(self, *args):
                        try:
                            focused = self.entry.focus_get() is self.entry
                        except (KeyError, tk.TclError):
                            focused = False
                        if self.entry.get() or focused:
                            self.label.place_forget()
                        else:
                            self.label.place(x=%s, rely=0.5, anchor="w")
                ''' % (py_str(self.theme_color("muted", "#8a8f98")), self.px(4))) + [""]
        if "Tooltip" in self.need:
            out += [""] + block('''
                class Tooltip:
                    """A small hint shown when the mouse rests on a widget (made by TkBuilder)."""

                    def __init__(self, widget, text, delay=600):
                        self.widget, self.text, self.delay = widget, text, delay
                        self.tip = self.job = None
                        widget.bind("<Enter>", self.schedule, add="+")
                        widget.bind("<Leave>", self.hide, add="+")
                        widget.bind("<ButtonPress>", self.hide, add="+")

                    def schedule(self, event=None):
                        self.hide()
                        self.job = self.widget.after(self.delay, self.show)

                    def show(self):
                        x = self.widget.winfo_rootx() + %s
                        y = self.widget.winfo_rooty() + self.widget.winfo_height() + %s
                        self.tip = tk.Toplevel(self.widget)
                        self.tip.wm_overrideredirect(True)
                        self.tip.wm_geometry(f"+{x}+{y}")
                        tk.Label(self.tip, text=self.text, bg="#1f2328", fg="#ffffff", padx=%s,
                                 pady=%s, justify="left", wraplength=%s).pack()

                    def hide(self, event=None):
                        if self.job:
                            self.widget.after_cancel(self.job)
                            self.job = None
                        if self.tip:
                            self.tip.destroy()
                            self.tip = None
                ''' % tuple(self.px(v) for v in (12, 4, 8, 4, 320))) + [""]
        if "capture" in self.need:
            out += [""] + block('''
                class ConsoleWriter:
                    """Sends print() output to a console of the app (made by TkBuilder)."""

                    def __init__(self, app, console, stream):
                        self.app, self.console, self.stream = app, console, stream
                        self.pending = ""

                    def write(self, text):
                        if self.stream is not None:
                            self.stream.write(text)
                        *lines, self.pending = (self.pending + text).split("\\n")
                        for line in lines:
                            self.app.log(line, self.console)
                        return len(text)

                    def flush(self):
                        if self.stream is not None:
                            self.stream.flush()
                ''') + [""]
        return out

    def backend_lines(self):
        """sys.path setup + guarded imports of the user's modules."""
        if not self.modules:
            return []
        out = []
        path = self.backend_path_expr()
        if path:
            out += ["", "# Where your own Python modules are",
                    "HERE = os.path.dirname(os.path.abspath(__file__))",
                    "sys.path.insert(0, %s)" % path]
        out += ["", "# Your own Python code (the app still starts if a module is missing;"
                    " using it", "# then shows a clear error)"]
        for module in sorted(self.modules):
            alias = self.modules[module]
            imp = "import %s" % module if alias == module else "import %s as %s" % (module, alias)
            out += ["try:", IND + imp,
                    "except Exception as error:  # noqa: BLE001 - shown when it is used",
                    IND + "%s = MissingModule(%s, error)" % (alias, py_str(module))]
        return out

    def backend_path_expr(self):
        bd = (self.settings.get("backend_dir") or "").strip()
        if not bd:
            return None
        if not os.path.isabs(bd) and self.project_dir:
            bd = os.path.normpath(os.path.join(os.path.abspath(self.project_dir), bd))
        if os.path.isabs(bd):
            if self.folder:
                try:
                    rel = os.path.relpath(bd, os.path.abspath(self.folder))
                except ValueError:       # another drive on Windows
                    rel = None
                if rel == ".":
                    return None
                if rel is not None and not rel.replace("\\", "/").startswith("../../.."):
                    return "os.path.join(HERE, %s)" % py_str(rel.replace("\\", "/"))
            return py_str(bd.replace("\\", "/"))
        return "os.path.join(HERE, %s)" % py_str(bd.replace("\\", "/"))

    # ---- docstring of the UI class ----------------------------------------
    def class_doc(self):
        title = _doc(self.main["props"].get("title") or self.project.app_name)
        where = "in %s.py (class %s)" % (self.base, self.cls) if not self.single else \
            "in the methods at the end of this class"
        lines = ['"""The window(s) of "%s", made with TkBuilder.' % title, "",
                 "How to use - %s:" % where,
                 '    value = self.get("name")       # read a widget (typed: str, int, float, bool...)',
                 '    self.set("name", value)        # change a widget (safe from any thread)',
                 "    data = self.values()           # {name: value} of every input",
                 '    self.on("name", "click", self.my_method)   # react to the user',
                 '    self.log("text")               # add a line to the console',
                 "    self.run_in_background(slow_function, arg, on_done=self.show_result)",
                 '    self.show_info("Done!")  /  self.show_error(...)  /  self.ask_yes_no(...)',
                 '    self.open_window("name")  /  self.close_window("name")']
        named = [n for n in self.widgets if n["type"] in model.VALUE_WIDGETS]
        if named:
            lines += ["", "Your widgets (name: what get() gives):"]
            seen = set()
            for n in named[:40]:
                name = self.value_name(n["id"])
                if name in seen:
                    continue
                seen.add(name)
                kind = "option group" if n["type"] == "Radiobutton" else WIDGETS[n["type"]]["name"]
                lines.append("    %-22s %-15s %s" % (name, kind, self.value_type_text(n)))
            if len(named) > 40:
                lines.append("    ... and %d more" % (len(named) - 40))
        lines.append('"""')
        return [IND + _doc_line(line) if line else "" for line in lines]

    # =====================================================================
    # assembling the files
    # =====================================================================
    def ui_class(self):
        """Lines of the UI class (shared by both code styles)."""
        builds, build_names = [], []
        for w in self.project.windows:
            name, lines = self.build_method(w)
            build_names.append(name)
            builds += [""] + lines
        handlers, connect = self.handlers_and_connections()
        self.tables_text = self.class_tables()
        api = [""] + self.api_get() + [""] + self.api_set() + [""] + self.api_rest()
        theme = self.theme_method()
        init = self.init_method(build_names)
        helpers = self.helpers()
        hooks = []
        for name, doc, node in self.hook_list():
            if self.single:
                hooks += ["", IND + "def %s(self):" % name, IND * 2 + '"""%s"""' % doc,
                          IND * 2 + "# TODO: your code here, for example:"]
                hooks += self.hook_example(node, IND * 2)
                hooks.append(IND * 2 + "pass")
            else:
                hooks += ["", IND + "def %s(self):" % name,
                          IND * 2 + '"""%s Write this method in %s.py."""' % (doc, self.base)]
        connect_m = [IND + "def _connect_events(self):",
                     IND * 2 + '"""Connect the designed actions and your own methods."""']
        connect_m += connect or [IND * 2 + "pass  # (nothing to connect)"]

        base = "tk.Tk"
        out = ["class %s(%s):" % (self.ui_cls, base)] + self.class_doc() + [""]
        out += self.tables_text + [""] + init
        out += ["", IND + "# " + "-" * 70, IND + "# Windows and widgets", IND + "# " + "-" * 70]
        out += builds
        out += ["", IND + "# " + "-" * 70, IND + "# What happens when the user does something",
                IND + "# " + "-" * 70, ""] + connect_m + handlers
        if hooks:
            title = "Your own code" if self.single else \
                "Your own code: write these methods in %s.py (they are empty here)" % self.base
            out += ["", IND + "# " + "-" * 70, IND + "# " + title, IND + "# " + "-" * 70] + hooks
        out += ["", IND + "# " + "-" * 70,
                IND + "# Use these in your code (see the class docstring)",
                IND + "# " + "-" * 70] + api
        out += ["", IND + "# " + "-" * 70, IND + "# Theme", IND + "# " + "-" * 70, ""] + theme
        out += ["", IND + "# " + "-" * 70,
                IND + "# Helpers used above (you don't need to change these)",
                IND + "# " + "-" * 70] + helpers
        return out

    def module_text(self, header, cls_lines, footer):
        pre = []
        if self.settings.get("dpi_aware", True):
            pre += ["", ""] + block('''
                def make_dpi_aware():
                    """Sharp text on high-resolution Windows screens (call before Tk())."""
                    if sys.platform == "win32":
                        try:
                            import ctypes
                            ctypes.windll.shcore.SetProcessDpiAwareness(1)
                        except (AttributeError, OSError):
                            pass  # older Windows: it still works, just less sharp
                ''')
        if self.dpi:
            pre += ["", ""] + block('''
                # Screen pixels per design pixel (measured when the app starts)
                SCALE = 1.0


                def set_scale(root):
                    """Measure the screen: 2.5 on a 4K screen at 250 %, 1.0 on a normal one."""
                    global SCALE
                    SCALE = max(1.0, root.winfo_fpixels("1i") / 96)


                def px(pixels):
                    """A size from the design in pixels of this screen (same physical size)."""
                    return round(pixels * SCALE)
                ''')
        family = (self.settings.get("font_family") or "").strip()
        consts = ["", "# The font of the app (\"\" = the normal font of this computer)",
                  "FONT_FAMILY = %s" % py_str(family)]
        classes = self.helper_classes()
        backend = self.backend_lines()
        body = consts + pre + ["", ""] + classes + (backend + ["", ""] if backend else [])
        text = "\n".join(body + cls_lines + footer)
        imports = self.imports_for(text)
        out = header + imports + text.split("\n")
        result = "\n".join(out).rstrip() + "\n"
        result = re.sub(r"\n{4,}", "\n\n\n", result)
        result = re.sub(r"(\n    [^\n]*\n)\n\n+(    \S)", r"\1\n\2", result)
        return result

    def imports_for(self, text):
        """Import lines for the names the code really uses (no unused imports)."""
        try:
            tree = ast.parse(text)
        except SyntaxError:
            used = set(re.findall(r"\b([A-Za-z_]\w*)\.", text))
        else:
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        lines = ["import %s" % m for m in STD_IMPORTS if m in used]
        lines.append("import tkinter as tk")
        if "tkfont" in used:
            lines.append("import tkinter.font as tkfont")
        names = [m for m in TK_FROM if m in used]
        if names:
            lines.append("from tkinter import " + ", ".join(names))
        return lines

    def ui_file(self):
        title = _doc(self.main["props"].get("title") or self.project.app_name)
        header = ['"""', "%s - the user interface." % title, "",
                  "Generated by TkBuilder - do not edit, your changes would be lost.",
                  "Put your code in %s.py (TkBuilder never overwrites that file)." % self.base,
                  '"""', ""]
        cls = self.ui_class()
        footer = ["", "", 'if __name__ == "__main__":',
                  IND + "# Normally you run %s.py (with your code); this shows the bare window."
                  % self.base,
                  IND + "%s().mainloop()" % self.ui_cls]
        return self.module_text(header, cls, footer)

    def single_file(self):
        title = _doc(self.main["props"].get("title") or self.project.app_name)
        header = ['"""', "%s - made with TkBuilder." % title, "",
                  "Generated by TkBuilder (single file). Run it with:  python %s.py" % self.base,
                  "Exporting again from TkBuilder replaces this file.",
                  '"""', ""]
        cls = self.ui_class()
        footer = ["", "", 'if __name__ == "__main__":', IND + "%s().mainloop()" % self.ui_cls]
        return self.module_text(header, cls, footer)

    def user_file(self):
        title = _doc(self.main["props"].get("title") or self.project.app_name)
        out = ['"""', "%s - your code." % title, "",
               "TkBuilder created this file once and never overwrites it: write your own",
               "code here. The window itself is in %s_ui.py (regenerated by TkBuilder)." % self.base,
               "Run your app with:  python %s.py" % self.base,
               '"""', "",
               "from %s_ui import %s" % (self.base, self.ui_cls), "", "",
               "class %s(%s):" % (self.cls, self.ui_cls),
               IND + '"""%s. See %s for self.get(), self.set() and more."""'
               % (title, self.ui_cls)]
        stubs = self.user_stubs()
        if stubs:
            for s in stubs:
                out += [""] + s.rstrip("\n").split("\n")
        else:
            out += ["", IND + "def __init__(self):",
                    IND * 2 + "super().__init__()",
                    IND * 2 + "# Your start-up code here, for example:",
                    IND * 2 + '# self.on("button1", "click", self.my_method)']
        out += ["", "", 'if __name__ == "__main__":', IND + "%s().mainloop()" % self.cls]
        return "\n".join(out) + "\n"

    def user_stubs(self):
        """{hook name: stub source} for every hook (indented for the class body)."""
        return [self.stub_for(name, doc, node) for name, doc, node in self.hook_list()]

    def stub_for(self, name, doc, node):
        lines = [IND + "def %s(self):" % name, IND * 2 + '"""%s"""' % doc]
        lines += self.hook_example(node, IND * 2)
        lines.append(IND * 2 + "pass")
        return "\n".join(lines) + "\n"


def _doc_line(line):
    return line.replace("\\", "\\\\")


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def generate(project, folder=None, project_dir=None):
    """Return {filename: source} for the project's code style (settings
    "code_style": "split" = <base>_ui.py + <base>.py, "single" = <base>.py).
    folder / project_dir (optional) make backend_dir relative to the output."""
    base = project.module_basename()
    if project.settings.get("code_style", "split") == "single":
        return {base + ".py": _Gen(project, True, folder, project_dir).single_file()}
    gen = _Gen(project, False, folder, project_dir)
    return {base + "_ui.py": gen.ui_file(), base + ".py": gen.user_file()}


def generate_single(project, folder=None, project_dir=None):
    """The whole app as one self-contained Python file."""
    return _Gen(project, True, folder, project_dir).single_file()


def hook_stubs(project):
    """{hook method name: stub source} - handy for 'copy method' buttons."""
    gen = _Gen(project, False)
    return {name: gen.stub_for(name, doc, node) for name, doc, node in gen.hook_list()}


def defined_methods(source):
    """Names of the methods defined in the classes of `source` (None if it
    can't be parsed)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(item.name)
    return names


GENERATED_MARK = "Generated by TkBuilder"


def export(project, folder, overwrite_user_code=False, project_dir=None):
    """Write the generated files into `folder`.

    Split style: <base>_ui.py is always (re)written; <base>.py (the user's
    code) is only created if missing (or when overwrite_user_code=True).
    Single style: <base>.py is written unless it is someone's own code (no
    TkBuilder header) - then it is kept, unless overwrite_user_code=True.

    Returns {"written": [paths], "kept": [paths], "missing_hooks": [stub source]}
    where missing_hooks are methods the kept user file doesn't define yet.
    """
    os.makedirs(folder, exist_ok=True)
    files = generate(project, folder, project_dir)
    base = project.module_basename()
    user_name = base + ".py"
    single = project.settings.get("code_style", "split") == "single"
    report = {"written": [], "kept": [], "missing_hooks": []}
    for name, source in files.items():
        path = os.path.join(folder, name)
        if name == user_name and os.path.exists(path) and not overwrite_user_code:
            try:
                with open(path, encoding="utf-8-sig") as f:
                    existing = f.read()
            except OSError:
                existing = ""
            if not single or GENERATED_MARK not in existing[:1000]:
                report["kept"].append(path)
                if not single:
                    defined = defined_methods(existing) or set()
                    stubs = hook_stubs(project)
                    report["missing_hooks"] = [src for hook, src in stubs.items()
                                               if hook not in defined]
                continue
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(source)
        report["written"].append(path)
    return report
