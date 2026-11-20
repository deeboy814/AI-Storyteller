"""Character sheets for NPCs (optional, per story, off by default).

A sheet is a short list of stats ("Level: 3", "HP: 40/40", "Strength: 12"...), free notes, and an optional picture the player uploads for their own reference (a stat card,
a drawn sheet, a map of the character). The stats are free-form so they fit any game system the story uses. Stored as story["statsheets"]:
    {"on": False, "auto": True, "items": {slug: {"stats": [{"name", "value"}], "notes": "", "image": "", "turn": 0}}}
When a story has them on:
  - the narrator is sent the sheet of every character who is named in the last few turns (a hidden note on the player's move, like lore cards), and
  - when "auto" is on it is told to keep the sheets current with one hidden line per character that changed:
        <sheet>Name | Level: 4; HP: 30/50 | a short note</sheet>
    which the app reads, applies and hides from the player (same way as the <items> line of the inventory).
No AI call is involved."""
import re

MAX_STATS = 40
MAX_NOTE = 1200
NOTE_TURNS = 3             # the last turns scanned for names
NOTE_MAX_SHEETS = 6
NOTE_MAX_CHARS = 2400

SHEET_RE = re.compile(r"<sheet>(.*?)(?:</sheet>|\Z)", re.DOTALL | re.IGNORECASE)

RULE = ("CHARACTER SHEETS (kept by the app): the app keeps stat sheets for some of the story's non-player characters. The CHARACTER SHEETS note that comes with the player's move lists "
        "the current sheet of each character named in the recent story: those numbers and facts are true, so keep the characters consistent with them (an NPC cannot do what their "
        "sheet says is beyond them), and do not recite the sheet.\n")
RULE_AUTO = ("When a character's stats or sheet facts change IN THIS REPLY (they level up, are hurt or healed, learn a skill, gain or lose something that is on their sheet), or the "
             "story first gives a character stats, add one hidden line per character near the end of the reply (before the state block, if this story has one):\n"
             "<sheet>Name | Level: 4; HP: 30/50; Strength: 12 | a short note, if any</sheet>\n"
             "Use the character's name exactly as in the story. Write only the stats that are new or changed (the app keeps the others); a stat is 'Name: value'; use the same stat names "
             "the story's own rules use (and the names already on the sheet). The notes part is optional and replaces the old note, so keep what still matters. Never for the player "
             "character (their numbers are in the story's own panel) and never for a character without any stats worth keeping. The line is hidden from the player.")


def blank() -> dict:
    return {"on": False, "auto": True, "items": {}}


def get(story: dict) -> dict:
    b = story.get("statsheets")
    if not isinstance(b, dict):
        b = story["statsheets"] = blank()
    b.setdefault("on", False)
    b.setdefault("auto", True)
    if not isinstance(b.get("items"), dict):
        b["items"] = {}
    return b


def is_on(story: dict) -> bool:
    return bool((story.get("statsheets") or {}).get("on"))


def _clean_text(v, n: int) -> str:
    return " ".join(str(v or "").split())[:n]


def clean_stats(raw) -> list[dict]:
    """A list of {name, value} (from the editor), or text with one 'Name: value' per line / separated by semicolons."""
    pairs = []
    if isinstance(raw, str):
        for part in re.split(r"[;\n]+", raw):
            m = re.match(r"^(.*?)\s*[:=]\s*(.*)$", part.strip())
            pairs.append((m.group(1), m.group(2)) if m else (part.strip(), ""))
    else:
        for it in raw or []:
            if isinstance(it, dict):
                pairs.append((it.get("name"), it.get("value")))
    out, seen = [], set()
    for n, v in pairs:
        n, v = _clean_text(n, 30).strip(" :"), _clean_text(v, 60)
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append({"name": n, "value": v})
    return out[:MAX_STATS]


def sheet_of(story: dict, slug: str) -> dict:
    return get(story)["items"].setdefault(slug, {"stats": [], "notes": "", "image": "", "turn": 0})


def has_content(sh: dict | None) -> bool:
    return bool(sh and (sh.get("stats") or (sh.get("notes") or "").strip()))


