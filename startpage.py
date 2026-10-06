"""
Start page: shown when TkBuilder starts without a file and for File ▸ New.

New blank project, open, recent projects and the templates (the projects in
./examples).  Everything is drawn on one Canvas, so it appears instantly
(creating many native widgets is slow) and resizes smoothly.  Each template
card shows a miniature of the template's main window in its own theme.
"""

import json
import os
import time
import tkinter as tk
import tkinter.font as tkfont

import apptheme
import theme

EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "examples")


def _rounded(canvas, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle as a smoothed polygon."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


def _template_title(filename):
    name = os.path.splitext(filename)[0]
    head, sep, tail = name.partition(" - ")
    return tail if sep and head.strip().isdigit() else name


_cache = {}


def load_templates(folder=EXAMPLES_DIR):
    """[{path, title, description, project}] for every .tkb in `folder`
    (cached by modification time)."""
    out = []
    try:
        names = sorted(os.listdir(folder), key=lambda n: (
            int(n.split(" - ")[0]) if n.split(" - ")[0].isdigit() else 999, n.lower()))
    except OSError:
        return out
    for name in names:
        if not name.lower().endswith(".tkb"):
            continue
        path = os.path.join(folder, name)
        try:
            mtime = os.path.getmtime(path)
            cached = _cache.get(path)
            if cached is None or cached[0] != mtime:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                _cache[path] = cached = (mtime, data)
            data = cached[1]
            win = data["windows"][0]
        except (OSError, ValueError, KeyError, IndexError):
            continue
        settings = data.get("settings", {})
        out.append({"path": path, "title": _template_title(name),
                    "description": settings.get("description")
                    or win.get("props", {}).get("title", ""),
                    "theme": settings.get("theme", "native"), "window": win})
    return out


# --------------------------------------------------------------------------
# Miniature of a window, drawn from the project data
# --------------------------------------------------------------------------

def _colors(theme_key):
    p = apptheme.palette(theme_key)
    if p is None:     # stock themes look like a plain light window
        p = dict(bg="#f0f0f0", surface="#ffffff", fg="#1f2328", muted="#8a8f98",
                 border="#adadad", field="#ffffff", accent="#0078d4", trough="#e6e6e6",
                 heading="#e8e8e8", button="#fdfdfd")
    p = dict(p)
    p["console"] = apptheme.tk_colors(theme_key, "Console").get("bg", "#1e1e1e")
    return p


def draw_miniature(canvas, window, theme_key, x1, y1, x2, y2, tags=()):
    """Draw a tiny picture of `window` (a Window node) into the box."""
    c = _colors(theme_key)
    dark = apptheme.is_dark(theme_key)
    props = window.get("props", {})
    ww, wh = max(100, props.get("width", 480)), max(80, props.get("height", 360))
    # draw in 96-DPI units, then scale everything up for the screen
    k = theme.SCALE
    x1, y1, x2, y2 = x1 / k, y1 / k, x2 / k, y2 / k
    tag = "mini%d" % id(window)
    tags = tuple(tags) + (tag,)
    bar = 9
    avail_w, avail_h = x2 - x1, y2 - y1 - bar
    s = min(avail_w / ww, avail_h / wh)
    w, h = ww * s, wh * s + bar
    ox, oy = x1 + (avail_w - w) / 2, y1 + (y2 - y1 - h) / 2
    kw = {"tags": tags}
    canvas.create_rectangle(ox, oy, ox + w, oy + h, fill=c["bg"], outline=c["border"], **kw)
    canvas.create_rectangle(ox, oy, ox + w, oy + bar, fill="#2b2d31" if dark else "#e9ebef",
                            outline="", **kw)
    for i, col in enumerate(("#ef4444", "#f59e0b", "#22c55e")):
        cx = ox + 6 + i * 6
        canvas.create_oval(cx - 2, oy + bar / 2 - 2, cx + 2, oy + bar / 2 + 2, fill=col,
                           outline="", **kw)
    pad = max(2, props.get("padding", 12) * s)
    _draw_children(canvas, window, c, ox + pad, oy + bar + pad, ox + w - pad, oy + h - pad,
                   kw, 0)
    canvas.scale(tag, 0, 0, k, k)


def _draw_children(canvas, container, c, x1, y1, x2, y2, kw, depth):
    kids = container.get("children", [])
    if not kids or depth > 3 or x2 - x1 < 6 or y2 - y1 < 6:
        return
    if container["type"] == "Notebook":
        tab_h = min(8, (y2 - y1) / 4)
        n = len(kids)
        tw = min((x2 - x1) / max(n, 1), 26)
        for i in range(n):
            canvas.create_rectangle(x1 + i * tw, y1, x1 + (i + 1) * tw - 1, y1 + tab_h,
                                    fill=c["bg"] if i == 0 else c["heading"],
                                    outline=c["border"], **kw)
        canvas.create_rectangle(x1, y1 + tab_h, x2, y2, fill=c["bg"], outline=c["border"], **kw)
        _draw_children(canvas, kids[0], c, x1 + 2, y1 + tab_h + 2, x2 - 2, y2 - 2, kw, depth + 1)
        return
    rows = max(ch["layout"]["row"] + ch["layout"].get("rowspan", 1) for ch in kids)
    cols = max(ch["layout"]["col"] + ch["layout"].get("colspan", 1) for ch in kids)
    # rows get a height that suits their content; stretching rows get the rest
    weights = []
    for r in range(rows):
        kinds = [ch["type"] for ch in kids if ch["layout"]["row"] == r]
        big = any(k in ("Text", "Listbox", "Table", "Console", "Canvas", "Notebook",
                        "LabelFrame", "Frame") for k in kinds)
        weights.append(3.0 if big else 1.0)
    total = sum(weights) or 1
    ys = [y1]
    for wgt in weights:
        ys.append(ys[-1] + (y2 - y1) * wgt / total)
    cw = (x2 - x1) / cols
    for ch in kids:
        lay = ch["layout"]
        cx1 = x1 + lay["col"] * cw + 1
        cx2 = x1 + (lay["col"] + lay.get("colspan", 1)) * cw - 1
        cy1 = ys[lay["row"]] + 1
        cy2 = ys[min(rows, lay["row"] + lay.get("rowspan", 1))] - 1
        _draw_widget(canvas, ch, c, cx1, cy1, cx2, cy2, kw, depth)


def _draw_widget(canvas, node, c, x1, y1, x2, y2, kw, depth):
    t = node["type"]
    props = node.get("props", {})
    fill = node.get("layout", {}).get("fill", "none")
    mid = (y1 + y2) / 2
    if t in ("Frame", "LabelFrame", "Tab"):
        if t == "LabelFrame" or props.get("variant") == "Card":
            canvas.create_rectangle(x1, y1 + 2, x2, y2, outline=c["border"], **kw)
            canvas.create_line(x1 + 3, y1 + 2, x1 + min(18, (x2 - x1) / 3), y1 + 2,
                               fill=c["fg"], width=2, **kw)
        _draw_children(canvas, node, c, x1 + 3, y1 + 5, x2 - 3, y2 - 2, kw, depth + 1)
    elif t == "Notebook":
        _draw_children(canvas, node, c, x1, y1, x2, y2, kw, depth)
    elif t == "Button":
        accent = props.get("variant") in ("Accent", "Success", "Danger")
        color = {"Success": c.get("success", "#16a34a"),
                 "Danger": c.get("danger", "#dc2626")}.get(props.get("variant"), c["accent"])
        bw = x2 - x1 if fill in ("x", "both") else min(x2 - x1, 34)
        bx = {"center": (x1 + x2 - bw) / 2, "right": x2 - bw}.get(
            node.get("layout", {}).get("halign"), x1)
        h = min(9, y2 - y1)
        _rounded(canvas, bx, mid - h / 2, bx + bw, mid + h / 2, 2,
                 fill=color if accent else c.get("button", c["surface"]),
                 outline=color if accent else c["border"], **kw)
    elif t == "Label":
        variant = props.get("variant")
        size = props.get("font", {}).get("size", 0) or 0
        big = variant == "Title" or size >= 14
        h = 4 if big else 2
        text = str(props.get("text", ""))
        ln = min(x2 - x1, max(8, len(text) * (3.2 if big else 2.2)))
        color = c["muted"] if variant == "Muted" else c["fg"]
        canvas.create_rectangle(x1, mid - h / 2, x1 + ln, mid + h / 2, fill=color, outline="",
                                **kw)
    elif t in ("Entry", "Combobox", "Spinbox", "FilePicker"):
        h = min(9, y2 - y1)
        ex2 = x2 if fill in ("x", "both") or t == "FilePicker" else min(x2, x1 + 48)
        if t == "FilePicker":
            ex2 -= 14
            canvas.create_rectangle(ex2 + 2, mid - h / 2, x2, mid + h / 2,
                                    fill=c.get("button", c["surface"]), outline=c["border"], **kw)
        canvas.create_rectangle(x1, mid - h / 2, ex2, mid + h / 2, fill=c["field"],
                                outline=c["border"], **kw)
    elif t in ("Text", "Listbox", "Table", "Console", "Canvas"):
        bg = c["console"] if t == "Console" else c["field"]
        canvas.create_rectangle(x1, y1, x2, y2, fill=bg, outline=c["border"], **kw)
        if t == "Table":
            canvas.create_rectangle(x1, y1, x2, min(y2, y1 + 6), fill=c["heading"],
                                    outline=c["border"], **kw)
        line = "#7ee787" if t == "Console" else c["muted"]
        if t != "Canvas":
            y = y1 + (9 if t == "Table" else 4)
            k = 0
            while y < y2 - 3 and k < 8:
                canvas.create_line(x1 + 3, y, x1 + 3 + (x2 - x1 - 6) * (0.8 - 0.25 * (k % 3)),
                                   y, fill=line, **kw)
                y += 4
                k += 1
    elif t == "Progressbar":
        h = min(5, y2 - y1)
        canvas.create_rectangle(x1, mid - h / 2, x2, mid + h / 2, fill=c["trough"], outline="",
                                **kw)
        frac = max(0.0, min(1.0, float(props.get("value", 30) or 0)
                            / float(props.get("maximum", 100) or 100))) or 0.6
        canvas.create_rectangle(x1, mid - h / 2, x1 + (x2 - x1) * frac, mid + h / 2,
                                fill=c["accent"], outline="", **kw)
    elif t == "Scale":
        canvas.create_line(x1, mid, x2, mid, fill=c["trough"], width=2, **kw)
        kx = x1 + (x2 - x1) * 0.4
        canvas.create_oval(kx - 3, mid - 3, kx + 3, mid + 3, fill=c["accent"], outline="", **kw)
    elif t in ("Checkbutton", "Radiobutton"):
        sel = props.get("checked") or props.get("selected")
        box = (canvas.create_oval if t == "Radiobutton" else canvas.create_rectangle)
        box(x1, mid - 3, x1 + 6, mid + 3, fill=c["accent"] if sel else c["field"],
            outline=c["border"], **kw)
        canvas.create_rectangle(x1 + 9, mid - 1, min(x2, x1 + 30), mid + 1, fill=c["fg"],
                                outline="", **kw)
    elif t == "Separator":
        canvas.create_line(x1, mid, x2, mid, fill=c["border"], **kw)


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------

class StartPage(tk.Frame):
    """Welcome screen.  Talks to the app hub: new_blank(), open_project(),
    open_recent(path), open_template(path), recover_autosave(),
    clear_autosave(), autosave_info(), hide_start_page(), prefs."""

    def __init__(self, master, app):
        super().__init__(master, bg=theme.BG, takefocus=True)
        self.app = app
        self.canvas = tk.Canvas(self, bg=theme.BG, highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self.regions = []          # (x1, y1, x2, y2, on_click, hover_items, hover_style)
        self.hover = None
        self._job = None
        p = theme.px
        f = theme.UI_FAMILY
        self.f_title = tkfont.Font(self, family=f, size=22, weight="bold")
        self.f_sub = tkfont.Font(self, family=f, size=11)
        self.f_head = tkfont.Font(self, family=f, size=9, weight="bold")
        self.f_card = tkfont.Font(self, family=f, size=11, weight="bold")
        self.f_text = tkfont.Font(self, family=f, size=9)
        self.f_name = tkfont.Font(self, family=f, size=10, weight="bold")
        self.f_icon = tkfont.Font(self, family=theme.ICON_FAMILY or f,
                                  size=15 if theme.ICON_FAMILY else 14)
        self.pad = p(40)
        c = self.canvas
        c.bind("<Configure>", lambda e: self._schedule())
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", lambda e: self._set_hover(None))
        c.bind("<Button-1>", self._click)
        c.bind("<MouseWheel>", self._wheel)
        self.bind("<MouseWheel>", self._wheel)

    # ---------------------------------------------------------- drawing
    def refresh(self):
        self._schedule()

    def _schedule(self):
        if self._job is None:
            self._job = self.after_idle(self._draw)

    def _region(self, x1, y1, x2, y2, on_click, items, style="card"):
        self.regions.append((x1, y1, x2, y2, on_click, items, style))

    def _draw(self):
        self._job = None
        c = self.canvas
        c.delete("all")
        self.regions = []
        self.hover = None
        p = theme.px
        W = max(c.winfo_width(), p(700))
        content_w = min(W - 2 * self.pad, p(1180))
        x0 = (W - content_w) / 2
        y = p(36)

        # ---- header
        s = p(46)
        _rounded(c, x0, y, x0 + s, y + s, p(10), fill=theme.ACCENT, outline="")
        c.create_text(x0 + s / 2, y + s / 2, text="Tk", fill="#ffffff",
                      font=(theme.UI_FAMILY, 15, "bold"))
        c.create_text(x0 + s + p(16), y + p(2), text="TkBuilder", anchor="nw", fill=theme.TEXT,
                      font=self.f_title)
        c.create_text(x0 + s + p(16), y + p(40), anchor="nw", fill=theme.MUTED,
                      font=self.f_sub,
                      text="Design a desktop app by drag & drop, connect it to your own "
                           "Python functions, and export clean code.")
        y += s + p(34)

        # ---- recovery banner
        info = self.app.autosave_info()
        if info:
            y = self._draw_banner(info, x0, y, content_w) + p(24)

        # ---- columns
        left_w = p(330)
        gap = p(48)
        right_x = x0 + left_w + gap
        right_w = content_w - left_w - gap
        ly = self._draw_left(x0, y, left_w)
        ry = self._draw_templates(right_x, y, right_w)
        bottom = max(ly, ry) + p(24)
        c.create_text(x0, bottom, anchor="nw", fill=theme.MUTED, font=self.f_text,
                      text="Tip: press F1 any time for keyboard shortcuts.")
        c.configure(scrollregion=(0, 0, W, bottom + p(40)))

    def _section(self, x, y, text):
        self.canvas.create_text(x, y, anchor="nw", text=text.upper(), fill=theme.MUTED,
                                font=self.f_head)
        return y + theme.px(24)

    def _action_card(self, x, y, w, icon, fallback, title, desc, command, primary=False):
        p = theme.px
        c = self.canvas
        h = p(64)
        bg = _rounded(c, x, y, x + w, y + h, p(10), fill=theme.PANEL, outline=theme.BORDER)
        r = p(18)
        cx, cy = x + p(14) + r, y + h / 2
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="",
                      fill=theme.ACCENT if primary else theme.ACCENT_LIGHT)
        c.create_text(cx, cy, text=icon if theme.ICON_FAMILY else fallback, font=self.f_icon,
                      fill="#ffffff" if primary else theme.ACCENT)
        tx = cx + r + p(14)
        c.create_text(tx, y + p(14), anchor="nw", text=title, font=self.f_card, fill=theme.TEXT)
        c.create_text(tx, y + p(36), anchor="nw", text=desc, font=self.f_text, fill=theme.MUTED,
                      width=w - (tx - x) - p(10))
        self._region(x, y, x + w, y + h, command, [bg])
        return y + h + p(10)

    def _elide(self, text, font, width):
        if font.measure(text) <= width:
            return text
        while text and font.measure("…" + text) > width:
            text = text[1:]
        return "…" + text

    def _draw_left(self, x, y, w):
        p = theme.px
        c = self.canvas
        app = self.app
        y = self._section(x, y, "Start")
        y = self._action_card(x, y, w, "", "＋", "New blank project",
                              "An empty window to design from scratch", app.new_blank,
                              primary=True)
        y = self._action_card(x, y, w, "", "\U0001F4C2", "Open a project…",
                              "A .tkb file you saved before", app.open_project)
        if app._has_project_open():
            name = os.path.basename(app.file_path) if app.file_path else "Untitled"
            y = self._action_card(x, y, w, "", "←", "Back to your project",
                                  "%s%s" % (name, " (unsaved changes)" if app.dirty else ""),
                                  app.hide_start_page)
        y += p(20)
        y = self._section(x, y, "Recent projects")
        recent = list(app.prefs.get("recent", []))[:8]
        if not recent:
            c.create_text(x, y, anchor="nw", fill=theme.MUTED, font=self.f_text, width=w,
                          text="Projects you open or save will be listed here.")
            return y + p(24)
        row_h = p(46)
        for path in recent:
            exists = os.path.exists(path)
            bg = _rounded(c, x, y, x + w, y + row_h - p(4), p(8), fill=theme.BG, outline="")
            name = os.path.splitext(os.path.basename(path))[0]
            c.create_text(x + p(12), y + p(6), anchor="nw", font=self.f_name,
                          fill=theme.TEXT if exists else theme.DISABLED,
                          text=self._elide(name, self.f_name, w - p(24)))
            folder = os.path.dirname(path) if exists else "Not found: " + os.path.dirname(path)
            c.create_text(x + p(12), y + p(24), anchor="nw", font=self.f_text,
                          fill=theme.MUTED if exists else theme.DISABLED,
                          text=self._elide(folder, self.f_text, w - p(24)))
            self._region(x, y, x + w, y + row_h - p(4),
                         lambda pth=path: app.open_recent(pth), [bg], "row")
            y += row_h
        return y

    def _draw_templates(self, x, y, w):
        p = theme.px
        c = self.canvas
        y = self._section(x, y, "Templates")
        c.create_text(x + self.f_head.measure("TEMPLATES") + p(10), y - p(24), anchor="nw",
                      fill=theme.MUTED, font=self.f_text,
                      text="ready-made apps to try, learn from or adapt")
        templates = load_templates()
        if not templates:
            c.create_text(x, y, anchor="nw", fill=theme.MUTED, font=self.f_text,
                          text="No templates found (the examples folder is missing).")
            return y + p(24)
        gap = p(16)
        cols = max(1, min(3, int((w + gap) // (p(230) + gap))))
        cw = (w - gap * (cols - 1)) / cols
        thumb_h = p(118)
        ch = thumb_h + p(76)
        for i, t in enumerate(templates):
            r, k = divmod(i, cols)
            cx = x + k * (cw + gap)
            cy = y + r * (ch + gap)
            bg = _rounded(c, cx, cy, cx + cw, cy + ch, p(10), fill=theme.PANEL,
                          outline=theme.BORDER)
            # thumbnail area
            _rounded(c, cx + p(8), cy + p(8), cx + cw - p(8), cy + thumb_h, p(6),
                     fill=theme.WORKPLANE_BG, outline="")
            draw_miniature(c, t["window"], t["theme"], cx + p(18), cy + p(16),
                           cx + cw - p(18), cy + thumb_h - p(8))
            c.create_text(cx + p(14), cy + thumb_h + p(10), anchor="nw", text=t["title"],
                          font=self.f_card, fill=theme.TEXT)
            c.create_text(cx + p(14), cy + thumb_h + p(32), anchor="nw", text=t["description"],
                          font=self.f_text, fill=theme.MUTED, width=cw - p(28))
            name = apptheme.THEMES.get(t["theme"], {}).get("name", "")
            if name:
                c.create_text(cx + cw - p(14), cy + thumb_h + p(12), anchor="ne", text=name,
                              font=self.f_text, fill=theme.MUTED)
            self._region(cx, cy, cx + cw, cy + ch,
                         lambda pth=t["path"]: self.app.open_template(pth), [bg])
        rows = (len(templates) + cols - 1) // cols
        return y + rows * (ch + gap)

    def _draw_banner(self, info, x, y, w):
        p = theme.px
        c = self.canvas
        h = p(56)
        warm_bg = "#fff7e6" if not theme.is_dark() else "#3a2f17"
        warm_line = "#f5c46b" if not theme.is_dark() else "#8a6a2a"
        _rounded(c, x, y, x + w, y + h, p(10), fill=warm_bg, outline=warm_line)
        when = time.strftime("%d %b, %H:%M", time.localtime(info.get("saved_at", time.time())))
        c.create_text(x + p(18), y + h / 2, anchor="w", font=self.f_name, fill=theme.TEXT,
                      text="TkBuilder closed before your changes to “%s” were saved." % (
                          info.get("app_name") or "Untitled"))
        bx = x + w - p(14)
        for label, cmd, primary in (("Discard", self._discard, False),
                                    ("Recover (%s)" % when, self.app.recover_autosave, True)):
            tw = self.f_name.measure(label) + p(28)
            b = _rounded(c, bx - tw, y + p(12), bx, y + h - p(12), p(8),
                         fill=theme.ACCENT if primary else theme.PANEL,
                         outline=theme.ACCENT if primary else theme.BORDER)
            c.create_text(bx - tw / 2, y + h / 2, text=label, font=self.f_name,
                          fill="#ffffff" if primary else theme.TEXT)
            self._region(bx - tw, y + p(12), bx, y + h - p(12), cmd, [b],
                         "primary" if primary else "button")
            bx -= tw + p(10)
        return y + h

    def _discard(self):
        self.app.clear_autosave()
        self.refresh()

    # ---------------------------------------------------------- interaction
    def _hit(self, event):
        x, y = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        for reg in self.regions:
            if reg[0] <= x <= reg[2] and reg[1] <= y <= reg[3]:
                return reg
        return None

    def _set_hover(self, reg):
        if reg is self.hover:
            return
        c = self.canvas
        if self.hover is not None:
            for item in self.hover[5]:
                style = self.hover[6]
                if style == "row":
                    c.itemconfigure(item, fill=theme.BG)
                elif style == "primary":
                    c.itemconfigure(item, fill=theme.ACCENT, outline=theme.ACCENT)
                else:
                    c.itemconfigure(item, fill=theme.PANEL, outline=theme.BORDER)
        self.hover = reg
        if reg is not None:
            for item in reg[5]:
                if reg[6] == "row":
                    c.itemconfigure(item, fill=theme.HOVER)
                elif reg[6] == "primary":
                    c.itemconfigure(item, fill=theme.ACCENT_HOVER, outline=theme.ACCENT_HOVER)
                elif reg[6] == "button":
                    c.itemconfigure(item, fill=theme.HOVER)
                else:
                    c.itemconfigure(item, outline=theme.ACCENT)
        c.configure(cursor="hand2" if reg is not None else "")

    def _motion(self, event):
        self._set_hover(self._hit(event))

    def _click(self, event):
        reg = self._hit(event)
        if reg is not None:
            self._set_hover(None)
            reg[4]()

    def _wheel(self, event):
        c = self.canvas
        region = c.cget("scrollregion").split()
        if region and float(region[3]) > c.winfo_height():
            c.yview_scroll(int(-event.delta / 120), "units")
