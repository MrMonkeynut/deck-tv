#!/usr/bin/env python3
"""Deck TV dock - a Mac-style dock that floats over every app, full-screen ones included.

Hidden:  an invisible strip along the bottom edge of the screen.
Hint:    mouse to the bottom and a glowing line appears; click it to open the dock.
Dock:    Home plus every Deck TV tile; a short line under an icon means that app is open.
         The chevron hides it again, and so does picking an app or clicking anywhere else.
         Only apps that are open are shown (Home is always there), so it stays short.
         Right-click an app for a small menu that closes it.
Alt+Tab: Deck TV's own switcher between Home and the open apps (see switch()), drawn by this
         process; the deck-tv-switch command (bound to Alt+Tab) pokes it with a signal. Alt+Tab
         is only Deck TV's while the dock runs: it gives it back to KDE when it closes.
         The speaker on the left sets the volume: click for a slider, or scroll over it.
         Left of it, the player button pops up previous / play-pause / next.
"""
import fcntl
import json
import os
import signal
import sys
import subprocess
import threading
import time
import urllib.request

import ctypes

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_foreign("cairo")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

API = "http://127.0.0.1:" + os.environ.get("DECK_TV_PORT", "8765")
ICON = 52
STRIP = 3          # height of the invisible hover strip
HINT_W, HINT_H = 420, 90   # click target around the glowing line
MARGIN = 14        # gap between the dock and the bottom edge
SWITCH_ICON = 88   # icon size in the Alt+Tab switcher
DOCK_AWAY = 5      # seconds an open dock stays once the pointer leaves it
DOCK_IDLE = 15     # ... or sits with the pointer still on it, untouched
HOME_ICON = os.path.expanduser("~/.local/share/deck-tv/icons/deck-tv.png")

# Same line icons as the Deck TV page, for tiles without an installed app icon
LINE_ICONS = {
    "film": '<rect x="2" y="5" width="20" height="14" rx="2"/><path d="M10 9.5v5l4.5-2.5z"/>',
    "gamepad": '<path d="M6 11h4M8 9v4"/><circle cx="15" cy="12" r="1"/><circle cx="18" cy="10" r="1"/><path d="M17.3 5H6.7A4.7 4.7 0 0 0 2 9.7V14a4 4 0 0 0 7 2.6L10 15h4l1 1.6A4 4 0 0 0 22 14V9.7A4.7 4.7 0 0 0 17.3 5z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "tv": '<rect x="2" y="4" width="20" height="14" rx="3"/><path d="M10 8.5v5l4-2.5zM8 21h8"/>',
    "octagon": '<path d="M8 3h8l5 5v8l-5 5H8l-5-5V8z"/><path d="M9.5 15v-4.5a1.5 1.5 0 0 1 3 0V9.5a1.5 1.5 0 0 1 3 0V14a3 3 0 0 1-3 3h-1.5a1.5 1.5 0 0 1-1.5-1.5z"/>',
    "spark": '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8"/>',
}

CSS = b"""
window { background: transparent; }
.bloom {
  min-width: 220px; min-height: 8px; border-radius: 4px;
  background: rgba(242, 244, 248, 0.92);
  box-shadow: 0 0 10px 3px rgba(139, 147, 255, 0.85), 0 0 24px 8px rgba(91, 99, 230, 0.45);
}
.bloom.hot {
  background: #ffffff;
  box-shadow: 0 0 14px 5px rgba(160, 167, 255, 0.95), 0 0 32px 12px rgba(91, 99, 230, 0.55);
}
.dock {
  background: rgba(20, 24, 31, 0.86); border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 24px; padding: 10px 12px 6px;
}
.item {
  padding: 6px 8px 2px; border: none; border-radius: 16px; background: transparent;
  box-shadow: none; outline: none; background-image: none;
}
.item:hover { background: rgba(255, 255, 255, 0.09); }
.dot { min-width: 18px; min-height: 4px; border-radius: 2px; background: transparent; }
.dot.open { background: #F2F4F8; }
.sep { min-width: 1px; background: rgba(255, 255, 255, 0.12); margin: 10px 6px 14px; }
.hide { color: #DDE2EB; padding: 6px 10px; }
.menu {
  background: rgba(20, 24, 31, 0.95); border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 14px; padding: 6px;
}
.menu button {
  color: #F2F4F8; font-size: 15px; padding: 8px 16px; border: none; border-radius: 9px;
  background: transparent; background-image: none; box-shadow: none;
}
.menu button:hover { background: rgba(255, 90, 90, 0.22); }
.menu label.note { color: rgba(242, 244, 248, 0.55); font-size: 15px; padding: 8px 16px; }
.switcher {
  background: rgba(20, 24, 31, 0.95); border: 1px solid rgba(255, 255, 255, 0.10);
  border-radius: 30px; padding: 22px;
}
.switch-item { padding: 16px 18px 12px; border-radius: 22px; }
.switch-item.sel { background: rgba(255, 255, 255, 0.14); box-shadow: inset 0 0 0 3px #ffffff; }
.switch-item label { color: #F2F4F8; font-size: 17px; }
.vol { color: #DDE2EB; padding: 6px 10px; border-radius: 16px; }
.vol:hover { background: rgba(255, 255, 255, 0.09); }
.menu.volume { padding: 10px 16px 10px 8px; }
.menu scale { min-width: 260px; padding: 0 8px; }
.menu scale trough { min-height: 6px; border: none; border-radius: 3px; background: rgba(255, 255, 255, 0.15); }
.menu scale highlight { border: none; border-radius: 3px; background: #F2F4F8; }
.menu scale slider { min-width: 20px; min-height: 20px; margin: -7px; border: none; border-radius: 10px; background: #ffffff; box-shadow: none; }
.menu label.vol-num { color: #F2F4F8; font-size: 16px; min-width: 40px; }
.menu button.mute { padding: 6px 10px; }
.menu button.mute:hover { background: rgba(255, 255, 255, 0.12); }
"""


