"""
Outline panel: a tree showing every window of the project and the widgets
inside it.  Clicking a row selects that widget everywhere.
"""

import tkinter as tk
from tkinter import ttk, messagebox

import model
from model import WIDGETS
import theme


def _row_text(node, project):
    """Text shown for a node, e.g. '▭  button1 – Button'."""
    spec = WIDGETS[node["type"]]
    icon = spec["icon"]
    if node["type"] == "Window":
        kind = "Main window" if node is project.main_window else "Window"
        title = node["props"].get("title", "")
        return "%s  %s – %s “%s”" % (icon, node["id"], kind, title)
    if node["type"] == "Tab":
        return "%s  %s – Tab “%s”" % (icon, node["id"], node["props"].get("text", ""))
    return "%s  %s – %s" % (icon, node["id"], spec["name"])


def _ordered_children(node):
    kids = list(node.get("children", []))
    if node["type"] == "Notebook":
        return kids
    return sorted(kids, key=lambda c: (c["layout"].get("row", 0), c["layout"].get("col", 0)))


class Outline(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._closed = set()      # ids the user collapsed
        self._menu_target = None

        head = ttk.Frame(self, padding=(8, 6, 8, 2))
        head.pack(fill="x")
        ttk.Label(head, text="Structure", style="Heading.TLabel").pack(side="left")
        info = ttk.Label(head, text="ⓘ", style="Muted.TLabel", cursor="question_arrow")
        info.pack(side="right")
        theme.tooltip(info, "Every window of your app and everything inside it.\n"
                            "Click a row to select it. Right-click for more options.")

        body = ttk.Frame(self, padding=(6, 0, 6, 6))
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.column("#0", width=200, stretch=True)
        self.tree.tag_configure("window", font=(theme.UI_FAMILY, 10, "bold"))
        self.tree.tag_configure("tab", foreground="#374151")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._on_toggle(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._on_toggle(False))
        self.tree.bind("<Button-3>", self._on_right_click)

        self.menu = tk.Menu(self, tearoff=False)
        self.refresh()

    # ------------------------------------------------------------ building
    def refresh(self):
        tree = self.tree
        project = self.app.project
        rows = []

        def collect(node, parent_iid):
            tags = ("window",) if node["type"] == "Window" else (
                ("tab",) if node["type"] == "Tab" else ())
            rows.append((parent_iid, node["id"], _row_text(node, project), tags))
            for ch in _ordered_children(node):
                collect(ch, node["id"])

        for w in project.windows:
            collect(w, "")
        if rows == getattr(self, "_rows", None):
            # nothing visible changed (e.g. typing a property): keep the tree
            self.select(self.app.selected_id)
            return
        self._rows = rows
        top = tree.yview()[0]
        tree.delete(*tree.get_children(""))
        for parent_iid, iid, text, tags in rows:
            tree.insert(parent_iid, "end", iid=iid, text=text,
                        open=iid not in self._closed, tags=tags)
        # forget collapsed ids that no longer exist
        self._closed &= set(project.all_ids())
        sel = self.app.selected_id
        if sel and tree.exists(sel):
            tree.selection_set(sel)
        tree.update_idletasks()
        tree.yview_moveto(top)

    def select(self, node_id):
        """Highlight a row without calling back into the app."""
        tree = self.tree
        if node_id and tree.exists(node_id):
            # make sure its parents are open so it is visible
            p = tree.parent(node_id)
            while p:
                tree.item(p, open=True)
                self._closed.discard(p)
                p = tree.parent(p)
            tree.selection_set(node_id)
            tree.see(node_id)
        else:
            sel = tree.selection()
            if sel:
                tree.selection_remove(*sel)

    # ------------------------------------------------------------ events
    def _on_select(self, _event=None):
        sel = self.tree.selection()
        node_id = sel[0] if sel else None
        if node_id is None or node_id == self.app.selected_id:
            return          # caused by select()/refresh(), not by the user
        self.app.select(node_id, source=self)

    def _on_toggle(self, opened):
        iid = self.tree.focus()
        if not iid:
            return
        if opened:
            self._closed.discard(iid)
        else:
            self._closed.add(iid)

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        node = self.app.project.find(iid)
        if node is None:
            return
        if iid != self.app.selected_id:
            self.tree.selection_set(iid)
            self.app.select(iid, source=self)
        self._menu_target = iid
        m = self.menu
        m.delete(0, "end")
        is_window = node["type"] == "Window"
        is_main = node is self.app.project.main_window
        m.add_command(label="Rename…", command=self._rename)
        if WIDGETS[node["type"]]["events"] or is_window:
            m.add_command(label="Actions…", command=self._actions)
        if node["type"] == "Notebook":
            m.add_command(label="Add a tab", command=self._add_tab)
        m.add_separator()
        if not is_window:
            m.add_command(label="Duplicate", command=self._duplicate)
        m.add_command(label="Delete window" if is_window else "Delete",
                      command=self._delete, state="disabled" if is_main else "normal")
        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ------------------------------------------------------------ menu cmds
    def _target(self):
        node_id = self._menu_target
        if node_id is None or self.app.project.find(node_id) is None:
            return None
        if self.app.selected_id != node_id:
            self.app.select(node_id)
        return node_id

    def _rename(self):
        node_id = self._target()
        if node_id is None:
            return
        import dialogs
        new = dialogs.ask_string(
            self.winfo_toplevel(), "Rename",
            "New name for %s:\n(letters, numbers and _ only)" % node_id, node_id)
        if new is None:
            return
        new = new.strip()
        if new == node_id:
            return
        try:
            self.app.project.rename(node_id, new)
        except ValueError as e:
            messagebox.showinfo("Rename", str(e), parent=self.winfo_toplevel())
            return
        from inspector import _after_rename
        _after_rename(self.app, node_id, new)

    def _actions(self):
        node_id = self._target()
        if node_id is None:
            return
        insp = getattr(self.app, "inspector", None)
        if insp is not None and hasattr(insp, "show_actions"):
            insp.show_actions(node_id)

    def _add_tab(self):
        node_id = self._target()
        if node_id is None:
            return
        tab = self.app.project.add_tab(node_id)
        self.app.commit("Added a tab.")
        self.app.select(tab["id"])

    def _duplicate(self):
        if self._target() is not None:
            self.app.duplicate_selected()

    def _delete(self):
        if self._target() is not None:
            self.app.delete_selected()
