#!/usr/bin/env bash
# Deck TV installer: run it in Desktop Mode, from the unzipped deck-tv folder, in Konsole:
#   bash install.sh
# It asks two questions (your town, for the weather; and whether to open Deck TV at start-up).
# Running it again updates Deck TV and keeps your settings.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/deck-tv"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ASK=1; [ -t 0 ] || ASK=0   # no questions when there's nobody to answer them

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------- checks ----------
if [ -f /run/.containerenv ] || [ -n "${DISTROBOX_ENTER_PATH:-}" ]; then
  echo "Run this in a normal Konsole window, not inside a distrobox container."; exit 1
fi
python3 -c 'import gi, dbus, cairo; gi.require_version("Gtk", "3.0")' 2>/dev/null || {
  echo "Missing Python libraries (gi, dbus, cairo). On a Steam Deck these come with SteamOS;"
  echo "is this Desktop Mode on SteamOS 3?"; exit 1; }
for tool in xdotool xprop wpctl kwriteconfig6 flatpak curl; do
  command -v "$tool" >/dev/null || { echo "Missing $tool (it normally comes with SteamOS)."; exit 1; }
done

# ---------- apps it needs (from Flathub, into your user account: no password) ----------
need_app() {
  flatpak info "$1" >/dev/null 2>&1 && return 0
  say "Installing $2 (this can take a few minutes)…"
  flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
  flatpak install -y --user flathub "$1" || { echo "Couldn't install $2. Install it from Discover, then run this again."; exit 1; }
}
need_app com.google.Chrome "Google Chrome"
need_app com.spotify.Client "Spotify"

# ---------- files ----------
say "Copying Deck TV…"
mkdir -p "$DEST/images" "$DEST/icons" "$BIN" "$APPS"
cp -r "$SRC/web" "$SRC/remote" "$SRC/server.py" "$SRC/search.py" "$SRC/cdp.py" "$SRC/media.py" "$SRC/watchlist.py" "$SRC/dock.py" "$DEST/"
cp "$SRC/icons/"*.png "$SRC/icons/"*.svg "$DEST/icons/"
cp "$SRC/images/"* "$DEST/images/"
cp "$SRC/uninstall.sh" "$DEST/"

FIRST=1
if [ -f "$DEST/tiles.json" ]; then
  FIRST=0
  # Updating: new tiles and commands, but keep the weather town you chose
  cp "$DEST/tiles.json" "$DEST/tiles.json.bak"
  python3 - "$SRC/tiles.json" "$DEST/tiles.json.bak" "$DEST/tiles.json" <<'PY'
import json, sys
new, old = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
if old.get("weather"):
    new["weather"] = old["weather"]
json.dump(new, open(sys.argv[3], "w"), indent=2)
PY
else
  cp "$SRC/tiles.json" "$DEST/"
fi

# ---------- weather town ----------
if [ "$FIRST" = 1 ] && [ "$ASK" = 1 ]; then
  read -r -p "Town for the weather (press Enter for Wexford): " TOWN || TOWN=""
  if [ -n "$TOWN" ]; then
    python3 - "$DEST/tiles.json" "$TOWN" <<'PY' || echo "Couldn't look that town up; keeping Wexford. You can change it later in $HOME/.local/share/deck-tv/tiles.json"
import json, sys, urllib.parse, urllib.request
path, town = sys.argv[1], sys.argv[2]
url = "https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(town)
place = json.load(urllib.request.urlopen(url, timeout=10))["results"][0]
cfg = json.load(open(path))
cfg["weather"] = {"label": place["name"], "latitude": round(place["latitude"], 3), "longitude": round(place["longitude"], 3)}
json.dump(cfg, open(path, "w"), indent=2)
print(f"Weather set to {place['name']}, {place.get('admin1', '')} {place.get('country', '')}".strip())
PY
  fi
fi

