# Deck TV

A TV-style home screen for the Steam Deck's Desktop Mode: big tiles for Movy, Steam Big Picture,
Spotify, YouTube, UFC, Live TV (RTÉ Player, Virgin Media and Plex) and WhatsApp, a pop-up dock at the bottom of the screen for switching apps, a
"now playing" card, and a phone remote.

## Install (about 5 minutes)

You need: a Steam Deck in **Desktop Mode** and an internet connection. A mouse and keyboard
(or the Deck's trackpads) make setup easier.

1. Switch to Desktop Mode: hold the power button → **Switch to Desktop**.
2. Copy `deck-tv.zip` to the Deck, into **Downloads**.
3. Open **Dolphin** (the file manager), go to Downloads, right-click `deck-tv.zip` →
   **Extract → Extract archive here**. You get a `deck-tv` folder.
4. Open the `deck-tv` folder, right-click an empty space → **Open Terminal Here**.
5. Type this and press Enter:

   ```
   bash install.sh
   ```

6. Answer the two questions:
   - **Town for the weather**: type your town (or press Enter for Wexford).
   - **Open Deck TV automatically whenever Desktop Mode starts?**: press Enter for yes.

The installer adds Google Chrome and Spotify if they aren't there yet (no password needed, but it
can take a few minutes), then opens Deck TV full screen.

## Using it

- **Tiles**: click one to open that app full screen. Click again later to switch back to it.
  **Live TV** (RTÉ, Virgin Media, Plex) and **UFC** (Fights, Results) are groups: they ask which
  app you want first. Make your own groups, or change these, in Settings (the gear button).
- **The dock**: move the mouse to the very bottom edge of the screen and a glowing line appears.
  Click it to open the dock: Home (Deck TV), the apps you have open, and the speaker on the left.
  - Click the speaker for what's playing (previous, play/pause, next), the volume, and
    **Go to the desktop**. Scroll over it to change the volume.
  - Right-click an app in the dock to close it.
  - Slide the dock left or right if it's in the way. It hides 4 seconds after the pointer leaves
    it, or click elsewhere.
- **Home**: the TV icon in the dock, or **Super (Windows key) + H** on a keyboard.
- **Alt+Tab** flips between Deck TV and your open apps (tap it to jump back to the last one; hold
  Alt and press Tab to pick). If Deck TV isn't running, Alt+Tab works as normal.
- **Leaving Deck TV**: the **Desktop** button (bottom-left, beside Power) tucks
  everything away to show the normal desktop. **Alt+F4** on the home screen closes Deck TV
  completely (dock and all), in case anything ever gets stuck. Open it again from the app menu.
- **Now playing**: whatever's playing shows at the top of the home screen with play/pause and
  skip. Starting something in another app pauses the first. Right-click a tile →
  **Show in now playing** to control that app instead.
- **Weather**: click it for the 5-day forecast.
- **Power**: the button in the bottom-left corner: Sleep, Restart or Power off.
- **System**: the wide button at the end of the row shows Wi-Fi, battery and free space. Click it
  for the details: space on the Deck (and any microSD card or USB drive), the Wi-Fi network and
  signal, sound, battery, and the Deck's temperature and memory. Space turns amber when it's
  getting full and red when it's almost out.
- **Sound** (System → Sound): the volume, and where sound plays: the TV, the Deck's
  speakers, headphones or a Bluetooth speaker. A paired speaker that isn't connected shows
  "Connect": switch it on and click it. **Add speaker** pairs a new one: put it in pairing mode,
  pick it from the list, and sound moves to it.
- **Watchlist**: the bookmark button at the end of the bottom row. Lists of films to watch:
  **Liam and Laura**, **Top 100** (the scratch poster, with the ones already scratched off marked
  watched) and **Kids**. Each film is one slim row (its name and running time): click it (or press A) to open it up for its
  poster, what it's about (from Wikipedia) and **Watch on Movy** (opens Movy and searches it for
  the film), **Mark watched** and **Edit**. **To watch / Watched / All**
  picks what shows, and **Sort** orders them (list order, title, year or running time). **Add**
  puts a film on the list you're looking at (a keyboard, or the phone
  remote's, is easiest for typing), **Edit** changes it, moves it to another list or deletes it, and
  **New list / Edit list** make, rename or delete lists. Kept in `~/.config/deck-tv/watchlist.json`.
- **Phone remote**: scan the QR code in the bottom-right corner with your phone (on the same
  Wi-Fi). You get a touchpad, a keyboard for search boxes, app switching and media controls.
  Tip: "Add to Home Screen" in your phone's browser makes it an app.

## Optional extras

- **Pin it to the taskbar**: open the app menu (bottom-left), find **Deck TV**, right-click it →
  **Pin to Task Manager**. Do the same for any of the other apps if you like.
- **Ad blocking for Movy and UFC**: each runs in its own Chrome window with its own settings. Open
  the app, press **Ctrl+T** if needed, go to the Chrome Web Store, search **uBlock Origin Lite**
  and add it. Do this once in each of them.
- **Sign in to YouTube**: open YouTube from Deck TV and sign in as you would on a TV.
- **Link WhatsApp**: open the WhatsApp tile, then on your phone go to WhatsApp → Settings →
  Linked devices → Link a device, and scan the code on the TV. After that, unread messages show
  as a badge on the tile. (Messaging only: WhatsApp doesn't do calls in a browser.)
- **RTÉ Player and Virgin Media** need a free account for live TV: sign in once in each and it's remembered.
  Plex works without one.

## If something's not right

- **A new speaker doesn't show up under Add speaker**: put it back in pairing mode (its light
  usually flashes quickly) and press Search again. Some speakers only stay discoverable for a minute.

- **Super + H doesn't work yet**: log out and back in once (or restart the Deck).
- **The Deck wakes up again straight after Sleep**: a wireless mouse/keyboard receiver is usually
  waking it. In the `deck-tv` folder, open a terminal and run `sudo bash fix-mouse-wake.sh`
  (it needs the Deck's sudo password; if you've never set one, run `passwd` first). After that,
  wake the Deck with its power button.
- **The phone remote won't connect**: make sure the phone is on the same Wi-Fi as the Deck, and
  scan the QR code again.
- **A tile's missing**: tiles only show for apps that are installed.

## Change the weather town later

Open `~/.local/share/deck-tv/tiles.json` in a text editor (Kate) and change the `weather` part
(name, latitude, longitude). Then close and reopen Deck TV.

## Update or remove

- **Update**: unzip the new version and run `bash install.sh` again. Your settings are kept.
- **Remove**: open a terminal and run `bash ~/.local/share/deck-tv/uninstall.sh`. Chrome, Spotify
  and the apps' logins are left in place.
