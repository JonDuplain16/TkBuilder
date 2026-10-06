"""
Theme-accurate previews of design nodes (used by the workplane and the
drag ghost).

The editor runs its own ttk theme (theme.py), but the user's app may use any
theme from apptheme.py.  Only one ttk theme can be active per Tk interpreter,
so for every app theme we create *prefixed twins* of the ttk styles:

  * layouts are copied from the theme's base ttk theme (e.g. "vista");
    when that base isn't the editor's theme, the elements they use are cloned
    into the editor's theme under prefixed names;
  * each twin gets the *flattened* settings of its real style chain
    (style -> parent styles -> '.') of the base theme, with
    apptheme.style_ops() layered on top exactly like the generated program
    does - so nothing leaks in from the editor's own styles;
  * variant and personal styles (tkmap.custom_style_ops) are configured on
    the renamed names; ttk's normal name fallback ('Accent.P1TButton' ->
    'P1TButton') then behaves like in the real app.

Names follow apptheme.rename_style(name, prefix).  One prefix per theme,
cached on the Tk root (installing a theme takes a few milliseconds; after
that it is free).

build(parent, node, styler, ...) creates the real widget(s) for one node and
returns a Rendered record.  restyle(...) re-applies only the theme-dependent
look (styles, colours, fonts) so switching theme doesn't recreate widgets.
"""

import os
import sys
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

import apptheme
import model
import tkmap

FAMILY = apptheme.FAMILY

# real style names whose twins are installed (layouts + flattened settings)
_CLASSES = ["TFrame", "TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TEntry",
            "TCombobox", "TSpinbox", "TLabelframe", "TLabelframe.Label", "TNotebook",
            "TNotebook.Tab", "Horizontal.TScale", "Vertical.TScale",
            "Horizontal.TProgressbar", "Vertical.TProgressbar", "TSeparator",
            "Treeview", "Treeview.Heading", "Treeview.Item", "Treeview.Cell", "Treeview.Row",
            "Vertical.TScrollbar", "Horizontal.TScrollbar", "Toolbutton"]

# options of classic tk widgets that make up their "look"
_TK_LOOK = ("bg", "fg", "insertbackground", "selectbackground", "selectforeground",
            "highlightthickness", "highlightbackground", "highlightcolor", "relief",
            "borderwidth", "font")

# widgets whose own font option (not the style's) shows their text
_WIDGET_FONT = ("Entry", "Combobox", "Spinbox", "Listbox", "Text", "Console")

# neutral values for options that would otherwise leak in from the editor's
# own styles (see Styler._apply_settings)
_PIXEL_STYLE = {"rowheight", "thickness", "sliderlength", "arrowsize", "padding"}

_NEUTRAL = {"-padding": "0", "-relief": "flat", "-shiftrelief": "0"}

SAMPLE_TABLE_ROWS = 2
CONSOLE_SAMPLE = ("> Output from your app appears here.\n"
                  "> e.g. messages, print() and command output")


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

def platform_family(root):
    """Family of TkDefaultFont in a *fresh* Tk app on this platform (the
    editor changes its own TkDefaultFont, so we can't simply ask)."""
    cached = getattr(root, "_tkb_platform_family", None)
    if cached:
        return cached
    try:
        families = set(tkfont.families(root))
        ws = root.tk.call("tk", "windowingsystem")
    except tk.TclError:
        families, ws = set(), ""
    if ws == "win32":
        fam = "Segoe UI" if "Segoe UI" in families else "Tahoma"
    elif ws == "aqua":
        fam = ".AppleSystemUIFont"
    else:
        fam = next((f for f in ("DejaVu Sans", "Liberation Sans", "Helvetica") if f in families),
                   "Helvetica")
    try:
        root._tkb_platform_family = fam
    except (AttributeError, tk.TclError):
        pass
    return fam


def _mono_family():
    return "Consolas" if sys.platform == "win32" else "Courier"


# --------------------------------------------------------------------------
# Styler: prefixed preview styles for one app theme
# --------------------------------------------------------------------------

def _store(root):
    st = getattr(root, "_tkb_preview_store", None)
    if st is None:
        st = {"installed": {}, "applied": {}, "stylers": {}, "custom": {}}
        root._tkb_preview_store = st
    return st


