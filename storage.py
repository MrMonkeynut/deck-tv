"""Storage: what's using the space on the Deck (and on its microSD card or a USB drive), folder by
folder and by kind of file, and a careful way to clear some of it.

Each drive is walked in the background at the lowest priority, counting the space files really take
on the disk (each hard-linked file once), and the result is kept in ~/.cache/deck-tv/storage/ so the
page opens straight away. A folder's files are always read fresh when it's opened.

Nothing here deletes anything straight away. Remove moves things to the Trash (the same one Dolphin
uses, so they can be put back from either), and the space only comes back when the Trash is emptied,
which is asked for on its own. Some places can't be removed from here at all (the folders everything
else lives in, Steam's and the apps' own files, saves kept by Steam, anything marked Keep); a Steam
game is uninstalled through Steam instead. Everything moved, put back or emptied is written to
~/.local/share/deck-tv/storage.log.
"""
import heapq
import json
import os
import pickle
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

HOME = Path.home()
H = str(HOME)
STEAM = f"{H}/.local/share/Steam"
CACHE_DIR = HOME / ".cache/deck-tv/storage"
SETTINGS = HOME / ".config/deck-tv/storage.json"
LOG = HOME / ".local/share/deck-tv/storage.log"
HOME_TRASH = f"{H}/.local/share/Trash"
UID = os.getuid()
FORMAT = 4  # bump when what's saved changes
TOP_FILES = 3000
RECENT = 3 * 86400
STALE = 12 * 3600  # a scan older than this is redone when the page opens

# The kinds of space, in the order the bar shows them (the page has their names and colours)
CATS = ["games", "saves", "apps", "video", "music", "pictures", "docs", "archives", "cache", "system", "other"]
GAMES, SAVES, APPS, VIDEO, MUSIC, PICTURES, DOCS, ARCHIVES, CACHE, SYSTEM, OTHER = range(len(CATS))

EXT = {}
for _cat, _exts in {
    VIDEO: "mp4 mkv avi mov webm m4v wmv flv ts m2ts mpg mpeg 3gp vob",
    MUSIC: "mp3 flac m4a ogg opus wav aac wma aiff alac ape mka",
    PICTURES: "jpg jpeg png gif webp bmp heic heif tif tiff raw cr2 nef arw dng avif svg psd xcf kra",
    DOCS: "pdf doc docx odt xls xlsx ods ppt pptx odp txt md epub mobi azw3 cbz cbr csv rtf json xml log",
    ARCHIVES: "zip 7z rar tar gz tgz xz zst bz2 iso img qcow2 vdi vmdk vhd vhdx deb rpm dmg part crdownload",
    GAMES: "nsp xci nsz wbfs rvz gcz cso chd pbp 3ds cia nds gba gbc gb sfc smc n64 z64 v64 nes md gen sav srm",
    APPS: "appimage exe msi flatpak flatpakref",
}.items():
    for _e in _exts.split():
        EXT[_e] = _cat
EXT["sav"] = EXT["srm"] = SAVES

# Places whose files are all one kind, whatever they're called
PLACES = {
    f"{H}/.cache": CACHE, HOME_TRASH: CACHE, f"{H}/.thumbnails": CACHE,
    f"{H}/.var/app": APPS, f"{H}/.local/share/flatpak": APPS, f"{H}/Applications": APPS,
    f"{H}/.local/share/containers": APPS, STEAM: APPS,
    f"{STEAM}/userdata": SAVES, f"{H}/Emulation/saves": SAVES, f"{H}/Games/Heroic/Prefixes": SAVES,
    f"{H}/Emulation/roms": GAMES, f"{H}/Emulation/bios": GAMES, f"{H}/Emulation/storage": GAMES, f"{H}/Games": GAMES,
    "/home/.steamos": SYSTEM, "/home/.steamos/offload/var/lib/flatpak": APPS,
}
STEAMAPPS = {"shadercache": CACHE, "compatdata": SAVES, "common": GAMES, "downloading": GAMES, "temp": GAMES,
             "workshop": GAMES, "sourcemods": GAMES, "music": MUSIC}
ROOTFS = {"bin", "boot", "etc", "lib", "lib32", "lib64", "usr", "var", "sbin", "opt", "root", "srv", "proc",
          "sys", "dev", "run", "tmp", "mnt", "media", "lost+found", "snap"}

# Folders that hold everything else: never removed themselves (what's inside them can be)
HOLDS = {
    "/home": "The folder every account's files are in",
    H: "Your home folder",
    f"{H}/.local": "Where apps keep their data", f"{H}/.local/share": "Where apps keep their data",
    f"{H}/.config": "Where apps keep their settings", f"{H}/.var": "The apps' own folders",
    f"{H}/.var/app": "The apps' own folders", f"{H}/.cache": "The folder apps keep caches in",
    f"{H}/Desktop": "Your Desktop folder", f"{H}/Documents": "Your Documents folder",
    f"{H}/Downloads": "Your Downloads folder", f"{H}/Music": "Your Music folder",
    f"{H}/Pictures": "Your Pictures folder", f"{H}/Videos": "Your Videos folder",
    f"{H}/Public": "Your Public folder", f"{H}/Templates": "Your Templates folder",
    f"{H}/Applications": "Where AppImage apps live", f"{H}/Emulation": "EmuDeck's folder",
    f"{H}/Emulation/roms": "EmuDeck's games", f"{H}/Music/Karaoke": "Deck TV's karaoke songs",
}
# Places that can't be removed from here at all (nor anything in them)
NO_GO = {
    "/home/.steamos": "SteamOS's own files",
    f"{H}/.local/share/flatpak": "Apps installed from Discover: uninstall them in Discover",
    f"{H}/.local/share/deck-tv": "Deck TV itself", f"{H}/.config/deck-tv": "Deck TV's settings",
    f"{H}/.ssh": "Keys for signing in to other computers", f"{H}/.gnupg": "Encryption keys",
    f"{H}/.pki": "Certificates", f"{H}/.local/share/kwalletd": "Saved passwords",
    f"{H}/.local/share/containers": "The Linux boxes apps run in (Claude's Ubuntu box)",
    f"{STEAM}/userdata": "Your Steam settings, screenshots and saves",
    f"{H}/.steam": "Steam's own files", HOME_TRASH: "The Trash: empty it from Trash",
}

_lock = threading.RLock()
_ops = threading.Lock()  # one remove / restore / empty at a time
_jobs = queue.Queue()
_drives = {}  # id -> {"root", "tree", "exts", "top", "at", "files", "dirs", "took", "scanning", "progress", "error"}
_steam = {"at": 0, "apps": {}, "libs": [], "shortcuts": {}}
_names = {}  # Steam app id -> name, for games no longer installed (from Steam's store)
_flatpaks = {}
_trash_sizes = {}  # (trash dir, name, deleted) -> (bytes, files)


class Node:
    """A folder: its space (by kind), how many files, the newest change inside, and its folders"""
    __slots__ = ("name", "size", "files", "mtime", "cats", "kids", "cat")

    def __init__(self, name, cat):
        self.name, self.cat = name, cat  # cat: the kind its place makes its files (None: by their own kind)
        self.size = self.files = 0
        self.mtime = 0.0
        self.cats = [0] * len(CATS)
        self.kids = None