# ---------- the Deck TV command and menu entry ----------
cat > "$BIN/deck-tv" <<'EOF'
#!/usr/bin/env bash
DIR="$HOME/.local/share/deck-tv"
PORT="${DECK_TV_PORT:-8765}"
# The dock, restarted fresh each time (so Home also recovers a misbehaving dock)
setsid python3 "$DIR/dock.py" --restart >/dev/null 2>&1 < /dev/null &
# The server first, even with the window already open: an update stops the old server, and the
# open page picks the new one up by itself
if ! (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null; then
  setsid python3 "$DIR/server.py" >/dev/null 2>&1 < /dev/null &
  for _ in $(seq 25); do (exec 3<>/dev/tcp/127.0.0.1/$PORT) 2>/dev/null && break; sleep 0.2; done
fi
# Already open: bring it to the front (this is what the Home shortcut runs)
for w in $(xdotool search --class '^deck-tv$' 2>/dev/null); do
  case "$(xprop -id "$w" WM_NAME 2>/dev/null)" in *'"Deck TV"'*) exec xdotool windowactivate "$w" ;; esac
done
# Own profile + window class, so the taskbar groups it under the Deck TV icon, not Chrome
exec flatpak run com.google.Chrome \
  --user-data-dir="$HOME/.var/app/com.google.Chrome/deck-tv" --class=deck-tv \
  --app="http://127.0.0.1:$PORT" --start-fullscreen \
  --no-first-run --no-default-browser-check --disable-features=Translate
EOF
chmod +x "$BIN/deck-tv"

cat > "$APPS/deck-tv.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Deck TV
Comment=Big-screen launcher
Exec=$BIN/deck-tv
Icon=$DEST/icons/deck-tv.png
StartupWMClass=deck-tv
Categories=AudioVideo;
EOF

# Menu entries for the browser apps (Movy, YouTube, UFC), so each gets its own taskbar icon.
# Skipped where you already have one for that app.
python3 - "$DEST/tiles.json" "$APPS" <<'PY'
import glob, json, os, re, sys
cfg, apps = json.load(open(sys.argv[1])), sys.argv[2]
home = os.path.expanduser("~")
have = set()
for f in glob.glob(os.path.join(apps, "*.desktop")):
    if os.path.basename(f).startswith("deck-tv-"):
        continue
    m = re.search(r"^StartupWMClass=(.+)$", open(f, errors="replace").read(), re.M)
    if m:
        have.add(m.group(1).strip().lower())
def quote(arg):
    arg = arg.replace("=~/", f"={home}/")
    return '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"' if re.search(r'[\s"`$\\]', arg) else arg
# Entries for tiles that have since been removed go too
keep = {f"deck-tv-{t['id']}.desktop" for t in cfg["tiles"]}
for f in glob.glob(os.path.join(apps, "deck-tv-*.desktop")):
    if os.path.basename(f) not in keep:
        os.remove(f)
for t in cfg["tiles"]:
    cmd, cls = t.get("command", []), t.get("window_class", "")
    if cmd[:3] != ["flatpak", "run", "com.google.Chrome"] or not cls or cls.lower() in have:
        continue
    icon = next((os.path.expanduser(p) for p in t.get("app_icon", []) if os.path.isfile(os.path.expanduser(p))), "video-display")
    with open(os.path.join(apps, f"deck-tv-{t['id']}.desktop"), "w") as f:
        f.write(f"[Desktop Entry]\nType=Application\nName={t['name']}\nComment={t.get('sub', '')}\n"
                f"Exec={' '.join(quote(a) for a in cmd)}\nIcon={icon}\nStartupWMClass={cls}\nCategories=AudioVideo;\n")
PY

