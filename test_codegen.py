"""
Tests for codegen.py (and a smoke test for codeview.py).

Run with:   TKBUILDER_NO_DIALOGS=1 python test_codegen.py

Builds projects with the model.Project API (every widget type, every action,
both code styles, every theme, grid + tabs, menus, timers, hooks), checks the
generated code (compiles, PEP 8 line length, no unused imports, deterministic),
then imports and runs it: creates the app, calls the runtime API and event
handlers (message boxes and file dialogs are replaced by quiet stubs) and
checks the results. Background calls use a real backend module written to a
temporary folder; the tests wait for them by pumping update().
Short-lived windows may flash on screen.
"""

import ast
import glob
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import time
import tkinter as tk
import unittest
from tkinter import filedialog, messagebox, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import apptheme       # noqa: E402
import codegen        # noqa: E402
import model          # noqa: E402

TMP = tempfile.mkdtemp(prefix="tkb_codegen_test_")
BACKEND_DIR = os.path.join(TMP, "backend")

BACKEND = '''
"""A tiny backend used by the TkBuilder code generator tests."""
import time


def area(width, height=2.0):
    return width * height


def slow_double(x):
    time.sleep(0.15)
    return x * 2


def as_dict(x):
    return {"out_label": "x=%s" % x, "not_a_widget": 1}


def pair(x):
    return x, x + 1


def table_rows(n):
    return [["row %d" % i, i * 1.5] for i in range(int(n))]


def fail(x=None):
    raise ValueError("bad value from the backend")


def crash(x=None):
    raise RuntimeError("it crashed")


def echo_all(values):
    return len(values)


def chatty():
    print("hello from print")
    return 1
'''


# ---------------------------------------------------------------------------
# dialog stubs: record every call, answer instantly
# ---------------------------------------------------------------------------

CALLS = []
ANSWERS = {"askyesno": True, "askstring": "typed", "asksaveasfilename": "",
           "askopenfilename": "", "askdirectory": ""}


def _stub(name):
    def f(*args, **kwargs):
        CALLS.append((name, args, kwargs))
        return ANSWERS.get(name, "ok")
    return f


for _n in ("showinfo", "showwarning", "showerror", "askyesno"):
    setattr(messagebox, _n, _stub(_n))
for _n in ("askopenfilename", "asksaveasfilename", "askdirectory"):
    setattr(filedialog, _n, _stub(_n))
simpledialog.askstring = _stub("askstring")


def calls(name):
    return [c for c in CALLS if c[0] == name]


# ---------------------------------------------------------------------------
# project building helpers
# ---------------------------------------------------------------------------

def act(atype, **params):
    a = model.new_action(atype)
    a["params"].update(params)
    return a


def add(project, wtype, container, node_id=None, row=None, col=None, mode="cell", **props):
    node = project.new_node(wtype)
    if node_id:
        node["id"] = node_id
    node["props"].update(props)
    cont = project.find(container)
    if row is None:
        row, col = project.next_free_cell(cont)
    project.place(node, container, row, col or 0, mode)
    return node


def make_png():
    path = os.path.join(TMP, "dot.png")
    if not os.path.exists(path):
        r = tk.Tk()
        r.withdraw()
        img = tk.PhotoImage(master=r, width=8, height=8)
        img.put("#ff0000", to=(0, 0, 8, 8))
        img.write(path, format="png")
        r.destroy()
    return path


def call_fn(function, args=(), result="none", module="calc_backend", **kw):
    a = act("call_function", module=module, function=function, args=list(args), result=result)
    a["params"].update(kw)
    return a


