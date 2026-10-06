"""
Shared mapping from design nodes to real tkinter widgets/options (v2).

Both the designer (live preview) and the code generator use these functions,
so what you see while designing is what the generated program shows.

Most widgets are themed ttk widgets.  Classic tk widgets are only used where
ttk has no equivalent (Text, Listbox, Canvas); those get explicit colours from
apptheme.tk_colors() so they match the theme.

Context ("ctx") passed to most functions:
    {"theme": theme key, "size": base font size (int)}
Use make_ctx(project_settings) to build it.

Fonts: a font tuple whose first item is apptheme.FAMILY ("<family>") means
"the app's font family".  The designer substitutes the real family; the code
generator emits a variable (e.g. FONT_FAMILY).
"""

import apptheme
from apptheme import FAMILY

# Which class creates each node type: (module, class name, ttk style class)
#   module: "tk", "ttk" or "composite" (built by a helper in both designer
#   and generated code - see COMPOSITES below)
WIDGET_CLASS = {
    "Window": ("tk", "Toplevel", None),
    "Frame": ("ttk", "Frame", "TFrame"),
    "LabelFrame": ("ttk", "Labelframe", "TLabelframe"),
    "Notebook": ("ttk", "Notebook", "TNotebook"),
    "Tab": ("ttk", "Frame", "TFrame"),
    "Label": ("ttk", "Label", "TLabel"),
    "Button": ("ttk", "Button", "TButton"),
    "Entry": ("ttk", "Entry", "TEntry"),
    "Text": ("tk", "Text", None),
    "Checkbutton": ("ttk", "Checkbutton", "TCheckbutton"),
    "Radiobutton": ("ttk", "Radiobutton", "TRadiobutton"),
    "Combobox": ("ttk", "Combobox", "TCombobox"),
    "Listbox": ("tk", "Listbox", None),
    "Spinbox": ("ttk", "Spinbox", "TSpinbox"),
    "Scale": ("ttk", "Scale", "Horizontal.TScale"),
    "FilePicker": ("composite", "FilePicker", None),
    "Table": ("ttk", "Treeview", "Treeview"),
    "Console": ("tk", "Text", None),
    "Progressbar": ("ttk", "Progressbar", "Horizontal.TProgressbar"),
    "Separator": ("ttk", "Separator", "TSeparator"),
    "Canvas": ("tk", "Canvas", None),
}

# Composite widgets (a frame with parts).  The generated code defines small
# helper classes with these names; the designer builds equivalent previews.
#   FilePicker  : ttk.Frame + ttk.Entry (.entry) + ttk.Button (.button)
# Text, Listbox, Table and Console with a scrollbar are wrapped in a
# ttk.Frame + ttk.Scrollbar ("scroll wrapper"); the wrapper is what gets placed.
SCROLLABLE = ("Text", "Listbox", "Table", "Console")

MONO_FAMILY = "Consolas"


def make_ctx(settings):
    theme = settings.get("theme") or "native"
    size = int(settings.get("font_size") or 0) or apptheme.default_font_size(theme)
    return {"theme": theme, "size": size}


def font_value(font, ctx):
    """Font dict -> font tuple (may contain FAMILY), or None for 'use the default'."""
    if not font:
        return None
    family = font.get("family") or ""
    size = int(font.get("size") or 0)
    styles = [s for s in ("bold", "italic", "underline") if font.get(s)]
    if not family and not size and not styles:
        return None
    t = (family or FAMILY, size or ctx["size"])
    if styles:
        t += (" ".join(styles),)
    return t


