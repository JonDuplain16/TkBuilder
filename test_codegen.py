"""
Tests for codegen.py (and a smoke test for codeview.py).

Run with:  python test_codegen.py

Builds several projects with the model.Project API (every widget type,
every action type, menus, several windows, tabs, placeholders, radio
groups, fonts, colors, spans/fills), checks the generated code compiles,
then runs it: creates the App, opens the other windows, calls every event
handler (with message boxes and file dialogs replaced by quiet stubs) and
checks the results.  Short-lived windows will flash on screen.
"""

import ast
import os
import sys
import tempfile
import tkinter as tk
import traceback
import unittest
from tkinter import messagebox, filedialog, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import model          # noqa: E402
import codegen        # noqa: E402

TMP = tempfile.mkdtemp(prefix="tkb_codegen_test_")


# ---------------------------------------------------------------------------
# project building helpers
# ---------------------------------------------------------------------------

def act(atype, **params):
    a = model.new_action(atype)
    a["params"].update(params)
    return a


def add(project, wtype, container, row=None, col=None, mode="cell", node_id=None, **props):
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
    """A tiny real PNG so image loading can be tested."""
    path = os.path.join(TMP, "dot.png")
    if not os.path.exists(path):
        r = tk.Tk()
        r.withdraw()
        img = tk.PhotoImage(master=r, width=8, height=8)
        img.put("#ff0000", to=(0, 0, 8, 8))
        img.write(path, format="png")
        r.destroy()
    return path