# ---------- drives ----------
def _mounts():
    """Internal storage (/home) and each microSD card or USB drive: id -> (name, kind, root)"""
    try:
        root = "/home" if os.stat("/home").st_dev == os.stat(H).st_dev else H  # the home folder, when it's a drive of its own
    except OSError:
        root = H
    found = {"internal": ("Internal storage", "internal", root)}
    seen = set()
    try:
        lines = Path("/proc/mounts").read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        dev, mnt, fstype = line.split()[:3]
        mnt = mnt.replace("\\040", " ")
        if not mnt.startswith("/run/media/") or fstype.startswith("fuse") or dev in seen:
            continue
        seen.add(dev)
        kind = "sd" if "mmcblk" in dev else "usb"
        name = "microSD card" if kind == "sd" else os.path.basename(mnt) or "USB drive"
        found[f"{kind}-{re.sub(r'[^A-Za-z0-9]+', '-', os.path.basename(mnt)).strip('-').lower() or 'drive'}"] = (name, kind, mnt)
    return found


def _drive_of(path, mounts=None):
    best = None
    for did, (_, _, root) in (mounts or _mounts()).items():
        if (path == root or path.startswith(root.rstrip("/") + "/")) and (best is None or len(root) > len(best[1])):
            best = (did, root)
    return best


def _space(root):
    st = os.statvfs(root)
    return {"total": st.f_blocks * st.f_frsize, "free": st.f_bavail * st.f_frsize,
            "used": (st.f_blocks - st.f_bfree) * st.f_frsize, "reserved": (st.f_bfree - st.f_bavail) * st.f_frsize}


def _state(did, root):
    """The drive's last scan (loaded from disk the first time it's asked for)"""
    with _lock:
        d = _drives.get(did)
        if d is None or d["root"] != root:
            d = _drives[did] = {"root": root, "tree": None, "exts": {}, "top": [], "at": 0, "files": 0, "dirs": 0,
                                "took": 0, "scanning": False, "progress": None, "error": None}
            try:
                with open(CACHE_DIR / f"{did}.pickle", "rb") as f:
                    saved = pickle.load(f)
                if saved.get("format") == FORMAT and saved.get("root") == root:
                    d.update({k: saved[k] for k in ("tree", "exts", "top", "at", "files", "dirs", "took")})
            except Exception:
                pass
        return d


def _save(did, d):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_DIR / f"{did}.pickle.tmp"
        with open(tmp, "wb") as f:
            pickle.dump({"format": FORMAT, **{k: d[k] for k in ("root", "tree", "exts", "top", "at", "files", "dirs", "took")}},
                        f, protocol=pickle.HIGHEST_PROTOCOL)
        tmp.replace(CACHE_DIR / f"{did}.pickle")
    except Exception as e:
        print(f"storage: couldn't save the scan: {e}", file=sys.stderr)


# ---------- what kind of space a place is ----------
def _place(path, name, pname, pcat, top, rootfs):
    """The kind a folder's files are because of where it is, or None to go by each file's own kind"""
    if pname == "steamapps" and name in STEAMAPPS:
        return STEAMAPPS[name]
    if path in PLACES:
        return PLACES[path]
    if top and (name == "lost+found" or rootfs and name in ROOTFS):
        return SYSTEM
    if name.startswith(".Trash-") or name == ".Trash":
        return CACHE
    if pcat is None and name.lower() in ("roms", "games"):
        return GAMES
    if pcat == APPS and name in ("cache", "Cache") and pname not in ("app", "runtime"):  # an app's own cache
        return CACHE
    return pcat


def _is_rootfs(root):
    return os.path.isdir(os.path.join(root, "etc")) and os.path.isdir(os.path.join(root, "usr"))


def _cat_at(root, path):
    """The kind a folder's files are, worked out from the drive's top down to it"""
    cat, pname, cur, rootfs = None, "", root.rstrip("/"), _is_rootfs(root)
    rel = os.path.relpath(path, root)
    if rel == ".":
        return None
    for i, part in enumerate(rel.split("/")):
        cur = f"{cur}/{part}"
        cat = _place(cur, part, pname, cat, i == 0, rootfs)
        pname = part
    return cat


def _ext(name):
    base, dot, ext = name.rpartition(".")
    return ext.lower() if dot and base and len(ext) <= 10 and " " not in ext else ""


def _file_cat(dir_cat, name):
    return dir_cat if dir_cat is not None else EXT.get(_ext(name), OTHER)


# ---------- scanning ----------
class _Walk:
    def __init__(self, root, progress=None):
        self.root, self.rootfs = root, _is_rootfs(root)
        self.seen = set()
        self.exts = {}
        self.top = []
        self.files = self.dirs = self.bytes = 0
        self.progress = progress
        self.stop = False

    def file(self, path, size, mtime, cat):
        if len(self.top) < TOP_FILES:
            heapq.heappush(self.top, (size, path, mtime, cat))
        elif size > self.top[0][0]:
            heapq.heappushpop(self.top, (size, path, mtime, cat))

    def walk(self, path, node, dev, top=False, depth=0):
        try:
            it = os.scandir(path)
        except OSError:
            return
        own = node.cats
        with it:
            for e in it:
                if self.stop:
                    return
                try:
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISDIR(s.st_mode):
                    if s.st_dev != dev or depth > 120:  # another drive mounted in here
                        continue
                    kid = Node(e.name, _place(e.path, e.name, node.name, node.cat, top, self.rootfs))
                    kid.mtime = s.st_mtime
                    self.walk(e.path, kid, dev, depth=depth + 1)
                    if node.kids is None:
                        node.kids = {}
                    node.kids[e.name] = kid
                    node.size += kid.size
                    node.files += kid.files
                    node.mtime = max(node.mtime, kid.mtime)
                    for i, v in enumerate(kid.cats):
                        if v:
                            own[i] += v
                    self.dirs += 1
                elif stat.S_ISREG(s.st_mode):
                    if s.st_nlink > 1:
                        if s.st_ino in self.seen:
                            continue
                        self.seen.add(s.st_ino)
                    used = s.st_blocks * 512
                    ext = _ext(e.name)
                    cat = node.cat if node.cat is not None else EXT.get(ext, OTHER)
                    own[cat] += used
                    node.size += used
                    node.files += 1
                    node.mtime = max(node.mtime, s.st_mtime)
                    x = self.exts.get(ext)
                    if x is None:
                        x = self.exts[ext] = [0, 0, {}]
                    x[0] += 1
                    x[1] += used
                    x[2][cat] = x[2].get(cat, 0) + used
                    if used >= 1 << 20:
                        self.file(e.path, used, s.st_mtime, cat)
                    self.files += 1
                    self.bytes += used
                    if self.progress is not None and not self.files % 2000:
                        self.progress.update(files=self.files, bytes=self.bytes)


def _measure(root, path):
    """A file or folder as it is now: (Node or None, bytes by kind, files, file types, big files)"""
    try:
        s = os.stat(path, follow_symlinks=False)
    except OSError:
        return None
    cat = _cat_at(root, path)
    w = _Walk(root)
    if stat.S_ISDIR(s.st_mode):
        node = Node(os.path.basename(path), cat)
        node.mtime = s.st_mtime
        w.walk(path, node, s.st_dev, top=path == root.rstrip("/"))
        return node, node.cats, node.files, w.exts, w.top
    cats = [0] * len(CATS)
    used = s.st_blocks * 512 if stat.S_ISREG(s.st_mode) else 0
    pcat = _cat_at(root, os.path.dirname(path)) if os.path.dirname(path) != root.rstrip("/") else None
    cats[_file_cat(pcat, os.path.basename(path))] += used
    if used >= 1 << 20:
        w.file(path, used, s.st_mtime, _file_cat(pcat, os.path.basename(path)))
    fc = _file_cat(pcat, os.path.basename(path))
    return None, cats, 1 if stat.S_ISREG(s.st_mode) else 0, {_ext(path): [1, used, {fc: used}]} if used else {}, w.top


