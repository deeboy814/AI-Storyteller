"""The side panel's data: what the story's narrative has established about the scene, the player and each character.

Three ways to decide what is tracked (set per story in Settings):
  prompt - whatever the story prompt's own status panel (```Info block) or state block provides;
  player - the fields the player ticked (read from the panel when there is one, otherwise extracted by the AI);
  ai     - the AI decides the fields from the story prompt, and extracts them each turn.
Values only change when a reply causes it; every turn's changes (before -> after) are kept so the panel can show them.
The visible data is also given to the narrator before each reply, so it stays consistent."""
import copy
import json
import re

# Every field the app knows how to show. id = section.key. kind: text | number | status | slider
# In "player" mode the player can also type their own trackers (sidebar["custom"]): a name, where it lives (scene / you / each character),
# a type (text, number, slider, status) and optionally a note saying what it means. The AI reads each reply and keeps them up to date.
DEFAULT_FIELDS = [
    {"id": "scene.day", "label": "Day", "kind": "text"},
    {"id": "scene.time", "label": "Time", "kind": "text"},
    {"id": "scene.place", "label": "Place", "kind": "text"},
    {"id": "scene.situation", "label": "Situation", "kind": "text"},
    {"id": "me.status", "label": "Your status", "kind": "status"},
    {"id": "me.alignment", "label": "Alignment", "kind": "text"},
    {"id": "me.bonds", "label": "Your bonds", "kind": "text"},
    {"id": "me.stats", "label": "Stats (food, items…)", "kind": "text"},
    {"id": "chars.status", "label": "Status", "kind": "status"},
    {"id": "chars.affection", "label": "Affection", "kind": "slider", "min": -50, "max": 200},
    {"id": "chars.bonds", "label": "Bonds", "kind": "text"},
    {"id": "chars.location", "label": "Location", "kind": "text"},
    {"id": "chars.thought", "label": "Thought", "kind": "text"},
]
SECTIONS = ("scene", "me", "chars")
FIELD_ID = re.compile(r"^(scene|me|chars)\.[a-z][a-z0-9_]{0,23}$")


def blank() -> dict:
    return {"scene": {}, "me": {}, "chars": {}, "changes": [], "turn": 0}


def default_sidebar() -> dict:
    return {"mode": "prompt", "fields": [f["id"] for f in DEFAULT_FIELDS], "schema": None, "custom": []}


def effective_mode(sb: dict) -> str:
    """A story that comes with its own trackers (an author's package) shows and updates them even if the mode says 'leave it to the prompt':
    otherwise choosing that option would silently switch the author's trackers off."""
    mode = (sb or {}).get("mode", "prompt")
    return "player" if mode == "prompt" and (sb or {}).get("custom") else mode


def active_fields(story: dict) -> list[dict]:
    sb = story.get("sidebar") or default_sidebar()
    if sb["mode"] == "ai" and sb.get("schema"):
        return sb["schema"]
    if effective_mode(sb) == "player":
        return [f for f in DEFAULT_FIELDS if f["id"] in (sb.get("fields") or [])] + list(sb.get("custom") or [])
    return DEFAULT_FIELDS


def custom_fields(story: dict) -> list[dict]:
    sb = story.get("sidebar") or {}
    return list(sb.get("custom") or []) if effective_mode(sb) == "player" else []


# ---------------- the player's own trackers ----------------
KINDS = ("text", "number", "slider", "status", "quests")
DONE_STATES = ("completed", "failed")
SCOPES = ("scene", "me", "chars")


