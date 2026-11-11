"""Small jobs the app does itself in code, mainly to steady local models (Settings -> Local models -> Local-model mode). No AI calls.

 - trim_unfinished: a reply cut off by the length limit loses its unfinished last sentence.
 - fix_format: unclosed *italic* / **bold** and unclosed picture tags are closed.
 - repeat_note: phrases and paragraph openers the narrator keeps reusing become a "say it differently" note for the next reply.
 - length_note: when a reply drifts far from the story's usual length, a one-line nudge.
 - guess_mood: a character's mood read from the words around their name (for the "right now" card when the narrator wrote no portrait tag).
"""
import re
import statistics

from . import rules

_DONE = re.compile(r"[.!?…][*_\"”’)\]]*(?=\s|$)")
_TAIL_TAG = re.compile(r"(?:!?\[\[?[^\]\n]*|!\[\]\([^)\n]*)$")
_TAG = re.compile(r"!\[\]\([^)\n]*\)|\[\[[^\]\n]*\]\]")
_STOP = set("a an the and or but of to in on at for with from by as is was were be been it its he she they his her their him them you your i me my we our this that these those there then than so if not no yes do did does had has have will would could should can may might just still into over under out up down off about like".split())


def _plain(text: str) -> str:
    return _TAG.sub(" ", text)


# ---------------- 1. unfinished replies ----------------
def trim_unfinished(text: str) -> tuple[str, bool]:
    """(text, changed). Cuts back to the last finished sentence; a reply with no sentence end at all is left as it is."""
    t = _TAIL_TAG.sub("", text.rstrip()).rstrip()
    if not t or _DONE.search(t[-6:]):
        return (t, t != text.rstrip())
    last = None
    for m in _DONE.finditer(t):
        last = m
    if not last or last.end() < len(t) * 0.3:
        return text, False
    return t[:last.end()].rstrip(), True


# ---------------- 3. broken formatting ----------------
def _fix_line(ln: str) -> str:
    m = re.match(r"^(\s*[*+-]\s+)(.*)$", ln)
    lead, body = (m.group(1), m.group(2)) if m else ("", ln)
    runs = re.findall(r"\*+", re.sub(r"(?<=\w)\*+(?=\w)", "", body))        # "f**king" is a censored word, not emphasis
    if not runs or any(len(r) > 2 for r in runs) or not body.strip("* "):
        return ln
    it = bd = False
    for r in runs:
        if len(r) == 1:
            it = not it
        else:
            bd = not bd
    if it == bd:        # balanced, or so tangled (both open) that guessing would make it worse
        return ln
    return lead + body.rstrip() + ("*" if it else "**")


def fix_format(text: str) -> tuple[str, int]:
    """(text, lines changed): unclosed italic or bold closed at the end of their line, and an unclosed picture tag closed."""
    out, fenced, n = [], False, 0
    for ln in text.split("\n"):
        if ln.strip().startswith("```"):
            fenced = not fenced
            out.append(ln)
            continue
        new = ln
        if not fenced:
            new = re.sub(r"(!\[\]\((?:portrait|place):[^)\n]*)$", r"\1)", new)
            new = _fix_line(new)
        n += new != ln
        out.append(new)
    return "\n".join(out), n


# ---------------- 2. repeated phrases ----------------
def _names(story: dict) -> set[str]:
    out = set()
    for c in story.get("cast", []) or []:
        out |= {w.lower() for w in re.findall(r"[A-Za-z']+", c.get("name", ""))}
    out |= {w.lower() for w in re.findall(r"[A-Za-z']+", (story.get("avatar") or {}).get("name", "") or story.get("player_name", ""))}
    return out


def _prose(text: str) -> list[str]:
    """Paragraphs of narration: no tags, notifications, status lines or code fences."""
    text = _plain(text)
    out = []
    for p in re.split(r"\n\s*\n", text):
        p = p.strip()
        if not p or p.startswith("```") or rules._NOTE.match(p) or any(rules._pairs(l) for l in p.split("\n")):
            continue
        out.append(p)
    return out


def _words(p: str) -> list[str]:
    return re.findall(r"[a-z']+", p.lower().replace("*", " "))