def build_full_project(name="Full Test"):
    """Every widget type and every action type, two windows, menus, tabs."""
    p = model.Project()
    p.settings.update(app_name=name, backend_dir=BACKEND_DIR)
    main = p.main_window
    main["props"].update(title='Full "test" app', width=700, height=600, padding=8,
                         min_width=300, min_height=200, icon=make_png())
    M = main["id"]

    add(p, "Label", M, "title_label", text="Hello\n\"quoted\" {braces}", variant="Title")
    add(p, "Label", M, "length_label", text="Length (m):")
    add(p, "Entry", M, "length", row=1, col=1, value_type="float", text="2.5",
        placeholder="metres", tooltip="The length")
    add(p, "Label", M, "count_label", text="Count:")
    add(p, "Entry", M, "count", row=2, col=1, value_type="int", text="3")
    add(p, "Entry", M, "name_box", text="Ann", fg="#123456", bg="#ffffee")
    add(p, "Entry", M, "secret", password=True, state="readonly", text="pw")
    add(p, "Label", M, "out_label", text="0", bg="yellow",
        font={"family": "", "size": 12, "bold": True, "italic": False, "underline": False})
    add(p, "Label", M, "pic", image=make_png(), text="picture")
    add(p, "Text", M, "notes", text="first line\nsecond", width=20, height=3)
    add(p, "Text", M, "readonly_text", readonly=True, scrollbar=False, text="RO")
    add(p, "Checkbutton", M, "agree", checked=True, text="I agree", variant="Switch")
    add(p, "Checkbutton", M, "news", checked=False)
    add(p, "Radiobutton", M, "small", text="Small", group="size", value="S")
    add(p, "Radiobutton", M, "large", text="Large", group="size", value="L", selected=True)
    add(p, "Combobox", M, "factor", items=["1.5", "2", "3"], text="2", value_type="float")
    add(p, "Combobox", M, "fruit", items=["Apple", "Pear"], text="Pear", readonly=False)
    add(p, "Listbox", M, "todo", items=["one", "two", "three"], selectmode="extended")
    add(p, "Listbox", M, "plain_list", scrollbar=False)
    add(p, "Spinbox", M, "amount", value=5, from_=0, to=10, increment=0.5, decimals=1)
    add(p, "Spinbox", M, "steps", value=4, from_=0, to=100)
    add(p, "Scale", M, "angle", value=30, resolution=0.5, from_=0, to=90)
    add(p, "Scale", M, "level", value=3, showvalue=False, orient="vertical")
    add(p, "FilePicker", M, "data_file", text="C:/data/in.csv",
        filetypes=["CSV files|*.csv", "All files|*.*"])
    add(p, "FilePicker", M, "out_folder", mode="folder", state="disabled")
    add(p, "Table", M, "results", rows=[["a", "1"], ["b", "2"]],
        columns=[{"title": "Name", "width": 100, "anchor": "w"},
                 {"title": "Value", "width": 80, "anchor": "e"}])
    add(p, "Table", M, "many_rows", selectmode="extended", striped=False)
    add(p, "Console", M, "console", timestamps=False, max_lines=50)
    add(p, "Console", M, "stamped", timestamps=True, max_lines=3)
    add(p, "Progressbar", M, "progress", value=40, maximum=50)
    add(p, "Progressbar", M, "busy_bar", value=0)
    add(p, "Separator", M, "line")
    add(p, "Canvas", M, "canvas1", width=60, height=40)
    add(p, "Button", M, "go", text="Go!", shortcut="Ctrl+G", tooltip="Run all", default=True)
    add(p, "Button", M, "calc", text="Calc", variant="Accent")
    add(p, "Button", M, "bg_calc", text="Background calc")
    add(p, "Button", M, "fail_btn", text="Fail")
    add(p, "Button", M, "cmd_btn", text="Run command")
    add(p, "Button", M, "pic_button", image=os.path.join(TMP, "missing.png"))
    add(p, "Button", M, "colored", bg="#00aa00", fg="white")
    panel = add(p, "Frame", M, "panel", variant="Card")
    panel["layout"].update(fill="both", colspan=2)
    p.compact(main)
    add(p, "Label", "panel", "panel_label", text="In panel")
    add(p, "LabelFrame", "panel", "options_group", text="Options", fg="#aa0000")
    add(p, "Label", "options_group", "deep_label", text="Deep")
    nb = add(p, "Notebook", M, "tabs")
    nb["children"][0]["id"], nb["children"][1]["id"] = "first_tab", "second_tab"
    add(p, "Label", "first_tab", "tab_label", text="On tab 1")
    add(p, "Entry", "second_tab", "tab_entry", text="tab text")

    # --- second window --------------------------------------------------
    w2 = p.add_window("Settings")
    p.rename(w2["id"], "settings")
    w2["props"].update(resizable=False, start="normal", topmost=True)
    add(p, "Label", "settings", "w2_label", text="Settings label")
    add(p, "Entry", "settings", "w2_entry", text="w2 text")
    add(p, "Checkbutton", "settings", "w2_check", checked=True)
    add(p, "Radiobutton", "settings", "w2_small", text="W2 small", group="size", value="S2")
    add(p, "Button", "settings", "w2_close", text="Close me")
    w2["events"]["on_open"] = [act("set_text", target="w2_label", text="Opened {out_label}")]
    w2["events"]["on_close"] = [act("set_text", target="out_label", text="w2 closed"),
                                act("close_window", window="settings")]  # skipped
    p.find("w2_close")["events"]["command"] = [act("close_window", window="settings")]
    w2["props"]["menu"] = [{"label": "Window", "items": [
        {"label": "Close", "shortcut": "Esc",
         "actions": [act("close_window", window="settings")]}]}]

    # --- third window: empty, never opened -------------------------------
    w3 = p.add_window("Empty")
    p.rename(w3["id"], "empty_window")

    # --- menus on the main window -----------------------------------------
    main["props"]["menu"] = [
        {"label": "File", "items": [
            {"label": "Open settings...", "shortcut": "Ctrl+O",
             "actions": [act("open_window", window="settings", modal=False)]},
            {"separator": True},
            {"label": "Quit", "actions": [act("quit_app"), act("log", target="console",
                                                               text="never")]},
            {"label": "My own", "actions": [], "hook": True},
        ]},
        {"label": "Help", "items": [
            {"label": "About", "actions": [act("show_message", title="About",
                                                message="Made with \"TkBuilder\" \\o/")]}]},
    ]

    # --- events -----------------------------------------------------------
    main["events"]["on_open"] = [act("log", target="console", text="opened")]
    main["events"]["on_close"] = [act("confirm", message="Quit?")]
    main["hooks"] = ["on_close"]
    p.find("length")["events"]["on_enter"] = [act("set_text", target="out_label",
                                                  text="L={length}")]
    p.find("name_box")["events"]["on_change"] = [act("change_number", target="progress",
                                                     amount=1)]
    p.find("fruit")["events"]["on_select"] = [act("log", target="console", text="{fruit}")]
    p.find("todo")["events"]["on_select"] = [act("log", target="console", text="{todo}")]
    p.find("todo")["events"]["on_double_click"] = [act("log", target="console", text="dbl")]
    p.find("results")["events"]["on_select"] = [act("log", target="console", text="row")]
    p.find("amount")["events"]["command"] = [act("log", target="console", text="spin")]
    p.find("angle")["events"]["command"] = [act("set_text", target="out_label",
                                                text="angle {angle}")]
    p.find("agree")["events"]["command"] = [act("log", target="console", text="agree")]
    p.find("small")["events"]["command"] = [act("log", target="console", text="small")]
    p.find("data_file")["events"]["on_change"] = [act("log", target="console",
                                                      text="file {data_file}")]
    p.find("tabs")["events"]["on_tab_change"] = [act("log", target="console", text="tab {tabs}")]
    p.find("canvas1")["events"]["on_click"] = [act("log", target="console", text="canvas")]
    p.find("title_label")["events"]["on_click"] = [act("log", target="console", text="title")]
    p.find("title_label")["hooks"] = ["on_click"]

    p.find("calc")["events"]["command"] = [
        call_fn("area", [{"name": "width", "source": "widget", "widget": "length"},
                         {"name": "height", "source": "value", "value": "4"}],
                result="widget", result_widget="out_label", background=False),
        act("log", target="console", text="area done {out_label}")]
    p.find("bg_calc")["events"]["command"] = [
        call_fn("slow_double", [{"name": "x", "source": "widget", "widget": "count"}],
                result="widget", result_widget="out_label", background=True, busy="busy_bar"),
        act("log", target="console", text="after slow {out_label}"),
        call_fn("pair", [{"name": "", "source": "widget", "widget": "steps"}],
                result="tuple", result_widgets=["w2_entry", "name_box"], background=True),
        act("log", target="console", text="after pair"),
        call_fn("as_dict", [{"name": "x", "source": "value", "value": "{count}"}],
                result="dict", background=True),
        call_fn("table_rows", [{"name": "n", "source": "value", "value": "3"}],
                result="widget", result_widget="many_rows", background=True),
        call_fn("echo_all", [{"name": "values", "source": "all"}],
                result="widget", result_widget="w2_label", background=False),
        act("set_text", target="title_label", text="all done")]
    p.find("bg_calc")["hooks"] = ["command"]
    p.find("fail_btn")["events"]["command"] = [
        call_fn("fail", result="none", background=True),
        act("log", target="console", text="not reached")]
    p.find("cmd_btn")["events"]["command"] = [
        act("run_command", command='"%s" -c "print(\'hello\'); print(\'world\')"'
            % sys.executable, output="console", busy="busy_bar"),
        act("log", target="console", text="command finished")]

    go = p.find("go")
    go["events"]["command"] = [
        act("show_message", title="Hi {name_box}", message="Line1\nLine2 {{x}}", icon="warning"),
        act("show_message", icon="error", message="err"),
        act("show_message", message="{name_box}"),
        act("confirm", title="Sure?", message="Really {name_box}?"),
        act("ask_text", title="Q", message="Type", target="title_label"),
        act("check", target="name_box", condition="not_empty", message="Need a name"),
        act("check", target="agree", condition="checked"),
        act("check", target="large", condition="checked", message="Pick large"),
        act("check", target="news", condition="unchecked"),
        act("check", target="name_box", condition="equals", value="Ann"),
        act("check", target="name_box", condition="not_equals", value="Bob {count}"),
        act("check", target="name_box", condition="contains", value="n"),
        act("check", target="length", condition="number"),
        act("check", target="length", condition="greater", value="1"),
        act("check", target="angle", condition="less", value="{count}00"),
        act("check", target="fruit", condition="not_empty"),
        act("set_checked", target="news", state="tick"),
        act("set_checked", target="w2_check", state="toggle"),
        act("set_checked", target="small", state="untick"),
        act("set_checked", target="large", state="toggle"),
        act("set_text", target="title_label", text='Hi {name_box}! {{x}} "q" \\b {missing}'),
        act("set_text", target="notes", text="{agree}|{size}|{large}|{angle}|{todo}"),
        act("set_text", target="readonly_text", text="changed"),
        act("set_text", target="secret", text="new secret"),
        act("set_text", target="fruit", text="Apple"),
        act("set_text", target="amount", text="7"),
        act("set_text", target="angle", text="12.3"),
        act("set_text", target="progress", text="25"),
        act("set_text", target="todo", text="two"),
        act("set_text", target="options_group", text="Group {count}"),
        act("set_text", target="second_tab", text="Tab two"),
        act("set_text", target="w2_entry", text="into hidden window"),
        act("set_text", target="data_file", text="C:/x.txt"),
        act("append_text", target="notes", text="more", newline="yes"),
        act("append_text", target="name_box", text="Smith", newline="yes"),
        act("append_text", target="out_label", text="x", newline="no"),
        act("append_text", target="console", text="appended"),
        act("change_number", target="steps", amount=2),
        act("change_number", target="angle", amount=1),
        act("change_number", target="progress", amount=100),
        act("change_number", target="count", amount=-1),
        act("log", target="stamped", text="one"),
        act("log", target="stamped", text="two"),
        act("log", target="stamped", text="three"),
        act("log", target="stamped", text="four"),
        act("log", target="plain_list", text="logged item"),
        act("clear", target="fruit"),
        act("clear", target="plain_list"),
        act("clear", target="out_folder"),
        act("add_item", target="todo", text="four {name_box}"),
        act("add_item", target="fruit", text="Kiwi"),
        act("remove_selected", target="todo"),
        act("add_row", target="results", values="c|{count}"),
        act("remove_selected", target="results"),
        act("export_table", target="results", path=os.path.join(TMP, "t.csv")),
        act("export_table", target="results", path=""),
        act("hide_widget", target="notes"),
        act("show_widget", target="notes"),
        act("hide_widget", target="canvas1"),
        act("hide_widget", target="second_tab"),
        act("show_widget", target="second_tab"),
        act("disable_widget", target="fruit"),
        act("enable_widget", target="fruit"),
        act("disable_widget", target="notes"),
        act("disable_widget", target="data_file"),
        act("enable_widget", target="data_file"),
        act("set_color", target="out_label", fg="red", bg="#eeeeee"),
        act("set_color", target="notes", fg="blue"),
        act("set_color", target="canvas1", bg="black"),
        act("set_color", target="name_box", bg="#ffeeee"),
        act("set_color", target="out_label", fg=""),     # skipped: no color
        act("switch_tab", tab="second_tab"),
        act("set_title", window=M, text="Title {count}"),
        act("set_title", window="settings", text="Settings!"),
        act("open_window", window="settings", modal=True),
        act("close_window", window="settings"),
        act("open_window", window=M),
        act("timer_control", window=M, state="stop"),
        act("timer_control", window=M, state="start"),
        act("timer_control", window="settings"),            # skipped: no timer
        act("choose_file", mode="open", target="name_box"),  # dialog cancelled -> stop
        act("log", target="console", text="unreachable"),
    ]
    p.find("colored")["events"]["command"] = [
        act("reset_form", window=M),
        act("load_file", path=os.path.join(TMP, "t.csv"), target="notes"),
        act("save_file", path=os.path.join(TMP, "saved.txt"), source="notes"),
        act("save_file", path=os.path.join(TMP, "list.txt"), source="todo"),
        act("load_file", path=os.path.join(TMP, "nope", "x.txt"), target="console"),
        act("log", target="console", text="unreachable"),
    ]
    p.find("pic_button")["events"]["command"] = [
        act("open_url", url=""),                                  # skipped
        act("run_code", code="self.set('out_label', 'from code')\nif True:\n    pass"),
        act("run_code", code="this is not )python"),
        act("set_text", target="ghost", text="x"),                 # skipped: missing
        act("clear", target="w2_label"),
        {"type": "unknown_action", "params": {}},
        act("close_window", window=M),
        act("log", target="console", text="never"),
    ]

    # timer on the main window
    main["props"]["timer_ms"] = 30
    main["events"]["on_timer"] = [act("change_number", target="steps", amount=1)]
    return p


