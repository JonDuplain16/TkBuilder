"""
TkBuilder data model.

A project is a plain JSON-serialisable structure so that saving, loading and
undo/redo (snapshots) are trivial.  Everything the designer, inspector and
code generator need to know about widget types and actions lives in the
catalogs below (WIDGETS, ACTIONS) so the UI can be generated from data.

Node (dict):
    {
      "id":       "button1",          # unique, valid python identifier
      "type":     "Button",           # key into WIDGETS
      "props":    {...},              # values for WIDGETS[type]["props"]
      "layout":   {...},              # grid placement in parent (see LAYOUT_DEFAULTS)
      "events":   {"command": [action, ...]},
      "children": [node, ...]         # only for containers
    }

Action (dict):
    {"type": "show_message", "params": {"title": "...", "message": "..."}}

Project (dict, wrapped by class Project):
    {"version": 1, "app_name": "My App", "windows": [window_node, ...]}
    windows[0] is the main window.  Window nodes have type "Window".
"""

import copy
import json
import keyword
import re

FORMAT_VERSION = 1

FONT_FAMILIES = [
    "", "Segoe UI", "Arial", "Helvetica", "Calibri", "Verdana", "Tahoma",
    "Times New Roman", "Georgia", "Courier New", "Consolas", "Comic Sans MS",
]

# --------------------------------------------------------------------------
# Property specs
# --------------------------------------------------------------------------
# kind:
#   text       single line string
#   multiline  multi-line string
#   lines      list[str], edited one item per line
#   int        integer, options={"min":..,"max":..}
#   float      number,  options={"min":..,"max":..}
#   bool       True/False
#   color      "" (= default) or "#rrggbb" / colour name
#   font       {"family": "", "size": 0, "bold": False, "italic": False, "underline": False}
#              empty family / size 0 => tkinter default
#   choice     one of options (list of [value, label])
#   image      "" or path to a .png/.gif file
#   menu       window menu bar, see MENU FORMAT below
#
# group: section heading used by the inspector.


def P(key, label, kind, default=None, options=None, group="Content", help=""):
    return {"key": key, "label": label, "kind": kind, "default": default,
            "options": options, "group": group, "help": help}


def default_font():
    return {"family": "", "size": 0, "bold": False, "italic": False, "underline": False}


RELIEFS = [["flat", "None"], ["groove", "Groove"], ["ridge", "Ridge"],
           ["sunken", "Sunken"], ["raised", "Raised"], ["solid", "Solid line"]]
ORIENT = [["horizontal", "Horizontal"], ["vertical", "Vertical"]]
STATE = [["normal", "Enabled"], ["disabled", "Disabled"]]

_font = lambda: P("font", "Font", "font", default_font(), group="Style")
_fg = lambda: P("fg", "Text color", "color", "", group="Style")
_bg = lambda: P("bg", "Background color", "color", "", group="Style")
_state = lambda: P("state", "State", "choice", "normal", STATE, group="Behavior")
_width_chars = lambda d=0: P("width", "Width (characters, 0 = auto)", "int", d,
                             {"min": 0, "max": 500}, group="Size")

# --------------------------------------------------------------------------
# MENU FORMAT (Window prop "menu"):
#   [ {"label": "File", "items": [
#         {"label": "Open", "actions": [action, ...]},
#         {"separator": True},
#     ]}, ... ]
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Widget catalog
# --------------------------------------------------------------------------
# name        friendly name shown in the palette
# icon        a short unicode glyph for the palette
# category    palette section
# container   True if the node has "children" laid out on a grid
# palette     False => not shown in palette (Window is added via window bar)
# parents     None = any container; otherwise list of allowed parent types
# events      list of [event_key, friendly label]
# props       list of property specs
# help        one-liner shown in the palette tooltip / status bar

