"""
TkBuilder main application window.

Layout:
    +-------------------------------------------------------------+
    | menu bar:  File  Edit  View  Insert  Run  Help              |
    | toolbar:   file | undo redo | clipboard | + Window ... Run  |
    +-----------+-------------------------------+-----------------+
    | Palette   |  window bar (Main | Win 2 | +)| Inspector       |
    |-----------|        Workplane              |  (or the app    |
    | Outline   |   (the design surface)        |   settings)     |
    +-----------+-------------------------------+-----------------+
    | status bar                                                  |
    +-------------------------------------------------------------+
The start page (startpage.py) replaces the body when no project is open.

The App object is the hub every panel talks to (see SPEC.md / SPEC2.md).

Command line:
    python app.py [project.tkb]                  open the editor
    python app.py --export project.tkb [folder]  regenerate the code, no GUI
"""

import datetime
import inspect
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import model
from model import Project, WIDGETS
import prefs as prefs_mod
import theme

APP_TITLE = "TkBuilder"
APP_VERSION = "2.0"
FILE_TYPES = [("TkBuilder project", "*.tkb"), ("All files", "*.*")]
MAX_UNDO = 200
AUTOSAVE_MS = 60_000
HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(HERE, "examples")

# (keys, what it does) - shown by Help > Keyboard shortcuts
SHORTCUTS = [
    ("Ctrl+N", "New project (start page)"),
    ("Ctrl+O", "Open a project"),
    ("Ctrl+S / Ctrl+Shift+S", "Save / Save as"),
    ("Ctrl+Z / Ctrl+Y", "Undo / Redo"),
    ("Ctrl+X / Ctrl+C / Ctrl+V", "Cut / Copy / Paste the selected widget"),
    ("Ctrl+D", "Duplicate the selected widget"),
    ("Delete", "Delete the selected widget"),
    ("Arrow keys", "Move the selected widget one cell"),
    ("Esc", "Cancel a drag / clear the selection"),
    ("F5 / Shift+F5", "Run my app / Stop it"),
    ("Ctrl+E", "Export the Python code"),
    ("F1", "This list"),
]


def install_silent_dialogs():
    """Test mode: replace every standard pop-up with an instant answer so
    automated runs never wait for a click. Enabled by setting the environment
    variable TKBUILDER_NO_DIALOGS=1 (or by calling this before creating App).
    Each suppressed dialog is logged to stderr."""
    from tkinter import colorchooser, simpledialog

    def fake(name, answer):
        def f(*args, **kwargs):
            text = " | ".join(str(a) for a in args) or kwargs.get("message", "")
            sys.stderr.write("[silent dialog] %s: %s -> %r\n" % (name, str(text)[:200], answer))
            return answer
        return f

    for name in ("showinfo", "showwarning", "showerror"):
        setattr(messagebox, name, fake(name, "ok"))
    messagebox.askyesno = fake("askyesno", True)           # "Delete …?" -> yes
    messagebox.askokcancel = fake("askokcancel", True)
    messagebox.askretrycancel = fake("askretrycancel", False)
    messagebox.askquestion = fake("askquestion", "yes")
    messagebox.askyesnocancel = fake("askyesnocancel", False)  # "Save first?" -> no, discard
    for name in ("askopenfilename", "asksaveasfilename", "askdirectory"):
        setattr(filedialog, name, fake(name, ""))          # cancel
    filedialog.askopenfilenames = fake("askopenfilenames", ())
    for name in ("askstring", "askinteger", "askfloat"):
        setattr(simpledialog, name, fake(name, None))
    colorchooser.askcolor = fake("askcolor", (None, None))


TEST_MODE = bool(os.environ.get("TKBUILDER_NO_DIALOGS"))
if TEST_MODE:
    install_silent_dialogs()


def _auto_close(win):
    """In test mode, TkBuilder's own windows close themselves after
    TKBUILDER_DIALOG_TIMEOUT seconds so nothing waits for a click."""
    if TEST_MODE:
        secs = float(os.environ.get("TKBUILDER_DIALOG_TIMEOUT", "8"))
        win.after(int(secs * 1000), lambda: win.winfo_exists() and win.destroy())


# --------------------------------------------------------------------------
# Code generation helpers (shared by the GUI and the command line)
# --------------------------------------------------------------------------

def _paths_kw(func, folder, project_dir):
    """folder/project_dir keyword arguments if codegen accepts them (v2)."""
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return {}
    kw = {}
    if "folder" in params and folder:
        kw["folder"] = folder
    if "project_dir" in params and project_dir:
        kw["project_dir"] = project_dir
    return kw


def generate_files(project, folder=None, project_dir=None):
    """{filename: source} for the project (works with codegen v1 and v2).
    folder / project_dir let relative backend folders resolve correctly."""
    import codegen
    result = codegen.generate(project, **_paths_kw(codegen.generate, folder, project_dir))
    if isinstance(result, str):
        return {project.module_basename() + ".py": result}
    return dict(result)


def export_files(project, folder, project_dir=None):
    """Write the generated files into `folder`, never overwriting the user's
    own <base>.py (split style). Returns codegen's report dict."""
    import codegen
    os.makedirs(folder, exist_ok=True)
    if hasattr(codegen, "export"):
        return codegen.export(project, folder,
                              **_paths_kw(codegen.export, None, project_dir))
    report = {"written": [], "kept": [], "missing_hooks": []}
    for name, source in generate_files(project, folder, project_dir).items():
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        report["written"].append(path)
    return report


def resolve_dir(value, base):
    """A folder setting -> absolute path ('' stays '')."""
    if not value:
        return ""
    value = os.path.expanduser(value)
    return os.path.normpath(value if os.path.isabs(value) else os.path.join(base, value))


def relative_dir(path, base):
    """Store a folder relative to `base` when that's possible and tidy."""
    try:
        rel = os.path.relpath(path, base)
    except ValueError:              # another drive
        return path
    return path if rel.startswith("..%s.." % os.sep) else rel.replace("\\", "/")


def python_exe():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        console = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(console):
            exe = console
    return exe


# --------------------------------------------------------------------------
# Small windows
# --------------------------------------------------------------------------

