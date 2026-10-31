"""The tour's visit to Movy: the home screen's tour opens Movy and shows it off, captions and all.

Movy is its own Chrome window, in front of Deck TV, so the home screen's captions can't be seen over
it. So this drives Movy over DevTools (as search.py does) and draws the same caption, and a ring round
what it's talking about, inside Movy's page. Esc (or B) in Movy stops it; the right arrow or Enter skips
on. It shows what Movy is (a simple, Netflix-like way in), how much is on it, and its search, then
puts Deck TV back in front, where the tour carries on. The home screen asks how it's getting on with
status().
"""
import json
import threading
import time

from cdp import Page
import search

MOVY = "https://www.movy.sx"
state = {"run": "idle"}  # idle, running, done (Deck TV is back in front), stopped or failed
_lock = threading.Lock()


class Stopped(Exception):
    pass


# The caption and ring, drawn in Movy's page and sized to match the home screen's (its 1280-wide stage)
OVERLAY = r"""(() => {
  if (window.__deckTour) return;
  const css = document.createElement('style');
  css.textContent = `
    #dt-ring { position: fixed; z-index: 2147483646; pointer-events: none; border: 3px solid #fff; border-radius: 22px; opacity: 0;
      box-shadow: 0 0 0 9999px rgba(8,10,14,.55), 0 0 30px rgba(255,255,255,.35);
      transition: left .5s ease, top .5s ease, width .5s ease, height .5s ease, opacity .35s; }
    #dt-ring.show { opacity: 1; }
    #dt-cap { position: fixed; z-index: 2147483647; pointer-events: none; left: 50%; bottom: calc(30px * var(--k));
      width: calc(780px * var(--k)); padding: calc(18px * var(--k)) calc(26px * var(--k)) calc(16px * var(--k));
      border-radius: calc(24px * var(--k)); background: #F2F4F8; color: #0E1116; box-shadow: 0 18px 50px rgba(0,0,0,.5);
      font-family: "Space Grotesk", "Cantarell", "Noto Sans", system-ui, sans-serif;
      transform: translateX(-50%) translateY(14px); opacity: 0; transition: opacity .3s, transform .3s; }
    #dt-cap.top { bottom: auto; top: calc(30px * var(--k)); }
    #dt-cap.show { opacity: 1; transform: translateX(-50%) translateY(0); }
    #dt-cap .s { display: flex; gap: 10px; font-size: calc(14px * var(--k)); font-weight: 600; color: #5B6475; text-transform: uppercase; letter-spacing: .06em; }
    #dt-cap .s span:last-child { margin-left: auto; text-transform: none; letter-spacing: 0; font-weight: 500; }
    #dt-cap b { display: block; margin-top: calc(6px * var(--k)); font-size: calc(27px * var(--k)); line-height: 1.2; }
    #dt-cap p { margin: calc(4px * var(--k)) 0 0; font-size: calc(19px * var(--k)); line-height: 1.35; color: #2A3140; }
    #dt-cap .bar { height: 4px; margin-top: calc(14px * var(--k)); border-radius: 2px; background: #D5DAE3; overflow: hidden; }
    #dt-cap .bar i { display: block; height: 100%; width: 0; background: #0E1116; }`;
  const ring = Object.assign(document.createElement('div'), { id: 'dt-ring' });
  const cap = Object.assign(document.createElement('div'), { id: 'dt-cap' });
  cap.innerHTML = '<div class="s"><span>Movy</span><span class="n"></span><span>Esc or B stops · ▶ skips</span></div><b></b><p></p><div class="bar"><i></i></div>';
  const t = window.__deckTour = { stop: false, skip: false, target: null };
  const place = () => {
    document.documentElement.style.setProperty('--k', innerWidth / 1280);
    let els = null;
    try { els = t.target && t.target(); } catch {}
    els = (Array.isArray(els) ? els : [els]).filter(e => e && e.offsetWidth);
    const rs = els.map(e => e.getBoundingClientRect()), pad = 10;
    const r = rs.length ? { left: Math.min(...rs.map(x => x.left)), top: Math.min(...rs.map(x => x.top)),
      right: Math.max(...rs.map(x => x.right)), bottom: Math.max(...rs.map(x => x.bottom)) } : null;
    if (r) { r.width = r.right - r.left; r.height = r.bottom - r.top; }
    ring.classList.toggle('show', !!(r && r.width));
    if (r && r.width) Object.assign(ring.style, { left: r.left - pad + 'px', top: r.top - pad + 'px', width: r.width + pad * 2 + 'px', height: r.height + pad * 2 + 'px' });
    cap.classList.toggle('top', !!(r && r.width) && r.top > innerHeight - r.bottom);
  };
  t.show = (target, title, text, n, ms) => {
    if (!ring.isConnected) document.documentElement.append(css, ring, cap);
    t.target = target;
    cap.querySelector('.n').textContent = n;
    cap.querySelector('b').textContent = title;
    cap.querySelector('p').textContent = text;
    cap.classList.add('show');
    place();
    const bar = cap.querySelector('.bar i');
    bar.style.transition = 'none'; bar.style.width = '0'; void bar.offsetWidth;
    bar.style.transition = `width ${ms}ms linear`; bar.style.width = '100%';
  };
  t.hide = () => { t.target = null; cap.classList.remove('show'); ring.classList.remove('show'); };
  t.remove = () => { clearInterval(t.timer); ring.remove(); cap.remove(); css.remove(); delete window.__deckTour; };
  t.timer = setInterval(place, 250);  // things move as rows load and the page scrolls
  addEventListener('keydown', e => {
    if (!window.__deckTour) return;
    if (e.key === 'Escape' || e.key === 'Backspace') t.stop = true;
    else if (e.key === 'ArrowRight' || e.key === 'Enter') t.skip = true;
    else return;
    e.preventDefault(); e.stopImmediatePropagation();
  }, true);
})()"""

