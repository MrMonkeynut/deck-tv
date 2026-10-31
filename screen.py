"""Deck TV - the TV's picture for the phone remote.

Snapshots of the screen Deck TV is on (the main one), a few a second, shrunk to phone size, with the
mouse pointer drawn in (screenshots leave it out). Nothing is captured unless a phone is watching:
the capture thread sleeps until one connects and goes back to sleep when the last one leaves.
"""
import math
import threading
import time

try:
    import cairo
    import gi
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, GdkPixbuf
    Gdk.init([])
    AVAILABLE = Gdk.Display.get_default() is not None
except (ImportError, ValueError, TypeError, RuntimeError):
    AVAILABLE = False

FPS = 15         # snapshots a second while a phone is watching
WIDTH = 960      # the picture's width; tall enough to read on a phone, small enough to send quickly
QUALITY = "68"   # JPEG quality


class Screen:
    def __init__(self):
        self.cond = threading.Condition()
        self.gdk = threading.Lock()  # GDK is used from the capture thread and for taps: one at a time
        self.frame, self.seq, self.viewers, self.thread = None, 0, 0, None

    def area(self):
        """The main screen's place and size: (x, y, width, height)."""
        with self.gdk:
            display = Gdk.Display.get_default()
            g = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
            return g.x, g.y, g.width, g.height

    def grab(self):
        x, y, w, h = self.area()
        with self.gdk:
            pix = Gdk.pixbuf_get_from_window(Gdk.get_default_root_window(), x, y, w, h)
            if pix is None:
                return None
            _, px, py = Gdk.Display.get_default().get_default_seat().get_pointer().get_position()
        th = round(h * WIDTH / w)
        pix = pix.scale_simple(WIDTH, th, GdkPixbuf.InterpType.BILINEAR)
        # The pointer, as a white dot ringed in black so it shows on anything
        if x <= px < x + w and y <= py < y + h:
            surface = cairo.ImageSurface(cairo.FORMAT_RGB24, WIDTH, th)
            cr = cairo.Context(surface)
            Gdk.cairo_set_source_pixbuf(cr, pix, 0, 0)
            cr.paint()
            cx, cy = (px - x) * WIDTH / w, (py - y) * th / h
            cr.arc(cx, cy, 7, 0, 2 * math.pi)
            cr.set_source_rgba(0, 0, 0, .55)
            cr.set_line_width(4)
            cr.stroke_preserve()
            cr.set_source_rgb(1, 1, 1)
            cr.fill()
            pix = Gdk.pixbuf_get_from_surface(surface, 0, 0, WIDTH, th)
        ok, data = pix.save_to_bufferv("jpeg", ["quality"], [QUALITY])
        return bytes(data) if ok else None

    def run(self):
        while True:
            with self.cond:
                self.cond.wait_for(lambda: self.viewers > 0)
            started = time.monotonic()
            try:
                frame = self.grab()
            except Exception:
                frame = None
            if frame:
                with self.cond:
                    self.frame, self.seq = frame, self.seq + 1
                    self.cond.notify_all()
            time.sleep(max(0.0, 1 / FPS - (time.monotonic() - started)))

    def frames(self):
        """Each new snapshot, for as long as the caller keeps asking; counts as a viewer meanwhile."""
        with self.cond:
            self.viewers += 1
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, daemon=True)
                self.thread.start()
            self.cond.notify_all()
        try:
            seen = -1
            while True:
                with self.cond:
                    self.cond.wait_for(lambda: self.seq != seen, timeout=3)
                    seen, frame = self.seq, self.frame
                if frame:
                    yield frame
        finally:
            with self.cond:
                self.viewers -= 1

    def point(self, fx, fy):
        """Where on the screen a spot on the picture is: fx, fy from 0 to 1 across and down."""
        x, y, w, h = self.area()
        return x + round(min(max(fx, 0.0), 1.0) * (w - 1)), y + round(min(max(fy, 0.0), 1.0) * (h - 1))


screen = Screen() if AVAILABLE else None