def _num(v):
    """A number from a value or from text like '40%', '12 / 100', '-3'. None if there isn't one."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = float(v)
    else:
        m = re.search(r"-?\d+(?:[.,]\d+)?", str(v))
        if not m:
            return None
        n = float(m.group(0).replace(",", "."))
    return int(n) if n == int(n) else round(n, 2)


def clean_quests(v):
    """A quest log: [{name, objectives: [{text, done}], reward, status}]. Accepts the loose forms an AI may write (a ' | ' string, plain strings, a single reward list). None if it isn't a list of quests."""
    if isinstance(v, str):
        v = [{"name": x.strip()} for x in v.split(" | ") if x.strip()] if v.strip() else []
    if not isinstance(v, list):
        return None
    out = []
    for q in v:
        if isinstance(q, str):
            q = {"name": q}
        if not isinstance(q, dict) or not str(q.get("name") or q.get("title") or "").strip():
            continue
        objs = []
        for o in q.get("objectives") if isinstance(q.get("objectives"), list) else ([q["objective"]] if q.get("objective") else []):
            if isinstance(o, str):
                o = {"text": o}
            if isinstance(o, dict) and str(o.get("text") or "").strip():
                objs.append({"text": str(o["text"]).strip()[:140], "done": bool(o.get("done"))})
        rw = q.get("reward") or q.get("rewards") or ""
        if isinstance(rw, list):
            rw = ", ".join(str(x) for x in rw)
        st = str(q.get("status") or "active").strip().lower()
        st = "completed" if st.startswith(("complet", "done", "finish")) else "failed" if st.startswith("fail") else "active"
        out.append({"name": str(q.get("name") or q.get("title")).strip()[:60], "objectives": objs[:8], "reward": str(rw).strip()[:160], "status": st})
    return out[:12]


def coerce(field: dict, v):
    """The value in the form its field needs, or None when it doesn't fit (the caller can then ask the AI to fix it)."""
    kind = field.get("kind", "text")
    if kind == "quests":
        return clean_quests(v)
    if kind in ("number", "slider"):
        n = _num(v)
        if n is None:
            return None
        if kind == "slider":
            n = max(field.get("min", 0), min(field.get("max", 100), n))
        return n
    if not isinstance(v, (str, int, float)) or isinstance(v, bool):
        return None
    s = str(v).strip()[:400]
    if kind == "status" and field.get("options"):
        low = {o.lower(): o for o in field["options"]}
        if s.lower() in low:
            return low[s.lower()]
        hit = next((o for k, o in low.items() if k in s.lower() or s.lower() in k), None)
        return hit
    return s


def clean_custom(raw, old: list[dict] | None = None) -> list[dict]:
    """The player's typed trackers, made safe: unique ids (section.slug), a known type, sane limits. Existing ids are kept when the label is unchanged."""
    out, seen = [], {d["id"] for d in DEFAULT_FIELDS}
    keep = {(f["id"]): f for f in (old or [])}
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict):
            continue
        label = re.sub(r"\s+", " ", str(f.get("label") or "")).strip()[:30]
        scope = f.get("scope") if f.get("scope") in SCOPES else (str(f.get("id", "")).split(".")[0] if str(f.get("id", "")).split(".")[0] in SCOPES else "me")
        if not label:
            continue
        kind = f.get("kind") if f.get("kind") in KINDS else "text"
        if kind == "quests":
            scope = "me"
        fid = f.get("id") if isinstance(f.get("id"), str) and f["id"] in keep and keep[f["id"]]["label"] == label and f["id"].startswith(scope + ".") else None
        if not fid:
            slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:20] or "tracker"
            if not slug[0].isalpha():
                slug = "t_" + slug
            fid, n = f"{scope}.{slug}", 2
            while fid in seen:
                fid, n = f"{scope}.{slug[:20]}_{n}", n + 1
        if fid in seen or not FIELD_ID.match(fid):
            continue
        d = {"id": fid, "label": label, "kind": kind, "custom": True}
        if f.get("note"):
            d["note"] = str(f["note"]).strip()[:200]
        if kind == "slider":
            lo, hi = _num(f.get("min")), _num(f.get("max"))
            lo, hi = (0 if lo is None else lo), (100 if hi is None else hi)
            if hi <= lo:
                lo, hi = 0, 100
            d["min"], d["max"] = lo, hi
        if kind == "status":
            opts = f.get("options")
            if isinstance(opts, str):
                opts = [o for o in re.split(r"[,/|]", opts)]
            d["options"] = [str(o).strip()[:20] for o in (opts or []) if str(o).strip()][:8]
            if not d["options"]:
                d.pop("options")
        if kind == "number":
            d["unit"] = str(f.get("unit") or "").strip()[:12]
        seen.add(fid)
        out.append(d)
    return out[:16]


