# Deck TV

## After changing anything: put it where Deck TV runs

Deck TV doesn't run from this folder. `install.sh` copies it to `~/.local/share/deck-tv/`, and the
running server serves that copy. Editing files here changes nothing on the TV or the phone until
they're copied across. Do this as the last step of every change, then check that it arrived.

| Changed | Do this | Then |
|---|---|---|
| `remote/` (the phone remote) | `cp remote/index.html ~/.local/share/deck-tv/remote/` (and any other changed file in `remote/`) | Nothing to restart: the page is read from disk each time and sent `no-cache`. Close and reopen the remote on the phone (or pull to refresh). |
| `web/index.html` (the TV's home screen) | `cp web/index.html ~/.local/share/deck-tv/web/` | The open page notices the new file and reloads itself. |
| `server.py` or another `.py`, `tiles.json`, `images/`, `icons/` | `bash install.sh < /dev/null` (copies everything, keeps settings, asks nothing, restarts the server and reopens Deck TV) | Wait a few seconds for it to come back. |

Check it arrived: `cmp remote/index.html ~/.local/share/deck-tv/remote/index.html`, or
`curl -s http://127.0.0.1:8766/ | grep <something new>` for the phone remote (port 8766; the TV page is on 8765).

Settings, groups and the layout live in `~/.config/deck-tv/` and are never overwritten by an update.
