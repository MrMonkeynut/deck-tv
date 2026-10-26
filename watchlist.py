"""Deck TV - the watchlist: lists of films to watch ("Liam and Laura", "Top 100", "Kids"), each
film marked watched or not. Kept in ~/.config/deck-tv/watchlist.json, apart from tiles.json, so
reinstalling Deck TV doesn't lose it.
"""
import json
import re
import time
from pathlib import Path

FILE = Path.home() / ".config/deck-tv/watchlist.json"

MAX_LISTS, MAX_ITEMS = 20, 500

# The "100 Movies Bucket List" scratch poster: (title, year, directed by, minutes, scratched off already).
# A trilogy's minutes are all its films together.
TOP_100 = [
    ("The Shawshank Redemption", 1994, "Frank Darabont", 142, True), ("The Dark Knight", 2008, "Christopher Nolan", 152, True),
    ("City of God", 2002, "Fernando Meirelles, Kátia Lund", 130, False), ("Pulp Fiction", 1994, "Quentin Tarantino", 154, True),
    ("Amélie", 2001, "Jean-Pierre Jeunet", 122, False), ("12 Angry Men", 1957, "Sidney Lumet", 96, False),
    ("Blade Runner", 1982, "Ridley Scott", 117, False), ("A Clockwork Orange", 1971, "Stanley Kubrick", 136, False),
    ("The Deer Hunter", 1978, "Michael Cimino", 183, False), ("Casablanca", 1942, "Michael Curtiz", 102, False),
    ("E.T.", 1982, "Steven Spielberg", 115, False), ("Up", 2009, "Pete Docter, Bob Peterson", 96, True),
    ("The Rocky Horror Picture Show", 1975, "Jim Sharman", 100, False), ("The Big Lebowski", 1998, "The Coen Brothers", 117, True),
    ("Office Space", 1999, "Mike Judge", 89, False), ("Fight Club", 1999, "David Fincher", 139, True),
    ("Snatch", 2000, "Guy Ritchie", 104, True), ("Oldboy", 2003, "Park Chan-wook", 120, True),
    ("Léon: The Professional", 1994, "Luc Besson", 110, False), ("Scarface", 1983, "Brian De Palma", 170, True),
    ("Raiders of the Lost Ark", 1981, "Steven Spielberg", 115, False), ("The Lord of the Rings (trilogy)", 2001, "Peter Jackson", 558, True),
    ("Moonlight", 2016, "Barry Jenkins", 111, False), ("The Matrix", 1999, "The Wachowskis", 136, True),
    ("Apocalypse Now", 1979, "Francis Ford Coppola", 147, False), ("The Grand Budapest Hotel", 2014, "Wes Anderson", 99, False),
    ("Monty Python's Life of Brian", 1979, "Terry Jones", 94, True), ("In Bruges", 2008, "Martin McDonagh", 107, True),
    ("3 Idiots", 2009, "Rajkumar Hirani", 170, False), ("The Godfather", 1972, "Francis Ford Coppola", 175, True),
    ("The Notebook", 2004, "Nick Cassavetes", 123, False), ("The Lion King", 1994, "Roger Allers, Rob Minkoff", 88, True),
    ("Stand by Me", 1986, "Rob Reiner", 89, False), ("Dirty Dancing", 1987, "Emile Ardolino", 100, False),
    ("Jurassic Park", 1993, "Steven Spielberg", 127, False), ("2001: A Space Odyssey", 1968, "Stanley Kubrick", 149, False),
    ("The Goonies", 1985, "Richard Donner", 114, True), ("WALL-E", 2008, "Andrew Stanton", 98, True),
    ("Groundhog Day", 1993, "Harold Ramis", 101, True), ("Star Wars: Episodes IV, V, VI", 1977, "George Lucas", 376, True),
    ("Schindler's List", 1993, "Steven Spielberg", 195, True), ("Breakfast at Tiffany's", 1961, "Blake Edwards", 115, False),
    ("Shaun of the Dead", 2004, "Edgar Wright", 99, True), ("Back to the Future", 1985, "Robert Zemeckis", 116, True),
    ("Forrest Gump", 1994, "Robert Zemeckis", 142, True), ("The Silence of the Lambs", 1991, "Jonathan Demme", 118, True),
    ("The Shining", 1980, "Stanley Kubrick", 146, True), ("Alien", 1979, "Ridley Scott", 117, True),
    ("Memento", 2000, "Christopher Nolan", 113, True), ("Se7en", 1995, "David Fincher", 127, True),
    ("Halloween", 1978, "John Carpenter", 91, True), ("Jaws", 1975, "Steven Spielberg", 124, True),
    ("The Evil Dead", 1981, "Sam Raimi", 85, True), ("Airplane!", 1980, "Zucker, Abrahams, Zucker", 88, False),
    ("Mean Girls", 2004, "Mark Waters", 97, True), ("Lawrence of Arabia", 1962, "David Lean", 228, False),
    ("Drive", 2011, "Nicolas Winding Refn", 100, False), ("Casino Royale", 2006, "Martin Campbell", 144, False),
    ("Ghostbusters", 1984, "Ivan Reitman", 105, True), ("Rosemary's Baby", 1968, "Roman Polanski", 137, False),
    ("Mary and Max", 2009, "Adam Elliot", 92, False), ("The Terminator", 1984, "James Cameron", 107, True),
    ("The Green Mile", 1999, "Frank Darabont", 189, True), ("Rocky", 1976, "John G. Avildsen", 120, True),
    ("This Is Spinal Tap", 1984, "Rob Reiner", 82, True), ("American Psycho", 2000, "Mary Harron", 102, False),
    ("Citizen Kane", 1941, "Orson Welles", 119, False), ("The Intouchables", 2011, "O. Nakache, É. Toledano", 112, False),
    ("American History X", 1998, "Tony Kaye", 119, True), ("Seven Samurai", 1954, "Akira Kurosawa", 207, False),
    ("Gladiator", 2000, "Ridley Scott", 155, False), ("The Good, the Bad and the Ugly", 1966, "Sergio Leone", 178, False),
    ("Brokeback Mountain", 2005, "Ang Lee", 134, False), ("The Great Dictator", 1940, "Charles Chaplin", 125, False),
    ("Toy Story", 1995, "John Lasseter", 81, True), ("The Prestige", 2006, "Christopher Nolan", 130, True),
    ("Titanic", 1997, "James Cameron", 194, True), ("Her", 2013, "Spike Jonze", 126, True),
    ("Boyz n the Hood", 1991, "John Singleton", 112, True), ("Four Weddings and a Funeral", 1994, "Mike Newell", 117, False),
    ("Little Miss Sunshine", 2006, "J. Dayton, V. Faris", 101, False), ("Trainspotting", 1996, "Danny Boyle", 93, True),
    ("The Departed", 2006, "Martin Scorsese", 151, True), ("Saving Private Ryan", 1998, "Steven Spielberg", 169, True),
    ("No Country for Old Men", 2007, "The Coen Brothers", 122, True), ("The Pianist", 2002, "Roman Polanski", 150, True),
    ("Dr. Strangelove", 1964, "Stanley Kubrick", 95, False), ("Lagaan", 2001, "Ashutosh Gowariker", 224, False),
    ("Baahubali", 2015, "S. S. Rajamouli", 159, False), ("Psycho", 1960, "Alfred Hitchcock", 109, False),
    ("Vertigo", 1958, "Alfred Hitchcock", 128, False), ("The Truman Show", 1998, "Peter Weir", 103, False),
    ("Reservoir Dogs", 1992, "Quentin Tarantino", 99, True), ("Amadeus", 1984, "Miloš Forman", 160, False),
    ("Enter the Dragon", 1973, "Robert Clouse", 102, False), ("The Wizard of Oz", 1939, "Victor Fleming", 102, True),
    ("Gangs of New York", 2002, "Martin Scorsese", 167, True), ("The Usual Suspects", 1995, "Bryan Singer", 106, True),
    ("Spirited Away", 2001, "Hayao Miyazaki", 125, False), ("Good Will Hunting", 1997, "Gus Van Sant", 126, True),
]


