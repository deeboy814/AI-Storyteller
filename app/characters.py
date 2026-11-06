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
DRAWABLE = {"normal", "smile", "shy", "surprise", "sulky", "fear", "sad", "angry", "scorn", "despair", "combat", "cute", "dead", "zombie"}

DEFAULT_SETS = {
    "basic": ["normal 2", "smile 1", "smile 2", "surprise", "fear", "sad", "angry 1", "scorn 1", "shy"],
    "extended": ["normal 2", "smile 1", "smile 2", "surprise", "sulky", "fear", "sad", "angry 1", "angry 2", "scorn 1", "scorn 2",
                 "despair", "shy", "cute 1", "cute 2", "combat 1", "combat 2", "dead"],
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


def drawable(moods: list[str]) -> list[str]:
    return [m for m in moods if mood_base(m) in DRAWABLE]
