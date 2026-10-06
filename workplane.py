"""
Workplane - the design surface of TkBuilder.

The current window of the project is shown as a *fake window* (title bar,
optional menu bar, content area of exactly width x height pixels) on a
scrollable canvas.  The widgets inside are REAL tkinter widgets built by
preview.py with the same options the generated program uses (tkmap), in the
project's app theme, so what you see is what you get.  They are made inert
by replacing their bindtags with a private tag; all mouse handling (select,
drag & drop, context menu, inline editing) goes through that tag.

Rendering is incremental: every design widget is a direct tk child of the
window's content frame and is gridded `in_` its container.  So moving a
widget (drag & drop, undo, paste...) only re-grids it - nothing is rebuilt -
and only new or changed widgets are created (creating native windows is
slow on some machines).

Drag & drop ("snapping"):
  * nothing is inserted into the design while dragging (no layout jumps);
    the target is computed from geometry (grid cell boxes, widget boxes);
  * indicators are drawn on a transparent overlay window above the design
    and glide between positions: an insertion bar next to a widget, a cell
    highlight for empty cells, "new row" / "new column" strips just outside
    a grid, an outline of the target container and its faint grid lines;
  * the ghost next to the pointer is a real rendered preview of the widget;
  * hovering a tab label switches tabs, the view scrolls near its edges,
    Esc cancels, and the dropped widget flashes briefly.
Span handles on the selected widget change how many columns/rows it spans.
"""

import json
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import tkinter.font as tkfont

import model
from model import WIDGETS
import preview
import tkmap
import theme

DRAG_THRESHOLD = 5          # pixels before a press becomes a drag
TAB_HOVER_MS = 400          # hover time on a tab label to switch tab while dragging
EDGE = 6                    # px at a panel's border = insert next to the panel
SNAP = 10                   # window resize snapping
ANIM_MS = 90                # indicator glide time
STRIP = 22                  # thickness of the "new row / column" strips
BAR = 4                     # insertion bar thickness
TEXT_EDITABLE = ("Label", "Button", "Checkbutton", "Radiobutton", "LabelFrame", "Tab")
SIDE_WORDS = {"above": "above", "below": "below", "left": "to the left of",
              "right": "to the right of"}
OVERLAY_KEY = "#ff00fe"     # transparent colour of the overlay window


def _px(n):
    f = getattr(theme, "px", None)
    return f(n) if f else n


def _ui_font(size=9, *styles):
    return (getattr(theme, "UI_FAMILY", "Segoe UI"), size) + styles


def _mix(widget, c1, c2, t):
    try:
        a = widget.winfo_rgb(c1)
        b = widget.winfo_rgb(c2)
    except tk.TclError:
        return c1
    return "#%02x%02x%02x" % tuple(int((x * (1 - t) + y * t) / 257) for x, y in zip(a, b))


def _raise(w):
    """Raise a window to the top of its siblings (Canvas.lift means tag_raise)."""
    try:
        w.tk.call("raise", w._w)
    except tk.TclError:
        pass


def _ease(t):
    return 1 - (1 - t) ** 3


# --------------------------------------------------------------------------
# Small overlay helper: an outline made of 4 thin frames (selection, hover)
# --------------------------------------------------------------------------

class _Box:
    def __init__(self, parent, color):
        self.parts = [tk.Frame(parent, bg=color, bd=0, highlightthickness=0) for _ in range(4)]
        self.target = None

    def show(self, w, out=2, t=2):
        top, bottom, left, right = self.parts
        try:
            top.place(in_=w, x=-out, y=-out, relwidth=1, width=2 * out, height=t)
            bottom.place(in_=w, x=-out, rely=1, y=out - t, relwidth=1, width=2 * out, height=t)
            left.place(in_=w, x=-out, y=-out, relheight=1, height=2 * out, width=t)
            right.place(in_=w, relx=1, x=out - t, y=-out, relheight=1, height=2 * out, width=t)
            for p in self.parts:
                p.lift()
            self.target = w
        except tk.TclError:
            self.hide()

    def color(self, c):
        for p in self.parts:
            p.configure(bg=c)

    def lift(self):
        for p in self.parts:
            try:
                p.lift()
            except tk.TclError:
                pass

    def hide(self):
        for p in self.parts:
            try:
                p.place_forget()
            except tk.TclError:
                pass
        self.target = None


# --------------------------------------------------------------------------
# Overlay: a borderless, transparent window over the design area on which
# the drag indicators are drawn (so the design itself never changes while
# dragging).  Shapes glide to new positions.
# --------------------------------------------------------------------------

class _Overlay:
    def __init__(self, wp):
        self.wp = wp
        self.top = None
        self.ok = None              # None = not tried yet
        self.origin = (0, 0)
        self.anims = {}             # item tag -> anim state
        self.tick = None
        self.shown = False
        self.grid_items = []

    # ---- window ---------------------------------------------------------
    def _ensure(self):
        if self.ok is not None:
            return self.ok
        try:
            top = tk.Toplevel(self.wp)
            top.withdraw()
            top.overrideredirect(True)
            top.attributes("-transparentcolor", OVERLAY_KEY)
            c = tk.Canvas(top, bg=OVERLAY_KEY, highlightthickness=0, bd=0)
            c.pack(fill="both", expand=True)
            top.bind("<Escape>", self.wp._on_escape)
        except tk.TclError:
            self.ok = False
            return False
        self.top, self.c = top, c
        acc, ok = theme.ACCENT, theme.DROP_OK
        c.create_rectangle(0, 0, 0, 0, outline="", fill=theme.WORKPLANE_BG, stipple="gray50",
                           state="hidden", tags=("dim",))
        c.create_rectangle(0, 0, 0, 0, outline=acc, width=_px(2), state="hidden", tags=("box",))
        c.create_rectangle(0, 0, 0, 0, outline=ok, width=_px(2), fill=ok, stipple="gray25",
                           state="hidden", tags=("ind",))
        c.create_rectangle(0, 0, 0, 0, outline="", fill=_mix(c, ok, "#000000", 0.4),
                           state="hidden", tags=("lab", "labbg"))
        c.create_text(0, 0, text="", fill="#ffffff", font=_ui_font(8, "bold"),
                      state="hidden", tags=("lab", "labtext"))
        c.create_rectangle(0, 0, 0, 0, outline=ok, width=2, fill=ok, stipple="gray50",
                           state="hidden", tags=("flash",))
        self.ok = True
        return True

    def show(self):
        if not self._ensure():
            return False
        cv = self.wp.canvas
        try:
            x, y = cv.winfo_rootx(), cv.winfo_rooty()
            w, h = max(1, cv.winfo_width()), max(1, cv.winfo_height())
            self.origin = (x, y)
            self.top.geometry("%dx%d+%d+%d" % (w, h, x, y))
            if not self.shown:
                self.top.deiconify()
                self.shown = True
        except tk.TclError:
            return False
        return True

    def hide(self):
        self.stop()
        if self.top is not None and self.shown:
            try:
                for tag in ("dim", "box", "ind", "lab", "flash"):
                    self.c.itemconfigure(tag, state="hidden")
                self.set_grid([])
                self.top.withdraw()
            except tk.TclError:
                pass
        self.shown = False
        self.anims = {}

    def destroy(self):
        self.stop()
        if self.top is not None:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
        self.top = None
        self.ok = None
        self.shown = False

    def local(self, rect):
        ox, oy = self.origin
        return (rect[0] - ox, rect[1] - oy, rect[2] - ox, rect[3] - oy)

    # ---- shapes ---------------------------------------------------------
    def set_rect(self, tag, rect, animate=True):
        """Show item `tag` at root rectangle `rect` (None hides it)."""
        if not self.ok or self.top is None:
            return
        c = self.c
        if rect is None:
            c.itemconfigure(tag, state="hidden")
            self.anims.pop(tag, None)
            return
        dst = self.local(rect)
        visible = c.itemcget(tag, "state") != "hidden"
        if not animate or not visible:
            c.coords(tag, *dst)
            c.itemconfigure(tag, state="normal")
            self.anims.pop(tag, None)
            return
        cur = c.coords(tag)
        a = self.anims.get(tag)
        if a is not None and a["dst"] == dst:
            return
        if a is None and len(cur) == 4 and tuple(round(v) for v in cur) == tuple(dst):
            return
        self.anims[tag] = {"src": tuple(cur) if len(cur) == 4 else dst, "dst": dst,
                           "t0": time.perf_counter()}
        self._schedule()

    def current(self, tag):
        """Destination root rect of an item (or None if hidden)."""
        if not self.ok or self.c.itemcget(tag, "state") == "hidden":
            return None
        a = self.anims.get(tag)
        r = a["dst"] if a else tuple(self.c.coords(tag))
        ox, oy = self.origin
        return (r[0] + ox, r[1] + oy, r[2] + ox, r[3] + oy)

    def style_ind(self, kind):
        """Look of the main indicator: 'bar', 'cell' or 'strip'."""
        if not self.ok:
            return
        ok = theme.DROP_OK
        if kind == "bar":
            self.c.itemconfigure("ind", fill=ok, stipple="", outline=ok, width=1, dash=())
        elif kind == "strip":
            self.c.itemconfigure("ind", fill=ok, stipple="gray25", outline=ok, width=_px(2),
                                 dash=(4, 3))
        else:
            self.c.itemconfigure("ind", fill=ok, stipple="gray25", outline=ok, width=_px(2),
                                 dash=())

    def set_label(self, text, rect):
        """Small caption bubble centred in root rect (None hides)."""
        if not self.ok:
            return
        c = self.c
        if not text or rect is None:
            c.itemconfigure("lab", state="hidden")
            return
        x0, y0, x1, y1 = self.local(rect)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        c.itemconfigure("labtext", text=text, state="normal")
        c.coords("labtext", cx, cy)
        bb = c.bbox("labtext")
        if bb:
            c.coords("labbg", bb[0] - 5, bb[1] - 1, bb[2] + 5, bb[3] + 1)
        c.itemconfigure("lab", state="normal")
        c.tag_raise("labbg")
        c.tag_raise("labtext")

    def set_grid(self, lines):
        """Faint grid lines: list of root (x0, y0, x1, y1)."""
        if not self.ok or self.top is None:
            return
        c = self.c
        while len(self.grid_items) < len(lines):
            self.grid_items.append(c.create_line(0, 0, 0, 0, fill=theme.ACCENT, dash=(2, 3),
                                                 state="hidden"))
            c.tag_lower(self.grid_items[-1])
        for i, item in enumerate(self.grid_items):
            if i < len(lines):
                c.coords(item, *self.local(lines[i]))
                c.itemconfigure(item, state="normal")
            else:
                c.itemconfigure(item, state="hidden")

    # ---- animation ------------------------------------------------------
    def _schedule(self):
        if self.tick is None:
            self.tick = self.wp.after(15, self._step)

    def stop(self):
        if self.tick is not None:
            try:
                self.wp.after_cancel(self.tick)
            except tk.TclError:
                pass
        self.tick = None

    def _step(self):
        self.tick = None
        if not self.ok or self.top is None:
            return
        now = time.perf_counter()
        done = []
        for tag, a in self.anims.items():
            f = min(1.0, (now - a["t0"]) * 1000.0 / ANIM_MS)
            e = _ease(f)
            r = [s + (d - s) * e for s, d in zip(a["src"], a["dst"])]
            try:
                self.c.coords(tag, *r)
            except tk.TclError:
                pass
            if f >= 1.0:
                done.append(tag)
        for tag in done:
            del self.anims[tag]
        if self.anims:
            self._schedule()


# --------------------------------------------------------------------------
# Ghost: a small semi-transparent window next to the pointer showing a real
# preview of what is being dragged.
# --------------------------------------------------------------------------

class _Ghost:
    def __init__(self, wp):
        self.wp = wp
        self.top = None
        self.cache = {}             # (wtype, style signature) -> frame
        self.current = None
        self.offset = (18, 16)
        self.valid = None

    def _ensure(self):
        if self.top is not None:
            return
        g = tk.Toplevel(self.wp)
        g.withdraw()
        g.overrideredirect(True)
        try:
            g.attributes("-topmost", True)
            g.attributes("-alpha", 0.85)
        except tk.TclError:
            pass
        g.configure(bg=theme.ACCENT)
        self.border = tk.Frame(g, bg=theme.ACCENT, padx=1, pady=1)
        self.border.pack()
        self.body = tk.Frame(self.border, bg=theme.PANEL)
        self.body.pack()
        self.cap = tk.Label(self.border, bg=theme.ACCENT, fg=theme.ACCENT_FG or "#ffffff",
                            font=_ui_font(8, "bold"), anchor="w", padx=6, pady=1)
        self.cap.pack(side="bottom", fill="x")
        g.bind("<Escape>", self.wp._on_escape)
        self.top = g

    def show(self, content_factory, key, caption, x, y):
        """content_factory(parent) -> widget, cached under key (None = no cache)."""
        self._ensure()
        if self.current is not None:
            try:
                self.current.pack_forget()
                if self.current not in self.cache.values():
                    self.current.destroy()
            except tk.TclError:
                pass
            self.current = None
        frame = self.cache.get(key) if key is not None else None
        if frame is None:
            frame = tk.Frame(self.body, bg=theme.PANEL)
            try:
                content_factory(frame)
            except tk.TclError:
                pass
            for w in preview.all_widgets(frame):
                try:
                    w.bindtags((str(w), "all") if w is not frame else w.bindtags())
                    w.configure(cursor="arrow")
                except tk.TclError:
                    pass
            if key is not None:
                self.cache[key] = frame
        frame.pack()
        self.current = frame
        self.cap.configure(text=caption)
        self.valid = None
        self.set_valid(True)
        self.move(x, y)
        try:
            self.top.deiconify()
        except tk.TclError:
            pass

    def lift(self):
        if self.top is not None:
            try:
                self.top.lift()
            except tk.TclError:
                pass

    def move(self, x, y):
        if self.top is not None:
            try:
                self.top.geometry("+%d+%d" % (x + self.offset[0], y + self.offset[1]))
            except tk.TclError:
                pass

    def set_valid(self, ok):
        if self.top is None or self.valid == ok:
            return
        self.valid = ok
        color = theme.ACCENT if ok else theme.MUTED
        try:
            self.border.configure(bg=color)
            self.cap.configure(bg=color)
            self.top.attributes("-alpha", 0.85 if ok else 0.6)
        except tk.TclError:
            pass

    def glide(self, rect, on_done=None):
        """Slide to rect (root coords) while fading out, then hide."""
        if self.top is None or rect is None:
            self.hide()
            if on_done:
                on_done()
            return
        try:
            x0, y0 = self.top.winfo_rootx(), self.top.winfo_rooty()
        except tk.TclError:
            self.hide()
            return
        t0 = time.perf_counter()

        def step():
            f = min(1.0, (time.perf_counter() - t0) * 1000.0 / 110)
            e = _ease(f)
            try:
                self.top.geometry("+%d+%d" % (x0 + (rect[0] - x0) * e, y0 + (rect[1] - y0) * e))
                self.top.attributes("-alpha", 0.85 - 0.6 * e)
            except tk.TclError:
                return
            if f < 1.0:
                self.wp.after(15, step)
            else:
                self.hide()
                if on_done:
                    on_done()
        step()

    def hide(self):
        if self.top is not None:
            try:
                self.top.withdraw()
                self.top.attributes("-alpha", 0.85)
            except tk.TclError:
                pass

    def reset(self):
        """Forget cached previews (theme changed)."""
        for f in self.cache.values():
            try:
                f.destroy()
            except tk.TclError:
                pass
        self.cache = {}
        self.current = None

    def destroy(self):
        if self.top is not None:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
        self.top = None
        self.cache = {}
        self.current = None


