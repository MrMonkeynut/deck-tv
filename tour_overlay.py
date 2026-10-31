#!/usr/bin/env python3
"""Deck TV tour captions, over every app.

The home screen draws its tour's captions itself, but they vanish as soon as another app comes to
the front (the tour opens Spotify, Steam, Claude and the rest). So the captions are drawn here
instead: a see-through, click-through window that floats over everything, full-screen apps
included, as the dock does. It looks like the home screen's caption card, scaled to the screen.

It also holds the tour's pretend phone. That's a browser window, and KWin puts the full-screen app in
front above any window it manages, "keep above" or not; so the server moves the phone's window into one
of these instead (an unmanaged window, as the dock is), which stays on top.

The server starts it and writes one command per line on its stdin:
  {"cmd": "caption", "section": "Movy", "n": "3 of 8", "title": "…", "text": "…", "ms": 6000,
   "place": {"left": false, "top": false, "small": false}}
  {"cmd": "hide"}
  {"cmd": "holder", "x": 0, "y": 0, "w": 450, "h": 960}   (its X window id goes in HOLDER, for the server)
  {"cmd": "holder-off"}
It quits when its stdin closes (the server stopping it, or the server itself going).
"""
import json
import os
import sys
import threading
import time

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402,F401  (GdkX11: for the holder's window id)

STAGE_W, STAGE_H = 1280, 720  # the home screen's own size, which its caption sizes are given in
FADE = 250  # ms
HOLDER = os.path.expanduser("~/.cache/deck-tv-tour-holder")

CSS = """
window { background: transparent; }
.card { background: #F2F4F8; border-radius: %(r)dpx; padding: %(pt)dpx %(px)dpx %(pb)dpx;
        box-shadow: 0 %(sy)dpx %(sb)dpx rgba(0,0,0,.5); }
.small .card { padding: %(spt)dpx %(spx)dpx %(spb)dpx; }
label { font-family: "Space Grotesk", "Cantarell", "Noto Sans", sans-serif; color: #0E1116; }
.step { font-size: %(step)dpx; font-weight: 600; color: #5B6475; letter-spacing: 1px; }
.hint { font-size: %(step)dpx; font-weight: 500; color: #5B6475; }
.title { font-size: %(title)dpx; font-weight: 700; margin-top: %(tt)dpx; }
.small .title { font-size: %(stitle)dpx; margin-top: %(stt)dpx; }
.text { font-size: %(text)dpx; color: #2A3140; margin-top: %(tx)dpx; }
.small .text { font-size: %(stext)dpx; }
"""


def scaled_css(k):
    n = lambda v: round(v * k)
    return (CSS % {
        "r": n(24), "pt": n(18), "px": n(26), "pb": n(16), "sy": n(18), "sb": n(50),
        "spt": n(12), "spx": n(20), "spb": n(11),
        "step": n(14), "title": n(27), "tt": n(6), "stitle": n(21), "stt": n(3),
        "text": n(19), "tx": n(4), "stext": n(16),
    }).encode()


class Caption(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)  # unmanaged: no focus, no taskbar entry, no border
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.k = None
        self.css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.card.get_style_context().add_class("card")
        head = Gtk.Box(spacing=10)
        self.section, self.n = Gtk.Label(xalign=0), Gtk.Label(xalign=0)
        for lbl in (self.section, self.n):
            lbl.get_style_context().add_class("step")
            head.pack_start(lbl, False, False, 0)
        hint = Gtk.Label(label="Esc or B stops · ▶ skips", xalign=1)
        hint.get_style_context().add_class("hint")
        head.pack_end(hint, False, False, 0)
        self.title = Gtk.Label(xalign=0)
        self.title.get_style_context().add_class("title")
        self.text = Gtk.Label(xalign=0)
        self.text.set_line_wrap(True)
        self.text.get_style_context().add_class("text")
        self.bar = Gtk.DrawingArea()
        self.bar.connect("draw", self.draw_bar)
        for w in (head, self.title, self.text, self.bar):
            self.card.pack_start(w, False, False, 0)
        # Room round the card for its shadow
        self.outer = Gtk.Box()
        self.outer.pack_start(self.card, True, True, 0)
        self.add(self.outer)

        self.started, self.ms = 0, 1
        self.fade_timer = None
        self.connect("realize", lambda *_: self.input_shape_combine_region(cairo.Region()))  # clicks go through
        GLib.timeout_add(500, self.keep_on_top)
        GLib.timeout_add(33, self.tick)

    def area(self):
        display = self.get_display()
        return (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()

    def set_scale(self, k):
        if k != self.k:
            self.k = k
            self.css.load_from_data(scaled_css(k))

    # ---------- commands ----------
    def caption(self, c):
        a = self.area()
        k = min(a.width / STAGE_W, a.height / STAGE_H)
        self.set_scale(k)
        place = c.get("place") or {}
        small, left, top = place.get("small"), place.get("left"), place.get("top")
        self.get_style_context().remove_class("small")
        self.outer.get_style_context().remove_class("small")
        if small:
            self.outer.get_style_context().add_class("small")
        self.section.set_text((c.get("section") or "").upper())
        self.n.set_text((c.get("n") or "").upper())
        self.title.set_text(c.get("title") or "")
        self.text.set_text(c.get("text") or "")
        width = round((640 if small else 760 if left else 780) * k)
        pad = round(30 * k)  # the shadow's room
        self.outer.set_margin_start(pad); self.outer.set_margin_end(pad)
        self.outer.set_margin_top(round(10 * k)); self.outer.set_margin_bottom(pad)
        self.card.set_size_request(width, -1)
        self.bar.set_size_request(-1, max(3, round(4 * k)))
        self.bar.set_margin_top(round((9 if small else 14) * k))
        self.text.set_max_width_chars(10)  # wrap at the card's width, not the text's
        self.text.set_size_request(width - round((40 if small else 52) * k), -1)
        self.started, self.ms = time.monotonic(), max(1, c.get("ms") or 1)

        self.show_all()
        self.resize(1, 1)
        _, nat = self.get_preferred_size()
        w, h = nat.width, nat.height
        edge = round((14 if small else 30) * k)
        x = a.x + (round(72 * k) - pad if left else (a.width - w) // 2)
        y = a.y + (edge - round(10 * k) if top else a.height - edge - h + pad)
        self.resize(w, h)
        self.move(x, y)
        if not self.get_visible() or self.get_opacity() < 1:
            self.fade(1)

    def hide_caption(self):
        if self.get_visible():
            self.fade(0)

    # ---------- drawing and keeping on top ----------
    def draw_bar(self, area, cr):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        f = min(1, (time.monotonic() - self.started) * 1000 / self.ms)
        cr.set_source_rgb(0xD5 / 255, 0xDA / 255, 0xE3 / 255)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        cr.set_source_rgb(0x0E / 255, 0x11 / 255, 0x16 / 255)
        cr.rectangle(0, 0, w * f, h)
        cr.fill()

    def tick(self):
        if self.get_visible():
            self.bar.queue_draw()
        return True

    def fade(self, to):
        if self.fade_timer:
            GLib.source_remove(self.fade_timer)
        if to:
            self.set_opacity(0)
            self.show_all()
        start, o0 = time.monotonic(), self.get_opacity()

        def step():
            t = min(1, (time.monotonic() - start) * 1000 / FADE)
            self.set_opacity(o0 + (to - o0) * t)
            if t < 1:
                return True
            self.fade_timer = None
            if not to:
                self.hide()
            return False
        self.fade_timer = GLib.timeout_add(16, step)

    def keep_on_top(self):
        win = self.get_window()
        if win and self.get_visible():
            win.raise_()
        return True


class Holder(Gtk.Window):
    """An unmanaged, always-on-top window for the pretend phone's browser window to live in."""
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)
        self.connect("draw", lambda w, cr: (cr.set_source_rgb(0, 0, 0), cr.paint()) and False)
        self.set_app_paintable(True)
        GLib.timeout_add(500, self.keep_on_top)

    def place(self, c):
        self.resize(c["w"], c["h"])
        self.move(c["x"], c["y"])
        self.show_all()
        with open(HOLDER, "w") as f:
            f.write(str(self.get_window().get_xid()))

    def keep_on_top(self):
        if self.get_window():
            self.get_window().raise_()
        return True


def main():
    cap = Caption()
    holder = None

    def handle(line):
        nonlocal holder
        try:
            c = json.loads(line)
        except ValueError:
            return False
        if c.get("cmd") == "caption":
            cap.caption(c)
        elif c.get("cmd") == "hide":
            cap.hide_caption()
        elif c.get("cmd") == "holder":
            holder = holder or Holder()
            holder.place(c)
        elif c.get("cmd") == "holder-off" and holder:
            holder.destroy()
            holder = None
        return False

    def read():
        for line in sys.stdin:
            GLib.idle_add(handle, line)
        GLib.idle_add(Gtk.main_quit)  # stdin closed: the server's done with us
    threading.Thread(target=read, daemon=True).start()
    Gtk.main()


if __name__ == "__main__":
    main()
