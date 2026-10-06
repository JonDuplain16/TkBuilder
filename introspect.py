"""
Look inside a user's Python file WITHOUT running it (uses `ast`), so the
"Call a Python function" action can offer a list of functions and their
arguments.  Safe for any file: nothing is imported or executed.
"""

import ast
import os


def _expr(node):
    """Short source text for a default value / annotation node."""
    if node is None:
        return None
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 - ast.unparse can fail on odd nodes
        return "\u2026"


def _args_of(fn, skip_self=False):
    a = fn.args
    out = []
    positional = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    for arg, default in zip(positional, defaults):
        out.append({"name": arg.arg, "default": _expr(default),
                    "annotation": _expr(arg.annotation), "kind": "positional"})
    if a.vararg:
        out.append({"name": a.vararg.arg, "default": None,
                    "annotation": _expr(a.vararg.annotation), "kind": "varargs"})
    for arg, default in zip(a.kwonlyargs, a.kw_defaults):
        out.append({"name": arg.arg, "default": _expr(default),
                    "annotation": _expr(arg.annotation), "kind": "keyword"})
    if a.kwarg:
        out.append({"name": a.kwarg.arg, "default": None,
                    "annotation": _expr(a.kwarg.annotation), "kind": "varkw"})
    if skip_self and out and out[0]["name"] in ("self", "cls"):
        out = out[1:]
    return out


def list_functions(path):
    """Return the public top-level functions of a .py file.

    [{"name", "args": [{"name", "default", "annotation", "kind"}],
      "returns", "doc", "lineno", "is_async"}]
    Raises OSError / SyntaxError with the original message on problems.
    """
    with open(path, "r", encoding="utf-8-sig") as f:
        source = f.read()
    tree = ast.parse(source, filename=path)
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and not node.name.startswith("_"):
            out.append({"name": node.name, "args": _args_of(node),
                        "returns": _expr(node.returns),
                        "doc": (ast.get_docstring(node) or "").strip(),
                        "lineno": node.lineno,
                        "is_async": isinstance(node, ast.AsyncFunctionDef)})
    return out


def signature_text(fn):
    """'compute(x, y=2.0) -> float' for display."""
    parts = []
    for a in fn["args"]:
        s = a["name"]
        if a["kind"] == "varargs":
            s = "*" + s
        elif a["kind"] == "varkw":
            s = "**" + s
        if a.get("annotation"):
            s += ": " + a["annotation"]
        if a.get("default") is not None:
            s += " = " + a["default"]
        parts.append(s)
    text = "%s(%s)" % (fn["name"], ", ".join(parts))
    if fn.get("returns"):
        text += " -> " + fn["returns"]
    return text


def module_name_for(path, base_dir=None):
    """Module name to import a file by, relative to base_dir if it's inside it.

    module_name_for("C:/proj/tools/calc.py", "C:/proj") -> "tools.calc"
    module_name_for("C:/other/calc.py", "C:/proj")      -> "calc"
    """
    path = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    if base_dir:
        base = os.path.abspath(base_dir)
        try:
            rel = os.path.relpath(path, base)
        except ValueError:          # different drive on Windows
            return stem
        if not rel.startswith(".."):
            parts = os.path.splitext(rel)[0].replace("\\", "/").split("/")
            if all(p.isidentifier() for p in parts):
                return ".".join(parts)
    return stem


def find_module_file(module, search_dirs):
    """Locate 'pkg.mod' as a .py file inside any of search_dirs (or None)."""
    rel = module.replace(".", os.sep) + ".py"
    for d in search_dirs:
        if d:
            p = os.path.join(d, rel)
            if os.path.isfile(p):
                return p
    return None


def param_widget_hint(arg):
    """Guess the best input widget for an argument (used by 'Create inputs for
    me'): returns a widget type and props."""
    ann = (arg.get("annotation") or "").lower()
    default = arg.get("default")
    if ann in ("bool",) or default in ("True", "False"):
        return "Checkbutton", {"text": arg["name"].replace("_", " ").capitalize(),
                               "checked": default == "True"}
    if ann in ("int", "float") or _looks_number(default):
        is_int = ann == "int" or (default is not None and _looks_int(default))
        props = {"value_type": "int" if is_int else "float"}
        if default is not None and _looks_number(default):
            props["text"] = default
        return "Entry", props
    if any(k in arg["name"].lower() for k in ("path", "file", "folder", "dir")):
        return "FilePicker", {"mode": "folder" if ("folder" in arg["name"].lower()
                                                   or "dir" in arg["name"].lower()) else "open",
                              "text": (default or "").strip("'\"")}
    props = {}
    if default and default not in ("None",):
        props["text"] = default.strip("'\"")
    return "Entry", props


def _looks_number(text):
    if text is None:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def _looks_int(text):
    try:
        int(text)
        return True
    except ValueError:
        return False
