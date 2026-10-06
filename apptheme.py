"""
Themes for the apps people build with TkBuilder.

One data source, two consumers:
  * the code generator prints `style_ops()` as plain `style.configure(...)`
    lines into the generated program, and passes `tk_colors()` to the classic
    tk widgets (Text, Listbox, Canvas, Menu) that ttk themes don't reach;
  * the designer applies the very same ops to *renamed* preview styles (see
    `rename_style`) so the preview looks exactly like the generated app while
    the editor keeps its own look.

Theme kinds
  "builtin": a stock ttk theme ("native" = the platform look: vista on
             Windows, aqua on macOS, clam elsewhere). Only fonts and the style
             variants are added.
  "palette": built on "clam" and fully recoloured from a palette.
"""

import sys

# --------------------------------------------------------------------------
# Theme catalog
# --------------------------------------------------------------------------

_LIGHT = dict(bg="#f5f6f8", surface="#ffffff", fg="#1f2328", muted="#6b7280",
              border="#c9d1d9", field="#ffffff", field_fg="#1f2328",
              accent="#2563eb", accent_fg="#ffffff", accent_hover="#1d4ed8",
              select_bg="#2563eb", select_fg="#ffffff", trough="#e5e7eb",
              danger="#dc2626", success="#16a34a", warning="#d97706",
              disabled="#9ca3af", button="#ffffff", button_hover="#eef2f7",
              heading="#eef1f5")

_DARK = dict(bg="#1e1f22", surface="#2b2d31", fg="#e6e6e6", muted="#9aa0a6",
             border="#45484f", field="#2b2d31", field_fg="#e6e6e6",
             accent="#3b82f6", accent_fg="#ffffff", accent_hover="#2563eb",
             select_bg="#3b82f6", select_fg="#ffffff", trough="#383a40",
             danger="#f87171", success="#4ade80", warning="#fbbf24",
             disabled="#6b6f76", button="#383a40", button_hover="#45484f",
             heading="#313338")


def _pal(base, **kw):
    p = dict(base)
    p.update(kw)
    return p


