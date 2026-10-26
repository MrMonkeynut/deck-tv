#!/usr/bin/env bash
# Removes Deck TV: its files, menu entries, start-up entry, Home shortcut and window rules.
# Chrome, Spotify and the apps' browser profiles (logins, uBlock) are left in place.
set -u
DEST="$HOME/.local/share/deck-tv"

pkill -f "$DEST/server.py" 2>/dev/null
[ -f "$HOME/.cache/deck-tv-dock.lock" ] && kill "$(cat "$HOME/.cache/deck-tv-dock.lock")" 2>/dev/null
rm -f "$HOME/.cache/deck-tv-dock.lock"

rm -f "$HOME/.local/bin/deck-tv" "$HOME/.local/bin/deck-tv-switch" "$HOME/.local/bin/deck-tv-alttab" "$HOME/.local/share/applications/deck-tv.desktop" \
      "$HOME/.local/share/applications/deck-tv-"*.desktop "$HOME/.config/autostart/deck-tv.desktop"
rm -rf "$HOME/.config/deck-tv"

kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv.desktop --key _launch --delete
# Alt+Tab back to KDE's own switcher
kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv-switch.desktop --key _launch --delete
kwriteconfig6 --file kglobalshortcutsrc --group services --group deck-tv-switch.desktop --key back --delete
kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Walk Through Windows" "Alt+Tab,Alt+Tab,Walk Through Windows"
kwriteconfig6 --file kglobalshortcutsrc --group kwin --key "Walk Through Windows (Reverse)" "Alt+Shift+Tab,Alt+Shift+Tab,Walk Through Windows (Reverse)"
systemctl --user restart plasma-kglobalaccel.service 2>/dev/null
RULES=$(kreadconfig6 --file kwinrulesrc --group General --key rules)
KEEP=$(echo "$RULES" | tr ',' '\n' | grep -v '^deck-tv-' | paste -sd, -)
for id in $(echo "$RULES" | tr ',' '\n' | grep '^deck-tv-'); do
  for key in Description wmclass wmclassmatch wmclasscomplete fullscreen fullscreenrule noborder noborderrule; do
    kwriteconfig6 --file kwinrulesrc --group "$id" --key "$key" --delete
  done
done
kwriteconfig6 --file kwinrulesrc --group General --key rules "$KEEP"
kwriteconfig6 --file kwinrulesrc --group General --key count "$(echo "$KEEP" | tr ',' '\n' | grep -c .)"
# Screen corners and edges back to KDE's defaults
for k in TopLeft Top TopRight Right BottomRight Bottom BottomLeft Left; do
  kwriteconfig6 --file kwinrc --group ElectricBorders --key "$k" --delete
done
for k in Top Right Bottom Left; do kwriteconfig6 --file kwinrc --group TouchEdges --key "$k" --delete; done
for k in BorderActivate TouchBorderActivate; do kwriteconfig6 --file kwinrc --group Effect-overview --key "$k" --delete; done
for k in BorderActivate BorderActivateAll BorderActivateClass TouchBorderActivate TouchBorderActivateAll TouchBorderActivateClass; do
  kwriteconfig6 --file kwinrc --group Effect-windowview --key "$k" --delete
done
kwriteconfig6 --file kwinrc --group Effect-desktopgrid --key BorderActivate --delete
qdbus org.kde.KWin /KWin reconfigure 2>/dev/null
kbuildsycoca6 >/dev/null 2>&1

rm -rf "$DEST"
echo "Deck TV removed."