WIDGETS = {
    "Window": {
        "name": "Window", "icon": "▣", "category": "Windows", "container": True,
        "palette": False, "parents": [],
        "help": "A window of your app. The first window is the main window.",
        "events": [["on_open", "When the window opens"],
                   ["on_close", "When the window is closed"]],
        "props": [
            P("title", "Title", "text", "My App"),
            P("width", "Width (pixels)", "int", 420, {"min": 100, "max": 4000}, group="Size"),
            P("height", "Height (pixels)", "int", 320, {"min": 80, "max": 4000}, group="Size"),
            P("resizable", "User can resize", "bool", True, group="Behavior"),
            P("padding", "Inner padding", "int", 10, {"min": 0, "max": 200}, group="Size"),
            _bg(),
            P("menu", "Menu bar", "menu", [], group="Menu"),
        ],
    },
    "Frame": {
        "name": "Panel", "icon": "▢", "category": "Containers", "container": True,
        "help": "An invisible (or bordered) box that holds other widgets.",
        "events": [],
        "props": [
            _bg(),
            P("relief", "Border style", "choice", "flat", RELIEFS, group="Style"),
            P("borderwidth", "Border width", "int", 0, {"min": 0, "max": 20}, group="Style"),
            P("padding", "Inner padding", "int", 5, {"min": 0, "max": 200}, group="Size"),
        ],
    },
    "LabelFrame": {
        "name": "Group Box", "icon": "⬚", "category": "Containers", "container": True,
        "help": "A box with a border and a title that groups widgets together.",
        "events": [],
        "props": [
            P("text", "Title", "text", "Group"),
            _font(), _fg(), _bg(),
            P("padding", "Inner padding", "int", 8, {"min": 0, "max": 200}, group="Size"),
        ],
    },
    "Notebook": {
        "name": "Tabs", "icon": "▤", "category": "Containers", "container": True,
        "help": "A set of tabs. Each tab is its own area you can drop widgets into.",
        "events": [["on_tab_change", "When the user switches tab"]],
        "props": [
            P("width", "Width (pixels, 0 = auto)", "int", 0, {"min": 0, "max": 4000}, group="Size"),
            P("height", "Height (pixels, 0 = auto)", "int", 0, {"min": 0, "max": 4000}, group="Size"),
        ],
    },
    "Tab": {
        "name": "Tab", "icon": "⬓", "category": "Containers", "container": True,
        "parents": ["Notebook"],
        "help": "One page of a Tabs widget. Drop it onto a Tabs widget.",
        "events": [],
        "props": [
            P("text", "Tab title", "text", "Tab"),
            _bg(),
            P("padding", "Inner padding", "int", 8, {"min": 0, "max": 200}, group="Size"),
        ],
    },
    "Label": {
        "name": "Label", "icon": "A", "category": "Basic",
        "help": "Shows text (or a picture) that the user cannot edit.",
        "events": [],
        "props": [
            P("text", "Text", "multiline", "Label"),
            P("image", "Picture (PNG/GIF)", "image", ""),
            P("justify", "Text alignment", "choice", "left",
              [["left", "Left"], ["center", "Center"], ["right", "Right"]]),
            P("wraplength", "Wrap text at (pixels, 0 = never)", "int", 0, {"min": 0, "max": 4000}, group="Size"),
            _width_chars(),
            _font(), _fg(), _bg(),
        ],
    },
    "Button": {
        "name": "Button", "icon": "▭", "category": "Basic",
        "help": "A button the user can click to make things happen.",
        "events": [["command", "When clicked"]],
        "props": [
            P("text", "Text", "text", "Button"),
            P("image", "Picture (PNG/GIF)", "image", ""),
            _width_chars(),
            _font(), _fg(), _bg(), _state(),
        ],
    },
    "Entry": {
        "name": "Text Box", "icon": "⎕", "category": "Input",
        "help": "A one-line box where the user can type text.",
        "events": [["on_enter", "When Enter is pressed"]],
        "props": [
            P("text", "Starting text", "text", ""),
            P("password", "Hide what is typed (password)", "bool", False, group="Behavior"),
            _width_chars(20),
            _font(), _fg(), _bg(), _state(),
        ],
    },
    "Text": {
        "name": "Text Area", "icon": "≡", "category": "Input",
        "help": "A big box for several lines of text.",
        "events": [],
        "props": [
            P("text", "Starting text", "multiline", ""),
            P("width", "Width (characters)", "int", 30, {"min": 1, "max": 500}, group="Size"),
            P("height", "Height (lines)", "int", 6, {"min": 1, "max": 200}, group="Size"),
            P("wrap", "Wrap long lines", "choice", "word",
              [["word", "At words"], ["char", "At any letter"], ["none", "Don't wrap"]], group="Behavior"),
            P("scrollbar", "Show scrollbar", "bool", True, group="Behavior"),
            P("readonly", "Read only", "bool", False, group="Behavior"),
            _font(), _fg(), _bg(),
        ],
    },
    "Checkbutton": {
        "name": "Checkbox", "icon": "☑", "category": "Input",
        "help": "A box the user can tick on or off.",
        "events": [["command", "When ticked / unticked"]],
        "props": [
            P("text", "Text", "text", "Check me"),
            P("checked", "Ticked at start", "bool", False),
            _font(), _fg(), _bg(), _state(),
        ],
    },
    "Radiobutton": {
        "name": "Option Button", "icon": "◉", "category": "Input",
        "help": "Round buttons where only one in the same group can be chosen.",
        "events": [["command", "When chosen"]],
        "props": [
            P("text", "Text", "text", "Option"),
            P("group", "Group name", "text", "group1",
              help="Option buttons with the same group name work together."),
            P("selected", "Chosen at start", "bool", False),
            _font(), _fg(), _bg(), _state(),
        ],
    },
    "Combobox": {
        "name": "Dropdown", "icon": "▾", "category": "Input",
        "help": "A drop-down list to pick one item from.",
        "events": [["on_select", "When an item is picked"]],
        "props": [
            P("items", "Items (one per line)", "lines", ["Option 1", "Option 2", "Option 3"]),
            P("text", "Starting value", "text", "Option 1"),
            P("readonly", "Only allow items from the list", "bool", True, group="Behavior"),
            _width_chars(18),
            _font(), _state(),
        ],
    },
    "Listbox": {
        "name": "List", "icon": "☰", "category": "Input",
        "help": "A list of items the user can select.",
        "events": [["on_select", "When an item is selected"]],
        "props": [
            P("items", "Items (one per line)", "lines", ["Item 1", "Item 2", "Item 3"]),
            P("selectmode", "Selection", "choice", "browse",
              [["browse", "One item"], ["extended", "Many items"]], group="Behavior"),
            P("scrollbar", "Show scrollbar", "bool", True, group="Behavior"),
            P("width", "Width (characters)", "int", 20, {"min": 1, "max": 500}, group="Size"),
            P("height", "Height (rows)", "int", 6, {"min": 1, "max": 200}, group="Size"),
            _font(), _fg(), _bg(),
        ],
    },
    "Spinbox": {
        "name": "Number Box", "icon": "⇅", "category": "Input",
        "help": "A box for picking a number with up/down arrows.",
        "events": [["command", "When the number changes"]],
        "props": [
            P("from_", "Smallest", "float", 0),
            P("to", "Largest", "float", 100),
            P("increment", "Step", "float", 1),
            P("value", "Starting value", "float", 0),
            _width_chars(8),
            _font(), _state(),
        ],
    },
    "Scale": {
        "name": "Slider", "icon": "↔", "category": "Input",
        "help": "A slider for choosing a number.",
        "events": [["command", "When moved"]],
        "props": [
            P("label", "Caption", "text", ""),
            P("from_", "Smallest", "float", 0),
            P("to", "Largest", "float", 100),
            P("resolution", "Step", "float", 1),
            P("value", "Starting value", "float", 0),
            P("orient", "Direction", "choice", "horizontal", ORIENT),
            P("length", "Length (pixels)", "int", 160, {"min": 20, "max": 4000}, group="Size"),
            P("showvalue", "Show the number", "bool", True, group="Behavior"),
            _fg(), _bg(), _state(),
        ],
    },
    "Progressbar": {
        "name": "Progress Bar", "icon": "▰", "category": "Display",
        "help": "A bar that shows how far along something is.",
        "events": [],
        "props": [
            P("value", "Value", "float", 30),
            P("maximum", "Maximum", "float", 100),
            P("orient", "Direction", "choice", "horizontal", ORIENT),
            P("length", "Length (pixels)", "int", 200, {"min": 20, "max": 4000}, group="Size"),
            P("mode", "Mode", "choice", "determinate",
              [["determinate", "Shows a value"], ["indeterminate", "Busy animation"]], group="Behavior"),
        ],
    },
    "Separator": {
        "name": "Separator", "icon": "―", "category": "Display",
        "help": "A thin line to separate parts of the window.",
        "events": [],
        "props": [
            P("orient", "Direction", "choice", "horizontal", ORIENT),
        ],
    },
    "Canvas": {
        "name": "Drawing Area", "icon": "▧", "category": "Display",
        "help": "An empty area (for pictures or drawing with code later).",
        "events": [["on_click", "When clicked"]],
        "props": [
            P("width", "Width (pixels)", "int", 200, {"min": 1, "max": 4000}, group="Size"),
            P("height", "Height (pixels)", "int", 120, {"min": 1, "max": 4000}, group="Size"),
            P("bg", "Background color", "color", "#ffffff", group="Style"),
            P("relief", "Border style", "choice", "solid", RELIEFS, group="Style"),
            P("borderwidth", "Border width", "int", 1, {"min": 0, "max": 20}, group="Style"),
        ],
    },
}

