# TkBuilder

Build Python desktop apps (Tkinter) by dragging and dropping. No coding needed.

## Start it

* Double-click **`Start TkBuilder.bat`** (or `TkBuilder.pyw`), or
* run `python app.py` in this folder.

Requires Python 3.8+ (Tkinter ships with Python on Windows). No other installs.

## How to use

1. **Drag** a widget (Button, Label, Text Box, …) from the left panel onto the
   window in the middle. Green lines and boxes show where it will snap in.
   You can also just **click** a widget in the left panel to add it.
2. **Click** a widget to select it. Change its text, colours, font and size
   on the right under **Properties**. Double-click a label or button to edit
   its text in place.
3. Use **Layout** to make a widget stretch, line up left/right, or take up
   several columns.
4. Use **Actions** to make things happen, e.g. *When clicked → Show a message*,
   *Set the text of a widget*, *Open another window*, *Add an item to a list*.
   Write `{name_box}` inside a text to use what the user typed in `name_box`.
5. Need more windows? Click **➕ New Window**, then use an *Open another
   window* action on a button.
6. Press **▶ Run My App** (F5) to try it for real.
7. Press **📥 Get Python Code** to copy or save your finished program as a
   `.py` file. Run it with a double-click or `python my_app.py`.

Save your design with **💾 Save** (a `.tkb` file) to keep working on it later.
The **📚 Examples** menu has ready-made projects to learn from.

## Keyboard shortcuts

| Keys | Does |
|---|---|
| Ctrl+Z / Ctrl+Y | Undo / Redo |
| Delete | Delete selected widget |
| Ctrl+D | Duplicate selected widget |
| Arrow keys | Move selected widget one cell |
| Ctrl+S / Ctrl+O / Ctrl+N | Save / Open / New |
| F5 | Run my app |
| Esc | Cancel a drag / clear selection |

## For developers

See `SPEC.md` for the architecture. `python test_codegen.py` checks the code
generator against projects using every widget and action.

For automated runs set `TKBUILDER_NO_DIALOGS=1`: every pop-up then answers
itself instantly (logged to stderr), and TkBuilder's own dialogs close
themselves after `TKBUILDER_DIALOG_TIMEOUT` seconds (default 8), so nothing
waits for a click.