def build_full_project():
    """Every widget type and every action type."""
    p = model.Project()
    main = p.main_window
    main["props"].update(title='Full "test" app', width=640, height=520, bg="#f0f0ff",
                         padding=6, resizable=True)
    M = main["id"]

    # --- main window widgets -------------------------------------------
    add(p, "Label", M, node_id="title_label", text="Hello\nworld \\ \"quoted\" {braces}",
        font={"family": "", "size": 14, "bold": True, "italic": False, "underline": False},
        fg="#123456", bg="yellow", justify="center", wraplength=200)
    name = add(p, "Entry", M, 0, 0, "right", node_id="name_box", text="Ann",
               font={"family": "Courier New", "size": 11, "bold": False, "italic": True,
                     "underline": True})
    add(p, "Entry", M, node_id="secret", password=True, state="disabled", text="pw")
    add(p, "Label", M, node_id="counter", text="0")
    add(p, "Label", M, node_id="pic", image=make_png(), text="ignored")
    add(p, "Button", M, node_id="pic_button", image=os.path.join(TMP, "missing.png"))
    btn = add(p, "Button", M, node_id="go", text="Go!", bg="#00aa00", fg="white", width=10)
    btn["layout"].update(colspan=2, fill="x")
    p.compact(main)
    add(p, "Button", M, node_id="disabled_btn", state="disabled")
    add(p, "Checkbutton", M, node_id="agree", checked=True, text="I agree")
    add(p, "Checkbutton", M, node_id="news", checked=False)
    add(p, "Radiobutton", M, node_id="small", text="Small", group="Size", selected=False)
    add(p, "Radiobutton", M, node_id="large", text="Large", group="Size", selected=True)
    add(p, "Radiobutton", M, node_id="red", text="Red", group="color choice!")
    add(p, "Radiobutton", M, node_id="blue", text="Blue", group="color choice!")
    add(p, "Combobox", M, node_id="fruit", items=["Apple", "Pear", 'Quote "q"'], text="Pear")
    add(p, "Combobox", M, node_id="free_combo", readonly=False, items=[], text="")
    lst = add(p, "Listbox", M, node_id="todo", items=["one", "two", "three"],
              selectmode="extended")
    lst["layout"].update(rowspan=2, fill="both")
    add(p, "Listbox", M, node_id="plain_list", scrollbar=False)
    add(p, "Spinbox", M, node_id="amount", value=5, from_=0, to=10, increment=0.5)
    add(p, "Spinbox", M, node_id="locked_spin", state="disabled")
    add(p, "Scale", M, node_id="volume", value=30, label="Volume", showvalue=False,
        resolution=0.5, orient="vertical")
    add(p, "Progressbar", M, node_id="progress", value=40, maximum=50)
    add(p, "Separator", M, node_id="line")
    add(p, "Canvas", M, node_id="canvas1", width=60, height=40)
    notes = add(p, "Text", M, node_id="notes", text="first line\nsecond", width=20, height=3)
    notes["layout"].update(fill="both", halign="left")
    add(p, "Text", M, node_id="readonly_text", readonly=True, scrollbar=False, text="RO",
        width=10, height=2)

    # containers
    panel = add(p, "Frame", M, node_id="panel", bg="#eeeeee", relief="groove", borderwidth=2)
    panel["layout"].update(fill="both", valign="top")
    add(p, "Label", "panel", node_id="panel_label", text="In panel")
    group = add(p, "LabelFrame", "panel", node_id="options_group", text="Options",
                font={"family": "", "size": 0, "bold": False, "italic": True, "underline": False})
    add(p, "Label", "options_group", node_id="deep_label", text="Deep")
    nb = add(p, "Notebook", M, node_id="tabs")
    tab1, tab2 = nb["children"]
    tab1["id"], tab2["id"] = "first_tab", "second_tab"
    add(p, "Label", "first_tab", node_id="tab_label", text="On tab 1")
    add(p, "Entry", "second_tab", node_id="tab_entry", text="tab text")
    assert group and panel

    # --- second window ----------------------------------------------------
    w2 = p.add_window("Settings")
    p.rename(w2["id"], "window2")
    W2 = w2["id"]
    w2["props"].update(resizable=False, bg="", padding=0)
    add(p, "Label", W2, node_id="w2_label", text="Settings label")
    add(p, "Entry", W2, node_id="w2_entry", text="w2 text")
    add(p, "Text", W2, node_id="w2_text", text="")
    add(p, "Listbox", W2, node_id="w2_list")
    add(p, "Checkbutton", W2, node_id="w2_check", checked=True)
    add(p, "Radiobutton", W2, node_id="w2_small", text="W2 small", group="Size")
    add(p, "Button", W2, node_id="w2_close", text="Close me")
    w2nb = add(p, "Notebook", W2, node_id="w2_tabs")
    w2nb["children"][0]["id"] = "w2_tab"
    w2["events"]["on_open"] = [act("set_text", target="w2_label", text="Opened at {counter}")]
    w2["events"]["on_close"] = [act("set_text", target="counter", text="w2 closed"),
                                act("close_window", window=W2)]       # skipped (already closing)
    p.find("w2_close")["events"]["command"] = [act("close_window", window=W2),
                                               act("set_text", target="w2_label", text="after")]
    w2["props"]["menu"] = [{"label": "Window", "items": [
        {"label": "Close", "actions": [act("close_window", window=W2)]}]}]

    # --- third window: never opened, empty -------------------------------
    w3 = p.add_window("Never opened")
    p.rename(w3["id"], "window3")
    W3 = w3["id"]

    # --- menus on the main window -----------------------------------------
    main["props"]["menu"] = [
        {"label": "File", "items": [
            {"label": "Open settings...", "actions": [act("open_window", window=W2)]},
            {"separator": True},
            {"label": "Quit", "actions": [act("quit_app")]},
            {"label": "Does nothing", "actions": []},
        ]},
        {"label": "Help", "items": [
            {"label": "About", "actions": [act("show_message", title="About",
                                                message="Made with \"TkBuilder\" \\o/")]}]},
        {"label": "Empty", "items": []},
    ]

    # --- every action, on the Go! button and others -----------------------
    go_actions = [
        act("show_message", title="Hi {name_box}", message="Line1\nLine2 {{not a placeholder}}",
            icon="warning"),
        act("show_message", icon="error", message="err"),
        act("show_message", icon="info", message="{name_box}"),
        act("confirm", title="Sure?", message="Really {name_box}?"),
        act("ask_text", title="Q", message="Type", target="title_label"),
        act("ask_text", target="w2_label"),
        act("check", target="name_box", condition="not_empty", message="Need a name"),
        act("check", target="agree", condition="checked", message=""),
        act("check", target="large", condition="checked", message="Pick large"),
        act("check", target="news", condition="unchecked"),
        act("check", target="small", condition="unchecked"),
        act("check", target="name_box", condition="equals", value="Ann"),
        act("check", target="name_box", condition="not_equals", value="Bob {counter}"),
        act("check", target="name_box", condition="contains", value="n"),
        act("check", target="amount", condition="number"),
        act("check", target="amount", condition="greater", value="1"),
        act("check", target="volume", condition="less", value="{amount}0"),
        act("check", target="counter", condition="number"),
        act("check", target="free_combo", condition="empty"),
        act("check", target="todo", condition="checked"),
        act("set_checked", target="news", state="tick"),
        act("set_checked", target="w2_check", state="toggle"),       # window may be closed
        act("set_checked", target="w2_small", state="untick"),
        act("set_text", target="title_label",
            text='Hello {name_box}! {{x}} "q" \\back\nnew {missing_widget} {fruit}'),
        act("set_text", target="name_box", text="{fruit}"),
        act("set_text", target="secret", text="new secret"),
        act("set_text", target="notes", text="{agree}|{large}|{volume}|{progress}|{todo}"),
        act("set_text", target="readonly_text", text="changed"),
        act("set_text", target="fruit", text="Apple"),
        act("set_text", target="amount", text="7"),
        act("set_text", target="volume", text="12"),
        act("set_text", target="progress", text="25"),
        act("set_text", target="todo", text="two"),
        act("set_text", target="agree", text="Agree?"),
        act("set_text", target="options_group", text="Group {notes}"),
        act("set_text", target="w2_entry", text="into closed window {w2_entry}"),
        act("append_text", target="notes", text="more", newline="yes"),
        act("append_text", target="name_box", text="Smith", newline="yes"),
        act("append_text", target="counter", text="x", newline="no"),
        act("change_number", target="counter", amount=1),     # "0x" -> not a number -> 1
        act("change_number", target="counter", amount=1.5),
        act("change_number", target="amount", amount=-1),
        act("change_number", target="volume", amount=2),
        act("change_number", target="progress", amount=100),
        act("change_number", target="locked_spin", amount=1),
        act("clear", target="free_combo"),
        act("clear", target="plain_list"),
        act("add_item", target="todo", text="four {name_box}"),
        act("add_item", target="free_combo", text="new item"),
        act("remove_selected", target="todo"),
        act("hide_widget", target="notes"),
        act("show_widget", target="notes"),
        act("hide_widget", target="canvas1"),
        act("hide_widget", target="second_tab"),
        act("show_widget", target="second_tab"),
        act("enable_widget", target="secret"),
        act("disable_widget", target="fruit"),
        act("enable_widget", target="fruit"),
        act("disable_widget", target="w2_entry"),
        act("set_color", target="title_label", fg="red", bg="#000000"),
        act("set_color", target="panel", fg="red", bg="#ffeeee"),
        act("set_color", target="canvas1", fg="", bg="pink"),
        act("set_color", target="go", fg="", bg=""),                # skipped: no colours
        act("switch_tab", tab="second_tab"),
        act("switch_tab", tab="w2_tab"),
        act("set_title", window=M, text="Title {name_box}"),
        act("set_title", window=W2, text="W2 title"),
        act("choose_file", mode="open", target="name_box"),
        act("choose_file", mode="save", target="notes"),
        act("choose_file", mode="folder", target="title_label"),
        act("save_file", path=os.path.join(TMP, "out_{counter}.txt"), source="notes"),
        act("save_file", path=os.path.join(TMP, "list.txt"), source="todo"),
        act("load_file", path=os.path.join(TMP, "list.txt"), target="readonly_text"),
        act("load_file", path="", target="counter"),                 # skipped
        act("open_window", window=W3),
        act("close_window", window=W3),
        act("open_window", window=W3),
        act("open_window", window=M),
        act("run_code", code="    x = 1\n    if x:\n        self.title('from code')\n"),
        act("run_code", code=""),
        act("run_code", code="this is not python ("),
        act("set_text", target="no_such_widget", text="x"),          # skipped
        act("set_text", target="", text="x"),                         # skipped
        act("open_window", window="no_such_window"),                  # skipped
        act("switch_tab", tab="nope"),                                # skipped
        # a failing load shows an error and stops the remaining actions
        act("load_file", path=os.path.join(TMP, "does_not_exist.txt"), target="counter"),
        act("set_text", target="counter", text="not reached"),
    ]
    btn["events"]["command"] = go_actions

    # other events
    p.find("agree")["events"]["command"] = [act("set_text", target="counter", text="toggled {agree}")]
    p.find("small")["events"]["command"] = [act("set_text", target="counter", text="radio {small}")]
    p.find("fruit")["events"]["on_select"] = [act("set_text", target="counter", text="{fruit}")]
    p.find("todo")["events"]["on_select"] = [act("set_text", target="counter", text="{todo}")]
    p.find("amount")["events"]["command"] = [act("set_text", target="counter", text="{amount}")]
    p.find("volume")["events"]["command"] = [act("set_text", target="counter", text="{volume}")]
    p.find("name_box")["events"]["on_enter"] = [act("set_text", target="counter", text="enter")]
    p.find("tabs")["events"]["on_tab_change"] = [act("set_text", target="counter", text="tab")]
    p.find("canvas1")["events"]["on_click"] = [act("set_text", target="counter", text="canvas")]
    main["events"]["on_open"] = [act("set_text", target="counter", text="{counter}")]
    main["events"]["on_close"] = [act("confirm", message="Quit?")]
    return p


