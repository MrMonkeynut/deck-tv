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
import signal
import socket
import subprocess
import sys
import threading
import urllib.parse
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import media
import movy_tour
import search
import trailers
from screen import screen
import watchlist
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
_roles = {}  # window id -> its WM_WINDOW_ROLE, the same


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
            _roles.pop(gone, None)
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
    (every Steam window is class "steam": Big Picture and the library count, the friends list and
    pop-ups don't). With a list of titles, windows come in its order: Big Picture first."""
    cls = tile.get("window_class", "").lower()
    found = [wid for wid, c in wins if cls and c == cls]
    titles = tile.get("window_title")
    if not titles:
        return found
    titles = [titles] if isinstance(titles, str) else titles
    named = [(titles.index(t), wid) for wid in found for t in [window_title(wid)] if t in titles]
    return [wid for _, wid in sorted(named)]


def activate(wid):
    run(f"xdotool windowactivate {int(wid, 16)}")


def is_fullscreen(wid):
    _, out = run(f"xprop -id {wid} _NET_WM_STATE")
    return "_NET_WM_STATE_FULLSCREEN" in out


def make_fullscreen(wid):
    run(f"xdotool windowstate --add FULLSCREEN {int(wid, 16)}")


def tab_window(tile, wid):
    """A web app's ordinary Chrome window, with tabs and an address bar: what a link from the app
    opens in. It's left as an ordinary window; only the app's own window goes full screen."""
    if not any(arg.startswith("--app=") for arg in tile.get("command", [])):
        return False
    if wid not in _roles:
        _, out = run(f"xprop -id {wid} WM_WINDOW_ROLE")
        _roles[wid] = out
    return '"browser"' in _roles[wid]


# Most recently used first: tile ids, and "home" for Deck TV itself (for the Alt+Tab switcher)
recent = []


def used(item_id):
    if item_id in recent:
        recent.remove(item_id)
    recent.insert(0, item_id)


def shut_down():
    """Deck TV's window was closed (Alt+F4, or its own close): take the dock and server down
    with it, so nothing of Deck TV is left on screen. Opening Deck TV again starts it all afresh."""
    try:
        os.kill(int((Path.home() / ".cache/deck-tv-dock.lock").read_text().strip()), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    os._exit(0)


# Tiles with "fullscreen": "launch" only go full screen when opened from Deck TV:
# tile id -> time of the launch, so the watcher knows a new window came from us
launched = {}


def open_tiles(wins=None):
    """Ids of tiles whose window is currently open; a group tile (Live TV) counts as open when
    any of its apps is."""
    wins = wins or client_windows()
    tiles = all_tiles()
    found = [t["id"] for t in tiles if tile_windows(t, wins)]
    groups = [t["id"] for t in tiles if any(c.get("tile") in found for c in t.get("choices", []))]
    return found + groups


def watch_for_closed_apps():
    """Watch app windows: make new ones full screen where the tile asks for it (some apps, like
    Spotify, name their window too late for KWin's rules), and when one closes, bring Deck TV back
    instead of leaving the bare desktop."""
    seen = set()
    dropped = {}  # window -> seen out of full screen once
    home_seen = False
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
                        if not tab_window(t, wid):
                            make_fullscreen(wid)
                    launched.pop(t["id"], None)
            # A site's own full-screen button, when you leave it, takes Chrome out of full screen
            # too: half a window, the taskbar showing, white where it no longer reaches. Put the
            # app in front straight back (it has to stay out for two checks, so a change in
            # progress isn't fought).
            active = active_window()
            home = find_window(HOME_CLASS, wins)
            if home_seen and not home:
                shut_down()
            home_seen = home is not None
            in_front = next((t["id"] for t in tiles if active in owned[t["id"]]), None) or ("home" if active == home else None)
            if in_front and (not recent or recent[0] != in_front):
                used(in_front)
            front = next((t for t in tiles if t.get("fullscreen") is True and active in owned[t["id"]]
                          and not tab_window(t, active)), None)
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


# The person's own card layout, from Settings: the order of the cards and which are switched off.
# Kept apart from tiles.json, so reinstalling Deck TV doesn't undo it.
LAYOUT_FILE = Path.home() / ".config/deck-tv/layout.json"

# The person's own groups ("folders" of apps, made in Settings) are cards too. Each has a name, one of
# these looks (the card's colours; its art is images/group-<style>.svg) and the apps inside it.
GROUP_STYLES = {
    "violet": ("#2E1A5C", "#8B5CF6"), "ocean": ("#0B2A4A", "#38BDF8"), "forest": ("#10361F", "#34D399"),
    "ember": ("#4A1A08", "#FB923C"), "rose": ("#4A0F2A", "#F472B6"), "gold": ("#3D2E08", "#FACC15"),
    "slate": ("#1E2633", "#94A3B8"), "crimson": ("#4A0B12", "#EF4444"),
}


def load_layout():
    try:
        with open(LAYOUT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        groups = [g for g in data.get("groups", []) if isinstance(g, dict) and g.get("id")]
        layout = {"order": [str(i) for i in data.get("order", [])], "off": [str(i) for i in data.get("off", [])],
                  "groups": groups, "seeded": [str(i) for i in data.get("seeded", [])]}
    except (OSError, ValueError, AttributeError):
        layout = {"order": [], "off": [], "groups": [], "seeded": []}
    return with_starting_groups(layout)


def with_starting_groups(layout):
    """tiles.json's starting groups (Live TV, UFC) join the person's groups the first time they're
    seen, each in the place (and on or off) of the card it replaces. One the person deletes stays
    deleted: it's remembered as added."""
    cfg = load_config()
    new = [g for g in cfg.get("groups", []) if g["id"] not in layout["seeded"]]
    if not new:
        return layout
    if not layout["order"]:
        layout["order"] = [t["id"] for t in cfg["tiles"]]
    taken = {a for g in layout["groups"] for a in g.get("apps", [])}
    for g in new:
        layout["seeded"].append(g["id"])
        if any(x["id"] == g["id"] for x in layout["groups"]):
            continue
        layout["groups"].append({"id": g["id"], "name": g["name"], "style": g["style"],
                                 "apps": [a for a in g["apps"] if a not in taken]})
        old, order = g.get("replaces"), layout["order"]
        if old in order:
            order[order.index(old)] = g["id"]
        else:  # where its first app is, or at the end
            at = next((n for n, i in enumerate(order) if i in g["apps"]), len(order))
            order.insert(at, g["id"])
        if old in layout["off"]:
            layout["off"][layout["off"].index(old)] = g["id"]
    try:
        write_layout(layout)
    except OSError:
        pass
    return layout


def write_layout(layout):
    LAYOUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LAYOUT_FILE, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)


def groupable(tile):
    """Can this tile go in a group? Any app: not a group tile from tiles.json, nor an app tiles.json
    keeps off the home screen."""
    return "command" in tile and not tile.get("hidden") and not any(c.get("tile") for c in tile.get("choices", []))


def clean_groups(groups, tiles):
    """The groups from Settings, checked: known looks, real apps, each app in one group at most.
    "icon" is the app whose look the group's card wears, or None for a folder of its apps."""
    apps = {t["id"] for t in tiles if groupable(t)}
    taken, out = set(), []
    for g in groups:
        if not isinstance(g, dict) or not re.fullmatch(r"g-[a-z0-9]{1,16}", str(g.get("id", ""))):
            return None
        name = str(g.get("name", "")).strip()[:24] or "Group"
        style = g.get("style") if g.get("style") in GROUP_STYLES else "violet"
        members = [a for a in g.get("apps", []) if a in apps and a not in taken]
        taken |= set(members)
        icon = g.get("icon") if g.get("icon") in members else None
        out.append({"id": g["id"], "name": name, "style": style, "apps": members, "icon": icon})
    return out


def group_tiles(tiles, groups=None):
    """The person's groups as tiles: each choice is one of the apps inside. An app switched off in
    Settings stays in its group but is "hidden": left out of the group's picker and card."""
    names = {t["id"]: t["name"] for t in tiles}
    by_id = {t["id"]: t for t in tiles}
    layout = load_layout()
    off = set(layout["off"])
    out = []
    for g in clean_groups(layout["groups"] if groups is None else groups, tiles) or []:
        color, accent = GROUP_STYLES[g["style"]]
        apps = [a for a in g["apps"] if a in names]
        shown = len([a for a in apps if a not in off])
        look = {"color": color, "accent": accent, "image": f"group-{g['style']}.svg"}
        if g["icon"]:  # the card wears one of its apps' looks: that app's art, colours and icon
            app = by_id[g["icon"]]
            look = {"color": app.get("color"), "accent": app.get("accent"), "image": app.get("image"), "iconApp": app["id"]}
        out.append({"id": g["id"], "name": g["name"], "sub": f"{shown} app" + ("" if shown == 1 else "s"),
                    "icon": "grid", **look, "userGroup": True, "style": g["style"],
                    "choices": [{"id": a, "name": names[a], "tile": a, **({"hidden": True} if a in off else {})} for a in apps]})
    return out


def all_tiles():
    """tiles.json's tiles and the person's groups."""
    tiles = load_config()["tiles"]
    return tiles + group_tiles(tiles)


def save_layout(body):
    """Store {"order": [ids], "off": [ids], "groups": [...]} from the settings panel ("groups" left
    out keeps the groups as they are). Returns (body, status)."""
    tiles = load_config()["tiles"]
    layout = load_layout()
    groups = body.get("groups", layout["groups"])
    groups = clean_groups(groups, tiles) if isinstance(groups, list) else None
    if groups is None:
        return {"error": "bad groups"}, 400
    known = {t["id"] for t in tiles} | {g["id"] for g in groups}
    order, off = body.get("order"), body.get("off")
    if not isinstance(order, list) or not isinstance(off, list) or not set(order + off) <= known:
        return {"error": "bad layout"}, 400
    write_layout({"order": order, "off": off, "groups": groups, "seeded": layout["seeded"]})
    return {"ok": True}, 200


def laid_out(tiles):
    """Tiles in the person's order (new ones keep their tiles.json place at the end), each marked
    "off" if switched off. A group's apps (Live TV's, or the person's own) go off with their group,
    and the apps in the person's groups are kept off the home screen: they're in their group's card."""
    layout = load_layout()
    rank = {tid: n for n, tid in enumerate(layout["order"])}
    tiles = sorted(tiles, key=lambda t: rank.get(t["id"], len(rank)))
    off = set(layout["off"])
    grouped = {c["tile"]: t["id"] for t in tiles if t.get("userGroup") for c in t["choices"]}
    for t in tiles:
        if t["id"] in off:
            off |= {c["tile"] for c in t.get("choices", []) if c.get("tile")}
    return [{**t, "off": t["id"] in off, **({"hidden": True, "inGroup": grouped[t["id"]]} if t["id"] in grouped else {})}
            for t in tiles]


def public_config():
    cfg = load_config()
    tiles = [t for t in cfg["tiles"] if installed(t)]
    return {
        "weather": cfg.get("weather", {}),
        "tiles": laid_out([public_tile(t) for t in tiles + group_tiles(tiles)]),
        "groupStyles": {k: {"color": c, "accent": a} for k, (c, a) in GROUP_STYLES.items()},
        "layoutOff": load_layout()["off"],  # what's switched off, as Settings saved it (apps inside groups too)
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
            "players": now_playing.players(), "page": page_version(), "unread": unread_counts(wins),
            "recent": list(recent)}


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
    item = {i["id"]: i for i in all_tiles() + cfg["actions"]}.get(item_id)
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
    if item in cfg["actions"]:
        return power_action(item)
    # Just closed from the dock: a stray click while it shuts down mustn't start it again
    if time.time() - closing.get(item_id, 0) < 4:
        return {"error": "closing"}, 409
    # Already open: switch to it rather than starting a second copy
    found = sorted(tile_windows(item, client_windows()), key=lambda wid: tab_window(item, wid))  # the app's own window first
    used(item["id"])
    if found:
        if choice:
            open_url(item, choice["url"])
        if item.get("fullscreen") and not tab_window(item, found[0]):
            make_fullscreen(found[0])
        activate(found[0])
        return {"ok": True, "switched": True}, 200
    launched[item["id"]] = time.time()
    try:
        spawn(with_url(item["command"], choice["url"]) if choice else item["command"])
    except OSError as e:
        return {"error": str(e)}, 500
    return {"ok": True}, 200


POWER_LOG = Path.home() / ".local/share/deck-tv/power.log"


def power_action(item):
    """Sleep, restart or power off. Run here rather than detached, so a refusal comes back as a
    message: started detached, a Sleep that logind turns down (an app holding off sleep, usually)
    just puts the countdown away, which looks like the Deck waking straight up again. Failures go
    in power.log, with what was holding the Deck awake at the time."""
    try:
        out = subprocess.run(item["command"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        out = subprocess.CompletedProcess(item["command"], 1, "", str(e))
    if out.returncode == 0:
        return {"ok": True}, 200
    why = (out.stderr or out.stdout).strip() or f"exit status {out.returncode}"
    _, inhibitors = run("systemd-inhibit --list --no-pager")
    try:
        with open(POWER_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(item['command'])}\n{why}\n{inhibitors}\n")
    except OSError:
        pass
    m = re.search(r'inhibited by "([^"]+)".*?reason is "([^"]*)"', why, re.S)
    if m:
        return {"error": f"{m[1]} is keeping the Deck awake ({m[2]})" if m[2] else f"{m[1]} is keeping the Deck awake"}, 409
    return {"error": why.splitlines()[0]}, 500


closing = {}  # tile id -> when it was closed


def watch_now(title):
    """The watchlist's Watch now: open Movy (or switch to it) and search it for the film. Movy just
    started takes a few seconds before its page answers, so wait for that first. If the full title
    finds nothing ("Star Wars: Episodes IV, V, VI"), try the part before the colon. Returns (body, status)."""
    tile = next((t for t in load_config()["tiles"] if t.get("search") == "movy" and installed(t)), None)
    if not tile:
        return {"error": "Movy isn't set up"}, 404
    body, status = launch_item(tile["id"])
    if status != 200:
        return body, status
    text = re.sub(r"\s*\([^)]*\)", "", title).strip() or title
    for _ in range(60):  # up to about 15 s
        try:
            with Page(tile["devtools_port"]) as page:
                if page.eval("location.hostname.includes('movy') && document.readyState === 'complete'"):
                    break
        except Exception:
            pass
        time.sleep(0.25)
    else:
        return {"error": "Movy didn't open in time"}, 504
    try:
        found = search.run(tile, text)
        if not found and ":" in text:
            text = text.split(":")[0].strip()
            found = search.run(tile, text)
    except Exception as e:
        return {"error": str(e)}, 500
    return {"ok": True, "searched": text, "found": len(found or [])}, 200


def tour_movy(step):
    """The home screen's tour, handing over to Movy for a walk round it (movy_tour.py)."""
    tile = next((t for t in load_config()["tiles"] if t.get("search") == "movy" and installed(t)), None)
    if not tile:
        return {"error": "Movy isn't set up"}, 404
    return movy_tour.start(tile, launch_item, go_home, tour_caption, step)


# ---------- the tour: what the home screen can't do from inside its own page ----------
# Its captions over other apps (tour_overlay.py), the pretend phone in a window that stays above apps,
# the real pointer (and the dock, worked with it), and opening Claude. The home screen's tour calls
# these as it goes; /api/tour/end puts it all away.
tour_overlay = None
tour_lock = threading.Lock()
PHONE_TITLE = "Deck TV phone"   # tour-phone.html's title, which is how its window is found
# The phone is its own browser (own profile, own window class), so KWin's rule that makes every Deck TV
# window full screen leaves it be. So it can't hear the home screen directly: the tour's commands for it
# queue here, and the phone fetches them (tour_phone_cmds), waiting for the next.
phone_cmds = []                 # (number, command)
phone_cond = threading.Condition()
phone_ready = threading.Event()
DOCK_AT = Path.home() / ".cache/deck-tv-dock-at.json"  # where dock.py says its line and Home icon are
HOLDER = Path.home() / ".cache/deck-tv-tour-holder"     # the window tour_overlay.py keeps the phone in


def tour_caption(c):
    """Show (or with {"cmd": "hide"}, hide) a caption over everything, starting the overlay if need be."""
    global tour_overlay
    with tour_lock:
        if tour_overlay is None or tour_overlay.poll() is not None:
            if c.get("cmd") in ("hide", "holder-off"):  # nothing showing to put away
                return {"ok": True}
            tour_overlay = subprocess.Popen([sys.executable, str(ROOT / "tour_overlay.py")], stdin=subprocess.PIPE,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
                                            start_new_session=True)
        try:
            tour_overlay.stdin.write(json.dumps(c) + "\n")
            tour_overlay.stdin.flush()
        except OSError:
            tour_overlay = None
    return {"ok": True}


def screen_size():
    _, out = run("xdotool getdisplaygeometry")
    try:
        w, h = map(int, out.split())
        return w, h
    except ValueError:
        return 1920, 1080


def stage_scale():
    """The home screen's 1280x720 stage on this screen: its scale and where its corner is."""
    w, h = screen_size()
    k = min(w / 1280, h / 720)
    return k, (w - 1280 * k) / 2, (h - 720 * k) / 2


def stage_to_screen(x, y):
    k, ox, oy = stage_scale()
    return round(ox + x * k), round(oy + y * k)


def phone_window():
    _, out = run(f"xdotool search --name '^{PHONE_TITLE}$'")
    ids = out.split()
    return ids[0] if ids else None


def tour_phone(on):
    """The pretend phone's window: opened in Deck TV's own browser (so the tour can talk to it), at the
    bottom right where the home screen's phone was, borderless and kept above every app."""
    wid = phone_window()
    if not on:
        tour_caption({"cmd": "holder-off"})
        stop_phone_browser()
        return {"ok": True}
    _, was_active = run("xdotool getactivewindow")
    if not wid:
        stop_phone_browser()  # anything left of an earlier one, which would swallow the new window
        phone_ready.clear()
        spawn(["flatpak", "run", "com.google.Chrome", "--user-data-dir=~/.var/app/com.google.Chrome/deck-tv-phone",
               "--class=deck-tv-phone", "--no-first-run", "--no-default-browser-check",
               "--simulate-outdated-no-au=Tue, 31 Dec 2099 23:59:59 GMT",  # no "Can't update Chrome" (flatpak) taking the focus
               f"--app=http://127.0.0.1:{PORT}/tour-phone.html"])
        for _ in range(100):  # a browser of its own, so it can take a while the first time
            time.sleep(0.25)
            wid = phone_window()
            if wid:
                break
        else:
            return {"error": "the phone didn't open"}
    # No title bar or focus at first: install.sh's KWin rule for its class. Then into tour_overlay.py's
    # holder, an unmanaged window that stays above even the full-screen app in front
    k, _, _ = stage_scale()
    x, y = stage_to_screen(1280 - 90 - 300, 720 - 34 - 640)
    w, h = round(300 * k), round(640 * k)
    for state in ("MAXIMIZED_VERT", "MAXIMIZED_HORZ"):  # its browser remembers being maximised; one at a time
        run(f"xdotool windowstate --remove {state} {wid}")
    since = time.time()
    tour_caption({"cmd": "holder", "x": x, "y": y, "w": w, "h": h})
    holder = None
    for _ in range(40):
        try:
            if HOLDER.stat().st_mtime >= since - 1:
                holder = HOLDER.read_text().strip()
                break
        except OSError:
            pass
        time.sleep(0.05)
    if holder:
        run(f"xdotool windowreparent {wid} {holder}")
        run(f"xdotool windowmove {wid} 0 0")
    else:
        run(f"xdotool windowmove {wid} {x} {y}")
    run(f"xdotool windowsize {wid} {w} {h}")
    if was_active.strip().isdigit():  # whatever was in front keeps the focus (and stays full screen)
        for _ in range(2):  # again once it's settled: the move into the holder can take the focus too
            run(f"xdotool windowactivate {was_active.strip()}")
            time.sleep(0.4)
    if not phone_ready.wait(15):  # the remote inside it, loaded and listening
        return {"error": "the phone's remote didn't load"}
    return {"ok": True}


def stop_phone_browser():
    """The phone's browser (a profile of its own), window and all. [d]: so pkill doesn't match itself."""
    run("pkill -f [d]eck-tv-phone")
    for _ in range(20):
        if run("pgrep -f [d]eck-tv-phone")[0]:  # none left
            return
        time.sleep(0.1)
    run("pkill -9 -f [d]eck-tv-phone")


def tour_phone_cmd(c):
    with phone_cond:
        phone_cmds.append((len(phone_cmds), c))
        del phone_cmds[:-50]  # only the last few matter
        phone_cond.notify_all()
    return {"ok": True}


def tour_phone_cmds(after):
    """The phone's commands after number after, waiting a while for one if there are none yet.
    after None: just the latest number, to start from."""
    with phone_cond:
        latest = lambda: phone_cmds[-1][0] if phone_cmds else -1
        if after is None:
            return {"latest": latest(), "cmds": []}
        phone_cond.wait_for(lambda: latest() > after, timeout=15)
        return {"latest": latest(), "cmds": [c for n, c in phone_cmds if n > after]}


def pointer_at():
    _, out = run("xdotool getmouselocation --shell")
    m = dict(line.split("=", 1) for line in out.split() if "=" in line)
    return int(m.get("X", 0)), int(m.get("Y", 0))


def glide(points, ms):
    """Move the real pointer through screen points over ms, easing in and out, as a hand would."""
    if not pointer or len(points) < 2:
        return
    start = time.monotonic()
    while True:
        f = min(1.0, (time.monotonic() - start) * 1000 / max(1, ms))
        e = 2 * f * f if f < .5 else 1 - (-2 * f + 2) ** 2 / 2
        t = e * (len(points) - 1)
        i = min(len(points) - 2, int(t))
        u = t - i
        (x0, y0), (x1, y1) = points[i], points[i + 1]
        pointer.move_to(x0 + (x1 - x0) * u, y0 + (y1 - y0) * u)
        if f >= 1:
            return
        time.sleep(0.012)


def tour_pointer(path, ms, click):
    """The pointer along a path given on the home screen's stage (1280x720), then a click if asked."""
    if not pointer:
        return {"error": "no pointer"}
    glide([pointer_at()] + [stage_to_screen(x, y) for x, y in path], ms)
    if click:
        time.sleep(0.15)
        pointer.click()
    return {"ok": True}


def dock_at(since, state):
    """What dock.py last said about where it is, once it says so in this state, after since."""
    for _ in range(40):
        try:
            if DOCK_AT.stat().st_mtime >= since:
                at = json.loads(DOCK_AT.read_text())
                if at.get("state") == state:
                    return at
        except (OSError, ValueError):
            pass
        time.sleep(0.05)
    return None


def tour_dock_home():
    """Home the way a person would: the pointer to the bottom edge, the line that appears, the dock
    that opens, and its Home icon."""
    if not pointer:
        return go_home()
    w, h = screen_size()
    start = time.time()
    x0, _ = pointer_at()
    glide([pointer_at(), (x0 + (w // 2 - x0) * .5, h - 60), (w // 2, h - 1)], 700)
    hint = dock_at(start, "hint")
    if not hint:
        return go_home()
    hx, hy, hw, hh = hint["window"]
    glide([pointer_at(), (hx + hw // 2, hy + hh - 18)], 300)
    time.sleep(0.25)
    start = time.time()
    pointer.click()
    dock = dock_at(start, "dock")
    if not dock or "home" not in dock:
        return go_home()
    x, y, bw, bh = dock["home"]
    time.sleep(0.2)
    glide([pointer_at(), (x + bw // 2, y + bh // 2)], 450)
    time.sleep(0.3)
    pointer.click()
    return {"ok": True}


def tour_claude(prompt):
    """A new Claude Code session in the deck-tv folder, with the prompt in it, sent. Claude Desktop's
    claude://code/new link opens the session with the prompt filled in; it asks to confirm the folder
    first, then waits for Enter. So: wait for Claude to be in front, then Enter for each."""
    folder = Path.home() / "Downloads/deck-tv"
    if not folder.is_dir():
        folder = ROOT
    url = "claude://code/new?" + urllib.parse.urlencode({"q": prompt, "folder": str(folder)}, quote_via=urllib.parse.quote)
    spawn(["xdg-open", url])

    def press():
        for _ in range(120):  # Claude may need starting first
            _, cls = run("xdotool getactivewindow getwindowclassname")
            if "claude" in cls.lower():
                break
            time.sleep(0.25)
        time.sleep(3)
        run("xdotool key --clearmodifiers Return")  # the folder: yes
        time.sleep(2.5)
        run("xdotool key --clearmodifiers Return")  # and send
    threading.Thread(target=press, daemon=True).start()
    return {"ok": True}


def tour_end():
    tour_caption({"cmd": "hide"})
    tour_phone(False)
    return {"ok": True}


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


def show_desktop():
    """Step out to the ordinary desktop: KWin's Show Desktop, which tucks every window away."""
    run('qdbus org.kde.kglobalaccel /component/kwin invokeShortcut "Show Desktop"')
    return {"ok": True}


def go_home():
    used("home")
    wid = find_window(HOME_CLASS)
    if wid:
        activate(wid)
    else:
        spawn([str(Path.home() / ".local/bin/deck-tv")])
    return {"ok": True}


# ---------- system info: storage, Wi-Fi, sound, battery ----------
POWER = Path("/sys/class/power_supply")


def drives():
    """Internal storage (/home: the games, apps and files; the system's own small partitions are
    left out) and any microSD card or USB drive, with their space in bytes."""
    found = [("Internal storage", "internal", "/home")]
    seen = set()
    try:
        mounts = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        mounts = []
    for line in mounts:
        dev, mnt, fstype = line.split()[:3]
        mnt = mnt.replace("\\040", " ")
        if not mnt.startswith("/run/media/") or fstype.startswith("fuse") or dev in seen:
            continue
        seen.add(dev)
        kind = "sd" if "mmcblk" in dev else "usb"
        found.append(("microSD card" if kind == "sd" else os.path.basename(mnt) or "USB drive", kind, mnt))
    out = []
    for name, kind, mnt in found:
        try:
            st = os.statvfs(mnt)
        except OSError:
            continue
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        out.append({"name": name, "kind": kind, "total": total, "free": free, "used": total - free})
    return out


def network():
    """The Wi-Fi network (name, signal, band) or a cable, and the Deck's address on it."""
    _, devs = run("nmcli -t -c no -f DEVICE,TYPE,STATE,CONNECTION dev")
    info = {"connected": False}
    for line in devs.splitlines():
        dev, kind, state, conn = (line.split(":") + ["", "", "", ""])[:4]
        if state == "connected" and kind in ("wifi", "ethernet"):
            info = {"connected": True, "kind": kind, "device": dev, "name": conn}
            break
    if info["connected"] and info["kind"] == "wifi":
        _, nets = run("nmcli -t -c no -f ACTIVE,SSID,SIGNAL,FREQ dev wifi list --rescan no")
        for line in nets.splitlines():
            m = re.match(r"yes:(.*):(\d+):(\d+) MHz$", line)
            if m:
                ghz = int(m.group(3)) / 1000
                info.update(name=m.group(1).replace("\\:", ":"), signal=int(m.group(2)),
                            band="6 GHz" if ghz > 5.9 else "5 GHz" if ghz > 4.9 else "2.4 GHz")
    elif not info["connected"]:
        _, radio = run("nmcli -t -c no radio wifi")
        info["wifiOff"] = radio.strip() == "disabled"
    info["ip"] = lan_ip() if info["connected"] else None
    return info


def battery():
    try:
        bat = next(p for p in POWER.iterdir() if p.name.startswith("BAT"))
        level = int((bat / "capacity").read_text())
        status = (bat / "status").read_text().strip()
    except (OSError, StopIteration, ValueError):
        return None
    plugged = any((p / "online").read_text().strip() == "1"
                  for p in POWER.iterdir() if (p / "online").exists())
    return {"level": level, "charging": status == "Charging", "full": status == "Full" or level >= 100,
            "plugged": plugged}


def deck_info():
    """The Deck itself: model, SteamOS version, how long it's been on, temperature and memory."""
    def read(path, default=""):
        try:
            return Path(path).read_text().strip()
        except OSError:
            return default
    model = {"Jupiter": "Steam Deck LCD", "Galileo": "Steam Deck OLED"}.get(read("/sys/class/dmi/id/product_name"), "Steam Deck")
    os_release = dict(re.findall(r'^(\w+)="?([^"\n]*)"?$', read("/etc/os-release"), re.M))
    temp = None
    for h in Path("/sys/class/hwmon").glob("hwmon*"):
        if read(h / "name") == "amdgpu":  # the APU: the chip that runs everything
            t = read(h / "temp1_input")
            temp = round(int(t) / 1000) if t.isdigit() else None
    mem = dict(re.findall(r"^(\w+):\s+(\d+) kB", read("/proc/meminfo"), re.M))
    total, avail = int(mem.get("MemTotal", 0)) * 1024, int(mem.get("MemAvailable", 0)) * 1024
    up = float(read("/proc/uptime", "0").split()[0] or 0)
    return {"model": model, "os": f"{os_release.get('NAME', 'SteamOS')} {os_release.get('VERSION_ID', '')}".strip(),
            "uptime": int(up), "temp": temp, "memTotal": total, "memUsed": total - avail}


def system_info():
    vol = get_volume()
    out = next((o for o in audio_outputs().get("outputs", []) if o.get("active")), None)
    return {"storage": drives(), "network": network(), "battery": battery(), "deck": deck_info(),
            "sound": {"output": out["name"] if out else None, "kind": out["kind"] if out else None, **vol}}


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
        self.lib.xdo_move_mouse.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.lib.xdo_click_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int]
        self.lib.xdo_enter_text_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_uint]
        self.lib.xdo_send_keysequence_window.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_char_p, ctypes.c_uint]
        self.xdo = self.lib.xdo_new(None)
        self.lock = threading.Lock()

    def move(self, dx, dy):
        with self.lock:
            self.lib.xdo_move_mouse_relative(self.xdo, int(dx), int(dy))

    def move_to(self, x, y):
        with self.lock:
            self.lib.xdo_move_mouse(self.xdo, int(x), int(y), 0)

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
            if self.path == "/api/system":
                return self._json(system_info())
            if self.path == "/api/remote":
                return self._json({"url": remote_url()})
            if self.path == "/api/watchlist":
                return self._json(watchlist.load())
            if self.path.startswith(("/api/trailers?", "/api/details?")):
                return self._lookup()
            if self.path == "/api/tour/movy":
                return self._json(movy_tour.status())
            if self.path.startswith("/api/tour/phone-cmds?"):  # ?start, or ?after=<number>
                try:
                    after = int(self.path.rsplit("after=", 1)[1]) if "after=" in self.path else None
                except ValueError:
                    after = None
                return self._json(tour_phone_cmds(after))
            return self._json({"error": "not found"}, 404)
        return super().do_GET()

    def _lookup(self):
        """A film's trailers, or a show's details, from TMDB (for the watchlist, on the TV or the phone)"""
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        title = (q.get("title") or [""])[0].strip()[:80]
        year = (q.get("year") or [""])[0]
        kind = "show" if (q.get("kind") or [""])[0] == "show" else "film"
        if not title:
            return self._json({"error": "no title"}, 400)
        look = trailers.find if self.path.startswith("/api/trailers?") else trailers.details
        return self._json(look(title, int(year) if year.isdigit() else None, kind))

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
        if self.path == "/api/layout":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            return self._json(*save_layout(body if isinstance(body, dict) else {}))
        if self.path == "/api/watchlist":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            return self._json(*watchlist.save(body))
        if self.path == "/api/tmdb-key":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            return self._json(*trailers.save_key(body.get("key") if isinstance(body, dict) else None))
        if self.path == "/api/watchnow":
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                title = str(body.get("title", "")).strip()[:80]
            except (ValueError, AttributeError):
                return self._json({"error": "bad request"}, 400)
            if not title:
                return self._json({"error": "no title"}, 400)
            return self._json(*watch_now(title))
        if len(parts) == 3 and parts[:2] == ["api", "close"]:
            return self._json(*close_item(parts[2]))
        if len(parts) == 4 and parts[:3] == ["api", "media", "focus"]:
            return self._json({"ok": now_playing.focus(parts[3])})
        if self.path == "/api/desktop":
            return self._json(show_desktop())
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
        if self.path.startswith("/api/tour/") and not self.path.startswith("/api/tour/movy"):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self._json({"error": "bad request"}, 400)
            what = self.path[len("/api/tour/"):]
            if what == "caption":
                return self._json(tour_caption(body))
            if what == "phone":
                return self._json(tour_phone(bool(body.get("on"))))
            if what == "pointer":
                return self._json(tour_pointer(body.get("path") or [], body.get("ms") or 800, bool(body.get("click"))))
            if what == "phone-cmd":
                return self._json(tour_phone_cmd(body))
            if what == "phone-ready":
                phone_ready.set()
                return self._json({"ok": True})
            if what == "dock-home":
                return self._json(tour_dock_home())
            if what == "claude":
                return self._json(tour_claude(str(body.get("prompt") or "")[:500]))
            if what == "end":
                return self._json(tour_end())
            return self._json({"error": "not found"}, 404)
        if self.path.startswith("/api/tour/movy"):  # the tour's visit to Movy; ?step=4 of 15 for its caption
            return self._json(*tour_movy((urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("step") or [""])[0][:20]))
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

    def end_headers(self):
        # The remote page and its files: always checked with the Deck before a saved copy is used, so a
        # phone (or its home-screen shortcut) gets a new version without anyone clearing anything
        if not self.path.startswith(("/api/", "/appicon/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        if self.path.startswith("/api/screen"):
            return self._screen()
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
            if self.path == "/api/watchlist":
                return self._json(watchlist.load())
            if self.path.startswith(("/api/trailers?", "/api/details?")):
                return self._lookup()
            return self._json({"error": "not found"}, 404)
        return SimpleHTTPRequestHandler.do_GET(self)  # the remote page itself; Handler's localhost check doesn't apply

    def _screen(self):
        """The TV's picture, about ten a second, as one long response the phone's <img> shows as it
        comes (multipart/x-mixed-replace). The token comes in the URL: an <img> can't send headers.
        Ends when the phone stops watching; a phone that vanishes is dropped after 10 s of no reading."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if not secrets.compare_digest((query.get("t") or [""])[0], remote_token()):
            return self._json({"error": "pair again"}, 401)
        if screen is None:
            return self._json({"error": "no screen to show"}, 503)
        self.close_connection = True
        self.connection.settimeout(10)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        frames = screen.frames()
        try:
            for frame in frames:
                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(frame) + frame + b"\r\n")
                self.wfile.flush()
        except OSError:
            pass  # the phone stopped watching
        finally:
            frames.close()

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
        # The watchlist, on the phone: the same lists as the TV's, and Watch on Movy starts it on the TV
        if self.path == "/api/watchlist":
            return self._json(*watchlist.save(body))
        if self.path == "/api/watchnow":
            title = str(body.get("title", "")).strip()[:80] if isinstance(body, dict) else ""
            if not title:
                return self._json({"error": "no title"}, 400)
            return self._json(*watch_now(title))
        if self.path == "/api/move":
            pointer.move(max(-400, min(400, body.get("dx", 0))), max(-400, min(400, body.get("dy", 0))))
            return self._json({"ok": True})
        if self.path == "/api/click":
            button = body.get("button", 1)
            if button not in (1, 2, 3):
                return self._json({"error": "bad button"}, 400)
            pointer.click(button)
            return self._json({"ok": True})
        if self.path == "/api/tap":
            # A tap on the TV's picture on the phone: the pointer goes there and clicks (or right-clicks)
            button = body.get("button", 1)
            if screen is None or button not in (1, 3):
                return self._json({"error": "can't"}, 400)
            try:
                x, y = screen.point(float(body.get("x")), float(body.get("y")))
            except (TypeError, ValueError):
                return self._json({"error": "bad spot"}, 400)
            pointer.move_to(x, y)
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