class _Dialog(tk.Toplevel):
    """Base for TkBuilder's small, non-blocking windows."""

    def __init__(self, app, title, width=None):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title(title)
        self.configure(bg=theme.BG)
        self.transient(app)
        self.resizable(True, True)
        self.bind("<Escape>", lambda e: self.destroy())
        self.body = ttk.Frame(self, padding=theme.px(16))
        self.body.pack(fill="both", expand=True)
        self._width = width
        _auto_close(self)

    def footer(self, *buttons):
        bar = ttk.Frame(self.body)
        bar.pack(side="bottom", fill="x", pady=(theme.px(12), 0))
        for text, cmd, style in reversed(buttons):
            ttk.Button(bar, text=text, command=cmd, style=style).pack(side="right",
                                                                      padx=(theme.px(8), 0))
        return bar

    def show(self):
        self.update_idletasks()
        w = max(self.winfo_reqwidth(), theme.px(self._width or 0))
        h = self.winfo_reqheight()
        x = self.app.winfo_rootx() + (self.app.winfo_width() - w) // 2
        y = self.app.winfo_rooty() + max(0, (self.app.winfo_height() - h) // 3)
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, x), max(0, y)))
        self.deiconify()
        self.lift()
        self.focus_set()


class TextDialog(_Dialog):
    """A message plus scrollable details (e.g. a traceback) with Copy/Close."""

    def __init__(self, app, title, message, details="", heading=None):
        super().__init__(app, title, width=640)
        if heading:
            ttk.Label(self.body, text=heading, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(self.body, text=message, wraplength=theme.px(600), justify="left").pack(
            anchor="w", pady=(theme.px(4), theme.px(8)))
        self.footer(("Copy details", lambda: self._copy(details), "TButton"),
                    ("Close", self.destroy, "Accent.TButton"))
        if details:
            box = ttk.Frame(self.body)
            box.pack(fill="both", expand=True)
            text = tk.Text(box, height=14, width=88, wrap="none", relief="flat",
                           font=(theme.MONO_FAMILY, 9), bg=theme.FIELD, fg=theme.TEXT,
                           highlightthickness=1, highlightbackground=theme.BORDER,
                           padx=theme.px(8), pady=theme.px(6))
            vsb = ttk.Scrollbar(box, orient="vertical", command=text.yview)
            hsb = ttk.Scrollbar(box, orient="horizontal", command=text.xview)
            text.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
            text.grid(row=0, column=0, sticky="nsew")
            vsb.grid(row=0, column=1, sticky="ns")
            hsb.grid(row=1, column=0, sticky="ew")
            box.rowconfigure(0, weight=1)
            box.columnconfigure(0, weight=1)
            text.insert("1.0", details)
            text.see("end")
            text.configure(state="disabled")
        self.show()

    def _copy(self, details):
        self.clipboard_clear()
        self.clipboard_append(details)
        self.app.toast("Copied to the clipboard")


class ShortcutsDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "Keyboard shortcuts", width=460)
        ttk.Label(self.body, text="Keyboard shortcuts", style="Heading.TLabel").pack(anchor="w")
        grid = ttk.Frame(self.body)
        grid.pack(fill="x", pady=(theme.px(8), 0))
        for i, (keys, what) in enumerate(SHORTCUTS):
            ttk.Label(grid, text=keys, font=(theme.UI_FAMILY, 10, "bold")).grid(
                row=i, column=0, sticky="w", padx=(0, theme.px(24)), pady=theme.px(2))
            ttk.Label(grid, text=what).grid(row=i, column=1, sticky="w", pady=theme.px(2))
        ttk.Label(self.body, style="Muted.TLabel", wraplength=theme.px(420), justify="left",
                  text="Tip: double-click a label or button in the design to edit its text, "
                       "right-click a widget for more.").pack(anchor="w", pady=(theme.px(12), 0))
        self.footer(("Close", self.destroy, "Accent.TButton"))
        self.show()


class AboutDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "About TkBuilder", width=420)
        ttk.Label(self.body, text="TkBuilder %s" % APP_VERSION,
                  font=(theme.UI_FAMILY, 16, "bold")).pack(anchor="w")
        ttk.Label(self.body, wraplength=theme.px(400), justify="left", text=(
            "Design Tkinter desktop apps by drag and drop, wire them to your own Python "
            "functions without writing GUI code, and export clean Python that runs with "
            "the standard library only.")).pack(anchor="w", pady=(theme.px(6), theme.px(10)))
        ttk.Label(self.body, style="Muted.TLabel", text="Python %s  ·  Tk %s" % (
            sys.version.split()[0], app.tk.call("info", "patchlevel"))).pack(anchor="w")
        self.footer(("Close", self.destroy, "Accent.TButton"))
        self.show()


class PreferencesDialog(_Dialog):
    def __init__(self, app):
        super().__init__(app, "Preferences", width=440)
        p = theme.px
        ttk.Label(self.body, text="Preferences", style="Heading.TLabel").pack(anchor="w")

        ttk.Label(self.body, text="EDITOR APPEARANCE", style="Section.TLabel").pack(
            anchor="w", pady=(p(12), p(4)))
        self.mode = tk.StringVar(value=theme.MODE)
        row = ttk.Frame(self.body)
        row.pack(anchor="w")
        for value, label in (("light", "Light"), ("dark", "Dark")):
            ttk.Radiobutton(row, text=label, value=value, variable=self.mode,
                            command=self._mode_changed).pack(side="left", padx=(0, p(16)))
        ttk.Label(self.body, style="Muted.TLabel", wraplength=p(400), justify="left",
                  text="Only changes TkBuilder itself. Your app's look is chosen under "
                       "App settings ▸ Theme.").pack(anchor="w", pady=(p(2), 0))

        ttk.Label(self.body, text="SAFETY", style="Section.TLabel").pack(
            anchor="w", pady=(p(14), p(4)))
        self.autosave = tk.BooleanVar(value=bool(app.prefs.get("autosave", True)))
        ttk.Checkbutton(self.body, text="Save a recovery copy every minute",
                        variable=self.autosave, command=self._changed).pack(anchor="w")

        ttk.Label(self.body, text="SCREEN", style="Section.TLabel").pack(
            anchor="w", pady=(p(14), p(4)))
        self.dpi = tk.BooleanVar(value=bool(app.prefs.get("dpi_aware", True)))
        ttk.Checkbutton(self.body, text="Sharp text on high-resolution screens "
                                        "(takes effect after a restart)",
                        variable=self.dpi, command=self._changed).pack(anchor="w")

        ttk.Label(self.body, text="RECENT PROJECTS", style="Section.TLabel").pack(
            anchor="w", pady=(p(14), p(4)))
        self.clear_btn = ttk.Button(self.body, text="Clear the recent projects list",
                                    command=self._clear_recent)
        self.clear_btn.pack(anchor="w")
        if not app.prefs.get("recent"):
            self.clear_btn.state(["disabled"])

        self.footer(("Close", self.destroy, "Accent.TButton"))
        self.show()

    def _changed(self):
        self.app.prefs["autosave"] = self.autosave.get()
        self.app.prefs["dpi_aware"] = self.dpi.get()
        self.app.save_prefs()

    def _clear_recent(self):
        self.app.prefs["recent"] = []
        self.app.save_prefs()
        self.clear_btn.state(["disabled"])
        self.app.toast("Recent projects list cleared")

    def _mode_changed(self):
        app = self.app
        self.destroy()
        app.set_editor_mode(self.mode.get())
        app.after_idle(app.show_preferences)