THEMES = {
    # ---- stock ttk themes ------------------------------------------------
    "native": {"name": "System default", "kind": "builtin", "base": "native", "dark": False,
               "desc": "The normal look of the operating system."},
    "clam": {"name": "Clam", "kind": "builtin", "base": "clam", "dark": False,
             "desc": "Tk's flat, clean built-in theme. Works everywhere."},
    "alt": {"name": "Alt", "kind": "builtin", "base": "alt", "dark": False,
            "desc": "Tk's 'alt' theme with classic 3D edges."},
    "default": {"name": "Tk Default", "kind": "builtin", "base": "default", "dark": False,
                "desc": "The plain Tk default theme."},
    "classic": {"name": "Classic", "kind": "builtin", "base": "classic", "dark": False,
                "desc": "Old-school Motif look with raised 3D borders."},
    "winnative": {"name": "Windows Classic", "kind": "builtin", "base": "winnative", "dark": False,
                  "desc": "Windows 95/2000 look (Windows only)."},
    "xpnative": {"name": "Windows XP", "kind": "builtin", "base": "xpnative", "dark": False,
                 "desc": "Windows XP visual style (Windows only)."},
    # ---- recoloured themes on clam -------------------------------------
    "modern_light": {"name": "Modern Light", "kind": "palette", "base": "clam", "dark": False,
                     "desc": "Clean, bright and professional.", "palette": _LIGHT},
    "modern_dark": {"name": "Modern Dark", "kind": "palette", "base": "clam", "dark": True,
                    "desc": "Easy on the eyes for long sessions.", "palette": _DARK},
    "slate": {"name": "Slate", "kind": "palette", "base": "clam", "dark": False,
              "desc": "Cool grey-blue engineering tool look.",
              "palette": _pal(_LIGHT, bg="#e9edf2", surface="#f8fafc", accent="#475569",
                              accent_hover="#334155", select_bg="#475569", heading="#dde3ea",
                              button="#f8fafc", button_hover="#e2e8f0", border="#b8c2cf")},
    "ocean": {"name": "Ocean", "kind": "palette", "base": "clam", "dark": False,
              "desc": "Light theme with teal accents.",
              "palette": _pal(_LIGHT, bg="#f0f7f8", accent="#0e7490", accent_hover="#155e75",
                              select_bg="#0e7490", heading="#e0f0f2", button_hover="#e3f2f4")},
    "forest": {"name": "Forest", "kind": "palette", "base": "clam", "dark": False,
               "desc": "Light theme with green accents.",
               "palette": _pal(_LIGHT, bg="#f3f7f2", accent="#15803d", accent_hover="#166534",
                               select_bg="#15803d", heading="#e4eee2", button_hover="#e7f1e5")},
    "sunset": {"name": "Sunset", "kind": "palette", "base": "clam", "dark": False,
               "desc": "Warm light theme with orange accents.",
               "palette": _pal(_LIGHT, bg="#fdf8f3", accent="#ea580c", accent_hover="#c2410c",
                               select_bg="#ea580c", heading="#f8ece0", button_hover="#fbefe4")},
    "rose": {"name": "Rose", "kind": "palette", "base": "clam", "dark": False,
             "desc": "Soft light theme with pink accents.",
             "palette": _pal(_LIGHT, bg="#fdf6f9", accent="#db2777", accent_hover="#be185d",
                             select_bg="#db2777", heading="#f9e8f0", button_hover="#fbecf3")},
    "midnight": {"name": "Midnight", "kind": "palette", "base": "clam", "dark": True,
                 "desc": "Deep navy dark theme with sky-blue accents.",
                 "palette": _pal(_DARK, bg="#0f172a", surface="#1e293b", fg="#e2e8f0",
                                 muted="#94a3b8", border="#334155", field="#1e293b",
                                 field_fg="#e2e8f0", accent="#38bdf8", accent_fg="#0f172a",
                                 accent_hover="#0ea5e9", select_bg="#0284c7", trough="#273449",
                                 button="#1e293b", button_hover="#334155", heading="#1a2436")},
    "nord": {"name": "Nord", "kind": "palette", "base": "clam", "dark": True,
             "desc": "Arctic, bluish dark theme.",
             "palette": _pal(_DARK, bg="#2e3440", surface="#3b4252", fg="#eceff4",
                             muted="#a3adbf", border="#4c566a", field="#3b4252", field_fg="#eceff4",
                             accent="#88c0d0", accent_fg="#2e3440", accent_hover="#8fbcbb",
                             select_bg="#5e81ac", trough="#434c5e", danger="#bf616a",
                             success="#a3be8c", warning="#ebcb8b", button="#3b4252",
                             button_hover="#434c5e", heading="#353c4a")},
    "dracula": {"name": "Dracula", "kind": "palette", "base": "clam", "dark": True,
                "desc": "Popular purple-accented dark theme.",
                "palette": _pal(_DARK, bg="#282a36", surface="#343746", fg="#f8f8f2",
                                muted="#a0a4bd", border="#44475a", field="#343746",
                                field_fg="#f8f8f2", accent="#bd93f9", accent_fg="#282a36",
                                accent_hover="#a87ef5", select_bg="#6d5aa8", trough="#3c3f52",
                                danger="#ff5555", success="#50fa7b", warning="#f1fa8c",
                                button="#343746", button_hover="#44475a", heading="#2f3140")},
    "solarized_light": {"name": "Solarized Light", "kind": "palette", "base": "clam", "dark": False,
                        "desc": "Low-contrast warm light theme.",
                        "palette": _pal(_LIGHT, bg="#fdf6e3", surface="#fffdf6", fg="#586e75",
                                        muted="#93a1a1", border="#d6cfb8", field="#fffdf6",
                                        field_fg="#586e75", accent="#268bd2",
                                        accent_hover="#1f6fa8", select_bg="#268bd2",
                                        trough="#eee8d5", button="#fffdf6",
                                        button_hover="#eee8d5", heading="#eee8d5")},
    "high_contrast": {"name": "High Contrast", "kind": "palette", "base": "clam", "dark": True,
                      "desc": "Maximum readability: black, white and yellow.",
                      "palette": _pal(_DARK, bg="#000000", surface="#000000", fg="#ffffff",
                                      muted="#d0d0d0", border="#ffffff", field="#000000",
                                      field_fg="#ffffff", accent="#ffd400", accent_fg="#000000",
                                      accent_hover="#ffe55c", select_bg="#ffd400",
                                      select_fg="#000000", trough="#333333", button="#000000",
                                      button_hover="#333333", heading="#1a1a1a")},
}

