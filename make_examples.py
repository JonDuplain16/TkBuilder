"""Builds the template projects in ./examples (run: python make_examples.py).

The engineering templates call ordinary functions in examples/backend/
engineering.py ("backend" folder = settings backend_dir), which is the
workflow TkBuilder is made for: your code stays plain Python, the GUI calls it.
"""
import glob
import os

from model import Project, new_action

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "examples")


def act(kind, **params):
    a = new_action(kind)
    a["params"].update(params)
    return a


def call(function, args=(), result="none", module="engineering", **params):
    """A 'Call a Python function' action. args: (parameter name, widget id) pairs."""
    return act("call_function", module=module, function=function, result=result,
               args=[{"name": n, "source": "widget", "widget": w, "value": ""} for n, w in args],
               **params)


def add(p, parent, wtype, row, col, name=None, layout=None, **props):
    n = p.new_node(wtype)
    n["props"].update(props)
    p.place(n, parent, row, col)
    n["layout"].update(layout or {})
    if name:
        p.rename(n["id"], name)
    return n


def project(title, app_name, theme, description, width, height, backend=False):
    p = Project()
    p.settings.update(app_name=app_name, theme=theme, description=description)
    if backend:
        p.settings["backend_dir"] = "backend"
    p.main_window["props"].update(title=title, width=width, height=height)
    return p, p.main_window["id"]


def form_row(p, parent, row, label, name, **props):
    add(p, parent, "Label", row, 0, text=label)
    return add(p, parent, "Entry", row, 1, name=name, layout={"fill": "x"}, **props)


# ------------------------------------------------------------ engineering
def beam_calculator():
    p, win = project("Beam Calculator", "Beam Calculator", "slate",
                     "Calls beam_deflection() and shows the returned dict in labels.",
                     440, 360, backend=True)
    add(p, win, "Label", 0, 0, text="Simply supported beam, centre load", variant="Heading")
    inputs = add(p, win, "LabelFrame", 1, 0, text="Inputs", layout={"fill": "x"})
    form_row(p, inputs["id"], 0, "Load (N)", "load_n", text="5000", value_type="float")
    form_row(p, inputs["id"], 1, "Span (m)", "length_m", text="2.0", value_type="float")
    form_row(p, inputs["id"], 2, "Young's modulus (GPa)", "e_gpa", text="200",
             value_type="float")
    form_row(p, inputs["id"], 3, "Second moment of area (cm⁴)", "i_cm4", text="800",
             value_type="float")
    btn = add(p, win, "Button", 2, 0, name="calculate", text="Calculate", variant="Accent",
              shortcut="F9", layout={"halign": "right"}, tooltip="Shortcut: F9")
    results = add(p, win, "LabelFrame", 3, 0, text="Results", layout={"fill": "x"})
    add(p, results["id"], "Label", 0, 0, text="Max deflection (mm)")
    add(p, results["id"], "Label", 0, 1, name="deflection_mm", text="–", variant="Mono")
    add(p, results["id"], "Label", 1, 0, text="Max bending moment (kN·m)")
    add(p, results["id"], "Label", 1, 1, name="moment_knm", text="–", variant="Mono")
    btn["events"]["command"].append(call(
        "beam_deflection", [("load_n", "load_n"), ("length_m", "length_m"),
                            ("e_gpa", "e_gpa"), ("i_cm4", "i_cm4")],
        result="dict", background=False))
    return p