def starting_lists():
    """The first time: Liam and Laura's list and the kids' (empty), and the poster's hundred, with
    the ones already scratched off marked watched."""
    now = int(time.time())
    top = [{"id": f"i-top{n}", "title": t, "year": y, "runtime": mins, "note": by, "watched": w, "added": now,
            "watchedAt": now if w else None} for n, (t, y, by, mins, w) in enumerate(TOP_100)]
    return {"lists": [
        {"id": "l-us", "name": "Liam and Laura", "items": []},
        {"id": "l-top100", "name": "Top 100", "items": top},
        {"id": "l-kids", "name": "Kids", "items": []},
    ]}


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            raw = json.load(f)
        added = with_runtimes(raw)
        data = clean(raw)
        if data is not None:
            if added:
                try:
                    write(data)
                except OSError:
                    pass
            return data
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if FILE.exists():  # there, but unreadable: keep a copy before starting again
        try:
            FILE.replace(FILE.with_name("watchlist.bad.json"))
        except OSError:
            return {"lists": []}
    data = starting_lists()
    try:
        write(data)
    except OSError:
        pass
    return data


def with_runtimes(raw):
    """Lists made before films had a running time: the poster's films get theirs, once. (One whose
    time was since cleared keeps it cleared: it has the key, empty.) Says whether any were added."""
    added = False
    for lst in raw.get("lists", []):
        for it in lst.get("items", []):
            m = re.fullmatch(r"i-top(\d+)", str(it.get("id", "")))
            if m and "runtime" not in it and int(m[1]) < len(TOP_100):
                it["runtime"] = TOP_100[int(m[1])][3]
                added = True
    return added


