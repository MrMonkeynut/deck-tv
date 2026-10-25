# DeckTV — Feature Todo List

_Started 2026-10-25_

> DeckTV runs in Desktop Mode only; Game Mode support is not a goal.

## Up next

- [ ] **Bug: Sleep button doesn't work**: Power → Sleep doesn't put the Deck to sleep (reported 2026-10-25)
- [ ] **More cards**: add more card types to the home screen
- [ ] **Card settings panel**: a settings panel with a toggle per card to enable/disable it
- [ ] **Moveable cards**: let the user change the order of the cards
- [ ] **Profiles**: support multiple user profiles

## Later / ideas

- [ ] **Picture-in-Picture (maybe)**: keep a video playing in a small floating window
  - Check first: CPU/GPU overhead (use hardware decoding, test two streams at once)
  - Floating always-on-top window via KWin (Desktop Mode)
- [ ] **Mobile app refinement**: polish and improve the mobile app
- [ ] **Gamepad navigation**: navigate DeckTV fully with the D-pad/sticks and buttons
- [ ] **Real-time interface scaling**: adjust UI scale on the fly without restarting
- [ ] **Screensaver**: show a screensaver / ambient mode when idle
- [ ] **Wi-Fi status & controls**: show connection status and manage Wi-Fi from DeckTV

## Done

- [x] **UFC card: choice menu**: clicking UFC asks which to open
  - **Results** → Google search for "UFC" (`https://www.google.com/search?q=UFC`), which shows latest results/standings
  - **Fights** → the current watchwrestling URL (keep as-is) _(done 2026-10-25)_
- [x] **Player controls in the dock bar**: play/pause, next and previous in the dock, beside the volume control (as well as on the main page) _(done 2026-10-25)_
