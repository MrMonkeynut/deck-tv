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
    # release's trailer before an anniversary re-release's)
    rank = {"Trailer": 0, "Teaser": 1}
    vids = [v for v in vids if v.get("type") in rank]
    vids.sort(key=lambda v: (rank[v["type"]], not v.get("official"), v.get("published_at") or ""))
    return [{"id": v["key"], "name": v.get("name", "")} for v in vids[:8]]


def find(title, year=None):
    """The film's trailers: {"videos": [{"id", "name"}...]}, {"needKey": True}, or {"error": ...}."""
    key = get_key()
    if not key:
        return {"needKey": True}
    title = re.sub(r"\s*\([^)]*\)", "", title).strip() or title
    if (title, year) in _found:
        return _found[(title, year)]
    try:
        # A film first (with its year, then without); a show if no film matches
        hits = []
        for kind, params in (("movie", {"year": year} if year else None), ("movie", {}), ("tv", {})):
            if params is None:
                continue
            hits = [(kind, h) for h in _call(f"/search/{kind}", key, query=title, include_adult="false", **params).get("results", [])]
            if hits:
                break
        out = {"videos": []}
        for kind, hit in hits[:3]:  # the best match, or the next if it has no trailer
            videos = _videos(kind, hit["id"], key)
            if videos:
                out = {"videos": videos, "tmdb": f"{kind}/{hit['id']}"}
                break
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return {"needKey": True, "error": "TMDB no longer accepts the saved key"}
        return {"error": f"TMDB answered {e.code}"}
    except OSError:
        return {"error": "Couldn't reach TMDB"}
    _found[(title, year)] = out
    return out
