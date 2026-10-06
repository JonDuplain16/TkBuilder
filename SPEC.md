# TkBuilder – design spec

TkBuilder is a **no-code, drag-and-drop designer for Tkinter apps**, written in
pure Python + Tkinter (stdlib only, Python 3.8+; dev machine has 3.12 on
Windows 11). The user is NOT a programmer: every label, message and dialog must
use plain, friendly English (say "Text Box", not "Entry"; "When clicked", not
"command"). The output is a single, readable, runnable `.py` file.

Run it with: `python app.py` (from this folder).

## Files & ownership

| File | Owner | Purpose |
|---|---|---|
| `model.py` | lead (read-only for agents) | Project data, widget catalog `WIDGETS`, action catalog `ACTIONS`, grid editing ops |
| `tkmap.py` | lead (read-only) | `widget_options(node)`, `initial_state(node)`, `font_value()`, `WIDGET_CLASS` – shared by designer & codegen so preview == output |
| `theme.py` | lead (read-only) | Editor colours, ttk styles, `tooltip()`, `scrollable()` |
| `app.py` | lead (read-only) | Main window, hub object `App`, undo/redo, open/save, run, get-code |
| `codegen.py`, `codeview.py`, `test_codegen.py` | **agent A** | Python code generator + "Get Python Code" window |
| `workplane.py`, `palette.py` | **agent B** | Design surface with drag/drop/snap + widget palette |
| `inspector.py`, `outline.py`, `dialogs.py` | **agent C** | Properties/actions editor, outline tree, editor dialogs |

**Agents must not edit files they don't own.** If you need a change in a
shared file, work around it locally and describe the needed change in your
final report. Other agents work in parallel, so their files may be stubs or
half-finished while you work – test your module with your own small stub
`App` if needed.

## Data model (see docstring of model.py)

* Node = `{"id", "type", "props", "layout", "events", "children"?}`.
* `id` is a unique python identifier and is also the attribute name in the
  generated code (`self.<id>`). Users can rename (`project.rename`, which
  updates all references).
* Containers (`Window`, `Frame`, `LabelFrame`, `Tab`) lay their children out on
  a **grid** (`layout.row/col/rowspan/colspan`). `Notebook` children are `Tab`
  nodes only (no layout). Grids are kept compact (no empty rows/cols) by
  `Project.compact()`, which `place/detach/move` call automatically.
* Sticky + weights: `model.sticky_for(layout)` and `model.grid_weights(container)`.
  Both the designer and the generated code MUST use these.
* Events: `node["events"][event_key]` = list of actions. Window menu bar is
  `window["props"]["menu"]` (format in model.py), each menu item has `"actions"`.
* Actions: `{"type": key into ACTIONS, "params": {...}}`. Param kinds are
  described above `ACTIONS` in model.py. Text params may contain
  `{widget_id}` placeholders = "the current value of that widget".
  `model.action_summary(action, project)` gives a readable one-liner.

## The App hub (app.py) – what panels can call

```
app.project                 # model.Project
app.selected_id / app.selected
app.select(node_id, source=self)      # select; source = calling panel (not refreshed)
app.commit(description, merge_key=None, skip=self)
        # call AFTER mutating app.project. Records undo, marks dirty, refreshes
        # all panels except `skip`. Use merge_key (e.g. ("prop", id, key)) for
        # live typing so one edit = one undo step.
app.add_widget(wtype, container_id=None, row=None, col=None, mode="cell")
app.default_container_for(wtype)
app.delete_selected(); app.duplicate_selected(); app.add_window()
app.status(text)            # status bar hint
app.workplane / app.inspector / app.outline / app.palette
```

## Panel interfaces (must exist exactly like this)

```python
class Palette(ttk.Frame):            # palette.py
    def __init__(self, master, app)

class Workplane(ttk.Frame):          # workplane.py
    def __init__(self, master, app)
    current_window_id: str           # window being shown
    def refresh(self)                # rebuild from app.project (keep scroll pos, current tab per notebook)
    def set_window(self, window_id)  # show another window (then refresh)
    def show_selection(self, node_id)# draw the selection outline (None = clear); switch tabs so it's visible
    def current_tab_of(self, notebook_id) -> tab_id or None
    def begin_drag_new(self, wtype, event)   # palette calls on press-and-drag of an item

class Inspector(ttk.Frame):          # inspector.py
    def __init__(self, master, app)
    def show(self, node_id)          # rebuild editors for node (None => project/help page)

class Outline(ttk.Frame):            # outline.py
    def __init__(self, master, app)
    def refresh(self)
    def select(self, node_id)        # highlight row, don't call back into app

# codegen.py
def generate(project) -> str

# codeview.py
class CodeView(tk.Toplevel):
    def __init__(self, app, code: str, filename: str)
```