THEME_ORDER = ["native", "modern_light", "modern_dark", "slate", "ocean", "forest", "sunset",
               "rose", "midnight", "nord", "dracula", "solarized_light", "high_contrast",
               "clam", "alt", "default", "classic", "winnative", "xpnative"]

# Style variants users can pick per widget ("Look" property).
# key -> (friendly name, ttk class it applies to)
VARIANTS = {
    "TButton": [["", "Normal"], ["Accent", "Accent (main action)"], ["Success", "Success (green)"],
                ["Danger", "Danger (red)"], ["Flat", "Flat (toolbar)"]],
    "TLabel": [["", "Normal"], ["Title", "Title (big)"], ["Heading", "Heading"],
               ["Muted", "Muted (grey)"], ["Success", "Success (green)"],
               ["Danger", "Danger (red)"], ["Mono", "Monospace (numbers/code)"]],
    "TCheckbutton": [["", "Normal"], ["Switch", "Toggle button"]],
    "TFrame": [["", "Normal"], ["Card", "Card (raised panel)"]],
    "TLabelframe": [["", "Normal"], ["Card", "Card (raised panel)"]],
}


def native_base():
    """The stock ttk theme a fresh Tk() uses on this platform."""
    if sys.platform == "win32":
        return "vista"
    if sys.platform == "darwin":
        return "aqua"
    return "clam"


def base_theme(key):
    t = THEMES.get(key, THEMES["native"])
    return native_base() if t["base"] == "native" else t["base"]


def is_dark(key):
    return THEMES.get(key, THEMES["native"]).get("dark", False)


def palette(key):
    """Palette dict for palette themes, or None for stock themes."""
    return THEMES.get(key, THEMES["native"]).get("palette")


def default_font_size(key):
    return 9 if THEMES.get(key, {}).get("kind") == "builtin" and sys.platform == "win32" else 10


# --------------------------------------------------------------------------
# Style operations
# --------------------------------------------------------------------------
# Each op is one of:
#   ("configure", style_name, {option: value})
#   ("map",       style_name, {option: [(state_spec, value), ...]})
#   ("option",    tk_option_pattern, value)  -- option database (codegen only)
# Fonts are tuples whose first item may be the FAMILY placeholder, which the
# consumer replaces with the real family (designer) or a variable (codegen).

FAMILY = "<family>"


def _f(size, *styles):
    t = (FAMILY, size)
    if styles:
        t += (" ".join(styles),)
    return t


TTK_CLASSES = ["TFrame", "TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TEntry",
               "TCombobox", "TSpinbox", "TLabelframe", "TLabelframe.Label", "TNotebook",
               "TNotebook.Tab", "Horizontal.TScale", "Vertical.TScale",
               "Horizontal.TProgressbar", "Vertical.TProgressbar", "TSeparator",
               "Treeview", "Treeview.Heading", "Vertical.TScrollbar", "Horizontal.TScrollbar",
               "TPanedwindow", "TMenubutton"]


