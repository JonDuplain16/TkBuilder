"""
Shared mapping from design nodes to real tkinter widgets/options.

Both the designer (live preview) and the code generator use these functions,
so what you see while designing is what the generated program shows.
"""

from model import WIDGETS

# Which class creates each node type: (module, class name)
#   module is "tk" or "ttk"
WIDGET_CLASS = {
    "Window": ("tk", "Toplevel"),
    "Frame": ("tk", "Frame"),
    "LabelFrame": ("tk", "LabelFrame"),
    "Notebook": ("ttk", "Notebook"),
    "Tab": ("tk", "Frame"),
    "Label": ("tk", "Label"),
    "Button": ("tk", "Button"),
    "Entry": ("tk", "Entry"),
    "Text": ("tk", "Text"),
    "Checkbutton": ("tk", "Checkbutton"),
    "Radiobutton": ("tk", "Radiobutton"),
    "Combobox": ("ttk", "Combobox"),
    "Listbox": ("tk", "Listbox"),
    "Spinbox": ("tk", "Spinbox"),
    "Scale": ("tk", "Scale"),
    "Progressbar": ("ttk", "Progressbar"),
    "Separator": ("ttk", "Separator"),
    "Canvas": ("tk", "Canvas"),
}

# Types that are wrapped in a tk.Frame together with a vertical scrollbar
# when their "scrollbar" property is on.
SCROLLABLE = ("Text", "Listbox")


class DefaultFamily:
    """Marker used inside a font tuple when the user picked a size/style but
    no font family. The designer substitutes the real default family; the
    code generator emits the DEFAULT_FONT variable."""

    def __repr__(self):
        return "DEFAULT_FONT"


DEFAULT_FAMILY = DefaultFamily()


def font_value(font):
    """Font dict -> tkinter font tuple, or None for 'use the default font'."""
    if not font:
        return None
    family = font.get("family") or ""
    size = int(font.get("size") or 0)
    styles = [s for s in ("bold", "italic", "underline") if font.get(s)]
    if not family and not size and not styles:
        return None
    t = (family or DEFAULT_FAMILY, size or 10)
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


def widget_options(node):
    """Return an ordered list of (option, value) for the widget constructor.

    Values are plain python values (str, int, float, tuple, or tuples that
    contain DEFAULT_FAMILY).  Things that can't be constructor options
    (starting text, checked state, list items, images, variables, scrollbars)
    are handled by the caller - see initial_state().
    """
    t = node["type"]
    p = node["props"]
    o = []

    def add(key, value, skip=("", None)):
        if value in skip:
            return
        o.append((key, value))

    def common_text_style():
        add("font", font_value(p.get("font")))
        add("fg", p.get("fg"))
        add("bg", p.get("bg"))

    if t == "Window":
        add("bg", p.get("bg"))
        pad = int(p.get("padding") or 0)
        if pad:
            o += [("padx", pad), ("pady", pad)]
    elif t == "Frame":
        add("bg", p.get("bg"))
        add("relief", p.get("relief"), ("", None, "flat"))
        add("bd", int(p.get("borderwidth") or 0), (0,))
        pad = int(p.get("padding") or 0)
        if pad:
            o += [("padx", pad), ("pady", pad)]
    elif t == "LabelFrame":
        add("text", p.get("text"))
        common_text_style()
        pad = int(p.get("padding") or 0)
        if pad:
            o += [("padx", pad), ("pady", pad)]
    elif t == "Notebook":
        add("width", int(p.get("width") or 0), (0,))
        add("height", int(p.get("height") or 0), (0,))
    elif t == "Tab":
        add("bg", p.get("bg"))
        pad = int(p.get("padding") or 0)
        if pad:
            o += [("padx", pad), ("pady", pad)]
    elif t == "Label":
        add("text", p.get("text"))
        add("justify", p.get("justify"), ("", None, "center"))
        add("wraplength", int(p.get("wraplength") or 0), (0,))
        add("width", int(p.get("width") or 0), (0,))
        common_text_style()
    elif t == "Button":
        add("text", p.get("text"))
        add("width", int(p.get("width") or 0), (0,))
        common_text_style()
        add("state", p.get("state"), ("", None, "normal"))
    elif t == "Entry":
        add("width", int(p.get("width") or 0), (0,))
        if p.get("password"):
            o.append(("show", "*"))
        common_text_style()
    elif t == "Text":
        add("width", int(p.get("width") or 30))
        add("height", int(p.get("height") or 6))
        add("wrap", p.get("wrap"), ("", None, "char"))
        common_text_style()
    elif t == "Checkbutton":
        add("text", p.get("text"))
        common_text_style()
        add("state", p.get("state"), ("", None, "normal"))
    elif t == "Radiobutton":
        add("text", p.get("text"))
        add("value", p.get("text") or node["id"])
        common_text_style()
        add("state", p.get("state"), ("", None, "normal"))
    elif t == "Combobox":
        o.append(("values", tuple(p.get("items") or ())))
        add("width", int(p.get("width") or 0), (0,))
        add("font", font_value(p.get("font")))
        state = p.get("state") or "normal"
        if state == "normal" and p.get("readonly"):
            state = "readonly"
        add("state", state, ("", None, "normal"))
    elif t == "Listbox":
        add("width", int(p.get("width") or 20))
        add("height", int(p.get("height") or 6))
        add("selectmode", p.get("selectmode"), ("", None, "browse"))
        o.append(("exportselection", False))
        common_text_style()
    elif t == "Spinbox":
        o.append(("from_", _num(p.get("from_"))))
        o.append(("to", _num(p.get("to"))))
        add("increment", _num(p.get("increment") or 1), (1,))
        add("width", int(p.get("width") or 0), (0,))
        add("font", font_value(p.get("font")))
    elif t == "Scale":
        add("label", p.get("label"))
        o.append(("from_", _num(p.get("from_"))))
        o.append(("to", _num(p.get("to"))))
        add("resolution", _num(p.get("resolution") or 1), (1,))
        add("orient", p.get("orient") or "horizontal")
        add("length", int(p.get("length") or 0), (0,))
        if not p.get("showvalue", True):
            o.append(("showvalue", False))
        add("fg", p.get("fg"))
        add("bg", p.get("bg"))
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
        add("bg", p.get("bg"))
        add("relief", p.get("relief"), ("", None, "flat"))
        add("bd", int(p.get("borderwidth") or 0), (0,))
        o.append(("highlightthickness", 0))
    return o


