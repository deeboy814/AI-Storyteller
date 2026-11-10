"""Lore entries: facts about the world (a place, a faction, a rule, a character's secret history) that the player or the story's author keeps in the app and that are sent to
the narrator only when they matter: when one of an entry's trigger words appears in the player's move or the last couple of turns, or always for entries marked so.
No AI is involved; matching is whole words, case-insensitive. Stored as story["lore"] = {"on": True, "items": [...]}."""
import re
import uuid

MAX_ITEMS = 200
MAX_NOTE_CHARS = 3500       # what one move may carry
MAX_NOTE_ENTRIES = 8
SCAN_TURNS = 3              # the player's move plus the two turns before it


def default() -> dict:
    return {"on": True, "items": []}


def get(story: dict) -> dict:
    b = story.setdefault("lore", default())
    b.setdefault("on", True)
    b.setdefault("items", [])
    return b


def clean_keys(raw) -> list[str]:
    if isinstance(raw, str):
        raw = re.split(r"[,\n;]", raw)
    out = []
    for k in raw or []:
        k = " ".join(str(k).split()).strip(" \"'")[:40]
        if k and k.lower() not in {x.lower() for x in out}:
            out.append(k)
    return out[:12]


def clean_item(raw: dict, old: dict | None = None) -> dict:
    old = old or {}
    pri = raw.get("priority", old.get("priority", 5))
    try:
        pri = max(0, min(int(pri), 9))
    except (TypeError, ValueError):
        pri = 5
    return {"id": old.get("id") or raw.get("id") or uuid.uuid4().hex[:8], "title": " ".join(str(raw.get("title", old.get("title", ""))).split())[:90],
            "keys": clean_keys(raw.get("keys", old.get("keys", []))), "text": str(raw.get("text", old.get("text", "")) or "").strip()[:2000],
            "always": bool(raw.get("always", old.get("always", False))), "on": bool(raw.get("on", old.get("on", True))), "priority": pri}


def clean_list(raw) -> list[dict]:
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        it = clean_item(r)
        if (it["title"] or it["text"]) and it["id"] not in seen:
            it["title"] = it["title"] or it["text"][:60]
            seen.add(it["id"]); out.append(it)
    return out[:MAX_ITEMS]


def _hit(key: str, text: str) -> int:
    """Position of the first whole-word match of key in text (or -1)."""
    m = re.search(r"(?<!\w)" + re.escape(key) + r"(?!\w)", text, re.IGNORECASE)
    return m.start() if m else -1


def triggered(story: dict, text_override: str | None = None) -> list[dict]:
    """The entries that apply now, most important first: always-on ones, then those whose trigger words appear in the recent turns."""
    b = get(story)
    if not b["on"]:
        return []
    recent = text_override if text_override is not None else " \n ".join(t.get("text", "") for t in (story.get("turns") or [])[-SCAN_TURNS:])
    rows = []
    for it in b["items"]:
        if not it.get("on", True) or not it.get("text"):
            continue
        if it.get("always"):
            rows.append((0, -it.get("priority", 5), 0, it))
            continue
        hits = [h for h in (_hit(k, recent) for k in it.get("keys", [])) if h >= 0]
        if hits:
            rows.append((1, -it.get("priority", 5), -len(hits), it))
    rows.sort(key=lambda r: r[:3])
    return [r[3] for r in rows]


def note(story: dict, text_override: str | None = None) -> str:
    """The hidden note added to the player's move."""
    out, size = [], 0
    for it in triggered(story, text_override):
        line = f"- {it['title']}: {it['text']}" if it["title"] and not it["text"].lower().startswith(it["title"].lower()) else f"- {it['text']}"
        if len(out) >= MAX_NOTE_ENTRIES or (size + len(line) > MAX_NOTE_CHARS and out):
            break
        out.append(line); size += len(line)
    return ("[LORE: world facts from the story's own notes that matter right now. They are true; stay consistent with them and do not recite them.]\n" + "\n".join(out)) if out else ""
