#!/usr/bin/env python3
"""Deck TV - your profile: everything in ~/.config/deck-tv (layout, groups, watchlist, keys) plus the
weather town, kept in a git repo of your own (make it private: it holds your keys) so it's backed up
and can be put back on a new Deck. Does nothing until you link one:

  python3 profile_sync.py link git@github.com:YOU/deck-tv-profile.git [~/.ssh/key]   start backing up
  python3 profile_sync.py save                                                       back up now
  python3 profile_sync.py restore git@github.com:YOU/deck-tv-profile.git [~/.ssh/key] on a new Deck, after install.sh

Once linked, the running server backs up a few minutes after anything changes. The key is an SSH
key allowed to push to that one repo (a GitHub deploy key), since the server can't use your login.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

DIR = Path.home() / ".config/deck-tv"
TILES = Path.home() / ".local/share/deck-tv/tiles.json"
WEATHER = DIR / "weather.json"  # the town, copied out of tiles.json (which install.sh replaces)
IGNORE = "desktop-mode\n*.tmp\n"  # which mode the Deck is in right now isn't a setting
EVERY = 300


def git(*args, cwd=DIR, check=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True, timeout=120)


def ssh_command(key):
    return f"ssh -i {Path(key).expanduser()} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"


def linked():
    return (DIR / ".git").is_dir()


def copy_weather():
    try:
        weather = json.loads(TILES.read_text()).get("weather")
    except (OSError, ValueError):
        return
    if weather and (not WEATHER.exists() or json.loads(WEATHER.read_text()) != weather):
        WEATHER.write_text(json.dumps(weather, indent=2) + "\n")


def save():
    """Commit and push whatever changed. True if something was sent."""
    if not linked():
        return False
    copy_weather()
    git("add", "-A")
    changed = git("diff", "--cached", "--quiet", check=False).returncode != 0
    if changed:
        git("commit", "-q", "-m", time.strftime("Profile %Y-%m-%d %H:%M"))
    # Nothing new, and nothing left over from a push that failed (offline): leave the network alone
    branch = git("status", "-sb").stdout.split("\n")[0]
    if not changed and "..." in branch and "ahead" not in branch:
        return False
    git("push", "-q", "-u", "origin", "main")
    return True


def setup(key, cwd=DIR):
    """This repo's own name and key, so it works whoever's logged in to git (or nobody)."""
    git("config", "user.name", "Deck TV", cwd=cwd)
    git("config", "user.email", "deck-tv@localhost", cwd=cwd)
    if key:
        git("config", "core.sshCommand", ssh_command(key), cwd=cwd)


def link(url, key=None):
    DIR.mkdir(parents=True, exist_ok=True)
    if not linked():
        git("init", "-q", "-b", "main")
    (DIR / ".gitignore").write_text(IGNORE)
    setup(key)
    if git("remote", check=False).stdout.split():
        git("remote", "set-url", "origin", url)
    else:
        git("remote", "add", "origin", url)
    save()
    print(f"Backing up {DIR} to {url}")


def restore(url, key=None):
    """Put a backed-up profile in place of this Deck's settings (the old ones are kept beside it)."""
    if linked():
        sys.exit(f"{DIR} is already linked to a profile; it's backed up there.")
    tmp = DIR.with_name("deck-tv.restoring")
    shutil.rmtree(tmp, ignore_errors=True)
    env = ["-c", f"core.sshCommand={ssh_command(key)}"] if key else []
    git(*env, "clone", "-q", url, str(tmp), cwd=Path.home())
    setup(key, cwd=tmp)
    if DIR.exists():
        DIR.rename(DIR.with_name(time.strftime("deck-tv.before-restore-%Y%m%d-%H%M%S")))
    tmp.rename(DIR)
    if WEATHER.exists() and TILES.exists():
        cfg = json.loads(TILES.read_text())
        cfg["weather"] = json.loads(WEATHER.read_text())
        TILES.write_text(json.dumps(cfg, indent=2))
    print(f"Restored {DIR} from {url}. Restart Deck TV (bash install.sh) to use it.")


def run():
    """Server thread: back up every few minutes, when something has changed."""
    while True:
        time.sleep(EVERY)
        try:
            save()
        except (OSError, subprocess.SubprocessError):
            pass  # offline, or the repo's gone: try again next time


if __name__ == "__main__":
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd in ("link", "restore") and args:
        (link if cmd == "link" else restore)(*args[:2])
    elif cmd == "save":
        print("Backed up." if save() else "Nothing new to back up." if linked() else "No profile linked.")
    else:
        sys.exit(__doc__)
