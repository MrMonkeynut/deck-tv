# Deck TV

A TV-style home screen for the Steam Deck's Desktop Mode: big tiles for Movy, Steam Big Picture,
Spotify, YouTube, UFC, Live TV (RTÉ Player, Virgin Media and Plex), WhatsApp and Retro Games (ES-DE, when it's
installed), a pop-up dock at the bottom of the screen for switching apps, a
"now playing" card, and a phone remote.

## Install (about 5 minutes)

You need: a Steam Deck in **Desktop Mode** and an internet connection. A mouse and keyboard
(or the Deck's trackpads) make setup easier.

1. Switch to Desktop Mode: hold the power button → **Switch to Desktop**.
2. Get `deck-tv.zip` into **Downloads** on the Deck: copy it across, or on the GitHub page
   click **Code → Download ZIP** (that one's called `deck-tv-main.zip`; it works the same).
3. Open **Dolphin** (the file manager), go to Downloads, right-click the zip →
   **Extract → Extract archive here**. You get a `deck-tv` (or `deck-tv-main`) folder.
4. Open that folder, right-click an empty space → **Open Terminal Here**.
5. Type this and press Enter:

   ```
   bash install.sh
   ```

6. Answer the questions:
   - **Install qBittorrent?** and **Install Claude?**: optional extras, each with its own tile.
     Type `y` for yes, or press Enter to skip. Claude takes about 10 minutes and 2 GB. Skipped
     ones are asked again next time you run the installer.
   - **Town for the weather**: type your town (or press Enter for Wexford).
   - **Open Deck TV automatically whenever Desktop Mode starts?**: press Enter for yes.

The installer adds Google Chrome and Spotify if they aren't there yet (no password needed, but it
can take a few minutes), then opens Deck TV full screen, with the cards already sorted into
groups (Streaming, UFC, Torrents). Change them with the pencil button (see below).

## Using it

- **Tiles**: click one to open that app full screen. Click again later to switch back to it.
  **Live TV** (RTÉ, Virgin Media, Plex) and **UFC** (Fights, Results) are groups: they ask which
  app you want first.
- **Edit the home screen**: the **pencil** in the bottom row (or right-click a card → Edit the home
  screen). The cards stay where they are. In edit mode a click only ever drags; everything else is
  on a **right-click**:
  - **Move**: drag a card to where you want it.
  - **Group**: drop an app on the middle of a group to put it in, or on the middle of another app to
    make a new group of the two (name it right there). **New group** in the bar at the bottom makes
    an empty one at the end; right-click an app → **Add to group** works too.
  - **Rename**: right-click → Rename, and type where the name is. Clear it to go back to the card's
    own name.
  - **Hide**: right-click → Hide, on a card or on an app inside a group. **Hidden cards** in the bar
    lists every card, with each group's apps under it, and a switch to bring any of them back.
  - **Change a group's look** (right-click → Change look): one of eight colours, or look like one of
    its apps.
  - **Open a group** (right-click → Open group): rename it, pick its colour, drag its apps into
    order, or drag one onto **Remove from group** (it goes to the end of the home screen). Delete
    group puts all its apps back.
  - Everything saves as you go; hiding and taking out have **Undo**. **Done** (or B) finishes.
  - With a controller, A on a card gives the same choices as a menu.
- **Screensaver**: in the System panel (the Wi-Fi / battery button), press it to change the wait.
- **The dock**: move the mouse to the very bottom edge of the screen and a glowing line appears.
  Click it to open the dock: Home (Deck TV), the apps you have open, and the speaker on the left.
  - Click the speaker for what's playing (previous, play/pause, next), the volume, and
    **Desktop mode**. Scroll over it to change the volume.
  - Right-click an app in the dock to close it.
  - Slide the dock left or right if it's in the way. It hides 4 seconds after the pointer leaves
    it, or click elsewhere.
- **Home**: the TV icon in the dock, or **Super (Windows key) + H** on a keyboard.
- **Alt+Tab** flips between Deck TV and your open apps (tap it to jump back to the last one; hold
  Alt and press Tab to pick, or click an app while it's showing). If Deck TV isn't running, Alt+Tab works as normal.
- **Desktop mode**: the **Desktop** button (bottom-left, beside Power) switches it on and off;
  it's lit while it's on. On, Deck TV lets go of the Deck: no dock, KDE's own Alt+Tab, and apps open
  as ordinary windows that stay put when closed, but the home screen still works for opening them.
  It stays on (through restarts too) until you press the button again, which brings the dock, Deck
  TV's Alt+Tab and full-screen apps back. The dock's speaker menu has **Desktop mode** too, to
  switch it on from inside an app. Get back to the home screen with the **Deck TV icon on the
  desktop**, **Super+H**, the taskbar, Alt+Tab, or Home on the phone remote. **Alt+F5** switches
  desktop mode on or off from anywhere, with a short notice saying which. **Alt+F4** on the home screen closes Deck TV
  completely (dock and all), in case anything ever gets stuck. Open it again from the app menu.
- **Now playing**: whatever's playing shows at the top of the home screen with play/pause and
  skip. Starting something in another app pauses the first. Right-click a tile →
  **Show in now playing** to control that app instead.
- **Weather**: click it for the 5-day forecast.
- **Features**: the button in the top-right corner (or up from the cards) lists what Deck TV can do, in groups.
- **Power**: the button in the bottom-left corner: Sleep, Restart or Power off.
- **System**: the wide button at the end of the row shows Wi-Fi, battery and free space. Click it
  for the details: space on the Deck (and any microSD card or USB drive), the Wi-Fi network and
  signal, sound, battery, and the Deck's temperature and memory. Space turns amber when it's
  getting full and red when it's almost out.
- **Storage** (its own card, or System → Storage): what's using the space on the Deck and any
  microSD card or USB drive. **Overview** splits each drive into Games, Saves & Proton, Apps,
  Videos, Music, Pictures, Documents, Archives & disk images, Caches & Trash and Other; click one to
  see the folders that make it up (games and apps by name). **Folders** goes through any folder,
  sorted by size, name, oldest change or type; **Biggest folders** ranks every folder by size (leaving out the ones that can't be deleted from here, like a drive's top folder, Documents or Steam's own folders; games show as one entry each), filtered by what they mostly hold and by how long since anything in them changed; **File types** lists every ending by space, and opens the biggest files of one. Under the overview, **Ideas**
  finds app caches, shader caches, leftovers from uninstalled games, old downloads and games not
  played for 3 months (those are uninstalled through Steam, which asks first).
  Click a drive on the left to open its top folder, from anywhere.
  **Right-click** anything in a list (or **≡** on the controller, or **M**) for its menu: Open (or the
  folder a file is in), Mark for deletion / Unmark, Uninstall in Steam, Count again, **Hide from
  Biggest folders** (just it, or it and everything inside; only that list changes, nothing on disk),
  Keep / Stop keeping, Show in Dolphin and Copy its location. In Biggest folders, **Hidden → Show**
  lists the hidden ones, dimmed, to bring them back (kept in `~/.config/deck-tv/storage.json`).
  **Count again** on a folder recounts just that folder, straight away (after copying things in, say);
  **Count whole drive** (top right) recounts everything in the background. After **Uninstall in
  Steam**, Storage waits for Steam to finish and recounts the game's folders by itself, saying how
  much was freed.
  **Back** (beside the title, or B / Esc, or the mouse's back button) retraces every step you took,
  one at a time: each view, category, idea, folder, drive and filter, landing on the same item and
  scrolled to where you were. With nowhere left to go back to, it returns to Deck TV.
  Removing is careful on purpose:
  - Each thing is marked **Safe** (comes back by itself), **Look first** (your own files),
    **Careful** (an app's settings, a game's Proton setup or saves, part of a game) or **Can't
    remove** (the folders everything lives in, Steam's and the apps' own files, Steam saves,
    Deck TV, keys and passwords). The server refuses those too, not just the page.
  - The **bin** button (or X) on anything **marks it for deletion**: it turns red and goes on the
    **Delete list** (Y). Nothing is deleted then; keep marking as you look around. The sidebar shows
    how many are ticked to delete and their total. **Put all on the delete list** from an idea adds
    them *unticked*: tick (or X) each one you want deleted. Open any of them on the list to see
    what's inside. The list reads everything again, warns about anything changed in the last 3
    days, and needs a tick for Careful ones. **Clear the list** deletes nothing.
  - **Delete the ticked…** lists every one by name, where it is and its size, then offers two holds
    (until the bar fills), each deleting exactly those, all in one go:
    - **Delete to the Trash** (the same one Dolphin uses): **Undo** straight away, or **Put back**
      from Trash later. The space comes back once the Trash is emptied.
    - **Delete for good**: the space comes back now, and they can't be put back.
    Unticked ones stay on the list, untouched.
  - **Trash**: tick each thing to **Put back** or **Remove for good** (the same itemized list comes
    up first, then a hold).
  - **Keep** (in a folder's panel) protects a folder and everything in it.
  - Everything moved, put back, removed or deleted is written to `~/.local/share/deck-tv/storage.log`;
    Keep marks are in `~/.config/deck-tv/storage.json`. Counting a drive takes a minute or so, in
    the background, and again when it's more than 12 hours old (or **Count again**).
- **Sound** (System → Sound): the volume, and where sound plays: the TV, the Deck's
  speakers, headphones or a Bluetooth speaker. A paired speaker that isn't connected shows
  "Connect": switch it on and click it. **Add speaker** pairs a new one: put it in pairing mode,
  pick it from the list, and sound moves to it.
- **Watchlist**: the bookmark button at the end of the bottom row. Lists of films to watch:
  **Liam and Laura**, **Top 100** (the scratch poster, with the ones already scratched off marked
  watched) and **Kids**. Each film is one slim row (its name and running time): click it (or press A) to open it up for its
  poster, what it's about (from Wikipedia) and **Watch on Movy** (opens Movy and searches it for
  the film), **Trailer**, **Edit**, **Move** (to another list), and (small, off to the right)
  **Mark watched** and **Remove**, each with Undo on the message that follows. **Trailer** plays it right there, with a scrub bar,
  sound and volume, captions (CC), a button per trailer and Fullscreen (nothing in the player can take you off to YouTube); click the poster to see it larger. **To watch / Watched / All**
  picks what shows, and **Sort** orders them (list order, title, year or running time). **Add**
  puts a film on the list you're looking at (a keyboard, or the phone
  remote's, is easiest for typing), **Edit** changes it, moves it to another list or deletes it, and
  **New list / Edit list** make, rename or delete lists. Kept in `~/.config/deck-tv/watchlist.json`.
  Films and shows: when adding or editing, pick **Film** or **Show**. A show's row says how many
  seasons it has, and opening it shows its years running, episodes, episode length and whether it's
  still running (from TMDB); its trailer, poster and summary are the show's, not a film's of the same name.
- **Phone remote**: scan the QR code in the bottom-right corner with your phone (on the same
  Wi-Fi). Most of the phone is a touchpad; the bar along the bottom has **Apps** (switch app),
  **Play/Pause**, **Sound** (skip, volume, mute) and **Type** (a keyboard for the TV, or **Search** in
  Movy and YouTube). The top shows which app is in front on the TV.
  The **screen** button (top right) shows the TV live on the phone, about 10 times a second: tap the
  picture to click there, hold to right-click, two fingers to scroll. Turn the phone sideways for a
  bigger picture. Nothing is captured unless a phone is showing it.
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
- **Trailers in the Watchlist** need a free TMDB key: sign up at themoviedb.org, go to Settings → API,
  copy the API Key, and paste it into the box the first Trailer press shows (the phone remote's
  keyboard is easiest). It's kept in `~/.config/deck-tv/tmdb.json`.
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