## Workplane (agent B) – behaviour

* Top: a **window bar** – one tab-like button per project window ("Main
  window: My App", "window1: Settings"…) + "＋ Window" button. Clicking selects
  and shows that window.
* Below: a scrollable canvas (`theme.WORKPLANE_BG`) showing the current window
  as a **fake window**: title bar (title text + fake ─ □ ✕), fake menu bar if
  the window has menus (click → select the window), then the content area at
  exactly `width × height` pixels (no propagation), background/padding from the
  window props. A **resize grip** at the bottom-right corner drags the window
  size (commit on release, snap to 10 px).
* Widgets are rendered as **real tkinter widgets** built with
  `tkmap.widget_options()` / `initial_state()` and laid out with `grid` using
  `model.sticky_for()` + `model.grid_weights()` + padx/pady, so it looks like
  the real program. `Notebook` → real `ttk.Notebook` with a `tk.Frame` page
  per Tab. Text/Listbox with scrollbar → Frame + widget + Scrollbar (same as
  codegen). Images: `tk.PhotoImage(file=...)` (keep references; on failure
  show the text "[picture]").
* Rendered widgets must be inert: bind press/motion/release on every tk widget
  (incl. inner parts) and return `"break"`. Exception: clicking a notebook's
  tab label switches tab (and selects the Tab node).
* **Click** selects. Selection = coloured outline (`theme.SELECT`) drawn around
  the widget (e.g. 4 thin Frames placed with `place(in_=widget)` or similar).
  Clicking empty container area selects the container.
* **Double-click** a Label/Button/Checkbox/Option/Group box/Tab → small inline
  editor to change its text in place (Enter = commit, Esc = cancel).
* **Right-click** context menu: Edit text…, Actions… (calls
  `app.inspector.show_actions(node_id)` if it exists), Rename…, Duplicate,
  Delete, Select parent, Add tab (Tabs/Tab), Move up/down/left/right (swap
  with neighbour cell).
* **Drag & drop with snapping** (the heart of the program):
  - From the palette (`begin_drag_new`) or by press-and-drag (> 5 px) on an
    existing widget (moves it; dragging a container moves its whole content).
  - While dragging, show a ghost (small borderless Toplevel near the pointer
    with icon + name, offset so it isn't under the pointer).
  - Find the target with `winfo_containing(x_root, y_root)` → design node.
  - Over a non-container widget W (in container C): pointer in left 25% →
    insert left; right 25% → insert right; else upper half → above, lower
    half → below. Show a thick green insertion line (`theme.DROP_OK`) on that
    edge of W. Drop → `project.place/move(…, C, W.row, W.col, mode)`.
  - Over a container: each container shows **drop slots**: empty cells of its
    grid plus one extra row below and one extra column to the right (and the
    whole area if empty, labelled "Drop widgets here"). Slots are light dashed
    or outlined boxes (`theme.DROP_SLOT`); hovered slot turns
    `theme.DROP_SLOT_HOVER`. Drop on slot → mode `"cell"`. Slots should be
    visible only while dragging *except* the "Drop widgets here" hint of empty
    containers, which is always shown, so the static design looks like the
    real app.
  - Over a Notebook: hovering a tab label for ~400 ms switches to that tab;
    the tab page is the container. Dropping a `Tab` onto a Notebook adds a tab.
  - Invalid targets (`project.can_contain` false, or into itself) show no
    indicator; dropping there cancels. Esc cancels the drag.
  - After drop: `app.commit("Moved …")` and `app.select(node_id)`.
* Keyboard (when workplane has focus): arrows move the selected widget one
  cell (swap), Delete / Ctrl+D handled globally by app.
* `refresh()` must be fast enough for live property editing (rebuild only the
  current window).

## Palette (agent B)

* Sections from `model.PALETTE_CATEGORIES`, items = `WIDGETS` with
  `palette != False`, showing icon + friendly name; tooltip = `help`.
* Press + drag → `app.workplane.begin_drag_new(wtype, event)`.
* Plain click → `app.add_widget(wtype)` (adds to selected container).
* Search box at top filters items.

## Inspector (agent C) – behaviour

* Header: icon + friendly type name + editable **Name** (id) with validation
  via `project.rename` (show error inline, not a crash).
* `ttk.Notebook` with tabs **Properties**, **Layout** (not for Window/Tab),
  **Actions** (only when the type has events or is a Window with menus).
* Properties grouped by spec `group`. Editors per kind:
  text→Entry, multiline→small Text (3–4 lines), lines→Text (one item per
  line), int/float→Spinbox, bool→Checkbutton, color→swatch button
  (`tkinter.colorchooser`) + "×" to reset to default + a row of ~10 quick
  colour swatches, font→family Combobox + size Spinbox + B / I / U toggles,
  choice→readonly Combobox showing labels, image→path + Browse… (png/gif),
  menu→"Edit menu bar…" button opening a menu editor dialog.
* Changes apply live (`app.commit(..., merge_key=("prop", id, key), skip=self)`)
  so the workplane updates while typing; invalid numbers are ignored until valid.
* Layout tab: `model.LAYOUT_SPECS` + a small friendly diagram/explanation;
  plus buttons "Move ← ↑ → ↓".
* For Notebook: list of tabs with add / rename / remove / move up / down.
* Actions tab: for each event of the widget (`WIDGETS[type]["events"]`): a
  header "When clicked:" then the action list shown as readable sentences
  (`model.action_summary`) with ✎ edit, ↑ ↓ reorder, ✕ delete, and
  "＋ Add action" which opens the **action editor dialog**: left = actions
  grouped by category, right = form for the chosen action's params:
  widget→Combobox of matching widgets (`project.nodes_of_types(types)` shown
  with `display_name`), window→windows, tab→tabs, text→Entry with an
  "Insert value of…" button that inserts `{widget_id}` placeholders, code→Text.
  Show a sentence preview at the bottom. OK / Cancel.
* Public helper `show_actions(node_id)` – select node & switch to Actions tab.
* No selection (`show(None)`): show a friendly getting-started help page
  (how to drag, select, add actions, run, get code).
* Menu editor dialog (dialogs.py): add/remove/rename top menus, items,
  separators; each item has an actions list using the same action editor.

## Outline (agent C)

* `ttk.Treeview` of all windows and their widget trees (icon + id + friendly
  type, e.g. "▭ button1 – Button"). Click selects (`app.select(id, source=self)`).
  Keep expansion state across refresh. Optionally drag to reorder is NOT needed.

## Code generation (agent A)

Output: one self-contained file, stdlib only, readable for beginners:

```python
"""<App title> - made with TkBuilder."""
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog
import tkinter.font as tkfont

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        DEFAULT_FONT = ...   # (only if used) family of TkDefaultFont
        self.title("My App")
        self.geometry("420x320")
        ...build main window widgets as self.<id>...
        ...menu bar...
        ...event wiring...

    # one method per secondary window:  def open_<window_id>(self): (re-use if already open -> lift)
    # one method per event with actions: def <id>_<event>(self, event=None):
    # small helper methods only if used: _get(widget), _set(widget, value), ...

if __name__ == "__main__":
    app = App()
    app.mainloop()
```

* Use `tkmap.WIDGET_CLASS`, `widget_options`, `initial_state`, `font_value`,
  `model.sticky_for`, `model.grid_weights` – identical results to the designer.
* Variables: Checkbutton → `tk.BooleanVar`; Radiobuttons share one
  `tk.StringVar` per group name (`self.<group>_var`); others use widget methods.
* Placeholders `{widget_id}` in text params become f-string parts reading the
  widget's current value via a `_get` helper (works for every widget type,
  including Text, Listbox selection, Checkbutton, Scale, Radiobutton group…).
  `{{`/`}}` are literal braces.
* Every action type in `model.ACTIONS` must be implemented. `confirm` and
  `check` stop the remaining actions (`return`). Actions referring to a
  missing widget/window are skipped with a `# skipped: ...` comment.
* Widgets in secondary windows only exist while that window is open: actions
  targeting them must not crash if it's closed (e.g. check `winfo_exists`).
* Events: Button/Check/Radio/Spinbox/Scale `command=`; Entry `<Return>`;
  Combobox `<<ComboboxSelected>>`; Listbox `<<ListboxSelect>>`; Notebook
  `<<NotebookTabChanged>>`; Canvas `<Button-1>`; Window on_open → run with
  `after(0)`/at end of build; on_close → `protocol("WM_DELETE_WINDOW")` that
  runs actions then destroys.
* Window props: title, geometry, resizable, bg, padx/pady (padding), menu bar.
* Output must pass `compile()` and run. `test_codegen.py` builds several
  projects covering every widget/action and checks they compile and construct
  (create App, `update()`, invoke handlers where safe, destroy).

## CodeView (agent A)

Toplevel "Your Python code": read-only syntax-highlighted Text with line
numbers, buttons **Save as .py…**, **Copy to clipboard**, **Close**, plus a
friendly note on how to run it ("double-click the file, or run
`python <name>.py`").