def unit_converter():
    p, win = project("Unit Converter", "Unit Converter", "ocean",
                     "Calls unit_convert(value, conversion) whenever an input changes.",
                     380, 220, backend=True)
    add(p, win, "Label", 0, 0, text="Value")
    add(p, win, "Entry", 0, 1, name="value", text="25.4", value_type="float",
        layout={"fill": "x"})
    add(p, win, "Label", 1, 0, text="Conversion")
    conv = add(p, win, "Combobox", 1, 1, name="conversion", text="mm to inch",
               items=["mm to inch", "inch to mm", "bar to psi", "psi to bar",
                      "kg to lb", "lb to kg"], layout={"fill": "x"})
    btn = add(p, win, "Button", 2, 1, name="convert", text="Convert", variant="Accent",
              layout={"halign": "right"})
    add(p, win, "Label", 3, 0, text="Result")
    add(p, win, "Label", 3, 1, name="result", text="–", variant="Title")
    run = call("unit_convert", [("value", "value"), ("conversion", "conversion")],
               result="widget", result_widget="result", background=False)
    btn["events"]["command"].append(run)
    conv["events"]["on_select"].append(dict(run))
    p.find("value")["events"]["on_enter"].append(dict(run))
    return p


def sensor_dashboard():
    p, win = project("Live Sensor", "Live Sensor", "midnight",
                     "A window timer calls sensor_reading() every second and logs it.",
                     460, 420, backend=True)
    w = p.main_window
    w["props"]["timer_ms"] = 1000
    add(p, win, "Label", 0, 0, text="Channel")
    add(p, win, "Spinbox", 0, 1, name="channel", from_=0, to=3, value=0)
    start = add(p, win, "Button", 0, 2, name="start", text="Start", variant="Success")
    stop = add(p, win, "Button", 0, 3, name="stop", text="Stop")
    add(p, win, "Label", 1, 0, name="temperature", text="-- °C", variant="Title",
        layout={"colspan": 4})
    add(p, win, "Progressbar", 2, 0, name="level", value=0, layout={"colspan": 4})
    add(p, win, "Label", 3, 0, name="status", text="Waiting for the first reading…",
        variant="Muted", layout={"colspan": 4})
    add(p, win, "Console", 4, 0, name="readings", height=6, timestamps=True, max_lines=500,
        layout={"colspan": 4})
    w["events"]["on_timer"] += [
        call("sensor_reading", [("channel", "channel")], result="dict", background=False),
        act("log", target="readings", text="channel {channel}: {temperature}"),
    ]
    start["events"]["command"].append(act("timer_control", window=win, state="start"))
    stop["events"]["command"].append(act("timer_control", window=win, state="stop"))
    return p


def simulation_runner():
    p, win = project("Simulation Runner", "Simulation Runner", "nord",
                     "Runs simulate() in the background with a busy bar, results in a table.",
                     480, 460, backend=True)
    add(p, win, "Label", 0, 0, text="Steps")
    add(p, win, "Spinbox", 0, 1, name="steps", from_=10, to=500, increment=10, value=100)
    add(p, win, "Label", 0, 2, text="Noise")
    add(p, win, "Entry", 0, 3, name="noise", text="0.1", value_type="float", width=8)
    run = add(p, win, "Button", 0, 4, name="run_button", text="Run simulation", variant="Accent")
    add(p, win, "Progressbar", 1, 0, name="busy", value=0, layout={"colspan": 5})
    table = add(p, win, "Table", 2, 0, name="results", layout={"colspan": 5},
                columns=[{"title": "Step", "width": 60, "anchor": "e"},
                         {"title": "x", "width": 80, "anchor": "e"},
                         {"title": "y", "width": 120, "anchor": "e"}], height=10)
    save = add(p, win, "Button", 3, 4, name="save_csv", text="Save CSV…")
    add(p, win, "Label", 3, 0, name="info", text="Press Run simulation.", variant="Muted",
        layout={"colspan": 4})
    run["events"]["command"] += [
        act("set_text", target="info", text="Running {steps} steps…"),
        call("simulate", [("steps", "steps"), ("noise", "noise")], result="widget",
             result_widget="results", background=True, busy="busy"),
        act("set_text", target="info", text="Finished {steps} steps."),
    ]
    save["events"]["command"].append(act("export_table", target=table["id"]))
    return p


