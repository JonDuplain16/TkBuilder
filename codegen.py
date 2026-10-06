"""
Python code generator for TkBuilder.

generate(project) turns a project into ONE self-contained, runnable and
beginner-friendly Python file (standard library only).

Shape of the generated program:

    class App(tk.Tk):
        __init__           window settings, variables, menu bar, widgets of
                           the main window, event wiring
        open_<window>()    one per extra window (builds it as a Toplevel,
                           or brings it to the front if already open)
        <id>_<event>()     one per event that has actions
        _helpers()         small helpers, only the ones that are used

The designer and this generator share tkmap.widget_options / initial_state /
font_value and model.sticky_for / grid_weights, so the result looks exactly
like the design.
"""

import keyword
import re
import textwrap
import tkinter as tk

import model
import tkmap
from model import WIDGETS, ACTIONS

IND = "    "
MAX_LINE = 99

# Readable endings for event handler names: button1_clicked, list1_selected...
EVENT_SUFFIX = {
    ("Button", "command"): "clicked",
    ("Checkbutton", "command"): "toggled",
    ("Radiobutton", "command"): "chosen",
    ("Spinbox", "command"): "changed",
    ("Scale", "command"): "moved",
    ("Entry", "on_enter"): "enter_pressed",
    ("Combobox", "on_select"): "selected",
    ("Listbox", "on_select"): "selected",
    ("Notebook", "on_tab_change"): "tab_changed",
    ("Canvas", "on_click"): "clicked",
    ("Window", "on_open"): "opened",
    ("Window", "on_close"): "closing",
}

# Events wired with widget.bind(...) (the rest use command=...)
BIND_SEQUENCE = {
    ("Entry", "on_enter"): "<Return>",
    ("Combobox", "on_select"): "<<ComboboxSelected>>",
    ("Listbox", "on_select"): "<<ListboxSelect>>",
    ("Notebook", "on_tab_change"): "<<NotebookTabChanged>>",
    ("Canvas", "on_click"): "<Button-1>",
}

TEXT_LIKE = ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame")
NO_VALUE = ("Frame", "Notebook", "Tab", "Separator", "Canvas", "Window")

# Text shown in the generated code instead of the radio "tri-state" look
# (Tk draws every option as half-chosen when the group's value is "").
NOTHING_CHOSEN = "(nothing chosen)"


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
    return s