def get_styler(widget, settings):
    """The Styler for the project settings (theme, font family/size).
    Cheap to call often (cached)."""
    root = widget._root()
    ctx = tkmap.make_ctx(settings or {})
    family = (settings or {}).get("font_family") or platform_family(root)
    try:
        editor_theme = ttk.Style(root).theme_use()
    except tk.TclError:
        editor_theme = ""
    scale = 1.0
    if (settings or {}).get("dpi_aware", True):
        try:
            scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        except tk.TclError:
            scale = 1.0
    key = (ctx["theme"], family, ctx["size"], editor_theme, round(scale, 3))
    store = _store(root)
    s = store["stylers"].get(key)
    if s is None:
        s = Styler(root, ctx, family, editor_theme, scale)
        store["stylers"][key] = s
    s.ensure()
    return s


class Styler:
    """Maps real style names to installed preview twins for one theme."""

    def __init__(self, root, ctx, family, editor_theme, scale=1.0):
        self.root = root
        self.scale = scale
        self.ctx = dict(ctx)
        self.theme = ctx["theme"]
        self.size = ctx["size"]
        self.family = family
        self.editor_theme = editor_theme
        try:
            idx = apptheme.THEME_ORDER.index(self.theme)
        except ValueError:
            idx = 90 + (sum(map(ord, self.theme)) % 900)
        self.prefix = "Tkp%d" % idx
        self.dark = apptheme.is_dark(self.theme)
        self.palette = apptheme.palette(self.theme)
        self.ok = True
        self.signature = (self.theme, family, self.size, editor_theme, round(scale, 3))
        self._style = ttk.Style(root)
        self._options = {}          # tk option database ops of the theme

    # ---- public helpers -------------------------------------------------
    def px(self, n):
        """Design pixels -> screen pixels (the generated app scales every
        pixel size by the same factor when 'dpi_aware' is on)."""
        try:
            return int(round(float(n) * self.scale))
        except (TypeError, ValueError):
            return n

    def name(self, real):
        """Real style name -> installed preview twin."""
        if not real or not self.ok:
            return real or ""
        return apptheme.rename_style(real, self.prefix)

    def font(self, value):
        """Resolve the FAMILY marker in a font tuple."""
        return tkmap.resolve_family(value, self.family)

    @property
    def base_font(self):
        return (self.family, self.size)

    def lookup(self, real, option, default=""):
        try:
            v = self._style.lookup(self.name(real), option)
        except tk.TclError:
            v = ""
        return v if v not in ("", None) else default

    def colors(self, kind):
        return apptheme.tk_colors(self.theme, kind)

    def window_bg(self):
        """Background of a window with no colour of its own."""
        bg = self.colors("Window").get("bg")
        return bg or self.lookup("TFrame", "background", "SystemButtonFace")

    def option(self, pattern, default=None):
        """Value of an ('option', pattern, value) op of the theme."""
        return self._options.get(pattern, default)

    def muted(self):
        """A grey for hint / sample text that suits the theme."""
        if self.palette:
            return self.palette["muted"]
        return "#8a8f98"

    def default_font(self, wtype):
        """Font for widgets whose text font is a widget option, when the
        user didn't choose one.  Matches the theme's base font."""
        if wtype in ("Text",):      # the generated app leaves Text on TkFixedFont
            return self.font(self.option("*Text.font")) or "TkFixedFont"
        if wtype == "Console":
            return (tkmap.MONO_FAMILY, self.size)
        return self.base_font

    def node_style(self, node):
        """Renamed style for a node's main ttk widget (installs the node's
        own colours/font first).  None for classic tk widgets."""
        t = node["type"]
        if tkmap.WIDGET_CLASS[t][0] != "ttk":
            return None
        if t == "LabelFrame":
            real = tkmap.labelframe_style_name(node) or "TLabelframe"
        else:
            real = _style_name(node) or tkmap.style_class(node)
        self.apply_custom(node)
        return self.name(real)

    def apply_custom(self, node):
        ops = _custom_style_ops(node, self.ctx)
        if not ops:
            return
        store = _store(self.root)
        key = (self.prefix, repr(ops))
        if store["custom"].get((self.prefix, node["id"], node["type"])) == key:
            return
        for op in ops:
            self._apply_op(op, renamed=True)
        store["custom"][(self.prefix, node["id"], node["type"])] = key

    # ---- installation ---------------------------------------------------
    def ensure(self):
        """Install layouts (once per theme) and apply settings when the font
        changed since the last time this prefix was configured."""
        if not self.ok:
            return
        store = _store(self.root)
        ikey = (self.theme, self.editor_theme)
        try:
            if not store["installed"].get(ikey):
                self._install_layouts()
                store["installed"][ikey] = True
                store["applied"].pop(self.prefix, None)
            if store["applied"].get(self.prefix) != self.signature:
                self._apply_settings()
                store["applied"][self.prefix] = self.signature
                # personal styles must be re-applied on top of new settings
                for k in [k for k in store["custom"] if k[0] == self.prefix]:
                    del store["custom"][k]
            elif not self._options:
                self._collect_options()
        except tk.TclError as e:          # pragma: no cover - defensive
            sys.stderr.write("TkBuilder preview styles failed: %s\n" % e)
            self.ok = False

    def _src(self):
        names = self._style.theme_names()
        src = apptheme.base_theme(self.theme)
        if src not in names:
            src = "clam" if "clam" in names else self.editor_theme
        return src

    def _pristine(self, theme, cmd):
        """Like _query, but answered by an untouched copy of the ttk themes
        (the editor re-configures its own theme, e.g. 'clam', which must not
        leak into the preview of an app theme built on the same base)."""
        return _pristine_query(self.root, theme, cmd)

    def _query(self, theme, cmd):
        tcl = self.root.tk
        tcl.eval("ttk::style theme settings %s { set ::tkb_q [%s] }" % (theme, cmd))
        return tcl.eval("set ::tkb_q")

    def _install_layouts(self):
        tcl = self.root.tk
        src = self._src()
        cur = self.editor_theme
        prefix = self.prefix
        created = set()

        def relayout(spec):
            items = tcl.splitlist(spec)
            out, i = [], 0
            while i < len(items):
                name = str(items[i])
                i += 1
                new = name
                if src != cur:
                    new = prefix + "." + name
                    if new not in created:
                        try:
                            tcl.call("ttk::style", "element", "create", new, "from", src, name)
                        except tk.TclError:
                            pass        # already exists (re-install after a theme switch)
                        created.add(new)
                out.append(new)
                while i < len(items) and str(items[i]).startswith("-"):
                    opt, val = str(items[i]), items[i + 1]
                    i += 2
                    if opt == "-children":
                        val = relayout(val)
                    out += [opt, val]
            return tcl.call("list", *out)

        for cls in _CLASSES:
            try:
                spec = self._pristine(src, "ttk::style layout %s" % cls)
            except tk.TclError:
                try:
                    spec = self._pristine(src, "ttk::style layout %s" % cls.rsplit(".", 1)[-1])
                except tk.TclError:
                    continue
            if not spec:
                continue
            tcl.call("ttk::style", "layout", self.name(cls), relayout(spec))

    # settings ------------------------------------------------------------
    @staticmethod
    def _chain(name):
        parts = name.split(".")
        return [".".join(parts[i:]) for i in range(len(parts))] + ["."]

    def _pairs(self, raw):
        items = self.root.tk.splitlist(raw)
        return {str(items[i]): items[i + 1] for i in range(0, len(items) - 1, 2)}

    def _collect_options(self):
        self._options = {}
        for op in apptheme.style_ops(self.theme, self.size):
            if op[0] == "option":
                self._options[op[1]] = op[2]

    def _apply_settings(self):
        tcl = self.root.tk
        src = self._src()
        ops = apptheme.style_ops(self.theme, self.size)
        layers = {}           # real style name -> {"cfg": {}, "map": {}}

        def layer(name):
            lay = layers.get(name)
            if lay is None:
                lay = {"cfg": {}, "map": {}}
                for theme in ([src] if name != "." or src == "default" else ["default", src]):
                    try:
                        lay["cfg"].update(self._pairs(self._pristine(
                            theme, "ttk::style configure %s" % name)))
                        lay["map"].update(self._pairs(self._pristine(
                            theme, "ttk::style map %s" % name)))
                    except tk.TclError:
                        pass
                layers[name] = lay
            return lay

        chains = {}
        for cls in _CLASSES:
            chains[cls] = self._chain(cls)
            for n in chains[cls]:
                layer(n)
        self._options = {}
        extra = []
        for op in ops:
            kind, name, kw = op
            if kind == "option":
                self._options[name] = kw
                continue
            if kind == "configure":
                kw = self._scale_kw(kw)
            if name in layers:
                lay = layers[name]
                if kind == "configure":
                    for k, v in kw.items():
                        lay["cfg"]["-" + k] = ttk._format_optdict(
                            {k: self._resolve(v)})[1]
                else:
                    for k, v in kw.items():
                        lay["map"]["-" + k] = ttk._format_mapdict({k: v})[1]
            else:
                extra.append(op)
        # flatten each class chain into its twin (exactly like ttk's lookup:
        # a layer's map, then its configure value, then the next layer)
        for cls in _CLASSES:
            cfg, smap = {}, {}
            decided = set()
            pending = {}
            for n in chains[cls]:
                lay = layers[n]
                for opt, val in lay["map"].items():
                    if opt not in decided:
                        pending.setdefault(opt, []).extend(tcl.splitlist(val))
                for opt, val in lay["cfg"].items():
                    if opt not in decided:
                        cfg[opt] = val
                        decided.add(opt)
            for opt, entries in pending.items():
                smap[opt] = tcl.call("list", *entries)
            twin = self.name(cls)
            # ttk looks up options the twin doesn't define in the *editor's*
            # parent styles (e.g. 'Heading' for 'P1Treeview.Heading'): give
            # such options a neutral value so nothing leaks in
            for parent in self._chain(twin)[1:-1]:
                for raw in (self._query(self.editor_theme, "ttk::style configure %s" % parent),
                            self._query(self.editor_theme, "ttk::style map %s" % parent)):
                    for opt in self._pairs(raw):
                        if opt not in decided and opt not in smap and opt in _NEUTRAL:
                            cfg[opt] = _NEUTRAL[opt]
            args = []
            for k, v in cfg.items():
                args += [k, v]
            if args:
                tcl.call("ttk::style", "configure", twin, *args)
            args = []
            for k, v in smap.items():
                args += [k, v]
            if args:
                tcl.call("ttk::style", "map", twin, *args)
        for op in extra:
            self._apply_op(op, renamed=True)
        # grey hint text of an empty text box (placeholder)
        self._style.configure(self.name("Placeholder.TEntry"), foreground=self.muted())

    def _scale_kw(self, kw):
        """Pixel style options are scaled like the generated app does."""
        if self.scale == 1.0:
            return kw
        return {k: (self.px(v) if k in _PIXEL_STYLE and isinstance(v, (int, float))
                    and not isinstance(v, bool) else v) for k, v in kw.items()}

    def _resolve(self, v):
        if isinstance(v, tuple) and v and v[0] == FAMILY:
            return (self.family,) + v[1:]
        return v

    def _apply_op(self, op, renamed=True):
        kind, name, kw = op
        if kind == "option":
            return
        target = self.name(name) if renamed else name
        try:
            if kind == "configure":
                self._style.configure(target, **{k: self._resolve(v)
                                                 for k, v in self._scale_kw(kw).items()})
            else:
                self._style.map(target, **kw)
        except tk.TclError:
            pass


