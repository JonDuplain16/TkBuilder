"""
TkBuilder main application window.

Layout:
    +-------------------------------------------------------------+
    | toolbar: New Open Save | Undo Redo | Run  Get Code          |
    +-----------+-------------------------------+-----------------+
    | Palette   |  window bar (Main | Win 2 | +)| Inspector       |
    |           |                               |  Properties     |
    |-----------|        Workplane              |  Actions        |
    | Outline   |   (the design surface)        |                 |
    +-----------+-------------------------------+-----------------+
    | status bar                                                  |
    +-------------------------------------------------------------+

The App object is the hub every panel talks to (see SPEC.md).
"""

import os
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import model
from model import Project, WIDGETS
import theme

APP_TITLE = "TkBuilder"
FILE_TYPES = [("TkBuilder project", "*.tkb"), ("All files", "*.*")]
MAX_UNDO = 200


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


if os.environ.get("TKBUILDER_NO_DIALOGS"):
    install_silent_dialogs()


class App(tk.Tk):
    def __init__(self, path=None):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1360x820")
        self.minsize(980, 600)
        theme.apply(self)

        self.project = Project()
        self.file_path = None
        self.dirty = False
        self.selected_id = None
        self._undo = []
        self._redo = []
        self._last_merge_key = None
        self._restoring = False

        self._build_ui()
        self._bind_keys()
        self.protocol("WM_DELETE_WINDOW", self.quit_app)

        self._reset_history()
        self.refresh_all()
        self.select(None)
        if path:
            self.after(50, lambda: self.open_project(path))
        self.status("Welcome! Drag widgets from the left onto the window in the middle.")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        from palette import Palette
        from outline import Outline
        from workplane import Workplane
        from inspector import Inspector

        self._build_toolbar()

        self.statusbar = ttk.Label(self, style="Status.TLabel", anchor="w")
        self.statusbar.pack(side="bottom", fill="x")

        main = ttk.PanedWindow(self, orient="horizontal")
        main.pack(fill="both", expand=True)

        left = ttk.PanedWindow(main, orient="vertical")
        self.palette = Palette(left, self)
        self.outline = Outline(left, self)
        left.add(self.palette, weight=3)
        left.add(self.outline, weight=2)

        self.workplane = Workplane(main, self)
        self.inspector = Inspector(main, self)

        main.add(left, weight=0)
        main.add(self.workplane, weight=1)
        main.add(self.inspector, weight=0)

    def _build_toolbar(self):
        bar = ttk.Frame(self, style="Toolbar.TFrame", padding=(6, 4))
        bar.pack(side="top", fill="x")

        def btn(text, cmd, tip, style="Toolbar.TButton"):
            b = ttk.Button(bar, text=text, command=cmd, style=style)
            b.pack(side="left", padx=2)
            theme.tooltip(b, tip)
            return b

        def sep():
            ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6, pady=2)

        btn("\U0001F4C4 New", self.new_project, "Start a new, empty project (Ctrl+N)")
        btn("\U0001F4C2 Open", self.open_project, "Open a saved project (Ctrl+O)")
        btn("\U0001F4BE Save", self.save_project, "Save your project (Ctrl+S)")
        btn("Save As…", lambda: self.save_project(save_as=True), "Save under a new name")
        ex = ttk.Menubutton(bar, text="\U0001F4DA Examples", style="Toolbar.TButton")
        ex_menu = tk.Menu(ex, tearoff=False)
        ex["menu"] = ex_menu
        ex.pack(side="left", padx=2)
        theme.tooltip(ex, "Open a ready-made example to learn from")
        folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".tkb"):
                    path = os.path.join(folder, name)
                    ex_menu.add_command(label=name[:-4],
                                        command=lambda p=path: self.open_example(p))
        sep()
        self.undo_btn = btn("↶ Undo", self.undo, "Undo the last change (Ctrl+Z)")
        self.redo_btn = btn("↷ Redo", self.redo, "Redo (Ctrl+Y)")
        sep()
        btn("⧉ Duplicate", self.duplicate_selected, "Copy the selected widget (Ctrl+D)")
        btn("\U0001F5D1 Delete", self.delete_selected, "Delete the selected widget (Delete)")
        sep()
        btn("➕ New Window", self.add_window, "Add another window to your app")

        btn("\U0001F4E5 Get Python Code", self.show_code,
            "See, copy or save the generated Python program", "Accent.TButton").pack(side="right", padx=4)
        btn("▶ Run My App", self.run_preview,
            "Try your app for real (F5)", "Run.TButton").pack(side="right", padx=4)

    def _bind_keys(self):
        def unless_typing(fn):
            def handler(event):
                w = self.focus_get()
                if isinstance(w, (tk.Entry, tk.Text, ttk.Entry, tk.Spinbox, ttk.Spinbox, ttk.Combobox)):
                    return None
                fn()
                return "break"
            return handler

        self.bind_all("<Control-z>", unless_typing(self.undo))
        self.bind_all("<Control-y>", unless_typing(self.redo))
        self.bind_all("<Control-Z>", unless_typing(self.redo))
        self.bind_all("<Control-d>", unless_typing(self.duplicate_selected))
        self.bind_all("<Delete>", unless_typing(self.delete_selected))
        self.bind_all("<Control-s>", lambda e: (self.save_project(), "break")[1])
        self.bind_all("<Control-o>", lambda e: (self.open_project(), "break")[1])
        self.bind_all("<Control-n>", lambda e: (self.new_project(), "break")[1])
        self.bind_all("<F5>", lambda e: (self.run_preview(), "break")[1])
        self.bind_all("<Escape>", unless_typing(lambda: self.select(None)))

    # ------------------------------------------------------- selection etc
    def status(self, text):
        self.statusbar.configure(text="  " + text)

    def select(self, node_id, source=None):
        """Select a node (or None). `source` is the panel that caused it so it
        isn't refreshed needlessly."""
        # apply a half-typed rename in the inspector before moving on
        # (it stays set: the user may type a name again without the panel
        # being rebuilt; it does nothing when there is nothing to apply)
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
        self.undo_btn.state(["!disabled"] if len(self._undo) > 1 else ["disabled"])
        self.redo_btn.state(["!disabled"] if self._redo else ["disabled"])

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
        parent = self.project.parent_of(sel["id"])
        try:
            self.project.remove(sel["id"])
        except ValueError as e:
            messagebox.showinfo(APP_TITLE, str(e), parent=self)
            return
        if sel["type"] == "Window":
            self.workplane.set_window(self.project.main_window["id"])
        self.selected_id = parent["id"] if parent is not None and parent["type"] != "Window" else None
        self.commit("Deleted %s." % name)
        self.select(self.selected_id)

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
        w = self.project.add_window()
        self.commit("Added a new window. Use an 'Open another window' action to show it.")
        self.workplane.set_window(w["id"])
        self.select(w["id"])

    # ------------------------------------------------------------- files
    def confirm_discard(self):
        if not self.dirty:
            return True
        ans = messagebox.askyesnocancel(APP_TITLE, "Save changes to your project first?", parent=self)
        if ans is None:
            return False
        if ans:
            return self.save_project()
        return True

    def new_project(self):
        if not self.confirm_discard():
            return
        self.project = Project()
        self.file_path = None
        self.selected_id = None
        self.workplane.set_window(self.project.main_window["id"])
        self._reset_history()
        self._set_dirty(False)
        self.refresh_all()
        self.select(None)
        self.status("New project started.")

    def open_project(self, path=None):
        if path is None:
            if not self.confirm_discard():
                return
            path = filedialog.askopenfilename(parent=self, filetypes=FILE_TYPES,
                                              title="Open a TkBuilder project")
            if not path:
                return
        try:
            with open(path, "r", encoding="utf-8") as f:
                project = Project.from_json(f.read())
        except Exception as e:  # noqa: BLE001 - show any problem to the user
            messagebox.showerror(APP_TITLE, "Couldn't open that file:\n\n%s" % e, parent=self)
            return
        self.project = project
        self.file_path = path
        self.selected_id = None
        self.workplane.set_window(project.main_window["id"])
        self._reset_history()
        self._set_dirty(False)
        self.refresh_all()
        self.select(None)
        self.status("Opened %s" % path)

    def open_example(self, path):
        """Open an example as a new, unsaved project (so it isn't overwritten)."""
        if not self.confirm_discard():
            return
        self.open_project(path)
        self.file_path = None
        self._set_dirty(False)
        self.status("Opened an example. Try ▶ Run My App, or change it however you like!")

    def save_project(self, save_as=False):
        path = self.file_path
        if save_as or not path:
            path = filedialog.asksaveasfilename(
                parent=self, filetypes=FILE_TYPES, defaultextension=".tkb",
                title="Save your project",
                initialfile=self._safe_name() + ".tkb")
            if not path:
                return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.project.to_json())
        except OSError as e:
            messagebox.showerror(APP_TITLE, "Couldn't save:\n\n%s" % e, parent=self)
            return False
        self.file_path = path
        self._set_dirty(False)
        self.status("Saved to %s" % path)
        return True

    def _safe_name(self):
        title = self.project.main_window["props"].get("title") or "my_app"
        name = "".join(ch if ch.isalnum() else "_" for ch in title.lower()).strip("_")
        return name or "my_app"

    def quit_app(self):
        if self.confirm_discard():
            self.destroy()

    # ------------------------------------------------------ code / run
    def generate_code(self):
        import codegen
        return codegen.generate(self.project)

    def show_code(self):
        from codeview import CodeView
        try:
            code = self.generate_code()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "Something went wrong making the code:\n\n%s" % e,
                                 parent=self)
            return
        CodeView(self, code, self._safe_name() + ".py")

    def run_preview(self):
        try:
            code = self.generate_code()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, "Something went wrong making the code:\n\n%s" % e,
                                 parent=self)
            return
        folder = os.path.join(tempfile.gettempdir(), "tkbuilder_preview")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, self._safe_name() + ".py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-len("pythonw.exe")] + "python.exe"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen([exe, path], stderr=subprocess.PIPE, stdout=subprocess.PIPE,
                                    creationflags=flags, cwd=folder)
        except OSError as e:
            messagebox.showerror(APP_TITLE, "Couldn't start Python:\n\n%s" % e, parent=self)
            return
        self.status("Your app is running in its own window. Close it to come back.")

        def watch():
            _out, err = proc.communicate()
            if proc.returncode not in (0, None) and err:
                text = err.decode("utf-8", "replace").strip()
                self.after(0, lambda: messagebox.showerror(
                    APP_TITLE, "Your app stopped with an error:\n\n" + text[-2500:], parent=self))
        threading.Thread(target=watch, daemon=True).start()


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    app = App(path)
    app.mainloop()


if __name__ == "__main__":
    main()
