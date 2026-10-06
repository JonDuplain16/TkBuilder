"""
TkBuilder data model (format version 2).

A project is a plain JSON-serialisable structure so that saving, loading and
undo/redo (snapshots) are trivial.  Everything the designer, inspector and
code generator need to know about widget types and actions lives in the
catalogs below (WIDGETS, ACTIONS, SETTINGS_SPECS) so the UI is generated
from data.

Node (dict):
    {
      "id":       "button1",          # unique python identifier; also the
                                      # widget's name in generated code
      "type":     "Button",           # key into WIDGETS
      "props":    {...},              # values for WIDGETS[type]["props"]
      "layout":   {...},              # placement in parent (LAYOUT_DEFAULTS)
      "events":   {"command": [action, ...]},
      "hooks":    ["command"],        # events that also call the user's own
                                      # Python method (see hook_name())
      "children": [node, ...]         # only for containers
    }

Action (dict):
    {"type": "show_message", "params": {"title": "...", "message": "..."}}

Project (dict, wrapped by class Project):
    {"version": 2, "settings": {...}, "windows": [window_node, ...]}
    windows[0] is the main window.  Window nodes have type "Window".
"""

import copy
import json
import keyword
import re

FORMAT_VERSION = 2

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
#   color      "" (= theme default) or "#rrggbb" / colour name
#   font       {"family": "", "size": 0, "bold": False, "italic": False, "underline": False}
#              empty family / size 0 => theme default
#   choice     one of options (list of [value, label])
#   variant    style variant ("look"); options = ttk class, see apptheme.VARIANTS
#   image      "" or path to a .png/.gif file (.ico also allowed for window icons)
#   menu       window menu bar, see MENU FORMAT below
#   columns    Table columns: list of {"title": str, "width": int, "anchor": "w"|"center"|"e"}
#   rows       Table rows: list of list[str]
#   shortcut   keyboard shortcut text such as "Ctrl+R" or "F5" ("" = none)
#
# group: section heading used by the inspector.
# advanced: True => shown under a collapsed "More options" section.


def P(key, label, kind, default=None, options=None, group="Content", help="", advanced=False):
    return {"key": key, "label": label, "kind": kind, "default": default,
            "options": options, "group": group, "help": help, "advanced": advanced}


def default_font():
    return {"family": "", "size": 0, "bold": False, "italic": False, "underline": False}


RELIEFS = [["flat", "None"], ["groove", "Groove"], ["ridge", "Ridge"],
           ["sunken", "Sunken"], ["raised", "Raised"], ["solid", "Solid line"]]
ORIENT = [["horizontal", "Horizontal"], ["vertical", "Vertical"]]
STATE = [["normal", "Enabled"], ["disabled", "Disabled"]]
ENTRY_STATE = [["normal", "Editable"], ["readonly", "Read only"], ["disabled", "Disabled"]]
ANCHORS = [["w", "Left"], ["center", "Center"], ["e", "Right"]]
COMPOUND = [["left", "Picture left of text"], ["right", "Picture right of text"],
            ["top", "Picture above text"], ["bottom", "Picture below text"],
            ["image", "Picture only"], ["text", "Text only"]]
CURSORS = [["", "Normal"], ["hand2", "Hand (link)"], ["xterm", "Text I-beam"],
           ["crosshair", "Crosshair"], ["watch", "Busy"], ["fleur", "Move"],
           ["question_arrow", "Help"]]
VALUE_TYPES = [["text", "Text"], ["int", "Whole number"], ["float", "Decimal number"]]

_font = lambda: P("font", "Font", "font", default_font(), group="Style")
_fg = lambda: P("fg", "Text color", "color", "", group="Style",
                help="Leave empty to use the theme's color.")
_bg = lambda: P("bg", "Background color", "color", "", group="Style",
                help="Leave empty to use the theme's color.")
_state = lambda: P("state", "State", "choice", "normal", STATE, group="Behavior")
_tooltip = lambda: P("tooltip", "Tooltip (hover help)", "text", "", group="Behavior",
                     help="Shown when the mouse rests on the widget.")
_cursor = lambda: P("cursor", "Mouse pointer", "choice", "", CURSORS, group="Behavior",
                    advanced=True)
_width_chars = lambda d=0: P("width", "Width (characters, 0 = auto)", "int", d,
                             {"min": 0, "max": 500}, group="Size")
_look = lambda cls: P("variant", "Look", "variant", "", cls, group="Style",
                      help="A ready-made style from the app's theme.")
_padding = lambda d: P("padding", "Inner padding", "int", d, {"min": 0, "max": 200}, group="Size")

# --------------------------------------------------------------------------
# MENU FORMAT (Window prop "menu"):
#   [ {"label": "File", "items": [
#         {"label": "Open", "shortcut": "Ctrl+O", "actions": [action, ...],
#          "hook": False},
#         {"separator": True},
#     ]}, ... ]
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Widget catalog
# --------------------------------------------------------------------------
# name        friendly name shown in the palette
# icon        a short unicode glyph for the palette
# category    palette section
# container   True if the node has "children"
# children    for containers whose children are special pages ("Tab")
# palette     False => not shown in palette
# parents     None = any grid container; otherwise list of allowed parent types
# events      list of [event_key, friendly label, verb used in hook names]
# value       what get()/set() of the generated API return/accept (help text)
# props       list of property specs
# help        one-liner shown in the palette tooltip / status bar

