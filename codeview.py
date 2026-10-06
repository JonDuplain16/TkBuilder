"""
"Get Python Code" window: shows the generated program with simple syntax
highlighting and line numbers, and lets the user save or copy it.
"""

import io
import keyword
import os
import tokenize
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font as tkfont

import theme

CODE_BG = "#fbfcfe"
GUTTER_BG = "#eef1f5"
COLORS = {
    "keyword": "#7c3aed",    # def, class, if, return, True...
    "string": "#15803d",
    "comment": "#6b7280",
    "number": "#c2410c",
    "defname": "#2563eb",    # name after def / class
    "self": "#9333ea",
}


def _code_font(root):
    families = set(tkfont.families(root))
    for fam in ("Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"):
        if fam in families:
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
                elif prev is not None and prev.type == tokenize.NAME and prev.string in ("def", "class"):
                    yield "defname", tok.start, tok.end
                elif text == "self":
                    yield "self", tok.start, tok.end
            if ttype not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
                prev = tok
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return  # unfinished code: keep whatever was coloured so far


class CodeView(tk.Toplevel):
    """Toplevel window that shows the generated Python program."""

    def __init__(self, app, code, filename):
        super().__init__(app)
        self.app = app
        self.code = code
        self.filename = filename or "my_app.py"
        self.title("Your Python code")
        self.geometry("920x680")
        self.minsize(560, 360)
        self.configure(bg=theme.BG)
        self.transient(app)

        self._build()
        self._fill()
        self.bind("<Escape>", lambda e: self.destroy())
        self.focus_set()

    # ------------------------------------------------------------------ UI
    def _build(self):
        top = ttk.Frame(self, padding=(14, 12, 14, 6))
        top.pack(side="top", fill="x")
        ttk.Label(top, text="Here is your app as a Python program",
                  style="Heading.TLabel").pack(anchor="w")
        name = self.filename
        ttk.Label(top, style="Muted.TLabel", justify="left", wraplength=860, text=(
            "Save it with the button below. To run it, double-click the saved file, or open a "
            "terminal in that folder and type:  python %s\n"
            "It only needs Python (from python.org) - nothing else to install. You can open the "
            "file in any text editor (IDLE, VS Code, ...) to read or change it." % name)
        ).pack(anchor="w", pady=(4, 0))

        bottom = ttk.Frame(self, padding=(14, 6, 14, 12))
        bottom.pack(side="bottom", fill="x")
        save = ttk.Button(bottom, text="\U0001F4BE Save as .py…", style="Accent.TButton",
                          command=self.save)
        save.pack(side="left")
        theme.tooltip(save, "Save the program as a Python file on your computer")
        copy = ttk.Button(bottom, text="⧉ Copy to clipboard", command=self.copy)
        copy.pack(side="left", padx=8)
        theme.tooltip(copy, "Copy all the code, so you can paste it somewhere else")
        ttk.Button(bottom, text="Close", command=self.destroy).pack(side="right")
        self.status = ttk.Label(bottom, style="Muted.TLabel")
        self.status.pack(side="left", padx=8)

        body = tk.Frame(self, bg=theme.BORDER, bd=0)
        body.pack(side="top", fill="both", expand=True, padx=14)
        inner = tk.Frame(body, bg=CODE_BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        font = _code_font(self)
        self.gutter = tk.Text(inner, width=4, padx=6, pady=6, bd=0, highlightthickness=0,
                              bg=GUTTER_BG, fg=theme.MUTED, font=font, takefocus=False,
                              cursor="arrow", wrap="none")
        self.text = tk.Text(inner, padx=10, pady=6, bd=0, highlightthickness=0, bg=CODE_BG,
                            fg=theme.TEXT, font=font, wrap="none", undo=False,
                            selectbackground=theme.ACCENT_LIGHT, selectforeground=theme.TEXT,
                            inactiveselectbackground=theme.ACCENT_LIGHT)
        vsb = ttk.Scrollbar(inner, orient="vertical", command=self._yview)
        hsb = ttk.Scrollbar(inner, orient="horizontal", command=self.text.xview)
        self.vsb = vsb

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

        for tag, color in COLORS.items():
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure("defname", foreground=COLORS["defname"],
                                font=font[:2] + ("bold",))
        self.text.tag_raise("sel")

        # read-only, but selecting / copying / moving around still works
        self.text.bind("<Key>", self._block_typing)
        for seq in ("<<Cut>>", "<<Paste>>", "<<Clear>>", "<<PasteSelection>>"):
            self.text.bind(seq, lambda e: "break")
        self.text.bind("<Button-2>", lambda e: "break")
        self.text.bind("<Control-a>", self._select_all)
        self.text.bind("<Control-A>", self._select_all)
        self.gutter.bind("<MouseWheel>", self._wheel)
        for seq in ("<Button-1>", "<B1-Motion>", "<Double-Button-1>", "<Key>"):
            self.gutter.bind(seq, lambda e: "break")

    def _fill(self):
        code = self.code.rstrip("\n")
        self.text.insert("1.0", code)
        for tag, start, end in highlight_ranges(code + "\n"):
            self.text.tag_add(tag, "%d.%d" % start, "%d.%d" % end)
        lines = code.count("\n") + 1
        self.gutter.configure(width=max(3, len(str(lines))))
        self.gutter.insert("1.0", "\n".join(str(i) for i in range(1, lines + 1)))
        self.gutter.tag_configure("right", justify="right")
        self.gutter.tag_add("right", "1.0", "end")
        self.gutter.configure(state="disabled")
        self.text.mark_set("insert", "1.0")
        self.text.focus_set()

    # ------------------------------------------------------------- helpers
    def _yview(self, *args):
        self.text.yview(*args)
        self.gutter.yview_moveto(self.text.yview()[0])

    def _wheel(self, event):
        self.text.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")
        return "break"

    def _block_typing(self, event):
        ctrl = event.state & 0x4
        if ctrl and event.keysym.lower() in ("c", "a", "home", "end", "insert"):
            return None
        if event.keysym in ("Left", "Right", "Up", "Down", "Prior", "Next", "Home", "End",
                            "Shift_L", "Shift_R", "Control_L", "Control_R"):
            return None
        return "break"

    def _select_all(self, _event=None):
        self.text.tag_add("sel", "1.0", "end-1c")
        return "break"

    def _say(self, text):
        self.status.configure(text=text)

    # ------------------------------------------------------------- actions
    def copy(self):
        """Copy the whole program to the clipboard."""
        self.clipboard_clear()
        self.clipboard_append(self.code)
        self.update()  # keep the clipboard filled even after this window closes
        self._say("Copied! You can now paste it into any editor.")

    def save(self):
        """Ask where to save and write the program as a .py file."""
        path = filedialog.asksaveasfilename(
            parent=self, title="Save your Python program", defaultextension=".py",
            initialfile=self.filename,
            filetypes=[("Python program", "*.py"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.code)
        except OSError as e:
            messagebox.showerror("Couldn't save", "Sorry, the file couldn't be saved:\n\n%s" % e,
                                 parent=self)
            return
        self._say("Saved to %s - double-click it to run your app." % os.path.basename(path))