# Helpers in Movy's page: the search box with its results (for the ring), and a slow scroll
FIND = r"""// The search box and its results (found as search.py finds them), for the ring to go round them all
window.__dtSearch = () => {
  const inp = %s;
  if (!inp) return null;
  let box = inp; for (let i = 0; i < 6 && box.parentElement; i++) box = box.parentElement;
  return [inp.parentElement, ...[...box.querySelectorAll('button')].filter(b => b.querySelector('p'))];
};
// Scroll slowly down the page over ms, to show how much is on it
window.__dtGlide = ms => {
  const from = scrollY, by = Math.min(document.body.scrollHeight - innerHeight - from, ms * .45), t0 = window.__dtGlide.at = performance.now();
  const step = now => {
    const f = Math.min(1, (now - t0) / ms);
    scrollTo(0, from + by * (f < .5 ? 2 * f * f : 1 - (-2 * f + 2) ** 2 / 2));
    if (f < 1 && window.__deckTour && window.__dtGlide.at === t0) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
};""" % search.MOVY_INPUT


def status():
    with _lock:
        return dict(state)


def start(tile, launch, home, caption, step=""):
    """Run the visit in the background (one at a time). launch, home and caption (one over every app,
    tour_overlay.py's) are the server's own. Movy is left open and in front when it's done; stopped or
    gone wrong, Deck TV comes back to the front."""
    with _lock:
        if state["run"] == "running":
            return {"ok": True, "already": True}, 200
        state.clear()
        state["run"] = "running"
    threading.Thread(target=_run, args=(tile, launch, home, caption, step), daemon=True).start()
    return {"ok": True}, 200


def _finish(run, error=None):
    with _lock:
        state["run"] = run
        if error:
            state["error"] = error


def _run(tile, launch, home, caption, step):
    port = tile["devtools_port"]
    body, code = launch(tile["id"])
    if code != 200:
        return _finish("failed", body.get("error", "couldn't open Movy"))
    if not _wait(lambda: _ready(port), 15):
        home()
        return _finish("failed", "Movy didn't open in time")
    page = None
    try:
        page = Page(port, timeout=10)
        # Drawn again on each new page it goes to, for as long as this connection stays open
        page.call("Page.addScriptToEvaluateOnNewDocument", source=OVERLAY + ";" + FIND)
        _tour(page, caption, step)
        _finish("done")
        page = _done(page)
    except Stopped:
        _quietly(page, "document.querySelector('video')?.pause()")  # nothing left playing behind
        page = _done(page)
        home()
        _finish("stopped")
    except Exception as e:
        page = _done(page)
        home()
        _finish("failed", str(e))


def _done(page):
    _quietly(page, "window.__deckTour && window.__deckTour.remove()")
    if page:
        page.close()


def _tour(page, caption, step):
    def say(title, text, secs):
        """A caption over Movy for secs (the bar runs that long); what happens under it is up to the caller"""
        caption({"cmd": "caption", "section": "Movy", "n": step, "title": title, "text": text, "ms": int(secs * 1000)})

    def ring(target):
        """Ring something on Movy's page (a script for the element), dimming the rest; None for no ring"""
        page.eval(OVERLAY)
        page.eval(f"window.__deckTour.show(() => {target or 'null'}, '', '', '', 0); "
                  "document.getElementById('dt-cap').classList.remove('show')")  # the caption's over every app, not here

    def hold(secs, skippable=False):
        end = time.time() + secs
        while time.time() < end:
            flags = page.eval("window.__deckTour ? [window.__deckTour.stop, window.__deckTour.skip] : [false, false]")
            if flags[0]:
                raise Stopped
            if flags[1]:
                page.eval("window.__deckTour.skip = false")
                if skippable:
                    return
            time.sleep(0.2)

    def go(path, settle=1.2):
        page.call("Page.navigate", url=MOVY + path)
        if not _wait(lambda: page.eval("document.readyState === 'complete' && !!document.querySelector('h1, h2')"), 15):
            raise RuntimeError("Movy's page didn't load")
        page.eval(OVERLAY + ";" + FIND)
        hold(settle)  # its rows fill in

    def glide(secs):
        """Scroll slowly down the page, to show how much there is on it"""
        page.eval(f"window.__dtGlide({int(secs * 1000)})")
        hold(secs + 0.3)

    # One caption over the lot: the home page, the films, the shows, each scrolled through a little
    say("Movy", "Films and TV shows, everything you could want, in a simple app like Netflix.", 15)
    glide(3)
    go("/browse/movie", 1)
    glide(3.5)
    go("/browse/tv", 1)
    glide(3.5)
    page.eval("window.__dtGlide.at = null; window.scrollTo({ top: 0, behavior: 'smooth' })")
    hold(0.8)

    # Search: typed a letter at a time, so it looks like someone typing
    say("Search", "Find just about anything, film or show, old or new.", 6)
    search.movy_open_box(page)
    page.eval(f"(() => {{ const i = {search.MOVY_INPUT}; if (i) {{ i.focus(); i.select(); }} }})()")
    for ch in "Father Ted":
        page.call("Input.insertText", text=ch)
        time.sleep(0.15)
    hold(1)
    ring("window.__dtSearch()")
    hold(3.5, skippable=True)
    page.eval("window.__deckTour.hide()")


def _ready(port):
    try:
        with Page(port) as page:
            return page.eval("location.hostname.includes('movy') && document.readyState === 'complete'")
    except Exception:
        return False


def _wait(test, secs):
    end = time.time() + secs
    while time.time() < end:
        try:
            if test():
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _quietly(page, script):
    try:
        if page:
            page.eval(script)
    except Exception:
        pass