WIDGETS = {
    "Window": {
        "name": "Window", "icon": "▣", "category": "Windows", "container": True,
        "palette": False, "parents": [],
        "help": "A window of your app. The first window is the main window.",
        "events": [["on_open", "When the window opens", "opened"],
                   ["on_close", "When the window is closed", "closing"],
                   ["on_timer", "Every time the timer ticks", "tick"]],
        "props": [
            P("title", "Title", "text", "My App"),
            P("icon", "Window icon (.png/.gif/.ico)", "image", ""),
            P("width", "Width (pixels)", "int", 480, {"min": 100, "max": 4000}, group="Size"),
            P("height", "Height (pixels)", "int", 360, {"min": 80, "max": 4000}, group="Size"),
            P("min_width", "Smallest width (0 = any)", "int", 0, {"min": 0, "max": 4000},
              group="Size", advanced=True),
            P("min_height", "Smallest height (0 = any)", "int", 0, {"min": 0, "max": 4000},
              group="Size", advanced=True),
            _padding(12),
            P("resizable", "User can resize", "bool", True, group="Behavior"),
            P("start", "When it opens", "choice", "center",
              [["center", "Centered on screen"], ["normal", "Where the system puts it"],
               ["maximized", "Maximized"]], group="Behavior"),
            P("topmost", "Always on top", "bool", False, group="Behavior", advanced=True),
            P("timer_ms", "Timer every (milliseconds, 0 = off)", "int", 0,
              {"min": 0, "max": 3600000}, group="Behavior",
              help="Runs the 'Every time the timer ticks' actions repeatedly, "
                   "e.g. to refresh live readings."),
            _bg(),
            P("menu", "Menu bar", "menu", [], group="Menu"),
        ],
    },
    "Frame": {
        "name": "Panel", "icon": "▢", "category": "Containers", "container": True,
        "help": "A box that holds and groups other widgets.",
        "events": [],
        "props": [
            _look("TFrame"),
            _padding(6),
            P("relief", "Border style", "choice", "flat", RELIEFS, group="Style", advanced=True),
            P("borderwidth", "Border width", "int", 0, {"min": 0, "max": 20}, group="Style",
              advanced=True),
            _bg(),
        ],
    },
    "LabelFrame": {
        "name": "Group Box", "icon": "⬚", "category": "Containers", "container": True,
        "help": "A box with a border and a title that groups widgets together.",
        "events": [],
        "props": [
            P("text", "Title", "text", "Group"),
            _look("TLabelframe"),
            _padding(8),
            P("labelanchor", "Title position", "choice", "nw",
              [["nw", "Top left"], ["n", "Top center"], ["ne", "Top right"]], group="Style",
              advanced=True),
            _font(), _fg(), _bg(),
        ],
    },
    "Notebook": {
        "name": "Tabs", "icon": "▤", "category": "Containers", "container": True,
        "children": "Tab",
        "help": "A set of tabs. Each tab is its own area you can drop widgets into.",
        "events": [["on_tab_change", "When the user switches tab", "tab_changed"]],
        "value": "the title of the visible tab",
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
            _padding(10),
        ],
    },
    "Label": {
        "name": "Label", "icon": "A", "category": "Basic",
        "help": "Shows text (or a picture) that the user cannot edit.",
        "events": [["on_click", "When clicked", "clicked"]],
        "value": "its text",
        "props": [
            P("text", "Text", "multiline", "Label"),
            _look("TLabel"),
            P("image", "Picture (PNG/GIF)", "image", ""),
            P("compound", "Picture position", "choice", "left", COMPOUND, advanced=True),
            P("anchor", "Text position", "choice", "w", ANCHORS),
            P("justify", "Line alignment", "choice", "left",
              [["left", "Left"], ["center", "Center"], ["right", "Right"]], advanced=True),
            P("wraplength", "Wrap text at (pixels, 0 = never)", "int", 0, {"min": 0, "max": 4000},
              group="Size"),
            _width_chars(),
            _font(), _fg(), _bg(),
            _tooltip(), _cursor(),
        ],
    },
    "Button": {
        "name": "Button", "icon": "▭", "category": "Basic",
        "help": "A button the user can click to make things happen.",
        "events": [["command", "When clicked", "clicked"]],
        "default_hooks": ["command"],
        "value": "its text",
        "props": [
            P("text", "Text", "text", "Button"),
            _look("TButton"),
            P("image", "Picture (PNG/GIF)", "image", ""),
            P("compound", "Picture position", "choice", "left", COMPOUND, advanced=True),
            P("shortcut", "Keyboard shortcut", "shortcut", "", group="Behavior",
              help="e.g. Ctrl+R or F5. Pressing it is the same as clicking."),
            P("default", "Pressing Enter clicks it", "bool", False, group="Behavior",
              advanced=True),
            _width_chars(),
            _font(), _fg(), _bg(), _state(),
            _tooltip(), _cursor(),
        ],
    },
    "Entry": {
        "name": "Text Box", "icon": "⌨", "category": "Input",
        "help": "A one-line box where the user can type text or a number.",
        "events": [["on_enter", "When Enter is pressed", "submitted"],
                   ["on_change", "When the text changes", "changed"]],
        "value": "the typed text (converted to int/float if 'Value type' says so)",
        "props": [
            P("text", "Starting text", "text", ""),
            P("placeholder", "Hint shown when empty", "text", "",
              help="Grey hint text, e.g. 'Type your name'."),
            P("value_type", "Value type", "choice", "text", VALUE_TYPES,
              help="Numbers are checked and converted for your code."),
            P("password", "Hide what is typed (password)", "bool", False, group="Behavior"),
            P("justify", "Text alignment", "choice", "left",
              [["left", "Left"], ["center", "Center"], ["right", "Right"]], group="Behavior",
              advanced=True),
            P("state", "State", "choice", "normal", ENTRY_STATE, group="Behavior"),
            _width_chars(20),
            _font(), _fg(), _bg(),
            _tooltip(),
        ],
    },
    "Text": {
        "name": "Text Area", "icon": "≡", "category": "Input",
        "help": "A big box for several lines of text.",
        "events": [],
        "value": "all its text",
        "props": [
            P("text", "Starting text", "multiline", ""),
            P("width", "Width (characters)", "int", 30, {"min": 1, "max": 500}, group="Size"),
            P("height", "Height (lines)", "int", 6, {"min": 1, "max": 200}, group="Size"),
            P("wrap", "Wrap long lines", "choice", "word",
              [["word", "At words"], ["char", "At any letter"], ["none", "Don't wrap"]],
              group="Behavior"),
            P("scrollbar", "Show scrollbar", "bool", True, group="Behavior"),
            P("readonly", "Read only", "bool", False, group="Behavior"),
            P("monospace", "Fixed-width font (code/data)", "bool", False, group="Style"),
            _font(), _fg(), _bg(),
            _tooltip(),
        ],
    },
    "Checkbutton": {
        "name": "Checkbox", "icon": "☑", "category": "Input",
        "help": "A box the user can tick on or off.",
        "events": [["command", "When ticked / unticked", "toggled"]],
        "value": "True or False",
        "props": [
            P("text", "Text", "text", "Check me"),
            _look("TCheckbutton"),
            P("checked", "Ticked at start", "bool", False),
            _font(), _fg(), _bg(), _state(),
            _tooltip(),
        ],
    },
    "Radiobutton": {
        "name": "Option Button", "icon": "◉", "category": "Input",
        "help": "Round buttons where only one in the same group can be chosen.",
        "events": [["command", "When chosen", "chosen"]],
        "value": "the chosen option's value (shared by the whole group)",
        "props": [
            P("text", "Text", "text", "Option"),
            P("group", "Group name", "text", "group1",
              help="Option buttons with the same group name work together."),
            P("value", "Value for your code (empty = the text)", "text", "", advanced=True),
            P("selected", "Chosen at start", "bool", False),
            _font(), _fg(), _bg(), _state(),
            _tooltip(),
        ],
    },
    "Combobox": {
        "name": "Dropdown", "icon": "▾", "category": "Input",
        "help": "A drop-down list to pick one item from.",
        "events": [["on_select", "When an item is picked", "selected"]],
        "value": "the chosen text (or number, depending on 'Value type')",
        "props": [
            P("items", "Items (one per line)", "lines", ["Option 1", "Option 2", "Option 3"]),
            P("text", "Starting value", "text", "Option 1"),
            P("value_type", "Value type", "choice", "text", VALUE_TYPES),
            P("readonly", "Only allow items from the list", "bool", True, group="Behavior"),
            _width_chars(18),
            _font(), _state(),
            _tooltip(),
        ],
    },
    "Listbox": {
        "name": "List", "icon": "☰", "category": "Input",
        "help": "A list of items the user can select.",
        "events": [["on_select", "When an item is selected", "selected"],
                   ["on_double_click", "When an item is double-clicked", "double_clicked"]],
        "value": "the selected item (a list of items if many can be selected)",
        "props": [
            P("items", "Items (one per line)", "lines", ["Item 1", "Item 2", "Item 3"]),
            P("selectmode", "Selection", "choice", "browse",
              [["browse", "One item"], ["extended", "Many items"]], group="Behavior"),
            P("scrollbar", "Show scrollbar", "bool", True, group="Behavior"),
            P("width", "Width (characters)", "int", 20, {"min": 1, "max": 500}, group="Size"),
            P("height", "Height (rows)", "int", 6, {"min": 1, "max": 200}, group="Size"),
            _font(), _fg(), _bg(),
            _tooltip(),
        ],
    },
    "Spinbox": {
        "name": "Number Box", "icon": "⇅", "category": "Input",
        "help": "A box for picking a number with up/down arrows.",
        "events": [["command", "When the number changes", "changed"]],
        "value": "the number (int when 'Decimals' is 0, else float)",
        "props": [
            P("from_", "Smallest", "float", 0),
            P("to", "Largest", "float", 100),
            P("increment", "Step", "float", 1),
            P("decimals", "Decimals shown", "int", 0, {"min": 0, "max": 10}),
            P("value", "Starting value", "float", 0),
            P("wrap", "Wrap around at the ends", "bool", False, group="Behavior", advanced=True),
            _width_chars(8),
            _font(), _state(),
            _tooltip(),
        ],
    },
    "Scale": {
        "name": "Slider", "icon": "↔", "category": "Input",
        "help": "A slider for choosing a number.",
        "events": [["command", "When moved", "moved"]],
        "value": "the number (rounded to 'Step')",
        "props": [
            P("from_", "Smallest", "float", 0),
            P("to", "Largest", "float", 100),
            P("resolution", "Step", "float", 1),
            P("value", "Starting value", "float", 0),
            P("showvalue", "Show the number next to it", "bool", True),
            P("orient", "Direction", "choice", "horizontal", ORIENT),
            P("length", "Length (pixels)", "int", 160, {"min": 20, "max": 4000}, group="Size"),
            _state(),
            _tooltip(),
        ],
    },
    "FilePicker": {
        "name": "File Picker", "icon": "\U0001F4C1", "category": "Input",
        "help": "A path box with a Browse… button to pick a file or folder.",
        "events": [["on_change", "When a file is picked", "picked"]],
        "value": "the path as text",
        "props": [
            P("mode", "Pick", "choice", "open",
              [["open", "A file to open"], ["save", "A file to save"], ["folder", "A folder"]]),
            P("filetypes", "File types (one per line)", "lines", ["All files|*.*"],
              help="Format: Description|*.ext;*.ext2   e.g.  CSV files|*.csv"),
            P("text", "Starting path", "text", ""),
            P("button_text", "Button text", "text", "Browse…"),
            _width_chars(30),
            _state(),
            _tooltip(),
        ],
    },
    "Table": {
        "name": "Table", "icon": "▦", "category": "Data",
        "help": "Rows and columns of data, like a spreadsheet (read-only).",
        "events": [["on_select", "When a row is selected", "selected"],
                   ["on_double_click", "When a row is double-clicked", "double_clicked"]],
        "value": "the selected row as a dict {column: value} (set() takes a list of rows "
                 "or dicts)",
        "props": [
            P("columns", "Columns", "columns",
              [{"title": "Name", "width": 120, "anchor": "w"},
               {"title": "Value", "width": 100, "anchor": "w"}]),
            P("rows", "Starting rows", "rows", []),
            P("height", "Visible rows", "int", 8, {"min": 1, "max": 200}, group="Size"),
            P("selectmode", "Selection", "choice", "browse",
              [["browse", "One row"], ["extended", "Many rows"], ["none", "No selection"]],
              group="Behavior"),
            P("striped", "Striped rows", "bool", True, group="Style"),
            P("scrollbar", "Show scrollbar", "bool", True, group="Behavior"),
            _tooltip(),
        ],
    },
    "Console": {
        "name": "Output Console", "icon": "❯", "category": "Data",
        "help": "A read-only log box. Use it to show messages, command output or print().",
        "events": [],
        "value": "all its text (set() replaces it, log() adds a line)",
        "props": [
            P("width", "Width (characters)", "int", 60, {"min": 1, "max": 500}, group="Size"),
            P("height", "Height (lines)", "int", 10, {"min": 1, "max": 200}, group="Size"),
            P("capture_print", "Show print() output here", "bool", False, group="Behavior",
              help="Everything your code prints (and errors) appears in this console."),
            P("timestamps", "Add time to each line", "bool", False, group="Behavior"),
            P("max_lines", "Keep at most (lines, 0 = all)", "int", 5000,
              {"min": 0, "max": 1000000}, group="Behavior", advanced=True),
            _font(),
        ],
    },
    "Progressbar": {
        "name": "Progress Bar", "icon": "▰", "category": "Display",
        "help": "A bar that shows how far along something is.",
        "events": [],
        "value": "the value (number)",
        "props": [
            P("value", "Value", "float", 30),
            P("maximum", "Maximum", "float", 100),
            P("orient", "Direction", "choice", "horizontal", ORIENT),
            P("length", "Length (pixels)", "int", 200, {"min": 20, "max": 4000}, group="Size"),
            P("mode", "Mode", "choice", "determinate",
              [["determinate", "Shows a value"], ["indeterminate", "Busy animation"]],
              group="Behavior"),
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
        "help": "An empty area for pictures or drawing from your code.",
        "events": [["on_click", "When clicked", "clicked"]],
        "props": [
            P("width", "Width (pixels)", "int", 200, {"min": 1, "max": 4000}, group="Size"),
            P("height", "Height (pixels)", "int", 120, {"min": 1, "max": 4000}, group="Size"),
            P("bg", "Background color", "color", "", group="Style",
              help="Leave empty to use the theme's color."),
        ],
    },
}