def script_runner():
    p, win = project("Script Runner", "Script Runner", "dracula",
                     "Runs a script or program and streams its output into a console.",
                     520, 400)
    add(p, win, "Label", 0, 0, text="Script")
    add(p, win, "FilePicker", 0, 1, name="script", text="backend/slow_task.py",
        filetypes=["Python files|*.py", "All files|*.*"], layout={"fill": "x"})
    add(p, win, "Label", 1, 0, text="Steps")
    add(p, win, "Spinbox", 1, 1, name="steps", from_=1, to=50, value=8)
    run = add(p, win, "Button", 2, 1, name="run_button", text="Run", variant="Accent",
              layout={"halign": "right"})
    add(p, win, "Progressbar", 3, 0, name="busy", value=0, layout={"colspan": 2})
    add(p, win, "Console", 4, 0, name="output", height=10, layout={"colspan": 2})
    clear = add(p, win, "Button", 5, 1, name="clear", text="Clear",
                layout={"halign": "right"})
    run["events"]["command"].append(act("run_command", command='python -u "{script}" {steps}',
                                        output="output", busy="busy"))
    clear["events"]["command"].append(act("clear", target="output"))
    return p


# ------------------------------------------------------------ basics (v1)
def greeter():
    p, win = project("Greeter", "Greeter", "modern_light",
                     "Type a name, click: a check and a message with {name_box}.", 380, 200)
    add(p, win, "Label", 0, 0, name="question", text="What's your name?")
    ent = add(p, win, "Entry", 0, 1, name="name_box", placeholder="Type your name",
              layout={"fill": "x"})
    btn = add(p, win, "Button", 1, 1, name="hello_button", text="Say hello", variant="Accent",
              layout={"halign": "right"})
    add(p, win, "Label", 2, 0, name="greeting", text="", variant="Heading",
        layout={"colspan": 2})
    btn["events"]["command"] += [
        act("check", target="name_box", condition="not_empty",
            message="Please type your name first."),
        act("set_text", target="greeting", text="Hello, {name_box}!"),
    ]
    ent["events"]["on_enter"] = [dict(a, params=dict(a["params"]))
                                 for a in btn["events"]["command"]]
    return p


def counter():
    p, win = project("Click Counter", "Click Counter", "sunset",
                     "Three buttons that change a number - no code at all.", 300, 210)
    add(p, win, "Label", 0, 0, name="count", text="0", anchor="center", variant="Title",
        font={"family": "", "size": 36, "bold": True, "italic": False, "underline": False},
        layout={"colspan": 3, "fill": "x"})
    minus = add(p, win, "Button", 1, 0, text="− 1", width=6)
    reset = add(p, win, "Button", 1, 1, text="Reset", width=6)
    plus = add(p, win, "Button", 1, 2, text="+ 1", width=6, variant="Accent")
    minus["events"]["command"].append(act("change_number", target="count", amount=-1))
    plus["events"]["command"].append(act("change_number", target="count", amount=1))
    reset["events"]["command"].append(act("set_text", target="count", text="0"))
    return p


def todo():
    p, win = project("To-Do List", "To-Do List", "forest",
                     "A list with add/remove and a menu bar.", 380, 340)
    ent = add(p, win, "Entry", 0, 0, name="new_task", placeholder="New task",
              layout={"fill": "x"})
    addb = add(p, win, "Button", 0, 1, text="Add", variant="Accent")
    add(p, win, "Listbox", 1, 0, name="tasks", items=["Buy milk", "Learn Python"],
        layout={"colspan": 2, "fill": "both"})
    done = add(p, win, "Button", 2, 0, text="Done ✔ (remove selected)", layout={"colspan": 2})
    adding = [act("check", target="new_task", condition="not_empty", message="Type a task first."),
              act("add_item", target="tasks", text="{new_task}"),
              act("clear", target="new_task")]
    addb["events"]["command"] += adding
    ent["events"]["on_enter"] += [dict(a, params=dict(a["params"])) for a in adding]
    done["events"]["command"].append(act("remove_selected", target="tasks"))
    p.main_window["props"]["menu"] = [
        {"label": "File", "items": [
            {"label": "Clear all tasks", "shortcut": "", "hook": False, "actions": [
                act("confirm", message="Remove every task?"), act("clear", target="tasks")]},
            {"separator": True},
            {"label": "Quit", "shortcut": "Ctrl+Q", "hook": False,
             "actions": [act("quit_app")]}]},
        {"label": "Help", "items": [
            {"label": "About", "shortcut": "", "hook": False, "actions": [
                act("show_message", title="About",
                    message="A tiny to-do list made with TkBuilder.")]}]},
    ]
    return p


