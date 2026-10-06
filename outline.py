"""
Outline panel: a tree of every window of the project and the widgets inside
it.  Clicking a row selects that widget everywhere.

* a small coloured badge per widget category + the widget's glyph,
* a filter box (matches name, kind and text),
* a right-click menu (rename, actions, code, copy/paste, duplicate, delete…),
* cheap refresh: nothing is rebuilt when the visible rows didn't change, and
  only texts are updated when the structure is the same.
"""

import tkinter as tk
from tkinter import ttk, messagebox

import model
from model import WIDGETS
import theme

# badge colour per palette category
CATEGORY_COLORS = {"Windows": "#475569", "Basic": "#2563eb", "Input": "#16a34a",
                   "Data": "#7c3aed", "Display": "#d97706", "Containers": "#64748b"}


def _px(n):
    f = getattr(theme, "px", None)
    return f(n) if callable(f) else int(n)


def _row_text(node, project):
    """Text shown for a node, e.g. '▭  button1   Button'."""
    spec = WIDGETS[node["type"]]
    if node["type"] == "Window":
        kind = "Main window" if node is project.main_window else "Window"
        return "%s  %s  –  %s “%s”" % (spec["icon"], node["id"], kind,
                                       node["props"].get("title", ""))
    if node["type"] == "Tab":
        return "%s  %s  –  Tab “%s”" % (spec["icon"], node["id"], node["props"].get("text", ""))
    return "%s  %s  –  %s" % (spec["icon"], node["id"], spec["name"])


def _search_text(node):
    spec = WIDGETS[node["type"]]
    p = node["props"]
    parts = [node["id"], spec["name"], node["type"]]
    for k in ("text", "title"):
        if isinstance(p.get(k), str):
            parts.append(p[k])
    return " ".join(parts).lower()


def _ordered_children(node):
    kids = list(node.get("children", []))
    if model.layout_mode(node) == "pages":
        return kids
    return sorted(kids, key=lambda c: (c["layout"].get("row", 0), c["layout"].get("col", 0)))


