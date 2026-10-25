#!/usr/bin/env bash
# Optional fix: if the Deck wakes up again moments after Sleep, a wireless mouse or keyboard
# receiver is usually doing it. This stops USB mice/keyboards waking the Deck from sleep
# (wake it with the power button instead). Run once with:
#   sudo bash fix-mouse-wake.sh
# Undo: sudo rm /etc/udev/rules.d/90-deck-tv-no-mouse-wake.rules   (then replug the receiver)
set -e
[ "$(id -u)" = 0 ] || { echo "Run this with sudo: sudo bash $0"; exit 1; }
RULE=/etc/udev/rules.d/90-deck-tv-no-mouse-wake.rules
: > "$RULE.new"
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] && [ -f "$d/power/wakeup" ] || continue
  # Only USB input devices (mice, keyboards, their receivers), never network adapters or hubs
  grep -qx 03 "$d"/*:*/bInterfaceClass 2>/dev/null || continue
  [ "$(cat "$d/product" 2>/dev/null)" = "Steam Deck Controller" ] && continue
  v=$(cat "$d/idVendor"); p=$(cat "$d/idProduct")
  echo "ACTION==\"add\", SUBSYSTEM==\"usb\", ATTR{idVendor}==\"$v\", ATTR{idProduct}==\"$p\", ATTR{power/wakeup}=\"disabled\"" >> "$RULE.new"
  echo disabled > "$d/power/wakeup"
  echo "$(cat "$d/product" 2>/dev/null || echo "$v:$p"): won't wake the Deck any more"
done
if [ -s "$RULE.new" ]; then
  sort -u "$RULE.new" > "$RULE"; rm -f "$RULE.new"; echo "Saved in $RULE"
else
  rm -f "$RULE.new"; echo "No USB mouse or keyboard found. Plug the receiver in and run this again."
fi