# ---------------- reading the narrator's own status panel ----------------
def _status_of(s: str) -> str:
    if re.search(r"🪦|dead", s, re.I):
        return "dead"
    if "🧟" in s or re.search(r"zombie", s, re.I):
        return "zombie"
    if "🧬" in s or re.search(r"immune", s, re.I):
        return "immune"
    return "normal"


def parse_panel(text: str) -> dict | None:
    """{"scene", "me", "chars"} from a ```Info panel with a Characters── section, or None if the reply has no such panel."""
    m = re.search(r"```info\b(.*?)```", text, re.I | re.S)
    if not m:
        return None
    lines = [l.strip() for l in m.group(1).split("\n") if l.strip()]
    ci = next((i for i, l in enumerate(lines) if l.startswith("Characters──")), -1)
    if ci < 0:
        return None
    head, out = lines[:ci], {"scene": {}, "me": {}, "chars": {}}
    sc = (head[1] if len(head) > 1 else "").split("|")
    if len(sc) >= 3:
        out["scene"].update(day=sc[0].strip(), time=sc[1].strip(), place=sc[2].replace('"', "").strip())
    for l in head:
        if l.startswith("Situation:"):
            out["scene"]["situation"] = l[len("Situation:"):].strip()
    pi = next((i for i, l in enumerate(head) if l.endswith("──")), -1)
    if pi >= 0:
        out["me"]["name"] = head[pi][:-2]
    for l in head:
        x = re.match(r'^Alignment:(.*?)\|"([^"]*)"', l)
        if x:
            al = re.sub(r"\[(\d+)(?:\([^)]*\))?\]", r" \1", x.group(1)).replace("/", " · ").strip()
            out["me"]["alignment"] = f"{al} ({x.group(2)})" if x.group(2) else al
        x = re.search(r"Status:([^|]*)", l)
        if x:
            out["me"]["status"] = _status_of(x.group(1))
        x = re.match(r"^Bonds:\s*(.*)$", l)
        if x:
            out["me"]["bonds"] = re.sub(r"\[(\d+)(?:\(([^)]*)\))?\]", lambda k: f" {k.group(1)}" + (f" ({k.group(2).lower()})" if k.group(2) else ""), x.group(1)).replace("/", " · ").replace("  ", " ").strip()
        x = re.search(r"Food:(-?\d+)", l)
        if x:
            out["me"]["stats"] = f"Food {x.group(1)}"
    for l in lines[ci + 1:]:
        if l == "──":
            break
        r = re.match(r"^([A-Z][^:/|(]{0,28}?):(.*)$", l)
        if not r:
            continue
        heart = re.search(r"❤️?\s*(-?\d+)", r.group(2))
        if not heart:
            continue
        parts = r.group(2).split("/")
        kiss = re.search(r"💋\s*:?\s*(\d+)(?:\(([^)]*)\))?", r.group(2))
        c = {"status": _status_of(parts[0]), "affection": int(heart.group(1)), "location": (parts[2].strip() if len(parts) > 2 else "")}
        q = next((p.strip().strip('"') for p in parts if p.strip().startswith('"')), "")
        if q:
            c["thought"] = q
        if kiss and (int(kiss.group(1)) or kiss.group(2)):
            c["bonds"] = f"💋 {kiss.group(1)}" + (f" · {kiss.group(2)}" if kiss.group(2) else "")
        out["chars"][r.group(1).strip()] = c
    return out


