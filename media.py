"""Now playing: one media player at a time, across every Deck TV app.

Two kinds of player:
- Desktop apps (Spotify) announce themselves on the session bus (MPRIS) and are matched to
  their tile by name.
- The Chrome apps (Movy, YouTube, UFC) are read directly through their DevTools port. Their
  MPRIS players can't be trusted: every flatpak sandbox gives Chrome the same process id, so
  they all claim the same bus name and only the first app to play gets it.

Only one player is ever "the" player: the one that most recently started playing, or failing
that the one most recently paused. Starting another app's playback pauses the rest, like a TV
box, so the card, the controls and the phone remote all agree on a single source.
"""
import hashlib
import re
import subprocess
import threading
import time
from pathlib import Path

import dbus

from cdp import Page, targets

MPRIS = "org.mpris.MediaPlayer2."
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPS = "org.freedesktop.DBus.Properties"
PATH = "/org/mpris/MediaPlayer2"
PAUSED_FOR = 15 * 60   # a paused player stays on the card this long
SKIP = 10              # seconds the Chrome apps' back/forward buttons jump
SOCKET_LINE = re.compile(r'\s(\d+)\s+\S+\s+(\d+)\s+users:\(\("([^"]+)",pid=(\d+)')

# The main video or audio on a page: the one playing, else the longest. Muted autoplay
# previews (trailers behind a title page) don't count as something you're watching.
PICK_JS = """const pick = () => {
  const els = [...document.querySelectorAll('video, audio')].filter(v => v.duration > 0 && !v.muted && v.volume > 0);
  return els.find(v => !v.paused && !v.ended) || els.sort((a, b) => (b.duration || 0) - (a.duration || 0))[0];
};"""
READ_JS = "(() => {" + PICK_JS + """
  const v = pick(); if (!v) return null;
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  const og = document.querySelector('meta[property="og:image"]');
  // No artwork given: the biggest picture on the page is usually the poster
  const big = [...document.images].filter(i => i.src.startsWith('https://') && !/\\.svg(\\?|$)/.test(i.src) && i.naturalWidth >= 300)
    .sort((x, y) => y.naturalWidth * y.naturalHeight - x.naturalWidth * x.naturalHeight)[0];
  const art = m && m.artwork && m.artwork.length ? m.artwork[m.artwork.length - 1].src
    : (v.poster || (og && og.content) || (big && big.src) || '');
  return {playing: !v.paused && !v.ended, position: v.currentTime, length: isFinite(v.duration) ? v.duration : 0,
          title: (m && m.title) || document.title, sub: m ? [m.artist, m.album].filter(Boolean).join(' · ') : '', art};
})()"""
CONTROL_JS = "(() => {" + PICK_JS + """
  const v = pick(); if (!v) return false;
  const a = %r;
  if (a === 'play') v.paused ? v.play() : v.pause();
  if (a === 'pause') v.pause();
  if (a === 'next') v.currentTime = Math.min(v.duration, v.currentTime + %d);
  if (a === 'prev') v.currentTime = Math.max(0, v.currentTime - %d);
  return true;
})()"""


def cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def parent(pid):
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return 0


def window_class(pid):
    """The --class an app was started with, from the process or its parents."""
    for _ in range(8):
        for arg in cmdline(pid):
            if arg.startswith(b"--class="):
                return arg[8:].decode().lower()
        pid = parent(pid)
        if pid <= 1:
            break
    return None


def proxy_client(proxy_pid):
    """The process on the far side of a flatpak D-Bus proxy (the app it proxies for)."""
    out = subprocess.run(["ss", "-xpn"], capture_output=True, text=True, timeout=3).stdout
    owner, peers = {}, []
    for line in out.splitlines():
        m = SOCKET_LINE.search(line)
        if not m:
            continue
        local, peer, _comm, pid = m.groups()
        owner[local] = int(pid)
        if int(pid) == proxy_pid and "/.dbus-proxy/" in line:  # accepted from the app, not its link to the bus
            peers.append(peer)
    return next((owner[p] for p in peers if p in owner and owner[p] != proxy_pid), None)


def tidy_title(title, app):
    return re.sub(rf"\s*[|\-–—]\s*{re.escape(app)}\s*$", "", title, flags=re.I).strip() or app


