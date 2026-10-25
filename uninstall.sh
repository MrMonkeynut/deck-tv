#!/usr/bin/env bash
# Removes Deck TV: its files, menu entries, start-up entry, Home shortcut and window rules.
# Chrome, Spotify and the apps' browser profiles (logins, uBlock) are left in place.
set -u
DEST="$HOME/.local/share/deck-tv"

pkill -f "$DEST/server.py" 2>/dev/null
[ -f "$HOME/.cache/deck-tv-dock.lock" ] && kill "$(cat "$HOME/.cache/deck-tv-dock.lock")" 2>/dev/null
rm -f "$HOME/.cache/deck-tv-dock.lock"

rm -f "$HOME/.local/bin/deck-tv" "$HOME/.local/share/applications/deck-tv.desktop" \
      "$HOME/.local/share/applications/deck-tv-"*.desktop "$HOME/.config/autostart/deck-tv.desktop"
rm -rf "$HOME/.config/deck-tv"

kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv.desktop --key _launch --delete
RULES=$(kreadconfig6 --file kwinrulesrc --group General --key rules)
KEEP=$(echo "$RULES" | tr ',' '\n' | grep -v '^deck-tv-' | paste -sd, -)
for id in $(echo "$RULES" | tr ',' '\n' | grep '^deck-tv-'); do
  for key in Description wmclass wmclassmatch wmclasscomplete fullscreen fullscreenrule noborder noborderrule; do
    kwriteconfig6 --file kwinrulesrc --group "$id" --key "$key" --delete
  done
done
kwriteconfig6 --file kwinrulesrc --group General --key rules "$KEEP"
kwriteconfig6 --file kwinrulesrc --group General --key count "$(echo "$KEEP" | tr ',' '\n' | grep -c .)"
qdbus org.kde.KWin /KWin reconfigure 2>/dev/null
kbuildsycoca6 >/dev/null 2>&1

rm -rf "$DEST"
echo "Deck TV removed."