def build_edge_project():
    """Name clashes, empty containers, a quitting button, only defaults."""
    p = model.Project()
    M = p.main_window["id"]
    p.main_window["props"].update(padding=0, title="")
    # widget ids that clash with generated method names
    w2 = p.add_window()
    p.rename(w2["id"], "window2")
    add(p, "Button", M, node_id="open_" + w2["id"])
    add(p, "Button", M, node_id="go_clicked")
    b = add(p, "Button", M, node_id="go")
    b["events"]["command"] = [act("open_window", window=w2["id"]),
                              act("set_text", target="go_clicked", text="clicked")]
    add(p, "Checkbutton", M, node_id="box")
    add(p, "Label", M, node_id="box_var")       # clashes with the checkbox variable name
    add(p, "Frame", M, node_id="empty_panel")
    add(p, "LabelFrame", M, node_id="empty_group")
    q = add(p, "Button", M, node_id="quitter")
    q["events"]["command"] = [act("quit_app"), act("set_text", target="box_var", text="never")]
    c = add(p, "Button", M, node_id="closer")
    c["events"]["command"] = [act("close_window", window=M)]
    return p


def build_two_window_project():
    """A small realistic app: a form in the main window, a log window."""
    p = model.Project()
    M = p.main_window["id"]
    p.main_window["props"].update(title="Greeter", width=360, height=220)
    add(p, "Label", M, node_id="prompt", text="Your name:")
    add(p, "Entry", M, 0, 0, "right", node_id="name")
    greet = add(p, "Button", M, node_id="greet", text="Say hello")
    greet["layout"].update(colspan=2, fill="x")
    add(p, "Label", M, node_id="answer", text="")
    log = p.add_window("Log")
    p.rename(log["id"], "window2")
    add(p, "Text", log["id"], node_id="log_text")
    greet["events"]["command"] = [
        act("check", target="name", condition="not_empty", message="Please type your name."),
        act("set_text", target="answer", text="Hello, {name}!"),
        act("open_window", window=log["id"]),
        act("append_text", target="log_text", text="Greeted {name}"),
    ]
    return p