# --------------------------------------------------------------------------
# Pristine theme settings: a second, hidden Tcl/Tk interpreter whose ttk
# themes nobody has re-configured.  Answers are cached; the interpreter is
# created on first use and closed again right after.
# --------------------------------------------------------------------------

_PRISTINE_CACHE = {}


def _pristine_query(root, theme, cmd):
    key = (theme, cmd)
    if key in _PRISTINE_CACHE:
        v = _PRISTINE_CACHE[key]
        if isinstance(v, Exception):
            raise tk.TclError(str(v))
        return v
    interp = getattr(_pristine_query, "interp", None)
    if interp is None:
        try:
            interp = tk.Tk()
            interp.withdraw()
        except tk.TclError:
            interp = False
        _pristine_query.interp = interp
        if interp:          # close it once the current batch of queries is done
            def close():
                it = getattr(_pristine_query, "interp", None)
                _pristine_query.interp = None
                if it:
                    try:
                        it.destroy()
                    except tk.TclError:
                        pass
            root.after_idle(close)
    if not interp:          # fall back to the editor's own interpreter
        interp = root
    try:
        interp.tk.eval("ttk::style theme settings %s { set ::tkb_q [%s] }" % (theme, cmd))
        v = str(interp.tk.eval("set ::tkb_q"))
    except tk.TclError as e:
        _PRISTINE_CACHE[key] = e
        raise
    _PRISTINE_CACHE[key] = v
    return v