def _scan(did):
    mounts = _mounts()
    if did not in mounts:
        return
    root = mounts[did][2]
    d = _state(did, root)
    with _lock:
        d["scanning"], d["error"] = True, None
        d["progress"] = {"files": 0, "bytes": 0, "started": time.time()}
    try:
        os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 19)
    except (OSError, AttributeError):
        pass
    started = time.time()
    try:
        prog = d["progress"]
        w = _Walk(root, prog)
        tree = Node(root, None)
        s = os.stat(root)
        tree.mtime = s.st_mtime
        w.walk(root, tree, s.st_dev, top=True)
        with _lock:
            d.update(tree=tree, exts=w.exts, top=sorted(w.top, reverse=True), at=time.time(),
                     files=w.files, dirs=w.dirs, took=round(time.time() - started, 1))
        _save(did, d)
        _lookup_names()
    except Exception as e:
        d["error"] = str(e)
        print(f"storage: scan of {root} failed: {e}", file=sys.stderr)
    finally:
        with _lock:
            d["scanning"], d["progress"] = False, None


def scan(did=None):
    """Scan one drive again (or every one), in the background"""
    for x in ([did] if did else list(_mounts())):
        d = _state(x, _mounts()[x][2]) if x in _mounts() else None
        if d and not d["scanning"]:
            with _lock:
                d["scanning"] = True
                d["progress"] = {"files": 0, "bytes": 0, "started": time.time(), "queued": True}
            _jobs.put(("scan", x))
    return {"ok": True}


def run():
    """The background worker: scans, one at a time"""
    while True:
        job, arg = _jobs.get()
        try:
            if job == "scan":
                _scan(arg)
        except Exception as e:
            print(f"storage: {job} failed: {e}", file=sys.stderr)


# ---------- the tree ----------
def _chain(d, path):
    """The folders from the drive's top down to path (shorter if the scan doesn't have it)"""
    node = d["tree"]
    if node is None:
        return []
    out = [node]
    rel = os.path.relpath(path, d["root"])
    if rel == ".":
        return out
    for part in rel.split("/"):
        node = node.kids.get(part) if node.kids else None
        if node is None:
            break
        out.append(node)
    return out


def _node(path):
    hit = _drive_of(path)
    if not hit:
        return None
    d = _state(hit[0], hit[1])
    with _lock:
        chain = _chain(d, path)
        depth = 0 if os.path.relpath(path, hit[1]) == "." else os.path.relpath(path, hit[1]).count("/") + 1
        return chain[-1] if chain and len(chain) == depth + 1 else None


def _apply(path, m, sign):
    """Take a measured file or folder out of the scan (-1) or put it in (+1), so what's shown stays right
    without scanning again"""
    hit = _drive_of(path)
    if not hit or not m:
        return
    d = _state(*hit)
    node, cats, files, exts, top = m
    with _lock:
        if d["tree"] is None:
            return
        chain = _chain(d, os.path.dirname(path))
        rel = os.path.relpath(os.path.dirname(path), d["root"])
        if len(chain) != (1 if rel == "." else rel.count("/") + 2):
            return  # its folder isn't in the scan (new since): the next scan picks it up
        size = sum(cats)
        for n in chain:
            n.size = max(0, n.size + sign * size)
            n.files = max(0, n.files + sign * files)
            for i, v in enumerate(cats):
                n.cats[i] = max(0, n.cats[i] + sign * v)
            if sign > 0:
                n.mtime = max(n.mtime, time.time())
        parent, name = chain[-1], os.path.basename(path)
        if sign < 0 and parent.kids:
            parent.kids.pop(name, None)
        elif sign > 0 and node is not None:
            if parent.kids is None:
                parent.kids = {}
            parent.kids[name] = node
        for ext, (n, b, by) in exts.items():
            x = d["exts"].setdefault(ext, [0, 0, {}])
            x[0], x[1] = max(0, x[0] + sign * n), max(0, x[1] + sign * b)
            for c, v in by.items():
                x[2][c] = max(0, x[2].get(c, 0) + sign * v)
        d["files"] = max(0, d["files"] + sign * files)
        if sign < 0:
            d["top"] = [t for t in d["top"] if t[1] != path and not t[1].startswith(path + "/")]
        else:
            d["top"] = sorted(d["top"] + top, reverse=True)[:TOP_FILES]


# ---------- Steam and Flatpak names ----------
def _vdf_pairs(text):
    return re.findall(r'"([^"]+)"\s+"([^"]*)"', text)


def steam():
    """Installed Steam games, from every library's manifests: appid -> {name, dir, lastPlayed, size}"""
    with _lock:
        if time.time() - _steam["at"] < 20:
            return _steam
    libs = [STEAM]
    try:
        text = Path(f"{STEAM}/steamapps/libraryfolders.vdf").read_text(errors="replace")
        libs += [p for k, p in _vdf_pairs(text) if k == "path" and p != STEAM]
    except OSError:
        pass
    apps = {}
    for lib in libs:
        try:
            manifests = list(Path(lib, "steamapps").glob("appmanifest_*.acf"))
        except OSError:
            continue
        for mf in manifests:
            try:
                kv = {}
                for k, v in _vdf_pairs(mf.read_text(errors="replace")):
                    kv.setdefault(k, v)
                apps[kv["appid"]] = {"name": kv.get("name", kv["appid"]), "lib": lib,
                                     "dir": f"{lib}/steamapps/common/{kv.get('installdir', '')}",
                                     "lastPlayed": int(kv.get("LastPlayed") or 0), "size": int(kv.get("SizeOnDisk") or 0)}
            except (OSError, KeyError, ValueError):
                continue
    cuts = shortcuts()
    with _lock:
        _steam.update(at=time.time(), apps=apps, libs=libs, shortcuts=cuts)
        return _steam


def shortcuts():
    """Non-Steam games added to Steam: appid -> name (from each account's shortcuts.vdf)"""
    found = {}
    for f in Path(STEAM, "userdata").glob("*/config/shortcuts.vdf"):
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for m in re.finditer(rb"\x02appid\x00(.{4})\x01appname\x00([^\x00]*)\x00", data, re.S | re.I):
            found[str(int.from_bytes(m.group(1), "little"))] = m.group(2).decode(errors="replace").removesuffix(".exe")
    return found


def installed(appid):
    return appid in steam()["apps"] or appid in steam()["shortcuts"]


def _game_name(appid):
    app = steam()["apps"].get(appid)
    if app:
        return app["name"]
    if appid in steam()["shortcuts"]:
        return steam()["shortcuts"][appid]
    if appid.isdigit() and int(appid) >= 1 << 31:
        return "a non-Steam game that's been removed"
    _load_names()
    return _names.get(appid)


def _names_file():
    return CACHE_DIR / "names.json"


def _load_names():
    if not _names:
        try:
            _names.update(json.loads(_names_file().read_text()))
        except (OSError, ValueError):
            _names[""] = ""  # read once, even when there's no file yet