class Media:
    def __init__(self, tiles):
        self.tiles = tiles          # callable returning the configured tiles
        self.apps = {}              # bus unique name -> (tile id or None, app pid)
        self.status = {}            # player key -> last status seen
        self.since = {}             # player key -> when it last started or stopped playing
        self.seen = {}              # player key -> when it was last read
        self.current = None         # what the card shows, or None
        self.focused = None         # player key chosen from a tile's menu, until another starts playing
        self.available = []         # tiles that have a player right now: [{"tile", "playing"}]
        self.art = None             # artwork file of the current player, if it's a local file
        self.lock = threading.Lock()
        self.leaving = {}           # tile id -> when it was closed; left alone while it shuts down

    # ---------- desktop apps, over the session bus ----------
    def app_of(self, bus, name):
        unique = bus.get_name_owner(name)
        if unique in self.apps:
            return self.apps[unique]
        dbus_iface = dbus.Interface(bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"), "org.freedesktop.DBus")
        pid = int(dbus_iface.GetConnectionUnixProcessID(unique))
        comm_file = Path(f"/proc/{pid}/comm")
        if comm_file.exists() and comm_file.read_text().strip() == "xdg-dbus-proxy":
            pid = proxy_client(pid) or pid
        cls = window_class(pid)
        entry = name[len(MPRIS):].split(".")[0].lower()   # e.g. "spotify", "chromium"
        tile = next((t["id"] for t in self.tiles() if t.get("window_class", "").lower() in (cls, entry)), None)
        self.apps[unique] = (tile, pid)
        return self.apps[unique]

    def bus_players(self, bus, tiles):
        found = []
        for name in bus.list_names():
            if not name.startswith(MPRIS):
                continue
            try:
                tile_id, pid = self.app_of(bus, name)
                tile = tiles.get(tile_id)
                if not tile or tile.get("devtools_port") or self.gone(tile_id):
                    continue  # not ours, or a Chrome app (read through DevTools instead)
                props = dbus.Interface(bus.get_object(name, PATH), PROPS).GetAll(PLAYER)
            except dbus.DBusException:
                continue
            meta = props.get("Metadata", {})
            artists = [str(a) for a in meta.get("xesam:artist", []) if str(a)]
            art_url = str(meta.get("mpris:artUrl", ""))
            if art_url.startswith("file://"):
                art = Path(f"/proc/{pid}/root") / art_url[7:].lstrip("/")  # inside the app's sandbox
                art = str(art) if art.is_file() else None
            else:
                art = art_url if art_url.startswith("https://") else None
            length = int(meta.get("mpris:length", 0) or 0) / 1e6
            found.append({
                "key": name, "tile": tile, "status": str(props.get("PlaybackStatus", "Stopped")),
                "title": str(meta.get("xesam:title", "")), "art": art, "length": length,
                "sub": " · ".join(x for x in (", ".join(artists), str(meta.get("xesam:album", ""))) if x),
                "position": int(props.get("Position", 0) or 0) / 1e6 if length else 0,
                "canNext": bool(props.get("CanGoNext", False)), "canPrev": bool(props.get("CanGoPrevious", False)),
                "seek": False,
            })
        return found

    # ---------- Chrome apps, over DevTools ----------
    def chrome_players(self, tiles):
        found = []
        for tile in tiles.values():
            port = tile.get("devtools_port")
            if not port or self.gone(tile["id"]):
                continue
            try:
                frames = targets(port, ("page", "iframe"))
            except OSError:
                continue  # app not running
            best = None
            for target in frames:
                try:
                    with Page(port, target, timeout=1.5) as page:  # a busy or dying app mustn't hold things up
                        got = page.eval(READ_JS)
                except Exception:
                    continue
                if got and (best is None or (got["playing"] and not best[0]["playing"])):
                    best = (got, target["id"])
            if not best:
                continue
            got, target_id = best
            found.append({
                "key": "chrome:" + tile["id"], "tile": tile, "target": target_id,
                "status": "Playing" if got["playing"] else "Paused",
                "title": tidy_title(got["title"], tile["name"]), "sub": got["sub"],
                "art": got["art"] if str(got["art"]).startswith("https://") else None,
                "length": got["length"], "position": got["position"] if got["length"] else 0,
                "canNext": bool(got["length"]), "canPrev": bool(got["length"]), "seek": True,
            })
        return found

    def chrome_control(self, tile, target_id, action):
        port = tile["devtools_port"]
        target = next((t for t in targets(port, ("page", "iframe")) if t["id"] == target_id), None)
        if not target:
            return False
        with Page(port, target) as page:
            return bool(page.eval(CONTROL_JS % (action, SKIP, SKIP), gesture=True))

    # ---------- watching ----------
    def run(self):
        bus = dbus.SessionBus()
        first = True
        while True:
            try:
                self.poll(bus, first)
                first = False
            except dbus.DBusException:
                bus = dbus.SessionBus(private=True)  # the bus connection dropped; start again
            except Exception as e:
                print("media poll failed:", repr(e))
            time.sleep(1)

    def poll(self, bus, first):
        now = time.time()
        tiles = {t["id"]: t for t in self.tiles()}
        players = self.bus_players(bus, tiles) + self.chrome_players(tiles)
        for p in players:
            was = self.status.get(p["key"])
            if (p["status"] == "Playing") != (was == "Playing"):
                self.since[p["key"]] = now
            p["new"] = p["status"] == "Playing" and was != "Playing"
            self.status[p["key"]] = p["status"]
            self.seen[p["key"]] = now
        # A player that can't be read for a moment (a page loading, a busy app) keeps its history;
        # only one that's been gone a minute is forgotten
        for gone in [k for k, t in self.seen.items() if now - t > 60]:
            for d in (self.status, self.since, self.seen):
                d.pop(gone, None)

        # One source at a time: a player that just started pauses the others
        newest = [p for p in players if p["new"]]
        if any(p["key"] != self.focused for p in newest):
            self.focused = None  # something else started: it takes over, as it always does
        if newest and not first:
            keep = max(newest, key=lambda p: self.since[p["key"]])["key"]
            for p in players:
                if p["status"] == "Playing" and p["key"] != keep:
                    try:
                        self.send(bus, p, "pause")
                        p["status"] = "Paused"
                    except Exception:
                        pass

        playing = [p for p in players if p["status"] == "Playing"]
        paused = [p for p in players if p["status"] == "Paused" and now - self.since.get(p["key"], 0) < PAUSED_FOR]
        pick = max(playing or paused, key=lambda p: self.since.get(p["key"], 0), default=None)
        pick = next((p for p in players if p["key"] == self.focused), pick)
        with self.lock:
            self.available = [{"key": p["key"], "tile": p["tile"]["id"], "target": p.get("target"),
                               "playing": p["status"] == "Playing"} for p in players]
            self.current, self.art = self.describe(pick) if pick else (None, None)

    def describe(self, p):
        tile, art = p["tile"], p["art"]
        info = {
            "player": p["key"], "target": p.get("target"),
            "tile": tile["id"], "app": tile["name"], "color": tile.get("color"), "accent": tile.get("accent"),
            "playing": p["status"] == "Playing", "title": p["title"] or tile["name"], "sub": p["sub"],
            "length": round(p["length"], 1), "position": round(p["position"], 1),
            "canNext": p["canNext"], "canPrev": p["canPrev"], "seek": p["seek"], "art": None,
        }
        if art and art.startswith("https://"):
            info["art"], art = art, None
        elif art:
            info["art"] = "/media/art?v=" + hashlib.sha1((art + str(Path(art).stat().st_mtime)).encode()).hexdigest()[:10]
        return info, art

    # ---------- reading and controlling ----------
    def now_playing(self):
        with self.lock:
            return dict(self.current) if self.current else None

    def closing(self, tile_id):
        """This app is being closed: stop reading or pausing it while it shuts down."""
        self.leaving[tile_id] = time.time()
        if self.focused and self.focused.endswith(":" + tile_id):
            self.focused = None

    def gone(self, tile_id):
        return time.time() - self.leaving.get(tile_id, 0) < 8

    def players(self):
        with self.lock:
            return [{"tile": p["tile"], "playing": p["playing"]} for p in self.available]

    def focus(self, tile_id):
        """Make this app's player the one on the card (and the one the controls act on)."""
        with self.lock:
            key = next((p["key"] for p in self.available if p["tile"] == tile_id), None)
        if not key:
            return False
        self.focused = key
        return True

    def art_file(self):
        with self.lock:
            return self.art

    def send(self, bus, p, action):
        if p["key"].startswith("chrome:"):
            return self.chrome_control(p["tile"], p["target"], action)
        method = {"play": "PlayPause", "pause": "Pause", "next": "Next", "prev": "Previous"}[action]
        getattr(dbus.Interface(bus.get_object(p["key"], PATH), PLAYER), method)()
        return True

    def control(self, action, prefer=None):
        """play (toggle), next or prev. Acts on the player of the app `prefer` (the one on screen)
        if it has one, which then becomes the card's player too; otherwise on the card's player.
        Returns False if there's no player at all."""
        with self.lock:
            chosen = next((p for p in self.available if p["tile"] == prefer), None)
        if chosen:
            self.focused = chosen["key"]
            key, tile_id, target = chosen["key"], chosen["tile"], chosen["target"]
        else:
            cur = self.now_playing()
            if not cur:
                return False
            key, tile_id, target = cur["player"], cur["tile"], cur["target"]
        tile = next((t for t in self.tiles() if t["id"] == tile_id), None)
        bus = dbus.SessionBus(private=True)  # its own connection: this runs on a request thread
        try:
            return self.send(bus, {"key": key, "tile": tile, "target": target}, action)
        finally:
            bus.close()