# --------------------------------------------------------------------------
# Local fixes for tkmap (see report): a ttk widget with only its own font
# also needs a personal style (otherwise the font is lost or, with a variant,
# would change every widget of that variant).
# --------------------------------------------------------------------------

_FONT_STYLE_TYPES = ("Label", "Button", "Checkbutton", "Radiobutton")


def _own_font(node):
    if node["type"] not in _FONT_STYLE_TYPES:
        return False
    return tkmap.font_value(node["props"].get("font"), {"size": 10}) is not None


def _style_name(node):
    name = tkmap.style_name(node)
    if _own_font(node) and (name is None or not name.startswith(node["id"] + ".")):
        cls = tkmap.style_class(node)
        variant = node["props"].get("variant") or ""
        return node["id"] + "." + ((variant + ".") if variant else "") + cls
    return name


def _custom_style_ops(node, ctx):
    ops = tkmap.custom_style_ops(node, ctx)
    if _own_font(node) and not (tkmap.style_name(node) or "").startswith(node["id"] + "."):
        name = _style_name(node)
        ops = [op for op in ops if op[1] != tkmap.style_name(node)]
        ops.append(("configure", name, {"font": tkmap.font_value(node["props"]["font"], ctx)}))
    return ops


# --------------------------------------------------------------------------
# Building widgets
# --------------------------------------------------------------------------

