"""Story beats: the key events a story schedules (read from its prompt, or added by the player), kept by the app and sent to the narrator at the right time.

A beat has a trigger tied to the side panel ("Day reaches 7"), to the number of moves played ("move 12"), or "on my next move". When a beat becomes due it is added,
invisibly, to the player's move for that request only (never to the story text), and it keeps being sent for a few moves (`hold`) so the narrator does not forget it.
The next beats are also named as "coming up (do not reveal)", so the narrator steers toward them instead of drifting off the plot.
State lives in story["beats"]: {"on", "hold", "lookahead", "extracted", "items": [...]}; a beat's `fired` is the move number it first went out on."""
import re
import uuid

from . import tracker

OPS = (">=", "=")
SPECIAL = ("turn", "next", "")       # trigger fields besides side-panel fields: move number, next move, none yet
MAX_ITEMS = 60
MAX_NOW = 2                          # at most this many new beats go out in one move (a jump in the calendar does not flood the narrator)


def default() -> dict:
    return {"on": True, "hold": 3, "lookahead": True, "extracted": False, "items": []}


def get(story: dict) -> dict:
    """The story's beats settings, filled in with defaults (older stories have none)."""
    b = story.setdefault("beats", default())
    for k, v in default().items():
        b.setdefault(k, v)
    return b


def _clip(v, n) -> str:
    return " ".join(str(v or "").split())[:n] if n < 200 else str(v or "").strip()[:n]


def clean_item(raw: dict, old: dict | None = None) -> dict:
    """One beat from untrusted input (the AI's list, the player's form, a package)."""
    old = old or {}
    field = str(raw.get("field", old.get("field", "")) or "").strip()
    if field not in SPECIAL and not re.match(r"^(scene|me)\.[a-z][a-z0-9_]{0,23}$", field):
        field = ""
    op = raw.get("op", old.get("op", ">=")) if raw.get("op", old.get("op", ">=")) in OPS else ">="
    value = raw.get("value", old.get("value", ""))
    value = "" if value is None else str(value).strip()[:60]
    it = {"id": old.get("id") or raw.get("id") or uuid.uuid4().hex[:8], "title": _clip(raw.get("title", old.get("title", "")), 90),
          "text": _clip(raw.get("text", old.get("text", "")), 1500), "field": field, "op": op, "value": value,
          "source": old.get("source") or ("player" if raw.get("source") == "player" else "prompt"),
          "off": bool(raw.get("off", old.get("off", False))), "fired": old.get("fired"), "why": _clip(raw.get("why", old.get("why", "")), 80),
          "edited": bool(raw.get("edited", old.get("edited", False)))}
    return it


def moves(story: dict, upto: int | None = None) -> int:
    """How many moves the player has made (counting the one at index `upto` when given)."""
    turns = story.get("turns", [])
    return sum(1 for t in (turns if upto is None else turns[:upto + 1]) if t.get("role") == "user")


def number(v):
    m = re.search(r"-?\d+(?:\.\d+)?", str(v if v is not None else ""))
    return float(m.group(0)) if m else None


def panel_value(story: dict, field: str):
    """What the side panel currently shows for a scene or "you" field (None when it shows nothing)."""
    if "." not in field:
        return None
    sec, k = field.split(".", 1)
    return ((story.get("tracker") or tracker.blank()).get(sec) or {}).get(k)


def is_due(story: dict, it: dict, move_no: int) -> bool:
    f = it.get("field", "")
    if f == "next":
        return True
    if f == "turn":
        n = number(it.get("value"))
        return n is not None and move_no >= n
    if not f:
        return False
    cur = panel_value(story, f)
    if cur is None:
        return False
    if it.get("op") == "=":
        want = str(it.get("value", "")).strip().lower()
        return bool(want) and want in str(cur).lower()
    a, b = number(cur), number(it.get("value"))
    return a is not None and b is not None and a >= b


def order_key(story: dict, it: dict):
    f = it.get("field", "")
    n = number(it.get("value")) if f not in ("next", "") else None
    return (0 if f == "next" else 1 if n is not None else 2, n if n is not None else 0)


def status(it: dict, next_move: int, hold: int) -> str:
    """waiting | active | done | off | notrigger"""
    if it.get("off"):
        return "off"
    fired = it.get("fired")
    if fired is not None and fired < next_move:         # a beat marked on a move that was since undone counts as not sent
        return "active" if next_move - fired < hold else "done"
    return "notrigger" if not it.get("field") else "waiting"


def label_of(story: dict, it: dict) -> str:
    """The trigger in words, for the list and for the narrator."""
    f, v = it.get("field", ""), it.get("value", "")
    if f == "next":
        return "on the next move"
    if f == "turn":
        return f"at move {v}"
    if not f:
        return "no trigger yet"
    name = next((x["label"] for x in tracker.active_fields(story) if x["id"] == f), f.split(".")[-1].title())
    return f"when {name} reaches {v}" if it.get("op") != "=" else f"when {name} is {v}"


