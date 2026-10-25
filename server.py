#!/usr/bin/env python3
"""Deck TV - local launcher server for the Shelf dashboard.

Serves web/ on 127.0.0.1 and runs only the commands defined in tiles.json.
"""
import ctypes
import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import threading
import urllib.parse
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import media
import search
from cdp import Page

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "tiles.json"
PORT = int(os.environ.get("DECK_TV_PORT", "8765"))
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def find_icon(tile):
    """Largest existing file matching the tile's app_icon globs (the app's own installed icon)."""
    best = None
    for pattern in tile.get("app_icon", []):
        expanded = os.path.expanduser(pattern)
        base = Path(expanded).anchor or "/"
        for p in Path(base).glob(os.path.relpath(expanded, base)):
            if p.is_file() and (best is None or p.stat().st_size > best.stat().st_size):
                best = p
    return best


def home_paths(arg):
    """tiles.json says ~/ for the home folder, even inside an option like --user-data-dir=~/..."""
    return arg.replace("=~/", f"={Path.home()}/") if arg.startswith("-") else os.path.expanduser(arg)


def installed(tile):
    """Is the tile's app on this Deck? Flatpak apps are checked by their install folder, and a tile
    can list other files it needs ("requires", any one of them). Missing apps are left off screen."""
    cmd = tile.get("command", [])
    if len(cmd) > 2 and cmd[:2] == ["flatpak", "run"]:
        app = next(a for a in cmd[2:] if not a.startswith("-"))
        if not any(Path(d, app).exists() for d in (Path.home() / ".local/share/flatpak/app", "/var/lib/flatpak/app")):
            return False
    needs = tile.get("requires")
    if needs:
        return any(Path(p).exists() for n in needs for p in Path("/").glob(os.path.expanduser(n).lstrip("/")))
    return True