# Alt+Tab: Deck TV's own switcher, drawn by the dock. The dock takes Alt+Tab from KDE while it
# runs ("deck-tv-alttab claim") and gives it back when it closes ("deck-tv-alttab release"), so
# with Deck TV closed Alt+Tab is KDE's usual switcher, overlay and all.
cat > "$BIN/deck-tv-alttab" <<'EOS'
#!/usr/bin/env bash
set_keys() {  # component, action, key code (none: no key)
  local keys="@a(ai) []"; [ -n "$3" ] && keys="[([$3, 0, 0, 0],)]"
  gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
    --method org.kde.KGlobalAccel.setForeignShortcutKeys "['$1', '$2', '', '']" "$keys" >/dev/null
}
ALT_TAB=150994945; ALT_SHIFT_TAB=184549377  # Qt's codes for Alt+Tab and Alt+Shift+Tab
if [ "${1:-}" = claim ]; then  # take keys away before giving them out: two owners would clash
  set_keys kwin "Walk Through Windows" ""
  set_keys kwin "Walk Through Windows (Reverse)" ""
  set_keys deck-tv-switch.desktop _launch $ALT_TAB
  set_keys deck-tv-switch.desktop back $ALT_SHIFT_TAB
else
  set_keys deck-tv-switch.desktop _launch ""
  set_keys deck-tv-switch.desktop back ""
  set_keys kwin "Walk Through Windows" $ALT_TAB
  set_keys kwin "Walk Through Windows (Reverse)" $ALT_SHIFT_TAB
fi
EOS
chmod +x "$BIN/deck-tv-alttab"
# Bound to Alt+Tab while the dock runs. If the dock died without giving Alt+Tab back, this does
# it, then opens KDE's switcher (Alt is still held, so it shows as usual).
cat > "$BIN/deck-tv-switch" <<'EOS'
#!/usr/bin/env bash
SIG=USR1; NAME="Walk Through Windows"
[ "${1:-}" = back ] && SIG=USR2 && NAME="Walk Through Windows (Reverse)"
PID=$(cat "$HOME/.cache/deck-tv-dock.lock" 2>/dev/null)
if [ -n "$PID" ] && grep -q dock.py "/proc/$PID/cmdline" 2>/dev/null; then
  exec kill -"$SIG" "$PID"
fi
"$HOME/.local/bin/deck-tv-alttab" release
exec qdbus org.kde.kglobalaccel /component/kwin invokeShortcut "$NAME"
EOS
chmod +x "$BIN/deck-tv-switch"
cat > "$APPS/deck-tv-switch.desktop" <<EOS
[Desktop Entry]
Type=Application
Name=Deck TV switcher
Exec=$BIN/deck-tv-switch
NoDisplay=true
StartupNotify=false
Actions=back;

[Desktop Action back]
Name=Previous app
Exec=$BIN/deck-tv-switch back
EOS

# ---------- desktop settings ----------
say "Setting up the Home shortcut and full-screen windows…"
# Meta+H (the Super/Windows key + H on a keyboard) opens Deck TV or brings it back to the front
kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv.desktop --key _launch "Meta+H"
# Alt+Tab starts with KDE; the dock takes it while it runs (see deck-tv-alttab above)
kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Walk Through Windows" "Alt+Tab,Alt+Tab,Walk Through Windows"
kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Walk Through Windows (Reverse)" "Alt+Shift+Tab,Alt+Shift+Tab,Walk Through Windows (Reverse)"
kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv-switch.desktop --key _launch "none"
kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv-switch.desktop --key back "none"
# Shortcuts are read when the shortcut service starts: restart it so they apply now
systemctl --user restart plasma-kglobalaccel.service 2>/dev/null || true

