"""App-drawn status line.

Many story prompts end every reply with a status line or block ("Rank: Cadet   Merit: 0   Sync: 96 ..."). The narrator writes about 100 to 250 tokens of it
per reply, which is the slowest and (with Claude) most expensive part of a reply. With "Let the app draw the status line" on, the narrator leaves it out and
the app writes it: it takes the story's own last status block as a template, asks a small helper which values changed, and puts the new values in the
same slots. The saved reply then looks exactly as it always did, so the side panel, checks and history all keep working.

It stays dormant (the narrator writes the line itself) until the story's recent replies contain a status block the app can recognise.
"""
import re

from . import rules

HEADING_MAX = 26
SKIP_FENCE = "```"


def _blockish(line: str, labels: list[str]) -> bool:
    """A line that can be part of a status block: blank, "Label: value" pairs, or a short heading. Never a notification, a fence or prose."""
    s = line.strip()
    if not s:
        return True
    if s.startswith(SKIP_FENCE) or s.startswith("*") or s.startswith('"') or s.startswith(">"):
        return False
    if rules._NOTE.match(line) and not rules._line_is_status(line, labels):
        return False
    if rules._pairs(line):
        return len(s) <= 260
    return len(s) <= HEADING_MAX and not re.search(r"[.!?,;]$", s)


def find_block(text: str, labels: list[str]) -> tuple[int, int] | None:
    """(start, end) character offsets of the status block at the end of a reply, or None. The block holds the status line (the one carrying the labels the
    story prompt describes) plus any "Label: value" lines and short heading around it, up to the end of the reply."""
    if not labels or not text or SKIP_FENCE in text.split("\n")[-1]:
        return None
    lines = text.split("\n")
    offs, pos = [], 0
    for ln in lines:
        offs.append(pos)
        pos += len(ln) + 1
    stat = [i for i, ln in enumerate(lines) if rules._line_is_status(ln, labels)]
    if not stat:
        return None
    last = stat[-1]
    if not all(_blockish(ln, labels) for ln in lines[last:]):
        return None
    first = last
    i = last - 1
    while i >= 0 and _blockish(lines[i], labels):
        first = i
        i -= 1
    while first < last and not lines[first].strip():
        first += 1
    # a heading line directly above must be a heading, not a stray pair line from a notification (handled by _blockish); nothing more to trim
    start = offs[first]
    end = len(text)
    while end > start and not text[end - 1].strip():
        end -= 1
    return start, end


def slots(block: str) -> list[tuple[str, int, int]]:
    """[(key, value_start, value_end)] for every "Label: value" in the block (a repeated label gets "#2", "#3"...)."""
    out, seen, pos = [], {}, 0
    for ln in block.split("\n"):
        m0 = re.match(r"^[ \t>*_]*\[[^\]]{1,30}\][ \t]*", ln)
        base = m0.end() if m0 else 0
        for m in rules._PAIR.finditer(ln, base):
            label = m.group(1).strip()
            seen[label] = seen.get(label, 0) + 1
            key = label if seen[label] == 1 else f"{label}#{seen[label]}"
            v0, v1 = m.start(2), m.end(2)
            while v1 > v0 and ln[v1 - 1] in " `*_\t":
                v1 -= 1
            out.append((key, pos + v0, pos + v1))
        pos += len(ln) + 1
    return out


def values(block: str) -> dict[str, str]:
    return {k: block[a:b] for k, a, b in slots(block)}


def render(block: str, new: dict) -> str:
    """The block with these values put in their slots; everything else (headings, spacing, symbols) is untouched."""
    for k, a, b in reversed(slots(block)):
        if k in new:
            block = block[:a] + new[k] + block[b:]
    return block


def last_block(story: dict) -> str | None:
    """The status block of the most recent reply that has one."""
    labels = rules.expected_labels(story)
    if not labels:
        return None
    start = min(story.get("summary_upto", 0), len(story.get("turns", [])))
    for t in reversed(story.get("turns", [])[start:]):
        if t["role"] == "assistant":
            r = find_block(t["text"], labels)
            if r:
                return t["text"][r[0]:r[1]]
    return None


def active(story: dict) -> bool:
    return bool(story.get("app_status")) and last_block(story) is not None


def strip(text: str, labels: list[str]) -> str:
    """A reply without its status block (what the narrator is shown of earlier replies when the app draws the line)."""
    r = find_block(text, labels)
    return text[:r[0]].rstrip() if r else text


RULE = """\
=== STATUS LINE (added by the app; this overrides the story prompt on this one point) ===
The app writes the status line / status block itself at the end of every reply, from the values it keeps. Do NOT write it: end the reply with your last paragraph of narration (or with your own notifications, if the story prompt has them). The current values are given in the note at the end of the newest message; keep the story consistent with them."""

SYSTEM = """You keep the status line of an interactive story. You are given its current values, the rules the story prompt gives for them, the player's move and the narrator's reply.
Reply with ONLY JSON: {"values": {"Label": "new value"}} listing ONLY the labels whose value changed because of events in this reply. If nothing changed, reply {"values": {}}.
Rules: a number changes only when the reply says it was gained, lost, spent, earned or set, and only by that amount; never repeat an unchanged value; write each new value in exactly the form of the current one (same units, slashes, symbols and wording style); text values (a place, a partner, a mission) change only when the reply states it; never invent anything the reply does not support."""


def request(story: dict, block: str, move: str, reply: str) -> tuple[str, str]:
    """(system, user message) for the helper."""
    labels = rules.expected_labels(story)
    low = {l.lower() for l in labels}
    rule_lines = [ln.strip() for ln in (story.get("base_prompt") or "").splitlines() if any(l in ln.lower() for l in low)]
    rules_text = "\n".join(rule_lines)[:1800]
    user = ("CURRENT STATUS (keys are the labels; repeated labels carry #2, #3):\n" + "\n".join(f"{k}: {v}" for k, v in values(block).items())
            + ("\n\nRULES FROM THE STORY PROMPT (lines that mention these labels):\n" + rules_text if rules_text else "")
            + f"\n\nPLAYER'S MOVE:\n{move[:700]}\n\nNARRATOR'S REPLY:\n{reply[-7000:]}")
    return SYSTEM, user


def apply_reply(block: str, data) -> tuple[str, int]:
    """(new block, how many values changed) from the helper's JSON; unknown labels and unchanged values are ignored."""
    vals = data.get("values") if isinstance(data, dict) else None
    if not isinstance(vals, dict):
        return block, 0
    cur = values(block)
    new = {}
    for k, v in vals.items():
        k = str(k).strip()
        if k not in cur or v is None or isinstance(v, (dict, list)):
            continue
        v = " ".join(str(v).split())[:120]
        if v and v != cur[k]:
            new[k] = v
    return render(block, new), len(new)
