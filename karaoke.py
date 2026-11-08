"""Karaoke: songs from ~/Music/Karaoke, time-synced lyrics from LRCLIB (free, no key), and a backing
track for each song with the singing taken out, made on the Deck by Demucs (once per song, in the
background, slowest priority) and kept in ~/.cache/deck-tv/karaoke/.

A song is any audio file in the folder (or a folder inside it). Its artist and title come from its
tags, or from a file name like "Artist - Title.mp3". An .lrc file with the same name beside it
wins over LRCLIB, for a song LRCLIB doesn't have.
"""
import hashlib
import json
import os
import queue
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SONGS = Path.home() / "Music" / "Karaoke"
CACHE = Path.home() / ".cache" / "deck-tv" / "karaoke"
PYTHON = ROOT / "karaoke-env" / "bin" / "python"  # the vocal remover's own Python (install.sh)
SEPARATE = ROOT / "separate.py"
AUDIO = {".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".webm", ".aac"}
TRACKS = {"backing": "backing.opus", "vocals": "vocals.opus"}

_lock = threading.Lock()
_jobs = queue.Queue()
_waiting = []  # song ids, in the order they'll be made
_working = {}  # the one being made: {"id", "started"}
_files = {}  # id -> Path, from the last scan


def separator():
    """Is the vocal remover installed?"""
    return PYTHON.is_file()


def _id(path):
    return hashlib.sha1(str(path.relative_to(SONGS)).encode()).hexdigest()[:12]


def _info(path, sid):
    """Artist, title and length, from the file's tags (kept until the file changes)."""
    st = path.stat()
    stamp = f"{st.st_size}:{int(st.st_mtime)}"
    saved = CACHE / sid / "info.json"
    try:
        info = json.loads(saved.read_text())
        if info.get("stamp") == stamp:
            return info
    except (OSError, ValueError):
        pass
    tags, duration = {}, 0.0
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", str(path)],
                             capture_output=True, text=True, timeout=20).stdout
        fmt = json.loads(out).get("format", {})
        tags = {k.lower(): v for k, v in fmt.get("tags", {}).items()}
        duration = float(fmt.get("duration") or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    artist, title = tags.get("artist", "").strip(), tags.get("title", "").strip()
    if not (artist and title):
        stem = path.stem.replace("_", " ")
        a, sep, t = stem.partition(" - ")
        artist, title = (a.strip(), t.strip()) if sep else (artist, title or stem.strip())
        if not artist and path.parent != SONGS:  # Karaoke/Artist/Title.mp3
            artist = path.parent.name
    info = {"stamp": stamp, "artist": artist, "title": title, "duration": round(duration, 1)}
    try:
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(info))
    except OSError:
        pass
    return info


def _state(sid):
    folder = CACHE / sid
    if (folder / TRACKS["backing"]).is_file():
        return "ready"
    with _lock:
        if _working.get("id") == sid:
            return "working"
        if sid in _waiting:
            return "waiting"
    if (folder / "failed.txt").is_file():
        return "failed"
    return "waiting" if separator() else "unavailable"


def songs():
    """Every song in the folder, sorted by artist then title, with how its backing track is getting on.
    Songs without a backing track yet join the queue to be made."""
    SONGS.mkdir(parents=True, exist_ok=True)
    found = {}
    for path in sorted(SONGS.rglob("*")):
        if path.suffix.lower() in AUDIO and path.is_file() and not path.name.startswith("."):
            found[_id(path)] = path
    _files.clear()
    _files.update(found)
    out = []
    for sid, path in found.items():
        info = _info(path, sid)
        lyr = CACHE / sid / "lyrics.json"
        out.append({"id": sid, "artist": info["artist"], "title": info["title"], "duration": info["duration"],
                    "file": str(path.relative_to(SONGS)), "backing": _state(sid),
                    "lyrics": _lyrics_kind(lyr) if lyr.is_file() or path.with_suffix(".lrc").is_file() else None})
        if out[-1]["backing"] == "waiting":
            _queue(sid)
    out.sort(key=lambda s: (s["artist"].lower(), s["title"].lower()))
    with _lock:
        working = dict(_working)
        waiting = list(_waiting)
    return {"songs": out, "folder": str(SONGS), "separator": separator(),
            "working": {"id": working["id"], "seconds": int(time.time() - working["started"])} if working else None,
            "waiting": waiting}