SETTINGS = os.path.expanduser("~/.config/deck-tv/dock.json")


def load_offset():
    try:
        with open(SETTINGS) as f:
            return int(json.load(f).get("offset", 0))
    except (OSError, ValueError):
        return 0


def save_offset(offset):
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, "w") as f:
        json.dump({"offset": offset}, f)


class GrabProbe:
    """Asks X, from a separate connection, whether anything is holding the mouse right now.

    The dock has twice been caught holding a pointer grab it never let go of (once after Alt+Tab).
    Grabbing for a moment from another connection fails with AlreadyGrabbed while any grab is active.
    """

    def __init__(self):
        x = ctypes.CDLL("libX11.so.6")
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XGrabPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        x.XUngrabPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XFlush.argtypes = [ctypes.c_void_p]
        self.x = x
        self.dpy = x.XOpenDisplay(None)
        self.root = x.XDefaultRootWindow(self.dpy)
        x.XStringToKeysym.restype = ctypes.c_ulong
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        code = lambda name: x.XKeysymToKeycode(self.dpy, x.XStringToKeysym(name.encode())) if self.dpy else 0
        self.alt_keys = [c for c in (code("Alt_L"), code("Alt_R")) if c]
        self.esc_key = code("Escape")

    def keys_down(self, codes):
        """Is any of these keys held right now? (Reads the keyboard's state without taking it over.)"""
        if not self.dpy:
            return False
        keys = ctypes.create_string_buffer(32)
        self.x.XQueryKeymap(self.dpy, keys)
        return any(keys.raw[c // 8] >> (c % 8) & 1 for c in codes)

    def held(self):
        if not self.dpy:
            return False
        result = self.x.XGrabPointer(self.dpy, self.root, 0, 0, 1, 1, 0, 0, 0)  # async modes, CurrentTime
        if result == 0:
            self.x.XUngrabPointer(self.dpy, 0)
            self.x.XFlush(self.dpy)
        return result == 1  # AlreadyGrabbed


def api(path, method="GET", timeout=4):
    req = urllib.request.Request(API + path, method=method, headers={"X-Deck-TV": "1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def pixbuf_from(data, size=ICON):
    loader = GdkPixbuf.PixbufLoader()
    loader.connect("size-prepared", lambda l, w, h: l.set_size(size, size))
    loader.write(data)
    loader.close()
    return loader.get_pixbuf()


def fallback_icon(tile):
    """Rounded square in the tile's colour with its white line icon."""
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="{ICON}" height="{ICON}">'
        f'<rect width="48" height="48" rx="12" fill="{tile.get("color", "#333")}"/>'
        '<g transform="translate(10 10) scale(1.1667)" fill="none" stroke="#fff" stroke-width="1.7" '
        f'stroke-linecap="round" stroke-linejoin="round">{LINE_ICONS.get(tile.get("icon"), "")}</g></svg>'
    )
    return pixbuf_from(svg.encode())


class Dock(Gtk.Window):
    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)  # unmanaged: no focus, no taskbar entry, no border
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.area = self.monitor_area()
        self.get_screen().connect("monitors-changed", self.on_screen_changed)
        self.get_screen().connect("size-changed", self.on_screen_changed)
        self.away_since = None        # when the pointer last left an open dock or line
        self.probe = GrabProbe()
        self.grab_seconds = 0         # how long the mouse has been held with no button pressed
        self.held_pressed = 0         # ... and with a button pressed
        self.leave_timer = None
        self.dots = {}
        self.state = None
        self.offset = load_offset()   # px right of centre; the line and dock share it
        self.dragged = False
        self.open_ids = set()
        self.menu = None              # the right-click menu or volume slider, while it's showing
        self.volume = None            # last known {"level", "muted"}
        self.vol_pending = None       # slider value waiting to be sent
        self.playing = False          # whether the card's player is playing (play/pause icon)

        # Press and slide sideways to move the line or dock; a press that doesn't move stays a click
        self.drag = Gtk.GestureDrag.new(self)
        self.drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.drag.connect("drag-begin", self.on_drag_begin)
        self.drag.connect("drag-update", self.on_drag_update)
        self.drag.connect("drag-end", self.on_drag_end)

        self.strip = self.hover_box(Gtk.Box(), self.on_strip_enter)
        self.hint = self.build_hint()
        self.dock = self.build_dock()
        for child in (self.strip, self.hint, self.dock):
            child.connect("size-allocate", lambda w, _r: self.limit_clicks_to(w))

        self.show_hidden()
        GLib.timeout_add(1000, self.keep_on_top)
        self.was_down = False
        GLib.timeout_add(40, self.watch_clicks)  # quick enough to catch a short click
        # Alt+Tab and Alt+Shift+Tab arrive as signals from the deck-tv-switch command
        self.switcher = None
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR1, lambda: self.switch(1) or True)
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGUSR2, lambda: self.switch(-1) or True)
        GLib.timeout_add(2000, self.poll_state)

    # ---------- widgets ----------
    def hover_box(self, child, on_enter, on_leave=None):
        box = Gtk.EventBox()
        box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        box.connect("enter-notify-event", lambda *_: on_enter())
        if on_leave:
            box.connect("leave-notify-event", lambda w, e: e.detail != Gdk.NotifyType.INFERIOR and on_leave())
        box.add(child)
        return box

    def build_hint(self):
        # The line is thin, but the whole area around it takes the click
        line = Gtk.Box()
        line.get_style_context().add_class("bloom")
        line.set_halign(Gtk.Align.CENTER)
        line.set_valign(Gtk.Align.END)
        line.set_margin_bottom(14)
        ctx = line.get_style_context()

        def enter():
            self.cancel_leave()
            ctx.add_class("hot")

        def leave():
            ctx.remove_class("hot")
            self.leave_after(1200)

        area = self.hover_box(line, enter, leave)
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK)
        area.connect("button-release-event", lambda *_: self.dragged or self.request(self.show_dock))
        return area

    def build_dock(self):
        cfg = json.loads(api("/api/config"))
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        bar.get_style_context().add_class("dock")

        # Player controls, left of the volume: click for previous / play-pause / next
        player = Gtk.Button()
        player.add(Gtk.Image.new_from_icon_name("media-playlist-play-symbolic", Gtk.IconSize.DND))
        player.set_relief(Gtk.ReliefStyle.NONE)
        player.get_style_context().add_class("vol")
        player.set_tooltip_text("Player controls")
        player.set_valign(Gtk.Align.CENTER)
        player.connect("clicked", lambda b: self.hide_menu() if self.menu else self.show_player(b))
        bar.pack_start(player, False, False, 0)

        # Volume, always within reach: click for a slider, scroll to nudge it
        self.vol_img = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic", Gtk.IconSize.DND)
        vol = Gtk.Button()
        vol.add(self.vol_img)
        vol.set_relief(Gtk.ReliefStyle.NONE)
        vol.get_style_context().add_class("vol")
        vol.set_tooltip_text("Volume")
        vol.set_valign(Gtk.Align.CENTER)
        vol.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        vol.connect("clicked", lambda b: self.hide_menu() if self.menu else self.show_volume(b))
        vol.connect("scroll-event", self.on_volume_scroll)
        bar.pack_start(vol, False, False, 0)

        sep = Gtk.Box()
        sep.get_style_context().add_class("sep")
        bar.pack_start(sep, False, False, 0)

        try:
            home = GdkPixbuf.Pixbuf.new_from_file_at_size(HOME_ICON, ICON, ICON)
            big_home = GdkPixbuf.Pixbuf.new_from_file_at_size(HOME_ICON, SWITCH_ICON, SWITCH_ICON)
        except GLib.Error:
            home = Gtk.IconTheme.get_default().load_icon("video-television", ICON, 0)
            big_home = Gtk.IconTheme.get_default().load_icon("video-television", SWITCH_ICON, 0)
        bar.pack_start(self.item("home", "Deck TV", home, self.go_home), False, False, 0)
        self.switch_icons = {"home": ("Deck TV", big_home)}  # the Alt+Tab switcher's icons, larger
        self.apps = {}
        for t in cfg["tiles"]:
            if t.get("userGroup") or any(c.get("tile") for c in t.get("choices", [])):
                continue  # a group tile (Live TV) isn't an app; its apps have their own icons
            try:
                data = api(f"/appicon/{t['id']}") if t.get("appIcon") else None
                pix = pixbuf_from(data) if data else fallback_icon(t)
                big = pixbuf_from(data, SWITCH_ICON) if data else pix.scale_simple(SWITCH_ICON, SWITCH_ICON, GdkPixbuf.InterpType.BILINEAR)
            except Exception:
                pix = fallback_icon(t)
                big = pix.scale_simple(SWITCH_ICON, SWITCH_ICON, GdkPixbuf.InterpType.BILINEAR)
            self.switch_icons[t["id"]] = (t["name"], big)
            btn = self.item(t["id"], t["name"], pix, lambda tid=t["id"]: self.launch(tid))
            btn.connect("button-press-event", lambda b, e, tile=t: e.button == 3 and self.show_menu(tile, b))
            btn.set_no_show_all(True)  # shown only while the app is open
            self.apps[t["id"]] = btn
            bar.pack_start(btn, False, False, 0)

        sep = Gtk.Box()
        sep.get_style_context().add_class("sep")
        bar.pack_start(sep, False, False, 0)
        hide = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        hide.set_relief(Gtk.ReliefStyle.NONE)
        hide.get_style_context().add_class("hide")
        hide.set_tooltip_text("Hide dock")
        hide.set_valign(Gtk.Align.CENTER)
        hide.connect("clicked", lambda *_: self.request(self.show_hidden))
        bar.pack_start(hide, False, False, 0)

        bar.set_margin_bottom(MARGIN)
        return bar

    def item(self, key, name, pixbuf, on_click):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        col.pack_start(Gtk.Image.new_from_pixbuf(pixbuf), False, False, 0)
        dot = Gtk.Box()
        dot.get_style_context().add_class("dot")
        dot.set_halign(Gtk.Align.CENTER)
        col.pack_start(dot, False, False, 0)
        self.dots[key] = dot
        btn = Gtk.Button()
        btn.get_style_context().add_class("item")
        btn.set_relief(Gtk.ReliefStyle.NONE)
        btn.set_tooltip_text(name)
        btn.add(col)
        btn.connect("clicked", lambda *_: on_click())
        return btn

    # ---------- states ----------
    def monitor_area(self):
        display = self.get_display()
        return (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()

    def on_screen_changed(self, *_):
        # A TV switched off or a display change can move the screen edge; start again from it
        self.area = self.monitor_area()
        self.request(self.show_hidden)

    def buttons_down(self):
        seat = self.get_display().get_default_seat()
        root = self.get_screen().get_root_window()
        _, _, _, mask = root.get_device_position(seat.get_pointer())
        return bool(mask & (Gdk.ModifierType.BUTTON1_MASK | Gdk.ModifierType.BUTTON2_MASK | Gdk.ModifierType.BUTTON3_MASK))

    def request(self, change):
        """Swap or resize the window only once no mouse button is held, and outside event handling."""
        def attempt():
            if self.buttons_down():
                return True  # try again shortly
            change()
            return False
        GLib.timeout_add(40, attempt)

    def place(self, child, w, h):
        a = self.area
        self.resize(w, h)  # size first, so the new child is never squeezed into the old window
        old = self.get_child()
        if old is not child:
            if old:
                self.remove(old)
            self.add(child)
        child.show_all()
        offset = 0 if child is self.strip else self.offset
        self.move(self.clamp_x(a.x + (a.width - w) // 2 + offset, w), a.y + a.height - h)
        self.show()
        self.away_since = None
        GLib.idle_add(self.limit_clicks_to, child)

    def limit_clicks_to(self, child):
        """Only the visible part takes clicks; the see-through rest of the window lets them pass."""
        if self.get_child() is child and self.get_window():
            r = child.get_allocation()
            self.input_shape_combine_region(cairo.Region(cairo.RectangleInt(r.x, r.y, r.width, r.height)))
        return False

    def clamp_x(self, x, w):
        return max(self.area.x, min(x, self.area.x + self.area.width - w))

    # ---------- dragging ----------
    def pointer_x(self):
        return self.get_display().get_default_seat().get_pointer().get_position()[1]

    def on_drag_begin(self, *_):
        self.dragged = False
        self.drag_from = (self.pointer_x(), self.get_position()[0])

    def on_drag_update(self, gesture, dx, dy):
        if self.state == "hidden":
            return
        start_pointer, start_x = self.drag_from
        moved = self.pointer_x() - start_pointer
        if not self.dragged:
            if abs(moved) < 10:
                return
            self.dragged = True
            self.cancel_leave()
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # the icon under the pointer won't fire
        w = self.get_size()[0]
        x = self.clamp_x(start_x + moved, w)
        self.move(x, self.get_position()[1])
        self.offset = x + w // 2 - (self.area.x + self.area.width // 2)

    def on_drag_end(self, *_):
        if self.dragged:
            save_offset(self.offset)
            self.limit_clicks_to(self.get_child())
            if self.state == "hint":
                self.leave_after(1500)  # the drag cancelled the fade; start it again

    # ---------- Alt+Tab switcher ----------
    # Home plus every open app, most recently used first, like the usual switcher. Each Alt+Tab
    # moves along one; letting go of Alt switches to the highlighted one, Escape cancels.
    def switch(self, step):
        if self.switcher is None:
            try:
                state = json.loads(api("/api/state", timeout=1))
            except Exception:
                return
            open_ids = set(state.get("open", [])) | {"home"}
            order = [k for k in state.get("recent", []) if k in open_ids and k in self.switch_icons]
            order += [k for k in self.switch_icons if k in open_ids and k not in order]
            if not order:
                return
            self.show_switcher(order)
            self.switch_at = (1 if step > 0 else -1) % len(order) if len(order) > 1 else 0
            GLib.timeout_add(30, self.watch_alt)
        else:
            self.switch_at = (self.switch_at + step) % len(self.switch_order)
        for n, box in enumerate(self.switch_boxes):
            (box.get_style_context().add_class if n == self.switch_at else box.get_style_context().remove_class)("sel")

    def show_switcher(self, order):
        win = Gtk.Window(type=Gtk.WindowType.POPUP)
        visual = win.get_screen().get_rgba_visual()
        if visual:
            win.set_visual(visual)
        win.set_app_paintable(True)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.get_style_context().add_class("switcher")
        self.switch_boxes = []
        for key in order:
            name, pix = self.switch_icons[key]
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            col.get_style_context().add_class("switch-item")
            col.pack_start(Gtk.Image.new_from_pixbuf(pix), False, False, 0)
            label = Gtk.Label.new(name)
            label.set_max_width_chars(12)
            label.set_ellipsize(3)  # end
            col.pack_start(label, False, False, 0)
            row.pack_start(col, False, False, 0)
            self.switch_boxes.append(col)
        win.add(row)
        win.show_all()
        _, nat = row.get_preferred_size()
        a = self.area
        win.move(a.x + (a.width - nat.width) // 2, a.y + (a.height - nat.height) // 2)
        self.switcher, self.switch_order = win, order

    def watch_alt(self):
        if self.switcher is None:
            return False
        if self.probe.keys_down([self.probe.esc_key]):
            self.close_switcher()
            return False
        if self.probe.keys_down(self.probe.alt_keys):
            return True  # still choosing
        key = self.switch_order[self.switch_at]
        self.close_switcher()
        self.in_background(lambda: api("/api/home" if key == "home" else f"/api/launch/{key}", "POST"))
        return False

    def close_switcher(self):
        if self.switcher is not None:
            self.switcher.destroy()
            self.switcher = None

    # ---------- right-click menu ----------
    # A window of its own rather than a Gtk.Menu: menus grab the mouse, and a grab is exactly what
    # once left the dock locking every click on screen. This one holds nothing.
    def show_popup(self, box, btn):
        """Show box in a small window centred just above btn."""
        self.hide_menu()
        self.cancel_leave()
        menu = Gtk.Window(type=Gtk.WindowType.POPUP)
        visual = menu.get_screen().get_rgba_visual()
        if visual:
            menu.set_visual(visual)
        menu.set_app_paintable(True)
        box.get_style_context().add_class("menu")
        menu.add(box)
        menu.show_all()
        _, nat = box.get_preferred_size()
        _, ox, oy = btn.get_window().get_origin()
        r = btn.get_allocation()
        x = self.clamp_x(ox + r.x + (r.width - nat.width) // 2, nat.width)
        menu.move(x, max(self.area.y, oy + r.y - nat.height - 8))
        self.menu = menu

    def show_menu(self, tile, btn):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        if tile["id"] in self.open_ids:
            close = Gtk.Button.new_with_label(f"Close {tile['name']}")
            close.set_relief(Gtk.ReliefStyle.NONE)
            close.connect("clicked", lambda *_: self.close_app(tile["id"]))
            box.pack_start(close, False, False, 0)
        else:
            note = Gtk.Label.new(f"{tile['name']} isn't open")
            note.get_style_context().add_class("note")
            box.pack_start(note, False, False, 0)
        self.show_popup(box, btn)
        return True  # the right-click stops here

    # ---------- volume ----------
    def show_volume(self, btn):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.get_style_context().add_class("volume")
        self.mute_img = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic", Gtk.IconSize.LARGE_TOOLBAR)
        mute = Gtk.Button()
        mute.add(self.mute_img)
        mute.set_relief(Gtk.ReliefStyle.NONE)
        mute.get_style_context().add_class("mute")
        mute.set_tooltip_text("Mute")
        mute.connect("clicked", lambda *_: self.volume_call("/api/volume/mute"))
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_valign(Gtk.Align.CENTER)
        self.scale_handler = self.scale.connect("value-changed", self.on_slider)
        self.vol_num = Gtk.Label.new("")
        self.vol_num.get_style_context().add_class("vol-num")
        for w in (mute, self.scale, self.vol_num):
            box.pack_start(w, False, False, 0)
        self.show_popup(box, btn)
        self.show_volume_state(self.volume)
        self.in_background(lambda: json.loads(api("/api/volume")), self.show_volume_state)

    def volume_icon(self, v):
        if not v or v["muted"] or v["level"] == 0:
            return "audio-volume-muted-symbolic"
        return "audio-volume-" + ("low" if v["level"] < 34 else "medium" if v["level"] < 67 else "high") + "-symbolic"

    def show_volume_state(self, v):
        if not v:
            return False
        self.volume = v
        icon = self.volume_icon(v)
        self.vol_img.set_from_icon_name(icon, Gtk.IconSize.DND)
        if self.menu and getattr(self, "scale", None) and self.scale.get_toplevel() is self.menu:
            self.mute_img.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            self.vol_num.set_text("Muted" if v["muted"] else str(v["level"]))
            if self.vol_pending is None:  # don't fight a slider that's being dragged
                with self.scale.handler_block(self.scale_handler):
                    self.scale.set_value(v["level"])
        return False

    def on_slider(self, scale):
        first = self.vol_pending is None
        self.vol_pending = int(scale.get_value())
        self.vol_num.set_text(str(self.vol_pending))
        if first:
            GLib.timeout_add(120, self.send_slider)  # at most a few volume changes a second

    def send_slider(self):
        level, self.vol_pending = self.vol_pending, None
        self.volume_call(f"/api/volume/set/{level}")
        return False

    def on_volume_scroll(self, _btn, event):
        # One wheel notch is one step; touchpads send many small deltas, so add them up
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            self.scroll_acc = getattr(self, "scroll_acc", 0) + event.delta_y
            if abs(self.scroll_acc) < 1:
                return True
            up, self.scroll_acc = self.scroll_acc < 0, 0
        else:
            up = event.direction == Gdk.ScrollDirection.UP
        self.volume_call("/api/volume/up" if up else "/api/volume/down")
        return True

    def volume_call(self, path):
        self.in_background(lambda: json.loads(api(path, "POST")), self.show_volume_state)

    def hide_menu(self):
        if self.menu:
            self.menu.destroy()
            self.menu = None

    def pointer_in(self, win):
        _, px, py = self.get_display().get_default_seat().get_pointer().get_position()
        x, y = win.get_position()
        w, h = win.get_size()
        return x <= px < x + w and y <= py < y + h

    def close_app(self, tile_id):
        # Tuck the dock away straight off, so a click while the app shuts down can't land on an icon
        self.hide_menu()
        self.quiet_until = time.monotonic() + 2
        self.request(self.show_hidden)
        self.in_background(lambda: api(f"/api/close/{tile_id}", "POST"))

    def show_hidden(self):
        self.hide_menu()
        self.hidden_at = time.monotonic()
        self.state = "hidden"
        self.cancel_leave()
        self.place(self.strip, self.area.width, STRIP)

    def on_strip_enter(self):
        if self.buttons_down():
            return  # e.g. dragging something in another app down to the bottom edge
        self.state = "hint"
        self.moved_at = time.monotonic()
        self.place(self.hint, HINT_W, HINT_H)
        self.leave_after(1500)  # in case the pointer never touches the line

    def show_dock(self):
        self.moved_at = time.monotonic()
        self.in_background(lambda: json.loads(api("/api/volume")), self.show_volume_state)
        self.state = "dock"
        self.cancel_leave()
        try:  # know what's open before sizing the dock, so it opens at the right width
            self.show_open(json.loads(api("/api/state", timeout=1)), relayout=False)
        except Exception:
            pass
        self.dock.show_all()
        _, nat = self.dock.get_preferred_size()
        self.place(self.dock, nat.width, nat.height)
        self.poll_state()

    def leave_after(self, ms):
        self.cancel_leave()
        self.leave_timer = GLib.timeout_add(ms, self._leave)

    def cancel_leave(self):
        if self.leave_timer:
            GLib.source_remove(self.leave_timer)
            self.leave_timer = None

    def _leave(self):
        self.leave_timer = None
        if self.state == "hint":
            self.show_hidden()
        return False

    # ---------- actions ----------
    def in_background(self, fn, then=None):
        def run():
            try:
                result = fn()
            except Exception:
                result = None
            if then:
                GLib.idle_add(then, result)
        threading.Thread(target=run, daemon=True).start()

    def launch(self, tile_id):
        if time.monotonic() < getattr(self, "quiet_until", 0):
            return  # just closed something: ignore clicks for a moment
        self.hide_menu()
        self.request(self.show_hidden)
        self.in_background(lambda: api(f"/api/launch/{tile_id}", "POST"))

    def show_player(self, btn):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for action, icon, tip in (("prev", "media-skip-backward-symbolic", "Previous"),
                                  ("play", "media-playback-start-symbolic", "Play/pause"),
                                  ("next", "media-skip-forward-symbolic", "Next")):
            img = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)
            b = Gtk.Button()
            b.add(img)
            b.set_relief(Gtk.ReliefStyle.NONE)
            b.get_style_context().add_class("mute")
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, a=action: self.media(a))
            box.pack_start(b, False, False, 0)
            if action == "play":
                self.play_img = img
        self.show_popup(box, btn)
        self.show_playing(self.playing)

    def media(self, action):
        if action == "play":  # answer the click at once; the next poll corrects it if need be
            self.show_playing(not self.playing)
        self.in_background(lambda: api(f"/api/media/{action}", "POST"))

    def show_playing(self, playing):
        self.playing = playing
        img = getattr(self, "play_img", None)
        if self.menu and img and img.get_toplevel() is self.menu:  # only while the controls are showing
            icon = "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
            img.set_from_icon_name(icon, Gtk.IconSize.LARGE_TOOLBAR)

    def go_home(self):
        self.hide_menu()
        self.request(self.show_hidden)
        self.in_background(lambda: api("/api/home", "POST"))

    def poll_state(self):
        # One question at a time: if the server is slow, don't pile more requests on top
        if self.state == "dock" and not getattr(self, "polling", False):
            self.polling = True

            def done(state):
                self.polling = False
                return self.show_open(state)
            self.in_background(lambda: json.loads(api("/api/state")), done)
        return True

    def show_open(self, state, relayout=True):
        if not state:
            return False
        open_ids = set(state.get("open", [])) | ({"home"} if state.get("home") else set())
        self.open_ids = open_ids
        self.show_playing(bool((state.get("media") or {}).get("playing")))
        for key, dot in self.dots.items():
            ctx = dot.get_style_context()
            (ctx.add_class if key in open_ids else ctx.remove_class)("open")
        changed = False
        for key, btn in self.apps.items():
            if btn.get_visible() != (key in open_ids):
                if key in open_ids:
                    btn.get_child().show_all()  # the icon inside: show_all() skips buttons kept out of it
                    btn.show()
                else:
                    btn.hide()
                changed = True
        if changed and relayout and self.state == "dock":
            self.request(self.fit_dock)  # an app opened or closed: grow or shrink to match
        return False

    def fit_dock(self):
        if self.state == "dock":
            _, nat = self.dock.get_preferred_size()
            self.place(self.dock, nat.width, nat.height)

    def restart(self):
        """Ending this process always frees any grab it holds; start a fresh dock in its place.
        (The lock file closes on exec, so the new one starts normally.)"""
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])

    def release_mouse(self):
        self.get_display().get_default_seat().ungrab()
        Gdk.pointer_ungrab(Gdk.CURRENT_TIME)
        self.grab_remove()

    def watch_clicks(self):
        """Clicking anywhere outside the open dock (or its line, or a popup) tucks it away.
        The dock holds no grab, so it can't be told about clicks elsewhere; it watches the mouse
        buttons instead and acts on the moment one goes down."""
        if self.state == "hidden" and self.menu is None:
            self.was_down = False
            return True  # nothing open: nothing to watch
        try:
            down = self.buttons_down()
            if down and not self.was_down and self.state in ("hint", "dock"):
                inside = self.pointer_in(self) or (self.menu is not None and self.pointer_in(self.menu))
                if not inside:
                    self.hide_menu()
                    self.request(self.show_hidden)  # waits for the button to come back up
            elif down and not self.was_down and self.menu is not None and not self.pointer_in(self.menu) \
                    and not self.pointer_in(self):
                self.hide_menu()
            self.was_down = down
        except Exception as e:
            print("dock click watch failed:", repr(e), file=sys.stderr)
        return True

    def keep_on_top(self):
        """Once a second: stay above full-screen apps, and make sure the dock can never get stuck.
        An error must never stop this check (GLib drops a callback that raises), so it's caught."""
        try:
            self.check()
        except Exception as e:
            print("dock check failed:", repr(e), file=sys.stderr)
        return True

    def mouse_held(self):
        """Is something holding the mouse? Asking X means grabbing it for an instant, and every app
        sees that as the mouse leaving and coming back: enough to keep Spotify's or a video
        player's controls on screen. So X is only asked while the dock is in use (or just after),
        the only time the dock could be the one holding it; otherwise GTK says whether it is."""
        if self.state != "hidden" or self.menu is not None or time.monotonic() - self.hidden_at < 10:
            return self.probe.held()
        display = self.get_display()
        return display.device_is_grabbed(display.get_default_seat().get_pointer())

    def check(self):
        win = self.get_window()
        if not win:
            return True
        win.raise_()
        if self.menu:
            self.menu.get_window().raise_()
        if self.switcher is not None:
            self.switcher.get_window().raise_()
        if self.buttons_down():
            # A press can legitimately hold the mouse; only a very long one (or a button X thinks is
            # stuck down) counts, and then only towards a restart
            self.held_pressed = self.held_pressed + 1 if self.mouse_held() else 0
            if self.held_pressed >= 8:
                self.restart()
            return True
        self.held_pressed = 0
        # Never keep hold of the mouse once no button is pressed
        self.release_mouse()
        if self.mouse_held():
            self.grab_seconds += 1
            if self.grab_seconds == 2:
                self.show_hidden()
            elif self.grab_seconds >= 4:
                self.restart()
        else:
            self.grab_seconds = 0
        if self.state in ("hint", "dock"):
            # Tuck away an open dock (or a line) the pointer has left alone for a while
            in_menu = self.menu is not None and self.pointer_in(self.menu)
            if self.menu and not in_menu and not self.pointer_in(self):
                self.hide_menu()  # pointer wandered off: the menu goes, the dock stays a while
            # ... and one left sitting untouched, even with the pointer resting on it
            pointer = self.get_display().get_default_seat().get_pointer().get_position()[1:]
            if pointer != getattr(self, "last_pointer", None) or self.menu is not None:
                self.last_pointer, self.moved_at = pointer, time.monotonic()
            if time.monotonic() - getattr(self, "moved_at", time.monotonic()) > DOCK_IDLE:
                self.show_hidden()
                return True
            if in_menu or self.pointer_in(self):
                self.away_since = None
            elif self.away_since is None:
                self.away_since = time.monotonic()
            elif time.monotonic() - self.away_since > (DOCK_AWAY if self.state == "dock" else 3):
                self.show_hidden()
        return True


def main():
    path = os.path.expanduser("~/.cache/deck-tv-dock.lock")
    if "--restart" in sys.argv:
        # Recovery: Home (Meta+H) restarts the dock, so a misbehaving one is never more than a key away
        try:
            with open(path) as f:
                os.kill(int(f.read().strip()), signal.SIGTERM)
            time.sleep(0.3)
        except (OSError, ValueError):
            pass
    lock = open(path, "a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit(0)  # already running
    lock.seek(0)
    lock.truncate()
    lock.write(str(os.getpid()))
    lock.flush()

    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    for _ in range(30):  # the server may still be starting
        try:
            api("/api/config")
            break
        except Exception:
            GLib.usleep(500_000)
    Dock()
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, lambda: Gtk.main_quit() or False)
    alt_tab("claim")
    try:
        Gtk.main()
    finally:
        alt_tab("release")  # with Deck TV closed, Alt+Tab is KDE's own switcher again


def alt_tab(how):
    """Take Alt+Tab from KDE ("claim") or give it back ("release"); see install.sh."""
    try:
        subprocess.run([os.path.expanduser("~/.local/bin/deck-tv-alttab"), how], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


if __name__ == "__main__":
    main()
