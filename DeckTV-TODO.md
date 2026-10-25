# DeckTV — Feature Todo List

_Started 2026-10-25_

> DeckTV runs in Desktop Mode only; Game Mode support is not a goal.

## Up next

- [ ] **Bug: Sleep button doesn't work**: Power → Sleep doesn't put the Deck to sleep (reported 2026-10-25)
- [ ] **More cards**: add more card types to the home screen
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