def _lookup_names():
    """Names for games that left something behind but aren't installed any more (Steam's store, once each)"""
    _load_names()
    wanted = set()
    for lib in steam()["libs"]:
        for sub in ("compatdata", "shadercache", "workshop/content"):
            try:
                wanted |= {n for n in os.listdir(f"{lib}/steamapps/{sub}") if n.isdigit() and 0 < int(n) < 1 << 31}
            except OSError:
                pass
    wanted -= set(steam()["apps"]) | set(_names)
    for appid in sorted(wanted)[:60]:
        try:
            url = "https://store.steampowered.com/api/appdetails?filters=basic&appids=" + appid
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Deck TV"}), timeout=8) as r:
                data = json.load(r).get(appid, {})
            _names[appid] = data.get("data", {}).get("name") or ""
        except Exception:
            break  # offline: try again after the next scan
    try:
        _names_file().write_text(json.dumps(_names))
    except OSError:
        pass


def _flatpak_name(app_id):
    if app_id not in _flatpaks:
        name = None
        for base in (f"{H}/.local/share/flatpak/exports/share/applications", "/var/lib/flatpak/exports/share/applications"):
            try:
                for line in Path(base, app_id + ".desktop").read_text(errors="replace").splitlines():
                    if line.startswith("Name="):
                        name = line[5:].strip()
                        break
            except OSError:
                continue
            if name:
                break
        _flatpaks[app_id] = name
    return _flatpaks[app_id]


UNITS = {f"{H}/.local/share/containers", f"{H}/.local/share/flatpak", "/home/.steamos"}  # shown whole in a breakdown


def unit(path):
    """Is this one thing (a game, an app, one game's Proton setup), to show whole rather than open up?"""
    parent = os.path.dirname(path)
    return path in UNITS or parent.endswith(("/steamapps/common", "/steamapps/compatdata", "/steamapps/shadercache",
                                             "/workshop/content")) or parent in (f"{H}/.var/app", f"{H}/VMs")


def label(path):
    """A friendlier name for a folder, when there is one (a game's name for its Steam folders)"""
    parent, name = os.path.dirname(path), os.path.basename(path)
    pname = os.path.basename(parent)
    if parent.endswith("/steamapps/common"):
        for app in steam()["apps"].values():
            if app["dir"] == path:
                return app["name"]
    if pname in ("compatdata", "shadercache") and parent.endswith("/steamapps/" + pname) or parent.endswith("/workshop/content"):
        if name.isdigit():
            g = _game_name(name)
            what = {"compatdata": "Proton setup", "shadercache": "Shader cache", "content": "Workshop items"}[pname]
            return f"{what}: {g or 'game ' + name}" + ("" if installed(name) or int(name) >= 1 << 31 else " (not installed)")
    if parent in (f"{H}/.var/app", f"{H}/.local/share/flatpak/app", f"{H}/.local/share/flatpak/runtime"):
        return _flatpak_name(name)
    return {f"{H}/.cache": "Caches", HOME_TRASH: "Trash", STEAM: "Steam", f"{H}/.var/app": "Flatpak apps' data",
            f"{H}/.local/share/flatpak": "Flatpak apps", f"{H}/.cache/deck-tv": "Deck TV's cache (karaoke tracks)",
            "/home/.steamos": "SteamOS"}.get(path)


# ---------- what's safe to remove ----------
def _settings():
    try:
        s = json.loads(SETTINGS.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def _keeps():
    return [k for k in _settings().get("keep", []) if isinstance(k, str)]


def keep(path, on):
    path = os.path.normpath(path)
    s = _settings()
    keeps = [k for k in s.get("keep", []) if isinstance(k, str) and k != path]
    if on:
        keeps.append(path)
    s["keep"] = sorted(keeps)
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=1))
    _log("keep" if on else "unkeep", path)
    return {"ok": True, "keep": s["keep"]}


def guard(path, keeps=None, mounts=None):
    """How careful to be removing this: (level, why, extra). Levels: "safe" (comes back by itself or
    isn't needed), "check" (your own files: look first), "careful" (an app's or a game's data, or saves),
    "blocked" (not from here). extra can carry {"steam": appid}: uninstall it in Steam instead."""
    p = os.path.normpath(path)
    for k in (_keeps() if keeps is None else keeps):
        if p == k or p.startswith(k + "/"):
            return "blocked", "You marked this Keep" if p == k else "Inside a folder you marked Keep", {"kept": p == k}
        if k.startswith(p + "/"):
            return "blocked", "Has something inside that you marked Keep", {}
    hit = _drive_of(p, mounts)
    if not hit:
        return "blocked", "Not on a drive Deck TV looks after", {}
    if p == hit[1].rstrip("/"):
        return "blocked", "The whole drive", {}
    if p in HOLDS:
        return "blocked", HOLDS[p] + ": what's inside can be removed, not the folder itself", {}
    for area, why in NO_GO.items():
        if p == area or p.startswith(area + "/"):
            return "blocked", why, {}
    if os.path.basename(p).startswith(".Trash") and os.path.dirname(p) == hit[1].rstrip("/"):
        return "blocked", "This drive's Trash: empty it from Trash", {}
    for lib in steam()["libs"]:
        sa = f"{lib}/steamapps"
        if p == lib or p == sa or (lib == STEAM and p.startswith(STEAM + "/") and not p.startswith(sa + "/")):
            return "blocked", "Steam's own files", {}
        if not p.startswith(sa + "/"):
            continue
        rel = p[len(sa) + 1:].split("/")
        if rel[0] in ("common", "compatdata", "shadercache", "downloading", "temp", "workshop") and len(rel) == 1:
            return "blocked", "One of Steam's folders: what's inside can be removed, not the folder itself", {}
        if rel[0] == "common":
            app = next((a for a, x in steam()["apps"].items() if x["dir"] == f"{sa}/common/{rel[1]}"), None)
            if len(rel) == 2 and app:
                return "blocked", "An installed game: uninstall it in Steam", {"steam": app}
            if len(rel) == 2:
                return "check", "A game folder Steam doesn't list as installed (maybe left over)", {}
            return "blocked", "Part of a game: uninstall the whole game in Steam", {"steam": app} if app else {}
        if rel[0] == "shadercache":
            return "safe", "Steam builds it again when the game next runs (it may stutter a little at first)", {}
        if rel[0] in ("downloading", "temp"):
            return "check", "A Steam download, unfinished or in progress", {}
        if rel[0] == "compatdata":
            appid = rel[1]
            if len(rel) > 2:
                return "careful", "Inside a game's Proton setup, which may hold its saves", {}
            if appid in steam()["shortcuts"]:
                return "careful", "A non-Steam game's Windows setup, and its saves (non-Steam games have no Steam Cloud)", {}
            if appid.isdigit() and int(appid) >= 1 << 31:
                return "careful", "Left from a non-Steam game that's been removed from Steam. It may hold the game's saves", {}
            if appid in steam()["apps"]:
                return "careful", "This game's Windows setup, and its saves if it doesn't use Steam Cloud. Steam makes a new one, without the saves", {}
            return "careful", "Left from a game that isn't installed. It may hold the game's saves if it didn't use Steam Cloud", {}
        if rel[:2] == ["workshop", "content"] and len(rel) >= 3:
            return "check", "Workshop items (mods). Steam downloads them again if you're still subscribed", {}
        return "blocked", "Steam's own files", {}
    game = re.search(r"/games/([^/]+)/.", p, re.I)  # ~/Games/<game>/..., Documents/Games/<game>/...
    if game:
        return "careful", f"Part of {game.group(1)}: removing only some of it breaks the game. Remove the whole game folder instead", {}
    if p.startswith(f"{H}/.cache/deck-tv"):
        return "check", "Deck TV's karaoke backing tracks: each takes a few minutes to make again", {}
    if p.startswith(f"{H}/.cache/"):
        return "safe", "A cache: apps make it again when they need it", {}
    if p.startswith(f"{H}/.var/app/"):
        rel = p[len(f"{H}/.var/app/"):].split("/")
        if len(rel) >= 2 and rel[1] == "cache":
            return "safe", "An app's cache: it makes it again when it needs it", {}
        name = _flatpak_name(rel[0]) or rel[0]
        if len(rel) == 1:
            return "careful", f"{name}'s settings, sign-ins and data. Removing it resets {name}", {}
        return "careful", f"Part of {name}'s settings and data", {}
    if p.startswith(f"{H}/Emulation/saves") or re.search(r"(^|/)(saves?|savedata|save ?games?)(/|$)", p, re.I):
        return "careful", "May hold game saves", {}
    if p.startswith(f"{H}/.config/") or p.startswith(f"{H}/.local/") or os.path.basename(p).startswith("."):
        return "careful", "Settings or data an app keeps", {}
    rel = os.path.relpath(p, H) if p.startswith(H + "/") else ""
    if rel.startswith("."):
        return "careful", "Settings or data an app keeps", {}
    return "check", "Your own files: look before removing", {}


