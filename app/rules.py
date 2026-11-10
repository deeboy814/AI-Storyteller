"""Checks and bookkeeping the app does itself, with no AI call: the format of a reply's status line, numbers that moved the wrong way, stat points the narrator
changed on its own, and the key events a reply announces in its own notifications (Level 3 reached, Life lost, Feat recorded...)."""
import math
import re

# "Label: value" pairs separated by two or more spaces (the status line the story prompt asks for)
_PAIR = re.compile(r"([A-Za-z][A-Za-z' ]{0,24}?):\s*(\S[^\n]*?)(?=\s{2,}[A-Za-z][A-Za-z' ]{0,24}?:|\s*$)")
_NOTE = re.compile(r"^[ \t>*_]*\[(?:SYSTEM[ _-]*)?NOTIFICATION\][ \t*_]*(.+?)[ \t*_]*$", re.IGNORECASE | re.MULTILINE)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _pairs(line: str) -> list[tuple[str, str]]:
    line = re.sub(r"^[ \t>*_]*\[[^\]]{1,30}\][ \t]*", "", line).strip(" `*_\t")
    return [(k.strip(), v.strip(" `*_")) for k, v in _PAIR.findall(line)]


def expected_labels(story: dict) -> list[str]:
    """The labels of the status line the story prompt describes: the longest line in it with at least three "Label: value" pairs (usually inside backticks)."""
    best: list[str] = []
    for ln in (story.get("base_prompt") or "").splitlines():
        ps = _pairs(ln)
        if len(ps) >= 3 and len(ps) > len(best) and not re.match(r"^\s*(#|[-*]\s+\*\*)", ln):
            best = [k for k, _ in ps]
    return best


def status_lines(text: str, labels: list[str]) -> list[tuple[int, dict[str, str]]]:
    """(character offset, {label: value}) for each line of the reply that is a status line (carries at least 60% of the expected labels, and at least 3)."""
    need = max(3, math.ceil(0.6 * len(labels))) if labels else 99
    low = {l.lower() for l in labels}
    out, pos = [], 0
    for ln in text.splitlines(keepends=True):
        ps = _pairs(ln)
        got = {k: v for k, v in ps if k.lower() in low}
        if len(got) >= need:
            out.append((pos, got))
        pos += len(ln)
    return out


def num(v) -> float | None:
    m = _NUM.search(str(v if v is not None else "").replace(",", ""))
    return float(m.group(0)) if m else None


def _last_status(story: dict, before: int, labels: list[str]) -> dict[str, str]:
    for t in reversed(story.get("turns", [])[:before]):
        if t.get("role") == "assistant":
            sl = status_lines(t["text"], labels)
            if sl:
                return sl[-1][1]
    return {}


KEYWORDS = re.compile(r"\b(xp|level|life|lives|merit|class|feat|reward|awarded|stat points? (?:assigned|awarded|spent|gained)|graduat\w*|died|dead|death|killed|kills?|learned|"
                      r"unlocked|promoted|reached|recorded|quest (?:completed|failed)|completed|failed)\b", re.IGNORECASE)


def notifications(text: str) -> list[str]:
    """The notification lines of a reply that are not status lines."""
    out = []
    for m in _NOTE.finditer(text):
        body = m.group(1).strip()
        if len(_pairs(body)) >= 3:       # the status line itself
            continue
        out.append(body)
    return out


def key_events(text: str, limit: int = 6) -> list[str]:
    """Notifications worth keeping as a character's key events: level-ups, lives, feats, XP awards, merit, quests finished or failed, classes... Quests that merely
    start or update are left to the quest log."""
    out = []
    for b in notifications(text):
        if re.match(r"^quest\s+(started|updated|new|given)\b", b, re.IGNORECASE):
            continue
        if KEYWORDS.search(b):
            b = " ".join(b.split())[:200]
            if b.lower() not in {x.lower() for x in out}:
                out.append(b)
    return out[:limit]


