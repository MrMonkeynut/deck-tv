# DeckTV — Feature Todo List

_Started 2026-10-25_

> DeckTV runs in Desktop Mode only; Game Mode support is not a goal.

## Up next

- [ ] **Bug: Sleep button doesn't work**: Power → Sleep doesn't put the Deck to sleep (reported 2026-10-25)
- [ ] **More cards**: add more card types to the home screen
  - Added 2026-10-25: Netflix, Disney+, Prime Video, Apple TV+, NOW, Paramount+, DAZN, Crunchyroll, Twitch (simple lettermark icons for now)
- [ ] **Profiles**: support multiple user profiles

## Later / ideas

- [ ] **Picture-in-Picture (maybe)**: keep a video playing in a small floating window
  - Check first: CPU/GPU overhead (use hardware decoding, test two streams at once)
  - Floating always-on-top window via KWin (Desktop Mode)
- [ ] **Mobile app refinement**: polish and improve the mobile app
- [ ] **Gamepad navigation**: navigate DeckTV fully with the D-pad/sticks and buttons
- [ ] **Real-time interface scaling**: adjust UI scale on the fly without restarting
- [ ] **Wi-Fi status & controls**: show connection status and manage Wi-Fi from DeckTV

## Done

- [x] **UFC card: choice menu**: clicking UFC asks which to open
  - **Results** → Google search for "UFC" (`https://www.google.com/search?q=UFC`), which shows latest results/standings
  - **Fights** → the current watchwrestling URL (keep as-is) _(done 2026-10-25)_
- [x] **Player controls in the dock bar**: play/pause, next and previous in the dock, via a button left of the volume that pops up the controls _(done 2026-10-25)_
- [x] **Card settings panel**: gear button on the home screen opens Cards: switch cards on/off (saved in `~/.config/deck-tv/layout.json`; the phone remote follows it) _(done 2026-10-25)_
- [x] **Moveable cards**: Settings → Rearrange cards: drag the cards into place on the home screen (or A to pick up, ◀ ▶ to move with a controller), then Confirm or Cancel _(done 2026-10-25)_
- [x] **Screensaver**: after a set time with no input on the home screen, a dark screen with the time, date, weather and what's playing, drifting every 20 s to avoid burn-in; any input wakes it without doing anything else. Settings → Screensaver: Off / 2 / 5 (default) / 10 / 15 / 30 min. Never shows over another app _(done 2026-10-25)_
- [x] **Groups (folders)**: Settings → New group: name it, pick one of 8 looks, choose its apps. The group is a card showing its apps' icons like a phone folder, and opens a picker like Live TV. Edit or delete from Settings; deleting puts the apps back where they were _(done 2026-10-25)_
- [x] **Live TV and UFC as groups**: both are now ordinary groups (editable in Settings). UFC is split into two apps, UFC Fights and UFC Results; RTÉ, Virgin Media and Plex are ordinary apps inside Live TV. Existing layouts get the two groups once, in the old cards' places _(done 2026-10-25)_
- [x] **Groups UX pass**: Settings lists each group with an arrow that opens it to show its apps, each with its own on/off switch (hidden in the group) and Take out (comes out next to the group; taking the last one out removes the group). Editor lists the group's apps first, counts them, and won't save an empty group. On the home screen a group with one app showing opens it straight away; the picker marks open apps and starts on the one used last _(done 2026-10-25)_
- [x] **Group card icon**: in the group editor, Card icon: a folder of its apps (with a choice of look), or one of its apps, so the card looks exactly like that app's own (art, colours, icon) apart from the app count _(done 2026-10-25)_