PALETTE_CATEGORIES = ["Basic", "Input", "Display", "Containers"]

# Grid placement of a node inside its parent container.
#   fill:   none | x | y | both          (stretch to fill the cell)
#   halign: left | center | right        (when not stretched horizontally)
#   valign: top  | center | bottom       (when not stretched vertically)
LAYOUT_DEFAULTS = {"row": 0, "col": 0, "rowspan": 1, "colspan": 1,
                   "fill": "none", "halign": "center", "valign": "center",
                   "padx": 5, "pady": 5}

LAYOUT_SPECS = [
    P("fill", "Stretch to fill space", "choice", "none",
      [["none", "Don't stretch"], ["x", "Stretch sideways"],
       ["y", "Stretch up/down"], ["both", "Stretch both ways"]], group="Layout"),
    P("halign", "Horizontal position", "choice", "center",
      [["left", "Left"], ["center", "Center"], ["right", "Right"]], group="Layout"),
    P("valign", "Vertical position", "choice", "center",
      [["top", "Top"], ["center", "Middle"], ["bottom", "Bottom"]], group="Layout"),
    P("colspan", "Columns wide", "int", 1, {"min": 1, "max": 50}, group="Layout"),
    P("rowspan", "Rows tall", "int", 1, {"min": 1, "max": 50}, group="Layout"),
    P("padx", "Space left/right", "int", 5, {"min": 0, "max": 200}, group="Layout"),
    P("pady", "Space above/below", "int", 5, {"min": 0, "max": 200}, group="Layout"),
]


def sticky_for(layout):
    """Return the tkinter grid sticky string for a layout dict."""
    fill = layout.get("fill", "none")
    s = ""
    if fill in ("y", "both"):
        s += "ns"
    else:
        s += {"top": "n", "bottom": "s"}.get(layout.get("valign", "center"), "")
    if fill in ("x", "both"):
        s += "ew"
    else:
        s += {"left": "w", "right": "e"}.get(layout.get("halign", "center"), "")
    return s


def grid_weights(container):
    """Return (row_weights, col_weights): dicts index->1 for rows/cols that
    contain a child that stretches in that direction.  Both the designer
    and the generated code use this so the preview matches the result."""
    rows, cols = {}, {}
    for ch in container.get("children", []):
        lay = ch.get("layout", {})
        fill = lay.get("fill", "none")
        if fill in ("x", "both"):
            cols[lay.get("col", 0)] = 1
        if fill in ("y", "both"):
            rows[lay.get("row", 0)] = 1
    return rows, cols


# --------------------------------------------------------------------------
# Action catalog
# --------------------------------------------------------------------------
# Param kinds:
#   text      string; may contain {widget_id} placeholders that are replaced
#             with the current value of that widget when the action runs.
#             Literal braces are written {{ and }}.
#   multiline same as text but multi-line
#   number    float
#   widget    id of a widget; options={"types": [...]} restricts targets (None = any)
#   window    id of a window; options={"main": False} excludes the main window
#   tab       id of a Tab node
#   choice    options list of [value, label]
#   color     colour string ("" = leave unchanged)
#   code      python source (advanced)