def spawn(cmd):
    """Start a command detached from the server."""
    subprocess.Popen(
        [home_paths(a) for a in cmd] if isinstance(cmd, list) else shlex.split(cmd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run(cmd):
    try:
        out = subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=3)
        return out.returncode, out.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def get_volume():
    code, out = run("wpctl get-volume @DEFAULT_AUDIO_SINK@")
    if code == 0:
        m = re.search(r"([\d.]+)", out)
        if m:
            return {"level": round(float(m.group(1)) * 100), "muted": "MUTED" in out}
    code, out = run("pactl get-sink-volume @DEFAULT_SINK@")
    m = re.search(r"(\d+)%", out)
    level = int(m.group(1)) if m else 0
    _, mute = run("pactl get-sink-mute @DEFAULT_SINK@")
    return {"level": level, "muted": "yes" in mute}


def change_volume(step):
    sign = "+" if step > 0 else "-"
    code, _ = run(f"wpctl set-volume -l 1.0 @DEFAULT_AUDIO_SINK@ {abs(step)}%{sign}")
    if code != 0:
        run(f"pactl set-sink-volume @DEFAULT_SINK@ {sign}{abs(step)}%")
    return get_volume()


def set_volume(level):
    level = max(0, min(100, int(level)))
    code, _ = run(f"wpctl set-volume @DEFAULT_AUDIO_SINK@ {level}%")
    if code != 0:
        run(f"pactl set-sink-volume @DEFAULT_SINK@ {level}%")
    return get_volume()


def toggle_mute():
    code, _ = run("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle")
    if code != 0:
        run("pactl set-sink-mute @DEFAULT_SINK@ toggle")
    return get_volume()


# ---------- audio output: the Deck's speakers, the TV, headphones, Bluetooth speakers ----------
audio_connecting = {"mac": None, "error": None}  # a Bluetooth speaker being connected, in the background


def audio_outputs():
    """Where sound can go: every output PipeWire offers, plus paired Bluetooth speakers that
    aren't connected yet (choosing one connects it)."""
    _, default = run("pactl get-default-sink")
    _, out = run("pactl -f json list sinks")
    try:
        sinks = json.loads(out or "[]")
    except ValueError:
        sinks = []
    outputs = []
    for sink in sinks:
        name, desc, props = sink["name"], sink.get("description", ""), sink.get("properties", {})
        ports = sink.get("ports", [])
        if ports and all(p.get("availability") == "not available" for p in ports):
            continue  # nothing plugged in there (headphones, mostly)
        bus = props.get("device.bus", "")
        if "bluez" in name or bus == "bluetooth":
            kind, label = "bluetooth", props.get("device.description") or desc
        elif "hdmi" in name.lower():
            kind, label = "tv", "TV"
        elif "Headphones" in name:
            kind, label = "headphones", "Headphones"
        elif "Speaker" in name:
            kind, label = "speaker", "Steam Deck speakers"
        else:
            kind, label = ("usb" if bus == "usb" else "other"), desc
        outputs.append({"id": name, "name": label, "kind": kind, "active": name == default.strip()})
    _, paired = run("bluetoothctl devices Paired")
    for mac, dev in re.findall(r"^Device (\S+) (.+)$", paired, re.M):
        _, info = run(f"bluetoothctl info {mac}")
        if "Audio Sink" in info and "Connected: no" in info:
            outputs.append({"id": "bt:" + mac, "name": dev.strip(), "kind": "bluetooth", "active": False,
                            "connect": True, "connecting": audio_connecting["mac"] == mac})
    return {"outputs": outputs, "error": audio_connecting["error"]}


def use_sink(name):
    """Make this the output, and move anything already playing over to it."""
    run(f"pactl set-default-sink {shlex.quote(name)}")
    _, out = run("pactl -f json list sink-inputs")
    try:
        for stream in json.loads(out or "[]"):
            run(f"pactl move-sink-input {stream['index']} {shlex.quote(name)}")
    except ValueError:
        pass


def connect_speaker(mac):
    """Connect a paired Bluetooth speaker, then send sound to it once it appears."""
    audio_connecting.update(mac=mac, error=None)
    try:
        _, show = run("bluetoothctl show")
        if "Powered: no" in show:
            run("bluetoothctl power on")
        try:
            subprocess.run(["bluetoothctl", "connect", mac], capture_output=True, timeout=20)
        except subprocess.SubprocessError:
            pass
        want = "bluez_output." + mac.replace(":", "_")
        for _ in range(20):
            _, sinks = run("pactl list short sinks")
            found = next((line.split()[1] for line in sinks.splitlines() if want in line), None)
            if found:
                use_sink(found)
                return
            time.sleep(0.5)
        audio_connecting["error"] = "Couldn't connect. Is the speaker on and nearby?"
    finally:
        audio_connecting["mac"] = None


# ---------- adding a new Bluetooth speaker ----------
speaker_search = {"until": 0.0, "pairing": None, "error": None}
_device_info = {}  # mac -> (name, is_audio, paired), looked up once per device


def device_info(mac):
    if mac not in _device_info or not _device_info[mac][1]:
        _, info = run(f"bluetoothctl info {mac}")
        name = re.search(r"^\s*Name: (.+)$", info, re.M)
        cls = re.search(r"^\s*Class: (0x[0-9a-fA-F]+)", info, re.M)
        icon = re.search(r"^\s*Icon: (\S+)", info, re.M)
        audio = bool(icon and icon.group(1).startswith("audio")) or "Audio Sink" in info or \
            bool(cls and (int(cls.group(1), 16) >> 8) & 0x1F == 0x04)  # major class: audio/video
        _device_info[mac] = (name.group(1).strip() if name else None, audio, "Paired: yes" in info)
    return _device_info[mac]


def search_speakers():
    """Look for speakers in pairing mode for 30 seconds (Bluetooth turned on if it's off)."""
    if time.time() < speaker_search["until"]:
        return {"ok": True}
    _, show = run("bluetoothctl show")
    if "Powered: no" in show:
        run("bluetoothctl power on")
    speaker_search.update(until=time.time() + 30, error=None)
    subprocess.Popen(["bluetoothctl", "--timeout", "30", "scan", "on"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}


def speakers_found():
    _, out = run("bluetoothctl devices")
    found = []
    for mac, _label in re.findall(r"^Device (\S+) (.+)$", out, re.M):
        name, audio, paired = device_info(mac)
        if name and audio and not paired:
            found.append({"mac": mac, "name": name})
    return {"searching": time.time() < speaker_search["until"], "devices": found,
            "pairing": speaker_search["pairing"], "error": speaker_search["error"]}


def pair_speaker(mac):
    """Pair, trust (so it reconnects by itself next time) and connect a new speaker, then play on it."""
    speaker_search.update(pairing=mac, error=None)
    try:
        run("bluetoothctl scan off")
        try:
            result = subprocess.run(["bluetoothctl", "--agent", "NoInputNoOutput", "--timeout", "25", "pair", mac],
                                    capture_output=True, text=True, timeout=30)
            paired = "Pairing successful" in result.stdout or "Paired: yes" in run(f"bluetoothctl info {mac}")[1]
        except subprocess.SubprocessError:
            paired = False
        if not paired:
            speaker_search["error"] = "Couldn't pair. Make sure the speaker is in pairing mode and try again."
            return
        run(f"bluetoothctl trust {mac}")
        _device_info.pop(mac, None)
        connect_speaker(mac)
        if audio_connecting["error"]:
            speaker_search["error"] = audio_connecting["error"]
    finally:
        speaker_search["pairing"] = None
        speaker_search["until"] = 0


def start_pairing(mac):
    if not re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", mac, re.I):
        return {"error": "bad device"}
    if not speaker_search["pairing"]:
        threading.Thread(target=pair_speaker, args=(mac,), daemon=True).start()
    return {"ok": True}


def set_output(output_id):
    if output_id.startswith("bt:"):
        if not audio_connecting["mac"]:
            threading.Thread(target=connect_speaker, args=(output_id[3:],), daemon=True).start()
        return {"ok": True, "connecting": True}
    if output_id not in {o["id"] for o in audio_outputs()["outputs"]}:
        return {"error": "unknown output"}
    use_sink(output_id)
    return {"ok": True}


HOME_CLASS = "deck-tv"


_windows = {"at": 0.0, "list": []}
_windows_lock = threading.Lock()
_classes = {}  # window id -> class; a window's class doesn't change, so look it up once


def client_windows():
    """Managed X11 windows as (id, lower-case WM_CLASS class), via the window manager's client list.
    Shared for half a second: the page, the dock, the phone and the watcher all ask at once, and
    each look used to start an xprop for every window."""
    with _windows_lock:
        if time.time() - _windows["at"] < 0.5:
            return list(_windows["list"])
        _, out = run("xprop -root _NET_CLIENT_LIST")
        ids = re.findall(r"0x[0-9a-f]+", out)
        wins = []
        for wid in ids:
            if wid not in _classes:
                _, cls = run(f"xprop -id {wid} WM_CLASS")
                names = re.findall(r'"([^"]*)"', cls)
                _classes[wid] = names[-1].lower() if names else None
            if _classes[wid]:
                wins.append((wid, _classes[wid]))
        for gone in set(_classes) - set(ids):
            _classes.pop(gone, None)
        _windows.update(at=time.time(), list=wins)
        return list(wins)


def forget_windows():
    _windows["at"] = 0.0


def active_window():
    _, out = run("xprop -root _NET_ACTIVE_WINDOW")
    m = re.search(r"0x[0-9a-f]+", out)
    return m.group(0) if m else None


def find_window(cls, wins=None):
    cls = cls.lower()
    return next((wid for wid, c in (wins or client_windows()) if c == cls), None)


def window_title(wid):
    _, out = run(f"xprop -id {wid} _NET_WM_NAME")
    m = re.search(r'"(.*)"', out)
    return m.group(1) if m else ""


def tile_windows(tile, wins):
    """Windows belonging to a tile: matched by class, and by title too when the class is shared
    (Steam's library and Big Picture are both class "steam")."""
    cls = tile.get("window_class", "").lower()
    title = tile.get("window_title")
    return [wid for wid, c in wins if cls and c == cls and (not title or window_title(wid) == title)]


def activate(wid):
    run(f"xdotool windowactivate {int(wid, 16)}")


def is_fullscreen(wid):
    _, out = run(f"xprop -id {wid} _NET_WM_STATE")
    return "_NET_WM_STATE_FULLSCREEN" in out


def make_fullscreen(wid):
    run(f"xdotool windowstate --add FULLSCREEN {int(wid, 16)}")


# Tiles with "fullscreen": "launch" only go full screen when opened from Deck TV:
# tile id -> time of the launch, so the watcher knows a new window came from us
launched = {}


def open_tiles(wins=None):
    """Ids of tiles whose window is currently open; a group tile (Live TV) counts as open when
    any of its apps is."""
    wins = wins or client_windows()
    tiles = load_config()["tiles"]
    found = [t["id"] for t in tiles if tile_windows(t, wins)]
    groups = [t["id"] for t in tiles if any(c.get("tile") in found for c in t.get("choices", []))]
    return found + groups


def watch_for_closed_apps():
    """Watch app windows: make new ones full screen where the tile asks for it (some apps, like
    Spotify, name their window too late for KWin's rules), and when one closes, bring Deck TV back
    instead of leaving the bare desktop."""
    seen = set()
    dropped = {}  # window -> seen out of full screen once
    while True:
        time.sleep(1)
        try:
            tiles = [t for t in load_config()["tiles"] if t.get("window_class")]
            wins = client_windows()
            owned = {t["id"]: tile_windows(t, wins) for t in tiles}
            current = {wid for ids in owned.values() for wid in ids}
            for t in tiles:
                fresh = set(owned[t["id"]]) - seen
                ours = time.time() - launched.get(t["id"], 0) < 60
                if fresh and (t.get("fullscreen") is True or (t.get("fullscreen") == "launch" and ours)):
                    for wid in fresh:
                        make_fullscreen(wid)
                    launched.pop(t["id"], None)
            # A site's own full-screen button, when you leave it, takes Chrome out of full screen
            # too: half a window, the taskbar showing, white where it no longer reaches. Put the
            # app in front straight back (it has to stay out for two checks, so a change in
            # progress isn't fought).
            active = active_window()
            front = next((t for t in tiles if t.get("fullscreen") is True and active in owned[t["id"]]), None)
            if front and not is_fullscreen(active):
                if dropped.get(active):
                    make_fullscreen(active)
                    dropped.pop(active, None)
                else:
                    dropped[active] = True
            else:
                dropped.clear()
            closed = seen - current
            seen = current
            if not closed:
                continue
            home = find_window(HOME_CLASS, wins)
            active = active_window()
            app_in_front = active in current or active == home
            if home and not app_in_front:
                activate(home)
        except Exception:
            pass


def public_tile(t):
    icon = find_icon(t)
    return {**{k: v for k, v in t.items() if k not in ("command", "app_icon")},
            "appIcon": icon is not None,
            "iconVersion": int(icon.stat().st_mtime) if icon else 0}  # changes the icon's URL when the file changes


def public_config():
    cfg = load_config()
    return {
        "weather": cfg.get("weather", {}),
        "tiles": [public_tile(t) for t in cfg["tiles"] if installed(t)],
        "actions": [{k: v for k, v in a.items() if k != "command"} for a in cfg["actions"]],
    }


def front_tile(wins=None):
    """The tile whose window is in front, if any."""
    wins = wins or client_windows()
    active = active_window()
    return next((t for t in load_config()["tiles"] if active in tile_windows(t, wins)), None)


def app_state():
    wins = client_windows()
    front = front_tile(wins)
    return {"open": open_tiles(wins), "home": find_window(HOME_CLASS, wins) is not None,
            "front": front["id"] if front else None, "media": now_playing.now_playing(),
            "players": now_playing.players(), "page": page_version(), "unread": unread_counts(wins)}


def unread_counts(wins):
    """Unread messages per app, for tiles that show them in their window title, like WhatsApp's
    "(3) WhatsApp". Only the title is read, never the messages."""
    counts = {}
    for t in load_config()["tiles"]:
        if t.get("unread") == "title":
            for wid in tile_windows(t, wins):
                m = re.match(r"\((\d+)\)", window_title(wid))
                if m:
                    counts[t["id"]] = counts.get(t["id"], 0) + int(m.group(1))
    return counts


def page_version():
    """Changes whenever the home page is updated, so an open page knows to reload itself."""
    return int((ROOT / "web" / "index.html").stat().st_mtime)


# One player at a time across all the apps: see media.py
now_playing = media.Media(lambda: load_config()["tiles"])


def media_control(action, prefer_front=False):
    """Play/pause, next or previous on the player the card shows; media keys if there's none.
    From the phone (prefer_front), the app on screen wins if it has a player: what you're looking at
    is what play should play."""
    front = front_tile() if prefer_front else None
    try:
        if now_playing.control(action, front["id"] if front else None):
            return {"ok": True}
    except Exception:
        pass
    if pointer:
        pointer.key(MEDIA_KEYS[action])
    return {"ok": True, "keys": True}


def with_url(cmd, url):
    """A browser app's command, opening url instead of its usual page."""
    return [f"--app={url}" if arg.startswith("--app=") else arg for arg in cmd]


def open_url(tile, url):
    """Send an already open browser app to url (UFC's Results or Fights)."""
    try:
        with Page(tile["devtools_port"]) as page:
            page.call("Page.navigate", url=url)
    except Exception:
        pass


def launch_item(item_id, choice_id=None):
    """Switch to a tile's app if it's open, else start it. A tile with "choices" (UFC: Results or
    Fights) opens on the chosen page. Returns (body, status)."""
    cfg = load_config()
    item = {i["id"]: i for i in cfg["tiles"] + cfg["actions"]}.get(item_id)
    if not item:
        return {"error": "unknown"}, 404
    choice = None
    if choice_id:
        choice = {c["id"]: c for c in item.get("choices", [])}.get(choice_id)
        if not choice:
            return {"error": "unknown"}, 404
        if choice.get("tile"):
            return launch_item(choice["tile"])  # a group tile (Live TV): the choice is another app
    if "command" not in item:
        return {"error": "choose"}, 400
    # Just closed from the dock: a stray click while it shuts down mustn't start it again
    if time.time() - closing.get(item_id, 0) < 4:
        return {"error": "closing"}, 409
    # Already open: switch to it rather than starting a second copy
    found = tile_windows(item, client_windows())
    if found:
        if choice:
            open_url(item, choice["url"])
        if item.get("fullscreen"):
            make_fullscreen(found[0])
        activate(found[0])
        return {"ok": True, "switched": True}, 200
    launched[item["id"]] = time.time()
    try:
        spawn(with_url(item["command"], choice["url"]) if choice else item["command"])
    except OSError as e:
        return {"error": str(e)}, 500
    return {"ok": True}, 200


closing = {}  # tile id -> when it was closed


def close_item(item_id):
    """Close a tile's app windows the polite way, as their own close button would."""
    tile = {t["id"]: t for t in load_config()["tiles"]}.get(item_id)
    if not tile:
        return {"error": "unknown"}, 404
    if time.time() - closing.get(item_id, 0) < 4:
        return {"ok": True, "closed": 0}, 200  # already on its way out
    closing[item_id] = time.time()
    now_playing.closing(item_id)
    found = tile_windows(tile, client_windows())
    for wid in found:
        run(f"xdotool windowquit {int(wid, 16)}")
    forget_windows()
    return {"ok": True, "closed": len(found)}, 200


def go_home():
    wid = find_window(HOME_CLASS)
    if wid:
        activate(wid)
    else:
        spawn([str(Path.home() / ".local/bin/deck-tv")])
    return {"ok": True}


# ---------- phone remote ----------
# A second listener on the home network for the phone remote. Everything except the page
# itself needs the pairing token, which only the Deck TV screen shows (as a QR code).
REMOTE_PORT = int(os.environ.get("DECK_TV_REMOTE_PORT", "8766"))
TOKEN_FILE = Path.home() / ".config/deck-tv/remote-token"
MEDIA_KEYS = {"play": "XF86AudioPlay", "next": "XF86AudioNext", "prev": "XF86AudioPrev"}
TYPING_KEYS = {"Return", "BackSpace", "Escape", "Tab", "Up", "Down", "Left", "Right"}


def remote_token():
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(24))
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))  # no packet is sent; this just picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def remote_url():
    return f"http://{lan_ip()}:{REMOTE_PORT}/#{remote_token()}"


