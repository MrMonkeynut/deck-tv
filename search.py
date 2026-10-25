"""App-aware search for the phone remote.

Each tile can name a search "kind" in tiles.json. Browser apps (Movy, YouTube) are driven through
Chrome DevTools on 127.0.0.1, so the phone can read what's already in the search box, fill it, list
the results and open one. Spotify takes a spotify:search: link. Anything else gets plain typing.
"""
import json
import subprocess
import time
from urllib.parse import quote

from cdp import Page

# ---------- Movy: a search box that opens from a button, with live results underneath ----------
MOVY_INPUT = 'document.querySelector(\'input[placeholder^="Search movies"]\')'
MOVY_RESULTS = """(() => {
  const inp = %s;
  if (!inp) return [];
  let box = inp; for (let i = 0; i < 6 && box.parentElement; i++) box = box.parentElement;
  return [...box.querySelectorAll('button')].filter(b => b.querySelector('p')).slice(0, 15).map(b => {
    const lines = b.innerText.split('\\n').map(s => s.trim()).filter(s => s && s !== '·');
    return {title: lines[0], sub: lines.slice(1).join(' · ')};
  });
})()""" % MOVY_INPUT


def movy_open_box(page):
    opened = """(() => {
      const inp = %s;
      if (inp && inp.offsetWidth) return true;
      const b = [...document.querySelectorAll('button[aria-label=Search]')].find(b => b.offsetWidth);
      if (b) b.click();
      return !!b;
    })()""" % MOVY_INPUT
    if not page.eval(opened):
        # Title and player pages have no search button: search from Movy's home page
        page.call("Page.navigate", url="https://www.movy.sx/")
        for _ in range(40):  # wait until the page's code is attached to the button, not just drawn
            time.sleep(0.2)
            if page.eval("""(() => { const b = [...document.querySelectorAll('button[aria-label=Search]')].find(b => b.offsetWidth);
                 return !!b && Object.keys(b).some(k => k.startsWith('__reactProps')); })()"""):
                break
        page.eval(opened)
    for _ in range(10):
        if page.eval(f"!!({MOVY_INPUT})"):
            break
        time.sleep(0.1)


def movy_text(page):
    return page.eval(f"(({MOVY_INPUT}) || {{}}).value || ''")


def movy_set(page, text):
    movy_open_box(page)
    if not page.eval(f"(() => {{ const i = {MOVY_INPUT}; if (!i) return false; i.focus(); i.select(); return true; }})()"):
        raise RuntimeError("Movy's search box didn't open")
    if text:
        page.call("Input.insertText", text=text)  # replaces the selection, like typing would
    else:
        key(page, "Backspace")


def movy_results(page):
    return page.eval(MOVY_RESULTS)


def movy_open(page, index, play=False):
    # A result expands when clicked, revealing its "See more" and "Play" links; follow the one wanted
    href = page.eval("""(async () => {
      const inp = %s; if (!inp) return null;
      let box = inp; for (let i = 0; i < 6 && box.parentElement; i++) box = box.parentElement;
      const b = [...box.querySelectorAll('button')].filter(b => b.querySelector('p'))[%d];
      if (!b) return null;
      const item = b.parentElement;
      const links = () => [...item.querySelectorAll('a[href]')].map(a => a.getAttribute('href'));
      if (!links().length) b.click();
      for (let i = 0; i < 20 && !links().length; i++) await new Promise(r => setTimeout(r, 100));
      return links().find(h => %s) || null;
    })()""" % (MOVY_INPUT, index, "h.includes('play=true')" if play else "!h.includes('play=')"))
    if href:
        page.call("Page.navigate", url="https://www.movy.sx" + href)


# ---------- YouTube TV: a search page addressed by URL; results open with focus + Enter ----------
YT_LOCKUPS = "[...document.querySelectorAll('yt-lockup-view-model')]"


def youtube_text(page):
    return page.eval("""(() => /^#\\/(search|results)/.test(location.hash)
      ? ((document.querySelector('ytlr-search-text-box') || {}).textContent || '').trim() : '')()""")


def youtube_set(page, text):
    if text:
        page.eval(f"location.hash = '#/search?q=' + {json.dumps(quote(text))}")
    else:
        page.eval("location.hash = '#/search'")


def youtube_results(page):
    return page.eval(YT_LOCKUPS + """.slice(0, 15).map(l => {
      const parts = [...l.querySelectorAll('yt-lockup-metadata-view-model yt-core-attributed-string')].map(e => e.textContent.trim());
      return {title: parts[0] || '', sub: parts.slice(1, 3).join(' · ')};
    }).filter(r => r.title)""")


def youtube_open(page, index, play=True):  # opening a YouTube result always plays it
    if page.eval(f"(() => {{ const l = {YT_LOCKUPS}[{index}]; if (!l) return false; l.focus(); return true; }})()"):
        key(page, "Enter")


# ---------- shared ----------
KEY_CODES = {"Enter": 13, "Backspace": 8}


def key(page, name):
    for kind in ("keyDown", "keyUp"):
        page.call("Input.dispatchKeyEvent", type=kind, key=name, code=name, windowsVirtualKeyCode=KEY_CODES[name])


def typing_focused(port):
    """Is a text box focused in this browser app? (Backspace elsewhere would go back a page.)"""
    with Page(port) as page:
        return bool(page.eval("""(() => { const e = document.activeElement;
          return !!e && (e.isContentEditable || /^(INPUT|TEXTAREA)$/.test(e.tagName)); })()"""))


KINDS = {
    "movy": (movy_text, movy_set, movy_results, movy_open),
    "youtube": (youtube_text, youtube_set, youtube_results, youtube_open),
}


def context(tile):
    """What the phone's search box should do for this app, and the text already there."""
    kind = tile.get("search")
    ctx = {"app": tile["id"], "name": tile["name"]}
    if kind in KINDS and tile.get("devtools_port"):
        try:
            with Page(tile["devtools_port"]) as page:
                return {**ctx, "mode": "results", "text": KINDS[kind][0](page)}
        except Exception:
            return {**ctx, "mode": "type"}  # app not reachable: fall back to plain typing
    if kind == "spotify":
        return {**ctx, "mode": "send", "text": ""}
    return {**ctx, "mode": "type"}


def run(tile, text):
    """Search in this app. Returns the results the phone can list (may be empty)."""
    kind = tile.get("search")
    if kind == "spotify":
        if text:
            subprocess.Popen(["xdg-open", "spotify:search:" + text],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return []
    _, setter, results, _ = KINDS[kind]
    with Page(tile["devtools_port"]) as page:
        before = results(page)
        setter(page, text)
        if not text:
            return []
        # Results arrive a moment later; wait until they've changed from what was listed before
        found = before
        for _ in range(20):
            time.sleep(0.2)
            found = results(page)
            if found and found != before:
                return found
        return found


def open_result(tile, index, play=False):
    with Page(tile["devtools_port"]) as page:
        KINDS[tile["search"]][3](page, index, play)