class Outline(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._closed = set()      # ids the user collapsed
        self._menu_target = None
        self._rows = None
        self._images = {}

        head = ttk.Frame(self, padding=(_px(8), _px(6), _px(8), _px(2)))
        head.pack(fill="x")
        ttk.Label(head, text="Structure", style="Heading.TLabel").pack(side="left")
        info = ttk.Label(head, text="ⓘ", style="Muted.TLabel", cursor="question_arrow")
        info.pack(side="right")
        theme.tooltip(info, "Every window of your app and everything inside it.\n"
                            "Click a row to select it. Right-click for more options.")

        fbox = tk.Frame(self, bg=theme.BG)
        fbox.pack(fill="x", padx=_px(6), pady=(_px(2), _px(4)))
        self.filter_var = tk.StringVar()
        self.filter_ent = ttk.Entry(fbox, textvariable=self.filter_var)
        self.filter_ent.pack(side="left", fill="x", expand=True)
        self.hint = tk.Label(fbox, text="🔍 Filter by name or kind…",
                             bg=getattr(theme, "FIELD", None) or theme.PANEL, fg=theme.MUTED,
                             font=(theme.UI_FAMILY, 9), cursor="xterm")
        self.hint.place(in_=self.filter_ent, x=_px(6), rely=0.5, anchor="w")
        self.hint.bind("<Button-1>", lambda e: self.filter_ent.focus_set())
        self.clear_btn = tk.Label(fbox, text="✕", bg=theme.BG, fg=theme.MUTED, cursor="hand2",
                                  font=(theme.UI_FAMILY, 9))
        self.clear_btn.bind("<Button-1>", lambda e: self.filter_var.set(""))
        self.filter_var.trace_add("write", lambda *a: self._filter_changed())
        self.filter_ent.bind("<Escape>", lambda e: (self.filter_var.set(""), "break")[1])
        self.filter_ent.bind("<Down>", lambda e: (self._focus_first(), "break")[1])

        body = ttk.Frame(self, padding=(_px(6), 0, _px(6), _px(6)))
        body.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(body, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.column("#0", width=_px(200), stretch=True)
        self.tree.tag_configure("window", font=(theme.UI_FAMILY, 10, "bold"))
        self.tree.tag_configure("tab", foreground=theme.MUTED)
        self.tree.tag_configure("dim", foreground=theme.MUTED)
        self.tree.tag_configure("match", font=(theme.UI_FAMILY, 10, "bold"))
        self.empty = tk.Label(body, text="Nothing matches this filter.", bg=theme.PANEL,
                              fg=theme.MUTED, font=(theme.UI_FAMILY, 9))

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._on_toggle(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._on_toggle(False))
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-Button-1>", self._on_double)
        self.tree.bind("<F2>", lambda e: self._rename_selected())

        self.menu = tk.Menu(self, tearoff=False)
        self.refresh()

    # ------------------------------------------------------------ icons
    def _badge(self, category):
        """A small rounded square in the category colour (cached image)."""
        img = self._images.get(category)
        if img is not None:
            return img
        s = max(10, _px(10))
        img = tk.PhotoImage(width=s + 4, height=s)
        img.put(CATEGORY_COLORS.get(category, "#64748b"), to=(0, 0, s, s))
        for x in range(s, s + 4):              # a little gap before the text
            for y in range(s):
                img.transparency_set(x, y, True)
        for x, y in ((0, 0), (s - 1, 0), (0, s - 1), (s - 1, s - 1)):
            img.transparency_set(x, y, True)
        self._images[category] = img
        return img

    # ------------------------------------------------------------ building
    def _collect(self):
        project = self.app.project
        flt = self.filter_var.get().strip().lower()
        rows = []

        def collect(node, parent_iid):
            """Returns True if node (or a child) matches the filter."""
            start = len(rows)
            match = not flt or flt in _search_text(node)
            tags = ["window"] if node["type"] == "Window" else (
                ["tab"] if node["type"] == "Tab" else [])
            if flt:
                tags.append("match" if match else "dim")
            rows.append((parent_iid, node["id"], _row_text(node, project), tuple(tags),
                         WIDGETS[node["type"]]["category"]))
            any_child = False
            for ch in _ordered_children(node):
                any_child = collect(ch, node["id"]) or any_child
            if flt and not match and not any_child:
                del rows[start:]
                return False
            return True

        for w in project.windows:
            collect(w, "")
        return rows

    def refresh(self):
        tree = self.tree
        rows = self._collect()
        if rows == self._rows:
            # nothing visible changed (e.g. typing a property): keep the tree
            self.select(self.app.selected_id)
            return
        old = self._rows or []
        same_shape = [r[:2] for r in rows] == [r[:2] for r in old]
        self._rows = rows
        flt = bool(self.filter_var.get().strip())
        if same_shape:
            for new, prev in zip(rows, old):
                if new != prev:
                    tree.item(new[1], text=new[2], tags=new[3])
        else:
            top = tree.yview()[0]
            tree.delete(*tree.get_children(""))
            for parent_iid, iid, text, tags, cat in rows:
                tree.insert(parent_iid, "end", iid=iid, text=text,
                            open=flt or iid not in self._closed, tags=tags,
                            image=self._badge(cat))
            # forget collapsed ids that no longer exist
            self._closed &= set(self.app.project.all_ids())
            tree.yview_moveto(top)
        if flt and not rows:
            self.empty.place(relx=0.5, y=_px(30), anchor="n")
        else:
            self.empty.place_forget()
        sel = self.app.selected_id
        if sel and tree.exists(sel):
            if tree.selection() != (sel,):
                tree.selection_set(sel)
        elif tree.selection():
            tree.selection_remove(*tree.selection())

    def _filter_changed(self):
        text = self.filter_var.get()
        if text:
            self.hint.place_forget()
            if not self.clear_btn.winfo_manager():
                self.clear_btn.pack(side="left", padx=(_px(4), 0))
        else:
            self.hint.place(in_=self.filter_ent, x=_px(6), rely=0.5, anchor="w")
            self.clear_btn.pack_forget()
        self.refresh()
        if text:
            first = next((r[1] for r in self._rows if "match" in r[3]), None)
            if first:
                self.tree.see(first)

    def _focus_first(self):
        first = next((r[1] for r in self._rows or [] if "match" in r[3] or
                      not self.filter_var.get()), None)
        if first:
            self.tree.focus_set()
            self.tree.focus(first)
            self.tree.selection_set(first)

    def select(self, node_id):
        """Highlight a row without calling back into the app."""
        tree = self.tree
        if node_id and tree.exists(node_id):
            # make sure its parents are open so it is visible
            p = tree.parent(node_id)
            while p:
                if not tree.item(p, "open"):
                    tree.item(p, open=True)
                self._closed.discard(p)
                p = tree.parent(p)
            if tree.selection() != (node_id,):
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

    def _on_double(self, event):
        iid = self.tree.identify_row(event.y)
        node = self.app.project.find(iid) if iid else None
        if node is not None and node.get("children") is None:
            insp = getattr(self.app, "inspector", None)
            if insp is not None and hasattr(insp, "show_actions") and \
                    WIDGETS[node["type"]]["events"]:
                insp.show_actions(iid)
                return "break"
        return None

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
        project = self.app.project
        is_window = node["type"] == "Window"
        is_main = node is project.main_window
        insp = getattr(self.app, "inspector", None)
        m.add_command(label="Rename…", command=self._rename, accelerator="F2")
        if WIDGETS[node["type"]]["events"] or is_window:
            m.add_command(label="Actions…", command=self._actions)
        if insp is not None and hasattr(insp, "show_code"):
            m.add_command(label="Show code for it", command=self._code)
        if node["type"] == "Notebook":
            m.add_command(label="Add a tab", command=self._add_tab)
        parent = project.parent_of(iid)
        if parent is not None:
            m.add_command(label="Select parent (%s)" % parent["id"],
                          command=lambda: self.app.select(parent["id"]))
        m.add_separator()
        if not is_window:
            for label, name, acc in (("Cut", "clipboard_cut", "Ctrl+X"),
                                     ("Copy", "clipboard_copy", "Ctrl+C")):
                fn = getattr(self.app, name, None)
                if callable(fn):
                    m.add_command(label=label, accelerator=acc,
                                  command=lambda f=fn: self._run(f))
        paste = getattr(self.app, "clipboard_paste", None)
        if callable(paste) and model.is_container(node) and node["type"] != "Notebook":
            m.add_command(label="Paste inside", accelerator="Ctrl+V",
                          command=lambda: self._run(paste))
        if not is_window:
            m.add_command(label="Duplicate", command=self._duplicate, accelerator="Ctrl+D")
        m.add_separator()
        m.add_command(label="Delete window" if is_window else "Delete",
                      command=self._delete, accelerator="Del",
                      state="disabled" if is_main else "normal")
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

    def _run(self, fn):
        if self._target() is not None:
            fn()

    def _rename_selected(self):
        sel = self.tree.selection()
        if sel:
            self._menu_target = sel[0]
            self._rename()
        return "break"

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
        insp = getattr(self.app, "inspector", None)
        if node_id is not None and insp is not None and hasattr(insp, "show_actions"):
            insp.show_actions(node_id)

    def _code(self):
        node_id = self._target()
        insp = getattr(self.app, "inspector", None)
        if node_id is not None and insp is not None and hasattr(insp, "show_code"):
            insp.show_code(node_id)

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