def prepare(story: dict, move_no: int) -> str:
    """Called when the player sends move number `move_no`: forget beats that fired on a move that was undone or is being redone, send the due ones now,
    keep the recent ones going, and name what is coming up. Changes story["beats"] (the caller saves it). Returns the hidden note ("" when there is nothing)."""
    b = get(story)
    if not b["on"]:
        return ""
    items = b["items"]
    for it in items:
        if it.get("fired") is not None and it["fired"] >= move_no:
            it["fired"] = None
    hold = max(1, min(int(b.get("hold", 3)), 12))
    live = [it for it in items if not it.get("off")]
    ongoing = [it for it in live if it.get("fired") is not None and move_no - it["fired"] < hold]
    due = sorted((it for it in live if it.get("fired") is None and it.get("text") and is_due(story, it, move_no)), key=lambda it: order_key(story, it))[:MAX_NOW]
    for it in due:
        it["fired"] = move_no
    soon = []
    if b.get("lookahead"):
        soon = sorted((it for it in live if it.get("fired") is None and it not in due and it.get("field") not in ("", "next") and it.get("title")), key=lambda it: order_key(story, it))[:2]
    if not (due or ongoing or soon):
        return ""
    out = ["[STORY BEATS: hidden from the player; this is the story's own schedule. Work what is due into the story in your own words, at a fitting moment in this reply "
           "or the next, consistent with what the player does and with the side panel. Do not announce it as scheduled. If the player's actions made it impossible, "
           "adapt it sensibly instead of dropping the story's direction.]"]
    for it in due:
        out.append(f"DUE NOW ({label_of(story, it)}): {it['title']}. {it['text']}".replace(" .", "."))
    for it in ongoing:
        out.append(f"IN PROGRESS (started {move_no - it['fired']} move{'s' if move_no - it['fired'] != 1 else ''} ago; keep consistent, carry on or resolve it): {it['title']}. {it['text']}")
    for it in soon:
        out.append(f"COMING UP, not yet ({label_of(story, it)}): {it['title']}. Do NOT make it happen or reveal it yet; let events lead toward it without forcing it.")
    return "\n".join(out)


def prompt_context(story: dict) -> str:
    """The beats as plain lines, for the continuity check (so it can flag a story that has drifted from its plan)."""
    b = get(story)
    lines = [f"- {it['title']} ({label_of(story, it)}): {it['text']}" for it in sorted(b["items"], key=lambda i: order_key(story, i)) if it.get("title") and not it.get("off")]
    return "\n".join(lines)


def view(story: dict) -> dict:
    """Everything the dialog needs."""
    b = get(story)
    nm = moves(story) + 1
    fields = [{"id": f["id"], "label": f["label"], "kind": f["kind"], "now": panel_value(story, f["id"])} for f in tracker.active_fields(story)
              if f["id"].split(".")[0] in ("scene", "me") and f["kind"] in ("text", "number", "slider", "status")]
    items = [{**it, "status": status(it, nm, b["hold"]), "when": label_of(story, it)} for it in sorted(b["items"], key=lambda i: order_key(story, i))]
    return {"on": b["on"], "hold": b["hold"], "lookahead": b["lookahead"], "extracted": b["extracted"], "error": b.get("error", ""), "items": items, "fields": fields, "next_move": nm}


EXTRACT_SYSTEM = (
    "You read an interactive story's prompt and list the key events it schedules: dated or ordered story beats that the narrator should make happen at the right time "
    "(for example a day-by-day or week-by-week calendar, an exam, an attack, a reveal 'after the first mission'). Include only events the prompt itself states or clearly implies; "
    "never invent events. Skip rules, world facts, character descriptions and ongoing situations that are not events. "
    "For each event give: 'title' (at most 8 words), 'text' (one to three sentences telling the narrator what happens and what it changes, spoilers included, in the prompt's own terms), "
    "and when it is due: 'field' = the id of one of the TRACKER FIELDS listed when the event is tied to what that field shows (for example a Day or Week counter) with 'value' = the number "
    "where the event begins (the first day of a range); or 'field' = \"turn\" with 'value' = a player-move number only when the prompt gives a number of moves; "
    "or 'field' = \"\" when the prompt gives no usable schedule (the player will decide). "
    'Reply with ONLY JSON: {"events": [{"title": "...", "text": "...", "field": "scene.day", "value": 7}]} in story order, at most 40 events; {"events": []} when there are none.')


def extract_user(story: dict) -> str:
    flds = "\n".join(f"- {f['id']} | {f['label']} | {f['kind']}" + (f" | {f['note']}" if f.get("note") else "") for f in tracker.active_fields(story)
                     if f["id"].split(".")[0] in ("scene", "me") and f["kind"] in ("text", "number", "slider", "status")) or "(none)"
    return f"TRACKER FIELDS:\n{flds}\n\nSTORY PROMPT:\n{story.get('base_prompt', '')[:40000]}"


def merge(story: dict, events: list) -> tuple[int, int]:
    """Put the AI's list into the story's beats. A beat already there (same title) that the player has not edited is updated; edited and player-made ones are never touched;
    beats the story has already passed (when read mid-story) start switched off. Returns (added, updated)."""
    b = get(story)
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()
    have = {norm(it["title"]): it for it in b["items"]}
    added = updated = 0
    nm = moves(story) + 1
    for raw in events:
        if not isinstance(raw, dict):
            continue
        new = clean_item({**raw, "source": "prompt"})
        if not new["title"] or not new["text"]:
            continue
        cur = have.get(norm(new["title"]))
        if cur:
            if cur["source"] == "prompt" and not cur.get("edited"):
                for k in ("text", "field", "op", "value"):
                    cur[k] = new[k]
                updated += 1
            continue
        if len(b["items"]) >= MAX_ITEMS:
            break
        if moves(story) and new["field"] not in ("", "next") and is_due(story, new, nm):
            if new["field"] != "turn" and (number(panel_value(story, new["field"])) or 0) > (number(new["value"]) or 0) or new["field"] == "turn" and nm > (number(new["value"]) or 0):
                new["off"], new["why"] = True, "already passed when read"
        b["items"].append(new)
        have[norm(new["title"])] = new
        added += 1
    b["extracted"] = True
    b.pop("error", None)
    return added, updated