class Rendered:
    """The tk widgets that show one node.

    outer   the widget that is placed (gridded) in the parent container
    main    the node's main widget (== outer unless wrapped)
    parts   every other tk widget belonging to the node (wrappers, labels...)
    inner   for containers: the widget children are laid out in
    """

    __slots__ = ("type", "outer", "main", "parts", "inner", "var", "kind")

    def __init__(self, wtype):
        self.type = wtype
        self.outer = self.main = self.inner = self.var = None
        self.parts = []
        self.kind = ""

    def widgets(self):
        out = [self.outer]
        if self.main is not self.outer:
            out.append(self.main)
        return out + [p for p in self.parts if p is not self.outer and p is not self.main]


class ImageCache:
    """PhotoImages by (path, mtime) so rebuilding a widget doesn't reload."""

    def __init__(self, master):
        self.master = master
        self._cache = {}

    def get(self, path, max_size=None):
        try:
            key = (path, os.path.getmtime(path), max_size)
        except OSError:
            return None
        img = self._cache.get(key)
        if img is None:
            try:
                img = tk.PhotoImage(master=self.master, file=path)
            except (tk.TclError, OSError):
                return None
            if max_size:
                f = max(1, -(-max(img.width(), img.height()) // max_size))
                if f > 1:
                    img = img.subsample(f, f)
            self._cache[key] = img
        return img


# pixel-valued constructor options per widget type (scaled for DPI)
_PIXEL_OPTS = {"Frame": ("padding", "width", "height", "borderwidth"),
               "Tab": ("padding",), "LabelFrame": ("padding",),
               "Notebook": ("width", "height"), "Label": ("wraplength",),
               "Scale": ("length",), "Progressbar": ("length",),
               "Canvas": ("width", "height")}


def options(node, styler):
    """Constructor options of a node's main widget, ready for the preview
    (fonts resolved, pixel sizes scaled like the generated app does)."""
    o = tkmap.resolve_options(tkmap.widget_options(node, styler.ctx), styler.family)
    if styler.scale != 1.0:
        for k in _PIXEL_OPTS.get(node["type"], ()):
            if k in o:
                o[k] = styler.px(o[k])
    return o


_opts = options


def _font_for(node, opts, styler):
    t = node["type"]
    if t in _WIDGET_FONT and not opts.get("font"):
        return styler.default_font(t)
    return opts.get("font")


def _fmt_number(v, step):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    try:
        s = float(step)
    except (TypeError, ValueError):
        s = 1
    if s and float(s).is_integer() and v.is_integer():
        return str(int(v))
    dec = 0
    txt = ("%.10f" % s).rstrip("0")
    if "." in txt:
        dec = len(txt.split(".")[1])
    return "%.*f" % (max(dec, 1), v)


def _mix(widget, c1, c2, t):
    """Blend two colours (any Tk colour names) -> '#rrggbb'."""
    try:
        a = widget.winfo_rgb(c1)
        b = widget.winfo_rgb(c2)
    except tk.TclError:
        return c1
    return "#%02x%02x%02x" % tuple(int((x * (1 - t) + y * t) / 257) for x, y in zip(a, b))


def build(parent, node, styler, group_vars=None, images=None):
    """Create the widget(s) for one node inside `parent` (not gridded).

    Containers get an empty `inner` widget; their children are built by the
    caller.  group_vars: dict group name -> StringVar shared by Radiobuttons.
    Raises tk.TclError for impossible option values (caller shows an error)."""
    t = node["type"]
    p = node["props"]
    r = Rendered(t)
    opts = _opts(node, styler)
    st = tkmap.initial_state(node)
    style = styler.node_style(node)
    if style:
        opts["style"] = style
    font = _font_for(node, opts, styler)
    if font:
        opts["font"] = font

    if t in ("Frame", "Tab"):
        w = ttk.Frame(parent, **opts)
        r.inner = w
    elif t == "LabelFrame":
        w = ttk.Labelframe(parent, **opts)
        r.inner = w
    elif t == "Notebook":
        opts["style"] = styler.name("TNotebook")
        w = ttk.Notebook(parent, **opts)
        r.inner = w
    elif t in ("Text", "Console", "Listbox", "Table"):
        holder = parent
        if st.get("scrollbar"):
            holder = ttk.Frame(parent, style=styler.name("TFrame"))
            r.parts.append(holder)
        if t == "Listbox":
            w = tk.Listbox(holder, **opts)
            for it in st.get("items", []):
                w.insert("end", it)
        elif t == "Table":
            opts["style"] = styler.name("Treeview")
            w = ttk.Treeview(holder, **opts)
            _fill_table(w, node, st, styler)
        else:
            w = tk.Text(holder, **opts)
            if t == "Text" and st.get("text"):
                w.insert("1.0", st["text"])
            if t == "Console":
                _console_sample(w, styler)
            if st.get("readonly"):
                w.configure(state="disabled")
        if holder is not parent:
            sb = ttk.Scrollbar(holder, orient="vertical", style=styler.name("Vertical.TScrollbar"),
                               command=w.yview)
            w.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")      # same order as the generated code
            w.pack(side="left", fill="both", expand=True)
            r.parts.append(sb)
            r.outer = holder
        _tk_defaults(w, t, opts)
    elif t == "Checkbutton":
        var = tk.BooleanVar(parent, value=bool(st.get("checked")))
        w = ttk.Checkbutton(parent, variable=var, **opts)
        r.var = var
        if st.get("disable_after_fill"):
            w.state(["disabled"])
    elif t == "Radiobutton":
        group = p.get("group") or ""
        if group_vars is None:
            group_vars = {}
        var = group_vars.get(group)
        if var is None:
            var = group_vars[group] = tk.StringVar(parent, value="")
        w = ttk.Radiobutton(parent, variable=var, **opts)
        r.var = var
        if st.get("selected"):
            var.set(opts.get("value", ""))
    elif t == "Combobox":
        w = ttk.Combobox(parent, **opts)
        if st.get("text"):
            w.set(st["text"])
    elif t == "Spinbox":
        w = ttk.Spinbox(parent, **opts)
        dec = int(p.get("decimals") or 0)
        try:
            val = ("%%.%df" % dec) % float(st.get("value") or 0)
        except (TypeError, ValueError):
            val = str(st.get("value", ""))
        w.insert(0, val)
        if st.get("disable_after_fill"):
            w.state(["disabled"])
    elif t == "Scale":
        orient = opts.get("orient", "horizontal")
        opts["style"] = styler.name(tkmap.style_class(node))
        holder = parent
        if st.get("showvalue"):
            holder = ttk.Frame(parent, style=styler.name("TFrame"))
            r.parts.append(holder)
        w = ttk.Scale(holder, **opts)
        try:
            w.set(st.get("value", 0))
        except tk.TclError:
            pass
        if st.get("disable_after_fill"):
            w.state(["disabled"])
        if holder is not parent:
            res = st.get("resolution") or 1
            width = max(len(_fmt_number(p.get("from_"), res)), len(_fmt_number(p.get("to"), res)))
            lab = ttk.Label(holder, text=_fmt_number(st.get("value", 0), res),
                            style=styler.name("TLabel"), width=width,
                            anchor="e" if orient != "vertical" else "center")
            if orient == "vertical":
                lab.pack(side="top", pady=(0, 2))
                w.pack(side="top", fill="y", expand=True)
            else:
                w.pack(side="left", fill="x", expand=True)
                lab.pack(side="left", padx=(styler.px(6), 0))
            r.parts.append(lab)
            r.outer = holder
    elif t == "FilePicker":
        w = ttk.Frame(parent, style=styler.name("TFrame"))
        entry = ttk.Entry(w, width=opts.get("width", 30), style=styler.name("TEntry"),
                          font=styler.default_font("Entry"))
        if st.get("text"):
            entry.insert(0, st["text"])
        btn = ttk.Button(w, text=p.get("button_text") or "Browse…", style=styler.name("TButton"))
        # laid out exactly like the generated FilePicker class
        entry.grid(row=0, column=0, sticky="ew")
        btn.grid(row=0, column=1, padx=(styler.px(6), 0))
        w.columnconfigure(0, weight=1)
        if p.get("state") == "disabled":
            entry.state(["disabled"])
            btn.state(["disabled"])
        r.parts += [entry, btn]
    elif t in ("Label", "Button"):
        img = None
        if st.get("image"):
            img = images.get(st["image"]) if images is not None else None
            if img is None:
                opts["text"] = "[picture]" + ((" " + opts["text"]) if opts.get("text") else "")
                opts.pop("compound", None)
        if img is not None:
            opts["image"] = img
        w = (ttk.Label if t == "Label" else ttk.Button)(parent, **opts)
    elif t == "Entry":
        w = ttk.Entry(parent, **opts)
        if st.get("text"):
            w.insert(0, st["text"])
        elif st.get("placeholder"):
            w.insert(0, st["placeholder"])
            w.configure(style=styler.name("Placeholder.TEntry"))
            r.kind = "placeholder"
        state = p.get("state")
        if state in ("disabled", "readonly"):
            w.state([state])
    elif t == "Progressbar":
        opts["style"] = styler.name(tkmap.style_class(node))
        w = ttk.Progressbar(parent, **opts)
    elif t == "Separator":
        opts["style"] = styler.name("TSeparator")
        w = ttk.Separator(parent, **opts)
    elif t == "Canvas":
        w = tk.Canvas(parent, **opts)
    else:
        module, clsname, _s = tkmap.WIDGET_CLASS.get(t, ("ttk", "Label", None))
        cls = getattr(tk if module == "tk" else ttk, clsname, ttk.Label)
        w = cls(parent, **opts)

    r.main = w
    if r.outer is None:
        r.outer = w
    return r


def _tk_defaults(w, t, explicit):
    """Classic tk widgets: options the theme/user don't set get the widget's
    built-in default (not whatever the editor put into the option database)."""
    if t == "Table":
        return
    for opt in ("bg", "fg", "selectbackground", "selectforeground", "insertbackground",
                "highlightbackground", "highlightcolor"):
        if opt in explicit:
            continue
        try:
            cur = w.cget(opt)
            default = w.configure(opt)[3]
            if str(cur) != str(default):
                w.configure(**{opt: default})
        except (tk.TclError, IndexError, TypeError):
            continue


def _fill_table(tree, node, st, styler):
    cols = st.get("columns") or tkmap.table_columns(node)
    for i, c in enumerate(cols):
        cid = "c%d" % i
        tree.heading(cid, text=c["title"], anchor=c["anchor"])
        tree.column(cid, width=styler.px(c["width"]), anchor=c["anchor"], stretch=True)
    _table_tags(tree, styler)
    rows = st.get("rows") or []
    striped = st.get("striped")
    if rows:
        for i, row in enumerate(rows):
            vals = list(row) + [""] * (len(cols) - len(row))
            tags = ("odd",) if striped and i % 2 else ()
            tree.insert("", "end", values=vals[:len(cols)], tags=tags)
    else:
        for i in range(SAMPLE_TABLE_ROWS):
            vals = ["sample"] + ["…"] * (len(cols) - 1)
            tags = ("sample", "odd") if striped and i % 2 else ("sample",)
            tree.insert("", "end", values=vals, tags=tags)


def _table_tags(tree, styler):
    field = styler.lookup("Treeview", "fieldbackground") or styler.lookup(
        "Treeview", "background", "#ffffff")
    fg = styler.lookup("Treeview", "foreground", "#000000")
    # same colour as the generated code: the theme's heading colour
    tree.tag_configure("odd", background=(styler.palette or {}).get("heading", "#f3f4f6"))
    tree.tag_configure("sample", foreground=_mix(tree, fg, field, 0.6))


def _console_sample(w, styler):
    if w.get("1.0", "end-1c"):
        return
    try:
        fg = w.cget("fg")
        bg = w.cget("bg")
    except tk.TclError:
        fg, bg = "#d4d4d4", "#1e1e1e"
    w.insert("1.0", CONSOLE_SAMPLE, ("sample",))
    w.tag_configure("sample", foreground=_mix(w, fg, bg, 0.5))


def restyle(r, node, styler):
    """Re-apply the theme-dependent look of an existing rendered node."""
    t = node["type"]
    opts = _opts(node, styler)
    style = styler.node_style(node)
    main = r.main
    if t in ("Frame", "Tab", "LabelFrame"):
        main.configure(style=style)
    elif t == "Notebook":
        main.configure(style=styler.name("TNotebook"))
    elif t in ("Text", "Console", "Listbox"):
        look = {k: v for k, v in opts.items() if k in _TK_LOOK}
        look["font"] = _font_for(node, opts, styler)
        main.configure(**look)
        _tk_defaults(main, t, look)
        if t == "Console":
            try:
                main.tag_configure("sample", foreground=_mix(
                    main, main.cget("fg"), main.cget("bg"), 0.5))
            except tk.TclError:
                pass
    elif t == "Table":
        main.configure(style=styler.name("Treeview"))
        _table_tags(main, styler)
    elif t == "Canvas":
        main.configure(**{k: v for k, v in opts.items() if k in ("bg", "highlightthickness",
                                                                  "highlightbackground")})
    elif t in ("Scale", "Progressbar"):
        main.configure(style=styler.name(tkmap.style_class(node)))
    elif t == "Separator":
        main.configure(style=styler.name("TSeparator"))
    elif t == "Entry":
        main.configure(style=styler.name("Placeholder.TEntry") if r.kind == "placeholder"
                       else style, font=_font_for(node, opts, styler))
    elif t in ("Combobox", "Spinbox"):
        main.configure(style=style, font=_font_for(node, opts, styler))
    elif t == "FilePicker":
        main.configure(style=styler.name("TFrame"))
    elif style:
        main.configure(style=style)
    # parts (wrappers, value labels, file picker entry/button)
    for part in r.parts:
        try:
            cls = part.winfo_class()
            if cls == "TFrame":
                part.configure(style=styler.name("TFrame"))
            elif cls == "TScrollbar":
                part.configure(style=styler.name("Vertical.TScrollbar"))
            elif cls == "TLabel":
                part.configure(style=styler.name("TLabel"))
            elif cls == "TEntry":
                part.configure(style=styler.name("TEntry"), font=styler.default_font("Entry"))
            elif cls == "TButton":
                part.configure(style=styler.name("TButton"))
        except tk.TclError:
            pass


def build_tree(parent, node, styler, images=None, limit=40):
    """Build a node with all its content in a normal widget hierarchy (for
    the drag ghost).  Returns the outer widget.  At most `limit` widgets."""
    count = [0]
    groups = {}
    keep = []

    def rec(par, n):
        count[0] += 1
        r = build(par, n, styler, groups, images)
        keep.append(r)
        if r.inner is not None and count[0] < limit:
            if n["type"] == "Notebook":
                for tab in n.get("children", []):
                    if count[0] >= limit:
                        break
                    count[0] += 1
                    tr = build(r.inner, tab, styler, groups, images)
                    keep.append(tr)
                    r.inner.add(tr.outer, text=tab["props"].get("text", ""))
                    _children(tr.inner, tab)
            else:
                _children(r.inner, n)
        return r

    def _children(frame, n):
        kids = n.get("children", [])
        if not kids:
            if n["type"] in ("Frame", "LabelFrame", "Tab"):
                tk.Frame(frame, width=120, height=36, bg=styler.lookup(
                    "TFrame", "background", "#f0f0f0")).grid(row=0, column=0)
            return
        rows, cols = model.grid_weights(n)
        for i, wt in rows.items():
            frame.rowconfigure(i, weight=wt)
        for i, wt in cols.items():
            frame.columnconfigure(i, weight=wt)
        for ch in kids:
            if count[0] >= limit:
                break
            try:
                cr = rec(frame, ch)
            except tk.TclError:
                continue
            lay = ch["layout"]
            cr.outer.grid(row=lay["row"], column=lay["col"], rowspan=max(1, lay["rowspan"]),
                          columnspan=max(1, lay["colspan"]), sticky=model.sticky_for(lay),
                          padx=styler.px(lay.get("padx", 0)),
                          pady=styler.px(lay.get("pady", 0)))

    outer = rec(parent, node).outer
    outer._tkb_keep = (keep, groups)     # variables must stay alive
    return outer


def all_widgets(w):
    """w and all its tk descendants."""
    out = [w]
    for ch in w.winfo_children():
        out.extend(all_widgets(ch))
    return out