# ---------- reading ----------
def _cats(v):
    return {CATS[i]: b for i, b in enumerate(v) if b}


def overview():
    mounts = _mounts()
    out = []
    for did, (name, kind, root) in mounts.items():
        d = _state(did, root)
        try:
            space = _space(root)
        except OSError:
            continue
        if not d["scanning"] and (d["tree"] is None or time.time() - d["at"] > STALE) and not d["error"]:
            scan(did)
        with _lock:
            tree = d["tree"]
            out.append({"id": did, "name": name, "kind": kind, "root": root, **space,
                        "scanned": {"at": d["at"], "files": d["files"], "dirs": d["dirs"], "took": d["took"],
                                    "bytes": tree.size if tree else 0, "cats": _cats(tree.cats) if tree else {}} if tree else None,
                        "scanning": dict(d["progress"]) if d["scanning"] and d["progress"] else (True if d["scanning"] else None),
                        "error": d["error"]})
    t = trash_list()
    try:
        page = int((Path(__file__).resolve().parent / "web" / "storage.html").stat().st_mtime)
    except OSError:
        page = 0
    return {"drives": out, "cats": CATS, "keep": _keeps(), "trash": {"items": len(t["items"]), "bytes": t["bytes"]},
            "page": page}  # changes when the page is updated, so an open one reloads itself


def folder(path):
    path = os.path.normpath(path or "/home")
    mounts = _mounts()
    hit = _drive_of(path, mounts)
    if not hit:
        return {"error": "That isn't on a drive Deck TV looks after"}
    if not os.path.isdir(path):
        return {"error": "That folder isn't there any more", "gone": True}
    root = hit[1]
    d = _state(*hit)
    node = _node(path)
    keeps = _keeps()
    dir_cat = _cat_at(root, path)
    entries, more, more_bytes, err = [], 0, 0, None
    try:
        with os.scandir(path) as it:
            items = list(it)
    except OSError as e:
        items, err = [], "Deck TV isn't allowed to look in here" if isinstance(e, PermissionError) else str(e)
    rows = []
    for e in items:
        try:
            s = e.stat(follow_symlinks=False)
        except OSError:
            continue
        row = {"name": e.name, "path": e.path, "mtime": s.st_mtime}
        if stat.S_ISDIR(s.st_mode):
            kid = node.kids.get(e.name) if node is not None and node.kids else None
            row.update(dir=True, size=kid.size if kid else None, files=kid.files if kid else None,
                       newest=kid.mtime if kid else s.st_mtime, cats=_cats(kid.cats) if kid else {},
                       other_drive=s.st_dev != os.stat(path).st_dev)
        elif stat.S_ISLNK(s.st_mode):
            try:
                target = os.readlink(e.path)
            except OSError:
                target = ""
            row.update(link=target, size=0)
        elif stat.S_ISREG(s.st_mode):
            cat = _file_cat(dir_cat, e.name)
            row.update(size=s.st_blocks * 512, cat=CATS[cat], ext=_ext(e.name), atime=s.st_atime)
        else:
            continue
        rows.append(row)
    rows.sort(key=lambda r: -(r["size"] or 0))
    if len(rows) > 500:
        more, more_bytes = len(rows) - 500, sum(r["size"] or 0 for r in rows[500:])
        rows = rows[:500]
    for row in rows:
        level, why, extra = guard(row["path"], keeps, mounts)
        row.update(level=level, why=why, **extra)
        lab = label(row["path"]) if row.get("dir") else None
        if lab:
            row["label"] = lab
        entries.append(row)
    crumbs, cur = [], path
    while True:
        crumbs.append({"name": label(cur) or os.path.basename(cur) or cur, "path": cur})
        if cur == root.rstrip("/") or cur == "/":
            break
        cur = os.path.dirname(cur)
    crumbs.reverse()
    crumbs[0]["name"] = mounts[hit[0]][0]
    level, why, extra = guard(path, keeps, mounts)
    with _lock:
        info = {"size": node.size, "files": node.files, "cats": _cats(node.cats), "newest": node.mtime} if node else None
    return {"path": path, "drive": hit[0], "label": label(path), "crumbs": crumbs, "scanned": info,
            "scanAt": d["at"], "level": level, "why": why, **extra, "entries": entries,
            "more": more, "moreBytes": more_bytes, "error": err}


def where(did, cat, limit=36):
    """The folders that make up one kind of space: the biggest places first, opened up until each is
    one thing (a game, an app, a folder whose own files are the space) or the list is full"""
    mounts = _mounts()
    if did not in mounts or cat not in CATS:
        return {"error": "unknown"}
    c = CATS.index(cat)
    d = _state(did, mounts[did][2])
    root = d["root"].rstrip("/") or "/"
    with _lock:
        tree = d["tree"]
        if tree is None:
            return {"rows": [], "total": 0}
        total = tree.cats[c]
        n = 0
        heap = [(-total, n, root, tree, "node")]
        out = []
        while heap and len(out) + len(heap) < limit:
            neg, _, path, node, what = heapq.heappop(heap)
            kids = sorted((k for k in (node.kids or {}).values() if k.cats[c]), key=lambda k: -k.cats[c]) if what == "node" else []
            own = -neg - sum(k.cats[c] for k in kids)
            # one thing (a game, an app), or mostly its own files: shown whole, unless nearly all of it is in one folder inside
            atomic = ((path != root and unit(path)) or own >= -neg * .6 or not kids) and not (kids and kids[0].cats[c] >= -neg * .9)
            if what != "node" or atomic:
                out.append((-neg, path, node, what))
                continue
            for k in kids[:24]:
                n += 1
                heapq.heappush(heap, (-k.cats[c], n, f"{path.rstrip('/')}/{k.name}", k, "node"))
            rest = kids[24:]
            if rest:
                n += 1
                heapq.heappush(heap, (-sum(k.cats[c] for k in rest), n, path, node, f"rest:{len(rest)}"))
            if own > 0:
                n += 1
                heapq.heappush(heap, (-own, n, path, node, "own"))
        out += [(-neg, path, node, what) for neg, _, path, node, what in heap]
        out.sort(key=lambda x: -x[0])
        rows, small = [], 0
        for b, path, node, what in out:
            if b < max(total * .002, 1 << 20):
                small += b
                continue
            rows.append({"path": path, "bytes": b, "size": node.size, "files": node.files, "newest": node.mtime,
                         "what": what, "cats": _cats(node.cats)})
    keeps = _keeps()
    for r in rows:
        level, why, extra = guard(r["path"], keeps, mounts)
        r.update(level=level, why=why, label=label(r["path"]), **extra)
    return {"rows": rows, "total": total, "small": small}