def _num(v):
    """Return int when the float is whole (looks nicer in generated code)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else f


def style_class(node):
    """The ttk style class of a node (e.g. 'TButton'), or None for tk widgets."""
    cls = WIDGET_CLASS[node["type"]][2]
    if node["type"] in ("Scale", "Progressbar"):
        o = node["props"].get("orient") or "horizontal"
        cls = ("Vertical." if o == "vertical" else "Horizontal.") + cls.split(".", 1)[1]
    return cls


def style_name(node):
    """Style to pass as style=... for a ttk widget, or None for the default.

    Variant 'Accent' -> 'Accent.TButton'.  When the node has its own colours
    or font, a personal style '<id>.<variant>.<class>' is used, configured by
    custom_style_ops()."""
    cls = style_class(node)
    if cls is None:
        return None
    variant = node["props"].get("variant") or ""
    base = (variant + "." + cls) if variant else cls
    own_font = node["type"] in ("Label", "Button", "Checkbutton", "Radiobutton") \
        and font_value(node["props"].get("font"), {"size": 10}) is not None
    if _style_overrides(node) or own_font:
        return node["id"] + "." + base
    return base if variant else None


def _style_overrides(node):
    """{style option: value} for the node's own colours (ttk widgets only)."""
    t = node["type"]
    p = node["props"]
    if WIDGET_CLASS[t][0] != "ttk":
        return {}
    o = {}
    fg, bg = p.get("fg"), p.get("bg")
    if t in ("Entry",):
        if fg:
            o["foreground"] = fg
        if bg:
            o["fieldbackground"] = bg
        return o
    if t in ("Frame", "Tab"):
        if bg:
            o["background"] = bg
        return o
    if fg:
        o["foreground"] = fg
    if bg:
        o["background"] = bg
    if t in ("Label", "Button", "Checkbutton", "Radiobutton"):
        return o
    return o


def custom_style_ops(node, ctx):
    """Style ops (same format as apptheme.style_ops) for a node's own look."""
    name = style_name(node)
    over = _style_overrides(node)
    t = node["type"]
    ops = []
    font = font_value(node["props"].get("font"), ctx) if t in (
        "Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame") else None
    if t == "LabelFrame":
        # the title is drawn by the '<style>.Label' sub-style
        lab = {}
        if node["props"].get("fg"):
            lab["foreground"] = node["props"]["fg"]
        if node["props"].get("bg"):
            lab["background"] = node["props"]["bg"]
            over = {"background": node["props"]["bg"]}
        else:
            over = {}
        if font:
            lab["font"] = font
        if lab or over:
            base = node["id"] + "." + ((node["props"].get("variant") + ".")
                                       if node["props"].get("variant") else "") + "TLabelframe"
            if over:
                ops.append(("configure", base, over))
            if lab:
                ops.append(("configure", base + ".Label", lab))
        return ops
    if font:
        over = dict(over, font=font)
    if over and name:
        ops.append(("configure", name, over))
        if "background" in over and t in ("Button",):
            ops.append(("map", name, {"background": [("active", over["background"]),
                                                     ("pressed", over["background"])]}))
        if "background" in over and t in ("Checkbutton", "Radiobutton"):
            ops.append(("map", name, {"background": [("active", over["background"])]}))
    return ops


def labelframe_style_name(node):
    """LabelFrames with their own colours/font use '<id>.<variant>.TLabelframe'."""
    p = node["props"]
    if p.get("fg") or p.get("bg") or font_value(p.get("font"), {"size": 10}):
        return node["id"] + "." + ((p.get("variant") + ".") if p.get("variant") else "") \
            + "TLabelframe"
    return (p["variant"] + ".TLabelframe") if p.get("variant") else None