def tabs_and_windows():
    p, win = project("Tabs & Windows Demo", "Tabs and Windows", "rose",
                     "Tabs, option groups and a second window filled with {placeholders}.",
                     460, 340)
    tabs = add(p, win, "Notebook", 0, 0, layout={"fill": "both"})
    t1, t2 = tabs["children"]
    t1["props"]["text"], t2["props"]["text"] = "Profile", "Options"
    add(p, t1["id"], "Label", 0, 0, text="Name:", layout={"halign": "right"})
    add(p, t1["id"], "Entry", 0, 1, name="name")
    add(p, t1["id"], "Label", 1, 0, text="Favourite colour:", layout={"halign": "right"})
    add(p, t1["id"], "Combobox", 1, 1, name="colour", items=["Red", "Green", "Blue"],
        text="Blue")
    add(p, t1["id"], "Label", 2, 0, text="Age:", layout={"halign": "right"})
    add(p, t1["id"], "Spinbox", 2, 1, name="age", value=18, to=120)
    add(p, t2["id"], "Checkbutton", 0, 0, name="newsletter", text="Send me the newsletter")
    g = add(p, t2["id"], "LabelFrame", 1, 0, text="Size")
    for i, s in enumerate(["Small", "Medium", "Large"]):
        add(p, g["id"], "Radiobutton", 0, i, name="size_" + s.lower(), text=s, group="size",
            selected=(s == "Medium"))
    add(p, t2["id"], "Label", 2, 0, text="Volume")
    add(p, t2["id"], "Scale", 3, 0, name="volume", value=40, layout={"fill": "x"})
    summary_win = p.add_window("Summary")
    summary_win["props"].update(width=340, height=200)
    add(p, summary_win["id"], "Label", 0, 0, name="summary", text="", wraplength=300)
    close = add(p, summary_win["id"], "Button", 1, 0, text="Close")
    close["events"]["command"].append(act("close_window", window=summary_win["id"]))
    show = add(p, win, "Button", 1, 0, name="show_summary", text="Show summary →",
               variant="Accent", layout={"halign": "right"})
    show["events"]["command"] += [
        act("check", target="name", condition="not_empty",
            message="Please enter a name on the Profile tab."),
        act("open_window", window=summary_win["id"]),
        act("set_text", target="summary",
            text="{name} is {age} and likes {colour}.\nNewsletter: {newsletter}\n"
                 "Size: {size_medium}\nVolume: {volume}"),
    ]
    return p


EXAMPLES = [
    ("1 - Beam Calculator", beam_calculator),
    ("2 - Unit Converter", unit_converter),
    ("3 - Live Sensor", sensor_dashboard),
    ("4 - Simulation Runner", simulation_runner),
    ("5 - Script Runner", script_runner),
    ("6 - Greeter", greeter),
    ("7 - Click Counter", counter),
    ("8 - To-Do List", todo),
    ("9 - Tabs and Windows", tabs_and_windows),
]


def main():
    built = [(name, fn().to_json()) for name, fn in EXAMPLES]   # build all first
    for old in glob.glob(os.path.join(OUT, "*.tkb")):
        os.remove(old)
    for name, text in built:
        path = os.path.join(OUT, name + ".tkb")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote", path)


if __name__ == "__main__":
    main()