def _num_lit(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "0"
    return repr(int(f)) if f.is_integer() else repr(f)


class _Skip(Exception):
    """Raised by an action emitter when the action can't be generated."""


# ---------------------------------------------------------------------------
# the generator
# ---------------------------------------------------------------------------

class _Generator:
    def __init__(self, project):
        self.project = project
        self.main = project.main_window
        self.main_id = self.main["id"]
        self.nodes = {}
        self.window_of = {}
        for w in project.windows:
            for n in project.walk(w):
                self.nodes[n["id"]] = n
                self.window_of[n["id"]] = w["id"]
        self.parent = {}
        for n in project.walk():
            for ch in n.get("children", []):
                self.parent[ch["id"]] = n

        # names already used in the class: widget ids + everything tk.Tk has
        self.taken = set(self.nodes) | set(dir(tk.Tk)) | {"App"}
        self.helpers = set()
        self.get_types, self.set_types = set(), set()
        self.append_types, self.addnum_types = set(), set()
        self.default_family_used = False
        self.family_attr = self.unique("default_family")
        self._plan_names()

    # ---- naming --------------------------------------------------------
    def unique(self, base):
        base = _ident(base, "item")
        name, i = base, 2
        while name in self.taken or keyword.iskeyword(name):
            name = "%s_%d" % (base, i)
            i += 1
        self.taken.add(name)
        return name

    def _plan_names(self):
        self.open_method = {}
        for w in self.project.windows[1:]:
            self.open_method[w["id"]] = self.unique("open_" + w["id"])
        self.handler = {}
        for n in self.project.walk():
            for ev, actions in n.get("events", {}).items():
                if actions:
                    suffix = EVENT_SUFFIX.get((n["type"], ev), ev)
                    self.handler[(n["id"], ev)] = self.unique("%s_%s" % (n["id"], suffix))
        self.menu_handler = {}
        for w in self.project.windows:
            prefix = "menu" if w is self.main else w["id"] + "_menu"
            for mi, menu in enumerate(w["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    if item.get("separator") or not item.get("actions"):
                        continue
                    self.menu_handler[(w["id"], mi, ii)] = self.unique("%s_%s_%s" % (
                        prefix, _ident(menu.get("label"), "menu"), _ident(item.get("label"))))
        self.check_var = {}
        self.radio_group = {}      # group name -> dict(var, value, tristate)
        self.radio_var_of = {}     # radio id -> var name
        for n in self.project.walk():
            if n["type"] == "Checkbutton":
                self.check_var[n["id"]] = self.unique(n["id"] + "_var")
            elif n["type"] == "Radiobutton":
                group = n["props"].get("group") or ""
                g = self.radio_group.get(group)
                if g is None:
                    g = self.radio_group[group] = {
                        "var": self.unique(_ident(group, "group") + "_var"), "value": None}
                self.radio_var_of[n["id"]] = g["var"]
                if n["props"].get("selected"):
                    g["value"] = self.radio_value(n)
        # groups that an "untick" action can leave with nothing chosen
        for n in self.project.walk():
            for lst in self.project.iter_action_lists(n):
                for a in lst:
                    prm = a.get("params", {})
                    target = self.project.find(prm.get("target"))
                    if a.get("type") == "set_checked" and prm.get("state") != "tick" \
                            and target is not None and target["type"] == "Radiobutton":
                        self.radio_group[target["props"].get("group") or ""]["can_clear"] = True

    @staticmethod
    def radio_value(node):
        return dict(tkmap.widget_options(node)).get("value", node["id"])

    # ---- literals ------------------------------------------------------
    def lit(self, v, key=None):
        if isinstance(v, tkmap.DefaultFamily):
            self.default_family_used = True
            return "self." + self.family_attr
        if isinstance(v, bool) or v is None:
            return repr(v)
        if isinstance(v, (int, float)):
            return repr(v)
        if isinstance(v, str):
            return py_str(v)
        if isinstance(v, (tuple, list)):
            items = [self.lit(x) for x in v]
            if key == "font":
                return "(" + ", ".join(items) + ("," if len(items) == 1 else "") + ")"
            return "[" + ", ".join(items) + "]"
        return repr(v)

    def text_expr(self, template, quote_inner="'"):
        """Turn an action text with {widget_id} placeholders into a Python
        expression (a plain string or an f-string). {{ and }} are literal."""
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
            if m and m.group(1) in self.nodes and self.nodes[m.group(1)]["type"] not in NO_VALUE:
                if buf:
                    parts.append(("lit", "".join(buf)))
                    buf = []
                parts.append(("get", m.group(1)))
                self.use_get(self.nodes[m.group(1)]["type"])
                i += m.end()
                continue
            buf.append(template[i])
            i += 1
        if buf:
            parts.append(("lit", "".join(buf)))
        if not any(kind == "get" for kind, _ in parts):
            return py_str("".join(t for _, t in parts))
        if len(parts) == 1:
            return 'self._get("%s")' % parts[0][1]
        out = []
        for kind, val in parts:
            if kind == "lit":
                out.append(_dq_body(val.replace("{", "{{").replace("}", "}}")))
            else:
                out.append("{self._get(%s%s%s)}" % (quote_inner, val, quote_inner))
        return 'f"' + "".join(out) + '"'

    # ---- helper bookkeeping ----------------------------------------------
    def use_get(self, wtype):
        self.helpers.add("get")
        self.get_types.add(wtype)
        if wtype in ("Scale", "Progressbar"):
            self.helpers.add("number_text")

    def use_set(self, wtype):
        self.helpers.add("set")
        self.set_types.add(wtype)
        if wtype in ("Scale", "Progressbar"):
            self.helpers.add("number")

    # ---- code layout helpers ---------------------------------------------
    @staticmethod
    def call(indent, prefix, args, suffix=")"):
        """prefix + ', '.join(args) + suffix, wrapped if the line is long."""
        one = indent + prefix + ", ".join(args) + suffix
        if len(one) <= MAX_LINE or not args:
            return [one]
        lines = [indent + prefix]
        cur = indent + IND
        for k, a in enumerate(args):
            piece = a + ("," if k < len(args) - 1 else suffix)
            if cur.strip() and len(cur) + 1 + len(piece) > MAX_LINE:
                lines.append(cur.rstrip())
                cur = indent + IND
            cur += (" " if cur.strip() else "") + piece
        lines.append(cur.rstrip())
        return lines

    # =====================================================================
    # windows and widgets
    # =====================================================================
    def window_settings(self, win, expr, out, ind):
        p = win["props"]
        out.append(ind + "self.title(%s)" % py_str(p.get("title", "")) if expr == "self"
                   else ind + "%s.title(%s)" % (expr, py_str(p.get("title", ""))))
        out.append(ind + '%s.geometry("%dx%d")' % (expr, int(p.get("width") or 420),
                                                    int(p.get("height") or 320)))
        if not p.get("resizable", True):
            out.append(ind + "%s.resizable(False, False)" % expr)
        opts = tkmap.widget_options(win)
        if opts:
            keys = [k for k, _ in opts]
            what = [w for w, ok in (("background color", "bg" in keys),
                                    ("space around the edges", "padx" in keys)) if ok]
            out.append(ind + "# " + " and ".join(what).capitalize())
            out += self.call(ind, "%s.config(" % expr,
                             ["%s=%s" % (k, self.lit(v, k)) for k, v in opts])

    def menu_bar(self, win, expr, out, ind):
        menus = win["props"].get("menu") or []
        if not menus:
            return
        out.append("")
        out.append(ind + "# --- Menu bar ---")
        out.append(ind + "menubar = tk.Menu(%s)" % expr)
        used = set()
        for mi, menu in enumerate(menus):
            name = _ident(menu.get("label"), "menu") + "_menu"
            base, k = name, 2
            while name in used or keyword.iskeyword(name) or name in ("menubar", "self"):
                name = "%s%d" % (base, k)
                k += 1
            used.add(name)
            out.append(ind + "%s = tk.Menu(menubar, tearoff=False)" % name)
            for ii, item in enumerate(menu.get("items", [])):
                if item.get("separator"):
                    out.append(ind + "%s.add_separator()" % name)
                    continue
                args = ["label=%s" % py_str(item.get("label", ""))]
                h = self.menu_handler.get((win["id"], mi, ii))
                if h:
                    args.append("command=self.%s" % h)
                out += self.call(ind, "%s.add_command(" % name, args)
            out.append(ind + "menubar.add_cascade(label=%s, menu=%s)"
                       % (py_str(menu.get("label", "")), name))
        out.append(ind + "%s.config(menu=menubar)" % expr)

    def weights(self, container, expr, out, ind):
        rows, cols = model.grid_weights(container)
        if not rows and not cols:
            return
        def nums(word, d):
            keys = [str(k) for k in sorted(d)]
            if not keys:
                return None
            if len(keys) == 1:
                return "%s %s" % (word, keys[0])
            return "%ss %s and %s" % (word, ", ".join(keys[:-1]), keys[-1])
        what = " and ".join(x for x in (nums("column", cols), nums("row", rows)) if x)
        out.append(ind + "# Let %s grow when the window gets bigger" % what)
        for r in sorted(rows):
            out.append(ind + "%s.rowconfigure(%d, weight=1)" % (expr, r))
        for c in sorted(cols):
            out.append(ind + "%s.columnconfigure(%d, weight=1)" % (expr, c))

    def children(self, container, expr, out, ind):
        kids = container.get("children", [])
        if container["type"] != "Notebook":
            kids = sorted(kids, key=lambda ch: (ch["layout"].get("row", 0),
                                                ch["layout"].get("col", 0)))
        for ch in kids:
            self.widget(ch, expr, out, ind)

    @staticmethod
    def grid_args(layout):
        args = ["row=%d" % int(layout.get("row", 0)), "column=%d" % int(layout.get("col", 0))]
        if int(layout.get("rowspan", 1)) > 1:
            args.append("rowspan=%d" % int(layout["rowspan"]))
        if int(layout.get("colspan", 1)) > 1:
            args.append("columnspan=%d" % int(layout["colspan"]))
        for k in ("padx", "pady"):
            if int(layout.get(k, 0) or 0):
                args.append("%s=%d" % (k, int(layout[k])))
        sticky = model.sticky_for(layout)
        if sticky:
            args.append('sticky="%s"' % sticky)
        return args

    def widget(self, node, parent_expr, out, ind):
        t, nid = node["type"], node["id"]
        me = "self." + nid
        module, cls = tkmap.WIDGET_CLASS[t]
        state = tkmap.initial_state(node)
        out.append("")
        comment = self.project.display_name(node)
        text = node["props"].get("text")
        if t not in ("Tab", "LabelFrame") and isinstance(text, str) and text.strip() \
                and t not in ("Entry", "Text", "Combobox"):
            short = _one_line(text)
            comment += ': "%s"' % (short[:40] + "..." if len(short) > 43 else short)
        out.append(ind + "# " + comment)

        args = [parent_expr] + ["%s=%s" % (k, self.lit(v, k))
                                for k, v in tkmap.widget_options(node)]
        if state.get("image"):
            img = self.unique(nid + "_image")
            self.helpers.add("load_image")
            out.append(ind + "self.%s = self._load_image(%s)" % (img, py_str(state["image"])))
            args.append("image=self.%s" % img)
        if t == "Checkbutton":
            args.append("variable=self.%s" % self.check_var[nid])
        if t == "Radiobutton":
            args.append("variable=self.%s" % self.radio_var_of[nid])
            group = self.radio_group[node["props"].get("group") or ""]
            if group["value"] is None or group.get("can_clear"):
                args.append("tristatevalue=%s" % py_str(NOTHING_CHOSEN))
        handler = self.handler.get((nid, "command"))
        if handler and t != "Scale":
            args.append("command=self.%s" % handler)

        if t == "Tab":
            out += self.call(ind, "%s = tk.Frame(" % me, args)
            out.append(ind + "%s.add(%s, text=%s)" % (parent_expr, me,
                                                       py_str(node["props"].get("text", ""))))
        elif state.get("scrollbar"):
            what = WIDGETS[t]["name"].lower()
            out.append(ind + "# (a frame holds the %s together with its scrollbar)" % what)
            out.append(ind + "frame = tk.Frame(%s)" % parent_expr)
            out += self.call(ind, "frame.grid(", self.grid_args(node["layout"]))
            args[0] = "frame"
            out += self.call(ind, "%s = %s.%s(" % (me, module, cls), args)
            out.append(ind + 'scrollbar = ttk.Scrollbar(frame, orient="vertical", '
                             'command=%s.yview)' % me)
            out.append(ind + "%s.config(yscrollcommand=scrollbar.set)" % me)
            out.append(ind + '%s.pack(side="left", fill="both", expand=True)' % me)
            out.append(ind + 'scrollbar.pack(side="right", fill="y")')
        else:
            out += self.call(ind, "%s = %s.%s(" % (me, module, cls), args)
            out += self.call(ind, "%s.grid(" % me, self.grid_args(node["layout"]))

        # starting content / state
        if "text" in state:
            v = py_str(state["text"])
            if t == "Entry":
                out.append(ind + "%s.insert(0, %s)" % (me, v))
            elif t == "Text":
                out.append(ind + '%s.insert("1.0", %s)' % (me, v))
            elif t == "Combobox":
                out.append(ind + "%s.set(%s)" % (me, v))
        if state.get("items"):
            out += self.call(ind, "for item in [", [py_str(x) for x in state["items"]], "]:")
            out.append(ind + IND + '%s.insert("end", item)' % me)
        if "value" in state:
            v = _num_lit(state["value"])
            if t == "Spinbox":
                out.append(ind + '%s.delete(0, "end")' % me)
                out.append(ind + '%s.insert(0, "%s")' % (me, v))
            elif t == "Scale":
                out.append(ind + "%s.set(%s)" % (me, v))
        if state.get("disable_after_fill"):
            out.append(ind + '%s.config(state="disabled")' % me)
        if handler and t == "Scale":
            out.append(ind + "# (connected after setting the start value, so it doesn't run at start)")
            out.append(ind + "%s.config(command=self.%s)" % (me, handler))
        for (wt, ev), seq in BIND_SEQUENCE.items():
            h = self.handler.get((nid, ev))
            if wt == t and h:
                if t == "Notebook":
                    out.append(ind + "# (connected once the window is built, so it doesn't run at start)")
                    out.append(ind + 'self.after_idle(lambda: %s.bind("%s", self.%s))'
                               % (me, seq, h))
                else:
                    out.append(ind + '%s.bind("%s", self.%s)' % (me, seq, h))

        if model.is_container(t):
            if t != "Notebook":
                self.weights(node, me, out, ind)
            self.children(node, me, out, ind)

    # ---- __init__ ----------------------------------------------------------
    def init_method(self):
        ind = IND * 2
        win = self.main
        body = []
        widgets = []
        self.menu_bar(win, "self", widgets, ind)
        if win.get("children"):
            widgets.append("")
            widgets.append(ind + "# --- Widgets in the main window ---")
            self.weights(win, "self", widgets, ind)
            self.children(win, "self", widgets, ind)

        body.append(IND + "def __init__(self):")
        body.append(ind + "super().__init__()")
        self.window_settings(win, "self", body, ind)
        if self.default_family_used:
            body.append(ind + "# The normal font family (used where only a size or style was chosen)")
            body.append(ind + 'self.%s = tkfont.nametofont("TkDefaultFont").actual("family")'
                        % self.family_attr)
        others = self.project.windows[1:]
        if others:
            body.append("")
            body.append(ind + "# The other windows are made when they are opened (see open_...)")
            for w in others:
                note = ""
                if not self._window_is_opened(w["id"]):
                    note = "  # nothing opens this window yet"
                body.append(ind + "self.%s = None%s" % (w["id"], note))
        if self.check_var or self.radio_group:
            body.append("")
            body.append(ind + "# Variables that remember ticked checkboxes and chosen options")
            for cid, var in self.check_var.items():
                checked = bool(self.nodes[cid]["props"].get("checked"))
                body.append(ind + "self.%s = tk.BooleanVar(value=%r)" % (var, checked))
            for g in self.radio_group.values():
                body.append(ind + "self.%s = tk.StringVar(value=%s)"
                            % (g["var"], py_str(g["value"] or "")))
        body += widgets
        self.window_events(win, "self", body, ind)
        return body

    def _window_is_opened(self, wid):
        for n in self.project.walk():
            for lst in self.project.iter_action_lists(n):
                for a in lst:
                    if a.get("type") == "open_window" and a.get("params", {}).get("window") == wid:
                        return True
        return False

    def window_events(self, win, expr, out, ind):
        close = self.handler.get((win["id"], "on_close"))
        opened = self.handler.get((win["id"], "on_open"))
        if close or opened:
            out.append("")
        if close:
            out.append(ind + "# Run some actions when the window is closed")
            out.append(ind + '%s.protocol("WM_DELETE_WINDOW", self.%s)' % (expr, close))
        if opened:
            out.append(ind + "# Run the 'when the window opens' actions")
            if win is self.main:
                out.append(ind + "self.after(0, self.%s)" % opened)
            else:
                out.append(ind + "self.%s()" % opened)

    # ---- open_<window> -----------------------------------------------------
    def open_method_lines(self, win):
        ind = IND * 2
        wid = win["id"]
        expr = "self." + wid
        out = [IND + "def %s(self):" % self.open_method[wid],
               ind + '"""Open the window "%s" (or bring it to the front if it is already open)."""'
               % _doc(win["props"].get("title", wid))]
        self.helpers.add("is_open")
        out.append(ind + "if self._is_open(%s):" % expr)
        out.append(ind + IND + "%s.deiconify()" % expr)
        out.append(ind + IND + "%s.lift()" % expr)
        out.append(ind + IND + "return")
        out.append(ind + "%s = tk.Toplevel(self)" % expr)
        self.window_settings(win, expr, out, ind)
        self.menu_bar(win, expr, out, ind)
        if win.get("children"):
            out.append("")
            out.append(ind + "# --- Widgets in this window ---")
            self.weights(win, expr, out, ind)
            self.children(win, expr, out, ind)
        self.window_events(win, expr, out, ind)
        return out

    # =====================================================================
    # event handlers and actions
    # =====================================================================
    def handler_lines(self, name, doc, actions, owner):
        ind = IND * 2
        out = [IND + "def %s(self, event=None):" % name, ind + '"""%s"""' % doc]
        body, _ctx = self.actions(actions, owner, ind)
        out += body
        if not any(l.strip() and not l.strip().startswith("#") for l in body):
            out.append(ind + "pass")
        return out

    def window_closing_tail(self, wid):
        if wid == self.main_id:
            return ["self.destroy()"]
        return ["self.%s.destroy()" % wid]

    def actions(self, actions, owner, ind, closing=None):
        """Lines for a list of actions -> (lines, ctx). owner = id of the
        window the event belongs to (its widgets exist while it runs);
        closing = id of the window whose 'when closed' handler this is."""
        ctx = {"owner": owner, "closed": set(), "closing": closing}
        out = []
        for k, act in enumerate(actions):
            spec = ACTIONS.get(act.get("type"))
            summary = _one_line(model.action_summary(act, self.project))
            if spec is None:
                out.append(ind + "# skipped: unknown action %r" % act.get("type"))
                continue
            params = {p["key"]: act.get("params", {}).get(p["key"], p["default"])
                      for p in spec["params"]}
            try:
                lines = getattr(self, "act_" + act["type"])(params, ctx)
            except _Skip as e:
                out.append(ind + "# skipped: %s (%s)" % (summary, e))
                continue
            out.append(ind + "# " + summary)
            out += [ind + l if l else "" for l in lines]
            if ctx.get("stop"):
                if k < len(actions) - 1:
                    out.append(ind + "# (the actions after this one are not run: %s)" % ctx["stop"])
                break
        return out, ctx

    # ---- action helpers ----------------------------------------------------
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

    def guard(self, ctx, node_ids, lines):
        """Wrap lines in 'if self._is_open(...)' for widgets that live in a
        window that might be closed when the action runs."""
        wins = []
        for nid in node_ids:
            w = self.window_of.get(nid)
            if w is None or w == self.main_id or w in wins:
                continue
            if w == ctx["owner"] and w not in ctx["closed"]:
                continue
            wins.append(w)
        if not wins:
            return lines
        self.helpers.add("is_open")
        cond = " and ".join("self._is_open(self.%s)" % w for w in wins)
        return ["if %s:" % cond] + [IND + l for l in lines]

    def parent_arg(self, ctx):
        o = ctx["owner"]
        if o and o != self.main_id and o not in ctx["closed"]:
            return ", parent=self.%s" % o
        return ""

    def wexpr(self, node):
        return "self." + node["id"]

    def set_lines(self, node, value):
        t, w = node["type"], self.wexpr(node)
        if t in TEXT_LIKE:
            return ["%s.config(text=%s)" % (w, value)]
        if t == "Combobox":
            return ["%s.set(%s)" % (w, value)]
        if t in ("Entry", "Text", "Spinbox", "Listbox", "Scale", "Progressbar"):
            self.use_set(t)
            return ["self._set(%s, %s)" % (w, value)]
        raise _Skip("can't put text into a %s" % WIDGETS[t]["name"])

    def warn_title(self):
        return py_str(self.main["props"].get("title") or "Message")

    # ---- the actions -------------------------------------------------------
    def act_show_message(self, p, ctx):
        func = {"warning": "showwarning", "error": "showerror"}.get(p.get("icon"), "showinfo")
        return self.call("", "messagebox.%s(" % func,
                         [self.text_expr(p.get("title")), self.text_expr(p.get("message"))],
                         self.parent_arg(ctx) + ")")

    def act_confirm(self, p, ctx):
        lines = self.call("", "if not messagebox.askyesno(",
                          [self.text_expr(p.get("title")), self.text_expr(p.get("message"))],
                          self.parent_arg(ctx) + "):")
        return lines + [IND + "return  # the user said No: stop here"]

    def act_ask_text(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        lines = self.call("", "answer = simpledialog.askstring(",
                          [self.text_expr(p.get("title")), self.text_expr(p.get("message"))],
                          self.parent_arg(ctx) + ")")
        lines += ["if answer is None:", IND + "return  # the user pressed Cancel: stop here"]
        return lines + self.guard(ctx, [node["id"]], self.set_lines(node, "answer"))

    def act_check(self, p, ctx):
        node = self.target(p, types=model.INPUT_TYPES + ["Label"])
        t, nid = node["type"], node["id"]
        cond = p.get("condition") or "not_empty"
        value = self.text_expr(p.get("value"))
        pre = []

        def got():
            self.use_get(t)
            return 'self._get("%s")' % nid

        if cond in ("checked", "unchecked") and t == "Checkbutton":
            fail = "self.%s.get()" % self.check_var[nid]
            fail = "not " + fail if cond == "checked" else fail
        elif cond in ("checked", "unchecked") and t == "Radiobutton":
            op = "!=" if cond == "checked" else "=="
            fail = "self.%s.get() %s %s" % (self.radio_var_of[nid], op,
                                            py_str(self.radio_value(node)))
        elif cond in ("not_empty", "checked"):
            fail = "not %s.strip()" % got()
        elif cond in ("empty", "unchecked"):
            fail = "%s.strip()" % got()
        elif cond == "equals":
            fail = "%s.strip() != %s" % (got(), value)
        elif cond == "not_equals":
            fail = "%s.strip() == %s" % (got(), value)
        elif cond == "contains":
            fail = "%s not in %s" % (value, got())
        elif cond in ("number", "greater", "less"):
            self.helpers.add("number")
            pre.append("number = self._number(%s)" % got())
            if cond == "number":
                fail = "number is None"
            else:
                op = "<=" if cond == "greater" else ">="
                raw = p.get("value")
                try:
                    limit = _num_lit(float(str(raw).strip()))
                    fail = "number is None or number %s %s" % (op, limit)
                except ValueError:
                    pre.append("limit = self._number(%s)" % value)
                    fail = "number is None or limit is None or number %s limit" % op
        else:
            raise _Skip("unknown condition %r" % cond)
        lines = pre + ["if %s:" % fail]
        if (p.get("message") or "").strip():
            lines += [IND + l for l in self.call(
                "", "messagebox.showwarning(",
                [self.warn_title(), self.text_expr(p.get("message"))], self.parent_arg(ctx) + ")")]
        lines.append(IND + "return  # the check failed: stop here")
        return lines

    def act_set_checked(self, p, ctx):
        # Works through the variables made in __init__, so it is safe (and
        # remembered) even while the widget's window is closed.
        node = self.target(p, types=["Checkbutton", "Radiobutton"])
        state = p.get("state") or "tick"
        if node["type"] == "Checkbutton":
            var = "self." + self.check_var[node["id"]]
            if state == "toggle":
                return ["%s.set(not %s.get())" % (var, var)]
            return ["%s.set(%r)" % (var, state != "untick")]
        var = "self." + self.radio_var_of[node["id"]]
        value = py_str(self.radio_value(node))
        if state == "tick":
            return ["%s.set(%s)" % (var, value)]
        lines = ["if %s.get() == %s:" % (var, value),
                 IND + '%s.set("")  # now no option is chosen' % var]
        if state == "toggle":
            lines += ["else:", IND + "%s.set(%s)" % (var, value)]
        return lines

    def act_set_text(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        return self.guard(ctx, [node["id"]], self.set_lines(node, self.text_expr(p.get("text"))))

    def act_append_text(self, p, ctx):
        node = self.target(p, types=["Label", "Entry", "Text"])
        self.helpers.add("append")
        self.append_types.add(node["type"])
        new_line = p.get("newline", "yes") != "no"
        return self.guard(ctx, [node["id"]], ["self._append(%s, %s, new_line=%r)" % (
            self.wexpr(node), self.text_expr(p.get("text")), new_line)])

    def act_change_number(self, p, ctx):
        node = self.target(p, types=model.NUMBER_TARGETS)
        self.helpers.update(("add_number", "number"))
        self.addnum_types.add(node["type"])
        if node["type"] in ("Label", "Entry", "Spinbox"):
            self.helpers.add("number_text")
        if node["type"] in ("Entry", "Spinbox"):
            self.use_set(node["type"])
        return self.guard(ctx, [node["id"]], ["self._add_number(%s, %s)" % (
            self.wexpr(node), _num_lit(p.get("amount", 1)))])

    def act_clear(self, p, ctx):
        node = self.target(p, types=["Entry", "Text", "Listbox", "Combobox", "Label", "Spinbox"])
        w = self.wexpr(node)
        if node["type"] == "Listbox":
            lines = ['%s.delete(0, "end")  # remove all items' % w]
        else:
            lines = self.set_lines(node, '""')
        return self.guard(ctx, [node["id"]], lines)

    def act_add_item(self, p, ctx):
        node = self.target(p, types=["Listbox", "Combobox"])
        w, text = self.wexpr(node), self.text_expr(p.get("text"))
        if node["type"] == "Listbox":
            lines = ['%s.insert("end", %s)' % (w, text)]
        else:
            lines = ['%s.config(values=list(%s.cget("values")) + [%s])' % (w, w, text)]
        return self.guard(ctx, [node["id"]], lines)

    def act_remove_selected(self, p, ctx):
        node = self.target(p, types=["Listbox"])
        w = self.wexpr(node)
        lines = ["# (from the last to the first, so the positions don't shift)",
                 "for index in reversed(%s.curselection()):" % w,
                 IND + "%s.delete(index)" % w]
        return self.guard(ctx, [node["id"]], lines)

    def _gridded(self, node):
        """The widget that is actually placed on the grid."""
        if tkmap.initial_state(node).get("scrollbar"):
            return self.wexpr(node) + ".master"   # the frame with the scrollbar
        return self.wexpr(node)

    def act_show_widget(self, p, ctx):
        node = self.target(p)
        if node["type"] == "Tab":
            nb = self.parent.get(node["id"])
            lines = ["%s.add(%s)  # show the tab again" % (self.wexpr(nb), self.wexpr(node))]
        else:
            lines = ["%s.grid()" % self._gridded(node)]
        return self.guard(ctx, [node["id"]], lines)

    def act_hide_widget(self, p, ctx):
        node = self.target(p)
        if node["type"] == "Tab":
            nb = self.parent.get(node["id"])
            lines = ["%s.hide(%s)" % (self.wexpr(nb), self.wexpr(node))]
        else:
            lines = ["%s.grid_remove()  # .grid() shows it again" % self._gridded(node)]
        return self.guard(ctx, [node["id"]], lines)

    def _state_types(self):
        return next(a for a in ACTIONS["enable_widget"]["params"])["options"]["types"]

    def act_enable_widget(self, p, ctx):
        node = self.target(p, types=self._state_types())
        state = "normal"
        if node["type"] == "Combobox" and node["props"].get("readonly"):
            state = "readonly"
        return self.guard(ctx, [node["id"]], ['%s.config(state="%s")' % (self.wexpr(node), state)])

    def act_disable_widget(self, p, ctx):
        node = self.target(p, types=self._state_types())
        return self.guard(ctx, [node["id"]], ['%s.config(state="disabled")' % self.wexpr(node)])

    def act_set_color(self, p, ctx):
        node = self.target(p, types=model.COLOR_TARGETS)
        args = []
        if p.get("fg") and node["type"] not in ("Frame", "Canvas"):
            args.append("fg=%s" % py_str(p["fg"]))
        if p.get("bg"):
            args.append("bg=%s" % py_str(p["bg"]))
        if not args:
            raise _Skip("no color chosen")
        return self.guard(ctx, [node["id"]], ["%s.config(%s)" % (self.wexpr(node), ", ".join(args))])

    def act_open_window(self, p, ctx):
        win = self.window_param(p)
        if win["id"] == self.main_id:
            return ["self.deiconify()", "self.lift()"]
        return ["self.%s()" % self.open_method[win["id"]]]

    def act_close_window(self, p, ctx):
        win = self.window_param(p)
        wid = win["id"]
        if ctx["closing"] == wid:
            raise _Skip("this window is already closing")
        closer = self.handler.get((wid, "on_close"))
        if wid == self.main_id:
            ctx["stop"] = "the app has closed"
            return ["self.%s()" % closer if closer else "self.destroy()"]
        call = "self.%s()  # runs its 'when closed' actions too" % closer if closer \
            else "self.%s.destroy()" % wid
        self.helpers.add("is_open")
        ctx["closed"].add(wid)
        return ["if self._is_open(self.%s):" % wid, IND + call]

    def act_switch_tab(self, p, ctx):
        nid = p.get("tab") or ""
        node = self.nodes.get(nid)
        if node is None or node["type"] != "Tab":
            raise _Skip("no tab chosen" if not nid else "tab %r no longer exists" % nid)
        nb = self.parent[nid]
        return self.guard(ctx, [nid], ["%s.select(%s)" % (self.wexpr(nb), self.wexpr(node))])

    def act_set_title(self, p, ctx):
        win = self.window_param(p)
        text = self.text_expr(p.get("text"))
        if win["id"] == self.main_id:
            return ["self.title(%s)" % text]
        return self.guard(ctx, [win["id"]], ["self.%s.title(%s)" % (win["id"], text)])

    def act_choose_file(self, p, ctx):
        node = self.target(p, types=model.TEXT_TARGETS)
        func = {"save": "asksaveasfilename", "folder": "askdirectory"}.get(
            p.get("mode"), "askopenfilename")
        par = self.parent_arg(ctx)[2:]
        lines = ["path = filedialog.%s(%s)" % (func, par),
                 "if not path:",
                 IND + "return  # the user pressed Cancel: stop here"]
        return lines + self.guard(ctx, [node["id"]], self.set_lines(node, "path"))

    def act_load_file(self, p, ctx):
        node = self.target(p, types=["Text", "Entry", "Label"])
        if not (p.get("path") or "").strip():
            raise _Skip("no file name given")
        lines = ["try:",
                 IND + "with open(%s, encoding=\"utf-8\", errors=\"replace\") as file:"
                 % self.text_expr(p.get("path")),
                 IND * 2 + "text = file.read()",
                 "except OSError as error:",
                 IND + "messagebox.showerror(\"Could not open the file\", str(error)%s)"
                 % self.parent_arg(ctx),
                 IND + "return"]
        return lines + self.guard(ctx, [node["id"]], self.set_lines(node, "text"))

    def act_save_file(self, p, ctx):
        node = self.target(p, "source", types=["Text", "Entry", "Label", "Listbox"])
        if not (p.get("path") or "").strip():
            raise _Skip("no file name given")
        if node["type"] == "Listbox":
            text = '"\\n".join(%s.get(0, "end"))  # all items, one per line' % self.wexpr(node)
        else:
            self.use_get(node["type"])
            text = 'self._get("%s")' % node["id"]
        lines = ["text = " + text,
                 "try:",
                 IND + "with open(%s, \"w\", encoding=\"utf-8\") as file:"
                 % self.text_expr(p.get("path")),
                 IND * 2 + "file.write(text)",
                 "except OSError as error:",
                 IND + "messagebox.showerror(\"Could not save the file\", str(error)%s)"
                 % self.parent_arg(ctx),
                 IND + "return"]
        return self.guard(ctx, [node["id"]], lines)

    def act_quit_app(self, p, ctx):
        ctx["stop"] = "the app has quit"
        return ["self.destroy()"]

    def act_run_code(self, p, ctx):
        code = textwrap.dedent((p.get("code") or "").expandtabs(4)).strip("\n")
        if not code.strip():
            return ["# (no code was written)"]
        lines = [l.rstrip() for l in code.splitlines()]
        try:
            compile("def _check(self, event=None):\n" + "\n".join(IND + l for l in lines),
                    "<your code>", "exec")
        except SyntaxError as e:
            return ["# Your code has a mistake (%s on line %s), so it was turned"
                    % (_one_line(e.msg), e.lineno - 1 if e.lineno else "?"),
                    "# into comments. Fix it and remove the # signs:"] + \
                   ["# " + l for l in lines]
        return ["# --- your own code ---"] + lines + ["# --- end of your own code ---"]

    # =====================================================================
    # helper methods (emitted only when used)
    # =====================================================================
    def helper_lines(self):
        h = self.helpers
        out = []

        def method(src):
            out.append("")
            out.extend((IND + l if l else "") for l in textwrap.dedent(src).strip("\n").splitlines())

        def branches(spec, else_line=None):
            """spec: list of (condition, [lines]) -> if/elif lines (indented once)."""
            lines = []
            for k, (cond, body) in enumerate(spec):
                lines.append(IND + ("if " if k == 0 else "elif ") + cond + ":")
                lines += [IND * 2 + b for b in body]
            if else_line:
                lines += [IND + l for l in else_line]
            return lines

        if "is_open" in h:
            method('''
                def _is_open(self, window):
                    """True if `window` has been opened and is not closed yet."""
                    return window is not None and window.winfo_exists()
                ''')

        if "get" in h:
            ts = self.get_types
            spec = []
            if "Text" in ts:
                spec.append(("isinstance(widget, tk.Text)", ['return widget.get("1.0", "end-1c")']))
            if "Listbox" in ts:
                spec.append(("isinstance(widget, tk.Listbox)", [
                    "# the selected item(s), separated by commas",
                    'return ", ".join(widget.get(i) for i in widget.curselection())']))
            if "Checkbutton" in ts:
                spec.append(("isinstance(widget, tk.Checkbutton)", [
                    'ticked = self.getboolean(self.getvar(widget.cget("variable")))',
                    'return "Yes" if ticked else "No"']))
            if "Radiobutton" in ts:
                spec.append(("isinstance(widget, tk.Radiobutton)", [
                    "# the text of the option that is chosen in its group",
                    'return str(self.getvar(widget.cget("variable")))']))
            if "Scale" in ts:
                spec.append(("isinstance(widget, tk.Scale)",
                             ["return self._number_text(widget.get())"]))
            if "Progressbar" in ts:
                spec.append(("isinstance(widget, ttk.Progressbar)",
                             ['return self._number_text(widget.cget("value"))']))
            classes = [c for t, c in (("Entry", "tk.Entry"), ("Spinbox", "tk.Spinbox"),
                                      ("Combobox", "ttk.Combobox")) if t in ts]
            if classes:
                cond = "isinstance(widget, %s)" % (
                    classes[0] if len(classes) == 1 else "(" + ", ".join(classes) + ")")
                spec.append((cond, ["return widget.get()"]))
            lines = [
                'def _get(self, name):',
                '    """Return what the widget called `name` holds right now, as text.',
                '    Gives "" when the widget\'s window is not open."""',
                '    widget = getattr(self, name, None)',
                '    if not isinstance(widget, tk.Widget) or not widget.winfo_exists():',
                '        return ""']
            for cond, body in spec:
                lines.append(IND + "if " + cond + ":")
                lines += [IND * 2 + b for b in body]
            if ts & set(TEXT_LIKE):
                lines.append(IND + 'return str(widget.cget("text"))  # labels, buttons, ...')
            else:
                lines.append(IND + 'return ""')
            out.append("")
            out += [IND + l for l in lines]

        if "set" in h:
            ts = self.set_types
            spec = []
            classes = [c for t, c in (("Entry", "tk.Entry"), ("Spinbox", "tk.Spinbox")) if t in ts]
            if classes:
                cond = "isinstance(widget, %s)" % (
                    classes[0] if len(classes) == 1 else "(" + ", ".join(classes) + ")")
                spec.append((cond, [
                    'state = widget.cget("state")',
                    'widget.config(state="normal")  # a disabled box can\'t be changed',
                    'widget.delete(0, "end")',
                    'widget.insert(0, value)',
                    'widget.config(state=state)']))
            if "Text" in ts:
                spec.append(("isinstance(widget, tk.Text)", [
                    'state = widget.cget("state")',
                    'widget.config(state="normal")  # a read-only area can\'t be changed',
                    'widget.delete("1.0", "end")',
                    'widget.insert("1.0", value)',
                    'widget.config(state=state)']))
            if "Listbox" in ts:
                spec.append(("isinstance(widget, tk.Listbox)", [
                    "# select the item that has this text (if there is one)",
                    'widget.selection_clear(0, "end")',
                    'items = widget.get(0, "end")',
                    "if value in items:",
                    IND + "widget.selection_set(items.index(value))",
                    IND + "widget.see(items.index(value))"]))
            if "Scale" in ts:
                spec.append(("isinstance(widget, tk.Scale)", [
                    "number = self._number(value)",
                    "if number is not None:",
                    IND + "widget.set(number)"]))
            if "Progressbar" in ts:
                spec.append(("isinstance(widget, ttk.Progressbar)", [
                    "number = self._number(value)",
                    "if number is not None:",
                    IND + "widget.config(value=number)"]))
            lines = [
                "def _set(self, widget, value):",
                '    """Put `value` into a text box, text area, list, number box or slider."""',
                "    value = str(value)"] + branches(spec)
            out.append("")
            out += [IND + l for l in lines]

        if "append" in h:
            ts = self.append_types
            spec = []
            if "Text" in ts:
                spec.append(("isinstance(widget, tk.Text)", [
                    'state = widget.cget("state")',
                    'widget.config(state="normal")',
                    'if new_line and widget.get("1.0", "end-1c"):',
                    IND + 'text = "\\n" + text',
                    'widget.insert("end", text)',
                    'widget.see("end")  # scroll down to the new text',
                    "widget.config(state=state)"]))
            if "Entry" in ts:
                spec.append(("isinstance(widget, tk.Entry)", [
                    "if new_line and widget.get():",
                    IND + 'text = " " + text  # a text box has only one line: use a space',
                    'state = widget.cget("state")',
                    'widget.config(state="normal")',
                    'widget.insert("end", text)',
                    "widget.config(state=state)"]))
            if "Label" in ts:
                spec.append(("isinstance(widget, tk.Label)", [
                    'old = widget.cget("text")',
                    "if new_line and old:",
                    IND + 'text = "\\n" + text',
                    "widget.config(text=old + text)"]))
            lines = [
                "def _append(self, widget, text, new_line=True):",
                '    """Add `text` to the end of a label, text box or text area."""'] + branches(spec)
            out.append("")
            out += [IND + l for l in lines]

        if "add_number" in h:
            ts = self.addnum_types
            spec = []
            if "Scale" in ts:
                spec.append(("isinstance(widget, tk.Scale)", ["widget.set(widget.get() + amount)"]))
            if "Progressbar" in ts:
                spec.append(("isinstance(widget, ttk.Progressbar)", [
                    'value = float(widget.cget("value")) + amount',
                    '# keep it between 0 and the maximum',
                    'widget.config(value=max(0, min(value, float(widget.cget("maximum")))))']))
            if "Label" in ts:
                spec.append(("isinstance(widget, tk.Label)", [
                    'number = self._number(widget.cget("text")) or 0',
                    "widget.config(text=self._number_text(number + amount))"]))
            classes = [c for t, c in (("Entry", "tk.Entry"), ("Spinbox", "tk.Spinbox")) if t in ts]
            if classes:
                cond = "isinstance(widget, %s)" % (
                    classes[0] if len(classes) == 1 else "(" + ", ".join(classes) + ")")
                spec.append((cond, [
                    "number = self._number(widget.get()) or 0",
                    "self._set(widget, self._number_text(number + amount))"]))
            lines = [
                "def _add_number(self, widget, amount):",
                '    """Add `amount` to the number shown in a widget',
                '    (it counts from 0 if the widget does not show a number yet)."""'] + branches(spec)
            out.append("")
            out += [IND + l for l in lines]

        if "number" in h:
            method('''
                def _number(self, text):
                    """Turn text like "12" or "3.5" into a number (None if it isn't one)."""
                    try:
                        return float(str(text).strip())
                    except ValueError:
                        return None
                ''')
        if "number_text" in h:
            method('''
                def _number_text(self, number):
                    """Show a number nicely: 3.0 becomes "3", 2.50 becomes "2.5"."""
                    number = round(float(number), 10)
                    return str(int(number)) if number.is_integer() else str(number)
                ''')
        if "load_image" in h:
            method('''
                def _load_image(self, path):
                    """Load a PNG or GIF picture. Gives None if it can't be loaded."""
                    if not os.path.exists(path):
                        # also look for the picture next to this program
                        folder = os.path.dirname(os.path.abspath(__file__))
                        path = os.path.join(folder, os.path.basename(path))
                    try:
                        return tk.PhotoImage(file=path)
                    except tk.TclError:
                        print("Could not load the picture:", path)
                        return None
                ''')
        return out

    # =====================================================================
    # whole file
    # =====================================================================
    def generate(self):
        title = self.main["props"].get("title") or self.project.data.get("app_name") or "My App"

        # Methods first (they record which helpers / variables are needed).
        handlers = []
        for win in self.project.windows:
            for n in self.project.walk(win):
                for ev, label in WIDGETS[n["type"]]["events"]:
                    name = self.handler.get((n["id"], ev))
                    if not name:
                        continue
                    if n["type"] == "Window":
                        doc = 'Window "%s" - %s.' % (_doc(n["props"].get("title", "")),
                                                     _doc(label.lower()))
                    else:
                        doc = "%s - %s." % (_doc(self.project.display_name(n)), _doc(label.lower()))
                    if n["type"] == "Window" and ev == "on_close":
                        lines = self._closing_handler(name, doc, n)
                    else:
                        lines = self.handler_lines(name, doc, n["events"][ev], win["id"])
                    handlers.append("")
                    handlers += lines
            for mi, menu in enumerate(win["props"].get("menu") or []):
                for ii, item in enumerate(menu.get("items", [])):
                    name = self.menu_handler.get((win["id"], mi, ii))
                    if name:
                        doc = "Menu %s > %s" % (_doc(menu.get("label", "")), _doc(item.get("label", "")))
                        if not doc.rstrip().endswith((".", "…")):
                            doc += "."
                        handlers.append("")
                        handlers += self.handler_lines(name, doc, item["actions"], win["id"])

        opens = []
        for win in self.project.windows[1:]:
            opens.append("")
            opens += self.open_method_lines(win)
        init = self.init_method()
        helpers = self.helper_lines()

        cls = ["class App(tk.Tk):",
               IND + '"""Your app. It is the main window, "%s"."""' % _doc(title), ""]
        cls += init
        if opens:
            cls += ["", IND + "# --- The other windows ---"] + opens
        if handlers:
            cls += ["", IND + "# --- What happens when the user does something (event handlers) ---"]
            cls += handlers
        if helpers:
            cls += ["", IND + "# --- Little helpers used above (you don't need to change these) ---"]
            cls += helpers

        body = "\n".join(cls)
        code_only = "\n".join(l for l in cls if not l.strip().startswith("#"))
        from_tk = [m for m in ("ttk", "messagebox", "filedialog", "simpledialog")
                   if re.search(r"\b%s\." % m, code_only)]
        imports = []
        if re.search(r"\bos\.", code_only):
            imports.append("import os")
        imports.append("import tkinter as tk")
        if from_tk:
            imports.append("from tkinter import " + ", ".join(from_tk))
        if re.search(r"\btkfont\.", code_only):
            imports.append("import tkinter.font as tkfont")

        header = ['"""%s - made with TkBuilder.' % _doc(title),
                  "",
                  "To run it: double-click this file, or type  python <this file's name>.py",
                  '"""']
        footer = ["", "", 'if __name__ == "__main__":', IND + "app = App()", IND + "app.mainloop()"]
        text = "\n".join(header + imports + ["", ""] + [body] + footer) + "\n"
        return re.sub(r"\n{3,}(?=    )", "\n\n", text)

    def _closing_handler(self, name, doc, win):
        """Handler for a window's 'when closed' event: run the actions, then
        really close the window (unless an action stopped early)."""
        ind = IND * 2
        out = [IND + "def %s(self, event=None):" % name, ind + '"""%s"""' % doc]
        body, ctx = self.actions(win["events"]["on_close"], win["id"], ind, closing=win["id"])
        out += body
        if not ctx.get("stop"):
            out.append(ind + "# Now really close the window")
            out += [ind + l for l in self.window_closing_tail(win["id"])]
        return out


def generate(project):
    """Return the complete Python program for `project` as a string."""
    return _Generator(project).generate()