def from_state(story: dict) -> dict:
    st = story.get("state") or {}
    out = {"scene": {}, "me": {}, "chars": {}}
    if st.get("when"):
        out["scene"]["time"] = st["when"]
    if st.get("location"):
        out["scene"]["place"] = st["location"]
    bits = [st["sheet"]] if st.get("sheet") else []
    if st.get("hp"):
        bits.append(f"HP {st['hp'][0]}/{st['hp'][1]}")
    if st.get("inventory"):
        bits.append("Items: " + ", ".join(st["inventory"]))
    if bits:
        out["me"]["stats"] = " · ".join(bits)
    for n, v in (st.get("affinity") or {}).items():
        out["chars"][n] = {"affection": v}
    return out


# ---------------- applying updates, remembering what changed ----------------
def _has_art(story: dict, name: str) -> bool:
    c = next((x for x in story.get("cast") or [] if x["name"].lower() == name.lower()), None)
    return bool(c and c.get("moods"))


def resolve_alias(story: dict, name: str) -> str:
    """A generic label the narrator or tracker used for someone ("Shop Girl") becomes their real name once that character has a name AND pictures."""
    real = (story.get("char_alias") or {}).get(name)
    return real if real and _has_art(story, real) else name


def apply_aliases(story: dict) -> bool:
    """Move tracker rows filed under a generic label to the named character's row (their own values win). True if anything moved."""
    tr, moved = story.get("tracker") or {}, False
    for label, real in list((story.get("char_alias") or {}).items()):
        chars = tr.get("chars") or {}
        if label == real or label not in chars or not _has_art(story, real):
            continue
        row = chars.pop(label)
        dest = chars.setdefault(real, {})
        for k, v in row.items():
            dest.setdefault(k, v)
        for c in tr.get("changes") or []:
            if c.get("n") == label:
                c["n"] = real
        moved = True
    return moved


def _apply(story: dict, new: dict, turn: int) -> list[dict]:
    tr = story.setdefault("tracker", blank())
    changes = []
    for sec in ("scene", "me"):
        for k, v in (new.get(sec) or {}).items():
            if v in (None, "") or tr[sec].get(k) == v:
                continue
            changes.append({"s": sec, "n": None, "k": k, "b": tr[sec].get(k), "a": v})
            tr[sec][k] = v
    for name, fields in (new.get("chars") or {}).items():
        if not isinstance(fields, dict):
            continue
        name = resolve_alias(story, name)
        cur = tr["chars"].get(name)
        if cur is None:
            tr["chars"][name] = cur = {}
            changes.append({"s": "chars", "n": name, "k": "*", "b": None, "a": "new character"})
        for k, v in fields.items():
            if v in (None, "") or cur.get(k) == v:
                continue
            changes.append({"s": "chars", "n": name, "k": k, "b": cur.get(k), "a": v})
            cur[k] = v
    tr["changes"], tr["turn"] = changes, turn
    return changes


def update_from_reply(story: dict, reply: str, turn: int) -> bool:
    """Read the reply's own status panel (or the state block). Returns True if something was read."""
    sb = story.get("sidebar") or default_sidebar()
    if sb["mode"] == "ai":      # the AI alone keeps this panel up to date (mixing in the narrator's own panel would make values flicker)
        story.setdefault("tracker", blank())["changes"] = []
        return False
    parsed = parse_panel(reply)
    if parsed is None and sb["mode"] == "prompt" and story.get("use_state_block", True) and story.get("state"):
        parsed = from_state(story)
    if parsed is None:
        story.setdefault("tracker", blank())["changes"] = []
        return False
    keep = {f["id"] for f in active_fields(story)}
    parsed = {"scene": {k: v for k, v in (parsed.get("scene") or {}).items() if f"scene.{k}" in keep},
              "me": {k: v for k, v in (parsed.get("me") or {}).items() if f"me.{k}" in keep or k == "name"},
              "chars": {n: {k: v for k, v in f.items() if f"chars.{k}" in keep} for n, f in (parsed.get("chars") or {}).items()}}
    _apply(story, parsed, turn)
    return True


