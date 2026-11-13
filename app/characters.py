"""Find named characters that have no art yet, and decide which pictures to draw for a new character.

Everything here is free (no text-model calls): it reads the cast and the narrator's replies.
Signals that a name is a real, recurring character:
  - cast entries that have a description but no pictures;
  - speaker lines   **Name** | "..."     in narrator replies (and the opening scene);
  - status-panel rows  Name:👤/❤️...    between "Characters──" and the closing "──";
  - portrait tags   ![](portrait:Name/mood)  for a name that is not in the cast.
"""
import re
from collections import Counter

# Moods the app knows how to draw for ONE character (see main.MOOD_LOOKS). Moods outside this list (hug, kissing, ...) need
# other people in the frame or romantic content, so automatic sets never draw them; they can still be uploaded by hand.
DRAWABLE = {"normal", "smile", "shy", "surprise", "sulky", "fear", "sad", "angry", "scorn", "despair", "combat", "cute", "dead", "zombie",
            # action moods (one character in motion; see ACTION_MOODS)
            "strike", "guard", "dodge", "charge", "leap", "aim", "cast", "draw weapon", "wounded", "knocked down", "defeated", "exhausted", "flee", "sneak", "battle cry", "victory"}

# The action mood set: one character in the middle of physical action. The narrator prefers these in fights, chases and danger (Art → Style & options → Action moods).
ACTION_MOODS = ["combat 1", "combat 2", "strike 1", "strike 2", "guard", "dodge", "charge", "leap", "aim", "cast", "draw weapon", "wounded", "knocked down",
                "defeated", "exhausted", "flee", "sneak", "battle cry", "victory", "dead"]
# Only for stories with adult content on: single-character pictures for intimate scenes (drawn by the adult picture maker when that is set up).
ADULT_ACTION_MOODS = ["aroused", "seductive", "undressing", "lingerie", "showering", "bathing", "nude", "climaxing", "afterglow",
                       "riding cowgirl", "riding reverse cowgirl", "doggy-style", "blowjob", "missionary",
                       "creampie", "face cum", "ass cum", "anal creampie", "anal", "pussy cum", "swallow", "mouth cum"]
ADULT_POSITIONS = ["riding cowgirl", "riding reverse cowgirl", "doggy-style", "blowjob", "missionary", "anal"]   # sexual positions: the character alone in frame, the partner out of frame
ADULT_FINISHES = ["creampie", "face cum", "ass cum", "anal creampie", "pussy cum", "swallow", "mouth cum"]   # how a sex scene ends: the character alone, the result shown, the partner out of frame
ADULT_ONLY = {"aroused", "seductive", "undressing", "lingerie", "showering", "bathing", "nude", "climaxing", "afterglow", "anal", *ADULT_POSITIONS, *ADULT_FINISHES}   # moods that are always explicit pictures in an adult story

# Image categories: the groups the player sees and edits, each one also a batch-generation set ("Draw all of …"). Defaults: Expressions (faces and feelings), Actions (a body in
# motion), and three that are adult by default: Positions, Finishes and Other adult (undressing, nude, aroused...). The player can rename, change, add or delete them.
POSITIONS_NOTE = ("use these only while a sex scene is actually under way in the story (never before the characters have started, and never for a kiss or a caress): the portrait tag of the "
                  "character in it names the position or act they are in right now. Change the tag when the position changes in the text, not on every line; use aroused or seductive "
                  "before it begins and climaxing at the peak. Each of these pictures shows only that one character (the partner is out of frame), so give the tag to the character the "
                  "line is about. A position that is not listed is a new name in the same style, for example [[Name/spooning]].")
FINISHES_NOTE = ("once the partner has finished, use the tag that says where and how, only for the one line where it happens, then afterglow when the scene is over. Each of these pictures "
                 "shows only that one character (the partner is out of frame).")
DEFAULT_CATEGORIES = [
    {"id": "expressions", "name": "Expressions", "nsfw": False, "shot": "face", "note": ""},
    {"id": "actions", "name": "Actions", "nsfw": False, "shot": "body", "note": ""},
    {"id": "positions", "name": "Positions", "nsfw": True, "shot": "body", "note": POSITIONS_NOTE},
    {"id": "finishes", "name": "Finishes", "nsfw": True, "shot": "body", "note": FINISHES_NOTE},
    {"id": "adult", "name": "Other adult", "nsfw": True, "shot": "body", "note": ""},
]
# where the built-in picture types belong. Everything not named here that the app knows as a face-and-feeling is in Expressions; anything it has never heard of is an action.
BUILTIN_ACTIONS_ADULT = ["aroused", "seductive", "undressing", "lingerie", "showering", "bathing", "nude", "climaxing", "afterglow"]
BUILTIN_CLOTHED = {"aroused", "seductive"}     # adult pictures that keep the character's usual outfit (the others show the body: no everyday clothes in the look)