def widget_options(node, ctx):
    """Ordered list of (option, value) for the main widget's constructor.

    Values are plain python values (str, int, float, bool, tuple, or font
    tuples containing FAMILY).  'style' is included for ttk widgets that
    need one.  Things that aren't constructor options (starting text, ticks,
    items, rows, images, variables, scrollbars, tooltips, shortcuts, ...)
    are described by initial_state().  Composite widgets return the options
    of their main part (see COMPOSITES comment).
    """
    t = node["type"]
    p = node["props"]
    theme = ctx["theme"]
    o = []

    def add(key, value, skip=("", None)):
        if value in skip:
            return
        o.append((key, value))

    def tk_colors(kind, fg_key="fg", bg_key="bg"):
        cols = dict(apptheme.tk_colors(theme, kind))
        if p.get(fg_key):
            cols["fg"] = p[fg_key]
            if kind != "Listbox":
                cols["insertbackground"] = p[fg_key]
        if p.get(bg_key):
            cols["bg"] = p[bg_key]
        if kind == "Listbox":
            cols.pop("insertbackground", None)
        for k in ("bg", "fg", "insertbackground", "selectbackground", "selectforeground",
                  "highlightthickness", "highlightbackground", "highlightcolor", "relief",
                  "borderwidth"):
            if k in cols:
                o.append((k, cols[k]))

    style = labelframe_style_name(node) if t == "LabelFrame" else style_name(node)

    if t == "Window":
        cols = apptheme.tk_colors(theme, "Window")
        bg = p.get("bg") or cols.get("bg")
        add("bg", bg)
    elif t in ("Frame", "Tab"):
        pad = int(p.get("padding") or 0)
        add("padding", pad, (0, None))
        if t == "Frame":
            add("width", int(p.get("width") or 0), (0,))
            add("height", int(p.get("height") or 0), (0,))
            add("relief", p.get("relief"), ("", None, "flat"))
            add("borderwidth", int(p.get("borderwidth") or 0), (0,))
        add("style", style)
    elif t == "LabelFrame":
        add("text", p.get("text"))
        add("padding", int(p.get("padding") or 0), (0,))
        add("labelanchor", p.get("labelanchor"), ("", None, "nw"))
        add("style", style)
    elif t == "Notebook":
        add("width", int(p.get("width") or 0), (0,))
        add("height", int(p.get("height") or 0), (0,))
    elif t == "Label":
        add("text", p.get("text"))
        add("anchor", p.get("anchor"), ("", None))
        add("justify", p.get("justify"), ("", None, "left"))
        add("wraplength", int(p.get("wraplength") or 0), (0,))
        add("width", int(p.get("width") or 0), (0,))
        if p.get("image"):
            add("compound", p.get("compound") or "left")
        add("cursor", p.get("cursor"))
        add("style", style)
    elif t == "Button":
        add("text", p.get("text"))
        add("width", int(p.get("width") or 0), (0,))
        if p.get("image"):
            add("compound", p.get("compound") or "left")
        if p.get("default"):
            o.append(("default", "active"))
        add("state", p.get("state"), ("", None, "normal"))
        add("cursor", p.get("cursor"))
        add("style", style)
    elif t == "Entry":
        add("width", int(p.get("width") or 0), (0,))
        add("justify", p.get("justify"), ("", None, "left"))
        if p.get("password"):
            o.append(("show", "•"))
        add("font", font_value(p.get("font"), ctx))
        add("style", style)
    elif t in ("Text", "Console"):
        add("width", int(p.get("width") or (60 if t == "Console" else 30)))
        add("height", int(p.get("height") or (10 if t == "Console" else 6)))
        if t == "Text":
            add("wrap", p.get("wrap"), ("", None, "char"))
            add("undo", True)
            # explicit app font (Tk's default for Text is a fixed-width font)
            font = font_value(p.get("font"), ctx) or (FAMILY, ctx["size"])
            if p.get("monospace"):
                font = (MONO_FAMILY,) + font[1:]
            add("font", font)
            tk_colors("Text")
        else:
            add("wrap", "char")
            font = font_value(p.get("font"), ctx) or (MONO_FAMILY, ctx["size"])
            if font[0] == FAMILY:
                font = (MONO_FAMILY,) + font[1:]
            add("font", font)
            tk_colors("Console")
    elif t == "Checkbutton":
        add("text", p.get("text"))
        add("state", p.get("state"), ("", None, "normal"))
        add("style", style)
    elif t == "Radiobutton":
        add("text", p.get("text"))
        add("value", radio_value(node))
        add("state", p.get("state"), ("", None, "normal"))
        add("style", style)
    elif t == "Combobox":
        o.append(("values", tuple(p.get("items") or ())))
        add("width", int(p.get("width") or 0), (0,))
        add("font", font_value(p.get("font"), ctx))
        state = p.get("state") or "normal"
        if state == "normal" and p.get("readonly"):
            state = "readonly"
        add("state", state, ("", None, "normal"))
    elif t == "Listbox":
        add("width", int(p.get("width") or 20))
        add("height", int(p.get("height") or 6))
        add("selectmode", p.get("selectmode"), ("", None, "browse"))
        o.append(("exportselection", False))
        o.append(("activestyle", "none"))
        add("font", font_value(p.get("font"), ctx))
        tk_colors("Listbox")
    elif t == "Spinbox":
        o.append(("from_", _num(p.get("from_"))))
        o.append(("to", _num(p.get("to"))))
        add("increment", _num(p.get("increment") or 1), (1,))
        dec = int(p.get("decimals") or 0)
        if dec:
            o.append(("format", "%%.%df" % dec))
        if p.get("wrap"):
            o.append(("wrap", True))
        add("width", int(p.get("width") or 0), (0,))
        add("font", font_value(p.get("font"), ctx))
    elif t == "Scale":
        o.append(("from_", _num(p.get("from_"))))
        o.append(("to", _num(p.get("to"))))
        add("orient", p.get("orient") or "horizontal", ("", None, "horizontal"))
        add("length", int(p.get("length") or 0), (0,))
    elif t == "Progressbar":
        add("orient", p.get("orient") or "horizontal", ("", None, "horizontal"))
        add("length", int(p.get("length") or 0), (0,))
        add("mode", p.get("mode"), ("", None, "determinate"))
        o.append(("maximum", _num(p.get("maximum") or 100)))
        o.append(("value", _num(p.get("value") or 0)))
    elif t == "Separator":
        add("orient", p.get("orient") or "horizontal")
    elif t == "Canvas":
        add("width", int(p.get("width") or 0), (0,))
        add("height", int(p.get("height") or 0), (0,))
        cols = apptheme.tk_colors(theme, "Canvas")
        add("bg", p.get("bg") or cols.get("bg") or "#ffffff")
        o.append(("highlightthickness", cols.get("highlightthickness", 0)))
        if cols.get("highlightbackground"):
            o.append(("highlightbackground", cols["highlightbackground"]))
    elif t == "Table":
        cols = table_columns(node)
        o.append(("columns", tuple("c%d" % i for i in range(len(cols)))))
        o.append(("show", "headings"))
        add("height", int(p.get("height") or 8))
        # always explicit: a Treeview's own default is "extended"
        o.append(("selectmode", p.get("selectmode") or "browse"))
    elif t == "FilePicker":
        add("width", int(p.get("width") or 30))
    return o


