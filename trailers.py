"""Deck TV - trailers for the watchlist, from TMDB (The Movie Database).

TMDB lists each film's videos, nearly all of them YouTube uploads: the official trailers first, then
teasers. The page plays them in YouTube's embedded player, moving on to the next if one won't play
there. TMDB needs a free key (themoviedb.org: Settings, API); it's kept in
~/.config/deck-tv/tmdb.json, readable only by you, and never sent to the page.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KEY_FILE = Path.home() / ".config/deck-tv/tmdb.json"
API = "https://api.themoviedb.org/3"

_found = {}  # (title, year) -> the answer, for the life of the server


def get_key():
    try:
        return json.loads(KEY_FILE.read_text()).get("key") or None
    except (OSError, ValueError, AttributeError):
        return None


def _call(path, key, **params):
    """A TMDB request. The long Read Access Token goes in a header; the short v3 key in the URL."""
    headers = {"Accept": "application/json"}
    if "." in key and len(key) > 60:
        headers["Authorization"] = "Bearer " + key
    else:
        params["api_key"] = key
    url = f"{API}{path}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=8) as r:
        return json.load(r)


def save_key(key):
    """Check the key with TMDB, then keep it. Returns (body, status)."""
    key = re.sub(r"\s+", "", str(key or ""))[:400]
    if not key:
        return {"error": "Paste your TMDB key"}, 400
    try:
        _call("/configuration", key)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"error": "TMDB didn't accept that key"}, 400
        return {"error": f"TMDB answered {e.code}: try again in a moment"}, 502
    except OSError:
        return {"error": "Couldn't reach TMDB: is the Deck online?"}, 502
    KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"key": key}, f)
    _found.clear()
    return {"ok": True}, 200


def _videos(kind, tmdb_id, key):
    vids = _call(f"/{kind}/{tmdb_id}/videos", key, include_video_language="en,null").get("results", [])
    vids = [v for v in vids if v.get("site") == "YouTube" and re.fullmatch(r"[\w-]{11}", v.get("key", ""))]
    # Official trailers first, then other trailers, then teasers; each oldest first (the original
    # release's trailer before an anniversary re-release's). A show with none: its opening credits or a clip.
    rank = {"Trailer": 0, "Teaser": 1, "Opening Credits": 2, "Clip": 3}
    vids = [v for v in vids if v.get("type") in rank]
    if any(v["type"] in ("Trailer", "Teaser") for v in vids):
        vids = [v for v in vids if v["type"] in ("Trailer", "Teaser")]
    vids.sort(key=lambda v: (rank[v["type"]], not v.get("official"), v.get("published_at") or ""))
    return [{"id": v["key"], "name": v.get("name", "")} for v in vids[:8]]


def _matches(kind, title, year, key):
    """TMDB's best matches, as (catalogue, hit): a show is looked for among TV first (by the year it
    first aired), a film among films; then the other, in case it was put down as the wrong one."""
    order = ("tv", "movie") if kind == "show" else ("movie", "tv")
    year_param = {"tv": "first_air_date_year", "movie": "year"}
    for cat in order:
        for params in (({year_param[cat]: year},) if year else ()) + ({},):
            hits = _call(f"/search/{cat}", key, query=title, include_adult="false", **params).get("results", [])
            if hits:
                return [(cat, h) for h in hits[:3]]
    return []


def _clean_title(title):
    return re.sub(r"\s*\([^)]*\)", "", title).strip() or title


def _tmdb_errors(fn):
    """TMDB's failures as answers the page can show."""
    try:
        return fn()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"needKey": True, "error": "TMDB no longer accepts the saved key"}
        return {"error": f"TMDB answered {e.code}"}
    except OSError:
        return {"error": "Couldn't reach TMDB"}


def find(title, year=None, kind="film"):
    """Its trailers: {"videos": [{"id", "name"}...]}, {"needKey": True}, or {"error": ...}."""
    key = get_key()
    if not key:
        return {"needKey": True}
    title = _clean_title(title)
    if ("videos", kind, title, year) in _found:
        return _found[("videos", kind, title, year)]

    def look():
        for cat, hit in _matches(kind, title, year, key):  # the best match, or the next if it has no trailer
            videos = _videos(cat, hit["id"], key)
            if videos:
                return {"videos": videos, "tmdb": f"{cat}/{hit['id']}"}
        return {"videos": []}
    out = _tmdb_errors(look)
    if "videos" in out:
        _found[("videos", kind, title, year)] = out
    return out


IMG = "https://image.tmdb.org/t/p/"


def details(title, year=None, kind="show"):
    """A show's facts: seasons, episodes, episode length, years running, status, plus its overview and
    poster. {"needKey": True} without a key; {} if TMDB doesn't know it."""
    key = get_key()
    if not key:
        return {"needKey": True}
    title = _clean_title(title)
    if ("details", kind, title, year) in _found:
        return _found[("details", kind, title, year)]

    def look():
        hits = [(c, h) for c, h in _matches(kind, title, year, key) if c == "tv"]
        if not hits:
            return {}
        d = _call(f"/tv/{hits[0][1]['id']}", key)
        first, last = (d.get("first_air_date") or "")[:4], (d.get("last_air_date") or "")[:4]
        ended = d.get("status") in ("Ended", "Canceled")
        years = first and (first if ended and last == first else f"{first}–{last if ended else ''}")
        # Episode length: TMDB's list of typical lengths is often empty; the latest episode's will do
        mins = (d.get("episode_run_time") or [None])[0] or ((d.get("last_episode_to_air") or {}).get("runtime"))
        poster = d.get("poster_path")
        return {"tmdb": f"tv/{d['id']}", "name": d.get("name"), "seasons": d.get("number_of_seasons") or None,
                "episodes": d.get("number_of_episodes") or None, "runtime": mins or None, "years": years or None,
                "year": int(first) if first.isdigit() else None, "status": d.get("status"),
                "overview": d.get("overview") or None,
                "poster": poster and IMG + "w342" + poster, "posterBig": poster and IMG + "w780" + poster}
    out = _tmdb_errors(look)
    if "error" not in out and not out.get("needKey"):
        _found[("details", kind, title, year)] = out
    return out