def check(story: dict, idx: int) -> list[str]:
    """Format and number slips in the reply at index idx, found without an AI. Returns short sentences (at most 4); empty when all is well or the story prompt
    does not describe a status line."""
    labels = expected_labels(story)
    text = story["turns"][idx]["text"]
    issues: list[str] = []
    if labels:
        sl = status_lines(text, labels)
        prompt = (story.get("base_prompt") or "").lower()
        if len(sl) > 1:
            issues.append(f"The status line is printed {len(sl)} times; once is expected.")
        if sl and "last line" in prompt and text[sl[-1][0]:].strip() and any(ln.strip() for ln in text[sl[-1][0]:].splitlines()[1:]):
            issues.append("The status line is not the last line of the reply.")
        if sl:
            low = {k.lower() for k in sl[-1][1]}
            miss = [l for l in labels if l.lower() not in low]
            if miss:
                issues.append("The status line is missing: " + ", ".join(miss[:6]) + ".")
        else:
            moved = [n for n in notifications(text) if re.search(r"\b(xp|level|life|lives|merit|points?)\b", n, re.IGNORECASE)]
            if moved:
                issues.append("A number changed (" + moved[0][:60].rstrip(".") + ") but there is no status line.")
        if sl:
            cur, prev = sl[-1][1], _last_status(story, idx, labels)
            for k, v in cur.items():
                kl, a, b = k.lower(), num(v), num(prev.get(k)) if prev else None
                if a is None or b is None:
                    continue
                if kl in ("day", "week", "turn") and a < b:
                    issues.append(f"{k} went backwards ({b:g} to {a:g}).")
                elif kl in ("xp", "experience") and a < b:
                    issues.append(f"{k} went down ({b:g} to {a:g}).")
                elif kl in ("lives",) and a > b:
                    issues.append(f"{k} went up ({b:g} to {a:g}).")
                elif kl == "level" and a < b:
                    issues.append(f"{k} went down ({b:g} to {a:g}).")
            xp = next((v for k, v in cur.items() if k.lower() in ("xp", "experience")), "")
            m = re.match(r"\s*(-?\d[\d,]*)\s*/\s*(\d[\d,]*)", str(xp))
            if m and int(m.group(1).replace(",", "")) >= int(m.group(2).replace(",", "")):
                issues.append(f"XP {m.group(1)} is not below the next level's total {m.group(2)}: a level-up is missing.")
            # stats that rose without the player spending points
            for fld in _spend_targets(story):
                kl = fld["label"].lower()
                kk = next((k for k in cur if k.lower() == kl), None)
                if kk is None or kk not in prev:
                    continue
                a, b = num(cur[kk]), num(prev.get(kk))
                spent = sum(s.get("n", 0) for s in ((story["turns"][idx - 1].get("pts") or []) if idx >= 1 else []) if s.get("id") == fld["id"])
                if a is not None and b is not None and a > b + spent:
                    issues.append(f"{kk} rose from {b:g} to {a:g} without the player spending points.")
    try:
        issues += lint(story, idx)
    except Exception:
        pass
    return issues[:5]


def _spend_targets(story: dict) -> list[dict]:
    from . import tracker
    fields = tracker.active_fields(story)
    ids = {i for f in fields if f.get("spend_on") for i in f["spend_on"]}
    return [f for f in fields if f["id"] in ids]


def roll_note(r: dict) -> str:
    """The hidden note telling the narrator what the app's dice decided."""
    return ("[DICE CHECK made by the app for the player's attempt above: " + (f"{r['label']} {r['value']:g} ({r['mod']:+d}): " if r.get("label") else "") +
            f"d20 {r['die']}" + (f" (rolled {r['rolls'][0]} and {r['rolls'][1]}, took the {'higher' if r['adv'] == 'adv' else 'lower'})" if r.get("adv") in ("adv", "dis") else "") +
            f" {r['mod']:+d} = {r['total']} against {r['dc']} ({r['difficulty']}): {r['outcome'].upper()}"
            + (f" by {abs(r['margin'])}" if r["margin"] else "") + ". This outcome is final: narrate it, do not roll again or soften or reverse it, "
            "and keep the consequences in proportion. Do not print the numbers unless the story prompt shows them.]")


DIFFICULTY = {"easy": 8, "medium": 12, "hard": 16, "very hard": 20, "heroic": 24}
BASE_STAT = 5          # a stat of 5 is average and adds nothing; each point above or below adds or subtracts 1