def write(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    tmp.replace(FILE)  # all or nothing: a power cut mid-save can't leave half a file


def _ts(v):
    return int(v) if isinstance(v, (int, float)) and v > 0 else None


def clean(data):
    """The watchlist from the page, checked: known shapes, sensible lengths. None if it's not one."""
    if not isinstance(data, dict) or not isinstance(data.get("lists"), list):
        return None
    lists = []
    for lst in data["lists"][:MAX_LISTS]:
        if not isinstance(lst, dict) or not re.fullmatch(r"l-[a-z0-9]{1,16}", str(lst.get("id", ""))):
            return None
        items = []
        for it in (lst.get("items") or [])[:MAX_ITEMS]:
            if not isinstance(it, dict) or not re.fullmatch(r"i-[a-z0-9]{1,20}", str(it.get("id", ""))):
                return None
            title = str(it.get("title", "")).strip()[:80]
            if not title:
                continue
            year = it.get("year")
            year = year if isinstance(year, int) and 1870 <= year <= 2100 else None
            # A show's running time is its episodes' (and it has seasons, episodes and years running, from TMDB)
            kind = "show" if it.get("kind") == "show" else "film"
            runtime = it.get("runtime")
            runtime = runtime if isinstance(runtime, int) and 1 <= runtime <= 999 else None
            watched = bool(it.get("watched"))
            entry = {"id": it["id"], "kind": kind, "title": title, "year": year, "runtime": runtime,
                     "note": str(it.get("note") or "").strip()[:60],
                     "watched": watched, "added": _ts(it.get("added")) or int(time.time()),
                     "watchedAt": (_ts(it.get("watchedAt")) or int(time.time())) if watched else None}
            if kind == "show":
                for field, top in (("seasons", 200), ("episodes", 20000)):
                    v = it.get(field)
                    entry[field] = v if isinstance(v, int) and 1 <= v <= top else None
                entry["years"] = str(it.get("years") or "")[:12] or None      # "2014–2019", "2022–"
                entry["status"] = str(it.get("status") or "")[:24] or None    # "Ended", "Returning Series"
            items.append(entry)
        lists.append({"id": lst["id"], "name": str(lst.get("name", "")).strip()[:28] or "Watchlist", "items": items})
    return {"lists": lists}


def save(body):
    """Store the whole watchlist from the page. Returns (body, status)."""
    data = clean(body)
    if data is None:
        return {"error": "bad watchlist"}, 400
    write(data)
    return {"ok": True}, 200
