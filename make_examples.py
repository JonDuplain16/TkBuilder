"""Builds the example projects in ./examples (run: python make_examples.py)."""
import os

from model import Project, new_action

HERE = os.path.dirname(os.path.abspath(__file__))


def act(kind, **params):
    a = new_action(kind)
    a["params"].update(params)
    return a


def add(p, parent, wtype, row, col, **props):
    n = p.new_node(wtype)
    n["props"].update({k: v for k, v in props.items() if k != "layout"})
    p.place(n, parent, row, col)
    n["layout"].update(props.get("layout", {}))
    return n


def greeter():
    p = Project()
    w = p.main_window
    w["props"].update(title="Greeter", width=360, height=180)
    win = w["id"]
    lab = add(p, win, "Label", 0, 0, text="What's your name?")
    p.rename(lab["id"], "question")
    ent = add(p, win, "Entry", 0, 1)
    p.rename(ent["id"], "name_box")
    btn = add(p, win, "Button", 1, 0, text="Say hello", layout={"colspan": 2})
    p.rename(btn["id"], "hello_button")
    out = add(p, win, "Label", 2, 0, text="", layout={"colspan": 2},
              font={"family": "", "size": 14, "bold": True, "italic": False, "underline": False},
              fg="#2563eb")
    p.rename(out["id"], "greeting")
    btn["events"]["command"] += [
        act("check", target="name_box", condition="not_empty", message="Please type your name first."),
        act("set_text", target="greeting", text="Hello, {name_box}! \U0001F44B"),
    ]
    ent["events"]["on_enter"] = list(btn["events"]["command"])
    return p


def counter():
    p = Project()
    w = p.main_window
    w["props"].update(title="Click Counter", width=300, height=200)
    win = w["id"]
    num = add(p, win, "Label", 0, 0, text="0", layout={"colspan": 3},
              font={"family": "", "size": 36, "bold": True, "italic": False, "underline": False})
    p.rename(num["id"], "count")
    minus = add(p, win, "Button", 1, 0, text="− 1", width=6)
    reset = add(p, win, "Button", 1, 1, text="Reset", width=6)
    plus = add(p, win, "Button", 1, 2, text="+ 1", width=6, bg="#dcfce7")
    minus["events"]["command"].append(act("change_number", target="count", amount=-1))
    plus["events"]["command"].append(act("change_number", target="count", amount=1))
    reset["events"]["command"].append(act("set_text", target="count", text="0"))
    return p


def todo():
    p = Project()
    w = p.main_window
    w["props"].update(title="To-Do List", width=380, height=340)
    win = w["id"]
    ent = add(p, win, "Entry", 0, 0, layout={"fill": "x"})
    p.rename(ent["id"], "new_task")
    addb = add(p, win, "Button", 0, 1, text="Add")
    lst = add(p, win, "Listbox", 1, 0, items=["Buy milk", "Learn Python"],
              layout={"colspan": 2, "fill": "both"})
    p.rename(lst["id"], "tasks")
    done = add(p, win, "Button", 2, 0, text="Done ✔ (remove selected)", layout={"colspan": 2})
    adding = [act("check", target="new_task", condition="not_empty", message="Type a task first."),
              act("add_item", target="tasks", text="{new_task}"),
              act("clear", target="new_task")]
    addb["events"]["command"] += adding
    ent["events"]["on_enter"] += [dict(a, params=dict(a["params"])) for a in adding]
    done["events"]["command"].append(act("remove_selected", target="tasks"))
    # menu bar
    w["props"]["menu"] = [
        {"label": "File", "items": [
            {"label": "Clear all tasks", "actions": [
                act("confirm", message="Remove every task?"), act("clear", target="tasks")]},
            {"separator": True},
            {"label": "Quit", "actions": [act("quit_app")]}]},
        {"label": "Help", "items": [
            {"label": "About", "actions": [act("show_message", title="About",
                                                message="A tiny to-do list made with TkBuilder.")]}]},
    ]
    return p


def settings_demo():
    p = Project()
    w = p.main_window
    w["props"].update(title="Tabs & Windows Demo", width=460, height=330)
    win = w["id"]
    tabs = add(p, win, "Notebook", 0, 0, layout={"fill": "both"})
    t1, t2 = tabs["children"]
    t1["props"]["text"], t2["props"]["text"] = "Profile", "Options"
    lab = add(p, t1["id"], "Label", 0, 0, text="Name:", layout={"halign": "right"})
    name = add(p, t1["id"], "Entry", 0, 1)
    p.rename(name["id"], "name")
    add(p, t1["id"], "Label", 1, 0, text="Favourite colour:", layout={"halign": "right"})
    col = add(p, t1["id"], "Combobox", 1, 1, items=["Red", "Green", "Blue"], text="Blue")
    p.rename(col["id"], "colour")
    add(p, t1["id"], "Label", 2, 0, text="Age:", layout={"halign": "right"})
    age = add(p, t1["id"], "Spinbox", 2, 1, value=18, to=120)
    p.rename(age["id"], "age")
    news = add(p, t2["id"], "Checkbutton", 0, 0, text="Send me the newsletter", layout={"halign": "left"})
    p.rename(news["id"], "newsletter")
    g = add(p, t2["id"], "LabelFrame", 1, 0, text="Size")
    for i, s in enumerate(["Small", "Medium", "Large"]):
        add(p, g["id"], "Radiobutton", 0, i, text=s, group="size", selected=(s == "Medium"))
    vol = add(p, t2["id"], "Scale", 2, 0, label="Volume", value=40, layout={"fill": "x"})
    p.rename(vol["id"], "volume")
    summary_win = p.add_window("Summary")
    summary_win["props"].update(width=340, height=200)
    out = add(p, summary_win["id"], "Label", 0, 0, text="", justify="left", wraplength=300)
    p.rename(out["id"], "summary")
    close = add(p, summary_win["id"], "Button", 1, 0, text="Close")
    close["events"]["command"].append(act("close_window", window=summary_win["id"]))
    show = add(p, win, "Button", 1, 0, text="Show summary →", layout={"halign": "right"},
               bg="#2563eb", fg="#ffffff")
    show["events"]["command"] += [
        act("check", target="name", condition="not_empty", message="Please enter a name on the Profile tab."),
        act("open_window", window=summary_win["id"]),
        act("set_text", target="summary",
            text="{name} is {age} and likes {colour}.\nNewsletter: {newsletter}\n"
                 "Size: {option1}\nVolume: {volume}"),
    ]
    return p


if __name__ == "__main__":
    for name, fn in [("1 - Greeter", greeter), ("2 - Click Counter", counter),
                     ("3 - To-Do List", todo), ("4 - Tabs and Windows", settings_demo)]:
        path = os.path.join(HERE, "examples", name + ".tkb")
        with open(path, "w", encoding="utf-8") as f:
            f.write(fn().to_json())
        print("wrote", path)