PALETTE_CATEGORIES = ["Basic", "Input", "Data", "Display", "Containers"]
CATEGORY_TITLES = {"Basic": "Basic", "Input": "Input – things the user fills in",
                   "Data": "Data & engineering", "Display": "Display",
                   "Containers": "Containers & layout"}

# Containers whose children are laid out on a grid.  Notebook only holds tabs.
LAYOUT_CONTAINERS = ("Window", "Frame", "LabelFrame", "Tab")

# Grid placement of a node inside its parent container.
#   fill:   none | x | y | both          (stretch to fill the cell)
#   halign: left | center | right        (when not stretched horizontally)
#   valign: top  | center | bottom       (when not stretched vertically)
LAYOUT_DEFAULTS = {"row": 0, "col": 0, "rowspan": 1, "colspan": 1,
                   "fill": "none", "halign": "left", "valign": "center",
                   "padx": 6, "pady": 4}

LAYOUT_SPECS = [
    P("fill", "Stretch to fill space", "choice", "none",
      [["none", "Don't stretch"], ["x", "Stretch sideways"],
       ["y", "Stretch up/down"], ["both", "Stretch both ways"]], group="Layout"),
    P("halign", "Horizontal position", "choice", "left",
      [["left", "Left"], ["center", "Center"], ["right", "Right"]], group="Layout"),
    P("valign", "Vertical position", "choice", "center",
      [["top", "Top"], ["center", "Middle"], ["bottom", "Bottom"]], group="Layout"),
    P("colspan", "Columns wide", "int", 1, {"min": 1, "max": 50}, group="Layout"),
    P("rowspan", "Rows tall", "int", 1, {"min": 1, "max": 50}, group="Layout"),
    P("padx", "Space left/right", "int", 6, {"min": 0, "max": 200}, group="Layout"),
    P("pady", "Space above/below", "int", 4, {"min": 0, "max": 200}, group="Layout"),
]