# ---------------------------------------------------------------------------
# running generated code
# ---------------------------------------------------------------------------

class Stubs:
    """Replace blocking dialogs with recorders."""

    def __init__(self):
        self.calls = []
        self.yes = True
        self.answer = "typed answer"
        self.path = os.path.join(TMP, "chosen.txt")
        self._saved = []

    def __enter__(self):
        def rec(name, result):
            def f(*a, **k):
                self.calls.append((name, a, k))
                return result() if callable(result) else result
            return f
        patches = [
            (messagebox, "showinfo", None), (messagebox, "showwarning", None),
            (messagebox, "showerror", None),
            (messagebox, "askyesno", lambda: self.yes),
            (simpledialog, "askstring", lambda: self.answer),
            (filedialog, "askopenfilename", lambda: self.path),
            (filedialog, "asksaveasfilename", lambda: self.path),
            (filedialog, "askdirectory", lambda: TMP),
        ]
        for mod, name, result in patches:
            self._saved.append((mod, name, getattr(mod, name)))
            setattr(mod, name, rec(name, result))
        return self

    def __exit__(self, *exc):
        for mod, name, orig in self._saved:
            setattr(mod, name, orig)

    def names(self):
        return [c[0] for c in self.calls]


def load(code, path=None):
    """exec the generated code; returns its namespace."""
    ns = {"__name__": "generated_app", "__file__": path or os.path.join(TMP, "app.py")}
    exec(compile(code, "<generated>", "exec"), ns)
    return ns