DEFAULT_SETS = {
    "basic": ["normal 2", "smile 1", "smile 2", "surprise", "fear", "sad", "angry 1", "scorn 1", "shy"],
    "extended": ["normal 2", "smile 1", "smile 2", "surprise", "sulky", "fear", "sad", "angry 1", "angry 2", "scorn 1", "scorn 2",
                 "despair", "shy", "cute 1", "cute 2", "combat 1", "combat 2", "dead"],
    "action": list(ACTION_MOODS),
    "action_adult": list(ADULT_ACTION_MOODS),
}

GENERIC = {
    "narrator", "narration", "you", "player", "user", "ooc", "info", "status", "survivors", "alignment", "people", "characters", "day",
    "situation", "bonds", "food", "note", "notes", "warning", "system", "unknown", "student", "students", "teacher", "teachers",
    "zombie", "zombies", "soldier", "soldiers", "researcher", "researchers", "everyone", "someone", "group total", "total", "turn",
    "partner combat boost", "persona recommendation system", "input", "examples", "example",
}
SPEAKER_RE = re.compile(r"^\*\*([^*|\n]{1,40}?)\*\*\s*\|\s*(.*)$", re.MULTILINE)
PANEL_ROW_RE = re.compile(r"^([A-Z][^\n:/|(]{0,28}?):\s*(?:👤|🧬|😈|🧟|🩸|🤤|🪦)")
PORTRAIT_RE = re.compile(r"portrait:([^/)\n]+)/")
NAME_OK = re.compile(r"^[A-Z][A-Za-z0-9 .'’-]{0,29}$")


def clean_name(raw: str) -> str | None:
    n = re.sub(r"\s+", " ", raw.strip().strip("*_:,.").strip())
    if not n or not NAME_OK.match(n) or n.lower() in GENERIC or len(n.split()) > 4:
        return None
    return n


def known_names(story: dict) -> set[str]:
    names: set[str] = set()
    for c in story.get("cast") or []:
        names.add(c["name"].lower())
        names.add(c["name"].lower().split()[0])
        for a in c.get("aka") or []:            # other names the story uses for them
            names.add(a.lower())
            names.add(a.lower().split()[0])
    av = (story.get("avatar") or {}).get("name") or story.get("player_name") or ""
    if av:
        names.add(av.lower())
        names.add(av.lower().split()[0])
    names |= {n.lower() for n in story.get("art_dismissed") or []}
    return names


def _texts(story: dict):
    if story.get("opening"):
        yield story["opening"]
    for t in story.get("turns") or []:
        if t["role"] == "assistant":
            yield t["text"]


def detect_new(story: dict, min_mentions: int = 1) -> list[dict]:
    """Named characters that appear in the story but are not in the cast, most-mentioned first."""
    known = known_names(story)
    counts: Counter = Counter()
    sample: dict[str, str] = {}
    spelling: dict[str, str] = {}
    for text in _texts(story):
        found = [(m.group(1), m.group(2)) for m in SPEAKER_RE.finditer(text)]
        in_panel = False
        for line in text.splitlines():           # status-panel rows, only between "Characters──" and the closing "──"
            s = line.strip()
            if s.startswith("Characters──"):
                in_panel = True
            elif s == "──":
                in_panel = False
            elif in_panel:
                m = PANEL_ROW_RE.match(s)
                if m:
                    found.append((m.group(1), ""))
        found += [(m.group(1), "") for m in PORTRAIT_RE.finditer(text)]
        for raw, line in found:
            n = clean_name(raw)
            if not n or n.lower() in known or n.lower().split()[0] in known:
                continue
            key = n.lower()
            counts[key] += 1
            spelling.setdefault(key, n)
            if line and key not in sample:
                sample[key] = line.strip()[:160]
    return [{"name": spelling[k], "mentions": c, "sample": sample.get(k, "")}
            for k, c in counts.most_common() if c >= min_mentions]


def missing_art(story: dict) -> list[dict]:
    """Cast members that have a description but no pictures yet."""
    dismissed = {n.lower() for n in story.get("art_dismissed") or []}
    return [{"name": c["name"], "slug": c["slug"], "appearance": c.get("appearance", "")}
            for c in story.get("cast") or []
            if c.get("appearance", "").strip() and not c.get("moods") and c["name"].lower() not in dismissed]


def mood_base(mood: str) -> str:
    return re.sub(r"\s*\d+$", "", mood.strip().lower())