def layout_mode(container):
    """'grid', or 'pages' for a Notebook (whose children are tabs)."""
    return "pages" if container["type"] == "Notebook" else "grid"


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
    if layout_mode(container) != "grid":
        return rows, cols
    for ch in container.get("children", []):
        lay = ch.get("layout", {})
        fill = lay.get("fill", "none")
        if fill in ("x", "both"):
            cols[lay.get("col", 0)] = 1
        if fill in ("y", "both"):
            rows[lay.get("row", 0)] = 1
    return rows, cols


# --------------------------------------------------------------------------
# Project settings
# --------------------------------------------------------------------------

SETTINGS_SPECS = [
    P("app_name", "App name", "text", "My App", group="App",
      help="Used for the file names and the class name of the generated code."),
    P("theme", "Theme", "theme", "modern_light", group="Look",
      help="The look of every widget in your app."),
    P("font_family", "Font", "choice", "", [[f, f or "(theme default)"] for f in FONT_FAMILIES],
      group="Look"),
    P("font_size", "Font size (0 = theme default)", "int", 0, {"min": 0, "max": 48}, group="Look"),
    P("dpi_aware", "Sharp text on high-resolution screens (Windows)", "bool", True, group="Look"),
    P("code_style", "Code layout", "choice", "split",
      [["split", "Two files: UI (regenerated) + your code (kept)"],
       ["single", "One file (everything together)"]], group="Code",
      help="With two files you can re-design the GUI any time without losing your own code."),
    P("backend_dir", "Folder with your Python code", "text", "", group="Code",
      help="Where 'Call a Python function' looks for your modules. Empty = next to the "
           "generated files."),
    P("threads", "Run Python functions in the background by default", "bool", True,
      group="Code", help="Keeps the window responsive during long calculations."),
]


def default_settings():
    return {p["key"]: copy.deepcopy(p["default"]) for p in SETTINGS_SPECS}


# --------------------------------------------------------------------------
# Action catalog
# --------------------------------------------------------------------------
# Param kinds:
#   text      string; may contain {widget_id} placeholders that are replaced
#             with the current value of that widget when the action runs.
#             Literal braces are written {{ and }}.
#   multiline same as text but multi-line
#   number    float
#   bool      True/False
#   widget    id of a widget; options={"types": [...]} restricts targets (None = any)
#   widgets   list of widget ids (ordered); options like widget
#   window    id of a window; options={"main": False} excludes the main window
#   tab       id of a Tab node
#   choice    options list of [value, label]
#   color     colour string ("" = leave unchanged)
#   code      python source (advanced)
#   module    python module name ("mybackend" or "pkg.module")  (call_function)
#   function  function name inside that module                   (call_function)
#   args      list of {"name": str, "source": "widget"|"value"|"all",
#                      "widget": id, "value": python literal text} (call_function)

TEXT_TARGETS = ["Label", "Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                "Combobox", "Spinbox", "LabelFrame", "Scale", "Progressbar", "Listbox",
                "FilePicker", "Console", "Table", "Tab"]
INPUT_TYPES = ["Entry", "Text", "Combobox", "Spinbox", "Listbox", "Checkbutton",
               "Radiobutton", "Scale", "FilePicker", "Table"]