def style_ops(key, size=None):
    """List of style operations that make up a theme (see module docstring)."""
    size = size or default_font_size(key)
    ops = []
    p = palette(key)

    def conf(name, **kw):
        ops.append(("configure", name, kw))

    def smap(name, **kw):
        ops.append(("map", name, kw))

    base_font = _f(size)
    # fonts for every class (explicit so renamed preview styles get them too)
    for cls in TTK_CLASSES:
        if cls not in ("TSeparator", "TPanedwindow", "Horizontal.TScale", "Vertical.TScale",
                       "Horizontal.TProgressbar", "Vertical.TProgressbar",
                       "Vertical.TScrollbar", "Horizontal.TScrollbar"):
            conf(cls, font=base_font)

    if p is None:
        # stock theme: only variants
        conf("Accent.TButton", font=_f(size, "bold"))
        conf("Success.TButton", font=_f(size, "bold"), foreground="#15803d")
        conf("Danger.TButton", font=_f(size, "bold"), foreground="#b91c1c")
        conf("Flat.TButton", relief="flat", padding=(6, 2))
        conf("Title.TLabel", font=_f(size + 9, "bold"))
        conf("Heading.TLabel", font=_f(size + 3, "bold"))
        conf("Muted.TLabel", foreground="#6b7280")
        conf("Success.TLabel", foreground="#15803d")
        conf("Danger.TLabel", foreground="#b91c1c")
        conf("Mono.TLabel", font=("Consolas" if sys.platform == "win32" else "Courier", size))
        conf("Card.TFrame", relief="groove", borderwidth=2, padding=8)
        conf("Card.TLabelframe", relief="groove", borderwidth=2)
        ops.append(("option", "*Text.font", ("Consolas" if sys.platform == "win32" else "Courier", size)))
        return ops

    bg, surface, fg, muted = p["bg"], p["surface"], p["fg"], p["muted"]
    border, field, field_fg = p["border"], p["field"], p["field_fg"]
    accent, accent_fg, accent_hover = p["accent"], p["accent_fg"], p["accent_hover"]
    sel_bg, sel_fg, trough, disabled = p["select_bg"], p["select_fg"], p["trough"], p["disabled"]
    button, button_hover = p["button"], p["button_hover"]

    edges = dict(bordercolor=border, lightcolor=bg, darkcolor=bg)
    conf(".", background=bg, foreground=fg, fieldbackground=field, troughcolor=trough,
         selectbackground=sel_bg, selectforeground=sel_fg, insertcolor=fg,
         bordercolor=border, lightcolor=bg, darkcolor=bg, focuscolor=accent, font=base_font)
    smap(".", foreground=[("disabled", disabled)])
    conf("TFrame", background=bg)
    conf("TLabel", background=bg, foreground=fg)
    conf("TButton", background=button, foreground=fg, bordercolor=border, lightcolor=button,
         darkcolor=button, focuscolor=accent, padding=(10, 4), relief="flat", anchor="center")
    smap("TButton", background=[("disabled", bg), ("pressed", border), ("active", button_hover)],
         lightcolor=[("pressed", border), ("active", button_hover)],
         darkcolor=[("pressed", border), ("active", button_hover)],
         bordercolor=[("focus", accent)], foreground=[("disabled", disabled)])
    for variant, color, hover in (("Accent", accent, accent_hover),
                                  ("Success", p["success"], p["success"]),
                                  ("Danger", p["danger"], p["danger"])):
        name = variant + ".TButton"
        txt = accent_fg if variant == "Accent" else ("#ffffff" if not is_dark(key) else bg)
        conf(name, background=color, foreground=txt, bordercolor=color, lightcolor=color,
             darkcolor=color, font=_f(size, "bold"))
        smap(name, background=[("disabled", trough), ("pressed", hover), ("active", hover)],
             lightcolor=[("pressed", hover), ("active", hover)],
             darkcolor=[("pressed", hover), ("active", hover)],
             bordercolor=[("focus", fg)], foreground=[("disabled", disabled)])
    conf("Flat.TButton", background=bg, foreground=fg, bordercolor=bg, lightcolor=bg,
         darkcolor=bg, padding=(8, 4), relief="flat")
    smap("Flat.TButton", background=[("pressed", border), ("selected", border), ("active", button_hover)],
         lightcolor=[("active", button_hover)], darkcolor=[("active", button_hover)],
         bordercolor=[("active", button_hover)])
    conf("Title.TLabel", font=_f(size + 9, "bold"), foreground=fg)
    conf("Heading.TLabel", font=_f(size + 3, "bold"), foreground=fg)
    conf("Muted.TLabel", foreground=muted)
    conf("Success.TLabel", foreground=p["success"])
    conf("Danger.TLabel", foreground=p["danger"])
    conf("Mono.TLabel", font=("Consolas" if sys.platform == "win32" else "Courier", size))
    conf("Card.TFrame", background=bg, relief="solid", borderwidth=1, bordercolor=border,
         padding=10)
    for name in ("TCheckbutton", "TRadiobutton"):
        conf(name, background=bg, foreground=fg, indicatorbackground=field,
             indicatorforeground=accent_fg, upperbordercolor=border, lowerbordercolor=border,
             focuscolor=bg, indicatormargin=(0, 0, 6, 0))
        smap(name, background=[("active", bg)],
             indicatorbackground=[("disabled", trough), ("selected", accent), ("pressed", border)],
             upperbordercolor=[("selected", accent)], lowerbordercolor=[("selected", accent)],
             foreground=[("disabled", disabled)])
    conf("Switch.TCheckbutton", background=button, foreground=fg, padding=(10, 4),
         indicatorbackground=button, indicatormargin=0, relief="flat")
    smap("Switch.TCheckbutton", background=[("selected", accent), ("active", button_hover)],
         foreground=[("selected", accent_fg)])
    for name in ("TEntry", "TCombobox", "TSpinbox"):
        conf(name, fieldbackground=field, foreground=field_fg, background=button,
             insertcolor=field_fg, arrowcolor=fg, padding=4, **edges)
        smap(name, bordercolor=[("focus", accent)], lightcolor=[("focus", field)],
             fieldbackground=[("readonly", field), ("disabled", bg)],
             foreground=[("disabled", disabled)], arrowcolor=[("disabled", disabled)],
             selectbackground=[("focus", sel_bg), ("!focus", sel_bg)],
             selectforeground=[("focus", sel_fg), ("!focus", sel_fg)])
    conf("TLabelframe", background=bg, bordercolor=border, lightcolor=bg, darkcolor=bg,
         relief="solid", borderwidth=1)
    conf("TLabelframe.Label", background=bg, foreground=fg, font=_f(size, "bold"))
    conf("Card.TLabelframe", background=bg, bordercolor=border)
    conf("TNotebook", background=bg, bordercolor=border, lightcolor=bg, darkcolor=bg,
         tabmargins=(2, 4, 2, 0))
    conf("TNotebook.Tab", background=p["heading"], foreground=muted, padding=(14, 5),
         bordercolor=border, lightcolor=p["heading"], darkcolor=p["heading"])
    smap("TNotebook.Tab", background=[("selected", bg), ("active", button_hover)],
         foreground=[("selected", fg)], lightcolor=[("selected", bg)],
         padding=[("selected", (14, 5))], expand=[("selected", (1, 1, 1, 0))])
    for o in ("Horizontal", "Vertical"):
        conf(o + ".TProgressbar", background=accent, troughcolor=trough, bordercolor=trough,
             lightcolor=accent, darkcolor=accent, thickness=14)
        conf(o + ".TScale", background=button, troughcolor=trough, bordercolor=border,
             lightcolor=button, darkcolor=button, sliderlength=18, gripcount=0)
        smap(o + ".TScale", background=[("active", accent), ("pressed", accent)])
        conf(o + ".TScrollbar", background=button, troughcolor=bg, bordercolor=bg,
             lightcolor=button, darkcolor=button, arrowcolor=muted, gripcount=0, arrowsize=12)
        smap(o + ".TScrollbar", background=[("active", button_hover), ("pressed", border)])
    conf("TSeparator", background=border)
    conf("TPanedwindow", background=border)
    conf("Sash", sashthickness=6, gripcount=0, background=bg)
    conf("TMenubutton", background=button, foreground=fg, arrowcolor=fg, padding=(10, 4),
         bordercolor=border, lightcolor=button, darkcolor=button)
    smap("TMenubutton", background=[("active", button_hover)])
    conf("Treeview", background=field, fieldbackground=field, foreground=field_fg,
         bordercolor=border, lightcolor=field, darkcolor=field, rowheight=size * 2 + 8)
    smap("Treeview", background=[("selected", sel_bg)], foreground=[("selected", sel_fg)])
    conf("Treeview.Heading", background=p["heading"], foreground=fg, relief="flat",
         bordercolor=border, lightcolor=p["heading"], darkcolor=p["heading"],
         font=_f(size, "bold"), padding=(6, 4))
    smap("Treeview.Heading", background=[("active", button_hover)])
    # classic tk widgets / popups use the option database
    ops.append(("option", "*TCombobox*Listbox.background", field))
    ops.append(("option", "*TCombobox*Listbox.foreground", field_fg))
    ops.append(("option", "*TCombobox*Listbox.selectBackground", sel_bg))
    ops.append(("option", "*TCombobox*Listbox.selectForeground", sel_fg))
    ops.append(("option", "*Menu.background", surface))
    ops.append(("option", "*Menu.foreground", fg))
    ops.append(("option", "*Menu.activeBackground", sel_bg))
    ops.append(("option", "*Menu.activeForeground", sel_fg))
    ops.append(("option", "*Menu.relief", "flat"))
    return ops