# Window rules: Deck TV and the apps it opens start full screen with no title bar
RULES=$(kreadconfig6 --file kwinrulesrc --group General --key rules 2>/dev/null || true)
# (every tile marked "fullscreen": true in tiles.json, so a new tile needs no change here)
FULL=$(python3 -c 'import json, sys; print(" ".join(t["window_class"] for t in json.load(open(sys.argv[1]))["tiles"] if t.get("fullscreen") is True and t.get("window_class")))' "$DEST/tiles.json")
# Web apps (Chrome --app windows, role "pop-up"): only the app's own window. A link that opens an
# ordinary Chrome window, with tabs and an address bar (role "browser"), stays an ordinary window.
WEBAPPS=$(python3 -c 'import json, sys; print(" ".join(t["window_class"] for t in json.load(open(sys.argv[1]))["tiles"] if any(a.startswith("--app=") for a in t.get("command", [])) and t.get("window_class")))' "$DEST/tiles.json")
for app in deck-tv $FULL; do
  id="deck-tv-$app"
  W() { kwriteconfig6 --file kwinrulesrc --group "$id" --key "$1" "$2"; }
  W Description "Deck TV: $app full screen"
  W wmclass "$app"; W wmclassmatch 1; W wmclasscomplete false
  W fullscreen true; W fullscreenrule 3
  W noborder true; W noborderrule 3
  case " deck-tv $WEBAPPS " in
    *" $app "*) W windowrole "pop-up"; W windowrolematch 1 ;;
    *) kwriteconfig6 --file kwinrulesrc --group "$id" --key windowrole --delete
       kwriteconfig6 --file kwinrulesrc --group "$id" --key windowrolematch --delete ;;
  esac
  case ",$RULES," in *",$id,"*) ;; *) RULES="${RULES:+$RULES,}$id" ;; esac
done
# Rules for apps no longer in Deck TV are removed
for id in $(echo "$RULES" | tr ',' '\n' | grep '^deck-tv-'); do
  case " deck-tv $FULL " in *" ${id#deck-tv-} "*) continue ;; esac
  for key in Description wmclass wmclassmatch wmclasscomplete windowrole windowrolematch fullscreen fullscreenrule noborder noborderrule; do
    kwriteconfig6 --file kwinrulesrc --group "$id" --key "$key" --delete
  done
  RULES=$(echo "$RULES" | tr ',' '\n' | grep -vx "$id" | paste -sd, -)
done
kwriteconfig6 --file kwinrulesrc --group General --key rules "$RULES"
kwriteconfig6 --file kwinrulesrc --group General --key count "$(echo "$RULES" | tr ',' '\n' | grep -c .)"
# No screen-corner or edge actions: KDE opens Overview when the mouse hits the top-left corner,
# which breaks the full-screen TV feel (uninstall.sh puts the defaults back)
for k in TopLeft Top TopRight Right BottomRight Bottom BottomLeft Left; do
  kwriteconfig6 --file kwinrc --group ElectricBorders --key "$k" None
done
for k in Top Right Bottom Left; do kwriteconfig6 --file kwinrc --group TouchEdges --key "$k" None; done
for k in BorderActivate TouchBorderActivate; do kwriteconfig6 --file kwinrc --group Effect-overview --key "$k" 9; done
for k in BorderActivate BorderActivateAll BorderActivateClass TouchBorderActivate TouchBorderActivateAll TouchBorderActivateClass; do
  kwriteconfig6 --file kwinrc --group Effect-windowview --key "$k" 9
done
kwriteconfig6 --file kwinrc --group Effect-desktopgrid --key BorderActivate 9
qdbus org.kde.KWin /KWin reconfigure 2>/dev/null || true
kbuildsycoca6 >/dev/null 2>&1 || true

# ---------- open at start-up? ----------
AUTO=0
[ -f "$HOME/.config/autostart/deck-tv.desktop" ] && AUTO=1
if [ "$FIRST" = 1 ] && [ "$ASK" = 1 ]; then
  read -r -p "Open Deck TV automatically whenever Desktop Mode starts? [Y/n] " A || A=""
  case "$A" in [nN]*) AUTO=0 ;; *) AUTO=1 ;; esac
fi
if [ "$AUTO" = 1 ]; then
  mkdir -p "$HOME/.config/autostart"
  cp "$APPS/deck-tv.desktop" "$HOME/.config/autostart/"
fi

# ---------- (re)start ----------
# An update replaces the running server and dock with the new versions
pkill -f "$DEST/server.py" 2>/dev/null || true
sleep 0.5
if [ -n "${DECK_TV_NO_OPEN:-}" ]; then
  say "Done!"
else
  say "Done! Opening Deck TV…"
  setsid "$BIN/deck-tv" >/dev/null 2>&1 < /dev/null &
fi
echo "Next time, open it from the app menu (Deck TV), or press Meta+H."
echo "To remove it: bash $DEST/uninstall.sh"