def roll(value, difficulty: str = "medium", dc=None, adv: str = "", label: str = "", rng=None) -> dict:
    """A d20 check: d20 + (stat - 5) against a difficulty (or a number). `adv` is "adv" or "dis" for two dice. Pure code, so the outcome is fair and the narrator cannot fudge it."""
    import random
    rng = rng or random
    v = float(value) if value is not None else float(BASE_STAT)
    mod = int(round(v - BASE_STAT))
    rolls = [rng.randint(1, 20)] + ([rng.randint(1, 20)] if adv in ("adv", "dis") else [])
    die = max(rolls) if adv == "adv" else min(rolls)
    try:
        target = int(dc) if dc not in (None, "") else DIFFICULTY.get(str(difficulty).lower(), 12)
    except (TypeError, ValueError):
        target = DIFFICULTY.get(str(difficulty).lower(), 12)
    target = max(2, min(target, 40))
    total = die + mod
    margin = total - target
    if die == 20:
        outcome = "critical success"
    elif die == 1:
        outcome = "critical failure"
    elif margin >= 0:
        outcome = "success"
    elif margin >= -3:
        outcome = "partial success, at a cost"
    else:
        outcome = "failure"
    name = next((k for k, d in DIFFICULTY.items() if d == target), f"DC {target}")
    return {"label": label, "value": v, "mod": mod, "die": die, "rolls": rolls, "adv": adv if adv in ("adv", "dis") else "", "total": total, "dc": target,
            "difficulty": name, "outcome": outcome, "margin": margin}


# ---------------- when do dice get rolled ----------------
_CALLS_FOR_CHECKS = re.compile(r"\b(rolls?|rolled|rolling|dice|d20|d100|d12|d10|d8|d6|skill checks?|stat checks?|ability checks?|saving throws?|difficulty class|checks? (?:against|versus|vs\.?|using))\b|\bDC\s*\d", re.IGNORECASE)


def prompt_calls_for_checks(story: dict) -> bool:
    """Does the story prompt itself talk about rolls or checks? Then the app rolls for risky actions by itself; otherwise only when the player presses 🎲."""
    return bool(_CALLS_FOR_CHECKS.search(story.get("base_prompt") or ""))


def dice_mode(story: dict) -> str:
    """auto (the app rolls for risky actions) | manual (only when the player presses the dice button) | off. Unless the player chose, the story prompt decides."""
    m = (story.get("dice") or {}).get("mode")
    if m in ("auto", "manual", "off"):
        return m
    return "auto" if prompt_calls_for_checks(story) else "manual"


# What each kind of stat is used for: the words in a move that point at it. A stat whose name is not here is matched on its own name.
STAT_WORDS = {
    ("strength", "might", "brawn", "power"): r"lift|push|pull|break|smash|force|wrestl\w*|grappl\w*|punch\w*|hit|strike|fight\w*|carry|throw|tackle|swing|slam|overpower|bend|drag|heave|crush|bash",
    ("reflexes", "dexterity", "agility", "speed", "reflex"): r"dodg\w*|duck\w*|catch|jump\w*|leap\w*|dash\w*|evad\w*|parr\w*|balanc\w*|flip|climb\w*|twist|react\w*|slip|vault|roll away|scramble|swerve",
    ("aiming", "aim", "marksmanship", "accuracy", "shooting"): r"shoot\w*|aim\w*|fire|snip\w*|hurl\w*|target|toss|throw|launch",
    ("stamina", "endurance", "constitution", "toughness", "vitality"): r"run|sprint\w*|endur\w*|march\w*|swim\w*|outlast|keep going|push through|haul|trek|survive|hold my breath",
    ("magic", "arcana", "sorcery"): r"cast\w*|spell\w*|conjur\w*|channel\w*|enchant\w*|invok\w*|ward\w*|summon\w*|magic",
    ("knowledge", "intelligence", "lore", "wit"): r"recall\w*|remember\w*|study|studi\w*|analy[sz]\w*|identif\w*|recogni[sz]\w*|examin\w*|investigat\w*|deduc\w*|figure out|translat\w*|diagnos\w*|repair\w*|decipher\w*|work out",
    ("willpower", "wisdom", "resolve", "spirit", "composure"): r"resist\w*|focus\w*|concentrat\w*|steel|refus\w*|hold firm|persist\w*|withstand|ignor\w* the pain|stay calm|stay focused|overcome fear",
    ("recovery", "regeneration", "healing", "resilience"): r"recover\w*|heal\w*|rest|bandag\w*|treat|patch up|regenerat\w*|tend",
    ("charisma", "persuasion", "presence", "social"): r"persuad\w*|convinc\w*|charm\w*|negotiat\w*|bargain\w*|bluff\w*|lie|intimidat\w*|inspir\w*|reassur\w*|haggl\w*|talk (?:him|her|them) (?:into|out)",
    ("perception", "awareness", "observation", "alertness"): r"spot|notice|search\w*|listen\w*|track\w*|scout\w*|watch\w*|look for|scan\w*",
    ("stealth", "sneaking", "subterfuge"): r"sneak\w*|hid(?:e|ing)|creep\w*|slip past|shadow\w*|steal\w*|pick(?:ing)? (?:the|a) lock",
}
_NOT_A_STAT = {"day", "day turn", "lives", "life", "level", "xp", "experience", "points", "merit", "gold", "coins", "money", "hp", "health", "time", "turn", "ring", "class", "quests"}