def biggest_folders(did, cat="", older=0, limit=200, smallest=100 << 20):
    """Folders by size, leaving out the ones that can't be removed from here anyway (a drive's top, Documents,
    Steam's own folders...), except installed games, which Steam uninstalls. A game is one entry, not its
    parts, and a folder that's nearly all of the one around it (MGSDelta/MGSDelta) isn't listed twice.
    cat: mostly that kind of space. older: nothing inside changed for that many days."""
    mounts = _mounts()
    if did not in mounts:
        return {"error": "unknown"}
    d = _state(did, mounts[did][2])
    c = CATS.index(cat) if cat in CATS else None
    cutoff = time.time() - older * 86400 if older else None
    found = []
    with _lock:
        tree = d["tree"]
        if tree is None:
            return {"rows": []}
        stack = [(d["root"].rstrip("/") or "/", tree)]
        while stack:  # bigger folders hold the smaller ones: nothing under the smallest size is looked inside
            path, node = stack.pop()
            for k in (node.kids or {}).values():
                if k.size >= smallest:
                    p = f"{path}/{k.name}"
                    found.append((k.size, p, k))
                    stack.append((p, k))
        found.sort(key=lambda x: -x[0])
        picked = [(p, k.size, k.files, k.mtime, list(k.cats)) for size, p, k in found
                  if (c is None or k.cats[c] * 2 >= k.size) and (cutoff is None or k.mtime <= cutoff)]
    keeps, rows, shown = _keeps(), [], {}
    for p, size, files, newest, cats in picked:
        up = shown.get(os.path.dirname(p))
        if up and size >= up * .95:
            continue
        level, why, extra = guard(p, keeps, mounts)
        if level == "blocked" and not (extra.get("steam") and why.startswith("An installed game")) or why.startswith("Part of"):
            continue
        shown[p] = size
        rows.append({"path": p, "name": os.path.basename(p), "label": label(p), "dir": True, "size": size, "files": files,
                     "newest": newest, "cats": _cats(cats), "level": level, "why": why, **extra})
        if len(rows) >= limit:
            break
    return {"rows": rows}


def biggest(did, cat="", ext="", older=0, limit=300):
    mounts = _mounts()
    if did not in mounts:
        return {"error": "unknown"}
    d = _state(did, mounts[did][2])
    c = CATS.index(cat) if cat in CATS else None
    cutoff = time.time() - older * 86400 if older else None
    keeps = _keeps()
    with _lock:
        top = list(d["top"])
    rows = []
    for size, path, mtime, fc in top:
        if c is not None and fc != c or ext and _ext(path) != ext or cutoff and mtime > cutoff:
            continue
        if not os.path.exists(path):
            continue
        level, why, extra = guard(path, keeps, mounts)
        rows.append({"path": path, "name": os.path.basename(path), "size": size, "mtime": mtime, "cat": CATS[fc],
                     "ext": _ext(path), "level": level, "why": why, **extra})
        if len(rows) >= limit:
            break
    return {"rows": rows, "partial": len(top) >= TOP_FILES}


def types(did):
    mounts = _mounts()
    if did not in mounts:
        return {"error": "unknown"}
    d = _state(did, mounts[did][2])
    with _lock:
        rows = sorted(([e, n, b, max(by, key=by.get) if by else OTHER] for e, (n, b, by) in d["exts"].items() if b),
                      key=lambda r: -r[2])
    return {"rows": [{"ext": e, "files": n, "bytes": b, "cat": CATS[c]} for e, n, b, c in rows[:250]],
            "count": len(rows)}


def suggestions():
    """Places that are usually worth a look, with how careful to be with each"""
    out = []
    mounts = _mounts()
    keeps = _keeps()
    now = time.time()

    def rows(paths):
        res = []
        for p in paths:
            n = _node(p)
            if n is None or n.size < 1 << 20:
                continue
            level, why, extra = guard(p, keeps, mounts)
            if level == "blocked":
                continue
            res.append({"path": p, "size": n.size, "files": n.files, "newest": n.mtime, "label": label(p),
                        "level": level, "why": why, **extra})
        return sorted(res, key=lambda r: -r["size"])

    def kids(p):
        try:
            return [os.path.join(p, n) for n in os.listdir(p)]
        except OSError:
            return []

    def add(sid, title, text, level, items, **more):
        if items:
            out.append({"id": sid, "title": title, "text": text, "level": level, "rows": items,
                        "bytes": sum(r["size"] for r in items), **more})

    t = trash_list()
    if t["bytes"] >= 1 << 20:
        out.append({"id": "trash", "title": "Empty the Trash", "level": "check", "bytes": t["bytes"], "rows": [],
                    "text": f"{len(t['items'])} thing{'s' if len(t['items']) != 1 else ''} removed earlier. The space only comes back once the Trash is emptied."})
    caches = [p for p in kids(f"{H}/.cache") if not p.endswith("/deck-tv")]
    caches += [f"{p}/cache" for p in kids(f"{H}/.var/app")]
    add("caches", "App caches", "Apps make these again when they need them. Safe to clear; an app may be a little slower the first time.",
        "safe", rows(caches))
    libs = steam()["libs"]
    apps = steam()["apps"]
    shaders = [p for lib in libs for p in kids(f"{lib}/steamapps/shadercache")]
    add("shaders-gone", "Shader caches for games that aren't installed", "Left behind when a game was uninstalled. Nothing uses them.",
        "safe", rows([p for p in shaders if not installed(os.path.basename(p))]))
    add("shaders", "Shader caches", "Steam builds them again when each game next runs; the game may stutter a little at first.",
        "safe", rows([p for p in shaders if installed(os.path.basename(p))]))
    prefixes = [p for lib in libs for p in kids(f"{lib}/steamapps/compatdata")
                if os.path.basename(p).isdigit() and not installed(os.path.basename(p))]
    add("prefixes-gone", "Proton setups for games that aren't installed",
        "Each holds a game's Windows setup, and its saves if the game didn't use Steam Cloud. Check before removing one you might play again.",
        "careful", rows(prefixes))
    work = [p for lib in libs for p in kids(f"{lib}/steamapps/workshop/content") if not installed(os.path.basename(p))]
    add("workshop-gone", "Workshop items for games that aren't installed", "Mods for games that are gone.", "check", rows(work))
    add("downloads-steam", "Unfinished Steam downloads", "Steam starts these again if the game is still queued.",
        "check", rows([p for lib in libs for s in ("downloading", "temp") for p in kids(f"{lib}/steamapps/{s}")]))
    old = []
    for p in kids(f"{H}/Downloads"):
        try:
            s = os.stat(p, follow_symlinks=False)
        except OSError:
            continue
        if now - s.st_mtime < 30 * 86400:
            continue
        if stat.S_ISREG(s.st_mode) and s.st_blocks * 512 >= 20 << 20:
            level, why, extra = guard(p, keeps, mounts)
            if level != "blocked":
                old.append({"path": p, "size": s.st_blocks * 512, "files": 1, "newest": s.st_mtime, "label": None,
                            "level": level, "why": why, **extra})
        elif stat.S_ISDIR(s.st_mode):
            r = rows([p])
            if r and r[0]["newest"] < now - 30 * 86400 and r[0]["size"] >= 20 << 20:
                old += r
    add("downloads", "Old downloads", "Things in Downloads not touched for a month or more: installers, archives, videos.",
        "check", sorted(old, key=lambda r: -r["size"]))
    idle = []
    for appid, a in apps.items():
        if re.match(r"(Proton|Steam Linux Runtime|Steamworks Common)", a["name"]):
            continue
        if a["lastPlayed"] and now - a["lastPlayed"] < 90 * 86400 or a["size"] < 200 << 20:
            continue
        idle.append({"path": a["dir"], "size": a["size"], "label": a["name"], "lastPlayed": a["lastPlayed"],
                     "level": "blocked", "why": "An installed game: uninstall it in Steam", "steam": appid})
    if idle:
        out.append({"id": "games", "title": "Games not played for 3 months", "level": "steam",
                    "text": "Uninstalling in Steam keeps your saves in the cloud (for most games) and your achievements. Reinstalling downloads it again.",
                    "rows": sorted(idle, key=lambda r: -r["size"]), "bytes": sum(r["size"] for r in idle)})
    return {"groups": out}