class Pointer:
    """Mouse and keyboard through libxdo, so a stream of touchpad moves doesn't start a process each."""

    def __init__(self):
        self.lib = ctypes.CDLL("libxdo.so.3")
        self.lib.xdo_new.restype = ctypes.c_void_p
        self.lib.xdo_move_mouse_relative.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        self.lib.xdo_click_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        self.lib.xdo_enter_text_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_uint]
        self.lib.xdo_send_keysequence_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_uint]
        self.xdo = self.lib.xdo_new(None)
        self.lock = threading.Lock()

    def move(self, dx, dy):
        with self.lock:
            self.lib.xdo_move_mouse_relative(self.xdo, int(dx), int(dy))

    def click(self, button=1, times=1):
        with self.lock:
            for _ in range(times):
                self.lib.xdo_click_window(self.xdo, 0, button)

    def type(self, text):
        with self.lock:
            self.lib.xdo_enter_text_window(self.xdo, 0, text.encode(), 8000)

    def key(self, name):
        with self.lock:
            self.lib.xdo_send_keysequence_window(self.xdo, 0, name.encode(), 0)


pointer = None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web"), **kwargs)

    def log_message(self, *args):
        pass

    def _trusted(self):
        # Host check blocks DNS rebinding; the custom header forces a CORS
        # preflight (which we never approve), so other sites can't call us.
        return (
            self.headers.get("Host") in ALLOWED_HOSTS
            and self.headers.get("X-Deck-TV") == "1"
        )

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return self._json({"error": "forbidden"}, 403)
        if self.path == "/api/config":
            return self._json(public_config())
        if self.path.startswith("/appicon/"):
            return self._appicon()
        if self.path.startswith("/media/art"):
            return self._media_art()
        if self.path.startswith("/images/"):
            name = Path(self.path).name
            file = ROOT / "images" / name
            if file.is_file():
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(str(file)))
                self.end_headers()
                self.wfile.write(file.read_bytes())
                return
            return self._json({"error": "not found"}, 404)
        if self.path.startswith("/api/"):
            if not self._trusted():
                return self._json({"error": "forbidden"}, 403)
            if self.path == "/api/volume":
                return self._json(get_volume())
            if self.path == "/api/audio":
                return self._json(audio_outputs())
            if self.path == "/api/audio/found":
                return self._json(speakers_found())
            if self.path == "/api/state":
                return self._json(app_state())
            if self.path == "/api/remote":
                return self._json({"url": remote_url()})
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def _media_art(self):
        file = now_playing.art_file()
        try:
            data = Path(file).read_bytes() if file else None
        except OSError:
            data = None
        if not data:
            return self._json({"error": "no artwork"}, 404)
        self.send_response(200)
        self.send_header("Content-Type", "image/png" if data[:4] == b"\x89PNG" else "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=86400")  # the URL changes with the artwork
        self.end_headers()
        self.wfile.write(data)

    def _appicon(self):
        tile = next((t for t in load_config()["tiles"] if t["id"] == Path(self.path.split("?")[0]).name), None)
        file = find_icon(tile) if tile else None
        if not file:
            return self._json({"error": "not found"}, 404)
        data = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(file)))
        self.send_header("Content-Length", str(len(data)))  # the remote keeps connections open
        self.send_header("Cache-Control", "max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if not self._trusted():
            return self._json({"error": "forbidden"}, 403)
        parts = self.path.strip("/").split("/")
        if len(parts) in (3, 4) and parts[:2] == ["api", "launch"]:
            return self._json(*launch_item(*parts[2:]))
        if len(parts) == 3 and parts[:2] == ["api", "close"]:
            return self._json(*close_item(parts[2]))
        if len(parts) == 4 and parts[:3] == ["api", "media", "focus"]:
            return self._json({"ok": now_playing.focus(parts[3])})
        if self.path == "/api/audio/search":
            return self._json(search_speakers())
        if len(parts) == 4 and parts[:3] == ["api", "audio", "pair"]:
            return self._json(start_pairing(urllib.parse.unquote(parts[3])))
        if len(parts) == 4 and parts[:3] == ["api", "audio", "use"]:
            return self._json(set_output(urllib.parse.unquote(parts[3])))
        if len(parts) == 4 and parts[:3] == ["api", "volume", "set"] and parts[3].isdigit():
            return self._json(set_volume(parts[3]))
        if len(parts) == 3 and parts[:2] == ["api", "media"] and parts[2] in MEDIA_KEYS:
            return self._json(media_control(parts[2]))
        if self.path == "/api/home":
            return self._json(go_home())
        if self.path == "/api/volume/up":
            return self._json(change_volume(5))
        if self.path == "/api/volume/down":
            return self._json(change_volume(-5))
        if self.path == "/api/volume/mute":
            return self._json(toggle_mute())
        return self._json({"error": "not found"}, 404)


class RemoteHandler(Handler):
    protocol_version = "HTTP/1.1"  # keep-alive, so touchpad moves reuse one connection

    def __init__(self, *args, **kwargs):
        SimpleHTTPRequestHandler.__init__(self, *args, directory=str(ROOT / "remote"), **kwargs)

    def do_GET(self):
        if self.path.startswith("/api/") or self.path.startswith("/appicon/"):
            if not secrets.compare_digest(self.headers.get("X-Remote-Token", ""), remote_token()):
                return self._json({"error": "pair again"}, 401)
            if self.path == "/api/config":
                return self._json(public_config())
            if self.path == "/api/state":
                return self._json(app_state())
            if self.path == "/api/search":
                tile = front_tile()
                return self._json(search.context(tile) if tile else {"app": None, "mode": "type"})
            if self.path.startswith("/appicon/"):
                return self._appicon()
            return self._json({"error": "not found"}, 404)
        return SimpleHTTPRequestHandler.do_GET(self)  # the remote page itself; Handler's localhost check doesn't apply

    def do_POST(self):
        if not secrets.compare_digest(self.headers.get("X-Remote-Token", ""), remote_token()):
            return self._json({"error": "pair again"}, 401)
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return self._json({"error": "bad request"}, 400)
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[:2] == ["api", "launch"]:
            return self._json(*launch_item(parts[2]))
        if self.path == "/api/home":
            return self._json(go_home())
        if self.path == "/api/move":
            pointer.move(max(-400, min(400, body.get("dx", 0))), max(-400, min(400, body.get("dy", 0))))
            return self._json({"ok": True})
        if self.path == "/api/click":
            button = body.get("button", 1)
            if button not in (1, 2, 3):
                return self._json({"error": "bad button"}, 400)
            pointer.click(button)
            return self._json({"ok": True})
        if self.path == "/api/scroll":
            steps = max(-10, min(10, int(body.get("steps", 0))))
            if steps:
                pointer.click(4 if steps < 0 else 5, abs(steps))
            return self._json({"ok": True})
        if self.path == "/api/type":
            text = str(body.get("text", ""))[:500]
            if text:
                pointer.type(text)
            return self._json({"ok": True})
        if self.path == "/api/key":
            name = body.get("key")
            if name not in TYPING_KEYS:
                return self._json({"error": "bad key"}, 400)
            if name == "BackSpace":
                # In a browser app, Backspace outside a text box goes back a page: only send it to a box
                tile = front_tile()
                if tile and tile.get("devtools_port"):
                    try:
                        if not search.typing_focused(tile["devtools_port"]):
                            return self._json({"ok": True, "skipped": "no text box focused"})
                    except Exception:
                        pass
            pointer.key(name)
            return self._json({"ok": True})
        if self.path in ("/api/search", "/api/search/open"):
            tile = front_tile()
            if not tile or not tile.get("search"):
                return self._json({"error": "this app has no search"}, 404)
            try:
                if self.path == "/api/search":
                    return self._json({"app": tile["id"], "results": search.run(tile, str(body.get("text", ""))[:200])})
                search.open_result(tile, int(body.get("index", 0)), bool(body.get("play")))
                return self._json({"ok": True})
            except Exception as e:
                return self._json({"error": str(e)}, 500)
        if len(parts) == 3 and parts[:2] == ["api", "media"] and parts[2] in MEDIA_KEYS:
            return self._json(media_control(parts[2], prefer_front=True))
        if self.path == "/api/volume/up":
            return self._json(change_volume(5))
        if self.path == "/api/volume/down":
            return self._json(change_volume(-5))
        if self.path == "/api/volume/mute":
            return self._json(toggle_mute())
        return self._json({"error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    request_queue_size = 128  # the default 5 overflows the moment the page, dock and phone all ask at once
    daemon_threads = True


def main():
    global pointer
    server = Server(("127.0.0.1", PORT), Handler)
    threading.Thread(target=watch_for_closed_apps, daemon=True).start()
    threading.Thread(target=now_playing.run, daemon=True).start()
    # Phone remote: home network only (port 8766), every action needs the pairing token
    try:
        pointer = Pointer()
        remote = Server(("0.0.0.0", REMOTE_PORT), RemoteHandler)
        threading.Thread(target=remote.serve_forever, daemon=True).start()
        print(f"Phone remote on port {REMOTE_PORT}", file=sys.stderr)
    except OSError as e:
        print(f"Phone remote unavailable: {e}", file=sys.stderr)
    print(f"Deck TV on http://127.0.0.1:{PORT}", file=sys.stderr)
    server.serve_forever()


if __name__ == "__main__":
    main()