def radio_value(node):
    return node["props"].get("value") or node["props"].get("text") or node["id"]


def table_columns(node):
    cols = node["props"].get("columns") or []
    out = []
    for c in cols:
        if isinstance(c, str):
            c = {"title": c}
        out.append({"title": c.get("title", ""), "width": int(c.get("width") or 100),
                    "anchor": c.get("anchor") or "w"})
    return out or [{"title": "Value", "width": 100, "anchor": "w"}]


def filetypes(node):
    """FilePicker file types -> list of (description, pattern) tuples."""
    out = []
    for line in node["props"].get("filetypes") or []:
        if "|" in line:
            desc, pat = line.split("|", 1)
            out.append((desc.strip() or pat.strip(), pat.strip().replace(";", " ")))
        elif line.strip():
            out.append((line.strip(), line.strip()))
    return out or [("All files", "*.*")]


def initial_state(node):
    """Describe state that must be set after the widget is created.

    Returns a dict that may contain:
      text        str    -> Entry/FilePicker insert, Text insert, Combobox set
      placeholder str    -> grey hint text for an empty Entry
      checked     bool   -> Checkbutton variable
      selected    bool   -> Radiobutton: group variable set to this value
      items       list   -> Listbox inserts
      value       number -> Scale / Spinbox
      rows        list   -> Table rows (list of lists)
      columns     list   -> Table column dicts (title, width, anchor)
      striped     bool   -> Table: alternate row colours
      showvalue   bool   -> Scale: show a value label next to it
      resolution  number -> Scale: round values to this step
      image       path   -> Label/Button image (PhotoImage)
      scrollbar   True   -> wrap in Frame + Scrollbar
      readonly    True   -> Text/Console: disable after filling
      disable_after_fill True -> apply state=disabled after filling
      tooltip     str    -> hover tooltip
      shortcut    str    -> keyboard shortcut that clicks the button
      capture_print bool -> Console: redirect print()/errors into it
    """
    t = node["type"]
    p = node["props"]
    s = {}
    if t in ("Entry", "FilePicker"):
        if p.get("text"):
            s["text"] = p["text"]
        if t == "Entry" and p.get("placeholder"):
            s["placeholder"] = p["placeholder"]
        if p.get("state") in ("disabled", "readonly"):
            s["disable_after_fill"] = True
    elif t == "Text":
        if p.get("text"):
            s["text"] = p["text"]
        if p.get("readonly"):
            s["readonly"] = True
    elif t == "Console":
        s["readonly"] = True
        if p.get("capture_print"):
            s["capture_print"] = True
    elif t == "Combobox":
        if p.get("text"):
            s["text"] = p["text"]
    elif t == "Checkbutton":
        s["checked"] = bool(p.get("checked"))
        if p.get("state") == "disabled":
            s["disable_after_fill"] = True
    elif t == "Radiobutton":
        s["selected"] = bool(p.get("selected"))
    elif t == "Listbox":
        s["items"] = list(p.get("items") or [])
    elif t in ("Spinbox", "Scale"):
        s["value"] = _num(p.get("value"))
        if p.get("state") == "disabled":
            s["disable_after_fill"] = True
        if t == "Scale":
            s["showvalue"] = bool(p.get("showvalue", True))
            s["resolution"] = _num(p.get("resolution") or 1)
    elif t == "Table":
        s["columns"] = table_columns(node)
        s["rows"] = [list(r) for r in (p.get("rows") or [])]
        s["striped"] = bool(p.get("striped", True))
    if t in ("Label", "Button") and p.get("image"):
        s["image"] = p["image"]
    if t in SCROLLABLE and (p.get("scrollbar") or t == "Console"):
        s["scrollbar"] = True
    if p.get("tooltip"):
        s["tooltip"] = p["tooltip"]
    if t == "Button" and p.get("shortcut"):
        s["shortcut"] = p["shortcut"]
    return s


