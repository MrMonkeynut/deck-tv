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
- [ ] **Wi-Fi controls**: pick or join a network from DeckTV (status is shown in System already)

## Done

- [x] **Phone remote: tidier**: the touchpad gets most of the screen; one bar at the bottom (Apps, Play/Pause, Sound, Type) replaces the app row, the media and volume buttons and the always-there type box. Apps and Sound open as sheets; Type brings up the keyboard and tucks away again; the header names the app in front _(done 2026-10-27)_
- [x] **Phone remote: see the TV**: a Screen button shows the TV live (10 fps, 960 px, the pointer drawn in); tap the picture to click there, hold to right-click, two fingers to scroll; sideways it fills the phone. Only captures while a phone is watching (about a quarter of a core then) _(done 2026-10-27)_
- [x] **Features panel**: a Features button in the top-right corner (up from the cards with the controller) opens a one-screen summary of what Deck TV does, in eight groups. Also: an update no longer reloads the page while the watchlist (or a trailer) is open _(done 2026-10-27)_
- [x] **Trailers: captions**: a CC button beside the volume turns captions on or off (English where there is some), remembered for the next trailer. Also fixed: with the controller, focus couldn't get past the volume slider; it's now A to adjust it, A or B to finish _(done 2026-10-27)_
- [x] **Watchlist: Move**: a Move button on an opened film drops down the other lists; the film goes to the end of the one picked, with Undo putting it back where it was _(done 2026-10-27)_
- [x] **Watchlist: films and shows**: a Film / Show switch when adding or editing. Trailers, posters and summaries look up the right one (Fargo the series vs the film); a show gets its seasons, episodes, episode length, years running and status from TMDB, kept with it, and its row shows its seasons. Sorting by running time puts shows after films. A show with no trailer on TMDB plays its opening credits or a clip _(done 2026-10-27)_
- [x] **Watchlist: Remove**: a small Remove button beside Mark watched takes a film off its list straight away, with Undo on the toast putting it back in the same place (even after closing and reopening the watchlist) _(done 2026-10-27)_
- [x] **Watchlist: safer Mark watched, with Undo**: Mark watched is a small button on its own at the far right of an opened film; marking (or unmarking) shows a toast with Undo for 6 s (click it, Y on the controller, U or Ctrl+Z). Fixed the hover outline landing on the button to the left once a poster had loaded _(done 2026-10-27)_
- [x] **Trailers: scrubbing, sound, a button per trailer**: a scrub bar with the time (click or drag; left/right on the controller skip 10 s), mute and a volume slider (remembered), and Trailer 1…n buttons in place of Next trailer _(done 2026-10-27)_
- [x] **Watchlist: trailers**: an opened film has a Trailer button that plays its trailer in the panel (TMDB's list, in YouTube's player under a see-through layer so nothing can take you off to YouTube), with Play/Pause, Next trailer, Fullscreen (buttons fade after 3 s) and Close; a trailer the studio won't allow outside YouTube gives way to the next. Needs a free TMDB key, asked for the first time; no screensaver while one plays _(done 2026-10-27)_
- [x] **Watchlist: slim rows that open up**: each film is a single row with its name and running time (a tick once watched); opening it shows the poster and a summary from Wikipedia, the year, director and dates, and Watch on Movy / Mark watched / Edit. Watch on Movy no longer types into Movy's search box while it's still closing (it waited for the box to really be open) _(done 2026-10-27)_
- [x] **Watchlist: Watch now**: each film has a Watch button that opens Movy (or switches to it) and searches it for the film; a title Movy doesn't know ("Star Wars: Episodes IV, V, VI") is tried again as the part before the colon _(done 2026-10-27)_
- [x] **Watchlist: running times and Sort**: each film shows its running time (the Top 100 come with theirs; type minutes or "2h 15m" for others), and a Sort menu orders a list by list order, title, newest/oldest or shortest/longest _(done 2026-10-27)_
- [x] **Alt+Tab switcher: click and faster fade**: clicking an app in the switcher switches to it (moving the pointer moves the highlight), and it fades in and out twice as fast as KDE's popup fade (75 ms in, 300 ms out), drawing its own fade _(done 2026-10-27)_
- [x] **Watchlist**: a Watchlist button at the end of the bottom row opens lists of films: Liam and Laura, Top 100 (from the scratch poster, the scratched-off ones already watched) and Kids. Add, edit, move between lists, delete, and tick off as watched; To watch / Watched / All; make, rename or delete lists. Saved in `~/.config/deck-tv/watchlist.json` _(done 2026-10-27)_
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
- [x] **Groups on the phone remote**: the remote's Apps sheet follows the TV's home screen: each group is a folder (a little grid of its apps' icons, like a phone) in its place, and tapping it opens the group's apps in a popup. A group with one app showing is just that app; a folder is marked when one of its apps is open or in front _(done 2026-10-27)_
- [x] **System panel**: a status button beside Power, Desktop and Settings shows Wi-Fi, battery and free space; it opens System: storage for the Deck and any microSD/USB drive (amber when getting full, red when almost out), Wi-Fi network/signal/address, sound (opens Sound, which moved in here from its own button), battery, and the Deck's temperature, uptime and memory _(done 2026-10-26)_