def repeat_note(story: dict, replies: int = 5) -> str:
    """A note naming phrases used in at least 3 of the last replies, and paragraph openers used in at least 3; "" when nothing repeats (or too few replies)."""
    texts = [t["text"] for t in story.get("turns", []) if t["role"] == "assistant"][-replies:]
    if len(texts) < 4:
        return ""
    names = _names(story)
    grams, opens = {}, {}
    for t in texts:
        seen_g, seen_o = set(), set()
        for p in _prose(t):
            w = _words(p)
            for i in range(len(w) - 3):
                g = w[i:i + 4]
                if sum(x in _STOP or x in names for x in g) <= 1:
                    seen_g.add(" ".join(g))
            if len(w) >= 3 and not (set(w[:3]) & names):
                seen_o.add(" ".join(w[:3]))
        for g in seen_g:
            grams[g] = grams.get(g, 0) + 1
        for o in seen_o:
            opens[o] = opens.get(o, 0) + 1
    picks: list[str] = []
    for g, c in sorted(grams.items(), key=lambda kv: -kv[1]):
        if c >= 3 and not any(len(set(g.split()) & set(p.split())) >= 3 for p in picks):
            picks.append(g)
        if len(picks) >= 4:
            break
    starts = [o for o, c in sorted(opens.items(), key=lambda kv: -kv[1]) if c >= 3 and not any(o in p or p in o for p in picks)][:2]
    if not picks and not starts:
        return ""
    parts = []
    if picks:
        parts.append("phrases: " + "; ".join(f'"{p}"' for p in picks))
    if starts:
        parts.append("paragraph openings: " + "; ".join(f'"{s}..."' for s in starts))
    return "[VARIETY NOTE from the app, for the narrator only: your recent replies keep reusing these (" + " | ".join(parts) + "). Say it differently this time: new wording, new sentence shapes.]"


# ---------------- 6. reply length ----------------
def _count(text: str) -> int:
    return sum(len(_words(p)) for p in _prose(text))


def length_note(story: dict) -> str:
    """A nudge when the last reply is far longer or shorter than the story's usual one (the median of its first replies); "" otherwise."""
    texts = [t["text"] for t in story.get("turns", []) if t["role"] == "assistant"]
    if len(texts) < 6:
        return ""
    usual = statistics.median(_count(t) for t in texts[:8])
    last = _count(texts[-1])
    if usual < 80 or not (last > usual * 1.5 or last < usual * 0.55):
        return ""
    r = lambda n: int(round(n / 50.0) * 50)
    return (f"[LENGTH NOTE from the app, for the narrator only: your last reply was about {r(last)} words; this story's replies usually run about {r(usual)}. "
            f"Aim for roughly {r(usual)} words this time, unless the scene truly needs otherwise.]")


# ---------------- 5. mood from words ----------------
MOOD_WORDS = {
    "smile": r"smil|grin|laugh|giggl|beam|chuckl|delight|cheer|bright",
    "surprise": r"gasp|startl|stun|widen|wide-eyed|blink|taken aback|shock|stare",
    "fear": r"afraid|tremb|flinch|pale|terrif|shak|frighten|panic|cower|dread",
    "sad": r"tear|cry|crying|sob|sigh|grief|mourn|hollow|quiet and low|downcast|sorrow",
    "angry": r"glare|snap|fury|furious|scowl|clench|growl|seeth|rage|anger|angry",
    "scorn": r"sneer|scoff|roll[s]? her eyes|roll[s]? his eyes|smirk|disdain|contempt|unimpressed",
    "shy": r"blush|fidget|look[s]? away|flush|stammer|bashful|embarrass|averts",
}


def guess_mood(text: str, name: str, available: list[str]) -> str:
    """The best of the character's own mood names for the sentences around their name in this reply, or ""."""
    if not available:
        return ""
    first = name.split()[0]
    sents = [s for s in re.split(r"(?<=[.!?])\s+", _plain(text).replace("*", "")) if re.search(rf"\b{re.escape(first)}\b", s, re.I)][-4:]
    if not sents:
        return ""
    score = {}
    for w, s in enumerate(sents, 1):                 # later sentences weigh more
        for mood, pat in MOOD_WORDS.items():
            score[mood] = score.get(mood, 0) + w * len(re.findall(pat, s, re.I))
    for mood, _ in sorted(score.items(), key=lambda kv: -kv[1]):
        if score[mood] <= 0:
            break
        hit = next((a for a in available if re.sub(r"\s*\d+$", "", a.lower()) == mood or a.lower().startswith(mood)), "")
        if hit:
            return hit
    return ""