def shortcut_sequence(text):
    """'Ctrl+Shift+R' -> '<Control-Shift-R>' (None if it can't be parsed).
    Letters with Shift become uppercase as Tk expects."""
    if not text:
        return None
    parts = [x.strip() for x in text.replace("-", "+").split("+") if x.strip()]
    if not parts:
        return None
    key = parts[-1]
    mods = []
    for m in parts[:-1]:
        m = m.lower()
        mods.append({"ctrl": "Control", "control": "Control", "alt": "Alt",
                     "shift": "Shift", "cmd": "Command", "command": "Command"}.get(m))
    if None in mods:
        return None
    if len(key) == 1:
        key = key.upper() if "Shift" in mods else key.lower()
    else:
        key = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
               "space": "space", "tab": "Tab", "del": "Delete", "delete": "Delete",
               "backspace": "BackSpace", "up": "Up", "down": "Down", "left": "Left",
               "right": "Right", "home": "Home", "end": "End"}.get(key.lower(), key.upper()
                                                                    if key.upper().startswith("F")
                                                                    else key)
    return "<%s>" % "-".join(mods + [key])


def resolve_family(value, family):
    """Replace FAMILY markers (recursively in tuples) with a real family name."""
    if isinstance(value, tuple) and value and value[0] == FAMILY:
        return (family,) + value[1:]
    return value


def resolve_options(options, family):
    """Options list -> kwargs dict with FAMILY markers replaced (designer)."""
    return {k: resolve_family(v, family) for k, v in options}