VALUE_WIDGETS = INPUT_TYPES + ["Label", "Progressbar", "Console", "Notebook"]
NUMBER_TARGETS = ["Label", "Entry", "Spinbox", "Scale", "Progressbar"]
COLOR_TARGETS = ["Label", "Button", "Entry", "Text", "Checkbutton", "Radiobutton",
                 "Listbox", "Canvas", "Console"]
ENABLE_TARGETS = ["Button", "Entry", "Text", "Checkbutton", "Radiobutton", "Combobox",
                  "Spinbox", "Scale", "Listbox", "FilePicker", "Table"]
LOG_TARGETS = ["Console", "Text", "Listbox"]


def A(key, label, kind, default=None, options=None, help=""):
    return {"key": key, "label": label, "kind": kind, "default": default,
            "options": options, "help": help}


ACTIONS = {
    # ---------------------------------------------------------- your code
    "call_function": {
        "name": "Call a Python function", "category": "Your code",
        "summary": "Call {module}.{function}() → {result_summary}",
        "help": "Runs a function from your own Python file with values from the GUI, "
                "and shows what it returns.",
        "params": [
            A("module", "Python file / module", "module", "",
              help="The .py file that contains your function."),
            A("function", "Function", "function", ""),
            A("args", "Give it these values", "args", []),
            A("result", "Put the result into", "choice", "none",
              [["none", "Nothing (ignore it)"], ["widget", "One widget"],
               ["tuple", "Several widgets, in order (result is a tuple/list)"],
               ["dict", "Widgets with matching names (result is a dict)"]]),
            A("result_widget", "Result widget", "widget", "", {"types": TEXT_TARGETS}),
            A("result_widgets", "Result widgets (in order)", "widgets", [],
              {"types": TEXT_TARGETS}),
            A("background", "Run in the background (window stays responsive)", "bool", True),
            A("busy", "While running, show progress on", "widget", "",
              {"types": ["Progressbar"], "optional": True}),
            A("show_errors", "Show errors as a message", "bool", True),
        ],
    },
    "run_command": {
        "name": "Run a program / command", "category": "Your code",
        "summary": "Run “{command}”",
        "help": "Runs a command line (a script, an .exe, a batch file…) in the background "
                "and shows its output live.",
        "params": [
            A("command", "Command (can use {widget} values)", "text", "python --version"),
            A("cwd", "Working folder (empty = current)", "text", ""),
            A("output", "Show the output in", "widget", "", {"types": LOG_TARGETS,
                                                            "optional": True}),
            A("busy", "While running, show progress on", "widget", "",
              {"types": ["Progressbar"], "optional": True}),
            A("show_errors", "Show a message if it fails", "bool", True),
        ],
    },
    "run_code": {
        "name": "Run Python code (advanced)", "category": "Your code",
        "summary": "Run custom code",
        "help": "A few lines of Python. `self` is the app: self.get('name'), "
                "self.set('name', value), self.values() …",
        "params": [A("code", "Python code", "code",
                     "value = self.get('textbox1')\nprint(value)")],
    },
    # ---------------------------------------------------------- messages
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
    "log": {
        "name": "Write a line to a console", "category": "Messages",
        "summary": "Log “{text}” to {target}",
        "params": [
            A("target", "Console / text area / list", "widget", "", {"types": LOG_TARGETS}),
            A("text", "Text (can use {widget} values)", "text", ""),
        ],
    },
    # ---------------------------------------------------------- logic
    "check": {
        "name": "Only continue if…", "category": "Logic",
        "summary": "Only continue if {target} {condition} {value}",
        "params": [
            A("target", "Widget", "widget", "", {"types": VALUE_WIDGETS}),
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
            A("target", "Widget", "widget", "", {"types": ["Label", "Entry", "Text", "Console"]}),
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
                     {"types": ["Entry", "Text", "Listbox", "Combobox", "Label", "Spinbox",
                                "Console", "Table", "FilePicker"]})],
    },
    "reset_form": {
        "name": "Reset all inputs to their starting values", "category": "Widgets",
        "summary": "Reset the inputs in {window}",
        "params": [A("window", "Window", "window", "", {"main": True})],
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
        "params": [A("target", "Widget", "widget", "", {"types": ENABLE_TARGETS})],
    },
    "disable_widget": {
        "name": "Disable a widget", "category": "Widgets",
        "summary": "Disable {target}",
        "params": [A("target", "Widget", "widget", "", {"types": ENABLE_TARGETS})],
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
    # ---------------------------------------------------------- lists & tables
    "add_item": {
        "name": "Add an item to a list", "category": "Lists & tables",
        "summary": "Add “{text}” to {target}",
        "params": [
            A("target", "List or dropdown", "widget", "", {"types": ["Listbox", "Combobox"]}),
            A("text", "Item text", "text", ""),
        ],
    },
    "remove_selected": {
        "name": "Remove the selected item(s)", "category": "Lists & tables",
        "summary": "Remove selected item(s) from {target}",
        "params": [A("target", "List or table", "widget", "", {"types": ["Listbox", "Table"]})],
    },
    "add_row": {
        "name": "Add a row to a table", "category": "Lists & tables",
        "summary": "Add row “{values}” to {target}",
        "params": [
            A("target", "Table", "widget", "", {"types": ["Table"]}),
            A("values", "Values, separated by |  (can use {widget} values)", "text", ""),
        ],
    },
    "export_table": {
        "name": "Save a table to a CSV file", "category": "Lists & tables",
        "summary": "Save {target} to CSV",
        "params": [
            A("target", "Table", "widget", "", {"types": ["Table"]}),
            A("path", "File path (empty = ask the user)", "text", ""),
        ],
    },
    # ---------------------------------------------------------- windows
    "open_window": {
        "name": "Open another window", "category": "Windows",
        "summary": "Open window {window}",
        "params": [A("window", "Window", "window", "", {"main": False}),
                   A("modal", "Block the other windows until it's closed", "bool", False)],
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
    "timer_control": {
        "name": "Start / stop a window's timer", "category": "Windows",
        "summary": "{state} the timer of {window}",
        "params": [A("window", "Window", "window", "", {"main": True}),
                   A("state", "Do", "choice", "start", [["start", "Start"], ["stop", "Stop"]])],
    },
    "quit_app": {
        "name": "Quit the app", "category": "Windows",
        "summary": "Quit the app",
        "params": [],
    },
    # ---------------------------------------------------------- files & system
    "choose_file": {
        "name": "Let the user pick a file", "category": "Files & system",
        "summary": "Pick a file and put its path in {target}",
        "params": [
            A("mode", "Dialog", "choice", "open",
              [["open", "Open a file"], ["save", "Save as…"], ["folder", "Choose a folder"]]),
            A("target", "Put the path into", "widget", "", {"types": TEXT_TARGETS}),
        ],
    },
    "load_file": {
        "name": "Load a text file into a widget", "category": "Files & system",
        "summary": "Load file {path} into {target}",
        "params": [
            A("path", "File path (can use {widget} values)", "text", ""),
            A("target", "Widget", "widget", "", {"types": ["Text", "Entry", "Label", "Console"]}),
        ],
    },
    "save_file": {
        "name": "Save a widget's text to a file", "category": "Files & system",
        "summary": "Save {source} to file {path}",
        "params": [
            A("path", "File path (can use {widget} values)", "text", ""),
            A("source", "Widget", "widget", "", {"types": ["Text", "Entry", "Label", "Listbox",
                                                          "Console"]}),
        ],
    },
    "open_url": {
        "name": "Open a web page or file", "category": "Files & system",
        "summary": "Open {url}",
        "params": [A("url", "Address or file path (can use {widget} values)", "text",
                     "https://www.python.org")],
    },
}

ACTION_CATEGORIES = ["Your code", "Messages", "Logic", "Widgets", "Lists & tables",
                     "Windows", "Files & system"]

PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")


def new_action(action_type):
    spec = ACTIONS[action_type]
    return {"type": action_type,
            "params": {p["key"]: copy.deepcopy(p["default"]) for p in spec["params"]}}


def _upgrade_action(action):
    spec = ACTIONS.get(action.get("type"))
    if spec is None:
        return
    params = action.setdefault("params", {})
    for p in spec["params"]:
        params.setdefault(p["key"], copy.deepcopy(p["default"]))


def action_summary(action, project=None):
    """Human readable one-liner for an action, e.g. 'Set label1 to "Hi"'."""
    spec = ACTIONS.get(action.get("type"))
    if not spec:
        return "Unknown action"
    params = action.get("params", {})
    atype = action.get("type")

    def fmt(key):
        v = params.get(key, "")
        if isinstance(v, list):
            if project is not None:
                return ", ".join(x if project.find(x) else "%s (missing!)" % x
                                 for x in v if isinstance(x, str)) or "?"
            return ", ".join(str(x) for x in v) or "?"
        if project is not None and isinstance(v, str) and v:
            node = project.find(v)
            if key in ("target", "source", "window", "tab", "output", "busy",
                       "result_widget"):
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
        if key == "value" and atype == "check" and not needs_value:
            return ""
        if key == "state" and atype == "set_checked":
            return {"tick": "Tick", "untick": "Untick", "toggle": "Toggle"}.get(
                params.get("state"), "Tick")
        if key == "state" and atype == "timer_control":
            return "Start" if params.get("state") != "stop" else "Stop"
        if key == "result_summary":
            mode = params.get("result")
            if mode == "widget":
                return fmt("result_widget")
            if mode == "tuple":
                return fmt("result_widgets")
            if mode == "dict":
                return "widgets by name"
            return "ignore result"
        if key == "condition":
            for val, lab in next(p for p in spec["params"] if p["key"] == "condition")["options"]:
                if val == params.get("condition"):
                    return lab
        if key == "module" and atype == "call_function":
            return params.get("module") or "?"
        return fmt(key) or "?"

    return re.sub(r"\{(\w+)\}", repl, spec["summary"]).strip()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def is_container(node_or_type):
    t = node_or_type if isinstance(node_or_type, str) else node_or_type["type"]
    return WIDGETS[t].get("container", False)


# names that would clash with attributes of tk.Tk / the generated App class
RESERVED_NAMES = {"self", "app", "tk", "ttk", "messagebox", "filedialog", "simpledialog",
                  "title", "geometry", "destroy", "quit", "update", "after", "config",
                  "configure", "bind", "children", "master", "widgets", "main", "run",
                  "mainloop", "winfo", "grid", "pack", "place", "focus", "event",
                  "protocol", "menu", "keys", "get", "set", "image", "images", "values",
                  "set_values", "log", "on", "style", "theme", "vars", "call_in_gui",
                  "run_in_background", "windows", "os", "sys", "threading", "queue",
                  "json", "csv", "subprocess", "webbrowser", "time", "re", "widget",
                  "show_info", "show_warning", "show_error", "ask_yes_no", "open_window",
                  "close_window", "px", "SCALE"}
try:  # every method/attribute of tk.Tk would clash with the generated App class
    import tkinter as _tk
    RESERVED_NAMES |= {n for n in dir(_tk.Tk) if not n.startswith("_")}
    del _tk
except ImportError:  # pragma: no cover
    pass


def valid_identifier(name):
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name) \
        and not name.startswith("_") and name not in RESERVED_NAMES


