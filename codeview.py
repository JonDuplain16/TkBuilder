"""
The "Export" window: shows the generated Python files (syntax highlighted,
with line numbers, read-only), lets the user pick the code layout (two files
or one), choose a folder and export. After exporting it tells which files were
written or kept, and offers the methods the user's own file is still missing.

    CodeView(app, project)               the v2 Export window
    CodeView(app, code, filename)        v1 style: just show one piece of code
"""

import io
import keyword
import os
import subprocess
import sys
import tokenize
import tkinter as tk
from tkinter import filedialog, ttk
import tkinter.font as tkfont

import codegen
import theme

LIGHT_COLORS = {"keyword": "#7c3aed", "string": "#15803d", "comment": "#6b7280",
                "number": "#c2410c", "defname": "#2563eb", "self": "#9333ea"}
DARK_COLORS = {"keyword": "#c4a7ff", "string": "#86d993", "comment": "#8b929c",
               "number": "#f0a35e", "defname": "#7cb7ff", "self": "#e3a3f5"}

LAYOUT_HELP = {
    "split": "Two files: {ui} is made again on every export - don't edit it. "
             "{user} is yours: it is created once and never overwritten, so you can "
             "change the design any time without losing your code.",
    "single": "One file with everything. Exporting again replaces it, so keep your own "
              "code somewhere else (or switch to two files).",
}


def _px(n):
    return theme.px(n) if hasattr(theme, "px") else n


def _dark():
    return theme.is_dark() if hasattr(theme, "is_dark") else False


def _code_font(root):
    families = set(tkfont.families(root))
    for fam in (getattr(theme, "MONO_FAMILY", ""), "Cascadia Mono", "Consolas", "Menlo",
                "DejaVu Sans Mono", "Courier New"):
        if fam and fam in families:
            return (fam, 10)
    return ("TkFixedFont", 10)


def highlight_ranges(code):
    """Yield (tag, (row, col), (row, col)) for the parts of `code` to colour.
    Rows start at 1, columns at 0 (same as Tk text indexes)."""
    string_like = {tokenize.STRING}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        if hasattr(tokenize, name):
            string_like.add(getattr(tokenize, name))
    prev = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            ttype, text = tok.type, tok.string
            if ttype in string_like:
                yield "string", tok.start, tok.end
            elif ttype == tokenize.COMMENT:
                yield "comment", tok.start, tok.end
            elif ttype == tokenize.NUMBER:
                yield "number", tok.start, tok.end
            elif ttype == tokenize.NAME:
                if keyword.iskeyword(text):
                    yield "keyword", tok.start, tok.end
                elif prev is not None and prev.type == tokenize.NAME \
                        and prev.string in ("def", "class"):
                    yield "defname", tok.start, tok.end
                elif text == "self":
                    yield "self", tok.start, tok.end
            if ttype not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                prev = tok
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return  # unfinished code: keep whatever was coloured so far