def vocabulary_from_cast(story: dict, exclude_slug: str | None = None) -> list[str]:
    """The moods that at least half of the characters with art already have, limited to ones that can be drawn for one person."""
    chars = [c for c in story.get("cast") or [] if c.get("moods") and c["slug"] != exclude_slug]
    if not chars:
        return []
    counts = Counter(m for c in chars for m in c["moods"])
    need = max(1, (len(chars) + 1) // 2)
    return sorted((m for m, n in counts.items() if n >= need and mood_base(m) in DRAWABLE),
                  key=lambda m: (mood_base(m) != "normal", m))


def add_to_extended(cfg: dict, mood: str) -> bool:
    """A mood the narrator asked for and the player had drawn joins the extended set (unless a set already has it). True if added."""
    mood = mood.strip().lower()
    if not mood or mood == "default":
        return False
    sets = cfg.setdefault("mood_sets", {k: list(v) for k, v in DEFAULT_SETS.items()})
    have = {mood_base(m) for k in sets for m in sets.get(k, [])} | {m.lower() for k in sets for m in sets.get(k, [])}
    if mood in have or mood_base(mood) in have:
        return False
    sets.setdefault("extended", []).append(mood)
    return True


# ---------------- characters that are probably the same person ----------------
import difflib

_TITLES = {"dr", "doctor", "mr", "mrs", "ms", "miss", "mx", "sir", "lady", "lord", "captain", "capt", "sergeant", "sgt", "lieutenant", "lt", "major", "colonel", "general",
           "commander", "marshal", "professor", "prof", "father", "mother", "sister", "brother", "chief", "instructor", "officer", "private", "corporal", "king", "queen",
           "prince", "princess", "master", "madam", "mistress", "the", "old", "young", "little"}


def _tokens(name: str) -> list[str]:
    return [t for t in re.sub(r"[^\w' -]", " ", name.lower()).replace("-", " ").split() if t and t not in _TITLES]


def find_duplicates(story: dict) -> list[dict]:
    """Pairs of cast entries that are probably one person: the same name apart from a title, a short form of a fuller name ("Ann" and "Ann Marsh"), or names spelled almost
    alike. Free (no AI). Each result names which entry to fold into which (the shorter name into the fuller one). A short name that fits several fuller names is left alone."""
    cast = [c for c in story.get("cast") or [] if c.get("name")]
    toks = {c["slug"]: _tokens(c["name"]) for c in cast}
    out, seen = [], set()
    for i, a in enumerate(cast):
        for b in cast[i + 1:]:
            ta, tb = toks[a["slug"]], toks[b["slug"]]
            if not ta or not tb or a.get("player") != b.get("player"):
                continue
            if any(x.lower() == b["name"].lower() for x in a.get("aka") or []) or any(x.lower() == a["name"].lower() for x in b.get("aka") or []):
                continue
            reason = ""
            if sorted(ta) == sorted(tb):
                reason = "the same name apart from a title"
            else:
                short, long_ = (ta, tb) if len(ta) < len(tb) else (tb, ta)
                if len(short) < len(long_) and short and short == long_[:len(short)] or (len(short) == 1 and len(long_) > 1 and short[0] == long_[-1]):
                    fits = [c for c in cast if (lambda t: t and len(t) >= len(short) and (t[:len(short)] == short or (len(short) == 1 and t[-1] == short[0])))(toks[c["slug"]])]
                    if len([c for c in fits if toks[c["slug"]] != short]) == 1:
                        reason = "one is the short form of the other"
                elif len(" ".join(ta)) >= 5 and difflib.SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio() >= 0.86:
                    reason = "the names are spelled almost alike"
            if not reason:
                continue
            fold, into = (a, b) if len(toks[a["slug"]]) < len(toks[b["slug"]]) or (len(toks[a["slug"]]) == len(toks[b["slug"]]) and len(a["name"]) <= len(b["name"])) else (b, a)
            key = tuple(sorted((a["slug"], b["slug"])))
            if key not in seen:
                seen.add(key)
                out.append({"fold": fold["slug"], "into": into["slug"], "names": [fold["name"], into["name"]], "reason": reason})
    return out[:10]


# Throwaway characters for the unnamed people of a story (the waiter, a guard, someone in the crowd): kept in the cast with generic=True, shown as one "Generic characters" card.
GENERIC_GROUP = "Generic Characters"
GENERIC_PRESETS = [
    ("Waitress", "adult woman, late twenties, neat apron over a plain uniform, hair tied back, friendly professional look"),
    ("Waiter", "adult man, late twenties, white shirt and black waistcoat, neat short hair, polite professional look"),
    ("Shopkeeper", "adult man, fifties, simple apron over everyday clothes, tired kind eyes, short greying hair"),
    ("Bartender", "adult man, thirties, rolled-up sleeves and a dark apron, short beard, relaxed look"),
    ("Guard", "adult man, thirties, plain practical uniform and gear, serious face, short hair"),
    ("Police Officer", "adult woman, thirties, dark uniform and cap, hair in a bun, alert, no-nonsense look"),
    ("Office Lady", "adult woman, late twenties, neat blouse and skirt, shoulder-length hair, lanyard around the neck"),
    ("Salaryman", "adult man, forties, plain dark suit and tie, tired eyes, short hair"),
    ("Female Student", "young adult woman, about twenty, casual campus clothes, backpack, shoulder-length hair"),
    ("Male Student", "young adult man, about twenty, casual campus clothes, backpack, short messy hair"),
    ("Old Man", "elderly man, seventies, grey hair, wrinkled kind face, simple cardigan"),
    ("Old Woman", "elderly woman, seventies, grey hair in a bun, wrinkled kind face, simple shawl"),
    ("Doctor", "adult woman, forties, white coat over plain clothes, hair tied back, calm look"),
    ("Passerby Woman", "adult woman, thirties, ordinary everyday clothes, shoulder-length hair, neutral expression"),
    ("Passerby Man", "adult man, thirties, ordinary everyday clothes, short hair, neutral expression"),
]