def make_app(ns, errors):
    app = ns["App"]()
    app.geometry("+40+40")

    def report(exc, val, tb):
        errors.append("".join(traceback.format_exception(exc, val, tb)))
    app.report_callback_exception = report
    app.update()
    return app


def destroy(app):
    try:
        if app.winfo_exists():
            app.destroy()
    except tk.TclError:
        pass


def handler_names(ns):
    cls = ns["App"]
    return [n for n, v in vars(cls).items()
            if callable(v) and not n.startswith("_") and not n.startswith("open_")]


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

class CodegenTests(unittest.TestCase):

    def gen(self, project):
        code = codegen.generate(project)
        compile(code, "<generated>", "exec")
        return code

    def assert_no_unused_imports(self, code):
        tree = ast.parse(code)
        imported = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported += [(a.asname or a.name).split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported += [a.asname or a.name for a in node.names]
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        for name in imported:
            self.assertIn(name, used, "unused import %s" % name)

    # ---- static checks ---------------------------------------------------
    def test_all_projects_compile(self):
        for build in (build_full_project, build_edge_project, build_two_window_project,
                      model.Project):
            code = self.gen(build())
            self.assert_no_unused_imports(code)
            for line in code.splitlines():
                self.assertNotIn("DEFAULT_FONT", line)
                self.assertFalse(line.endswith(" "), "trailing space: %r" % line)

    def test_every_action_and_widget_is_covered(self):
        p = build_full_project()
        used_actions = {a["type"] for n in p.walk() for lst in p.iter_action_lists(n) for a in lst}
        self.assertEqual(used_actions, set(model.ACTIONS))
        self.assertEqual({n["type"] for n in p.walk()}, set(model.WIDGETS))

    def test_structure(self):
        code = self.gen(build_full_project())
        self.assertIn("class App(tk.Tk):", code)
        self.assertIn("def open_window2(self):", code)
        self.assertIn("def go_clicked(self, event=None):", code)
        self.assertIn("self.default_family = tkfont.nametofont", code)
        self.assertIn("(self.default_family, 14, \"bold\")", code)
        self.assertIn("# skipped:", code)
        self.assertIn("Your code has a mistake", code)
        self.assertIn('self.protocol("WM_DELETE_WINDOW", self.main_window_closing)', code)
        self.assertIn('if __name__ == "__main__":', code)
        # the empty default project has no helpers and no extra imports
        small = self.gen(model.Project())
        self.assertNotRegex(small, r"def _[a-z]")
        self.assertNotIn("from tkinter import", small)

    def test_placeholder_expressions(self):
        g = codegen._Generator(build_full_project())
        self.assertEqual(g.text_expr("plain"), '"plain"')
        self.assertEqual(g.text_expr("{{x}}"), '"{x}"')
        self.assertEqual(g.text_expr("{name_box}"), 'self._get("name_box")')
        self.assertEqual(g.text_expr("{missing}"), '"{missing}"')
        e = g.text_expr('a {name_box} "b" \\ {{c}}\n')
        self.assertEqual(eval(e, {"self": type("S", (), {"_get": lambda s, n: "<%s>" % n})()}),
                         'a <name_box> "b" \\ {c}\n')

    # ---- running the generated programs ----------------------------------
    def test_full_project_runs(self):
        code = self.gen(build_full_project())
        ns = load(code)
        handlers = handler_names(ns)
        self.assertIn("go_clicked", handlers)
        errors = []
        with Stubs() as stubs:
            app = make_app(ns, errors)
            try:
                # starting state
                self.assertEqual(app.name_box.get(), "Ann")
                self.assertEqual(app.secret.cget("state"), "disabled")
                self.assertEqual(app.fruit.get(), "Pear")
                self.assertEqual(app.amount.get(), "5")
                self.assertEqual(app.volume.get(), 30)
                self.assertTrue(app.agree_var.get())
                self.assertEqual(app.size_var.get(), "Large")
                self.assertEqual(app.notes.get("1.0", "end-1c"), "first line\nsecond")
                self.assertEqual(app.readonly_text.cget("state"), "disabled")
                self.assertEqual(app.todo.get(0, "end"), ("one", "two", "three"))
                self.assertIsNotNone(app.pic_image)          # real png loaded
                self.assertIsNone(app.pic_button_image)      # missing file -> None
                self.assertEqual(app.tabs.index("end"), 2)
                self.assertEqual(app.counter.cget("text"), "0")   # on_open ran ({counter})
                self.assertEqual(app.title(), 'Full "test" app')

                app.open_window2()
                app.update()
                self.assertTrue(app.window2.winfo_exists())
                self.assertEqual(app.w2_label.cget("text"), "Opened at 0")
                self.assertEqual(app.w2_entry.get(), "w2 text")
                app.open_window2()      # second call: just lifts
                app.todo.selection_set(0)
                app.go_clicked()
                app.update()
                self.assertEqual(stubs.names().count("askyesno"), 1)
                self.assertEqual(app.title(), "from code")
                self.assertEqual(app.fruit.get(), "Apple")
                self.assertTrue(app.window3.winfo_exists() is not None)
                # run every other handler (windows open)
                for name in handlers:
                    if name in ("menu_file_quit", "go_clicked", "main_window_closing"):
                        continue
                    getattr(app, name)()
                    app.update()
                # menus
                app.menu_help_about()
                self.assertEqual(stubs.calls[-1][0], "showinfo")
                self.assertIn('"TkBuilder"', stubs.calls[-1][1][1])
                # close the second window via its button: on_close actions run
                app.open_window2()
                app.w2_close_clicked()
                app.update()
                self.assertFalse(app._is_open(app.window2))
                self.assertEqual(app.counter.cget("text"), "w2 closed")
                # everything again with the other windows closed
                app.go_clicked()
                app.update()
                for name in handlers:
                    if name in ("menu_file_quit", "main_window_closing"):
                        continue
                    if name.startswith("window2"):
                        continue          # those only run while window2 is open
                    getattr(app, name)()
                    app.update()
                # closing the main window asks first; "No" keeps it open
                stubs.yes = False
                app.main_window_closing()
                self.assertTrue(app.winfo_exists())
                stubs.yes = True
                app.main_window_closing()
            finally:
                destroy(app)
        self.assertEqual(errors, [])

    def test_action_results(self):
        code = self.gen(build_full_project())
        ns = load(code)
        errors = []
        with Stubs() as stubs:
            app = make_app(ns, errors)
            try:
                # run the Go! handler step by step through its effects
                app.todo.selection_set(1)
                app.go_clicked()
                app.update()
                names = stubs.names()
                self.assertEqual(names[0], "showwarning")
                self.assertEqual(stubs.calls[0][1], ("Hi Ann", "Line1\nLine2 {not a placeholder}"))
                self.assertEqual(stubs.calls[2][0], "showinfo")
                self.assertEqual(stubs.calls[2][1][1], "Ann")
                self.assertIn("askstring", names)
                self.assertIn("askopenfilename", names)
                self.assertIn("asksaveasfilename", names)
                self.assertIn("askdirectory", names)
                # failed load_file shows an error
                self.assertIn("showerror", names)
                # save_file wrote the list items
                with open(os.path.join(TMP, "list.txt"), encoding="utf-8") as f:
                    saved = f.read()
                self.assertTrue(saved.startswith("one\n"), saved)
                # ... and load_file put that into the read-only area (still read-only)
                self.assertEqual(app.readonly_text.get("1.0", "end-1c"), saved)
                self.assertEqual(app.readonly_text.cget("state"), "disabled")
                self.assertEqual(app.title_label.cget("text"), TMP)   # choose folder
                self.assertEqual(app.title_label.cget("fg"), "red")
                self.assertEqual(app.name_box.get(), stubs.path)
                self.assertEqual(app.notes.get("1.0", "end-1c"), stubs.path)
                self.assertEqual(app.secret.cget("state"), "normal")
                self.assertEqual(app.secret.get(), "new secret")
                self.assertEqual(str(app.fruit.cget("state")), "readonly")
                self.assertEqual(app.free_combo.cget("values"), ("new item",))
                self.assertEqual(app.plain_list.size(), 0)
                self.assertEqual(app.amount.get(), "6")
                self.assertEqual(app.volume.get(), 14)
                self.assertEqual(float(app.progress.cget("value")), 50)
                self.assertEqual(app.locked_spin.get(), "1")
                self.assertEqual(app.agree.cget("text"), "Agree?")
                self.assertEqual(app.tabs.index("current"), 1)
                self.assertEqual(app.panel.cget("bg"), "#ffeeee")
                self.assertEqual(app.notes.master.winfo_manager(), "grid")   # shown again
                self.assertEqual(app.canvas1.winfo_manager(), "")            # hidden
                self.assertTrue(app.window3.winfo_exists())
                # window2 was closed: actions on its widgets were skipped quietly
                self.assertIsNone(app.window2)
            finally:
                destroy(app)
        self.assertEqual(errors, [])

    def test_set_text_details(self):
        p = build_full_project()
        p.find("go")["events"]["command"] = [
            act("set_text", target="title_label",
                text='Hello {name_box}! {{x}} "q" \\back\nnew {missing_widget}'),
            act("set_text", target="notes", text="{agree}|{large}|{small}|{volume}|{progress}|{todo}"),
            act("append_text", target="notes", text="more", newline="yes"),
            act("append_text", target="counter", text="x", newline="no"),
            act("change_number", target="counter", amount=1.5),
            act("change_number", target="counter", amount=1.5),
            act("set_text", target="options_group", text="{title_label}"),
        ]
        ns = load(self.gen(p))
        errors = []
        with Stubs():
            app = make_app(ns, errors)
            try:
                app.todo.selection_set(0, 2)
                app.go_clicked()
                self.assertEqual(app.title_label.cget("text"),
                                 'Hello Ann! {x} "q" \\back\nnew {missing_widget}')
                self.assertEqual(app.notes.get("1.0", "end-1c"),
                                 "Yes|Large|Large|30|40|one, two, three\nmore")
                self.assertEqual(app.counter.cget("text"), "3")    # "0x" -> 0 + 1.5 + 1.5
                self.assertEqual(app.options_group.cget("text"), app.title_label.cget("text"))
            finally:
                destroy(app)
        self.assertEqual(errors, [])

    def test_checks_stop(self):
        p = build_two_window_project()
        ns = load(self.gen(p))
        errors = []
        with Stubs() as stubs:
            app = make_app(ns, errors)
            try:
                app.greet_clicked()            # name empty -> warning, stop
                self.assertEqual(stubs.names(), ["showwarning"])
                self.assertEqual(app.answer.cget("text"), "")
                self.assertIsNone(app.window2)
                app.name.insert(0, "Zoe")
                app.greet_clicked()
                app.update()
                self.assertEqual(app.answer.cget("text"), "Hello, Zoe!")
                self.assertEqual(app.log_text.get("1.0", "end-1c"), "Greeted Zoe")
                app.greet_clicked()
                self.assertEqual(app.log_text.get("1.0", "end-1c"), "Greeted Zoe\nGreeted Zoe")
            finally:
                destroy(app)
        self.assertEqual(errors, [])

    def test_set_checked(self):
        p = build_full_project()
        p.find("go")["events"]["command"] = [
            act("set_checked", target="agree", state="untick"),
            act("set_checked", target="news", state="toggle"),
            act("set_checked", target="small", state="tick"),
            act("set_checked", target="large", state="untick"),    # not chosen: no change
            act("set_checked", target="w2_check", state="untick"),  # window2 is closed
        ]
        p.find("disabled_btn")["events"]["command"] = [
            act("set_checked", target="small", state="untick"),
            act("set_checked", target="red", state="toggle"),
        ]
        p.find("pic_button")["events"]["command"] = [
            act("set_checked", target="red", state="toggle"),
            act("set_checked", target="nope", state="tick"),         # skipped
        ]
        code = self.gen(p)
        self.assertIn("# skipped: Tick nope", code)
        ns = load(code)
        errors = []
        with Stubs():
            app = make_app(ns, errors)
            try:
                app.go_clicked()
                self.assertFalse(app.agree_var.get())
                self.assertTrue(app.news_var.get())
                self.assertEqual(app.size_var.get(), "Small")
                self.assertFalse(app.w2_check_var.get())
                app.open_window2()
                app.update()
                self.assertEqual(app._get("w2_check"), "No")     # remembered while closed
                app.disabled_btn_clicked()
                self.assertEqual(app.size_var.get(), "")
                self.assertEqual(app.color_choice_var.get(), "Red")
                # a group that can become empty must not look half-chosen
                self.assertNotEqual(str(app.small.cget("tristatevalue")), "")
                app.pic_button_clicked()
                self.assertEqual(app.color_choice_var.get(), "")
            finally:
                destroy(app)
        self.assertEqual(errors, [])

    def test_confirm_no_stops(self):
        p = build_two_window_project()
        p.find("greet")["events"]["command"] = [
            act("confirm", message="Sure?"), act("set_text", target="answer", text="yes")]
        ns = load(self.gen(p))
        errors = []
        with Stubs() as stubs:
            stubs.yes = False
            app = make_app(ns, errors)
            try:
                app.greet_clicked()
                self.assertEqual(app.answer.cget("text"), "")
                stubs.yes = True
                app.greet_clicked()
                self.assertEqual(app.answer.cget("text"), "yes")
            finally:
                destroy(app)

    def test_edge_project(self):
        p = build_edge_project()
        code = self.gen(p)
        ns = load(code)
        errors = []
        with Stubs():
            app = make_app(ns, errors)
            try:
                self.assertIsInstance(app.open_window2, tk.Button)   # widget keeps its name
                self.assertIsInstance(app.go_clicked, tk.Button)
                self.assertIsInstance(app.box_var, tk.Label)
                handlers = handler_names(ns)
                go = [h for h in handlers if h.startswith("go_clicked_")]
                self.assertEqual(len(go), 1, handlers)
                getattr(app, go[0])()
                app.update()
                self.assertEqual(app.go_clicked.cget("text"), "clicked")
                self.assertTrue(app.window2.winfo_exists())
                app.quitter_clicked()
                try:
                    alive = app.winfo_exists()
                except tk.TclError:
                    alive = False
                self.assertFalse(alive)
            finally:
                destroy(app)
        app = make_app(ns, errors)
        app.closer_clicked()
        destroy(app)
        self.assertEqual(errors, [])

    def test_radio_without_choice_is_not_tristate(self):
        ns = load(self.gen(build_full_project()))
        errors = []
        app = make_app(ns, errors)
        try:
            self.assertEqual(app.color_choice_var.get(), "")
            self.assertNotEqual(app.red.cget("tristatevalue"), "")
            app.red.invoke()
            self.assertEqual(app.color_choice_var.get(), "Red")
        finally:
            destroy(app)

    def test_events_do_not_fire_at_start(self):
        p = build_two_window_project()
        nb = add(p, "Notebook", p.main_window["id"], node_id="tabs")
        sl = add(p, "Scale", p.main_window["id"], node_id="slider", value=50)
        nb["events"]["on_tab_change"] = [act("show_message", message="tab")]
        sl["events"]["command"] = [act("show_message", message="slider")]
        ns = load(self.gen(p))
        errors = []
        with Stubs() as stubs:
            app = make_app(ns, errors)
            try:
                app.update()
                app.after(50, app.quit)
                app.mainloop()
                self.assertEqual(stubs.calls, [])
                app.tabs.select(1)
                app.update()
                self.assertEqual(stubs.calls[-1][1][1], "tab")
            finally:
                destroy(app)

    def test_codeview(self):
        import theme
        import codeview
        code = self.gen(build_two_window_project())
        root = tk.Tk()
        root.withdraw()
        theme.apply(root)
        try:
            view = codeview.CodeView(root, code, "greeter.py")
            root.update()
            text = view.text.get("1.0", "end-1c")
            self.assertEqual(text, code.rstrip("\n"))
            self.assertTrue(view.text.tag_ranges("keyword"))
            self.assertTrue(view.text.tag_ranges("string"))
            self.assertTrue(view.text.tag_ranges("comment"))
            view.copy()
            self.assertEqual(root.clipboard_get(), code)
            path = os.path.join(TMP, "saved_code.py")
            orig = filedialog.asksaveasfilename
            filedialog.asksaveasfilename = lambda **k: path
            try:
                view.save()
            finally:
                filedialog.asksaveasfilename = orig
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), code)
            view.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