# ---------- the Trash ----------
def _mount_top(path):
    """The top of the drive a path is on"""
    path = os.path.realpath(path)
    dev = os.stat(path).st_dev
    while path != "/":
        up = os.path.dirname(path)
        try:
            if os.stat(up).st_dev != dev:
                break
        except OSError:
            break
        path = up
    return path


def _trash_for(path):
    """The Trash a path goes to: the home one if it's on the same drive, or the drive's own"""
    dev = os.stat(path, follow_symlinks=False).st_dev
    try:
        if os.stat(H).st_dev == dev:
            os.makedirs(f"{HOME_TRASH}/files", exist_ok=True)
            os.makedirs(f"{HOME_TRASH}/info", exist_ok=True)
            return HOME_TRASH, None
    except OSError:
        pass
    top = _mount_top(os.path.dirname(path) if not os.path.isdir(path) else path)
    t = f"{top}/.Trash-{UID}"
    os.makedirs(f"{t}/files", mode=0o700, exist_ok=True)
    os.makedirs(f"{t}/info", mode=0o700, exist_ok=True)
    return t, top


def _trash_dirs():
    dirs = [(HOME_TRASH, None)]
    for _, kind, root in _mounts().values():
        if kind == "internal":
            continue
        for t in (f"{root}/.Trash-{UID}", f"{root}/.Trash/{UID}"):
            if os.path.isdir(f"{t}/files"):
                dirs.append((t, root))
    return dirs


def _info(t, name):
    try:
        text = Path(f"{t}/info/{name}.trashinfo").read_text(errors="replace")
    except OSError:
        return None, ""
    orig = re.search(r"^Path=(.*)$", text, re.M)
    when = re.search(r"^DeletionDate=(.*)$", text, re.M)
    return (urllib.parse.unquote(orig.group(1)) if orig else None), (when.group(1) if when else "")


def _du(path):
    total = files = 0
    try:
        s = os.stat(path, follow_symlinks=False)
    except OSError:
        return 0, 0
    if not stat.S_ISDIR(s.st_mode):
        return s.st_blocks * 512, 1
    for dirpath, dirnames, filenames in os.walk(path):
        for n in filenames:
            try:
                total += os.stat(os.path.join(dirpath, n), follow_symlinks=False).st_blocks * 512
                files += 1
            except OSError:
                pass
    return total, files


def trash_list(measure=True):
    items, total = [], 0
    for t, top in _trash_dirs():
        try:
            names = os.listdir(f"{t}/files")
        except OSError:
            continue
        for name in names:
            orig, when = _info(t, name)
            if orig and top and not orig.startswith("/"):
                orig = f"{top}/{orig}"
            key = (t, name, when)
            if key not in _trash_sizes and measure:
                _trash_sizes[key] = _du(f"{t}/files/{name}")
            size, files = _trash_sizes.get(key, (None, None))
            items.append({"id": f"{t}\n{name}", "name": name, "from": orig, "deleted": when, "size": size, "files": files,
                          "dir": os.path.isdir(f"{t}/files/{name}"), "drive": "internal" if top is None else top})
            total += size or 0
    items.sort(key=lambda r: r["deleted"], reverse=True)
    return {"items": items, "bytes": total}


def _trash_item(item_id):
    t, _, name = str(item_id).partition("\n")
    if not name or "/" in name or name in (".", "..") or t not in {x for x, _ in _trash_dirs()}:
        return None
    return t, name


# ---------- changing things ----------
def _log(action, path, size=None, note=""):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{action}\t{'' if size is None else size}\t{path}\t{note}\n")
    except OSError:
        pass


def _tidy(paths):
    """Each path once, and not one that's inside another in the list"""
    ps = sorted({os.path.normpath(p) for p in paths if isinstance(p, str) and p.startswith("/")})
    out = []
    for p in ps:
        if not any(p.startswith(q + "/") for q in out):
            out.append(p)
    return out


def check(paths):
    """What removing these would do, read fresh, for the last look before they go"""
    mounts, keeps = _mounts(), _keeps()
    rows = []
    for p in _tidy(paths):
        try:
            s = os.stat(p, follow_symlinks=False)
        except OSError:
            rows.append({"path": p, "gone": True})
            continue
        level, why, extra = guard(p, keeps, mounts)
        n = _node(p) if stat.S_ISDIR(s.st_mode) else None
        if stat.S_ISDIR(s.st_mode):
            size, files, newest = (n.size, n.files, n.mtime) if n else (*_du(p), s.st_mtime)
        else:
            size, files, newest = s.st_blocks * 512, 1, s.st_mtime
        rows.append({"path": p, "dir": stat.S_ISDIR(s.st_mode), "size": size, "files": files, "newest": newest,
                     "recent": time.time() - newest < RECENT, "label": label(p), "level": level, "why": why, **extra})
    return {"rows": rows}