def initial_state(node):
    """Describe state that must be set after the widget is created.

    Returns a dict that may contain:
      text      str    -> Entry insert / Text insert / Combobox set / Spinbox set
      checked   bool   -> Checkbutton variable
      selected  bool   -> Radiobutton: group variable set to this value
      items     list   -> Listbox inserts
      value     number -> Scale / Spinbox
      disable_after_fill  True for Entry/Text whose state must be applied
                          after inserting the text (state disabled/readonly)
      image     path   -> Label/Button image (PhotoImage)
      scrollbar True   -> wrap in Frame + Scrollbar
    """
    t = node["type"]
    p = node["props"]
    s = {}
    if t == "Entry":
        if p.get("text"):
            s["text"] = p["text"]
        if p.get("state") == "disabled":
            s["disable_after_fill"] = True
    elif t == "Text":
        if p.get("text"):
            s["text"] = p["text"]
        if p.get("readonly"):
            s["disable_after_fill"] = True
    elif t == "Combobox":
        if p.get("text"):
            s["text"] = p["text"]
    elif t == "Checkbutton":
        s["checked"] = bool(p.get("checked"))
    elif t == "Radiobutton":
        s["selected"] = bool(p.get("selected"))
    elif t == "Listbox":
        s["items"] = list(p.get("items") or [])
    elif t == "Spinbox":
        s["value"] = _num(p.get("value"))
        if p.get("state") == "disabled":
            s["disable_after_fill"] = True
    elif t == "Scale":
        s["value"] = _num(p.get("value"))
        if p.get("state") == "disabled":
            s["disable_after_fill"] = True
    if t in ("Label", "Button") and p.get("image"):
        s["image"] = p["image"]
    if t in SCROLLABLE and p.get("scrollbar"):
        s["scrollbar"] = True
    return s


def resolve_font_family(options, family):
    """Replace DEFAULT_FAMILY markers in an options list with a real family
    name (used by the designer).  Returns a dict ready for **kwargs."""
    out = {}
    for k, v in options:
        if isinstance(v, tuple) and v and v[0] is DEFAULT_FAMILY:
            v = (family,) + v[1:]
        out[k] = v
    return out