def _capture_png(widget):
    """PNG bytes of what `widget` shows on screen right now (Windows only;
    None elsewhere or on failure).  Copies the pixels from the window's
    device context, which takes a few milliseconds."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import struct
        import zlib
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

        class BIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32), ("a", ctypes.c_int32),
                        ("b", ctypes.c_int32), ("c", ctypes.c_uint32), ("d", ctypes.c_uint32)]
        top = widget.winfo_toplevel()
        hwnd = top.winfo_id()
        x, y = widget.winfo_rootx() - top.winfo_rootx(), widget.winfo_rooty() - top.winfo_rooty()
        w, h = widget.winfo_width(), widget.winfo_height()
        if w < 2 or h < 2 or w * h > 4000000 or not widget.winfo_viewable():
            return None
        hdc = user32.GetDC(hwnd)
        mdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(mdc, bmp)
        gdi32.BitBlt(mdc, 0, 0, w, h, hdc, x, y, 0x00CC0020)
        bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bi), 0)
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
        raw = buf.raw
        rgb = bytearray(w * h * 3)
        rgb[0::3] = raw[2::4]
        rgb[1::3] = raw[1::4]
        rgb[2::3] = raw[0::4]
        stride = w * 3
        rows = b"".join(b"\x00" + bytes(rgb[i * stride:(i + 1) * stride]) for i in range(h))

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b""))
    except Exception:  # noqa: BLE001 - purely cosmetic, fall back to a rebuilt preview
        return None


# --------------------------------------------------------------------------
# Rendered node record
# --------------------------------------------------------------------------

class _Rec:
    __slots__ = ("id", "r", "sig", "place", "parent", "paths")

    def __init__(self, node_id, rendered, sig):
        self.id = node_id
        self.r = rendered
        self.sig = sig
        self.place = None       # placement signature last applied
        self.parent = None      # container id it is placed in
        self.paths = []


# --------------------------------------------------------------------------
# Workplane
# --------------------------------------------------------------------------

class Workplane(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.current_window_id = app.project.main_window["id"]
        self._tag = "TkbDesign%d" % id(self)
        self._srctag = "TkbDragSrc%d" % id(self)
        self._handletag = "TkbHandle%d" % id(self)

        self._tab_of = {}        # notebook id -> tab id shown
        self._node_of = {}       # tk path -> node id
        self._rec = {}           # node id -> _Rec
        self._hints = {}         # container id -> hint canvas
        self._groups = {}        # radio group -> StringVar
        self._chrome = {}        # tk path -> "title" | "menu" | "frame"
        self._images = preview.ImageCache(self)
        self._outer = None
        self._content = None
        self._win_item = None
        self._win_rendered = None
        self._win_sig = None
        self._style_sig = None
        self.styler = None
        self._press = None
        self._drag = None
        self._span = None
        self._editor = None
        self._resize = None
        self._winbar_sig = None
        self._win_xy = (0, 0)
        self._win_size = (0, 0)
        self._menu_h = 0
        self._sel_id = None
        self._geo_cache = None
        self._pending_refresh = False
        self._autoscroll_id = None
        self._flash_id = None
        self._clip_text = None
        self._clip_src = ()
        self._created_last = []
        self._sel_state = None
        self._sel_restack = 0
        self.overlay = _Overlay(self)
        self.ghost = _Ghost(self)

        self._build_chrome()
        self._bind_tags()
        self.refresh()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _on_destroy(self, event):
        if event.widget is self:
            self.overlay.destroy()
            self.ghost.destroy()

    # ================================================================ chrome
    def _build_chrome(self):
        self.winbar = tk.Frame(self, bg=theme.BG, highlightthickness=0)
        self.winbar.pack(side="top", fill="x")
        tk.Frame(self, bg=theme.BORDER, height=1).pack(side="top", fill="x")

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=theme.WORKPLANE_BG, highlightthickness=0, bd=0,
                                takefocus=1)
        vsb = ttk.Scrollbar(body, orient="vertical", command=self._yview)
        hsb = ttk.Scrollbar(body, orient="horizontal", command=self._xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)

        c = self.canvas
        c.bind("<Configure>", lambda e: self._layout_canvas())
        c.bind("<ButtonPress-1>", self._on_canvas_press)
        c.bind("<MouseWheel>", self._on_wheel)
        c.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        c.bind("<Escape>", self._on_escape)
        for key, d in (("Left", "left"), ("Right", "right"), ("Up", "up"), ("Down", "down")):
            c.bind("<%s>" % key, lambda e, d=d: (self._key_move(d), "break")[1])
        c.bind("<F2>", lambda e: (self.edit_text(self.app.selected_id), "break")[1])
        c.bind("<Return>", lambda e: (self.edit_text(self.app.selected_id), "break")[1])

        # shadow, resize grip + size caption (canvas items)
        bg = theme.WORKPLANE_BG
        c.create_rectangle(0, 0, 0, 0, fill=_mix(c, bg, "#000000", 0.06), outline="",
                           tags=("shadow2",))
        c.create_rectangle(0, 0, 0, 0, fill=_mix(c, bg, "#000000", 0.12), outline="",
                           tags=("shadow1",))
        c.create_rectangle(0, 0, 18, 18, fill=bg, outline="", tags=("grip", "griparea"))
        for i in range(3):
            c.create_line(0, 0, 0, 0, fill=theme.MUTED, width=2, tags=("grip", "gripline%d" % i))
        c.create_text(0, 0, anchor="ne", fill=theme.MUTED, font=_ui_font(9), tags=("sizetext",))
        # "content doesn't fit" warning under the size caption (click = fit)
        c.create_text(0, 0, anchor="ne", fill=getattr(theme, "WARNING", "#d97706"),
                      font=_ui_font(9, "bold"), text="", tags=("fittext",))
        c.tag_bind("fittext", "<Enter>", lambda e: c.configure(cursor="hand2"))
        c.tag_bind("fittext", "<Leave>", lambda e: c.configure(cursor=""))
        c.tag_bind("fittext", "<ButtonPress-1>", lambda e: self.fit_window())
        c.tag_bind("grip", "<Enter>", lambda e: c.configure(cursor="size_nw_se"))
        c.tag_bind("grip", "<Leave>", lambda e: self._resize or c.configure(cursor=""))
        c.tag_bind("grip", "<ButtonPress-1>", self._grip_press)
        c.tag_bind("grip", "<B1-Motion>", self._grip_motion)
        c.tag_bind("grip", "<ButtonRelease-1>", self._grip_release)

    def _xview(self, *args):
        self.canvas.xview(*args)
        self._geo_cache = None

    def _yview(self, *args):
        self.canvas.yview(*args)
        self._geo_cache = None

    def _bind_tags(self):
        t = self._tag
        self.bind_class(t, "<ButtonPress-1>", self._on_press)
        self.bind_class(t, "<B1-Motion>", self._on_b1_motion)
        self.bind_class(t, "<ButtonRelease-1>", self._on_release)
        self.bind_class(t, "<Double-Button-1>", self._on_double)
        self.bind_class(t, "<ButtonPress-3>", self._on_right)
        self.bind_class(t, "<Motion>", self._on_hover)
        self.bind_class(t, "<Leave>", self._on_leave)
        self.bind_class(t, "<MouseWheel>", self._on_wheel)
        self.bind_class(t, "<Shift-MouseWheel>", self._on_shift_wheel)
        for seq in ("<ButtonPress-2>", "<ButtonRelease-2>", "<ButtonRelease-3>", "<Key>",
                    "<B2-Motion>", "<B3-Motion>", "<Enter>"):
            self.bind_class(t, seq, lambda e: "break")
        s = self._srctag
        self.bind_class(s, "<B1-Motion>", self._src_motion)
        self.bind_class(s, "<ButtonRelease-1>", self._src_release)
        self.bind_class(s, "<Escape>", self._on_escape)
        h = self._handletag
        self.bind_class(h, "<ButtonPress-1>", self._span_press)
        self.bind_class(h, "<B1-Motion>", self._span_motion)
        self.bind_class(h, "<ButtonRelease-1>", self._span_release)

    # ------------------------------------------------------------ window bar
    def _refresh_winbar(self):
        project = self.app.project
        sig = tuple((w["id"], w["props"].get("title", "")) for w in project.windows) + (
            self.current_window_id,)
        if sig == self._winbar_sig:
            return
        self._winbar_sig = sig
        for ch in self.winbar.winfo_children():
            ch.destroy()
        for i, w in enumerate(project.windows):
            title = w["props"].get("title", "") or "(no title)"
            text = ("Main window: %s" if i == 0 else w["id"] + ": %s") % title
            if len(text) > 34:
                text = text[:33] + "…"
            cur = w["id"] == self.current_window_id
            holder = tk.Frame(self.winbar, bg=theme.BG)
            holder.pack(side="left", padx=(_px(6) if i == 0 else _px(2), 0), pady=(_px(6), 0))
            lab = tk.Label(holder, text="▣  " + text, padx=_px(12), pady=_px(5), cursor="hand2",
                           bg=theme.PANEL if cur else theme.BG,
                           fg=theme.TEXT if cur else theme.MUTED,
                           font=_ui_font(9, "bold") if cur else _ui_font(9))
            lab.pack(side="top")
            tk.Frame(holder, height=_px(3), bg=theme.ACCENT if cur else theme.BG).pack(
                side="top", fill="x")

            def click(_e, wid=w["id"]):
                self.set_window(wid)
                self.app.select(wid)
            lab.bind("<Button-1>", click)
            if not cur:
                lab.bind("<Enter>", lambda e, lb=lab: lb.configure(bg=theme.ACCENT_LIGHT))
                lab.bind("<Leave>", lambda e, lb=lab: lb.configure(bg=theme.BG))
            theme.tooltip(lab, "Show and edit this window" if not cur else
                          "This is the window you are editing")
        add = tk.Label(self.winbar, text="＋ Window", padx=_px(10), pady=_px(5), cursor="hand2",
                       bg=theme.BG, fg=theme.ACCENT, font=_ui_font(9, "bold"))
        add.pack(side="left", padx=_px(6), pady=(_px(6), _px(3)))
        add.bind("<Button-1>", lambda e: self.app.add_window())
        add.bind("<Enter>", lambda e: add.configure(bg=theme.ACCENT_LIGHT))
        add.bind("<Leave>", lambda e: add.configure(bg=theme.BG))
        theme.tooltip(add, "Add another window to your app (for example a Settings window)")

    # ================================================================ public
    def set_window(self, window_id):
        node = self.app.project.find(window_id)
        if node is None or node["type"] != "Window":
            return
        if window_id != self.current_window_id:
            self.current_window_id = window_id
            self.refresh()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)

    def current_tab_of(self, notebook_id):
        nb_node = self.app.project.find(notebook_id)
        if nb_node is None or not nb_node.get("children"):
            return None
        ids = [t["id"] for t in nb_node["children"]]
        rec = self._rec.get(notebook_id)
        if rec is not None:
            try:
                tid = self._node_of.get(str(rec.r.main.select()))
                if tid in ids:
                    return tid
            except tk.TclError:
                pass
        tid = self._tab_of.get(notebook_id)
        return tid if tid in ids else ids[0]

    def show_selection(self, node_id):
        self._sel_id = node_id
        if self._outer is None:
            return
        w = self._widget(node_id) if node_id else None
        state = (node_id, str(w), self._sel_restack)
        if w is not None and state == self._sel_state and self._sel_box.target is w:
            self._reveal(node_id)
            return
        self._sel_state = state
        self._sel_box.hide()
        self._badge.place_forget()
        self._hide_handles()
        project = self.app.project
        node = project.find(node_id) if node_id else None
        if node is None or self._widget(node_id) is None:
            return
        self._reveal(node_id)
        w = self._widget(node_id)
        if node["type"] in ("Window", "Tab"):
            self._sel_box.show(w, out=0, t=2)
        else:
            self._sel_box.show(w, out=2, t=2)
            self._badge.configure(text=" %s " % node_id)
            try:
                if node["type"] == "LabelFrame":   # don't hide the group's title
                    self._badge.place(in_=w, relx=1, x=2, y=-2, anchor="se")
                else:
                    self._badge.place(in_=w, x=-2, y=-2, anchor="sw")
                self._badge.lift()
            except tk.TclError:
                pass
            self._show_handles(node, w)
        if self._hover_box.target is w:
            self._hover_box.hide()

    def begin_drag_new(self, wtype, event):
        """Start dragging a new widget of type wtype (called by the palette
        when an item is pressed and dragged).  The palette widget that got the
        press keeps receiving the mouse events; we temporarily put our own
        bindtag in front of its tags to receive them."""
        if wtype not in WIDGETS:
            return
        self._cancel_drag(quiet=True)
        src = event.widget
        try:
            tags = src.bindtags()
            if self._srctag not in tags:
                src.bindtags((self._srctag,) + tuple(tags))
        except (tk.TclError, AttributeError):
            src = None
        self._start_drag("new", wtype, None, event.x_root, event.y_root, src=src)

    # ---- clipboard --------------------------------------------------------
    def copy_selection(self):
        """Copy the selected widget (with its content) to the clipboard."""
        project = self.app.project
        node = self.app.selected
        if node is None or node["type"] == "Window":
            self.app.status("Select a widget first, then copy it.")
            return False
        text = project.copy_nodes([node["id"]])
        self._clip_text = text
        self._clip_src = (node["id"],)
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except tk.TclError:
            pass
        self.app.status("Copied %s. Select where it should go and press Ctrl+V to paste."
                        % node["id"])
        return True

    def cut_selection(self):
        """Copy the selected widget to the clipboard and remove it."""
        node = self.app.selected
        if node is None or node["type"] == "Window":
            self.app.status("Select a widget first, then cut it.")
            return False
        project = self.app.project
        parent = project.parent_of(node["id"])
        snap = project.snapshot()
        if not self.copy_selection():
            return False
        try:
            project.remove(node["id"])
        except ValueError as e:
            project.restore(snap)
            self.app.status(str(e))
            return False
        self._clip_src = ()
        self.app.selected_id = None
        self.app.commit("Cut %s. Press Ctrl+V to paste it somewhere else." % node["id"])
        self.app.select(parent["id"] if parent is not None and parent["type"] != "Window"
                        else None)
        return True

    def paste(self):
        """Paste widgets from the clipboard below the selected widget or into
        the selected container."""
        project = self.app.project
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = None
        if not text or not project.is_clip(text):
            text = self._clip_text
        if not text or not project.is_clip(text):
            self.app.status("Nothing to paste. Copy a widget first (Ctrl+C).")
            return []
        try:
            types = [n["type"] for n in json.loads(text).get("nodes", [])]
        except (ValueError, AttributeError):
            types = []
        if not types:
            return []
        cid, row, col = self._paste_spot(types[0], text)
        if cid is None:
            self.app.status("A %s can't be pasted here. Select a place that can hold it first."
                            % WIDGETS[types[0]]["name"])
            return []
        snap = project.snapshot()
        try:
            new = project.paste(text, cid, row, col, "below")
        except (ValueError, KeyError) as e:
            project.restore(snap)
            self.app.status(str(e) or "Couldn't paste that here.")
            return []
        if not new:
            self.app.status("That can't be pasted here.")
            return []
        if project.find(cid)["type"] == "Notebook":
            self._tab_of[cid] = new[0]["id"]
        self.app.commit("Pasted %s." % ", ".join(n["id"] for n in new))
        self.app.select(new[0]["id"])
        return new

    def _paste_spot(self, wtype, text):
        """(container id, row, col) where pasted widgets should go."""
        project = self.app.project
        sel = self.app.selected
        cands = []
        if sel is not None:
            if model.is_container(sel) and sel["id"] not in self._clip_src:
                if sel["type"] == "Notebook" and wtype != "Tab":
                    tab = self.current_tab_of(sel["id"])
                    if tab:
                        cands.append((tab, None, None))
                cands.append((sel["id"], None, None))
            parent = project.parent_of(sel["id"])
            if parent is not None and sel["type"] != "Window":
                lay = sel["layout"]
                if model.layout_mode(parent) == "grid":
                    cands.append((parent["id"], lay["row"] + lay["rowspan"] - 1, lay["col"]))
                else:
                    cands.append((parent["id"], None, None))
            for a in reversed(project.ancestors(sel["id"])):
                cands.append((a["id"], None, None))
        cands.append((self.current_window_id, None, None))
        for cid, row, col in cands:
            c = project.find(cid)
            if c is not None and project.can_contain(c, wtype):
                return cid, row, col
        return None, None, None

    # ================================================================ refresh
    def refresh(self):
        if self._drag or self._span:
            self._pending_refresh = True
            return
        self._pending_refresh = False
        project = self.app.project
        win = project.find(self.current_window_id)
        if win is None or win["type"] != "Window":
            win = project.main_window
            self.current_window_id = win["id"]
        self.styler = preview.get_styler(self, project.settings)
        if self._editor is not None:
            self._close_editor(commit=False)
        if (self._outer is None or self._win_rendered != win["id"]
                or (self._style_sig and self._style_sig[-1] != self.styler.signature[-1])):
            self._full_build(win)
        else:
            if self._style_sig != self.styler.signature:
                self._restyle_all(win)
            try:
                self._reconcile(win)
            except (tk.TclError, KeyError) as e:      # fall back to a clean rebuild
                sys.stderr.write("workplane: rebuilding after %r\n" % (e,))
                self._full_build(win)
        self._geo_cache = None
        self._refresh_winbar()
        self._layout_canvas(keep_x=True)
        self.show_selection(self.app.selected_id)
        if self._created_last:
            self.after_idle(self._flash_created)
        if getattr(self, "_fit_job", None) is None:
            self._fit_job = self.after_idle(self._check_fit)

    # ------------------------------------------------- does the content fit?
    def content_need(self):
        """(width, height) in design pixels that the window's content needs,
        estimated like Tk's grid does: per column/row the largest requested
        size of the widgets in it (spanning widgets widen their last column)."""
        c = self._content
        if c is None:
            return None

        def pad(v):
            parts = [int(float(x)) for x in str(v).split()] or [0]
            return parts[0] + parts[-1]

        cols, rows, spans = {}, {}, []
        for w in c.grid_slaves():
            try:
                info = w.grid_info()
                r, col = int(info["row"]), int(info["column"])
                rs, cs = int(info["rowspan"]), int(info["columnspan"])
                need_w = w.winfo_reqwidth() + pad(info["padx"]) + 2 * int(info["ipadx"])
                need_h = w.winfo_reqheight() + pad(info["pady"]) + 2 * int(info["ipady"])
            except (tk.TclError, KeyError, ValueError):
                continue
            if cs == 1:
                cols[col] = max(cols.get(col, 0), need_w)
            else:
                spans.append(("c", col, cs, need_w))
            if rs == 1:
                rows[r] = max(rows.get(r, 0), need_h)
            else:
                spans.append(("r", r, rs, need_h))
        for axis, start, n, need in spans:
            sizes = cols if axis == "c" else rows
            have = sum(sizes.get(i, 0) for i in range(start, start + n))
            if need > have:
                last = start + n - 1
                sizes[last] = sizes.get(last, 0) + need - have
        try:
            frame_pad_x = 2 * int(float(c.cget("padx")))
            frame_pad_y = 2 * int(float(c.cget("pady")))
        except (tk.TclError, ValueError):
            frame_pad_x = frame_pad_y = 0
        k = self.styler.scale or 1.0
        return ((sum(cols.values()) + frame_pad_x) / k, (sum(rows.values()) + frame_pad_y) / k)

    def _check_fit(self):
        self._fit_job = None
        win = self.app.project.find(self.current_window_id)
        if win is None or self._content is None:
            return
        need = self.content_need()
        text = ""
        if need is not None:
            w, h = int(win["props"]["width"]), int(win["props"]["height"])
            nw, nh = need
            if nw > w + 1 or nh > h + 1:
                text = "⚠ Some content is cut off – click here to fit the window"
        try:
            if self.canvas.itemcget("fittext", "text") != text:
                self.canvas.itemconfigure("fittext", text=text)
        except tk.TclError:
            pass

    def fit_window(self):
        """Grow the window so all of its content fits (rounded up to 10 px)."""
        win = self.app.project.find(self.current_window_id)
        need = self.content_need()
        if win is None or need is None:
            return
        p = win["props"]
        w = max(int(p["width"]), int(-(-need[0] // 10) * 10 + 10))
        h = max(int(p["height"]), int(-(-need[1] // 10) * 10 + 10))
        if (w, h) == (p["width"], p["height"]):
            return
        p["width"], p["height"] = w, h
        self.app.commit("Window size is now %d × %d pixels, so everything fits." % (w, h))

    def _flash_created(self):
        ids, self._created_last = self._created_last, []
        if ids and not self._drag and not self._span:
            self._flash_nodes(ids)

    def _full_build(self, win):
        xv, yv = self.canvas.xview()[0], self.canvas.yview()[0]
        old = self._outer
        self._node_of = {}
        self._rec = {}
        self._hints = {}
        self._chrome = {}
        self._groups = {}
        self._win_sig = None
        outer = self._build_window_frame(win)
        if self._win_item is None:
            self._win_item = self.canvas.create_window(0, 0, window=outer, anchor="nw")
        else:
            self.canvas.itemconfigure(self._win_item, window=outer)
        if old is not None:
            old.destroy()
        self._outer = outer
        self._win_rendered = win["id"]
        self._style_sig = self.styler.signature
        self.ghost.reset()
        self._reconcile(win, initial=True)
        self._layout_canvas()
        self.canvas.xview_moveto(xv)
        self.canvas.yview_moveto(yv)

    # ---------------------------------------------------------- the window
    def _chrome_colors(self):
        if self.styler is not None and self.styler.dark:
            return {"bar": "#202020", "fg": "#ffffff", "glyph": "#ffffff", "off": "#5c5c5c",
                    "border": "#3a3a3a", "menu": "#2b2b2b"}
        return {"bar": "#ffffff", "fg": "#1a1a1a", "glyph": "#1f1f1f", "off": "#c4c4c4",
                "border": "#9aa1ab", "menu": "#ffffff"}

    def _build_window_frame(self, win):
        cc = self._chrome_colors()
        wid = win["id"]
        outer = tk.Frame(self.canvas, bg=cc["border"], padx=1, pady=1, highlightthickness=0)
        self._frame = outer
        self._reg(outer, wid)
        self._chrome[str(outer)] = "frame"
        title_h = _px(31)
        bar = tk.Frame(outer, bg=cc["bar"], height=title_h)
        bar.pack(side="top", fill="x")
        bar.pack_propagate(False)
        self._reg(bar, wid)
        self._chrome[str(bar)] = "title"
        icon = tk.Canvas(bar, width=_px(16), height=_px(16), bg=cc["bar"], highlightthickness=0)
        icon.pack(side="left", padx=(_px(9), _px(6)))
        self._reg(icon, wid)
        self._chrome[str(icon)] = "title"
        self._icon = icon
        self._title_label = tk.Label(bar, text="", bg=cc["bar"], fg=cc["fg"],
                                     font=(self.styler.family, 9), anchor="w")
        self._title_label.pack(side="left", fill="x", expand=True)
        self._reg(self._title_label, wid)
        self._chrome[str(self._title_label)] = "title"
        fams = set(tkfont.families(self))
        if "Segoe Fluent Icons" in fams or "Segoe MDL2 Assets" in fams:
            gfont = ("Segoe Fluent Icons" if "Segoe Fluent Icons" in fams
                     else "Segoe MDL2 Assets", 7)
            glyphs = ("", "", "")
        else:
            gfont, glyphs = _ui_font(9), ("─", "□", "✕")
        self._glyphs = []
        for g in reversed(glyphs):
            b = tk.Label(bar, text=g, bg=cc["bar"], fg=cc["glyph"], font=gfont, width=6)
            b.pack(side="right", fill="y")
            self._reg(b, wid)
            self._chrome[str(b)] = "title"
            self._glyphs.insert(0, b)
        self._bar = bar
        self._menubar = tk.Frame(outer, bg=cc["menu"], height=_px(20))
        self._menubar.pack_propagate(False)
        self._reg(self._menubar, wid)
        self._chrome[str(self._menubar)] = "menu"
        self._menu_sig = None

        content = tk.Frame(outer, highlightthickness=0, bd=0)
        content.pack(side="top")
        content.pack_propagate(False)
        content.grid_propagate(False)
        self._reg(content, wid)
        self._content = content

        # overlays (created last so they are stacked on top)
        self._hover_box = _Box(outer, _mix(self, theme.SELECT, "#ffffff", 0.45))
        self._sel_box = _Box(outer, theme.SELECT)
        for box in (self._hover_box, self._sel_box):
            for part in box.parts:
                self._reg(part, None)
        self._badge = tk.Label(outer, bg=theme.SELECT, fg="#ffffff", font=_ui_font(8),
                               bd=0, padx=2, pady=0)
        self._reg(self._badge, None)
        self._handles = {}
        for axis, cursor in (("col", "sb_h_double_arrow"), ("row", "sb_v_double_arrow")):
            h = tk.Frame(outer, bg=theme.SELECT, width=_px(8), height=_px(8),
                         highlightthickness=1, highlightbackground="#ffffff", cursor=cursor)
            h.bindtags((self._handletag, "all"))
            h._tkb_axis = axis
            self._handles[axis] = h
        self._overlays = {str(x) for box in (self._hover_box, self._sel_box)
                          for x in box.parts} | {str(self._badge)} | {
            str(h) for h in self._handles.values()}
        self._update_window(win, force=True)
        return outer

    def _update_window(self, win, force=False):
        """Bring the fake window (chrome + content frame) up to date."""
        p = win["props"]
        sig = json.dumps([p.get("title"), p.get("width"), p.get("height"), p.get("bg"),
                          p.get("padding"), p.get("resizable", True), p.get("icon"),
                          [m.get("label") for m in (p.get("menu") or []) if isinstance(m, dict)],
                          self.styler.signature])
        if sig == self._win_sig and not force:
            return False
        self._win_sig = sig
        cc = self._chrome_colors()
        self._frame.configure(bg=cc["border"])
        for w in (self._bar, self._icon, self._title_label):
            w.configure(bg=cc["bar"])
        self._title_label.configure(text=p.get("title", ""), fg=cc["fg"],
                                    font=(self.styler.family, 9))
        for i, g in enumerate(self._glyphs):
            off = i == 1 and not p.get("resizable", True)
            g.configure(bg=cc["bar"], fg=cc["off"] if off else cc["glyph"])
        self._draw_icon(p.get("icon"))
        menus = [m for m in (p.get("menu") or []) if isinstance(m, dict)]
        msig = (tuple(m.get("label", "") for m in menus), cc["menu"])
        if msig != self._menu_sig:
            self._menu_sig = msig
            for ch in self._menubar.winfo_children():
                self._node_of.pop(str(ch), None)
                self._chrome.pop(str(ch), None)
                ch.destroy()
            self._menubar.configure(bg=cc["menu"])
            if menus:
                for m in menus:
                    lab = tk.Label(self._menubar, text=m.get("label", "") or "?", bg=cc["menu"],
                                   fg=cc["fg"], font=(self.styler.family, 9), padx=_px(6))
                    lab.pack(side="left", fill="y")
                    self._reg(lab, win["id"])
                    self._chrome[str(lab)] = "menu"
                self._menubar.pack(side="top", fill="x", before=self._content)
                self._menu_h = _px(20)
            else:
                self._menubar.pack_forget()
                self._menu_h = 0
        px = self.styler.px
        W, H = px(int(p.get("width") or 420)), px(int(p.get("height") or 320))
        pad = px(int(p.get("padding") or 0))
        bg = p.get("bg") or self.styler.window_bg()
        try:
            self._content.configure(width=W, height=H, bg=bg, padx=pad, pady=pad)
        except tk.TclError:
            self._content.configure(width=W, height=H, bg=self.styler.window_bg(), padx=pad,
                                    pady=pad)
        self._win_size = (W + 2, H + _px(31) + self._menu_h + 2)
        return True

    def _draw_icon(self, path):
        c = self._icon
        c.delete("all")
        s = _px(16)
        img = None
        if path and not path.lower().endswith(".ico"):
            img = self._images.get(path, max_size=s)
        if img is not None:
            c.create_image(s // 2, s // 2, image=img)
            c._img = img
            return
        f = s / 16.0
        c.create_polygon(3 * f, 14 * f, 6 * f, 6 * f, 13 * f, 2 * f, 11 * f, 9 * f, 4 * f, 14 * f,
                         fill="#3b82f6", outline="#1d4ed8")
        c.create_line(3 * f, 15 * f, 10 * f, 6 * f, fill="#1e3a8a")

    # ---------------------------------------------------------- reconcile
    @staticmethod
    def _sig(node):
        return json.dumps(node.get("props"), sort_keys=True)

    def _widget(self, node_id):
        """The widget that shows a node (content frame for the window)."""
        if node_id == self._win_rendered:
            return self._content
        rec = self._rec.get(node_id)
        return rec.r.outer if rec is not None else None

    def _inner(self, node_id):
        if node_id == self._win_rendered:
            return self._content
        rec = self._rec.get(node_id)
        return rec.r.inner if rec is not None else None

    def _reconcile(self, win, initial=False):
        """Make the rendered widgets match the project, creating/destroying as
        little as possible."""
        project = self.app.project
        self._update_window(win)
        order = []

        def walk(n, parent):
            order.append((n, parent))
            for ch in n.get("children", []):
                walk(ch, n)
        for ch in win.get("children", []):
            walk(ch, win)
        nodes = {n["id"]: n for n, _p in order}

        # 1. forget widgets of nodes that are gone (or changed type)
        for nid in list(self._rec):
            n = nodes.get(nid)
            if n is None or n["type"] != self._rec[nid].r.type:
                self._destroy_rec(nid)

        # 2. create / update / place
        created, placed, moved = [], False, set()
        for n, parent in order:
            nid = n["id"]
            rec = self._rec.get(nid)
            sig = self._sig(n)
            if rec is not None and rec.sig != sig:
                if model.is_container(n):
                    self._update_container(rec, n, parent)
                    rec.sig = sig
                else:
                    self._destroy_rec(nid)
                    rec = None
            if rec is None:
                rec = self._create(n)
                created.append(nid)
            psig = self._place_sig(n, parent)
            if rec.place != psig:
                self._place(rec, n, parent)
                rec.place = psig
                placed = True
                moved.add(nid)

        # 3. containers: grid weights, empty hints, notebook pages
        for n in [win] + [n for n, _p in order if model.is_container(n)]:
            self._configure_container_grid(n)
        self._update_radio_groups(order)

        # 4. stacking: containers below their content
        if created or placed:
            raised = set()
            for n, parent in order:
                if n["id"] in moved or parent["id"] in raised:
                    _raise(self._rec[n["id"]].r.outer)
                    raised.add(n["id"])
                    hint = self._hints.get(n["id"])
                    if hint is not None:
                        _raise(hint)
            self._lift_overlays()
            self._sel_restack += 1
        # remember what appeared (for the brief highlight), but not when the
        # whole window was just built
        if initial:
            self._created_last = []
        else:
            tops = [nid for nid in created
                    if project.parent_of(nid) is None or project.parent_of(nid)["id"] not in created]
            self._created_last = tops[:3]

    def _place_sig(self, n, parent):
        if parent["type"] == "Notebook":
            return ("tab", parent["id"])
        return ("grid", parent["id"], json.dumps(n.get("layout"), sort_keys=True))

    def _create(self, n):
        """Build the widget(s) of one node as children of the content frame."""
        try:
            r = preview.build(self._content, n, self.styler, self._groups, self._images)
        except tk.TclError as e:      # a bad property value must not kill the editor
            r = preview.Rendered(n["type"])
            r.outer = r.main = tk.Label(self._content, text="[%s: %s]" % (n["id"], e),
                                        fg=theme.DANGER, bg=self.styler.window_bg())
            if model.is_container(n):
                r.inner = r.outer
        rec = _Rec(n["id"], r, self._sig(n))
        for w in preview.all_widgets(r.outer):
            self._reg(w, n["id"])
            rec.paths.append(str(w))
        self._rec[n["id"]] = rec
        return rec

    def _destroy_rec(self, nid):
        rec = self._rec.pop(nid, None)
        if rec is None:
            return
        for p in rec.paths:
            self._node_of.pop(p, None)
        hint = self._hints.pop(nid, None)
        if hint is not None:
            self._node_of.pop(str(hint), None)
            try:
                hint.destroy()
            except tk.TclError:
                pass
        try:
            rec.r.outer.destroy()
        except tk.TclError:
            pass

    def _place(self, rec, n, parent):
        w = rec.r.outer
        old_parent = rec.parent
        if old_parent is not None and old_parent != parent["id"]:
            old = self._rec.get(old_parent)
            if old is not None and old.r.type == "Notebook":
                try:
                    old.r.main.forget(w)
                except tk.TclError:
                    pass
        if parent["type"] == "Notebook":
            nb = self._rec[parent["id"]].r.main
            try:
                if w.winfo_manager() == "grid":
                    w.grid_forget()
            except tk.TclError:
                pass
            if str(w) not in [str(x) for x in nb.tabs()]:
                nb.add(w, text=n["props"].get("text", ""))
        else:
            lay = n["layout"]
            px = self.styler.px
            w.grid(in_=self._inner(parent["id"]), row=lay.get("row", 0), column=lay.get("col", 0),
                   rowspan=max(1, lay.get("rowspan", 1)),
                   columnspan=max(1, lay.get("colspan", 1)),
                   sticky=model.sticky_for(lay), padx=px(lay.get("padx", 0)),
                   pady=px(lay.get("pady", 0)))
        rec.parent = parent["id"]

    _CONTAINER_KEYS = ("padding", "relief", "borderwidth", "text", "labelanchor", "width",
                       "height", "style")

    def _update_container(self, rec, n, parent):
        w = rec.r.main
        t = n["type"]
        opts = preview.options(n, self.styler)
        if t == "Notebook":
            opts["style"] = self.styler.name("TNotebook")
        else:
            opts["style"] = self.styler.node_style(n)
        supported = set(w.keys())
        for k in self._CONTAINER_KEYS:
            if k not in opts and k in supported:
                opts[k] = w.configure(k)[3]
        w.configure(**{k: v for k, v in opts.items() if k in supported})
        if t == "Tab" and parent["type"] == "Notebook":
            nb = self._rec.get(parent["id"])
            if nb is not None:
                try:
                    nb.r.main.tab(w, text=n["props"].get("text", ""))
                except tk.TclError:
                    pass

    def _configure_container_grid(self, n):
        nid = n["id"]
        if n["type"] == "Notebook":
            rec = self._rec.get(nid)
            if rec is None:
                return
            nb = rec.r.main
            pages = [self._rec[t["id"]].r.outer for t in n.get("children", [])
                     if t["id"] in self._rec]
            try:
                cur = [str(x) for x in nb.tabs()]
                if cur != [str(p) for p in pages]:
                    for i, pg in enumerate(pages):
                        nb.insert(i, pg)
                want = self._tab_of.get(nid)
                page = self._rec[want].r.outer if want in self._rec and want in [
                    t["id"] for t in n["children"]] else None
                if page is not None and str(nb.select()) != str(page):
                    nb.select(page)
            except tk.TclError:
                pass
            return
        frame = self._inner(nid)
        if frame is None:
            return
        kids = n.get("children", [])
        # weights (reset the old ones first)
        try:
            cols, rows = frame.grid_size()
            want_r, want_c = model.grid_weights(n)
            for i in range(max(rows, 1)):
                frame.rowconfigure(i, weight=want_r.get(i, 0))
            for i in range(max(cols, 1)):
                frame.columnconfigure(i, weight=want_c.get(i, 0))
        except tk.TclError:
            pass
        hint = self._hints.get(nid)
        if kids:
            if hint is not None:
                self._node_of.pop(str(hint), None)
                del self._hints[nid]
                hint.destroy()
        else:
            if hint is None:
                hint = self._make_hint(n, frame)
            self._update_hint(n, hint, frame)

    def _container_bg(self, n, frame):
        if n["type"] == "Window":
            try:
                return frame.cget("bg")
            except tk.TclError:
                return self.styler.window_bg()
        try:
            st = frame.cget("style") or ("TLabelframe" if n["type"] == "LabelFrame" else "TFrame")
            return ttk.Style(self).lookup(st, "background") or self.styler.window_bg()
        except tk.TclError:
            return self.styler.window_bg()

    def _make_hint(self, node, frame):
        """The always-visible 'Drop widgets here' box of an empty container."""
        big = node["type"] in ("Window", "Tab")
        c = tk.Canvas(self._content, highlightthickness=0, bd=0,
                      width=_px(150), height=_px(46) if not big else _px(60))
        c._tkb_big = big
        self._reg(c, node["id"])
        self._hints[node["id"]] = c
        c.bind("<Configure>", lambda e, n=node["id"]: self._draw_hint(n))
        return c

    def _update_hint(self, node, c, frame):
        big = node["type"] in ("Window", "Tab")
        try:
            c.configure(bg=self._container_bg(node, frame))
            if big:
                frame.rowconfigure(0, weight=1)
                frame.columnconfigure(0, weight=1)
                c.grid(in_=frame, row=0, column=0, sticky="nsew", padx=_px(6), pady=_px(6))
            else:
                c.grid(in_=frame, row=0, column=0, sticky="nsew", padx=_px(2), pady=_px(2))
            _raise(c)
        except tk.TclError:
            pass
        self._draw_hint(node["id"])

    def _draw_hint(self, nid):
        c = self._hints.get(nid)
        if c is None:
            return
        try:
            c.delete("all")
            w, h = c.winfo_width(), c.winfo_height()
            if w < 4 or h < 4:
                w, h = int(c.cget("width")), int(c.cget("height"))
            bg = c.cget("bg")
        except tk.TclError:
            return
        dark = self.styler.dark if self.styler else False
        line = _mix(c, bg, "#ffffff" if dark else "#000000", 0.3)
        fg = _mix(c, bg, "#ffffff" if dark else "#000000", 0.45)
        c.create_rectangle(1, 1, w - 2, h - 2, outline=line, dash=(4, 3), width=1)
        def fits(font, text, room):
            try:
                return int(c.tk.call("font", "measure", font, text)) < room
            except tk.TclError:
                return True
        sub = "Drag a widget from the list on the left onto this area"
        if c._tkb_big and h > _px(70) and fits(_ui_font(9), sub, w - 16):
            c.create_text(w / 2, h / 2 - _px(10), text="Drop widgets here", fill=fg,
                          font=_ui_font(12, "bold"))
            c.create_text(w / 2, h / 2 + _px(14), fill=fg, font=_ui_font(9), text=sub)
        elif c._tkb_big and h > _px(40) and fits(_ui_font(12, "bold"), "Drop widgets here", w - 16):
            c.create_text(w / 2, h / 2, text="Drop widgets here", fill=fg,
                          font=_ui_font(12, "bold"))
        elif fits(_ui_font(9), "Drop widgets here", w - 8):
            c.create_text(w / 2, h / 2, text="Drop widgets here", fill=fg, font=_ui_font(9))
        else:
            c.create_text(w / 2, h / 2, text="+", fill=fg, font=_ui_font(12))

    def _update_radio_groups(self, order):
        values = {}
        for n, _p in order:
            if n["type"] == "Radiobutton":
                g = n["props"].get("group") or ""
                values.setdefault(g, "")
                if n["props"].get("selected"):
                    values[g] = tkmap.radio_value(n)
        for g, v in values.items():
            var = self._groups.get(g)
            if var is not None and var.get() != v:
                var.set(v)

    def _restyle_all(self, win):
        """Theme / font changed: re-apply the look without rebuilding."""
        project = self.app.project
        self._win_sig = None
        self._update_window(win)
        for nid, rec in list(self._rec.items()):
            n = project.find(nid)
            if n is None:
                continue
            try:
                preview.restyle(rec.r, n, self.styler)
            except tk.TclError:
                rec.sig = None        # rebuilt by the reconcile that follows
        for nid in list(self._hints):
            n = project.find(nid)
            frame = self._inner(nid)
            if n is not None and frame is not None:
                self._update_hint(n, self._hints[nid], frame)
        self._style_sig = self.styler.signature
        self.ghost.reset()

    def _lift_overlays(self):
        for box in (getattr(self, "_hover_box", None), getattr(self, "_sel_box", None)):
            if box is not None:
                box.lift()

    # ---------------------------------------------------------- registry
    def _reg(self, w, node_id, own=False):
        """Map a tk widget to a node and make it inert (our tag only)."""
        if node_id is not None:
            self._node_of[str(w)] = node_id
        tags = (self._tag, str(w.winfo_toplevel()), "all")
        if own:
            tags = (str(w),) + tags
        w.bindtags(tags)
        try:
            w.configure(takefocus=0)
        except tk.TclError:
            pass
        try:
            w.configure(cursor="arrow")
        except tk.TclError:
            pass

    # ---------------------------------------------------------- canvas layout
    def _layout_canvas(self, keep_x=False):
        c = self.canvas
        ow, oh = self._win_size
        cw = max(c.winfo_width(), 1)
        margin = _px(36)
        x0 = max(margin, (cw - ow) // 2)
        if keep_x and self._win_xy[0] >= margin and self._resize:
            x0 = self._win_xy[0]
        y0 = margin
        if (x0, y0) != self._win_xy:
            self._geo_cache = None
        self._win_xy = (x0, y0)
        if self._win_item is not None:
            c.coords(self._win_item, x0, y0)
        x1, y1 = x0 + ow, y0 + oh
        c.coords("shadow2", x0 + 2, y0 + 4, x1 + 4, y1 + 5)
        c.coords("shadow1", x0 + 1, y0 + 2, x1 + 2, y1 + 2)
        c.coords("griparea", x1 - 4, y1 - 4, x1 + 14, y1 + 14)
        for i, d in enumerate((4, 8, 12)):
            c.coords("gripline%d" % i, x1 + d, y1 + 2, x1 + 2, y1 + d)
        win = self.app.project.find(self.current_window_id)
        if win is not None:
            p = win["props"]
            w, h = (self._resize["w"], self._resize["h"]) if self._resize else (
                p.get("width"), p.get("height"))
            c.itemconfigure("sizetext", text="%s × %s pixels   (drag the corner to resize)" % (w, h))
        c.coords("sizetext", x1 - 2, y1 + _px(16))
        c.coords("fittext", x1 - 2, y1 + _px(36))
        c.tag_raise("grip")
        c.configure(scrollregion=(0, 0, max(cw, x1 + margin), y1 + margin + _px(48)))

    # ---------------------------------------------------------- resize grip
    def _grip_press(self, event):
        win = self.app.project.find(self.current_window_id)
        if win is None:
            return
        self._resize = {"x": event.x_root, "y": event.y_root,
                        "w0": int(win["props"]["width"]), "h0": int(win["props"]["height"]),
                        "w": int(win["props"]["width"]), "h": int(win["props"]["height"])}
        self.app.status("Drag to change the size of the window. Release to keep it.")

    def _grip_motion(self, event):
        r = self._resize
        if not r:
            return
        k = self.styler.scale or 1.0
        w = r["w0"] + (event.x_root - r["x"]) / k
        h = r["h0"] + (event.y_root - r["y"]) / k
        w = max(100, min(4000, int(round(w / SNAP)) * SNAP))
        h = max(80, min(4000, int(round(h / SNAP)) * SNAP))
        if (w, h) == (r["w"], r["h"]):
            return
        r["w"], r["h"] = w, h
        if self._content is not None:
            self._content.configure(width=self.styler.px(w), height=self.styler.px(h))
        self._win_size = (self.styler.px(w) + 2, self.styler.px(h) + _px(31) + self._menu_h + 2)
        self._layout_canvas(keep_x=True)

    def _grip_release(self, event):
        r, self._resize = self._resize, None
        self.canvas.configure(cursor="")
        if not r:
            return
        win = self.app.project.find(self.current_window_id)
        if win is None or (r["w"], r["h"]) == (r["w0"], r["h0"]):
            self._win_sig = None
            self.refresh()
            return
        win["props"]["width"], win["props"]["height"] = r["w"], r["h"]
        self.app.commit("Window size is now %d × %d pixels." % (r["w"], r["h"]))

    # ================================================================ events
    def _node_for_widget(self, w):
        return self._node_of.get(str(w))

    def _on_canvas_press(self, event):
        self._close_editor(commit=True)
        self.canvas.focus_set()
        if self.canvas.find_withtag("current") and "grip" in self.canvas.gettags("current"):
            return
        self.app.select(None)

    def _tab_at(self, nb, x, y):
        """Index of the notebook tab label at widget coords x, y (or None)."""
        try:
            s = nb.tk.call(str(nb), "index", "@%d,%d" % (x, y))
        except tk.TclError:
            return None
        if s == "" or s is None:
            return None
        try:
            return int(s)
        except (TypeError, ValueError):
            return None

    def _resolve_click(self, event):
        """Node id for a click; clicking a notebook tab label switches tab and
        returns the Tab's id."""
        w = event.widget
        nid = self._node_for_widget(w)
        if nid is None:
            return None
        if isinstance(w, ttk.Notebook):
            idx = self._tab_at(w, event.x, event.y)
            nb_node = self.app.project.find(nid)
            if idx is not None and nb_node and idx < len(nb_node.get("children", [])):
                tab = nb_node["children"][idx]
                self._switch_tab(nid, tab["id"])
                return tab["id"]
        return nid

    def _switch_tab(self, nb_id, tab_id):
        self._tab_of[nb_id] = tab_id
        nb = self._rec.get(nb_id)
        page = self._rec.get(tab_id)
        if nb is not None and page is not None:
            try:
                nb.r.main.select(page.r.outer)
            except tk.TclError:
                pass
        self._geo_cache = None

    def _reveal(self, node_id):
        """Switch every notebook on the way so node_id is visible."""
        project = self.app.project
        chain = project.ancestors(node_id) + [project.find(node_id)]
        for n in chain:
            if n is not None and n["type"] == "Tab":
                nb = project.parent_of(n["id"])
                if nb is not None and self.current_tab_of(nb["id"]) != n["id"]:
                    self._switch_tab(nb["id"], n["id"])

    def _on_press(self, event):
        self._hover_box.hide()
        if self._drag:
            return "break"
        nid = self._resolve_click(event)
        if self._editor is not None:
            self._close_editor(commit=True)
        self.canvas.focus_set()
        if nid is None:
            return "break"
        self._press = {"id": nid, "x": event.x_root, "y": event.y_root,
                       "chrome": str(event.widget) in self._chrome, "widget": event.widget}
        if self.app.project.find(nid) is not None:
            self.app.select(nid, source=self)
            self.show_selection(nid)
            if self._chrome.get(str(event.widget)) == "menu":
                self.app.status("This is the menu bar. Change it in the properties panel "
                                "(Menu bar → Edit menu bar…).")
        return "break"

    def _on_b1_motion(self, event):
        if self._drag:
            self._drag_motion(event.x_root, event.y_root)
            return "break"
        p = self._press
        if p and not p["chrome"]:
            if (abs(event.x_root - p["x"]) > DRAG_THRESHOLD
                    or abs(event.y_root - p["y"]) > DRAG_THRESHOLD):
                node = self.app.project.find(p["id"])
                self._press = None
                if node is not None and node["type"] != "Window":
                    self._start_drag("move", node["type"], node["id"], event.x_root,
                                     event.y_root)
        return "break"

    def _on_release(self, event):
        if self._drag:
            self._drop()
        self._press = None
        return "break"

    def _on_double(self, event):
        nid = self._resolve_click(event)
        if nid is None:
            return "break"
        self._press = None
        if self._chrome.get(str(event.widget)) == "title":
            self.app.select(nid, source=self)
            self.show_selection(nid)
            self._edit_title(nid)
            return "break"
        node = self.app.project.find(nid)
        if node is not None and node["type"] in TEXT_EDITABLE:
            self.app.select(nid, source=self)
            self.show_selection(nid)
            self.edit_text(nid)
        return "break"

    def _on_right(self, event):
        if self._drag:
            return "break"
        nid = self._resolve_click(event)
        self._close_editor(commit=True)
        if nid is None or self.app.project.find(nid) is None:
            return "break"
        self.app.select(nid, source=self)
        self.show_selection(nid)
        self._context_menu(nid, event.x_root, event.y_root)
        return "break"

    def _on_hover(self, event):
        if self._drag or self._span or self._outer is None:
            return "break"
        nid = self._node_for_widget(event.widget)
        node = self.app.project.find(nid) if nid else None
        if (node is None or node["type"] in ("Window", "Tab") or nid == self._sel_id
                or str(event.widget) in self._chrome):
            self._hover_box.hide()
            return "break"
        w = self._widget(nid)
        if w is not None and self._hover_box.target is not w:
            self._hover_box.show(w, out=1, t=1)
        return "break"

    def _on_leave(self, event):
        if not self._drag and self._hover_box.target is not None:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
            if under is None or str(under) not in self._node_of:
                self._hover_box.hide()
        return "break"

    def _on_wheel(self, event):
        if self._editor is None:
            self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1),
                                     "units")
            self._geo_cache = None
        return "break"

    def _on_shift_wheel(self, event):
        self.canvas.xview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1),
                                 "units")
        self._geo_cache = None
        return "break"

    def _on_escape(self, event=None):
        if self._drag:
            self._cancel_drag()
            return "break"
        if self._span:
            self._span_cancel()
            return "break"
        self.app.select(None)
        return "break"

    # ================================================================ geometry
    def _geo(self):
        """Root rectangles of every visible design node (cached)."""
        c = self._content
        if c is None:
            return {}
        try:
            origin = (c.winfo_rootx(), c.winfo_rooty())
        except tk.TclError:
            return {}
        g = self._geo_cache
        if g is not None and g["origin"] == origin:
            return g["rects"]
        rects = {self._win_rendered: (origin[0], origin[1], origin[0] + c.winfo_width(),
                                      origin[1] + c.winfo_height())}
        for nid, rec in self._rec.items():
            w = rec.r.outer
            try:
                if not w.winfo_ismapped():
                    continue
                x, y = w.winfo_rootx(), w.winfo_rooty()
                rects[nid] = (x, y, x + w.winfo_width(), y + w.winfo_height())
            except tk.TclError:
                continue
        self._geo_cache = {"origin": origin, "rects": rects}
        return rects

    @staticmethod
    def _inside(rect, x, y):
        return rect is not None and rect[0] <= x < rect[2] and rect[1] <= y < rect[3]

    def _hit(self, x, y, skip=None):
        """Deepest node under root point (x, y), ignoring `skip` and its
        content.  Returns (node, on_strip) - on_strip: pointer is on a
        notebook outside its page."""
        project = self.app.project
        win = project.find(self._win_rendered)
        geo = self._geo()
        if win is None or not self._inside(geo.get(win["id"]), x, y):
            return None, False
        node = win
        while True:
            if node["type"] == "Notebook":
                tab_id = self.current_tab_of(node["id"])
                tab = project.find(tab_id) if tab_id else None
                if tab is not None and tab_id != skip and self._inside(geo.get(tab_id), x, y):
                    node = tab
                    continue
                return node, True
            found = None
            for ch in reversed(node.get("children", [])):
                if ch["id"] == skip:
                    continue
                if self._inside(geo.get(ch["id"]), x, y):
                    found = ch
                    break
            if found is None:
                return node, False
            node = found
            if not model.is_container(node):
                return node, False

    def _grid_lines(self, cid):
        """Column and row boundaries of a container's grid, in root coords:
        (xs, ys) lists of (start, size) per column/row, and the frame's
        root origin."""
        frame = self._inner(cid)
        node = self.app.project.find(cid)
        if frame is None or node is None:
            return None
        rows, cols = self.app.project.grid_size(node)
        try:
            fx, fy = frame.winfo_rootx(), frame.winfo_rooty()
            xs = [frame.grid_bbox(c, 0)[0::2] for c in range(cols)]
            ys = [frame.grid_bbox(0, r)[1::2] for r in range(rows)]
            fw, fh = frame.winfo_width(), frame.winfo_height()
        except (tk.TclError, TypeError):
            return None
        return {"fx": fx, "fy": fy, "fw": fw, "fh": fh, "rows": rows, "cols": cols,
                "xs": [(fx + a, b) for a, b in xs], "ys": [(fy + a, b) for a, b in ys]}

    @staticmethod
    def _cell_rect(g, r0, c0, r1=None, c1=None):
        r1 = r0 if r1 is None else r1
        c1 = c0 if c1 is None else c1
        x0 = g["xs"][c0][0]
        x1 = g["xs"][c1][0] + g["xs"][c1][1]
        y0 = g["ys"][r0][0]
        y1 = g["ys"][r1][0] + g["ys"][r1][1]
        return (x0, y0, x1, y1)

    # ================================================================ drag & drop
    def _start_drag(self, kind, wtype, node_id, x, y, src=None):
        self._close_editor(commit=True)
        self._stop_flash()
        self._hover_box.hide()
        self._sel_box.hide()
        self._badge.place_forget()
        self._hide_handles()
        self._sel_state = None
        self._drag = {"kind": kind, "wtype": wtype, "id": node_id, "src": src,
                      "target": None, "hover_tab": None, "after": None, "x": x, "y": y}
        self._geo_cache = None
        self._make_ghost(x, y)
        self.overlay.show()
        if node_id is not None:
            self.overlay.set_rect("dim", self._geo().get(node_id), animate=False)
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass
        what = ("a new " + WIDGETS[wtype]["name"]) if kind == "new" else node_id
        self.app.status("Dragging %s – drop it where you want it (Esc to cancel)." % what)
        self._drag_motion(x, y)

    def _make_ghost(self, x, y):
        d = self._drag
        spec = WIDGETS[d["wtype"]]
        project = self.app.project
        styler = self.styler
        if d["kind"] == "new":
            caption = "%s  %s" % (spec["icon"], spec["name"])
            key = (d["wtype"], styler.signature)

            def factory(parent):
                node = project.new_node(d["wtype"])
                if d["wtype"] in ("Text", "Console", "Table", "Listbox"):
                    node["props"]["height"] = min(int(node["props"].get("height") or 4), 4)
                if d["wtype"] == "Console":
                    node["props"]["width"] = 40
                holder = tk.Frame(parent, bg=styler.window_bg(), padx=6, pady=6)
                holder.pack()
                w = preview.build_tree(holder, node, styler, self._images, limit=12)
                w.pack()
            self.ghost.show(factory, key, caption, x, y)
        else:
            node = project.find(d["id"])
            caption = "%s  %s" % (spec["icon"], d["id"])
            w = self._widget(d["id"])
            try:
                self.update_idletasks()      # selection outline/badge gone first
            except tk.TclError:
                pass
            png = _capture_png(w) if w is not None else None
            if png is not None:
                def factory(parent, png=png):
                    img = tk.PhotoImage(master=parent, data=png)
                    big = max(img.width() / 360.0, img.height() / 260.0)
                    if big > 1:
                        f = int(big + 0.999)
                        img = img.subsample(f, f)
                    lab = tk.Label(parent, image=img, bd=0)
                    lab.image = img
                    lab.pack()
            else:
                def factory(parent):
                    holder = tk.Frame(parent, bg=styler.window_bg(), padx=4, pady=4)
                    holder.pack()
                    preview.build_tree(holder, node, styler, self._images, limit=12).pack()
            self.ghost.show(factory, None, caption, x, y)

    def _src_motion(self, event):
        if self._drag:
            self._drag_motion(event.x_root, event.y_root)
        return "break"

    def _src_release(self, event):
        if self._drag:
            self._drop()
        self._remove_srctag(event.widget)
        return "break"

    def _remove_srctag(self, w):
        try:
            tags = tuple(t for t in w.bindtags() if t != self._srctag)
            w.bindtags(tags)
        except (tk.TclError, AttributeError):
            pass

    def _drag_motion(self, x, y):
        d = self._drag
        if d is None:
            return
        d["x"], d["y"] = x, y
        self.ghost.move(x, y)
        self._autoscroll_check()
        target, reason = self._find_target(x, y)
        self._show_target(target, reason)

    # ---------------------------------------------------------- auto-scroll
    def _autoscroll_check(self):
        if self._autoscroll_id is None and self._drag is not None and self._scroll_dir():
            self._autoscroll_id = self.after(40, self._autoscroll_tick)

    def _scroll_dir(self):
        d = self._drag or self._span
        if not d:
            return None
        c = self.canvas
        x, y = d["x"], d["y"]
        try:
            cx, cy = c.winfo_rootx(), c.winfo_rooty()
            cw, ch = c.winfo_width(), c.winfo_height()
        except tk.TclError:
            return None
        zone = _px(24)
        if not (cx - zone <= x <= cx + cw + zone and cy - zone <= y <= cy + ch + zone):
            return None
        dx = -1 if x < cx + zone else (1 if x > cx + cw - zone else 0)
        dy = -1 if y < cy + zone else (1 if y > cy + ch - zone else 0)
        first, last = c.yview()
        if (dy < 0 and first <= 0) or (dy > 0 and last >= 1):
            dy = 0
        first, last = c.xview()
        if (dx < 0 and first <= 0) or (dx > 0 and last >= 1):
            dx = 0
        return (dx, dy) if (dx or dy) else None

    def _autoscroll_tick(self):
        self._autoscroll_id = None
        dirs = self._scroll_dir()
        if not dirs:
            return
        dx, dy = dirs
        if dy:
            self.canvas.yview_scroll(dy, "units")
        if dx:
            self.canvas.xview_scroll(dx, "units")
        self._geo_cache = None
        d = self._drag
        if d is not None:
            if d.get("id"):
                self.overlay.set_rect("dim", self._geo().get(d["id"]), animate=False)
            target, reason = self._find_target(d["x"], d["y"])
            self._show_target(target, reason)
        self._autoscroll_id = self.after(40, self._autoscroll_tick)

    # ---------------------------------------------------------- targeting
    def _find_target(self, x, y):
        """Return (target, reason).  target is a dict:
             {"kind": "cell"|"side"|"tab", "cid", "row", "col", "mode",
              "ref": node id (side/tab), "rect": indicator rect (root),
              "shape": "bar"|"cell"|"strip", "label": caption or ""}
        or None with a reason string for the status bar."""
        d = self._drag
        project = self.app.project
        wtype, moving = d["wtype"], d["id"]
        node, on_strip = self._hit(x, y, skip=moving)
        if node is None:
            self._hover_tab(None, None)
            return None, ""
        if wtype == "Tab":
            self._hover_tab(None, None)
            return self._find_tab_target(node, x, y)

        if node["type"] == "Notebook":
            nbw = self._rec[node["id"]].r.main
            idx = self._tab_at(nbw, x - nbw.winfo_rootx(), y - nbw.winfo_rooty())
            if idx is not None:
                self._hover_tab(node["id"], idx)
                return None, "Keep the pointer on a tab to open it, then drop onto the page."
            self._hover_tab(None, None)
            return self._side_of_widget(node, x, y)
        self._hover_tab(None, None)

        t = node["type"]
        if model.is_container(node):
            geo = self._geo()
            edge_node = node
            if t == "Tab":          # near the outer border of a Tabs widget
                edge_node = project.parent_of(node["id"]) or node
            if edge_node["type"] in ("Frame", "LabelFrame", "Notebook"):
                side = self._edge_side(geo.get(edge_node["id"]), x, y)
                if side:
                    tgt = self._side_target(edge_node, side)
                    if tgt[0] is not None:
                        return tgt
            ok, reason = self._can_drop_into(node)
            if not ok:
                if t in ("Frame", "LabelFrame"):
                    tgt = self._side_of_widget(node, x, y)
                    if tgt[0] is not None:
                        return tgt
                return None, reason
            return self._cell_target(node, x, y)
        return self._side_of_widget(node, x, y)

    def _edge_side(self, rect, x, y):
        if rect is None:
            return None
        ww, wh = rect[2] - rect[0], rect[3] - rect[1]
        if ww < 5 * EDGE or wh < 5 * EDGE:
            return None
        dists = {"left": x - rect[0], "right": rect[2] - x, "above": y - rect[1],
                 "below": rect[3] - y}
        side = min(dists, key=dists.get)
        return side if dists[side] < EDGE else None

    def _side_of_widget(self, node, x, y):
        """Pointer over a normal widget: left/right 25 %, else above/below."""
        rect = self._geo().get(node["id"])
        if rect is None:
            return None, ""
        rx = (x - rect[0]) / max(1, rect[2] - rect[0])
        ry = (y - rect[1]) / max(1, rect[3] - rect[1])
        if rx < 0.25:
            side = "left"
        elif rx > 0.75:
            side = "right"
        else:
            side = "above" if ry < 0.5 else "below"
        return self._side_target(node, side)

    def _can_drop_into(self, container):
        project = self.app.project
        d = self._drag
        if d["id"] and (container["id"] == d["id"]
                        or project.is_descendant(container["id"], d["id"])):
            return False, "A widget can't go inside itself."
        if not project.can_contain(container, d["wtype"]):
            if d["wtype"] == "Tab":
                return False, "A Tab can only be dropped onto a Tabs widget."
            if container["type"] == "Notebook":
                return False, "Drop it onto the page of a tab, not onto the tab labels."
            return False, "A %s can't go there." % WIDGETS[d["wtype"]]["name"]
        return True, ""

    def _side_target(self, ref, side):
        """Insert next to widget `ref` (in its container).  When the
        neighbouring cell on that side is empty, the widget simply goes
        into that cell."""
        project = self.app.project
        d = self._drag
        parent = project.parent_of(ref["id"])
        if parent is None or parent["type"] == "Notebook":
            return None, ""
        ok, reason = self._can_drop_into(parent)
        if not ok:
            return None, reason
        g = self._grid_lines(parent["id"])
        lay = ref["layout"]
        r, c = lay["row"], lay["col"]
        rs, cs = max(1, lay["rowspan"]), max(1, lay["colspan"])
        if g is None or r + rs > g["rows"] or c + cs > g["cols"]:
            return None, ""
        occ = project.occupancy(parent, exclude_id=d["id"])
        nb = {"left": (r, c - 1), "right": (r, c + cs), "above": (r - 1, c),
              "below": (r + rs, c)}[side]
        if 0 <= nb[0] < g["rows"] and 0 <= nb[1] < g["cols"] and nb not in occ:
            return {"kind": "cell", "cid": parent["id"], "row": nb[0], "col": nb[1],
                    "mode": "cell", "rect": self._cell_rect(g, nb[0], nb[1]),
                    "shape": "cell", "label": "", "ref": None}, ""
        x0, y0, x1, y1 = self._cell_rect(g, r, c, r + rs - 1, c + cs - 1)
        h = _px(BAR) // 2
        if side == "left":
            rect = (x0 - h, y0, x0 + h, y1)
        elif side == "right":
            rect = (x1 - h, y0, x1 + h, y1)
        elif side == "above":
            rect = (x0, y0 - h, x1, y0 + h)
        else:
            rect = (x0, y1 - h, x1, y1 + h)
        row = r + rs - 1 if side == "below" else r
        col = c + cs - 1 if side == "right" else c
        return {"kind": "side", "cid": parent["id"], "row": row, "col": col, "mode": side,
                "ref": ref["id"], "rect": rect, "shape": "bar", "label": ""}, ""

    def _cell_target(self, cont, x, y):
        """Pointer over a container's own area: empty cell, next to a widget,
        or a new row / column just outside the grid."""
        project = self.app.project
        d = self._drag
        cid = cont["id"]
        kids = [k for k in cont.get("children", []) if k["id"] != d["id"]]
        geo = self._geo()
        if not kids:
            hint = self._hints.get(cid)
            rect = None
            if hint is not None and hint.winfo_ismapped():
                rect = (hint.winfo_rootx(), hint.winfo_rooty(),
                        hint.winfo_rootx() + hint.winfo_width(),
                        hint.winfo_rooty() + hint.winfo_height())
            if rect is None:
                fr = self._inner(cid)
                rect = (fr.winfo_rootx() + 2, fr.winfo_rooty() + 2,
                        fr.winfo_rootx() + fr.winfo_width() - 2,
                        fr.winfo_rooty() + fr.winfo_height() - 2) if fr is not None else geo.get(cid)
            return {"kind": "cell", "cid": cid, "row": 0, "col": 0, "mode": "cell",
                    "rect": rect, "shape": "cell", "label": "Drop here", "ref": None, "empty": True}, ""
        g = self._grid_lines(cid)
        if g is None or not g["cols"] or not g["rows"]:
            return None, ""

        def index(spans, v):
            if v < spans[0][0]:
                return -1
            for i, (a, b) in enumerate(spans):
                if v < a + b:
                    return i
            return len(spans)
        ci = index(g["xs"], x)
        ri = index(g["ys"], y)
        rows, cols = g["rows"], g["cols"]
        occ = project.occupancy(cont, exclude_id=d["id"])
        gx1 = g["xs"][-1][0] + g["xs"][-1][1]
        gy1 = g["ys"][-1][0] + g["ys"][-1][1]
        gx0, gy0 = g["xs"][0][0], g["ys"][0][0]
        if 0 <= ri < rows and 0 <= ci < cols:
            k = occ.get((ri, ci))
            if k is None:
                return {"kind": "cell", "cid": cid, "row": ri, "col": ci, "mode": "cell",
                        "rect": self._cell_rect(g, ri, ci), "shape": "cell", "label": "",
                        "ref": None}, ""
            kn = project.find(k)
            lay = kn["layout"]
            cr = self._cell_rect(g, lay["row"], lay["col"],
                                 min(rows, lay["row"] + max(1, lay["rowspan"])) - 1,
                                 min(cols, lay["col"] + max(1, lay["colspan"])) - 1)
            rx = (x - cr[0]) / max(1, cr[2] - cr[0])
            ry = (y - cr[1]) / max(1, cr[3] - cr[1])
            side = "left" if rx < 0.25 else "right" if rx > 0.75 else (
                "above" if ry < 0.5 else "below")
            return self._side_target(kn, side)
        if ri >= rows:          # below the grid: new row under the column
            col = max(0, min(ci, cols - 1))
            x0, _y, x1, _y1 = self._cell_rect(g, 0, col)
            top = gy1 + 2
            rect = (x0, top, x1, top + _px(STRIP))
            return {"kind": "cell", "cid": cid, "row": rows, "col": col, "mode": "cell",
                    "rect": rect, "shape": "strip", "label": "New row", "ref": None}, ""
        if ci >= cols:          # right of the grid: new column next to the row
            row = max(0, min(ri, rows - 1))
            _x, y0, _x1, y1 = self._cell_rect(g, row, 0)
            left = gx1 + 2
            rect = (left, y0, left + _px(max(STRIP * 3, 60)), y1)
            return {"kind": "cell", "cid": cid, "row": row, "col": cols, "mode": "cell",
                    "rect": rect, "shape": "strip", "label": "New column", "ref": None}, ""
        h = _px(BAR) // 2
        if ri < 0:              # above the grid: new first row
            col = max(0, min(ci, cols - 1))
            return {"kind": "side", "cid": cid, "row": 0, "col": col, "mode": "above",
                    "rect": (gx0, gy0 - h - 1, gx1, gy0 + h - 1), "shape": "bar",
                    "label": "", "ref": None}, ""
        row = max(0, min(ri, rows - 1))   # left of the grid: new first column
        return {"kind": "side", "cid": cid, "row": row, "col": 0, "mode": "left",
                "rect": (gx0 - h - 1, gy0, gx0 + h - 1, gy1), "shape": "bar",
                "label": "", "ref": None}, ""

    def _find_tab_target(self, node, x, y):
        """Targets for dragging a Tab: between the labels of a Tabs widget."""
        project = self.app.project
        d = self._drag
        nb = node if node["type"] == "Notebook" else None
        if nb is None:
            for a in reversed(project.ancestors(node["id"])):
                if a["type"] == "Notebook":
                    nb = a
                    break
        if nb is None:
            return None, "A Tab can only be dropped onto a Tabs widget."
        if d["id"]:
            if project.is_descendant(nb["id"], d["id"]):
                return None, "A widget can't go inside itself."
            old = project.parent_of(d["id"])
            if old is not None and old["id"] != nb["id"] and len(old["children"]) <= 1:
                return None, "Tabs need at least one tab, so this one can't be moved away."
        nbw = self._rec[nb["id"]].r.main
        spans, strip_h = self._tab_spans(nbw)
        kids = nb["children"]
        ref, side = (kids[-1]["id"] if kids else None), "after"
        try:
            lx = x - nbw.winfo_rootx()
        except tk.TclError:
            lx = -1
        for i, (a, b) in enumerate(spans):
            if lx < (a + b) / 2:
                ref, side = kids[i]["id"], "before"
                break
        if d["id"] and ref is not None:
            ids = [k["id"] for k in kids]
            if d["id"] in ids:
                i, j = ids.index(d["id"]), ids.index(ref)
                if ref == d["id"] or (side == "before" and j == i + 1) or (
                        side == "after" and j == i - 1):
                    return None, "That is where it already is."
        rect = None
        ids = [k["id"] for k in kids]
        if spans and ref in ids and ids.index(ref) < len(spans):
            i = ids.index(ref)
            xx = spans[i][0] if side == "before" else spans[i][1]
            x0, y0 = nbw.winfo_rootx() + xx, nbw.winfo_rooty()
            rect = (x0 - _px(BAR) // 2, y0, x0 + _px(BAR) // 2, y0 + max(strip_h, 16))
        return {"kind": "tab", "cid": nb["id"], "row": 0, "col": 0, "mode": "append",
                "ref": ref, "side": side, "rect": rect, "shape": "bar", "label": ""}, ""

    def _tab_spans(self, nb):
        """[(x0, x1) per tab label], strip height – measured by probing."""
        spans, strip_h = [], 0
        try:
            width = nb.winfo_width()
            for yy in (4, 8, 12, 16):
                cur, start = None, 0
                spans = []
                for xx in range(0, width + 2, 2):
                    i = self._tab_at(nb, xx, yy)
                    if i != cur:
                        if cur is not None:
                            spans.append((start, xx))
                        cur, start = i, xx
                if cur is not None:
                    spans.append((start, width))
                if spans:
                    break
            if spans:
                mid = (spans[0][0] + spans[0][1]) // 2
                for yy in range(0, 60):
                    if self._tab_at(nb, mid, yy) is not None:
                        strip_h = yy + 1
        except tk.TclError:
            spans = []
        return spans, strip_h

    def _hover_tab(self, nb_id, idx):
        d = self._drag
        cur = d.get("hover_tab")
        if cur == ((nb_id, idx) if nb_id else None):
            return
        if d.get("after"):
            self.after_cancel(d["after"])
            d["after"] = None
        d["hover_tab"] = (nb_id, idx) if nb_id else None
        if nb_id is None:
            return
        nb_node = self.app.project.find(nb_id)
        if nb_node is None or idx >= len(nb_node["children"]):
            return
        tab_id = nb_node["children"][idx]["id"]
        if self.current_tab_of(nb_id) == tab_id:
            return

        def switch():
            if not self._drag:
                return
            self._drag["after"] = None
            self._switch_tab(nb_id, tab_id)
            try:
                self.update_idletasks()
            except tk.TclError:
                pass
            self._geo_cache = None
            self._drag_motion(self._drag["x"], self._drag["y"])
        d["after"] = self.after(TAB_HOVER_MS, switch)

    # ---------------------------------------------------------- feedback
    def _show_target(self, target, reason):
        d = self._drag
        d["target"] = target
        ov = self.overlay
        if target is not None and target.get("rect") is not None:
            ov.style_ind(target["shape"])
            ov.set_rect("ind", target["rect"])
            ov.set_label(target.get("label"), target["rect"])
        else:
            ov.set_rect("ind", None)
            ov.set_label(None, None)
        # target container outline + its faint grid lines
        cid = target["cid"] if target else None
        cont = self.app.project.find(cid) if cid else None
        if cont is not None and cont["type"] != "Notebook":
            frame = self._inner(cid)
            try:
                fx, fy = frame.winfo_rootx(), frame.winfo_rooty()
                box = (fx, fy, fx + frame.winfo_width(), fy + frame.winfo_height())
            except (tk.TclError, AttributeError):
                box = None
            ov.set_rect("box", box)
            ov.set_grid(self._grid_line_rects(cid) if box else [])
        elif cont is not None:
            ov.set_rect("box", self._geo().get(cid))
            ov.set_grid([])
        else:
            ov.set_rect("box", None)
            ov.set_grid([])
        if ov.ok:
            ov.c.tag_raise("ind")
            ov.c.tag_raise("lab")
        self.ghost.set_valid(target is not None)
        if target is None:
            self.app.status(reason or "Move over your window to choose a place. "
                                      "Release outside to cancel.")
        else:
            self.app.status(self._describe(target))

    def _grid_line_rects(self, cid):
        g = self._grid_lines(cid)
        if g is None or not g["cols"] or not g["rows"]:
            return []
        x0, x1 = g["xs"][0][0], g["xs"][-1][0] + g["xs"][-1][1]
        y0, y1 = g["ys"][0][0], g["ys"][-1][0] + g["ys"][-1][1]
        lines = [(a, y0, a, y1) for a, _b in g["xs"][1:]]
        lines += [(x0, a, x1, a) for a, _b in g["ys"][1:]]
        lines += [(x0, y0, x1, y0), (x0, y1, x1, y1), (x0, y0, x0, y1), (x1, y0, x1, y1)]
        return lines

    def _describe(self, target):
        project = self.app.project
        d = self._drag
        what = ("the new " + WIDGETS[d["wtype"]]["name"]) if d["kind"] == "new" else d["id"]
        cont = project.find(target["cid"])
        where = "the window" if cont["type"] == "Window" else cont["id"]
        if target["kind"] == "side":
            if target.get("ref"):
                return "Release to put %s %s %s." % (what, SIDE_WORDS[target["mode"]],
                                                     target["ref"])
            return "Release to put %s into a new %s of %s." % (
                what, "first row" if target["mode"] == "above" else "first column", where)
        if target["kind"] == "tab":
            return "Release to put %s %s %s." % (what, target["side"], target["ref"] or "the tabs")
        if target.get("empty"):
            return "Release to put %s into %s." % (what, where)
        if target.get("shape") == "strip":
            return "Release to put %s into a new %s of %s." % (
                what, "row" if target["label"] == "New row" else "column", where)
        return "Release to put %s into %s (row %d, column %d)." % (
            what, where, target["row"] + 1, target["col"] + 1)

    def _clear_drag_visuals(self, keep_overlay=False):
        d = self._drag
        if d is None:
            return
        if d.get("after"):
            try:
                self.after_cancel(d["after"])
            except tk.TclError:
                pass
        if self._autoscroll_id is not None:
            try:
                self.after_cancel(self._autoscroll_id)
            except tk.TclError:
                pass
            self._autoscroll_id = None
        if not keep_overlay:
            self.overlay.hide()
        if d.get("src") is not None:
            self._remove_srctag(d["src"])

    def _cancel_drag(self, quiet=False):
        if not self._drag:
            return
        self._clear_drag_visuals()
        self.ghost.hide()
        self._drag = None
        self._press = None
        if self._pending_refresh:
            self.refresh()
        self.show_selection(self.app.selected_id)
        if not quiet:
            self.app.status("Cancelled – nothing was changed.")

    def _drop(self):
        d = self._drag
        target = d.get("target")
        self._clear_drag_visuals()
        self._drag = None
        self._press = None
        if target is None:
            self.ghost.hide()
            if self._pending_refresh:
                self.refresh()
            self.app.status("Dropped outside the window – nothing was changed.")
            self.show_selection(self.app.selected_id)
            return
        project = self.app.project
        snap = project.snapshot()
        try:
            if d["kind"] == "new":
                node = project.new_node(d["wtype"])
                project.place(node, target["cid"], target["row"], target["col"], target["mode"])
                node_id = node["id"]
                msg = "Added %s." % project.display_name(node)
            else:
                node = project.move(d["id"], target["cid"], target["row"], target["col"],
                                    target["mode"])
                if node is None:
                    raise ValueError("That can't be moved.")
                node_id = node["id"]
                msg = "Moved %s." % project.display_name(node)
            if target["kind"] == "tab":
                kids = project.find(target["cid"])["children"]
                me = next(k for k in kids if k["id"] == node_id)
                kids.remove(me)
                ids = [k["id"] for k in kids]
                i = ids.index(target["ref"]) if target["ref"] in ids else len(kids) - 1
                kids.insert(i if target["side"] == "before" else i + 1, me)
                self._tab_of[target["cid"]] = node_id
            elif d["wtype"] != "Tab":
                cont = project.find(target["cid"])
                if cont is not None and cont["type"] == "Tab":
                    nb = project.parent_of(cont["id"])
                    if nb is not None:
                        self._tab_of[nb["id"]] = cont["id"]
        except (ValueError, KeyError, StopIteration) as e:
            project.restore(snap)
            self.ghost.hide()
            self.refresh()
            self.app.status(str(e) or "That didn't work.")
            return
        self.app.commit(msg)
        self.app.select(node_id)
        self._created_last = []
        self._geo_cache = None
        try:
            self.update_idletasks()
        except tk.TclError:
            pass
        rect = self._geo().get(node_id)
        self.ghost.glide(rect)
        self._flash_nodes([node_id])
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass

    # ---------------------------------------------------------- flash
    def _flash_nodes(self, ids):
        """Brief green highlight that fades on the given widgets."""
        self._created_last = []
        if self._drag or not ids:
            return
        try:
            self.update_idletasks()
        except tk.TclError:
            return
        self._geo_cache = None
        geo = self._geo()
        rects = [geo[i] for i in ids if i in geo]
        if not rects:
            return
        rect = (min(r[0] for r in rects), min(r[1] for r in rects),
                max(r[2] for r in rects), max(r[3] for r in rects))
        self._stop_flash()
        ov = self.overlay
        if not ov.show():
            return
        for tag in ("dim", "box", "ind", "lab"):
            ov.set_rect(tag, None) if tag != "lab" else ov.set_label(None, None)
        ov.set_grid([])
        ov.set_rect("flash", (rect[0] - 2, rect[1] - 2, rect[2] + 2, rect[3] + 2), animate=False)
        steps = ["gray50", "gray25", "gray12", ""]

        def step(i=0):
            self._flash_id = None
            if self._drag or not ov.ok:
                return
            if i >= len(steps):
                ov.set_rect("flash", None)
                if not self._drag:
                    ov.hide()
                return
            try:
                if steps[i]:
                    ov.c.itemconfigure("flash", stipple=steps[i])
                else:
                    ov.c.itemconfigure("flash", fill="", stipple="")
            except tk.TclError:
                return
            self._flash_id = self.after(90 if i < len(steps) - 1 else 140, lambda: step(i + 1))
        ov.c.itemconfigure("flash", fill=theme.DROP_OK, stipple="gray50")
        self._flash_id = self.after(120, step)

    def _stop_flash(self):
        if self._flash_id is not None:
            try:
                self.after_cancel(self._flash_id)
            except tk.TclError:
                pass
            self._flash_id = None
            if self.overlay.ok:
                self.overlay.set_rect("flash", None)
                if not self._drag:
                    self.overlay.hide()

    # ================================================================ span handles
    def _span_possible(self, node):
        project = self.app.project
        parent = project.parent_of(node["id"])
        if parent is None or model.layout_mode(parent) != "grid" or node["type"] == "Window":
            return set()
        rows, cols = project.grid_size(parent)
        lay = node["layout"]
        out = set()
        if cols > 1 or lay["colspan"] > 1:
            out.add("col")
        if rows > 1 or lay["rowspan"] > 1:
            out.add("row")
        return out

    def _show_handles(self, node, w):
        axes = self._span_possible(node)
        s = _px(8)
        for axis, h in self._handles.items():
            if axis not in axes:
                h.place_forget()
                continue
            try:
                if axis == "col":
                    h.place(in_=w, relx=1, rely=0.5, x=2, anchor="center", width=s, height=s)
                else:
                    h.place(in_=w, relx=0.5, rely=1, y=2, anchor="center", width=s, height=s)
                h.lift()
            except tk.TclError:
                pass

    def _hide_handles(self):
        for h in getattr(self, "_handles", {}).values():
            try:
                h.place_forget()
            except tk.TclError:
                pass

    def _span_press(self, event):
        node = self.app.selected
        if node is None or self._drag:
            return "break"
        project = self.app.project
        parent = project.parent_of(node["id"])
        if parent is None:
            return "break"
        axis = event.widget._tkb_axis
        lay = node["layout"]
        self._span = {"id": node["id"], "cid": parent["id"], "axis": axis,
                      "start": lay["colspan" if axis == "col" else "rowspan"],
                      "value": None, "x": event.x_root, "y": event.y_root}
        self._geo_cache = None
        self.overlay.show()
        self._span_motion(event)
        return "break"

    def _span_limit(self, node, parent, axis):
        project = self.app.project
        rows, cols = project.grid_size(parent)
        occ = project.occupancy(parent, exclude_id=node["id"])
        lay = node["layout"]
        r, c = lay["row"], lay["col"]
        rs, cs = max(1, lay["rowspan"]), max(1, lay["colspan"])
        k = 1
        if axis == "col":
            while c + k < cols and all((rr, c + k) not in occ for rr in range(r, r + rs)):
                k += 1
        else:
            while r + k < rows and all((r + k, cc) not in occ for cc in range(c, c + cs)):
                k += 1
        return k

    def _span_motion(self, event):
        s = self._span
        if not s:
            return "break"
        s["x"], s["y"] = event.x_root, event.y_root
        project = self.app.project
        node, parent = project.find(s["id"]), project.find(s["cid"])
        g = self._grid_lines(s["cid"]) if parent is not None else None
        if node is None or g is None or not g["cols"]:
            return "break"
        lay = node["layout"]
        r, c = lay["row"], lay["col"]
        rs, cs = max(1, lay["rowspan"]), max(1, lay["colspan"])
        limit = self._span_limit(node, parent, s["axis"])
        if s["axis"] == "col":
            ends = [g["xs"][c + k - 1][0] + g["xs"][c + k - 1][1] for k in range(1, limit + 1)]
            k = min(range(len(ends)), key=lambda i: abs(ends[i] - event.x_root)) + 1
            rect = self._cell_rect(g, r, c, min(g["rows"], r + rs) - 1, c + k - 1)
            label = "%d column%s" % (k, "s" if k > 1 else "")
        else:
            ends = [g["ys"][r + k - 1][0] + g["ys"][r + k - 1][1] for k in range(1, limit + 1)]
            k = min(range(len(ends)), key=lambda i: abs(ends[i] - event.y_root)) + 1
            rect = self._cell_rect(g, r, c, r + k - 1, min(g["cols"], c + cs) - 1)
            label = "%d row%s" % (k, "s" if k > 1 else "")
        s["value"] = k
        ov = self.overlay
        ov.style_ind("cell")
        ov.set_rect("ind", rect)
        ov.set_label(label, rect)
        frame = self._inner(s["cid"])
        try:
            box = (frame.winfo_rootx(), frame.winfo_rooty(),
                   frame.winfo_rootx() + frame.winfo_width(),
                   frame.winfo_rooty() + frame.winfo_height())
        except tk.TclError:
            box = None
        ov.set_rect("box", box)
        ov.set_grid(self._grid_line_rects(s["cid"]))
        what = "columns" if s["axis"] == "col" else "rows"
        if limit == 1:
            self.app.status("There is no free cell to grow into. Make room first, or move "
                            "widgets out of the way.")
        else:
            self.app.status("%s: spread over %s. Release to keep it (Esc to cancel)."
                            % (s["id"], label))
        _ = what
        return "break"

    def _span_release(self, event):
        s, self._span = self._span, None
        self.overlay.hide()
        if not s:
            return "break"
        if self._pending_refresh:
            self.refresh()
        project = self.app.project
        node = project.find(s["id"])
        k = s.get("value")
        if node is None or not k or k == s["start"]:
            self.show_selection(self.app.selected_id)
            return "break"
        lay = node["layout"]
        key = "colspan" if s["axis"] == "col" else "rowspan"
        lay[key] = k
        extra = ""
        if k > 1 and lay.get("fill", "none") == "none":
            extra = " (Layout ▸ 'Stretch to fill space' makes it use all of that room)"
        project.compact(project.find(s["cid"]))
        word = "column" if s["axis"] == "col" else "row"
        self.app.commit("%s now spans %d %s%s%s." % (s["id"], k, word, "s" if k > 1 else "",
                                                     extra))
        self.app.select(s["id"])
        return "break"

    def _span_cancel(self):
        self._span = None
        self.overlay.hide()
        if self._pending_refresh:
            self.refresh()
        self.show_selection(self.app.selected_id)
        self.app.status("Cancelled – nothing was changed.")

    # ================================================================ editing
    def edit_text(self, node_id):
        """Small in-place editor for the text of a Label/Button/... or Tab."""
        project = self.app.project
        node = project.find(node_id) if node_id else None
        if node is None:
            return
        if node["type"] == "Window":
            self._edit_title(node_id)
            return
        if node["type"] not in TEXT_EDITABLE:
            self.app.status("This widget has no text to edit here – use the properties panel.")
            return
        self._close_editor(commit=True)
        self._reveal(node_id)
        w = self._widget(node_id)
        if w is None:
            return
        text = node["props"].get("text", "") or ""
        try:
            self.update_idletasks()
            if node["type"] == "Tab":
                nb_id = project.parent_of(node_id)["id"]
                nbw = self._rec[nb_id].r.main
                spans, strip_h = self._tab_spans(nbw)
                idx = [k["id"] for k in project.parent_of(node_id)["children"]].index(node_id)
                x0, x1 = spans[idx] if idx < len(spans) else (0, 80)
                place = dict(in_=nbw, x=x0, y=0, width=max(x1 - x0, 90),
                             height=max(strip_h, 20))
            elif node["type"] == "LabelFrame":
                place = dict(in_=w, x=4, y=-2, width=max(120, min(w.winfo_width() - 8, 260)),
                             height=_px(22))
            else:
                place = dict(in_=w, x=0, y=0, relwidth=1, relheight=1)
                if w.winfo_width() < 120:
                    o = self._outer
                    room = (o.winfo_rootx() + o.winfo_width() - 4) - (w.winfo_rootx() + 120)
                    place = dict(in_=w, x=min(0, room), y=0, width=120, relheight=1)
        except (tk.TclError, KeyError, ValueError, AttributeError):
            return
        self._open_editor(node_id, "text", text, place, multiline=("\n" in text))

    def _edit_title(self, window_id):
        win = self.app.project.find(window_id)
        if win is None or window_id != self.current_window_id:
            return
        self._close_editor(commit=True)
        place = dict(in_=self._title_label, x=0, y=3, relwidth=1, width=-8, relheight=1,
                     height=-6)
        self._open_editor(window_id, "title", win["props"].get("title", ""), place)

    def _open_editor(self, node_id, key, value, place, multiline=False):
        font = (self.styler.family, self.styler.size)
        common = dict(font=font, bd=1, relief="solid", highlightthickness=2,
                      highlightcolor=theme.ACCENT, highlightbackground=theme.ACCENT,
                      bg="#ffffff", fg="#111111", insertbackground="#111111",
                      selectbackground=theme.ACCENT, selectforeground="#ffffff")
        if multiline:
            e = tk.Text(self._outer, wrap="word", undo=True, **common)
            e.insert("1.0", value)
            e.tag_add("sel", "1.0", "end-1c")
        else:
            e = tk.Entry(self._outer, **common)
            e.insert(0, value)
            e.select_range(0, "end")
            e.icursor("end")
        e.place(**place)
        e.lift()
        e.focus_set()
        self._editor = {"w": e, "id": node_id, "key": key, "old": value, "multi": multiline}

        def commit(_e=None):
            self._close_editor(commit=True)
            return "break"

        def cancel(_e=None):
            self._close_editor(commit=False)
            return "break"
        e.bind("<Return>", commit)
        e.bind("<KP_Enter>", commit)
        if multiline:
            e.bind("<Shift-Return>", lambda ev: None)
        e.bind("<Escape>", cancel)
        e.bind("<FocusOut>", commit)
        self.app.status("Type the new text. Enter = done, Esc = cancel."
                        + (" Shift+Enter = new line." if multiline else ""))

    def _close_editor(self, commit=True):
        ed, self._editor = self._editor, None
        if not ed:
            return
        w = ed["w"]
        try:
            value = w.get("1.0", "end-1c") if ed["multi"] else w.get()
        except tk.TclError:
            value = ed["old"]
        try:
            w.destroy()
        except tk.TclError:
            pass
        try:
            self.canvas.focus_set()
        except tk.TclError:
            pass
        if not commit or value == ed["old"]:
            return
        node = self.app.project.find(ed["id"])
        if node is None:
            return
        node["props"][ed["key"]] = value
        what = "title" if ed["key"] == "title" else "text"
        self.app.commit("Changed the %s of %s." % (what, ed["id"]))
        self.app.select(ed["id"])

    # ---------------------------------------------------------- context menu
    def _context_menu(self, nid, x, y):
        project = self.app.project
        node = project.find(nid)
        t = node["type"]
        parent = project.parent_of(nid)
        m = tk.Menu(self, tearoff=0)
        if t in TEXT_EDITABLE or t == "Window":
            m.add_command(label="Edit title…" if t in ("Window", "LabelFrame", "Tab")
                          else "Edit text…", command=lambda: self.edit_text(nid))
        if WIDGETS[t]["events"] or (t == "Window"):
            m.add_command(label="Actions…", command=lambda: self._show_actions(nid))
        m.add_command(label="Rename…", command=lambda: self._rename(nid))
        m.add_separator()
        if t != "Window":
            m.add_command(label="Cut", accelerator="Ctrl+X",
                          command=lambda: self._do_selected(nid, self.cut_selection))
            m.add_command(label="Copy", accelerator="Ctrl+C",
                          command=lambda: self._do_selected(nid, self.copy_selection))
        m.add_command(label="Paste", accelerator="Ctrl+V",
                      command=lambda: self._do_selected(nid, self.paste))
        if t != "Window":
            m.add_command(label="Duplicate", accelerator="Ctrl+D",
                          command=lambda: self._do_selected(nid, self.app.duplicate_selected))
            m.add_command(label="Delete", accelerator="Del",
                          command=lambda: self._do_selected(nid, self.app.delete_selected))
        if t in ("Notebook", "Tab"):
            m.add_separator()
            m.add_command(label="Add tab", command=lambda: self._add_tab(nid))
        if t != "Window" and parent is not None:
            m.add_separator()
            if parent["type"] == "Notebook":
                m.add_command(label="Move tab left", command=lambda: self.move_node(nid, "left"))
                m.add_command(label="Move tab right",
                              command=lambda: self.move_node(nid, "right"))
            else:
                for label, dname in (("Move up", "up"), ("Move down", "down"),
                                     ("Move left", "left"), ("Move right", "right")):
                    m.add_command(label=label, command=lambda dn=dname: self.move_node(nid, dn))
            m.add_separator()
            m.add_command(label="Select parent (%s)" % parent["id"],
                          command=lambda: self.app.select(parent["id"]))
        if t == "Window" and nid != project.main_window["id"]:
            m.add_separator()
            m.add_command(label="Delete this window",
                          command=lambda: self._do_selected(nid, self.app.delete_selected))
        try:
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def _do_selected(self, nid, fn):
        self.app.select(nid)
        fn()

    def _show_actions(self, nid):
        insp = getattr(self.app, "inspector", None)
        self.app.select(nid)
        if insp is not None and hasattr(insp, "show_actions"):
            insp.show_actions(nid)
        else:
            self.app.status("Use the Actions part of the properties panel to choose what happens.")

    def _rename(self, nid):
        project = self.app.project
        name = simpledialog.askstring(
            "Rename", "New name for %s\n(letters, numbers and _ only):" % nid,
            initialvalue=nid, parent=self.winfo_toplevel())
        if not name or name.strip() == nid:
            return
        name = name.strip()
        try:
            project.rename(nid, name)
        except ValueError as e:
            messagebox.showinfo("Rename", str(e), parent=self.winfo_toplevel())
            return
        after = None
        try:
            import inspector
            after = getattr(inspector, "_after_rename", None)
        except ImportError:
            pass
        for k, v in list(self._tab_of.items()):
            if v == nid:
                self._tab_of[k] = name
            if k == nid:
                self._tab_of[name] = self._tab_of.pop(k)
        if after is not None:
            after(self.app, nid, name)
        else:
            if self.app.selected_id == nid:
                self.app.selected_id = name
            if self.current_window_id == nid:
                self.current_window_id = name
            self.app.commit("Renamed %s to %s." % (nid, name))
            self.app.select(name)

    def _add_tab(self, nid):
        project = self.app.project
        node = project.find(nid)
        nb = node if node["type"] == "Notebook" else project.parent_of(nid)
        tab = project.add_tab(nb["id"])
        self._tab_of[nb["id"]] = tab["id"]
        self.app.commit("Added a tab.")
        self.app.select(tab["id"])

    # ---------------------------------------------------------- moving by keys
    def _key_move(self, direction):
        sel = self.app.selected_id
        if sel:
            self.move_node(sel, direction)

    def move_node(self, nid, direction):
        """Move a widget one cell (swapping with the neighbour)."""
        project = self.app.project
        node = project.find(nid)
        if node is None or node["type"] == "Window":
            return
        parent = project.parent_of(nid)
        if parent is None:
            return
        if parent["type"] == "Notebook":
            delta = -1 if direction in ("left", "up") else 1
            before = [k["id"] for k in parent["children"]]
            project.move_tab(nid, delta)
            if [k["id"] for k in parent["children"]] == before:
                self.app.status("This tab is already at the end.")
                return
            self.app.commit("Moved tab %s." % nid)
            self.app.select(nid)
            return
        lay = node["layout"]
        r, c, rs, cs = lay["row"], lay["col"], lay["rowspan"], lay["colspan"]
        cell = {"up": (r - 1, c), "down": (r + rs, c), "left": (r, c - 1),
                "right": (r, c + cs)}[direction]
        rows, cols = project.grid_size(parent)
        occ = project.occupancy(parent, exclude_id=nid)
        mode = {"up": "above", "down": "below", "left": "left", "right": "right"}[direction]
        snap = project.snapshot()
        try:
            if 0 <= cell[0] < rows and 0 <= cell[1] < cols:
                other_id = occ.get(cell)
                if other_id is None:
                    lay["row"], lay["col"] = cell
                    project.compact(parent)
                else:
                    other = project.find(other_id)
                    ol = other["layout"]
                    if rs == cs == ol["rowspan"] == ol["colspan"] == 1:
                        ol["row"], ol["col"], lay["row"], lay["col"] = r, c, ol["row"], ol["col"]
                    else:
                        project.move(nid, parent["id"], ol["row"], ol["col"], mode)
            else:
                # at the edge: only useful if it shares its row/column with others
                if direction in ("up", "down"):
                    alone = not any(
                        ch["id"] != nid and ch["layout"]["row"] <= r
                        < ch["layout"]["row"] + ch["layout"]["rowspan"]
                        for ch in parent["children"])
                else:
                    alone = not any(
                        ch["id"] != nid and ch["layout"]["col"] <= c
                        < ch["layout"]["col"] + ch["layout"]["colspan"]
                        for ch in parent["children"])
                if alone:
                    self.app.status("It is already at the edge.")
                    return
                project.move(nid, parent["id"], r, c, mode)
        except ValueError as e:
            project.restore(snap)
            self.app.status(str(e))
            return
        self.app.commit("Moved %s %s." % (nid, direction))
        self.app.select(nid)