def check_stats(story: dict) -> list[dict]:
    """The player's number trackers that can be rolled against (stats and skills): each {id, label, value}."""
    from . import tracker
    tr = story.get("tracker") or {}
    out = []
    for f in tracker.active_fields(story):
        if f.get("kind") != "number" or not f["id"].startswith("me.") or f["label"].strip().lower() in _NOT_A_STAT or f.get("levels") or f.get("counts") or f.get("spend_on"):
            continue
        v = num((tr.get("me") or {}).get(f["id"].split(".", 1)[1]))
        out.append({"id": f["id"], "label": f["label"], "value": v})
    return out


def auto_stat(story: dict, move: str) -> dict | None:
    """Which stat does this move rely on? None when it is not a risky action (a question, a command, plain talk) or no tracked stat fits."""
    text = " ".join(move.lower().split())
    if len(text.split()) < 3 or text.startswith(("(", "/", "?")) or text.rstrip().endswith("?"):
        return None
    best, score = None, 0
    for st in check_stats(story):
        label = st["label"].strip().lower()
        pat = next((p for names, p in STAT_WORDS.items() if label in names or any(n in label for n in names)), None)
        s = len(re.findall(r"\b(?:" + pat + r")\b", text)) if pat else 0
        if re.search(r"\b" + re.escape(label) + r"\b", text):
            s += 2
        if s > score:
            best, score = st, s
    return best


def auto_roll(story: dict, move: str, rng=None) -> dict | None:
    st = auto_stat(story, move)
    if not st:
        return None
    diff = "hard" if re.search(r"\b(difficult|hard|dangerous|risky|impossible)\b", move, re.IGNORECASE) else "easy" if re.search(r"\b(easy|simple|carefully)\b", move, re.IGNORECASE) else (story.get("dice") or {}).get("difficulty", "medium")
    r = roll(st["value"], diff, label=st["label"], rng=rng)
    r["auto"] = True
    return r


# ---------------- consistency lint (free): who is dead, misspelled names, eye and hair colours that contradict a character's card ----------------
import difflib

_COLORS = {"black", "brown", "blue", "green", "grey", "gray", "hazel", "amber", "golden", "blond", "blonde", "red", "auburn", "ginger", "white", "silver", "violet", "purple", "pink",
           "chestnut", "copper", "teal", "turquoise", "emerald", "sapphire"}
_SAME = {"gray": "grey", "blonde": "blond"}
_TAGNAME = re.compile(r"\[\[([^/\]\n]+)/[^\]]*\]\]|portrait:([^/)\n]+)/|^\s*\*\*([^*|\n]{2,40})\*\*\s*\|", re.MULTILINE)


def _norm(c: str) -> str:
    return _SAME.get(c.lower(), c.lower())


def _card_colors(appearance: str, part: str) -> set[str]:
    cols = "|".join(sorted(_COLORS))
    found = {_norm(m.group(1)) for m in re.finditer(r"\b(" + cols + r")\b(?:[- ]\w+){0,2}?[- ]" + part + r"\b", appearance or "", re.IGNORECASE)}
    found |= {_norm(m.group(1)) for m in re.finditer(part + r"\b[^.;,]{0,12}?\b(" + cols + r")\b", appearance or "", re.IGNORECASE)}
    return found