class CodePane(ttk.Frame):
    """Read-only code viewer with line numbers and syntax colours."""

    def __init__(self, master, font):
        super().__init__(master)
        dark = _dark()
        self.colors = DARK_COLORS if dark else LIGHT_COLORS
        code_bg = theme.FIELD if getattr(theme, "FIELD", None) else theme.PANEL
        gutter_bg = theme.BG
        border = tk.Frame(self, bg=theme.BORDER)
        border.pack(fill="both", expand=True)
        inner = tk.Frame(border, bg=code_bg)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.gutter = tk.Text(inner, width=4, padx=_px(6), pady=_px(6), bd=0,
                              highlightthickness=0, bg=gutter_bg, fg=theme.MUTED, font=font,
                              takefocus=False, cursor="arrow", wrap="none")
        self.text = tk.Text(inner, padx=_px(10), pady=_px(6), bd=0, highlightthickness=0,
                            bg=code_bg, fg=theme.TEXT, font=font, wrap="none", undo=False,
                            insertbackground=theme.TEXT, selectbackground=theme.ACCENT_LIGHT,
                            selectforeground=theme.TEXT,
                            inactiveselectbackground=theme.ACCENT_LIGHT)
        vsb = ttk.Scrollbar(inner, orient="vertical", command=self._yview)
        hsb = ttk.Scrollbar(inner, orient="horizontal", command=self.text.xview)

        def on_scroll(first, last):
            vsb.set(first, last)
            self.gutter.yview_moveto(first)
        self.text.configure(yscrollcommand=on_scroll, xscrollcommand=hsb.set)
        self.gutter.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        vsb.grid(row=0, column=2, sticky="ns")
        hsb.grid(row=1, column=1, sticky="ew")
        inner.rowconfigure(0, weight=1)
        inner.columnconfigure(1, weight=1)
        for tag, color in self.colors.items():
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure("defname", font=tuple(font[:2]) + ("bold",))
        self.text.tag_raise("sel")
        self.gutter.tag_configure("right", justify="right")

        # read-only, but selecting / copying / moving around still works
        self.text.bind("<Key>", self._block_typing)
        for seq in ("<<Cut>>", "<<Paste>>", "<<Clear>>", "<<PasteSelection>>", "<Button-2>"):
            self.text.bind(seq, lambda e: "break")
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-A>", self._select_all)
        self.gutter.bind("<MouseWheel>", self._wheel)
        for seq in ("<Button-1>", "<B1-Motion>", "<Double-Button-1>", "<Key>"):
            self.gutter.bind(seq, lambda e: "break")
        self.code = ""

    def show(self, code):
        """Replace the shown code (keeps the scroll position when possible)."""
        if code == self.code:
            return
        top = self.text.yview()[0]
        self.code = code
        code = code.rstrip("\n")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", code)
        for tag, start, end in highlight_ranges(code + "\n"):
            self.text.tag_add(tag, "%d.%d" % start, "%d.%d" % end)
        lines = code.count("\n") + 1
        self.gutter.configure(state="normal", width=max(3, len(str(lines))))
        self.gutter.delete("1.0", "end")
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, lines + 1)), "right")
        self.gutter.configure(state="disabled")
        self.text.yview_moveto(top)
        self.gutter.yview_moveto(top)

    def _yview(self, *args):
        self.text.yview(*args)
        self.gutter.yview_moveto(self.text.yview()[0])

    def _wheel(self, event):
        self.text.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    @staticmethod
    def _block_typing(event):
        if event.state & 0x4 and event.keysym.lower() in ("c", "a", "home", "end", "insert"):
            return None
        if event.keysym in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End",
                            "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return None
        return "break"

    def _select_all(self, _event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"


class CodeView(tk.Toplevel):
    """The Export window (see module docstring)."""

    def __init__(self, app, project=None, filename=None):
        super().__init__(app)
        self.app = app
        if isinstance(project, str):            # v1: CodeView(app, code, filename)
            self.project, self.fixed = None, {filename or "my_app.py": project}
        else:
            self.project, self.fixed = project or getattr(app, "project", None), None
        self.files = {}
        self.panes = {}
        self.missing = []
        self.title("Export Python code")
        self.geometry("%dx%d" % (_px(960), _px(720)))
        self.minsize(_px(620), _px(420))
        self.configure(bg=theme.BG)
        self.transient(app)
        self.font = _code_font(self)
        self._build()
        self.refresh()
        self.bind("<Escape>", lambda e: self.destroy())
        if hasattr(theme, "style_titlebar"):
            try:
                theme.style_titlebar(self)
            except tk.TclError:
                pass
        self.focus_set()

    # ------------------------------------------------------------------ UI
    def _build(self):
        pad = _px(16)
        top = ttk.Frame(self, padding=(pad, _px(14), pad, _px(8)))
        top.pack(side="top", fill="x")
        ttk.Label(top, text="Your app as Python code", style="Heading.TLabel").pack(anchor="w")
        self.layout_help = ttk.Label(top, style="Muted.TLabel", justify="left")
        self.layout_help.pack(anchor="w", fill="x", pady=(_px(4), 0))
        top.bind("<Configure>", lambda e: self.layout_help.configure(
            wraplength=max(200, e.width - 2 * pad)))

        if self.project is not None:
            row = ttk.Frame(top)
            row.pack(anchor="w", pady=(_px(10), 0))
            ttk.Label(row, text="Code layout:").pack(side="left", padx=(0, _px(8)))
            self.style_var = tk.StringVar(
                self, self.project.settings.get("code_style", "split") or "split")
            for value, text in (("split", "Two files (recommended)"),
                                ("single", "One file")):
                ttk.Radiobutton(row, text=text, value=value, variable=self.style_var,
                                command=self._style_changed).pack(side="left",
                                                                  padx=(0, _px(14)))

        # bottom first, so it keeps its place when the window gets small
        bottom = ttk.Frame(self, padding=(pad, _px(8), pad, _px(14)))
        bottom.pack(side="bottom", fill="x")
        if self.project is not None:
            self._build_export_box(bottom)
        buttons = ttk.Frame(bottom)
        buttons.pack(fill="x", pady=(_px(10), 0))
        copy = ttk.Button(buttons, text="Copy this file", command=self.copy)
        copy.pack(side="left")
        self._tip(copy, "Copy the code of the file you are looking at")
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="right")
        self.status = ttk.Label(buttons, style="Muted.TLabel")
        self.status.pack(side="left", padx=_px(10))

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(side="top", fill="both", expand=True, padx=pad)

    def _build_export_box(self, parent):
        box = ttk.Frame(parent)
        box.pack(fill="x")
        ttk.Label(box, text="Export to folder:").grid(row=0, column=0, sticky="w",
                                                      padx=(0, _px(8)))
        self.folder_var = tk.StringVar(self, self._initial_folder())
        entry = ttk.Entry(box, textvariable=self.folder_var)
        entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(box, text="Choose…", command=self.choose_folder).grid(
            row=0, column=2, padx=(_px(6), 0))
        export = ttk.Button(box, text="Export", style="Accent.TButton", command=self.export)
        export.grid(row=0, column=3, padx=(_px(10), 0))
        self._tip(export, "Write the files into this folder (Ctrl+E)")
        self.open_btn = ttk.Button(box, text="Open folder", command=self.open_folder)
        self.open_btn.grid(row=0, column=4, padx=(_px(6), 0))
        box.columnconfigure(1, weight=1)
        self.result = ttk.Label(box, justify="left")
        self.result.grid(row=1, column=0, columnspan=5, sticky="w", pady=(_px(8), 0))
        self.copy_missing = ttk.Button(box, text="Copy the missing methods",
                                       command=self.copy_missing_methods)
        box.bind("<Configure>", lambda e: self.result.configure(
            wraplength=max(200, e.width - _px(20))))
        self.bind("<Control-e>", lambda e: self.export())
        entry.bind("<Return>", lambda e: self.export())

    def _tip(self, widget, text):
        if hasattr(theme, "tooltip"):
            theme.tooltip(widget, text)

    # ------------------------------------------------------------- content
    def refresh(self):
        """Generate the code again and show it (one tab per file)."""
        if self.fixed is not None:
            files = self.fixed
            self.layout_help.configure(
                text="To run it, save it as a .py file and double-click it, or type "
                     "python %s in a terminal." % next(iter(files)))
        else:
            folder = self.folder_var.get().strip() or None
            files = codegen.generate(self.project, folder=folder, project_dir=self._pdir())
            base = self.project.module_basename()
            style = self.project.settings.get("code_style", "split") or "split"
            self.layout_help.configure(text=LAYOUT_HELP.get(style, "").format(
                ui=base + "_ui.py", user=base + ".py"))
        self.files = files
        for name in list(self.panes):
            if name not in files:
                self.tabs.forget(self.panes.pop(name))
        for name, code in files.items():
            pane = self.panes.get(name)
            if pane is None:
                pane = self.panes[name] = CodePane(self.tabs, self.font)
                self.tabs.add(pane, text="  %s  " % self._tab_title(name))
            pane.show(code)
        if self.panes:
            self.tabs.select(self.panes[next(iter(files))])

    def _tab_title(self, name):
        if self.project is None or self.project.settings.get("code_style") == "single":
            return name
        if name.endswith("_ui.py"):
            return name + "  (window - made by TkBuilder)"
        return name + "  (your code)"

    def current_file(self):
        try:
            current = self.tabs.select()
        except tk.TclError:
            return None
        for name, pane in self.panes.items():
            if str(pane) == current:
                return name
        return None

    # ------------------------------------------------------------- folders
    def _pdir(self):
        getter = getattr(self.app, "project_dir", None)
        try:
            return getter() if callable(getter) else None
        except Exception:  # noqa: BLE001 - the editor must not break the export
            return None

    def _initial_folder(self):
        saved = (self.project.settings.get("export_dir") or "").strip()
        base = self._pdir() or os.getcwd()
        if saved:
            return os.path.normpath(os.path.join(base, saved))
        return os.path.normpath(base)

    def _remember_folder(self, folder):
        base = self._pdir()
        value = folder
        if base:
            try:
                rel = os.path.relpath(folder, base)
                if not rel.startswith(".." + os.sep + ".."):
                    value = rel
            except ValueError:          # another drive on Windows
                pass
        value = value.replace("\\", "/")
        if self.project.settings.get("export_dir") != value:
            self.project.settings["export_dir"] = value
            if hasattr(self.app, "commit"):
                self.app.commit("Set the export folder")

    def choose_folder(self):
        folder = filedialog.askdirectory(parent=self, title="Export into which folder?",
                                         initialdir=self.folder_var.get() or None)
        if folder:
            self.folder_var.set(os.path.normpath(folder))
            self.refresh()

    def open_folder(self):
        folder = self.folder_var.get().strip()
        if not os.path.isdir(folder):
            self._say("That folder doesn't exist yet - press Export first.")
            return
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # noqa: S606 - opens Explorer
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])
        except OSError as e:
            self._say("Couldn't open the folder: %s" % e)

    # ------------------------------------------------------------- actions
    def _style_changed(self):
        value = self.style_var.get()
        if self.project.settings.get("code_style") != value:
            self.project.settings["code_style"] = value
            if hasattr(self.app, "commit"):
                self.app.commit("Changed the code layout")
            self.result.configure(text="")
            self.copy_missing.grid_forget()
            self.refresh()

    def export(self):
        """Write the files (the user's own file is never overwritten)."""
        folder = os.path.normpath(os.path.expanduser(self.folder_var.get().strip() or "."))
        try:
            report = codegen.export(self.project, folder, project_dir=self._pdir())
        except OSError as e:
            self._show_result("Couldn't export: %s" % e, error=True)
            return
        self._remember_folder(folder)
        self.refresh()
        names = [os.path.basename(p) for p in report["written"]]
        lines = ["Exported to %s" % folder]
        if names:
            lines.append("Written: " + ", ".join(names))
        for path in report["kept"]:
            lines.append("Kept %s (your code) - TkBuilder never overwrites it."
                         % os.path.basename(path))
        self.missing = report.get("missing_hooks") or []
        if self.missing:
            hooks = [src.split("def ", 1)[1].split("(", 1)[0] for src in self.missing]
            lines.append("Your file doesn't have these methods yet: %s. Copy them and paste "
                         "them into class %s." % (", ".join(hooks), self.project.class_name()))
            self.copy_missing.grid(row=2, column=0, columnspan=5, sticky="w", pady=(_px(6), 0))
        else:
            self.copy_missing.grid_forget()
        self._show_result("\n".join(lines))
        if hasattr(self.app, "toast"):
            try:
                self.app.toast("Exported to %s" % folder)
            except Exception:  # noqa: BLE001 - a missing toast is not worth an error
                pass
        return report

    def _show_result(self, text, error=False):
        self.result.configure(text=text, foreground=theme.DANGER if error else theme.TEXT)

    def copy_missing_methods(self):
        self._clip("\n".join(self.missing))
        self._say("Copied - paste them into your class.")

    def copy(self):
        """Copy the file shown in the current tab."""
        name = self.current_file()
        if name:
            self._clip(self.files[name])
            self._say("Copied %s." % name)

    def _clip(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()  # keep the clipboard filled after this window closes

    def _say(self, text):
        self.status.configure(text=text)