TEXT_TARGETS = ["Label", "Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                "Combobox", "Spinbox", "LabelFrame", "Scale", "Progressbar", "Listbox"]
INPUT_TYPES = ["Entry", "Text", "Combobox", "Spinbox", "Listbox", "Checkbutton",
               "Radiobutton", "Scale"]
NUMBER_TARGETS = ["Label", "Entry", "Spinbox", "Scale", "Progressbar"]
COLOR_TARGETS = ["Label", "Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                 "Listbox", "Frame", "LabelFrame", "Canvas", "Scale"]


def A(key, label, kind, default=None, options=None):
    return {"key": key, "label": label, "kind": kind, "default": default, "options": options}


ACTIONS = {
    "show_message": {
        "name": "Show a message", "category": "Messages",
        "summary": "Show message “{message}”",
        "params": [
            A("title", "Title", "text", "Message"),
            A("message", "Message", "multiline", "Hello!"),
            A("icon", "Kind", "choice", "info",
              [["info", "Information"], ["warning", "Warning"], ["error", "Error"]]),
        ],
    },
    "confirm": {
        "name": "Ask Yes/No (stop if No)", "category": "Messages",
        "summary": "Ask “{message}” – stop if No",
        "params": [
            A("title", "Title", "text", "Please confirm"),
            A("message", "Question", "multiline", "Are you sure?"),
        ],
    },
    "ask_text": {
        "name": "Ask the user to type something", "category": "Messages",
        "summary": "Ask “{message}” and put the answer in {target}",
        "params": [
            A("title", "Title", "text", "Question"),
            A("message", "Question", "text", "Please type something:"),
            A("target", "Put the answer into", "widget", "", {"types": TEXT_TARGETS}),
        ],
    },
    "check": {
        "name": "Only continue if…", "category": "Logic",
        "summary": "Only continue if {target} {condition} {value}",
        "params": [
            A("target", "Widget", "widget", "", {"types": INPUT_TYPES + ["Label"]}),
            A("condition", "Condition", "choice", "not_empty",
              [["not_empty", "is not empty"], ["empty", "is empty"],
               ["equals", "is equal to"], ["not_equals", "is not equal to"],
               ["contains", "contains"], ["checked", "is ticked/chosen"],
               ["unchecked", "is not ticked/chosen"], ["number", "is a number"],
               ["greater", "is a number greater than"], ["less", "is a number less than"]]),
            A("value", "Value (if needed)", "text", ""),
            A("message", "Otherwise show this message (optional)", "text",
              "Please fill in the form."),
        ],
    },
    "set_checked": {
        "name": "Tick / untick a checkbox or option", "category": "Widgets",
        "summary": "{state} {target}",
        "params": [
            A("target", "Checkbox or option button", "widget", "",
              {"types": ["Checkbutton", "Radiobutton"]}),
            A("state", "Make it", "choice", "tick",
              [["tick", "Tick / choose"], ["untick", "Untick / clear"], ["toggle", "Switch (toggle)"]]),
        ],
    },
    "set_text": {
        "name": "Set the text / value of a widget", "category": "Widgets",
        "summary": "Set {target} to “{text}”",
        "params": [
            A("target", "Widget", "widget", "", {"types": TEXT_TARGETS}),
            A("text", "New text / value", "multiline", ""),
        ],
    },
    "append_text": {
        "name": "Add text to the end of a widget", "category": "Widgets",
        "summary": "Add “{text}” to the end of {target}",
        "params": [
            A("target", "Widget", "widget", "", {"types": ["Label", "Entry", "Text"]}),
            A("text", "Text to add", "multiline", ""),
            A("newline", "Start on a new line", "choice", "yes", [["yes", "Yes"], ["no", "No"]]),
        ],
    },
    "change_number": {
        "name": "Add to a number (counter)", "category": "Widgets",
        "summary": "Add {amount} to {target}",
        "params": [
            A("target", "Widget", "widget", "", {"types": NUMBER_TARGETS}),
            A("amount", "Amount to add (use minus to subtract)", "number", 1),
        ],
    },
    "clear": {
        "name": "Clear a widget", "category": "Widgets",
        "summary": "Clear {target}",
        "params": [A("target", "Widget", "widget", "",
                     {"types": ["Entry", "Text", "Listbox", "Combobox", "Label", "Spinbox"]})],
    },
    "add_item": {
        "name": "Add an item to a list", "category": "Lists",
        "summary": "Add “{text}” to {target}",
        "params": [
            A("target", "List or dropdown", "widget", "", {"types": ["Listbox", "Combobox"]}),
            A("text", "Item text", "text", ""),
        ],
    },
    "remove_selected": {
        "name": "Remove the selected item from a list", "category": "Lists",
        "summary": "Remove selected item(s) from {target}",
        "params": [A("target", "List", "widget", "", {"types": ["Listbox"]})],
    },
    "show_widget": {
        "name": "Show a hidden widget", "category": "Widgets",
        "summary": "Show {target}",
        "params": [A("target", "Widget", "widget", "", None)],
    },
    "hide_widget": {
        "name": "Hide a widget", "category": "Widgets",
        "summary": "Hide {target}",
        "params": [A("target", "Widget", "widget", "", None)],
    },
    "enable_widget": {
        "name": "Enable a widget", "category": "Widgets",
        "summary": "Enable {target}",
        "params": [A("target", "Widget", "widget", "",
                     {"types": ["Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                                "Combobox", "Spinbox", "Scale", "Listbox"]})],
    },
    "disable_widget": {
        "name": "Disable a widget", "category": "Widgets",
        "summary": "Disable {target}",
        "params": [A("target", "Widget", "widget", "",
                     {"types": ["Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                                "Combobox", "Spinbox", "Scale", "Listbox"]})],
    },
    "set_color": {
        "name": "Change colors of a widget", "category": "Widgets",
        "summary": "Change colors of {target}",
        "params": [
            A("target", "Widget", "widget", "", {"types": COLOR_TARGETS}),
            A("fg", "Text color (empty = keep)", "color", ""),
            A("bg", "Background color (empty = keep)", "color", ""),
        ],
    },
    "open_window": {
        "name": "Open another window", "category": "Windows",
        "summary": "Open window {window}",
        "params": [A("window", "Window", "window", "", {"main": False})],
    },
    "close_window": {
        "name": "Close a window", "category": "Windows",
        "summary": "Close window {window}",
        "params": [A("window", "Window", "window", "", {"main": True})],
    },
    "switch_tab": {
        "name": "Switch to a tab", "category": "Windows",
        "summary": "Switch to tab {tab}",
        "params": [A("tab", "Tab", "tab", "")],
    },
    "set_title": {
        "name": "Change a window title", "category": "Windows",
        "summary": "Set title of {window} to “{text}”",
        "params": [A("window", "Window", "window", "", {"main": True}),
                   A("text", "New title", "text", "")],
    },
    "choose_file": {
        "name": "Let the user pick a file", "category": "Files",
        "summary": "Pick a file and put its path in {target}",
        "params": [
            A("mode", "Dialog", "choice", "open",
              [["open", "Open a file"], ["save", "Save as…"], ["folder", "Choose a folder"]]),
            A("target", "Put the path into", "widget", "", {"types": TEXT_TARGETS}),
        ],
    },
    "load_file": {
        "name": "Load a text file into a widget", "category": "Files",
        "summary": "Load file {path} into {target}",
        "params": [
            A("path", "File path (can use {widget} values)", "text", ""),
            A("target", "Widget", "widget", "", {"types": ["Text", "Entry", "Label"]}),
        ],
    },
    "save_file": {
        "name": "Save a widget's text to a file", "category": "Files",
        "summary": "Save {source} to file {path}",
        "params": [
            A("path", "File path (can use {widget} values)", "text", ""),
            A("source", "Widget", "widget", "", {"types": ["Text", "Entry", "Label", "Listbox"]}),
        ],
    },
    "quit_app": {
        "name": "Quit the app", "category": "Windows",
        "summary": "Quit the app",
        "params": [],
    },
    "run_code": {
        "name": "Run Python code (advanced)", "category": "Advanced",
        "summary": "Run custom code",
        "params": [A("code", "Python code (self = the app)", "code", "print('Hello')")],
    },
}

ACTION_CATEGORIES = ["Messages", "Logic", "Widgets", "Lists", "Windows", "Files", "Advanced"]

PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def new_action(action_type):
    spec = ACTIONS[action_type]
    return {"type": action_type,
            "params": {p["key"]: copy.deepcopy(p["default"]) for p in spec["params"]}}


def action_summary(action, project=None):
    """Human readable one-liner for an action, e.g. 'Set label1 to "Hi"'."""
    spec = ACTIONS.get(action.get("type"))
    if not spec:
        return "Unknown action"
    params = action.get("params", {})

    def fmt(key):
        v = params.get(key, "")
        if project is not None and isinstance(v, str) and v:
            node = project.find(v)
            if key in ("target", "source", "window", "tab"):
                if node is None:
                    return "%s (missing!)" % v
                return project.display_name(node)
        if isinstance(v, str):
            v = v.replace("\n", " ")
            return (v[:30] + "…") if len(v) > 31 else v
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)

    needs_value = params.get("condition") in ("equals", "not_equals", "contains", "greater", "less")

    def repl(m):
        key = m.group(1)
        if key == "value" and action.get("type") == "check" and not needs_value:
            return ""
        if key == "state" and action.get("type") == "set_checked":
            return {"tick": "Tick", "untick": "Untick", "toggle": "Toggle"}.get(params.get("state"), "Tick")
        if key == "condition":
            for val, lab in next(p for p in spec["params"] if p["key"] == "condition")["options"]:
                if val == params.get("condition"):
                    return lab
        return fmt(key) or "?"

    return re.sub(r"\{(\w+)\}", repl, spec["summary"]).strip()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def is_container(node_or_type):
    t = node_or_type if isinstance(node_or_type, str) else node_or_type["type"]
    return WIDGETS[t].get("container", False)


def valid_identifier(name):
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name) \
        and not name.startswith("_") and name not in RESERVED_NAMES


# names that would clash with attributes of tk.Tk / the generated App class
RESERVED_NAMES = {"self", "app", "tk", "ttk", "messagebox", "filedialog", "simpledialog",
                  "title", "geometry", "destroy", "quit", "update", "after", "config",
                  "configure", "bind", "children", "master", "widgets", "main", "run",
                  "mainloop", "winfo", "grid", "pack", "place", "focus", "event",
                  "protocol", "menu", "keys", "get", "set", "image", "images"}
try:  # every method/attribute of tk.Tk would clash with the generated App class
    import tkinter as _tk
    RESERVED_NAMES |= {n for n in dir(_tk.Tk) if not n.startswith("_")}
    del _tk
except ImportError:  # pragma: no cover
    pass


def make_node(wtype, node_id):
    spec = WIDGETS[wtype]
    node = {"id": node_id, "type": wtype,
            "props": {p["key"]: copy.deepcopy(p["default"]) for p in spec["props"]},
            "layout": dict(LAYOUT_DEFAULTS),
            "events": {e[0]: [] for e in spec["events"]}}
    if spec.get("container"):
        node["children"] = []
    return node


def _upgrade_node(node):
    """Fill in missing props/layout/events (for files made by older versions)."""
    spec = WIDGETS.get(node.get("type"))
    if spec is None:
        raise ValueError("Unknown widget type: %r" % node.get("type"))
    props = node.setdefault("props", {})
    for p in spec["props"]:
        props.setdefault(p["key"], copy.deepcopy(p["default"]))
    lay = node.setdefault("layout", {})
    for k, v in LAYOUT_DEFAULTS.items():
        lay.setdefault(k, v)
    ev = node.setdefault("events", {})
    for e in spec["events"]:
        ev.setdefault(e[0], [])
    if spec.get("container"):
        node.setdefault("children", [])
        for ch in node["children"]:
            _upgrade_node(ch)


# --------------------------------------------------------------------------
# Project
# --------------------------------------------------------------------------

class Project:
    """Wraps the project dict and offers all editing operations.

    All mutating methods only change the data; callers (the App) are
    responsible for calling app.commit() to record undo and refresh views.
    """

    def __init__(self, data=None):
        if data is None:
            data = {"version": FORMAT_VERSION, "app_name": "My App", "windows": []}
            self.data = data
            self.add_window(title="My App")
        else:
            self.data = data
            for w in self.data["windows"]:
                _upgrade_node(w)

    # ---- serialisation --------------------------------------------------
    def to_json(self):
        return json.dumps(self.data, indent=2)

    @classmethod
    def from_json(cls, text):
        data = json.loads(text)
        if not isinstance(data, dict) or "windows" not in data or not data["windows"]:
            raise ValueError("This is not a TkBuilder project file.")
        return cls(data)

    def snapshot(self):
        return json.dumps(self.data)

    def restore(self, snap):
        self.data = json.loads(snap)

    # ---- basic queries ---------------------------------------------------
    @property
    def windows(self):
        return self.data["windows"]

    @property
    def main_window(self):
        return self.data["windows"][0]

    def walk(self, node=None):
        """Yield every node (depth first). With node=None walks all windows."""
        roots = [node] if node is not None else self.windows
        for r in roots:
            yield r
            for ch in r.get("children", []):
                yield from self.walk(ch)

    def find(self, node_id):
        if not node_id:
            return None
        for n in self.walk():
            if n["id"] == node_id:
                return n
        return None

    def parent_of(self, node_id):
        for n in self.walk():
            for ch in n.get("children", []):
                if ch["id"] == node_id:
                    return n
        return None

    def window_of(self, node_id):
        """Return the window node that contains node_id (or the window itself)."""
        for w in self.windows:
            for n in self.walk(w):
                if n["id"] == node_id:
                    return w
        return None

    def ancestors(self, node_id):
        """List of ancestors from the window down to the direct parent."""
        chain = []
        p = self.parent_of(node_id)
        while p is not None:
            chain.insert(0, p)
            p = self.parent_of(p["id"])
        return chain

    def is_descendant(self, node_id, ancestor_id):
        return any(a["id"] == ancestor_id for a in self.ancestors(node_id))

    def all_ids(self):
        return {n["id"] for n in self.walk()}

    def unique_id(self, base):
        base = re.sub(r"\W", "", base.lower()) or "widget"
        if base[0].isdigit():
            base = "w" + base
        ids = self.all_ids()
        i = 1
        while "%s%d" % (base, i) in ids:
            i += 1
        return "%s%d" % (base, i)

    def display_name(self, node):
        """Friendly name: the id plus the widget kind, e.g. 'button1 (Button)'."""
        if node["type"] == "Window":
            return "%s (Window: %s)" % (node["id"], node["props"].get("title", ""))
        if node["type"] == "Tab":
            return "%s (Tab: %s)" % (node["id"], node["props"].get("text", ""))
        return "%s (%s)" % (node["id"], WIDGETS[node["type"]]["name"])

    def nodes_of_types(self, types=None):
        """All non-window nodes, optionally filtered by type list."""
        return [n for n in self.walk() if n["type"] != "Window"
                and (types is None or n["type"] in types)]

    # ---- creation --------------------------------------------------------
    def new_node(self, wtype):
        """Create (but don't insert) a fresh node with defaults and a unique id.
        Containers get sensible starting content (Tabs get two tabs)."""
        base = {"Checkbutton": "checkbox", "Radiobutton": "option", "Entry": "textbox",
                "Text": "textarea", "Combobox": "dropdown", "Listbox": "list",
                "Spinbox": "numberbox", "Scale": "slider", "Progressbar": "progress",
                "Frame": "panel", "LabelFrame": "group", "Notebook": "tabs",
                "Canvas": "drawing"}.get(wtype, wtype.lower())
        node = make_node(wtype, self.unique_id(base))
        if wtype in ("Separator", "Progressbar"):
            node["layout"]["fill"] = "x"
        if wtype in ("Notebook", "Text", "Canvas"):
            node["layout"]["fill"] = "both"
        if wtype == "Notebook":
            for i in (1, 2):
                # ids must be unique, so insert temporarily to reserve
                tab = make_node("Tab", "__tmp__")
                node["children"].append(tab)
                tab["id"] = self._unique_with_extra("tab", node)
                tab["props"]["text"] = "Tab %d" % i
        if wtype in ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame"):
            # make the default text distinguishable, e.g. "Button 2"
            num = re.sub(r"^\D+", "", node["id"])
            if num and num != "1":
                node["props"]["text"] = "%s %s" % (node["props"]["text"], num)
        return node

    def _unique_with_extra(self, base, extra_root):
        ids = self.all_ids() | {n["id"] for n in self.walk(extra_root)}
        i = 1
        while "%s%d" % (base, i) in ids:
            i += 1
        return "%s%d" % (base, i)

    def add_window(self, title=None):
        wid = "main_window" if not self.windows else self.unique_id("window")
        w = make_node("Window", wid)
        if self.windows:
            w["props"]["title"] = title or "Window %d" % (len(self.windows) + 1)
            w["props"]["width"], w["props"]["height"] = 320, 240
        else:
            w["props"]["title"] = title or "My App"
        self.windows.append(w)
        return w

    def add_tab(self, notebook_id, title=None):
        nb = self.find(notebook_id)
        tab = make_node("Tab", self.unique_id("tab"))
        tab["props"]["text"] = title or "Tab %d" % (len(nb["children"]) + 1)
        nb["children"].append(tab)
        return tab

    # ---- grid helpers ----------------------------------------------------
    @staticmethod
    def occupancy(container, exclude_id=None):
        """dict (row, col) -> child id for every cell covered by a child."""
        cells = {}
        for ch in container.get("children", []):
            if ch["id"] == exclude_id:
                continue
            lay = ch["layout"]
            for r in range(lay["row"], lay["row"] + max(1, lay["rowspan"])):
                for c in range(lay["col"], lay["col"] + max(1, lay["colspan"])):
                    cells[(r, c)] = ch["id"]
        return cells

    @staticmethod
    def grid_size(container):
        """(rows, cols) actually used by the children (0, 0 when empty)."""
        rows = cols = 0
        for ch in container.get("children", []):
            lay = ch["layout"]
            rows = max(rows, lay["row"] + max(1, lay["rowspan"]))
            cols = max(cols, lay["col"] + max(1, lay["colspan"]))
        return rows, cols

    def child_at(self, container, row, col, exclude_id=None):
        cid = self.occupancy(container, exclude_id).get((row, col))
        return self.find(cid) if cid else None

    @staticmethod
    def insert_row(container, at):
        """Make room: shift everything at row >= at down by one."""
        for ch in container.get("children", []):
            lay = ch["layout"]
            if lay["row"] >= at:
                lay["row"] += 1
            elif lay["row"] + lay["rowspan"] > at:
                lay["rowspan"] += 1

    @staticmethod
    def insert_col(container, at):
        for ch in container.get("children", []):
            lay = ch["layout"]
            if lay["col"] >= at:
                lay["col"] += 1
            elif lay["col"] + lay["colspan"] > at:
                lay["colspan"] += 1

    @staticmethod
    def compact(container):
        """Remove completely empty rows and columns so the grid stays tidy."""
        if container["type"] == "Notebook" or not container.get("children"):
            return
        for axis, span in (("row", "rowspan"), ("col", "colspan")):
            used = set()
            for ch in container["children"]:
                lay = ch["layout"]
                used.update(range(lay[axis], lay[axis] + max(1, lay[span])))
            if not used:
                continue
            mapping, nxt = {}, 0
            for i in range(max(used) + 1):
                if i in used:
                    mapping[i] = nxt
                    nxt += 1
            for ch in container["children"]:
                lay = ch["layout"]
                start, end = lay[axis], lay[axis] + max(1, lay[span]) - 1
                lay[axis] = mapping[start]
                lay[span] = mapping[end] - mapping[start] + 1

    def can_contain(self, container, wtype):
        """Can a widget of type wtype be dropped into container?"""
        if not is_container(container):
            return False
        allowed = WIDGETS[wtype].get("parents")
        if allowed is not None:
            return container["type"] in allowed
        # Notebooks only hold tabs; everything else holds anything but tabs/windows
        return container["type"] != "Notebook"

    def place(self, node, container_id, row, col, mode="cell"):
        """Insert `node` (new or detached) into the container.

        mode:
          "cell"   put it at (row, col); if the cell is taken, a new row is
                   inserted at `row` first.
          "above"/"below"/"left"/"right"
                   insert a new row/column next to (row, col) and put it there.
          "append" (Notebook tabs) just append to children.
        Returns the node.
        """
        container = self.find(container_id)
        if container is None:
            raise ValueError("No such container: %s" % container_id)
        if not self.can_contain(container, node["type"]):
            raise ValueError("A %s can't go inside a %s" % (
                WIDGETS[node["type"]]["name"], WIDGETS[container["type"]]["name"]))
        if container["type"] == "Notebook" or mode == "append":
            if node["type"] == "Tab" and node["props"].get("text") == "Tab":
                node["props"]["text"] = "Tab %d" % (len(container["children"]) + 1)
            container["children"].append(node)
            return node
        if mode == "above":
            self.insert_row(container, row)
        elif mode == "below":
            row += 1
            self.insert_row(container, row)
        elif mode == "left":
            self.insert_col(container, col)
        elif mode == "right":
            col += 1
            self.insert_col(container, col)
        elif self.child_at(container, row, col) is not None:
            self.insert_row(container, row)
        lay = node["layout"]
        lay["row"], lay["col"] = row, col
        lay["rowspan"] = lay["colspan"] = 1
        container["children"].append(node)
        self.compact(container)
        return node

    def next_free_cell(self, container):
        """A sensible spot for 'click to add': a new row below everything."""
        rows, _ = self.grid_size(container)
        return rows, 0

    def detach(self, node_id):
        """Remove node from its parent and return it (for moving)."""
        parent = self.parent_of(node_id)
        if parent is None:
            return None
        node = next(c for c in parent["children"] if c["id"] == node_id)
        parent["children"].remove(node)
        self.compact(parent)
        return node

    def move(self, node_id, container_id, row, col, mode="cell"):
        node = self.find(node_id)
        if node is None or node["type"] == "Window":
            return None
        if node_id == container_id or self.is_descendant(container_id, node_id):
            raise ValueError("Can't put a widget inside itself.")
        old_parent = self.parent_of(node_id)
        # If moving within the same container, adjust coordinates for compaction
        if old_parent is not None and old_parent["id"] == container_id \
                and container_id and old_parent["type"] != "Notebook":
            old_parent["children"].remove(node)
            self.place(node, container_id, row, col, mode)
            return node
        self.detach(node_id)
        return self.place(node, container_id, row, col, mode)

    def move_tab(self, tab_id, delta):
        nb = self.parent_of(tab_id)
        kids = nb["children"]
        i = next(k for k, c in enumerate(kids) if c["id"] == tab_id)
        j = max(0, min(len(kids) - 1, i + delta))
        kids.insert(j, kids.pop(i))

    def remove(self, node_id):
        """Delete a node (or a non-main window). Actions referring to it are
        left as-is (they show as '?' and the generator skips them)."""
        if self.windows and node_id == self.main_window["id"]:
            raise ValueError("The main window can't be deleted.")
        for w in list(self.windows):
            if w["id"] == node_id:
                self.windows.remove(w)
                return
        parent = self.parent_of(node_id)
        if parent is not None:
            if parent["type"] == "Notebook" and len(parent["children"]) <= 1:
                raise ValueError("Tabs need at least one tab.")
            self.detach(node_id)

    def duplicate(self, node_id):
        """Copy a node (with fresh ids) and place it just below the original."""
        node = self.find(node_id)
        if node is None or node["type"] == "Window":
            return None
        clone = copy.deepcopy(node)
        mapping = {}
        reserved = set(self.all_ids())
        for n in self.walk(clone):
            base = re.sub(r"\d+$", "", n["id"]) or "widget"
            i = 1
            while "%s%d" % (base, i) in reserved:
                i += 1
            mapping[n["id"]] = "%s%d" % (base, i)
            reserved.add(mapping[n["id"]])
        for n in self.walk(clone):
            n["id"] = mapping[n["id"]]
        # internal references inside the copy now point at the copies
        for n in self.walk(clone):
            self._rewrite_refs_in_node(n, mapping)
        parent = self.parent_of(node_id)
        if parent["type"] == "Notebook":
            parent["children"].append(clone)
            return clone
        lay = node["layout"]
        return self.place(clone, parent["id"], lay["row"] + lay["rowspan"] - 1, lay["col"], "below")

    # ---- renaming & references -------------------------------------------
    def rename(self, old_id, new_id):
        """Rename a node and update every action that refers to it.
        Raises ValueError with a friendly message if the name is not allowed."""
        if old_id == new_id:
            return
        if not valid_identifier(new_id):
            raise ValueError("Names may only use letters, numbers and _, can't start with a "
                             "number or _, and can't be a reserved word.")
        if new_id in self.all_ids():
            raise ValueError("Another item is already called %r." % new_id)
        node = self.find(old_id)
        node["id"] = new_id
        for n in self.walk():
            self._rewrite_refs_in_node(n, {old_id: new_id})

    def iter_action_lists(self, node):
        """Yield every list of actions stored on a node (events + menu items)."""
        for lst in node.get("events", {}).values():
            yield lst
        if node["type"] == "Window":
            for m in node["props"].get("menu", []) or []:
                for it in m.get("items", []):
                    if not it.get("separator"):
                        yield it.setdefault("actions", [])

    def _rewrite_refs_in_node(self, node, mapping):
        for lst in self.iter_action_lists(node):
            for act in lst:
                spec = ACTIONS.get(act.get("type"), {"params": []})
                params = act.get("params", {})
                for p in spec["params"]:
                    v = params.get(p["key"])
                    if not isinstance(v, str):
                        continue
                    if p["kind"] in ("widget", "window", "tab"):
                        params[p["key"]] = mapping.get(v, v)
                    elif p["kind"] in ("text", "multiline"):
                        params[p["key"]] = PLACEHOLDER_RE.sub(
                            lambda m: "{%s}" % mapping.get(m.group(1), m.group(1)), v)

    def references_to(self, node_id):
        """List of (owner_node, event_label) that have an action referring to node_id."""
        out = []
        for n in self.walk():
            for lst in self.iter_action_lists(n):
                for act in lst:
                    params = act.get("params", {})
                    if any(v == node_id for v in params.values()) or any(
                            isinstance(v, str) and node_id in PLACEHOLDER_RE.findall(v)
                            for v in params.values()):
                        out.append(n)
                        break
        return out