def lint(story: dict, idx: int) -> list[str]:
    """Slips a reader would notice, found without an AI. At most 3, short. Only what the app can know: the tracker's status for a character, the cast cards, the names used."""
    text = story["turns"][idx]["text"]
    cast = [c for c in story.get("cast") or [] if c.get("name")]
    out: list[str] = []
    tr = story.get("tracker") or {}
    tagged = []
    for m in _TAGNAME.finditer(text):
        nm = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        mood = ""
        tagged.append((nm, mood))
    # 1. a character the side panel says is dead speaks or gets an ordinary portrait
    dead = {n.lower() for n, f in (tr.get("chars") or {}).items() if isinstance(f, dict) and str(f.get("status", "")).strip().lower() in ("dead", "deceased")}
    for m in re.finditer(r"\[\[([^/\]\n]+)/([^\]]*)\]\]|portrait:([^/)\n]+)/([^)\n]*)", text):
        nm, mood = (m.group(1) or m.group(3) or "").strip(), (m.group(2) or m.group(4) or "").strip().lower()
        if nm.lower() in dead and not re.match(r"(dead|zombie)", mood):
            out.append(f"{nm} is shown as dead in the side panel but speaks here (mood: {mood or 'normal'}).")
            break
    # 2. a name that looks like a misspelling of a cast member
    known = {c["name"].lower() for c in cast} | {c["name"].lower().split()[0] for c in cast} | {a.lower() for c in cast for a in c.get("aka") or []}
    pn = ((story.get("avatar") or {}).get("name") or story.get("player_name") or "").lower()
    if pn:
        known |= {pn, pn.split()[0]}
    for nm, _ in tagged:
        low = nm.lower()
        if not low or low in known or low.split()[0] in known:
            continue
        best = max(((difflib.SequenceMatcher(None, low, k).ratio(), k) for k in known if len(k) >= 4), default=(0, ""))
        if best[0] >= 0.8 and best[1]:
            real = next((c["name"] for c in cast if best[1] in (c["name"].lower(), c["name"].lower().split()[0])), best[1].title())
            out.append(f"'{nm}' looks like a misspelling of {real}.")
            break
    # 3. eye or hair colour that contradicts the character's card
    for c in cast:
        first = c["name"].split()[0]
        for part in ("eyes", "hair"):
            want = _card_colors(c.get("appearance", ""), part)
            if not want:
                continue
            for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
                if not re.search(rf"\b{re.escape(first)}\b", sent, re.IGNORECASE) or sum(1 for d in cast if re.search(rf"\b{re.escape(d['name'].split()[0])}\b", sent, re.IGNORECASE)) > 1:
                    continue
                cols = {_norm(m.group(1)) for m in re.finditer(r"\b(" + "|".join(sorted(_COLORS)) + r")\b(?:[- ]\w+){0,2}?[- ]" + part + r"\b", sent, re.IGNORECASE)}
                if cols and not (cols & want):
                    out.append(f"{first}'s {part[:-1] if part == 'eyes' else part} are {'/'.join(sorted(cols))} here, but their card says {'/'.join(sorted(want))}.".replace("eye are", "eyes are"))
                    break
            else:
                continue
            break
        if len(out) >= 3:
            break
    return out[:3]


# ---------------- tidy: put a stray status line where the story prompt wants it (free) ----------------
def _line_is_status(line: str, labels: list[str]) -> bool:
    need = max(3, math.ceil(0.6 * len(labels)))
    low = {l.lower() for l in labels}
    return len({k for k, _ in _pairs(line) if k.lower() in low}) >= need


def tidy(story: dict, text: str) -> tuple[str, list[str]]:
    """When the story prompt describes a status line: drop an earlier copy of it, and move it to the end when the prompt says it is the last line. Returns (text, what was done).
    Lines inside a code fence are left alone."""
    labels = expected_labels(story)
    if not labels or not text:
        return text, []
    prompt = (story.get("base_prompt") or "").lower()
    wants_last = "last line" in prompt or bool(re.search(r"end of (?:every|each|the) reply", prompt))
    lines = text.split("\n")
    idxs = [i for i, ln in enumerate(lines) if _line_is_status(ln, labels)]
    if not idxs:
        return text, []

    def fenced(i: int) -> bool:
        j = i - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        return j >= 0 and lines[j].strip().startswith("```")

    if any(fenced(i) for i in idxs):
        return text, []
    notes = []
    if len(idxs) > 1:
        for i in idxs[:-1]:
            lines[i] = None
        notes.append("removed an earlier copy of the status line")
        idxs = [idxs[-1]]
    lines = [ln for ln in lines if ln is not None]
    keep = next(i for i, ln in enumerate(lines) if _line_is_status(ln, labels))
    if wants_last and any(ln.strip() for ln in lines[keep + 1:]):
        line = lines.pop(keep)
        while lines and not lines[-1].strip():
            lines.pop()
        lines += ["", line]
        notes.append("moved the status line to the end")
    if not notes:
        return text, []
    new = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return new, notes