def tk_colors(key, wtype):
    """Explicit colours for classic tk widgets that ttk themes don't style.
    wtype: "Window", "Text", "Listbox", "Canvas", "Menu", "Console", "Frame".
    Returns {} for stock themes (tk defaults are right there)."""
    p = palette(key)
    if p is None:
        if wtype == "Console":
            return {"bg": "#1e1e1e", "fg": "#d4d4d4", "insertbackground": "#d4d4d4"}
        return {}
    if wtype in ("Window", "Frame"):
        return {"bg": p["bg"]}
    if wtype in ("Text", "Listbox"):
        return {"bg": p["field"], "fg": p["field_fg"], "insertbackground": p["field_fg"],
                "selectbackground": p["select_bg"], "selectforeground": p["select_fg"],
                "highlightthickness": 1, "highlightbackground": p["border"],
                "highlightcolor": p["accent"], "relief": "flat", "borderwidth": 0}
    if wtype == "Console":
        dark = is_dark(key)
        return {"bg": p["surface"] if dark else "#1e1e1e",
                "fg": p["fg"] if dark else "#d4d4d4",
                "insertbackground": p["fg"] if dark else "#d4d4d4",
                "selectbackground": p["select_bg"], "selectforeground": p["select_fg"],
                "highlightthickness": 1, "highlightbackground": p["border"],
                "relief": "flat", "borderwidth": 0}
    if wtype == "Canvas":
        return {"bg": p["field"], "highlightthickness": 1, "highlightbackground": p["border"]}
    if wtype == "Menu":
        return {"bg": p["surface"], "fg": p["fg"], "activebackground": p["select_bg"],
                "activeforeground": p["select_fg"], "relief": "flat"}
    return {}


def rename_style(name, prefix):
    """Map a real style name to its designer-preview twin.

    The last dotted part (the widget class, e.g. 'TButton') gets the prefix,
    so variants keep inheriting: 'Accent.TButton' -> 'Accent.P1TButton',
    which falls back to 'P1TButton' (whose layout the designer copies from
    the theme being previewed).  '.' maps to prefix + 'Root'.
    Two-part class names are treated as one class: 'TNotebook.Tab' ->
    'P1TNotebook.Tab' (ttk derives a notebook's tab style from the notebook's
    style name, so this is what a notebook styled 'P1TNotebook' uses), and
    'Horizontal.TScale' -> 'P1Horizontal.TScale'.
    """
    if not prefix:
        return name
    if name == ".":
        return prefix + "Root"
    for cls in ("Horizontal.TScale", "Vertical.TScale", "Horizontal.TProgressbar",
                "Vertical.TProgressbar", "Vertical.TScrollbar", "Horizontal.TScrollbar",
                "TNotebook.Tab", "Treeview.Heading", "TLabelframe.Label"):
        if name == cls or name.endswith("." + cls):
            head = name[: -len(cls)]
            return head + prefix + cls
    head, _, last = name.rpartition(".")
    return (head + "." if head else "") + prefix + last