def rebuild(story: dict) -> None:
    """Start over and replay every narrator panel in order (so the last turn's changes are measured against the one before)."""
    old = story.get("tracker") or blank()
    story["tracker"] = blank()
    n = 0
    for i, t in enumerate(story.get("turns", [])):
        if t["role"] == "assistant":
            n += 1
            update_from_reply(story, t["text"], i // 2 + 1)
    for f in custom_fields(story):      # values the AI keeps for the player's own trackers can't be re-read from the narrator's panel: carry them over
        sec, k = f["id"].split(".")
        if sec == "chars":
            for name, flds in (old.get("chars") or {}).items():
                if k in flds:
                    story["tracker"]["chars"].setdefault(name, {})[k] = flds[k]
        elif k in (old.get(sec) or {}):
            story["tracker"][sec][k] = old[sec][k]
    story["tracker"]["turn"] = old.get("turn", story["tracker"]["turn"])
    for k in ("qprune", "qdone"):
        if k in old:
            story["tracker"][k] = old[k]


def _remember_done(story: dict, names) -> None:
    done = story.setdefault("tracker", blank()).setdefault("qdone", [])
    for n in names:
        if n and not any(d.lower() == str(n).lower() for d in done):
            done.append(str(n))
    del done[:-60]


def _same_quest(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a and b and (a == b or a.startswith(b) or b.startswith(a)))


def apply_patch(story: dict, patch: dict, turn: int, bad: list | None = None) -> list[dict]:
    """Apply an AI-written patch. Values are converted to what each field needs; values that don't fit are left out and, when `bad` is a list, reported there as (field id, name or None, value)."""
    fields = {f["id"]: f for f in active_fields(story)}
    clean = {"scene": {}, "me": {}, "chars": {}}
    tr0 = story.get("tracker") or blank()
    gone = {}           # quest logs: quests finished or failed last turn have been shown for a turn; clear them now
    if tr0.get("qprune", 0) < turn:          # once per turn (the repair call and the narrator's own panel read share the turn number)
        if any(f["kind"] == "quests" for f in fields.values()):
            story.setdefault("tracker", blank())["qprune"] = turn
        for fid, f in fields.items():
            if f["kind"] == "quests":
                sec, k = fid.split(".")
                cur = tr0.get(sec, {}).get(k) or []
                gone[k] = {q["name"].lower() for q in cur if q.get("status") in DONE_STATES}
                _remember_done(story, gone[k])
                if gone[k]:
                    clean[sec][k] = [q for q in cur if q.get("status") not in DONE_STATES]

    def take(sec, name, k, v, dest):
        f = fields.get(f"{sec}.{k}")
        if not f or v in (None, ""):
            return
        got = coerce(f, v)
        if got is None:
            if bad is not None:
                bad.append((f["id"], name, v))
            return
        if f["kind"] == "quests":
            _remember_done(story, [q["name"] for q in got if q.get("status") in DONE_STATES])
            finished = (story.get("tracker") or {}).get("qdone") or []     # a quest that is over does not come back as active (the AI re-reading the story may list it again)
            got = [q for q in got if q["name"].lower() not in gone.get(k, set())
                   and not (q.get("status") not in DONE_STATES and any(_same_quest(q["name"], d) for d in finished))]
            old = [q for q in (tr0.get(sec, {}).get(k) or []) if q.get("reward")]
            for q in got:    # a reward that was announced stays, even if a later reading leaves it out or writes "unknown"
                if q["reward"].strip().lower() in ("", "unknown", "none", "n/a", "?", "tbd"):
                    n = q["name"].lower()
                    hit = next((o for o in old if o["name"].lower() == n or o["name"].lower().startswith(n) or n.startswith(o["name"].lower())), None)
                    q["reward"] = hit["reward"] if hit else ""
        dest[k] = got
    for sec in ("scene", "me"):
        for k, v in (patch.get(sec) or {}).items():
            take(sec, None, k, v, clean[sec])
    for name, flds in (patch.get("chars") or {}).items():
        if isinstance(flds, dict) and isinstance(name, str) and name.strip():
            d = clean["chars"].setdefault(name.strip(), {})
            for k, v in flds.items():
                take("chars", name.strip(), k, v, d)
    return _apply(story, clean, turn)


# ---------------- what the narrator sees, what the AI is asked ----------------
def visible(story: dict) -> dict:
    tr = story.get("tracker") or blank()
    keep = {f["id"] for f in active_fields(story)}
    out = {"scene": {}, "me": {}, "chars": {}}
    for sec in ("scene", "me"):
        out[sec] = {k: v for k, v in tr[sec].items() if f"{sec}.{k}" in keep}
    for name, fields in tr["chars"].items():
        f = {k: v for k, v in fields.items() if f"chars.{k}" in keep}
        if f:
            out["chars"][name] = f
    return {k: v for k, v in out.items() if v}


def tail_text(story: dict) -> str:
    v = visible(story)
    if not v:
        return ""
    return ("[SIDE PANEL: the tracked state of the story as the player sees it. Keep it consistent: a value changes only when events in the story cause it, "
            "and only by the amount the story supports. A quest with status completed was finished last turn and its reward already given: do not give it again.]\n" + json.dumps(v, ensure_ascii=False))


def clear_custom_values(story: dict) -> None:
    """Forget the values of the story's own trackers (so they can be read again from the story)."""
    tr = story.setdefault("tracker", blank())
    for f in custom_fields(story):
        sec, k = f["id"].split(".")
        if sec == "chars":
            for flds in (tr.get("chars") or {}).values():
                flds.pop(k, None)
        else:
            (tr.get(sec) or {}).pop(k, None)
    tr["changes"] = []


def snapshot(story: dict) -> dict:
    return copy.deepcopy(story.get("tracker") or blank())


READ_RULES = """How to read the story:
- Facts come from what the narrator says HAPPENED, not from what the player asks, wonders about or proposes. The player's move is context only; a question about a reward is not a reward.
- A reward, price, bonus or amount that is only promised, offered, quoted, explained or asked about ("reward on completion", "you could earn", "costs 100 SP", shop lists) has NOT happened: never add it to a value and never mark a quest completed because of it.
- Prefer the narrator's structured status text (a [SYSTEM NOTIFICATION] or status block with "Total", "Current Status", levels and stats) over prose. When it states a total or current value, use exactly that number; do not add up amounts yourself.
- A number changes only when the narrator says it was gained, lost, spent or set. If the story does not state it, leave the field out. Number and slider fields take only numbers: words like "baseline" or "unchanged" are not numbers, so leave the field out.
- Quests: keep each quest's name, objectives and reward wording exactly as the narrator wrote them; do not rename quests or invent objectives. Tick an objective only when the narrator says it is done (a checked box, "completed"), never for partial progress (16/20 is not done). Set status completed only when the narrator announces the quest complete and grants its reward in that same reply. A reward that was announced earlier stays in the quest's reward text; if no reward has been stated yet, use an empty string."""

EXTRACT_SYSTEM = """You keep the side panel for an interactive story. You are given the tracked fields, the current panel, the player's move and the narrator's reply.
Reply with ONLY a JSON patch: {"scene": {key: value}, "me": {key: value}, "chars": {"Character Name": {key: value}}} containing ONLY the fields whose values actually CHANGED because of events in this reply, plus any newly appearing character with their starting values.
Rules: use only the tracked fields (the part of each id after the dot is the key); never repeat unchanged values; never invent anything the reply does not support; slider and number fields are plain numbers (no units or words); status fields use one of the listed options when options are given; keep values short. A field's "meaning" note says what it is for: follow it.
If nothing changed, reply {}.
""" + READ_RULES

BUILD_SYSTEM = """You build the side panel for an interactive story from what the story has established so far. Reply with ONLY JSON in the shape
{"scene": {key: value}, "me": {key: value}, "chars": {"Character Name": {key: value}}} giving the CURRENT value of every tracked field the story states or clearly implies (omit anything unknown; never invent).
Slider fields are numbers; status fields use one of the listed options when options are given; keep values short.
""" + READ_RULES

SCHEMA_SYSTEM = """You design the side panel for an interactive story, from its story prompt. Decide what is worth tracking in this particular story (at most 12 fields) so the player can follow it at a glance.
Reply with ONLY a JSON list of field definitions: [{"id": "chars.status", "label": "Status", "kind": "status", "options": ["..."]}, {"id": "chars.affection", "label": "Affection", "kind": "slider", "min": 0, "max": 100}, {"id": "me.stats", "label": "Stats", "kind": "text"}, ...]
Rules: id is section.key with section scene (the current scene: time, place), me (the player's character) or chars (one entry per named character), and key lowercase letters/digits/underscore. kind is text, status (a short label; give options) or slider (a number; give min and max).
Choose fields the story prompt itself cares about (its stats, relationships, conditions); include chars.status and a relationship slider only if they fit this story."""


def field_lines(fields: list[dict]) -> str:
    out = []
    for f in fields:
        s = f"- {f['id']} ({f['label']}, {f['kind']}"
        if f.get("options"):
            s += ", one of: " + "/".join(f["options"])
        if f["kind"] == "slider":
            s += f", a number from {f.get('min', 0)} to {f.get('max', 100)}"
        if f["kind"] == "number":
            s += ", a number" + (f" in {f['unit']}" if f.get("unit") else "")
        if f["kind"] == "quests":
            s += (", a list of quests: [{\"name\": str, \"objectives\": [{\"text\": str, \"done\": bool}], \"reward\": str, \"status\": \"active\" | \"completed\" | \"failed\"}]. "
                  "Always write the FULL current list. A quest the narrator finishes AND rewards in this reply stays in the list with status completed; "
                  "do not remove it yourself, the app clears it next turn. Add new quests when the story gives them; tick objectives when done")
        if f.get("note"):
            s += f"; meaning: {f['note']}"
        out.append(s + ")")
    return "\n".join(out)


def clean_schema(raw) -> list[dict]:
    out, seen = [], set()
    for f in raw if isinstance(raw, list) else []:
        if not isinstance(f, dict) or not FIELD_ID.match(str(f.get("id", ""))) or f["id"] in seen:
            continue
        kind = f.get("kind") if f.get("kind") in KINDS else "text"
        d = {"id": f["id"], "label": str(f.get("label") or f["id"].split(".")[1]).strip()[:30], "kind": kind}
        if kind == "status" and isinstance(f.get("options"), list):
            d["options"] = [str(o)[:20] for o in f["options"]][:8]
        if kind == "slider":
            try:
                d["min"], d["max"] = float(f.get("min", 0)), float(f.get("max", 100))
                d["min"], d["max"] = int(d["min"]), int(d["max"])
            except (TypeError, ValueError):
                d["min"], d["max"] = 0, 100
        seen.add(f["id"])
        out.append(d)
    return out[:12]


ASSIST_SYSTEM = """A player is adding a custom tracker to the side panel of an interactive story. From its name (and the story, if given), design it.
Reply with ONLY JSON: {"kind": "text|number|slider|status", "min": 0, "max": 100, "options": ["..."], "unit": "", "note": "one short sentence saying what it measures and what raises or lowers it", "scope": "scene|me|chars"}
kind guide: slider = a gauge with a known range (hunger, trust, stamina); number = an open-ended count (gold, days, ammo); status = one label from a short list (give options); text = free words. scope: scene = about the current scene, me = about the player's character, chars = tracked separately for each character.
Only include min/max for slider, options for status, unit for number."""

REPAIR_SYSTEM = """A side-panel update contained values that don't fit their field. For each item below, give the corrected value that fits the field's type from what the story says, or null if the story doesn't support one.
Reply with ONLY JSON: a list of {"i": item number, "value": corrected value or null}."""