def hook_name(node, event_key):
    """Name of the user's own method that an event calls, e.g.
    button1 + command -> 'on_button1_clicked', window on_open -> 'on_main_window_opened'."""
    for e in WIDGETS[node["type"]]["events"]:
        if e[0] == event_key:
            return "on_%s_%s" % (node["id"], e[2])
    return "on_%s_%s" % (node["id"], event_key)


def menu_hook_name(menu_label, item_label):
    def slug(s):
        s = re.sub(r"\W+", "_", s.lower()).strip("_")
        return s or "item"
    return "on_menu_%s_%s" % (slug(menu_label), slug(item_label))


def make_node(wtype, node_id):
    spec = WIDGETS[wtype]
    node = {"id": node_id, "type": wtype,
            "props": {p["key"]: copy.deepcopy(p["default"]) for p in spec["props"]},
            "layout": dict(LAYOUT_DEFAULTS),
            "events": {e[0]: [] for e in spec["events"]},
            "hooks": list(spec.get("default_hooks", []))}
    if spec.get("container"):
        node["children"] = []
    return node


_V1_TYPE_RENAMES = {}


def _upgrade_node(node):
    """Fill in missing props/layout/events (for files made by older versions)."""
    node["type"] = _V1_TYPE_RENAMES.get(node.get("type"), node.get("type"))
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
    node.setdefault("hooks", [])
    for lst in ev.values():
        for a in lst:
            _upgrade_action(a)
    if node["type"] == "Window":
        for m in props.get("menu") or []:
            for it in m.get("items", []):
                for a in it.get("actions", []):
                    _upgrade_action(a)
    if spec.get("container"):
        node.setdefault("children", [])
        for ch in node["children"]:
            _upgrade_node(ch)