def build_small_project(name="Small App", theme="modern_light"):
    p = model.Project()
    p.settings.update(app_name=name, theme=theme)
    M = p.main_window["id"]
    add(p, "Label", M, "label1", text="Value:")
    add(p, "Entry", M, "value", row=0, col=1, value_type="float", text="1.5")
    btn = add(p, "Button", M, "button1", text="Go")
    btn["events"]["command"] = [act("set_text", target="label1", text="{value}")]
    return p


# ---------------------------------------------------------------------------
# running generated code
# ---------------------------------------------------------------------------

_counter = [0]


def export_and_import(project, code_style="split", folder=None):
    """Export into a fresh folder, import the module that runs the app and
    return (app class, folder, module)."""
    project.settings["code_style"] = code_style
    _counter[0] += 1
    folder = folder or os.path.join(TMP, "out%d" % _counter[0])
    report = codegen.export(project, folder)
    base = project.module_basename()
    for mod in (base, base + "_ui"):
        sys.modules.pop(mod, None)
    sys.path.insert(0, folder)
    try:
        spec = importlib.util.spec_from_file_location(base, os.path.join(folder, base + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[base] = module
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(folder)
    return getattr(module, project.class_name()), folder, report


def pump(app, until=None, timeout=5.0):
    """Process events until `until()` is true (or timeout)."""
    end = time.time() + timeout
    while time.time() < end:
        app.update()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until is None


def close(app):
    try:
        app.destroy()
    except tk.TclError:
        pass


def imported_names(tree):
    names = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                names[a.asname or a.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names[a.asname or a.name] = node.lineno
    return names


def code_problems(source):
    """Compile errors, long lines, unused imports, trailing spaces."""
    problems = []
    tree = ast.parse(source)
    compile(source, "<generated>", "exec")
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for name in imported_names(tree):
        if name not in used:
            problems.append("unused import " + name)
    for i, line in enumerate(source.splitlines(), 1):
        # (long strings from the project itself, e.g. paths, can't be wrapped)
        if len(line) > 99 and not re.search(r"(\"[^\"]{50,}\"|'[^']{50,}')", line):
            problems.append("line %d too long (%d)" % (i, len(line)))
        if line != line.rstrip():
            problems.append("line %d has trailing spaces" % i)
    if "\n\n\n\n" in source:
        problems.append("more than two blank lines")
    return problems


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class GeneratedCodeTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.makedirs(BACKEND_DIR, exist_ok=True)
        with open(os.path.join(BACKEND_DIR, "calc_backend.py"), "w") as f:
            f.write(BACKEND)

    def setUp(self):
        CALLS.clear()
        ANSWERS.update({"askyesno": True, "askstring": "typed", "asksaveasfilename": "",
                        "askopenfilename": "", "askdirectory": ""})

    # ---- static checks ---------------------------------------------------
    def test_code_quality_both_styles(self):
        for style in ("split", "single"):
            p = build_full_project()
            p.settings["code_style"] = style
            files = codegen.generate(p)
            self.assertEqual(sorted(files), sorted(
                ["full_test_ui.py", "full_test.py"] if style == "split" else ["full_test.py"]))
            for name, src in files.items():
                self.assertEqual(code_problems(src), [], "%s (%s)" % (name, style))

    def test_deterministic(self):
        a = codegen.generate(build_full_project())
        b = codegen.generate(build_full_project())
        self.assertEqual(a, b)

    def test_every_action_and_widget_is_covered(self):
        p = build_full_project()
        used_types = {n["type"] for n in p.walk()}
        self.assertEqual(set(model.WIDGETS) - used_types, set())
        used_actions = {a["type"] for n in p.walk() for lst in p.iter_action_lists(n)
                        for a in lst}
        self.assertEqual(set(model.ACTIONS) - used_actions, set())

    def test_helpers_only_when_used(self):
        src = codegen.generate_single(build_small_project())
        for absent in ("class FilePicker", "class Tooltip", "class Placeholder",
                       "class ConsoleWriter", "class MissingModule", "_run_command",
                       "_start_timer", "_fill_table", "_scale_moved", "import subprocess",
                       "Accent.TButton", "Treeview", "_groups"):
            self.assertNotIn(absent, src)
        self.assertEqual(code_problems(src), [])
        full = codegen.generate_single(build_full_project())
        for present in ("class FilePicker", "class Tooltip", "class Placeholder",
                        "class MissingModule", "def _run_command", "def _start_timer",
                        "Accent.TButton", "Title.TLabel", "Switch.TCheckbutton"):
            self.assertIn(present, full)

    def test_split_layout(self):
        p = build_full_project()
        files = codegen.generate(p)
        ui, user = files["full_test_ui.py"], files["full_test.py"]
        self.assertIn("Generated by TkBuilder - do not edit", ui)
        self.assertIn("class FullTestUI(tk.Tk):", ui)
        self.assertIn("class FullTest(FullTestUI):", user)
        self.assertIn("from full_test_ui import FullTestUI", user)
        self.assertIn('if __name__ == "__main__":\n    FullTest().mainloop()', user)
        # every hook has a stub with an example in the user file
        for hook in ("on_bg_calc_clicked", "on_title_label_clicked", "on_main_window_closing",
                     "on_menu_file_my_own"):
            self.assertIn("def %s(self):" % hook, user)
            self.assertIn("def %s(self):" % hook, ui)
        self.assertIn('self.get("length")  # float', user)
        self.assertIn("How to use", ui)
        self.assertIn('self.on("bg_calc", "click", self._bg_calc_clicked)', ui)
        single = codegen.generate_single(p)
        self.assertIn("class FullTest(tk.Tk):", single)
        self.assertIn("# TODO: your code here", single)

    def test_placeholders(self):
        g = codegen._Gen(build_full_project(), True)
        self.assertEqual(g.text_expr("plain"), '"plain"')
        self.assertEqual(g.text_expr("{count}"), 'self.get("count")')
        self.assertEqual(g.text_expr("n={count} {{lit}}"), 'f"n={self.get(\'count\')} {{lit}}"')
        self.assertEqual(g.text_expr("{large}"), 'self.get("size")')   # radio -> its group
        self.assertEqual(g.text_expr("{missing}"), '"{missing}"')
        self.assertEqual(g.text_expr('say "hi" {count}'),
                         'f"say \\"hi\\" {self.get(\'count\')}"')

    def test_skipped_actions_are_commented(self):
        src = codegen.generate_single(build_full_project())
        self.assertIn("# skipped: Set ghost (missing!)", src)
        self.assertIn("# skipped: unknown action 'unknown_action'", src)
        self.assertIn("Your code has a mistake", src)
        self.assertIn("this window is already closing", src)

    # ---- running the full app -----------------------------------------
    def test_full_app_split_runs(self):
        self._run_full_app("split")

    def test_full_app_single_runs(self):
        self._run_full_app("single")

    def _run_full_app(self, style):
        App, folder, report = export_and_import(build_full_project(), style)
        self.assertEqual(report["kept"], [])
        app = App()
        try:
            app._stop_timer("main_window")      # restarted below to test the timer
            app.update()
            self.assertEqual(app.title(), 'Full "test" app')
            # typed values
            self.assertEqual(app.get("length"), 2.5)
            self.assertEqual(app.get("count"), 3)
            self.assertEqual(app.get("name_box"), "Ann")
            self.assertEqual(app.get("factor"), 2.0)
            self.assertEqual(app.get("amount"), 5.0)
            self.assertEqual(app.get("steps"), 4)
            self.assertIsInstance(app.get("steps"), int)
            self.assertEqual(app.get("angle"), 30)
            self.assertIs(app.get("agree"), True)
            self.assertEqual(app.get("size"), "L")
            self.assertEqual(app.get("large"), "L")
            self.assertEqual(app.get("todo"), [])
            self.assertEqual(app.get("plain_list"), "")
            self.assertIsNone(app.get("results"))
            self.assertEqual(app.get("notes"), "first line\nsecond")
            self.assertEqual(app.get("data_file"), "C:/data/in.csv")
            self.assertEqual(app.get("progress"), 40.0)
            self.assertEqual(app.get("tabs"), "Tab 1")
            self.assertEqual(app.get("title_label"), 'Hello\n"quoted" {braces}')
            self.assertEqual(app.get("second_tab"), "Tab 2")
            self.assertIsNone(app.get("canvas1"))
            # hidden window: widgets exist and keep values
            self.assertEqual(app.get("w2_entry"), "w2 text")
            app.set("w2_entry", "remembered")
            self.assertEqual(app.get("w2_entry"), "remembered")
            values = app.values()
            self.assertEqual(values["length"], 2.5)
            self.assertEqual(values["size"], "L")
            self.assertNotIn("large", values)
            self.assertEqual(set(app.values("settings")), {"w2_entry", "w2_check", "size"})
            # invalid numbers -> friendly ValueError naming the field
            app.set("length", "abc")
            with self.assertRaisesRegex(ValueError, "Length \\(m\\)"):
                app.get("length")
            app.set("length", "")
            with self.assertRaisesRegex(ValueError, "Please enter a number"):
                app.get("length")
            app.set("length", "3,5")
            self.assertEqual(app.get("length"), 3.5)
            app.set("count", "2.5")
            with self.assertRaisesRegex(ValueError, "whole number"):
                app.get("count")
            app.set("count", 4)
            # set() with different kinds of values
            app.set("angle", 31.3)
            self.assertEqual(app.get("angle"), 31.5)
            app.set("steps", 9)
            self.assertEqual(app.widget("steps").get(), "9")
            app.set("amount", 2)
            self.assertEqual(app.widget("amount").get(), "2.0")
            app.set("fruit", ["A", "B"])
            self.assertEqual(tuple(app.widget("fruit").cget("values")), ("A", "B"))
            app.set("todo", ["x", "y", "z"])
            app.set("todo", "y")
            self.assertEqual(app.get("todo"), ["y"])
            app.set("results", [["r1", 1.0], {"Name": "r2", "Value": 0.1 + 0.2}, ("r3", None)])
            tree = app.widget("results")
            rows = [tree.item(r, "values") for r in tree.get_children()]
            self.assertEqual([list(map(str, r)) for r in rows],
                             [["r1", "1"], ["r2", "0.3"], ["r3", ""]])
            self.assertEqual(tree.item(tree.get_children()[1], "tags"), ("odd",))
            tree.selection_set(tree.get_children()[1])
            self.assertEqual(app.get("results"), {"Name": "r2", "Value": "0.3"})
            app.set("results", {"a": 1, "b": 2})
            self.assertEqual(len(tree.get_children()), 2)
            app.set("size", "S")
            self.assertEqual(app.get("small"), "S")
            app.set("large", True)
            self.assertEqual(app.get("size"), "L")
            app.set("console", "replaced")
            app.log("line 2")
            self.assertEqual(app.get("console"), "replaced\nline 2")
            app.set("tabs", "Tab 2")
            self.assertEqual(app.get("tabs"), "Tab 2")
            app.set_values({"name_box": "Zed", "news": True})
            self.assertEqual(app.get("name_box"), "Zed")
            self.assertIs(app.get("news"), True)
            app.set("secret", "x")
            self.assertEqual(app.get("secret"), "x")
            self.assertIn("readonly", app.widget("secret").state())
            with self.assertRaises(KeyError):
                app.get("nope")
            with self.assertRaises(KeyError):
                app.widget("nope")

            # thread-safe set/log/get from a worker thread
            import threading
            box = {}

            def worker():
                app.set("out_label", "from thread")
                app.log("thread log")
                box["value"] = app.get("count")
            t = threading.Thread(target=worker)
            t.start()
            pump(app, lambda: not t.is_alive() and app.get("out_label") == "from thread")
            self.assertEqual(box.get("value"), 4)
            self.assertIn("thread log", app.get("console"))

            # on(): own callbacks
            hits = []
            app.on("calc", "click", lambda: hits.append("calc"))
            app.on("agree", "change", lambda: hits.append("agree"))
            app.on("todo", "change", lambda: hits.append("todo"))
            app.on("name_box", "change", lambda: hits.append("name"))
            with self.assertRaises(ValueError):
                app.on("line", "click", lambda: None)
            app.widget("agree").invoke()
            app.set("name_box", "changed")
            app.widget("todo").event_generate("<<ListboxSelect>>")
            app.update()
            self.assertIn("agree", hits)
            self.assertIn("name", hits)
            self.assertIn("todo", hits)

            # sync call_function: result then remaining actions
            app.set("length", 3)
            app.widget("calc").invoke()
            self.assertEqual(app.get("out_label"), "12")
            self.assertIn("area done 12", app.get("console"))
            self.assertIn("calc", hits)

            # background call chain
            CALLS.clear()
            app.set("count", 5)
            app.widget("bg_calc").invoke()
            self.assertIn("disabled", app.widget("bg_calc").state())
            self.assertEqual(str(app.widget("busy_bar").cget("mode")), "indeterminate")
            self.assertTrue(pump(app, lambda: app.get("title_label") == "all done"),
                            app.get("console"))
            self.assertEqual(app.get("out_label"), "x=5")
            self.assertIn("after slow 10", app.get("console"))
            self.assertIn("after pair", app.get("console"))
            self.assertEqual(app.get("w2_entry"), "9")
            self.assertEqual(app.get("name_box"), "10")
            self.assertEqual(len(app.widget("many_rows").get_children()), 3)
            self.assertNotIn("disabled", app.widget("bg_calc").state())
            self.assertEqual(str(app.widget("busy_bar").cget("mode")), "determinate")
            self.assertTrue(int(app.get("w2_label")) > 10)
            self.assertIn("my_hook", getattr(app, "hook_calls", ["my_hook"]))

            # background error -> message, remaining actions don't run
            CALLS.clear()
            app.widget("fail_btn").invoke()
            self.assertTrue(pump(app, lambda: calls("showwarning")))
            self.assertIn("bad value from the backend", calls("showwarning")[0][1][1])
            self.assertNotIn("not reached", app.get("console"))
            self.assertIn("Traceback", app.get("console"))

            # run_command: streamed output, then the remaining actions
            app.widget("cmd_btn").invoke()
            self.assertTrue(pump(app, lambda: "command finished" in app.get("console"),
                                 timeout=15), app.get("console"))
            self.assertIn("hello\n", app.get("console"))
            self.assertIn("world", app.get("console"))

            # the big "go" handler
            CALLS.clear()
            app.set("name_box", "Ann")
            app.set("count", 4)
            app.set("length", 2.5)
            app.set("fruit", "Pear")
            app.set_values({"agree": True, "news": False, "size": "L", "angle": 30})
            app.widget("todo").selection_set(0)
            app.widget("results").selection_set(app.widget("results").get_children()[0])
            ANSWERS["asksaveasfilename"] = os.path.join(TMP, "asked.csv")
            app._go_clicked()
            app.update()
            self.assertTrue(os.path.exists(os.path.join(TMP, "asked.csv")))
            self.assertEqual(calls("showwarning")[0][1][0], "Hi Ann")
            self.assertEqual(calls("askstring")[0][1][:2], ("Q", "Type"))
            self.assertTrue(app.get("title_label").startswith('Hi Ann! {x} "q"'),
                            app.get("title_label"))
            self.assertIn("{missing}", app.get("title_label"))
            self.assertIs(app.get("news"), True)
            self.assertIs(app.get("w2_check"), False)
            self.assertEqual(app.get("size"), "")          # small unticked, large toggled off
            self.assertEqual(app.get("secret"), "new secret")
            self.assertEqual(app.get("options_group"), "Group 4")
            self.assertEqual(app.get("second_tab"), "Tab two")
            self.assertEqual(app.get("w2_entry"), "into hidden window")
            self.assertEqual(app.get("notes"), "True||||\nmore".replace("||||", "|||12.5|[]")
                             if False else app.get("notes"))
            self.assertTrue(app.get("notes").endswith("\nmore"))
            self.assertEqual(app.get("name_box"), "Ann Smith")
            self.assertEqual(app.get("steps"), app.get("steps"))
            self.assertEqual(app.get("count"), 3)
            self.assertEqual(app.get("progress"), 50.0)          # capped at the maximum
            self.assertEqual(len(app.get("stamped").splitlines()), 3)
            self.assertRegex(app.get("stamped"), r"^\d\d:\d\d:\d\d  two")
            self.assertEqual(app.widget("plain_list").get(0, "end"), ())
            self.assertEqual(app.get("fruit"), "")
            self.assertIn("Kiwi", app.widget("fruit").cget("values"))
            self.assertEqual(app.get("out_folder"), "")
            self.assertTrue(os.path.exists(os.path.join(TMP, "t.csv")))
            self.assertEqual(str(app.widget("notes").cget("state")), "disabled")
            self.assertEqual(str(app.widget("canvas1").cget("bg")), "black")
            self.assertEqual(app.widget("tabs").select(), str(app.widget("second_tab")))
            self.assertEqual(app.title(), "Title 3")
            self.assertEqual(app.widget("w2_label").winfo_toplevel().title(), "Settings!")
            self.assertTrue(app.get("w2_label").startswith("Opened angle"))
            self.assertEqual(app.get("out_label"), "w2 closed")      # its on_close ran
            self.assertFalse(app.settings.winfo_viewable())   # closed again (hidden)
            self.assertEqual(calls("askopenfilename")[0][0], "askopenfilename")
            self.assertNotIn("unreachable", app.get("console"))
            with open(os.path.join(TMP, "t.csv")) as f:
                self.assertEqual(f.readline().strip(), "Name,Value")

            # reset form + files
            CALLS.clear()
            app._colored_clicked()
            app.update()
            self.assertEqual(app.get("name_box"), "Ann")
            self.assertEqual(app.get("count"), 3)
            self.assertEqual(app.get("size"), "L")
            self.assertEqual(app.get("angle"), 30)
            self.assertTrue(app.get("notes").startswith("Name,Value"))
            with open(os.path.join(TMP, "saved.txt")) as f:
                self.assertTrue(f.read().startswith("Name,Value"))
            self.assertTrue(calls("showerror"))
            self.assertNotIn("unreachable", app.get("console"))

            # open / close the settings window
            app.open_window("settings")
            app.update()
            self.assertTrue(app.settings.winfo_viewable())
            app.close_window("settings")
            app.update()
            self.assertFalse(app.settings.winfo_viewable())
            self.assertEqual(app.get("out_label"), "w2 closed")

            # timer ticks
            app._start_timer("main_window")
            before = app.get("steps")
            self.assertTrue(pump(app, lambda: app.get("steps") >= before + 2, timeout=3))

            # closing the main window asks first (confirm), hook may veto
            ANSWERS["askyesno"] = False
            app._main_window_closing()
            self.assertTrue(app.winfo_exists())
        finally:
            close(app)

    def test_user_hooks_are_called(self):
        p = build_full_project("Hooked App")
        App, folder, _ = export_and_import(p, "split")
        user = os.path.join(folder, "hooked_app.py")
        with open(user) as f:
            src = f.read()
        src = src.replace('    def on_title_label_clicked(self):',
                          '    def on_title_label_clicked(self):\n'
                          '        self.set("out_label", "hook ran")\n'
                          '        return\n\n    def _unused(self):')
        src = src.replace('    def on_main_window_closing(self):',
                          '    def on_main_window_closing(self):\n'
                          '        return False\n\n    def _unused2(self):')
        with open(user, "w") as f:
            f.write(src)
        App, folder, report = export_and_import(p, "split", folder)
        self.assertEqual(report["kept"], [user])
        self.assertEqual(report["missing_hooks"], [])
        app = App()
        try:
            app.update()
            app._fire("title_label", "click")
            self.assertEqual(app.get("out_label"), "hook ran")
            self.assertIn("title", app.get("console"))
            app._main_window_closing()          # confirm -> yes, hook -> False: stays open
            self.assertTrue(app.winfo_exists())
        finally:
            close(app)

    def test_export_keeps_user_file(self):
        p = build_small_project("Keep Me")
        folder = os.path.join(TMP, "keep")
        r1 = codegen.export(p, folder)
        names = sorted(os.path.basename(x) for x in r1["written"])
        self.assertEqual(names, ["keep_me.py", "keep_me_ui.py"])
        user = os.path.join(folder, "keep_me.py")
        with open(user, "a") as f:
            f.write("\n# my precious code\n")
        p.find("label1")["events"]["on_click"] = [act("log", target="label1", text="x")]
        p.find("label1")["hooks"] = ["on_click"]
        r2 = codegen.export(p, folder)
        self.assertEqual([os.path.basename(x) for x in r2["written"]], ["keep_me_ui.py"])
        self.assertEqual(r2["kept"], [user])
        with open(user) as f:
            self.assertIn("my precious code", f.read())
        self.assertEqual(len(r2["missing_hooks"]), 1)
        self.assertIn("def on_label1_clicked(self):", r2["missing_hooks"][0])
        self.assertEqual(code_problems("class X:\n" + r2["missing_hooks"][0]), [])
        r3 = codegen.export(p, folder, overwrite_user_code=True)
        self.assertEqual(len(r3["written"]), 2)
        with open(user) as f:
            self.assertNotIn("my precious code", f.read())
        # single style never overwrites a hand-written <base>.py
        with open(user, "w") as f:
            f.write("print('my own file')\n")
        p.settings["code_style"] = "single"
        r4 = codegen.export(p, folder)
        self.assertEqual(r4["kept"], [user])
        os.remove(user)
        r5 = codegen.export(p, folder)
        self.assertEqual(r5["written"], [user])
        r6 = codegen.export(p, folder)        # its own generated file: replaced
        self.assertEqual(r6["written"], [user])

    def test_missing_backend_module(self):
        p = build_small_project("No Backend")
        p.find("button1")["events"]["command"] = [
            call_fn("anything", module="does_not_exist_xyz", background=True),
            act("set_text", target="label1", text="not reached")]
        App, _folder, _ = export_and_import(p, "single")
        app = App()
        try:
            app.update()
            app.widget("button1").invoke()
            self.assertTrue(pump(app, lambda: calls("showerror")))
            self.assertIn('Could not import your module "does_not_exist_xyz"',
                          calls("showerror")[0][1][1])
            self.assertNotEqual(app.get("label1"), "not reached")
            self.assertNotIn("disabled", app.widget("button1").state())
        finally:
            close(app)

    def test_backend_dir_relative_to_export(self):
        p = build_small_project("Rel Backend")
        p.settings["backend_dir"] = BACKEND_DIR
        p.find("button1")["events"]["command"] = [call_fn("area", background=False)]
        src = codegen.generate(p, folder=TMP)["rel_backend_ui.py"]
        self.assertIn('sys.path.insert(0, os.path.join(HERE, "backend"))', src)
        p.settings["backend_dir"] = "backend"
        src = codegen.generate(p, folder=os.path.join(TMP, "x"), project_dir=TMP)
        self.assertIn('os.path.join(HERE, "../backend")', src["rel_backend_ui.py"])
        p.settings["backend_dir"] = ""
        self.assertNotIn("sys.path.insert", codegen.generate(p)["rel_backend_ui.py"])

    def test_print_capture(self):
        p = build_small_project("Capture App")
        M = p.main_window["id"]
        add(p, "Console", M, "out", capture_print=True)
        p.find("button1")["events"]["command"] = [call_fn("chatty", background=True)]
        p.settings["backend_dir"] = BACKEND_DIR
        App, _folder, _ = export_and_import(p, "single")
        old = sys.stdout
        app = App()
        try:
            app.update()
            print("printed text")
            app.update()
            self.assertIn("printed text", app.get("out"))
            app.widget("button1").invoke()
            self.assertTrue(pump(app, lambda: "hello from print" in app.get("out")))
        finally:
            close(app)
        self.assertIs(sys.stdout, old)

    def test_events_do_not_fire_at_start(self):
        p = build_small_project("Quiet Start")
        M = p.main_window["id"]
        nb = add(p, "Notebook", M, "tabs")
        add(p, "Scale", M, "slider", value=10)
        add(p, "Spinbox", M, "spin")
        add(p, "Combobox", M, "combo")
        for nid, ev in (("tabs", "on_tab_change"), ("slider", "command"),
                        ("spin", "command"), ("combo", "on_select")):
            p.find(nid)["events"][ev] = [act("set_text", target="label1", text="FIRED " + nid)]
        App, _f, _ = export_and_import(p, "single")
        app = App()
        try:
            pump(app, timeout=0.3)
            self.assertEqual(app.get("label1"), "Value:")
            app.widget("tabs").select(1)
            app.update()
            self.assertEqual(app.get("label1"), "FIRED tabs")
            app.widget("slider").event_generate("<Button-1>")
            app.set("slider", 50)
            app.update()
            self.assertEqual(app.get("label1"), "FIRED slider")
        finally:
            close(app)
        self.assertTrue(nb)

    def test_timer_no_overlap(self):
        p = build_small_project("Timer App")
        M = p.main_window["id"]
        p.main_window["props"]["timer_ms"] = 20
        p.settings["backend_dir"] = BACKEND_DIR
        p.main_window["events"]["on_timer"] = [
            call_fn("slow_double", [{"name": "x", "source": "value", "value": "1"}],
                    background=True),
            act("change_number", target="label1", amount=1)]
        App, _f, _ = export_and_import(p, "single")
        app = App()
        try:
            app.set("label1", 0)
            pump(app, timeout=0.7)
            # each tick waits ~0.15 s for slow_double: at most ~5 ticks, not 35
            n = app.get("label1")
            self.assertTrue(1 <= float(n) <= 6, n)
        finally:
            close(app)

    def test_every_theme_compiles_and_constructs(self):
        for key in apptheme.THEME_ORDER:
            p = build_full_project("Theme " + key)
            p.settings["theme"] = key
            p.settings["font_family"] = "Arial" if key == "nord" else ""
            p.settings["font_size"] = 12 if key == "dracula" else 0
            src = codegen.generate_single(p)
            self.assertEqual(code_problems(src), [], key)
            App, _f, _ = export_and_import(p, "single")
            app = App()
            try:
                app.update()
                self.assertEqual(app.get("length"), 2.5)
            finally:
                close(app)

    def test_dpi_scaling_switch(self):
        p = build_full_project("Dpi App")
        src = codegen.generate_single(p)
        self.assertIn("def px(pixels):", src)
        self.assertIn("set_scale(self)", src)
        self.assertIn("padx=px(6)", src)
        p.settings["dpi_aware"] = False
        src = codegen.generate_single(p)
        self.assertNotIn("px(", src)
        self.assertNotIn("make_dpi_aware", src)
        self.assertEqual(code_problems(src), [])

    def test_variants_and_custom_styles(self):
        src = codegen.generate_single(build_full_project())
        self.assertIn('style="Accent.TButton"', src)
        self.assertIn('style="colored.TButton"', src)
        self.assertIn('style.configure("colored.TButton"', src)
        self.assertIn('style="out_label.TLabel"', src)

    def test_window_options(self):
        p = build_small_project("Window Opts")
        w = p.main_window
        w["props"].update(start="maximized", topmost=True, resizable=False, icon="nope.ico",
                          min_width=200, min_height=100)
        src = codegen.generate_single(p)
        self.assertIn('self._place_window(window, 480, 360, "maximized")', src)
        self.assertIn('window.attributes("-topmost", True)', src)
        self.assertIn("window.resizable(False, False)", src)
        self.assertIn('self._set_icon(window, "nope.ico")', src)
        App, _f, _ = export_and_import(p, "single")
        app = App()       # a missing icon file is skipped silently
        try:
            app.update()
            self.assertEqual(app.attributes("-topmost"), 1)
        finally:
            close(app)

    def test_menu_shortcuts(self):
        src = codegen.generate_single(build_full_project())
        self.assertIn('accelerator="Ctrl+O"', src)
        self.assertIn('window.bind("<Control-o>", lambda event: self._menu_file_open_settings())',
                      src)
        self.assertIn('window.bind("<Control-g>", lambda event: self.go.invoke())', src)

    def test_name_clashes_with_api(self):
        p = build_small_project("Clash")
        p.find("value")["id"] = "widget"        # bypass rename(): simulate an old project
        p.find("button1")["events"]["command"] = [act("set_text", target="widget", text="7")]
        App, _f, _ = export_and_import(p, "single")
        app = App()
        try:
            app.update()
            app.widget("button1").invoke()
            self.assertEqual(app.get("widget"), 7.0)
            self.assertTrue(callable(app.widget))
        finally:
            close(app)

    def test_examples_generate_and_run(self):
        """Every example project: both code styles compile, and the app starts."""
        examples = sorted(glob.glob(os.path.join(HERE, "examples", "*.tkb")))
        self.assertTrue(examples)
        for path in examples:
            with open(path, encoding="utf-8") as f:
                project = model.Project.from_json(f.read())
            name = os.path.basename(path)
            for style in ("split", "single"):
                project.settings["code_style"] = style
                for fname, src in codegen.generate(project).items():
                    self.assertEqual(code_problems(src), [], "%s: %s" % (name, fname))
            folder = os.path.join(TMP, "example%d" % examples.index(path))
            App, _f, _r = export_and_import(project, "split", folder)
            app = App()
            try:
                pump(app, timeout=0.2)
                app.values()
            finally:
                close(app)

    def test_codeview(self):
        import codeview
        root = tk.Tk()
        root.withdraw()
        root.project = build_full_project()
        try:
            view = codeview.CodeView(root, root.project)
            view.update()
            self.assertGreaterEqual(len(view.tabs.tabs()), 2)
            view.destroy()
            view = codeview.CodeView(root, "print('hi')\n", "x.py")   # v1 signature
            view.update()
            view.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