def _lyrics_kind(file):
    try:
        data = json.loads(file.read_text())
        return "synced" if data.get("synced") else "plain" if data.get("plain") else "none"
    except (OSError, ValueError):
        return "synced"  # an .lrc beside the song


def lyrics(sid):
    """{"synced": "[mm:ss.xx] line…" or "", "plain": "…" or "", "source": …}; looked up once, then kept.
    An .lrc file beside the song wins."""
    path = _files.get(sid) or (songs() and _files.get(sid))
    if not path:
        return {"error": "no such song"}
    lrc = path.with_suffix(".lrc")
    if lrc.is_file():
        try:
            return {"synced": lrc.read_text(errors="replace"), "plain": "", "source": "file"}
        except OSError:
            pass
    saved = CACHE / sid / "lyrics.json"
    try:
        return json.loads(saved.read_text())
    except (OSError, ValueError):
        pass
    info = _info(path, sid)
    data = _lrclib(info["artist"], info["title"], info["duration"])
    if data is None:  # couldn't reach LRCLIB: try again next time
        return {"synced": "", "plain": "", "source": "offline"}
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(data))
    return data


def _lrclib(artist, title, duration):
    def get(url):
        req = urllib.request.Request(url, headers={"User-Agent": "DeckTV-karaoke/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            return {} if e.code == 404 else None
        except (OSError, ValueError):
            return None

    def pick(rec):
        return {"synced": rec.get("syncedLyrics") or "", "plain": rec.get("plainLyrics") or "", "source": "lrclib"}

    base = "https://lrclib.net/api/"
    if artist and duration:
        rec = get(base + "get?" + urllib.parse.urlencode(
            {"artist_name": artist, "track_name": title, "duration": round(duration)}))
        if rec is None:
            return None
        if rec.get("syncedLyrics"):
            return pick(rec)
    params = {"track_name": title, **({"artist_name": artist} if artist else {})}
    found = get(base + "search?" + urllib.parse.urlencode(params))
    if found is None:
        return None
    if not found and artist:  # tags spelt differently: try it as one search
        found = get(base + "search?" + urllib.parse.urlencode({"q": f"{artist} {title}"})) or []
    found = [r for r in found if not r.get("instrumental")]
    # Synced and about the right length first (a live or extended version is timed differently)
    near = lambda r: abs((r.get("duration") or 0) - duration) <= 4 if duration else True
    for test in (lambda r: r.get("syncedLyrics") and near(r), lambda r: r.get("syncedLyrics"),
                 lambda r: r.get("plainLyrics")):
        rec = next((r for r in found if test(r)), None)
        if rec:
            return pick(rec)
    return {"synced": "", "plain": "", "source": "none"}


def track(sid, name):
    """The file for one of a song's tracks: backing, vocals or the original."""
    if name == "original":
        return _files.get(sid)
    file = CACHE / sid / TRACKS.get(name, "")
    return file if name in TRACKS and file.is_file() else None


def _queue(sid, first=False):
    with _lock:
        if _working.get("id") == sid:
            return
        if sid in _waiting:
            if not first:
                return
            _waiting.remove(sid)
        _waiting.insert(0, sid) if first else _waiting.append(sid)
    _jobs.put(sid)


def prepare(sid):
    """Make this song's backing track next (it was picked to sing). A failed one is tried again."""
    if not separator() or sid not in _files:
        return {"ok": False}
    (CACHE / sid / "failed.txt").unlink(missing_ok=True)
    _queue(sid, first=True)
    return {"ok": True}


def run():
    """Makes backing tracks one at a time, in the queue's order, at the lowest priority."""
    while True:
        _jobs.get()
        with _lock:
            if not _waiting:
                continue
            sid = _waiting.pop(0)
            _working.update(id=sid, started=time.time())
        path, folder = _files.get(sid), CACHE / sid
        try:
            if path and separator() and not (folder / TRACKS["backing"]).is_file():
                folder.mkdir(parents=True, exist_ok=True)
                r = subprocess.run(["nice", "-n", "19", str(PYTHON), str(SEPARATE), str(path), str(folder)],
                                   capture_output=True, text=True, env={**os.environ, "OMP_NUM_THREADS": "4"})
                if r.returncode != 0:
                    (folder / "failed.txt").write_text((r.stderr or r.stdout)[-4000:])
        except OSError as e:
            (folder / "failed.txt").write_text(str(e))
        finally:
            with _lock:
                _working.clear()