def _upgrade_project(data):
    """Bring a project dict (any older version) up to FORMAT_VERSION."""
    version = data.get("version", 1)
    settings = data.setdefault("settings", {})
    if version < 2:
        # v1 projects were built with classic tk widgets and the native look
        settings.setdefault("theme", "native")
        settings.setdefault("code_style", "single")
        if data.get("app_name"):
            settings.setdefault("app_name", data["app_name"])
        for w in data.get("windows", []):
            w.setdefault("props", {}).setdefault("start", "normal")
            w["props"].setdefault("padding", 10)
    for k, v in default_settings().items():
        settings.setdefault(k, v)
    data.pop("app_name", None)
    data["version"] = FORMAT_VERSION
    for w in data["windows"]:
        _upgrade_node(w)
    return data


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
            self.data = {"version": FORMAT_VERSION, "settings": default_settings(),
                         "windows": []}
            self.add_window(title="My App")
        else:
            self.data = _upgrade_project(data)

    # ---- serialisation --------------------------------------------------
    def to_json(self):
        return json.dumps(self.data, indent=2)

    @classmethod
    def from_json(cls, text):
        data = json.loads(text)
        if not isinstance(data, dict) or "windows" not in data or not data["windows"]:
            raise ValueError("This is not a TkBuilder project file.")
        if data.get("version", 1) > FORMAT_VERSION:
            raise ValueError("This project was made with a newer TkBuilder.")
        return cls(data)

    def snapshot(self):
        return json.dumps(self.data)

    def restore(self, snap):
        self.data = json.loads(snap)

    # ---- settings --------------------------------------------------------
    @property
    def settings(self):
        return self.data["settings"]

    @property
    def app_name(self):
        return self.settings.get("app_name") or "My App"

    def module_basename(self):
        """File-name friendly app name, e.g. 'My Test App' -> 'my_test_app'."""
        name = re.sub(r"\W+", "_", self.app_name.lower()).strip("_") or "my_app"
        if name[0].isdigit():
            name = "app_" + name
        return name

    def class_name(self):
        """CamelCase class name, e.g. 'my test app' -> 'MyTestApp'."""
        parts = re.findall(r"[A-Za-z0-9]+", self.app_name)
        name = "".join(p[:1].upper() + p[1:] for p in parts) or "MyApp"
        if name[0].isdigit():
            name = "App" + name
        return name

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

    def unique_id(self, base, extra=()):
        base = re.sub(r"\W", "", base.lower()) or "widget"
        if base[0].isdigit():
            base = "w" + base
        ids = self.all_ids() | set(extra)
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
    _ID_BASES = {"Checkbutton": "checkbox", "Radiobutton": "option", "Entry": "textbox",
                 "Text": "textarea", "Combobox": "dropdown", "Listbox": "list",
                 "Spinbox": "numberbox", "Scale": "slider", "Progressbar": "progress",
                 "Frame": "panel", "LabelFrame": "group", "Notebook": "tabs",
                 "Canvas": "drawing", "FilePicker": "filepicker"}

    def new_node(self, wtype, extra_ids=()):
        """Create (but don't insert) a fresh node with defaults and a unique id.
        Containers get sensible starting content (Tabs get two tabs, Split View
        two panes)."""
        base = self._ID_BASES.get(wtype, wtype.lower())
        reserved = set(extra_ids)
        node = make_node(wtype, self.unique_id(base, reserved))
        reserved.add(node["id"])
        if wtype in ("Separator", "Progressbar", "FilePicker", "Console"):
            node["layout"]["fill"] = "x"
        if wtype in ("Notebook", "Text", "Canvas", "Table", "Console"):
            node["layout"]["fill"] = "both"
        if wtype == "Notebook":
            for i in (1, 2):
                tab = make_node("Tab", self.unique_id("tab", reserved))
                reserved.add(tab["id"])
                tab["props"]["text"] = "Tab %d" % i
                node["children"].append(tab)
        if wtype in ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame"):
            # make the default text distinguishable, e.g. "Button 2"
            num = re.sub(r"^\D+", "", node["id"])
            if num and num != "1":
                node["props"]["text"] = "%s %s" % (node["props"]["text"], num)
        return node

    def add_window(self, title=None):
        wid = "main_window" if not self.windows else self.unique_id("window")
        w = make_node("Window", wid)
        if self.windows:
            w["props"]["title"] = title or "Window %d" % (len(self.windows) + 1)
            w["props"]["width"], w["props"]["height"] = 360, 260
        else:
            w["props"]["title"] = title or "My App"
        self.windows.append(w)
        return w

    def add_tab(self, notebook_id, title=None):
        nb = self.find(notebook_id)
        page_type = WIDGETS[nb["type"]].get("children", "Tab")
        page = make_node(page_type, self.unique_id(page_type.lower()))
        if page_type == "Tab":
            page["props"]["text"] = title or "Tab %d" % (len(nb["children"]) + 1)
        nb["children"].append(page)
        return page

    add_page = add_tab

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
        if layout_mode(container) != "grid" or not container.get("children"):
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
        # page containers only hold their pages; windows/pages are never children
        return container["type"] != "Notebook"

    def place(self, node, container_id, row=0, col=0, mode="cell"):
        """Insert `node` (new or detached) into the container.

        mode:
          "cell"   put it at (row, col); if the cell is taken, a new row is
                   inserted at `row` first.
          "above"/"below"/"left"/"right"
                   insert a new row/column next to (row, col) and put it there.
          "index"  (Tabs) insert the tab at position `row`.
        Tabs are always appended to a Tabs widget unless mode is "index".
        Returns the node.
        """
        container = self.find(container_id)
        if container is None:
            raise ValueError("No such container: %s" % container_id)
        if not self.can_contain(container, node["type"]):
            raise ValueError("A %s can't go inside a %s" % (
                WIDGETS[node["type"]]["name"], WIDGETS[container["type"]]["name"]))
        if layout_mode(container) == "pages":
            if node["type"] == "Tab" and node["props"].get("text") == "Tab":
                node["props"]["text"] = "Tab %d" % (len(container["children"]) + 1)
            if mode == "index":
                container["children"].insert(max(0, min(row, len(container["children"]))), node)
            else:
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

    def move(self, node_id, container_id, row=0, col=0, mode="cell"):
        node = self.find(node_id)
        if node is None or node["type"] == "Window":
            return None
        if node_id == container_id or self.is_descendant(container_id, node_id):
            raise ValueError("Can't put a widget inside itself.")
        target = self.find(container_id)
        if target is None or not self.can_contain(target, node["type"]):
            raise ValueError("A %s can't go there." % WIDGETS[node["type"]]["name"])
        old_parent = self.parent_of(node_id)
        if old_parent is not None and old_parent["id"] == container_id:
            kids = old_parent["children"]
            if layout_mode(old_parent) == "pages":
                kids.remove(node)
                kids.insert(max(0, min(row or 0, len(kids))), node)
                return node
            # same grid: remove without compacting so (row, col) stay valid
            kids.remove(node)
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
        left as-is (they show as '(missing!)' and the generator skips them)."""
        if self.windows and node_id == self.main_window["id"]:
            raise ValueError("The main window can't be deleted.")
        for w in list(self.windows):
            if w["id"] == node_id:
                self.windows.remove(w)
                return
        parent = self.parent_of(node_id)
        if parent is not None:
            if layout_mode(parent) == "pages" and len(parent["children"]) <= 1:
                raise ValueError("Tabs need at least one tab.")
            self.detach(node_id)

    # ---- copy / paste / duplicate ---------------------------------------
    def copy_nodes(self, node_ids):
        """Serialise nodes (with their content) for the clipboard."""
        nodes = [copy.deepcopy(self.find(i)) for i in node_ids if self.find(i)]
        nodes = [n for n in nodes if n["type"] != "Window"]
        return json.dumps({"tkbuilder_clip": 1, "nodes": nodes})

    @staticmethod
    def is_clip(text):
        try:
            d = json.loads(text)
            return isinstance(d, dict) and d.get("tkbuilder_clip") == 1
        except (ValueError, TypeError):
            return False

    def _fresh_copy(self, node, reserved):
        """Deep copy with new unique ids; refs inside the copy are remapped."""
        clone = copy.deepcopy(node)
        mapping = {}
        for n in self.walk(clone):
            base = re.sub(r"\d+$", "", n["id"]) or "widget"
            i = 1
            while "%s%d" % (base, i) in reserved:
                i += 1
            mapping[n["id"]] = "%s%d" % (base, i)
            reserved.add(mapping[n["id"]])
        for n in self.walk(clone):
            n["id"] = mapping[n["id"]]
        for n in self.walk(clone):
            self._rewrite_refs_in_node(n, mapping)
        _upgrade_node(clone)
        return clone

    def paste(self, text, container_id, row=None, col=None, mode="below"):
        """Paste clipboard nodes into a container. Without row/col they go into
        new rows at the bottom; with row/col they go next to that cell (mode).
        Returns the new nodes."""
        data = json.loads(text)
        container = self.find(container_id)
        reserved = set(self.all_ids())
        out = []
        for n in data.get("nodes", []):
            if not self.can_contain(container, n["type"]):
                continue
            clone = self._fresh_copy(n, reserved)
            if layout_mode(container) == "pages":
                self.place(clone, container_id, mode="append")
            elif row is None:
                r, c = self.next_free_cell(container)
                self.place(clone, container_id, r, c)
            else:
                self.place(clone, container_id, row, col or 0, mode)
                row = clone["layout"]["row"]
                col = clone["layout"]["col"]
                mode = "below"
            out.append(clone)
        return out

    def duplicate(self, node_id):
        """Copy a node (with fresh ids) and place it just below the original."""
        node = self.find(node_id)
        if node is None or node["type"] == "Window":
            return None
        clone = self._fresh_copy(node, set(self.all_ids()))
        parent = self.parent_of(node_id)
        if layout_mode(parent) == "pages":
            parent["children"].insert(parent["children"].index(node) + 1, clone)
            return clone
        lay = node["layout"]
        return self.place(clone, parent["id"], lay["row"] + lay["rowspan"] - 1, lay["col"],
                          "below")

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
                    if p["kind"] in ("widget", "window", "tab") and isinstance(v, str):
                        params[p["key"]] = mapping.get(v, v)
                    elif p["kind"] == "widgets" and isinstance(v, list):
                        params[p["key"]] = [mapping.get(x, x) for x in v]
                    elif p["kind"] == "args" and isinstance(v, list):
                        for arg in v:
                            if arg.get("widget"):
                                arg["widget"] = mapping.get(arg["widget"], arg["widget"])
                            if isinstance(arg.get("value"), str):
                                arg["value"] = PLACEHOLDER_RE.sub(
                                    lambda m: "{%s}" % mapping.get(m.group(1), m.group(1)),
                                    arg["value"])
                    elif p["kind"] in ("text", "multiline") and isinstance(v, str):
                        params[p["key"]] = PLACEHOLDER_RE.sub(
                            lambda m: "{%s}" % mapping.get(m.group(1), m.group(1)), v)

    def references_to(self, node_id):
        """List of nodes that have an action referring to node_id."""
        out = []
        for n in self.walk():
            for lst in self.iter_action_lists(n):
                if any(node_id in action_refs(act) for act in lst):
                    out.append(n)
                    break
        return out


def action_refs(action):
    """Set of node ids an action refers to (targets and {placeholders})."""
    refs = set()
    spec = ACTIONS.get(action.get("type"), {"params": []})
    params = action.get("params", {})
    for p in spec["params"]:
        v = params.get(p["key"])
        if p["kind"] in ("widget", "window", "tab") and isinstance(v, str) and v:
            refs.add(v)
        elif p["kind"] == "widgets" and isinstance(v, list):
            refs.update(x for x in v if isinstance(x, str))
        elif p["kind"] == "args" and isinstance(v, list):
            for arg in v:
                if arg.get("source") == "widget" and arg.get("widget"):
                    refs.add(arg["widget"])
                if isinstance(arg.get("value"), str):
                    refs.update(PLACEHOLDER_RE.findall(arg["value"]))
        elif p["kind"] in ("text", "multiline") and isinstance(v, str):
            refs.update(PLACEHOLDER_RE.findall(v))
    return refs