def remove(paths, careful=False, forever=False):
    """Move to the Trash, or (forever) delete straight away, freeing the space. Blocked places are refused
    here too (not just on the page), and the careful ones only go when the page says they were looked at."""
    done, refused = [], []
    mounts, keeps = _mounts(), _keeps()
    with _ops:
        for p in _tidy(paths):
            level, why, _ = guard(p, keeps, mounts)
            if level == "blocked":
                refused.append({"path": p, "why": why})
                continue
            if level == "careful" and not careful:
                refused.append({"path": p, "why": "Needs a tick to say it was checked"})
                continue
            if not os.path.lexists(p):
                refused.append({"path": p, "why": "It isn't there any more"})
                continue
            hit = _drive_of(p, mounts)
            if forever:
                m = _measure(hit[1], p)
                size = sum(m[1]) if m else 0
                try:
                    _rmtree(p)
                except OSError as e:
                    refused.append({"path": p, "why": e.strerror or str(e)})
                if os.path.lexists(p):
                    if not refused or refused[-1]["path"] != p:
                        refused.append({"path": p, "why": "Some of it couldn't be deleted"})
                    _jobs.put(("scan", hit[0]))  # part of it went: count the drive again
                    continue
                _apply(p, m, -1)
                _log("delete", p, size)
                done.append({"path": p, "size": size})
                continue
            try:
                t, top = _trash_for(p)
            except OSError as e:
                refused.append({"path": p, "why": f"Couldn't use this drive's Trash ({e.strerror or e})"})
                continue
            m = _measure(hit[1], p)
            base = os.path.basename(p)
            name, n = base, 1
            while True:
                try:
                    fd = os.open(f"{t}/info/{name}.trashinfo", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    if os.path.lexists(f"{t}/files/{name}"):
                        os.close(fd)
                        os.unlink(f"{t}/info/{name}.trashinfo")
                        raise FileExistsError
                    break
                except FileExistsError:
                    n += 1
                    name = f"{base}.{n}"
            orig = urllib.parse.quote(p if top is None else os.path.relpath(p, top))
            os.write(fd, f"[Trash Info]\nPath={orig}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n".encode())
            os.close(fd)
            try:
                os.rename(p, f"{t}/files/{name}")
            except OSError as e:
                os.unlink(f"{t}/info/{name}.trashinfo")
                why = "Deck TV isn't allowed to move this (it belongs to the system)" if isinstance(e, PermissionError) else (e.strerror or str(e))
                refused.append({"path": p, "why": why})
                continue
            size = sum(m[1]) if m else 0
            _apply(p, m, -1)
            dest = f"{t}/files/{name}"
            if _drive_of(dest, mounts):
                _apply(dest, _measure(_drive_of(dest, mounts)[1], dest), +1)
            _log("trash", p, size, f"{t}/files/{name}")
            done.append({"path": p, "id": f"{t}\n{name}", "size": size})
    return {"done": done, "refused": refused, "bytes": sum(d["size"] for d in done)}


def restore(ids):
    done, refused = [], []
    mounts = _mounts()
    with _ops:
        for item in ids if isinstance(ids, list) else []:
            hit = _trash_item(item)
            if not hit:
                refused.append({"id": item, "why": "Not in the Trash"})
                continue
            t, name = hit
            orig, _ = _info(t, name)
            top = None if t == HOME_TRASH else os.path.dirname(t) if t.endswith(f"/.Trash-{UID}") else os.path.dirname(os.path.dirname(t))
            if orig and top and not orig.startswith("/"):
                orig = f"{top}/{orig}"
            if not orig:
                refused.append({"id": item, "why": "The Trash doesn't say where it came from"})
                continue
            if os.path.lexists(orig):
                refused.append({"id": item, "why": "Something else is there now", "path": orig})
                continue
            src = f"{t}/files/{name}"
            m = _measure(_drive_of(src, mounts)[1], src) if _drive_of(src, mounts) else None
            try:
                os.makedirs(os.path.dirname(orig), exist_ok=True)
                os.rename(src, orig)
            except OSError as e:
                refused.append({"id": item, "why": e.strerror or str(e), "path": orig})
                continue
            try:
                os.unlink(f"{t}/info/{name}.trashinfo")
            except OSError:
                pass
            if m:
                _apply(src, m, -1)
            if _drive_of(orig, mounts):
                _apply(orig, _measure(_drive_of(orig, mounts)[1], orig), +1)
            _log("restore", orig, sum(m[1]) if m else None)
            done.append({"id": item, "path": orig})
    return {"done": done, "refused": refused}


def _rmtree(path):
    def fix(func, p, *_):  # a read-only folder inside (some games ship them): make it writable and retry
        try:
            os.chmod(os.path.dirname(p), 0o700)
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            pass
    if os.path.isdir(path) and not os.path.islink(path):
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=fix)
        else:
            shutil.rmtree(path, onerror=fix)
    else:
        os.unlink(path)


def empty(ids):
    """Remove things from the Trash for good. Only what's named: the page lists each one first."""
    done, refused, freed = [], [], 0
    mounts = _mounts()
    with _ops:
        for item in ids if isinstance(ids, list) else []:
            hit = _trash_item(item)
            if not hit:
                refused.append({"id": item, "why": "Not in the Trash"})
                continue
            t, name = hit
            src = f"{t}/files/{name}"
            orig, _ = _info(t, name)
            m = _measure(_drive_of(src, mounts)[1], src) if _drive_of(src, mounts) else None
            size = sum(m[1]) if m else _du(src)[0]
            try:
                _rmtree(src)
            except OSError as e:
                refused.append({"id": item, "why": e.strerror or str(e)})
                continue
            if os.path.lexists(src):
                refused.append({"id": item, "why": "Some of it couldn't be removed"})
                continue
            try:
                os.unlink(f"{t}/info/{name}.trashinfo")
            except OSError:
                pass
            if m:
                _apply(src, m, -1)
            _log("empty", orig or src, size)
            freed += size
            done.append({"id": item})
    return {"done": done, "refused": refused, "bytes": freed}


def reveal(path):
    """Show it in Dolphin"""
    if not isinstance(path, str) or not os.path.lexists(path):
        return {"error": "It isn't there"}
    subprocess.Popen(["dolphin", "--select", path] if not os.path.isdir(path) else ["dolphin", path],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"ok": True}


def _game_paths(appid, lib):
    """Everything Steam keeps for a game: its files, Proton setup, shader cache and Workshop items"""
    app = steam()["apps"].get(appid)
    sa = f"{lib}/steamapps"
    return ([app["dir"]] if app else []) + [f"{sa}/compatdata/{appid}", f"{sa}/shadercache/{appid}", f"{sa}/workshop/content/{appid}"]


def uninstall(appid):
    """Steam's own uninstall (it asks first). The page then watches for it to finish (installed()) and
    counts the game's folders again (recount())."""
    appid = str(appid)
    app = steam()["apps"].get(appid)
    if not app:
        return {"error": "That game isn't installed"}
    subprocess.Popen(["steam", f"steam://uninstall/{appid}"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    _log("steam-uninstall", app["dir"], app["size"], appid)
    return {"ok": True, "name": app["name"], "paths": _game_paths(appid, app["lib"])}


def still_installed(appid):
    with _lock:
        _steam["at"] = 0  # read the manifests afresh
    return {"installed": str(appid) in steam()["apps"]}


def recount(paths):
    """Count folders again after something else changed them (Steam uninstalling a game, files copied in):
    each is measured afresh and takes the place of what the last count had. A drive's top folder is a whole
    count, in the background. (The File types totals catch up at the next whole count.)"""
    out = []
    mounts = _mounts()
    for p in [os.path.normpath(x) for x in paths if isinstance(x, str) and x.startswith("/")][:20]:
        hit = _drive_of(p, mounts)
        if not hit:
            continue
        if p == hit[1].rstrip("/"):
            scan(hit[0])
            out.append({"path": p, "whole": True})
            continue
        old = _node(p)
        with _lock:
            before = (None, list(old.cats), old.files, {}, []) if old else None
        now = _measure(hit[1], p) if os.path.isdir(p) else None
        if before:
            _apply(p, before, -1)
        if now:
            _apply(p, (now[0], now[1], now[2], {}, now[4]), +1)
        out.append({"path": p, "gone": not os.path.lexists(p), "before": sum(before[1]) if before else None,
                    "now": sum(now[1]) if now else 0})
    return {"rows": out, "freed": sum(max(0, (r.get("before") or 0) - r.get("now", 0)) for r in out)}


def act(action, body):
    """The page's POSTs"""
    body = body if isinstance(body, dict) else {}
    if action == "scan":
        return scan(body.get("drive"))
    if action == "check":
        return check(body.get("paths") or [])
    if action == "remove":
        return remove(body.get("paths") or [], careful=body.get("careful") is True, forever=body.get("forever") is True)
    if action == "restore":
        return restore(body.get("ids") or [])
    if action == "empty":
        return empty(body.get("ids") or [])
    if action == "keep":
        return keep(str(body.get("path", "")), bool(body.get("on")))
    if action == "reveal":
        return reveal(body.get("path"))
    if action == "uninstall":
        return uninstall(body.get("appid", ""))
    if action == "installed":
        return still_installed(body.get("appid", ""))
    if action == "recount":
        return recount(body.get("paths") or [])
    return {"error": "unknown"}