def line(name: str, sh: dict) -> str:
    stats = "; ".join(f"{s['name']}: {s['value']}" if s.get("value") else s["name"] for s in sh.get("stats") or [])
    notes = (sh.get("notes") or "").strip()
    return f"- {name}" + (f" | {stats}" if stats else "") + (f" | {notes}" if notes else "")


def _names(ch: dict) -> list[str]:
    out = [ch.get("name", "")] + list(ch.get("aka") or [])
    first = (ch.get("name") or "").split()[0] if ch.get("name") else ""
    if first and len(first) > 2:
        out.append(first)
    return [n for n in out if n]


def _hit(key: str, text: str) -> bool:
    return bool(re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text, re.IGNORECASE))


def find_char(story: dict, name: str) -> dict | None:
    """The cast member a name in a tag means: the full name, an 'also called' name, or a first name that only one character has."""
    n = " ".join(str(name).split()).lower().strip("*_` ")
    if not n:
        return None
    cast = [c for c in story.get("cast") or [] if c.get("name")]
    for c in cast:
        if n == c["name"].lower() or n in {a.lower() for a in c.get("aka") or []}:
            return c
    near = [c for c in cast if c["name"].lower().split()[0] == n]
    return near[0] if len(near) == 1 else None


def note(story: dict, text_override: str | None = None) -> str:
    """The hidden note added to the player's move: the sheets of the characters named in the recent turns."""
    b = get(story)
    if not b["on"] or not b["items"]:
        return ""
    recent = text_override if text_override is not None else " \n ".join(t.get("text", "") for t in (story.get("turns") or [])[-NOTE_TURNS:])
    rows, size = [], 0
    for c in story.get("cast") or []:
        sh = b["items"].get(c.get("slug"))
        if c.get("player") or not has_content(sh) or not any(_hit(n, recent) for n in _names(c)):
            continue
        ln = line(c["name"], sh)
        if len(rows) >= NOTE_MAX_SHEETS or (size + len(ln) > NOTE_MAX_CHARS and rows):
            break
        rows.append(ln)
        size += len(ln)
    return ("[CHARACTER SHEETS: the current stats of characters in this scene. They are true; stay consistent with them and do not recite them.]\n" + "\n".join(rows)) if rows else ""


def parse(text: str) -> list[dict]:
    """The hidden <sheet>Name | stats | note</sheet> lines of a raw reply: [{name, stats, notes}]."""
    out = []
    for block in SHEET_RE.findall(text or ""):
        parts = [p.strip() for p in block.split("|")]
        if not parts or not parts[0]:
            continue
        stats = clean_stats(parts[1]) if len(parts) > 1 else []
        notes = _clean_text(" | ".join(parts[2:]), MAX_NOTE) if len(parts) > 2 else ""
        out.append({"name": parts[0], "stats": stats, "notes": notes})
    return out[:12]


def strip(text: str) -> str:
    return SHEET_RE.sub("", text or "")


def apply(story: dict, ops: list[dict], turn: int | None = None) -> list[str]:
    """Merge the narrator's changes into the sheets of known non-player cast members (changed stats are replaced, new ones added, the rest kept). Names of the characters changed."""
    b = get(story)
    if not b["on"] or not b["auto"]:
        return []
    changed = []
    for op in ops:
        ch = find_char(story, op.get("name", ""))
        if not ch or ch.get("player") or not (op.get("stats") or op.get("notes")):
            continue
        sh = sheet_of(story, ch["slug"])
        cur = {s["name"].lower(): s for s in sh["stats"]}
        for s in op.get("stats") or []:
            if s["name"].lower() in cur:
                cur[s["name"].lower()]["value"] = s["value"]
            elif len(sh["stats"]) < MAX_STATS:
                sh["stats"].append(dict(s))
                cur[s["name"].lower()] = sh["stats"][-1]
        if op.get("notes"):
            sh["notes"] = op["notes"]
        if turn is not None:
            sh["turn"] = turn
        changed.append(ch["name"])
    return changed


def clean_sheet(raw: dict, old: dict | None = None) -> dict:
    old = old or {}
    return {"stats": clean_stats(raw.get("stats", old.get("stats"))), "notes": str(raw.get("notes", old.get("notes", "")) or "").strip()[:MAX_NOTE],
            "image": str(old.get("image") or ""), "turn": old.get("turn", 0)}