# --------------------------------------------------------------------------
# The application
# --------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, path=None, start_page=None):
        self.prefs = prefs_mod.load()
        if self.prefs.get("dpi_aware", True):
            theme.enable_dpi_awareness()
        super().__init__()
        theme.set_mode(self.prefs.get("editor_mode", "light"))
        theme.apply(self)
        self.title(APP_TITLE)
        self._initial_geometry()

        self.project = Project()
        self.file_path = None
        self.dirty = False
        self.selected_id = None
        self._undo = []
        self._redo = []
        self._last_merge_key = None
        self._restoring = False
        self._base_dir = None          # folder of an opened template (until saved)
        self._clip = None              # last copied widgets (if the clipboard fails)
        self._in_clip = False
        self._running = []             # processes started by Run My App
        self._toast = None
        self._toast_job = None
        self.start_page = None
        self.start_visible = False
        self.palette = self.outline = self.workplane = self.inspector = None
        self.body = None

        if start_page is None:
            start_page = not path and not TEST_MODE
        if start_page:      # paint the start page first: it appears instantly
            self.show_start_page()
            self.update()
        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        self._reset_history()
        self.refresh_all()
        self.select(None)
        self.status("Welcome! Drag widgets from the left onto the window in the middle.")

        if path:
            self.open_project(path)
        if start_page:
            self.start_page.refresh()
        elif self.autosave_info() and not TEST_MODE:
            self.after(300, self._offer_recovery)
        self._autosave_job = self.after(AUTOSAVE_MS, self._autosave_tick)

    def _initial_geometry(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w = min(theme.px(1360), int(sw * 0.92))
        h = min(theme.px(840), int(sh * 0.88))
        self.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(0, (sh - h) // 3)))
        self.minsize(min(theme.px(980), w), min(theme.px(600), h))

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        """Build menu bar, toolbar, panels and status bar for the current
        editor mode. Called again (after destroying them) when the mode changes."""
        from palette import Palette
        from outline import Outline
        from workplane import Workplane
        from inspector import Inspector

        self._build_menu()
        self.body = ttk.Frame(self)
        if not self.start_visible:
            self.body.pack(fill="both", expand=True)
        self._build_toolbar(self.body)
        ttk.Separator(self.body, orient="horizontal").pack(side="top", fill="x")

        status = ttk.Frame(self.body, style="Toolbar.TFrame")
        status.pack(side="bottom", fill="x")
        self.statusbar = ttk.Label(status, style="Status.TLabel", anchor="w")
        self.statusbar.pack(side="left", fill="x", expand=True)
        self.status_right = ttk.Label(status, style="Status.TLabel", anchor="e")
        self.status_right.pack(side="right")

        main = ttk.PanedWindow(self.body, orient="horizontal")
        main.pack(fill="both", expand=True)
        left = ttk.PanedWindow(main, orient="vertical")
        self.palette = Palette(left, self)
        self.outline = Outline(left, self)
        left.add(self.palette, weight=3)
        left.add(self.outline, weight=2)
        self.workplane = Workplane(main, self)
        self.inspector = Inspector(main, self)
        if hasattr(self.inspector, "start_prebuild") and not os.environ.get("TKBUILDER_NO_PREBUILD"):
            self.inspector.start_prebuild()     # build editor pages while the user is idle
        main.add(left, weight=0)
        main.add(self.workplane, weight=1)
        main.add(self.inspector, weight=0)
        self._main_pane = main

    def _icon(self, glyph, fallback):
        return glyph if theme.ICON_FAMILY else fallback

    def _build_toolbar(self, parent):
        p = theme.px
        bar = ttk.Frame(parent, style="Toolbar.TFrame", padding=(p(8), p(4)))
        bar.pack(side="top", fill="x")
        self.toolbar = bar

        def btn(icon, fallback, cmd, tip):
            text = self._icon(icon, fallback)
            style = "Icon.Toolbar.TButton" if theme.ICON_FAMILY else "Toolbar.TButton"
            b = ttk.Button(bar, text=text, command=cmd, style=style)
            b.pack(side="left", padx=p(1))
            theme.tooltip(b, tip)
            return b

        def text_btn(text, cmd, tip, style="Toolbar.TButton", side="left"):
            b = ttk.Button(bar, text=text, command=cmd, style=style)
            b.pack(side=side, padx=p(3) if side == "left" else (p(6), 0))
            theme.tooltip(b, tip)
            return b

        def sep():
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=p(8),
                                                       pady=p(4))

        btn("", "\U0001F4C4", self.show_start_page, "New project (Ctrl+N)")
        btn("", "\U0001F4C2", self.open_project, "Open a project (Ctrl+O)")
        btn("", "\U0001F4BE", self.save_project, "Save (Ctrl+S)")
        sep()
        self.undo_btn = btn("", "↶", self.undo, "Undo (Ctrl+Z)")
        self.redo_btn = btn("", "↷", self.redo, "Redo (Ctrl+Y)")
        sep()
        btn("", "✂", self.clipboard_cut, "Cut the selected widget (Ctrl+X)")
        btn("", "⧉", self.clipboard_copy, "Copy the selected widget (Ctrl+C)")
        btn("", "\U0001F4CB", self.clipboard_paste, "Paste (Ctrl+V)")
        btn("", "\U0001F5D1", self.delete_selected, "Delete the selected widget (Delete)")
        sep()
        text_btn("＋ Window", self.add_window, "Add another window to your app")
        text_btn("⚙ App settings", self.open_settings,
                 "Name, theme, fonts and code options of your app")

        text_btn("▶  Run", self.run_preview, "Try your app for real (F5)",
                 "Run.TButton", side="right")
        text_btn("Export code", self.export_project,
                 "See the generated Python and save it to a folder (Ctrl+E)",
                 "Accent.TButton", side="right")

    def _build_menu(self):
        m = tk.Menu(self)
        self.menubar = m
        self.configure(menu=m)

        def menu(label):
            sub = tk.Menu(m, tearoff=False)
            m.add_cascade(label=label, menu=sub, underline=0)
            return sub

        f = menu("File")
        f.add_command(label="New…", accelerator="Ctrl+N", command=self.show_start_page)
        f.add_command(label="Open…", accelerator="Ctrl+O", command=self.open_project)
        self.recent_menu = tk.Menu(f, tearoff=False, postcommand=self._fill_recent_menu)
        f.add_cascade(label="Open recent", menu=self.recent_menu)
        f.add_separator()
        f.add_command(label="Save", accelerator="Ctrl+S", command=self.save_project)
        f.add_command(label="Save as…", accelerator="Ctrl+Shift+S",
                      command=lambda: self.save_project(save_as=True))
        f.add_separator()
        f.add_command(label="Export Python code…", accelerator="Ctrl+E",
                      command=self.export_project)
        f.add_separator()
        f.add_command(label="Preferences…", command=self.show_preferences)
        f.add_separator()
        f.add_command(label="Exit", command=self.quit_app)

        e = menu("Edit")
        self.edit_menu = e
        e.add_command(label="Undo", accelerator="Ctrl+Z", command=self.undo)
        e.add_command(label="Redo", accelerator="Ctrl+Y", command=self.redo)
        e.add_separator()
        e.add_command(label="Cut", accelerator="Ctrl+X", command=self.clipboard_cut)
        e.add_command(label="Copy", accelerator="Ctrl+C", command=self.clipboard_copy)
        e.add_command(label="Paste", accelerator="Ctrl+V", command=self.clipboard_paste)
        e.add_command(label="Duplicate", accelerator="Ctrl+D", command=self.duplicate_selected)
        e.add_command(label="Delete", accelerator="Del", command=self.delete_selected)
        e.add_separator()
        e.add_command(label="Select the window", command=self._select_window)
        e.add_command(label="Clear the selection", accelerator="Esc",
                      command=lambda: self.select(None))

        v = menu("View")
        self._mode_var = tk.StringVar(value=theme.MODE)
        v.add_radiobutton(label="Light editor", value="light", variable=self._mode_var,
                          command=lambda: self.set_editor_mode("light"))
        v.add_radiobutton(label="Dark editor", value="dark", variable=self._mode_var,
                          command=lambda: self.set_editor_mode("dark"))
        v.add_separator()
        v.add_command(label="App settings…", command=self.open_settings)
        v.add_command(label="Start page", command=self.show_start_page)

        ins = menu("Insert")
        first = True
        for cat in model.PALETTE_CATEGORIES:
            items = [(k, s) for k, s in WIDGETS.items()
                     if s.get("category") == cat and s.get("palette", True) is not False]
            if not items:
                continue
            if not first:
                ins.add_separator()
            first = False
            for wtype, spec in items:
                ins.add_command(label="%s  %s" % (spec["icon"], spec["name"]),
                                command=lambda t=wtype: self.add_widget(t))
        ins.add_separator()
        ins.add_command(label="New window", command=self.add_window)

        r = menu("Run")
        r.add_command(label="Run my app", accelerator="F5", command=self.run_preview)
        r.add_command(label="Stop my app", accelerator="Shift+F5", command=self.stop_running)
        r.add_separator()
        r.add_command(label="Export / show the code…", accelerator="Ctrl+E",
                      command=self.export_project)
        r.add_command(label="Open the export folder", command=self.open_export_folder)

        h = menu("Help")
        h.add_command(label="Keyboard shortcuts", accelerator="F1", command=self.show_shortcuts)
        h.add_command(label="Examples and templates", command=self.show_start_page)
        h.add_separator()
        h.add_command(label="About TkBuilder", command=lambda: AboutDialog(self))

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.delete(0, "end")
        recent = [p for p in self.prefs.get("recent", [])]
        if not recent:
            menu.add_command(label="(none yet)", state="disabled")
            return
        for i, path in enumerate(recent):
            label = "%d  %s" % (i + 1, os.path.basename(path))
            menu.add_command(label=label, underline=0,
                             command=lambda p=path: self.open_recent(p))
        menu.add_separator()
        menu.add_command(label="Clear this list", command=self._clear_recent)

    def _clear_recent(self):
        self.prefs["recent"] = []
        self.save_prefs()

    def _bind_keys(self):
        def unless_typing(fn):
            def handler(event):
                w = self.focus_get()
                if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, tk.Spinbox, ttk.Spinbox,
                                  ttk.Combobox)):
                    return None
                fn()
                return "break"
            return handler

        def always(fn):
            def handler(event):
                fn()
                return "break"
            return handler

        b = self.bind_all
        b("<Control-z>", unless_typing(self.undo))
        b("<Control-y>", unless_typing(self.redo))
        b("<Control-Z>", unless_typing(self.redo))
        b("<Control-x>", unless_typing(self.clipboard_cut))
        b("<Control-c>", unless_typing(self.clipboard_copy))
        b("<Control-v>", unless_typing(self.clipboard_paste))
        b("<Control-d>", unless_typing(self.duplicate_selected))
        b("<Delete>", unless_typing(self.delete_selected))
        b("<Control-s>", always(self.save_project))
        b("<Control-S>", always(lambda: self.save_project(save_as=True)))
        b("<Control-o>", always(self.open_project))
        b("<Control-n>", always(self.show_start_page))
        b("<Control-e>", always(self.export_project))
        b("<F5>", always(self.run_preview))
        b("<Shift-F5>", always(self.stop_running))
        b("<F1>", always(self.show_shortcuts))
        b("<Escape>", unless_typing(self._escape))

    def _escape(self):
        if self.start_visible:
            if self._has_project_open():
                self.hide_start_page()
        else:
            self.select(None)

    def set_editor_mode(self, mode):
        """Switch the editor between light and dark (rebuilds the panels)."""
        mode = "dark" if mode == "dark" else "light"
        self.prefs["editor_mode"] = mode
        self.save_prefs()
        if mode == theme.MODE:
            return
        theme.set_mode(mode)
        theme.apply(self)
        self._rebuild_ui()
        self.toast("Dark editor" if mode == "dark" else "Light editor")

    def _rebuild_ui(self):
        window_id = self.workplane.current_window_id if self.workplane else None
        selected = self.selected_id
        start = self.start_visible
        for w in (self.body, self.start_page, self._toast):
            if w is not None:
                w.destroy()
        self.start_page = self._toast = None
        self.start_visible = False
        try:
            self.menubar.destroy()
        except tk.TclError:
            pass
        self._build_ui()
        if window_id and self.project.find(window_id):
            self.workplane.set_window(window_id)
        self._update_history_buttons()
        self.refresh_all()
        self.select(selected)
        if start:
            self.show_start_page()

    # --------------------------------------------------------- start page
    def _has_project_open(self):
        """True when there is something worth going back to."""
        return bool(self.file_path or self.dirty or len(self._undo) > 1
                    or any(w.get("children") for w in self.project.windows)
                    or len(self.project.windows) > 1)

    def show_start_page(self):
        from startpage import StartPage
        if self.start_page is None:
            self.start_page = StartPage(self, self)
        if not self.start_visible:
            if self.body is not None:
                self.body.pack_forget()
            self.start_page.pack(fill="both", expand=True)
            self.start_visible = True
        self.start_page.refresh()
        self.start_page.focus_set()

    def hide_start_page(self):
        if self.start_visible:
            self.start_page.pack_forget()
            self.body.pack(fill="both", expand=True)
            self.start_visible = False

    def new_blank(self):
        """Start page: an empty project."""
        if not self.confirm_discard():
            return False
        self._load(Project(), None)
        self.hide_start_page()
        self.status("New project. Drag widgets from the left onto your window.")
        return True

    def open_template(self, path):
        """Open an example as a new, unsaved project (so it isn't overwritten)."""
        if not self.confirm_discard():
            return False
        project = self._read_project(path)
        if project is None:
            return False
        self._load(project, None)
        self._base_dir = os.path.dirname(os.path.abspath(path))
        self.hide_start_page()
        self.status("Opened a template. Press ▶ Run to try it, then change it however you like.")
        return True

    open_example = open_template   # v1 name

    def open_recent(self, path):
        if not os.path.exists(path):
            messagebox.showinfo(APP_TITLE, "This project isn't there any more:\n\n%s" % path,
                                parent=self)
            prefs_mod.remove_recent(self.prefs, path)
            self.save_prefs()
            if self.start_visible:
                self.start_page.refresh()
            return False
        if not self.confirm_discard():
            return False
        return self.open_project(path, confirm=False)

    # ------------------------------------------------------- selection etc
    def status(self, text):
        try:
            self.statusbar.configure(text=text)
        except (tk.TclError, AttributeError):
            pass

    def toast(self, text, ms=2600):
        """Show a small notification at the bottom right for a moment
        (doesn't block or take the focus)."""
        if self._toast is None or not self._toast.winfo_exists():
            self._toast = tk.Label(self, bg=theme.TOOLTIP_BG, fg=theme.TOOLTIP_FG,
                                   font=(theme.UI_FAMILY, 10), padx=theme.px(14),
                                   pady=theme.px(7))
        t = self._toast
        t.configure(text=text)
        t.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        target = -theme.px(40)
        steps = [target + theme.px(d) for d in (14, 8, 4, 1, 0)]

        def slide(i=0):
            if not t.winfo_exists():
                return
            t.place(relx=1.0, rely=1.0, x=-theme.px(18), y=steps[i], anchor="se")
            if i + 1 < len(steps):
                self._toast_job = self.after(16, lambda: slide(i + 1))
            else:
                self._toast_job = self.after(ms, hide)

        def hide():
            self._toast_job = None
            if t.winfo_exists():
                t.place_forget()

        slide()

    def select(self, node_id, source=None):
        """Select a node (or None). `source` is the panel that caused it so it
        isn't refreshed needlessly."""
        # apply a half-typed rename in the inspector before moving on
        apply_name = getattr(self.inspector, "pending_name_apply", None)
        if apply_name is not None:
            try:
                apply_name()
            except tk.TclError:
                pass
        if node_id is not None and self.project.find(node_id) is None:
            node_id = None
        self.selected_id = node_id
        if node_id is not None:
            win = self.project.window_of(node_id)
            if win is not None and win["id"] != self.workplane.current_window_id:
                self.workplane.set_window(win["id"])
        if source is not self.workplane:
            self.workplane.show_selection(node_id)
        if source is not self.outline:
            self.outline.select(node_id)
        if source is not self.inspector:
            self.inspector.show(node_id)
        if node_id:
            node = self.project.find(node_id)
            self.status("Selected %s. %s" % (self.project.display_name(node),
                                             WIDGETS[node["type"]]["help"]))

    @property
    def selected(self):
        return self.project.find(self.selected_id)

    def _select_window(self):
        self.select(self.workplane.current_window_id)

    def open_settings(self):
        """Show the project's App settings page (theme, fonts, code options)."""
        self.hide_start_page()
        show = getattr(self.inspector, "show_settings", None)
        if show is not None:
            self.selected_id = None
            self.workplane.show_selection(None)
            self.outline.select(None)
            show()
        else:
            self.select(None)
        self.status("App settings: name, theme, fonts and code options of your app.")

    # ----------------------------------------------------------- history
    def _reset_history(self):
        self._undo = [self.project.snapshot()]
        self._redo = []
        self._last_merge_key = None
        self._update_history_buttons()

    def commit(self, description="", merge_key=None, skip=None):
        """Record the current project state for undo and refresh the views.

        merge_key: consecutive commits with the same key (e.g. typing in one
                   property box) are merged into a single undo step.
        skip:      a panel (or tuple of panels) that should NOT be refreshed,
                   usually the caller itself.
        """
        snap = self.project.snapshot()
        if merge_key is not None and merge_key == self._last_merge_key and len(self._undo) > 1:
            self._undo[-1] = snap
        elif not self._undo or self._undo[-1] != snap:
            self._undo.append(snap)
            if len(self._undo) > MAX_UNDO:
                self._undo.pop(0)
        self._last_merge_key = merge_key
        self._redo.clear()
        self._set_dirty(True)
        self.hide_start_page()          # something changed the design: show it
        self.refresh_all(skip=skip)
        if description:
            self.status(description)

    def undo(self):
        if len(self._undo) <= 1:
            self.status("Nothing to undo.")
            return
        self._redo.append(self._undo.pop())
        self._restore(self._undo[-1])
        self.status("Undone.")

    def redo(self):
        if not self._redo:
            self.status("Nothing to redo.")
            return
        snap = self._redo.pop()
        self._undo.append(snap)
        self._restore(snap)
        self.status("Redone.")

    def _restore(self, snap):
        self.project.restore(snap)
        self._last_merge_key = None
        self._set_dirty(True)
        if self.project.find(self.workplane.current_window_id) is None:
            self.workplane.set_window(self.project.main_window["id"])
        self.refresh_all()
        self.select(self.selected_id)

    def _update_history_buttons(self):
        can_undo, can_redo = len(self._undo) > 1, bool(self._redo)
        try:
            self.undo_btn.state(["!disabled"] if can_undo else ["disabled"])
            self.redo_btn.state(["!disabled"] if can_redo else ["disabled"])
            self.edit_menu.entryconfigure(0, state="normal" if can_undo else "disabled")
            self.edit_menu.entryconfigure(1, state="normal" if can_redo else "disabled")
        except (tk.TclError, AttributeError):
            pass

    def refresh_all(self, skip=None):
        skip = skip if isinstance(skip, tuple) else (skip,)
        if self.selected_id and self.project.find(self.selected_id) is None:
            self.selected_id = None
        if self.workplane not in skip:
            self.workplane.refresh()
        if self.outline not in skip:
            self.outline.refresh()
        if self.inspector not in skip:
            self.inspector.show(self.selected_id)
        self._update_history_buttons()

    def _set_dirty(self, dirty):
        self.dirty = dirty
        name = os.path.basename(self.file_path) if self.file_path else "Untitled"
        self.title("%s%s - %s" % ("*" if dirty else "", name, APP_TITLE))

    # ----------------------------------------------------------- editing
    def add_widget(self, wtype, container_id=None, row=None, col=None, mode="cell"):
        """Create a widget and put it somewhere sensible. Used when a palette
        item is clicked (rather than dragged). Returns the new node or None."""
        self.hide_start_page()
        project = self.project
        if container_id is None:
            container_id = self.default_container_for(wtype)
            if container_id is None:
                messagebox.showinfo(APP_TITLE, "A %s can only go inside a Tabs widget. "
                                    "Add a Tabs widget first, then select it."
                                    % WIDGETS[wtype]["name"], parent=self)
                return None
        container = project.find(container_id)
        node = project.new_node(wtype)
        if row is None:
            row, col = project.next_free_cell(container)
        try:
            project.place(node, container_id, row, col, mode)
        except ValueError as e:
            messagebox.showinfo(APP_TITLE, str(e), parent=self)
            return None
        self.commit("Added %s." % project.display_name(node))
        self.select(node["id"])
        return node

    def default_container_for(self, wtype):
        """The container a new widget should go into: the selected container,
        else the selected widget's container, else the current window."""
        project = self.project
        sel = self.selected
        candidates = []
        if sel is not None:
            if sel["type"] == "Notebook" and wtype != "Tab":
                # into the visible tab
                tab = self.workplane.current_tab_of(sel["id"]) if hasattr(
                    self.workplane, "current_tab_of") else None
                if tab:
                    candidates.append(project.find(tab))
            candidates.append(sel)
            candidates += reversed(project.ancestors(sel["id"]))
        candidates.append(project.find(self.workplane.current_window_id))
        for c in candidates:
            if c is not None and project.can_contain(c, wtype):
                return c["id"]
        return None

    def delete_selected(self):
        sel = self.selected
        if sel is None:
            self.status("Select a widget first, then press Delete.")
            return
        if sel["type"] == "Window" and sel["id"] == self.project.main_window["id"]:
            messagebox.showinfo(APP_TITLE, "The main window can't be deleted.", parent=self)
            return
        name = self.project.display_name(sel)
        if sel["type"] == "Window" or sel.get("children"):
            if not messagebox.askyesno(APP_TITLE, "Delete %s and everything inside it?" % name,
                                       parent=self):
                return
        self._remove(sel, "Deleted %s." % name)

    def _remove(self, sel, description):
        parent = self.project.parent_of(sel["id"])
        try:
            self.project.remove(sel["id"])
        except ValueError as e:
            messagebox.showinfo(APP_TITLE, str(e), parent=self)
            return False
        if sel["type"] == "Window":
            self.workplane.set_window(self.project.main_window["id"])
        self.selected_id = parent["id"] if parent is not None and parent["type"] != "Window" else None
        self.commit(description)
        self.select(self.selected_id)
        return True

    def duplicate_selected(self):
        sel = self.selected
        if sel is None or sel["type"] == "Window":
            self.status("Select a widget to duplicate.")
            return
        clone = self.project.duplicate(sel["id"])
        if clone:
            self.commit("Duplicated %s." % sel["id"])
            self.select(clone["id"])

    def add_window(self):
        self.hide_start_page()
        w = self.project.add_window()
        self.commit("Added a new window. Use an 'Open another window' action to show it.")
        self.workplane.set_window(w["id"])
        self.select(w["id"])

    # --------------------------------------------------------- clipboard
    def _delegate(self, name):
        """Use the workplane's own clipboard command if it has one."""
        fn = getattr(self.workplane, name, None)
        if fn is None or self._in_clip:
            return False
        self._in_clip = True
        try:
            fn()
        finally:
            self._in_clip = False
        return True

    def clipboard_copy(self):
        """Copy the selected widget (with everything inside it)."""
        if self.start_visible or self._delegate("copy_selection"):
            return
        sel = self.selected
        if sel is None or sel["type"] == "Window":
            self.status("Select a widget to copy.")
            return
        text = self.project.copy_nodes([sel["id"]])
        self._clip = text
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass
        self.toast("Copied %s" % sel["id"])

    def clipboard_cut(self):
        if self.start_visible or self._delegate("cut_selection"):
            return
        sel = self.selected
        if sel is None or sel["type"] == "Window":
            self.status("Select a widget to cut.")
            return
        self._in_clip = True        # copy without delegating again
        try:
            self.clipboard_copy()
        finally:
            self._in_clip = False
        self._remove(sel, "Cut %s. Paste it with Ctrl+V." % sel["id"])

    def clipboard_paste(self):
        """Paste copied widgets below the selected widget, or into the
        selected container (or the current window)."""
        if self.start_visible or self._delegate("paste"):
            return
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = None
        if not text or not Project.is_clip(text):
            text = self._clip
        if not text:
            self.status("Nothing to paste. Copy a widget first (Ctrl+C).")
            return
        project = self.project
        types = [n["type"] for n in json.loads(text).get("nodes", [])]
        sel = self.selected
        container, row, col, mode = None, None, None, "below"
        if sel is not None:
            if sel["type"] == "Notebook" and "Tab" not in types:
                tab = self.workplane.current_tab_of(sel["id"])
                container = project.find(tab) if tab else None
            elif model.is_container(sel) and any(project.can_contain(sel, t) for t in types):
                container = sel
            else:
                parent = project.parent_of(sel["id"])
                if parent is not None and any(project.can_contain(parent, t) for t in types):
                    container = parent
                    if model.layout_mode(parent) == "grid":
                        lay = sel["layout"]
                        row, col = lay["row"] + lay["rowspan"] - 1, lay["col"]
        if container is None:
            container = project.find(self.workplane.current_window_id)
        nodes = project.paste(text, container["id"], row, col, mode)
        if not nodes:
            self.status("That can't be pasted here.")
            return
        self.commit("Pasted %s." % ", ".join(n["id"] for n in nodes))
        self.select(nodes[0]["id"])

    # ------------------------------------------------------------- files
    def confirm_discard(self):
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(APP_TITLE, "Save changes to your project first?",
                                        parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    def _load(self, project, path):
        """Show `project` (just read from `path`, or new when path is None)."""
        self.project = project
        self.file_path = path
        self._base_dir = None
        self.selected_id = None
        self.workplane.set_window(project.main_window["id"])
        self._reset_history()
        self._set_dirty(False)
        self.refresh_all()
        self.select(None)
        self.clear_autosave()

    def new_project(self):
        """Start a blank project right away (File ▸ New shows the start page)."""
        if self.new_blank():
            self.status("New project started.")

    def _read_project(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return Project.from_json(f.read())
        except Exception as e:  # noqa: BLE001 - show any problem to the user
            messagebox.showerror(APP_TITLE, "Couldn't open that file:\n\n%s" % e, parent=self)
            return None

    def open_project(self, path=None, confirm=True):
        if path is None:
            if confirm and not self.confirm_discard():
                return False
            confirm = False
            path = filedialog.askopenfilename(parent=self, filetypes=FILE_TYPES,
                                              title="Open a TkBuilder project",
                                              initialdir=self._dialog_dir())
            if not path:
                return False
        project = self._read_project(path)
        if project is None:
            return False
        path = os.path.abspath(path)
        self._load(project, path)
        self.hide_start_page()
        self._remember(path)
        self.status("Opened %s" % path)
        return True

    def _dialog_dir(self):
        recent = self.prefs.get("recent") or []
        if self.file_path:
            return os.path.dirname(self.file_path)
        if recent:
            return os.path.dirname(recent[0])
        return os.path.expanduser("~")

    def _remember(self, path):
        prefs_mod.add_recent(self.prefs, path)
        self.save_prefs()

    def save_prefs(self):
        prefs_mod.save(self.prefs)

    def save_project(self, save_as=False):
        path = self.file_path
        if save_as or not path:
            path = filedialog.asksaveasfilename(
                parent=self, filetypes=FILE_TYPES, defaultextension=".tkb",
                title="Save your project", initialdir=self._dialog_dir(),
                initialfile=self._safe_name() + ".tkb")
            if not path:
                return False
        path = os.path.abspath(path)
        old_dir = self.project_dir()
        if os.path.normcase(os.path.dirname(path)) != os.path.normcase(old_dir):
            self._rebase_paths(old_dir, os.path.dirname(path))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.project.to_json())
        except OSError as e:
            messagebox.showerror(APP_TITLE, "Couldn't save:\n\n%s" % e, parent=self)
            return False
        self.file_path = path
        self._base_dir = None
        self._set_dirty(False)
        self._remember(path)
        self.clear_autosave()
        self.status("Saved to %s" % path)
        self.toast("Saved")
        return True

    def _rebase_paths(self, old_dir, new_dir):
        """Keep relative folder settings pointing at the same place when the
        project moves (e.g. a template saved into your own folder)."""
        settings = self.project.settings
        for key in ("export_dir", "backend_dir"):
            value = settings.get(key)
            if value and not os.path.isabs(value):
                settings[key] = relative_dir(resolve_dir(value, old_dir), new_dir)

    def project_dir(self):
        """Folder that relative paths of the project are relative to."""
        if self.file_path:
            return os.path.dirname(self.file_path)
        return self._base_dir or os.getcwd()

    def _safe_name(self):
        try:
            return self.project.module_basename()
        except Exception:  # noqa: BLE001
            return "my_app"

    def quit_app(self):
        if self.confirm_discard():
            self.clear_autosave()
            self.destroy()

    # ---------------------------------------------------------- autosave
    def _autosave_tick(self):
        self._autosave_job = self.after(AUTOSAVE_MS, self._autosave_tick)
        self.autosave_now()

    def autosave_now(self):
        """Write a recovery copy if there are unsaved changes."""
        if not (self.dirty and self.prefs.get("autosave", True)):
            return False
        try:
            os.makedirs(prefs_mod.config_dir(), exist_ok=True)
            with open(prefs_mod.autosave_path(), "w", encoding="utf-8") as f:
                f.write(self.project.to_json())
            info = {"file_path": self.file_path, "base_dir": self._base_dir,
                    "saved_at": time.time(), "app_name": self.project.app_name}
            with open(prefs_mod.autosave_info_path(), "w", encoding="utf-8") as f:
                json.dump(info, f)
        except OSError:
            return False
        try:
            self.status_right.configure(
                text="Recovery copy saved %s" % datetime.datetime.now().strftime("%H:%M"))
        except tk.TclError:
            pass
        return True

    def clear_autosave(self):
        for path in (prefs_mod.autosave_path(), prefs_mod.autosave_info_path()):
            try:
                os.remove(path)
            except OSError:
                pass
        try:
            self.status_right.configure(text="")
        except (tk.TclError, AttributeError):
            pass

    def autosave_info(self):
        """Details of a recovery copy from a session that didn't end normally,
        or None. {"file_path", "saved_at", "app_name", ...}"""
        if not os.path.exists(prefs_mod.autosave_path()):
            return None
        info = {}
        try:
            with open(prefs_mod.autosave_info_path(), "r", encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        if not info.get("saved_at"):
            info["saved_at"] = os.path.getmtime(prefs_mod.autosave_path())
        return info

    def recover_autosave(self):
        """Load the recovery copy (unsaved; Save writes to its original file)."""
        info = self.autosave_info()
        if info is None:
            return False
        project = self._read_project(prefs_mod.autosave_path())
        if project is None:
            return False
        path = info.get("file_path")
        self._load(project, path if path and os.path.isdir(os.path.dirname(path)) else None)
        self._base_dir = info.get("base_dir")
        self._set_dirty(True)
        self.autosave_now()            # keep the copy until it's saved
        self.hide_start_page()
        self.status("Recovered your unsaved work. Save it to keep it.")
        self.toast("Unsaved work recovered")
        return True

    def _offer_recovery(self):
        info = self.autosave_info()
        if info is None:
            return
        when = datetime.datetime.fromtimestamp(info["saved_at"]).strftime("%d %b %H:%M")
        if messagebox.askyesno(APP_TITLE, "TkBuilder closed before your changes to “%s” "
                               "were saved (last copy: %s).\n\nRecover them now?"
                               % (info.get("app_name") or "Untitled", when), parent=self):
            if not self.dirty or self.confirm_discard():
                self.recover_autosave()
        else:
            self.clear_autosave()

    # --------------------------------------------------------- dialogs
    def show_shortcuts(self):
        ShortcutsDialog(self)

    def show_preferences(self):
        PreferencesDialog(self)

    def show_error(self, title, message, details=""):
        """A friendly message with scrollable details (e.g. a traceback)."""
        return TextDialog(self, title, message, details, heading=title)

    # ------------------------------------------------------ code / run
    def generate_code(self):
        """Source of the main generated file (kept for v1 callers)."""
        files = generate_files(self.project)
        return files.get(self.project.module_basename() + ".py") or next(iter(files.values()))

    def export_project(self):
        """Open the Export window (generated files, folder, Export button)."""
        self.hide_start_page()
        try:
            from codeview import CodeView
            params = list(inspect.signature(CodeView.__init__).parameters)[1:]
            if len(params) >= 3 and params[1] in ("code", "source"):   # v1 window
                CodeView(self, self.generate_code(), self._safe_name() + ".py")
            else:
                CodeView(self, self.project)
        except Exception:  # noqa: BLE001 - never crash the editor
            self.show_error("Couldn't make the code",
                            "Something went wrong while generating the Python code. "
                            "Please report this together with the details below.",
                            traceback.format_exc())

    show_code = export_project   # v1 name

    def export_dir(self):
        """Absolute export folder from the settings, or ''."""
        return resolve_dir(self.project.settings.get("export_dir", ""), self.project_dir())

    def open_export_folder(self):
        folder = self.export_dir()
        if not folder or not os.path.isdir(folder):
            self.toast("Nothing exported yet. Use Export code first.")
            return
        if TEST_MODE:
            self.status("Would open %s" % folder)
            return
        if sys.platform == "win32":
            os.startfile(folder)  # noqa: S606
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder])

    def _run_target(self):
        """(folder, script, uses_user_file): run the user's own file from the
        export folder when there is one, else a temporary export."""
        project = self.project
        base = project.module_basename()
        folder = self.export_dir()
        user_file = os.path.join(folder, base + ".py") if folder else ""
        if (folder and project.settings.get("code_style", "split") == "split"
                and os.path.isfile(user_file)):
            return folder, user_file, True
        tmp = os.path.join(tempfile.gettempdir(), "tkbuilder_run", base)
        return tmp, os.path.join(tmp, base + ".py"), False

    def run_preview(self):
        """Run My App: generate the code and start it in its own process."""
        project = self.project
        folder, script, user = self._run_target()
        pdir = self.project_dir()
        try:
            if user:
                export_files(project, folder, pdir)
            else:
                os.makedirs(folder, exist_ok=True)
                for name, source in generate_files(project, folder, pdir).items():
                    with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
                        f.write(source)
        except Exception:  # noqa: BLE001
            self.show_error("Couldn't make the code",
                            "Something went wrong while generating the Python code.",
                            traceback.format_exc())
            return None
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        extra = [d for d in (resolve_dir(project.settings.get("backend_dir", ""), pdir), pdir)
                 if d and os.path.isdir(d)]
        env["PYTHONPATH"] = os.pathsep.join(extra + [env.get("PYTHONPATH", "")]).strip(os.pathsep)
        cwd = folder if user else (pdir if os.path.isdir(pdir) else folder)
        try:
            proc = subprocess.Popen([python_exe(), script], cwd=cwd, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as e:
            self.show_error("Couldn't start Python", "Python couldn't be started.", str(e))
            return None
        self._running.append(proc)
        self.status("Your app is running in its own window%s. Shift+F5 stops it." % (
            " (with your code from %s)" % os.path.basename(script) if user else ""))
        self.toast("▶ Running %s" % project.app_name)
        proc._tkb = {"script": script, "err": [], "shown": 0, "last": 0.0, "done": False}
        for stream, keep in ((proc.stdout, False), (proc.stderr, True)):
            threading.Thread(target=self._read_stream, args=(proc, stream, keep),
                             daemon=True).start()
        if len(self._running) == 1:
            self.after(250, self._poll_running)
        return proc

    @staticmethod
    def _read_stream(proc, stream, keep):
        """Worker thread: drain a pipe of the running app (stderr is kept)."""
        for raw in iter(stream.readline, b""):
            if keep:
                text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
                proc._tkb["err"].append(text)
                proc._tkb["last"] = time.time()
        stream.close()
        if keep:
            proc.wait()
            proc._tkb["done"] = True

    def _poll_running(self):
        """GUI thread: report errors of running apps (also while they keep
        running, e.g. an exception in a button handler) and notice exits."""
        for proc in list(self._running):
            info = proc._tkb
            new = "".join(info["err"][info["shown"]:])
            settled = time.time() - info["last"] > 0.4
            if info["done"]:
                self._running.remove(proc)
                stopped = getattr(proc, "_tkb_stopped", False)
                if not stopped and new.strip() and (proc.returncode or "Traceback" in new):
                    self._report_run_error(info["script"], new, proc.returncode)
                elif not stopped:
                    self.status("Your app was closed.")
            elif "Traceback" in new and settled:
                info["shown"] = len(info["err"])
                self._report_run_error(info["script"], new, None)
        if self._running:
            self.after(250, self._poll_running)

    def _report_run_error(self, script, text, code):
        state = ("stopped with an error" if code else
                 "had an error (it is still running)" if code is None else
                 "had an error while running")
        self.show_error(
            "Your app reported an error",
            "Your app (%s) %s. The details below show where it happened - the last lines "
            "are usually the most useful." % (os.path.basename(script), state),
            text.strip())

    def stop_running(self):
        """Stop every app started with Run."""
        if not self._running:
            self.status("Your app isn't running.")
            return
        for proc in list(self._running):
            proc._tkb_stopped = True
            try:
                proc.kill()
            except OSError:
                pass
        self.status("Stopped your app.")


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------

def cli_export(project_path, folder=None):
    """Regenerate the code of a project without opening the editor.
    Returns a process exit code."""
    try:
        with open(project_path, "r", encoding="utf-8") as f:
            project = Project.from_json(f.read())
    except Exception as e:  # noqa: BLE001
        print("Couldn't open %s: %s" % (project_path, e), file=sys.stderr)
        return 1
    base = os.path.dirname(os.path.abspath(project_path))
    folder = folder or resolve_dir(project.settings.get("export_dir", ""), base) or base
    try:
        report = export_files(project, folder, base)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return 1
    for path in report.get("written", []):
        print("written:", path)
    for path in report.get("kept", []):
        print("kept (your code):", path)
    for stub in report.get("missing_hooks", []):
        print("\nAdd this method to your code file:\n" + stub)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--export":
        if len(argv) < 2:
            print("usage: python app.py --export project.tkb [folder]", file=sys.stderr)
            return 2
        return cli_export(argv[1], argv[2] if len(argv) > 2 else None)
    path = argv[0] if argv else None
    app = App(path)
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
