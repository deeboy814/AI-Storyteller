"""Helpers for importing a story that was played elsewhere (summary + recent turns + latest status panel)."""
import re

_ROLES = {"player": "user", "user": "user", "me": "user", "narrator": "assistant", "gm": "assistant", "ai": "assistant", "assistant": "assistant"}
_LABEL = re.compile(r"^[ \t]*(player|user|me|narrator|gm|ai|assistant)[ \t]*:[ \t]*", re.IGNORECASE | re.MULTILINE)
_SEP = re.compile(r"^[ \t]*-{3,}[ \t]*$", re.MULTILINE)

RESUME_NOTE = (
    "(Story resumed from an imported summary. Continue from the latest status panel below, keeping the "
    "turn counter and every value going; the player's next message follows.)"
)


def parse_turns(text: str) -> tuple[list[dict], list[str]]:
    """Turn pasted recent turns into [{"role", "text"}], plus warnings.

    Accepted formats: blocks starting with `PLAYER:` / `NARRATOR:` (also USER, ME, GM, AI), or blocks separated by
    a line of `---` which alternate player, narrator, player... starting with the player.
    """
    text = (text or "").strip()
    warnings: list[str] = []
    if not text:
        return [], warnings
    parts = _LABEL.split(text)
    raw: list[list[str]] = []
    if len(parts) > 1:
        for i in range(1, len(parts), 2):
            body = parts[i + 1].strip()
            if body:
                raw.append([_ROLES[parts[i].lower()], body])
    else:
        blocks = [b.strip() for b in _SEP.split(text) if b.strip()]
        raw = [["user" if i % 2 == 0 else "assistant", b] for i, b in enumerate(blocks)]
    merged: list[list[str]] = []
    for role, body in raw:  # two same-role blocks in a row become one message (models need strict alternation)
        if merged and merged[-1][0] == role:
            merged[-1][1] += "\n\n" + body
        else:
            merged.append([role, body])
    if merged and merged[0][0] == "assistant":
        merged.insert(0, ["user", RESUME_NOTE])
        warnings.append("Recent turns started with a narrator message, so a short 'story resumed' player line was added before it.")
    if merged and merged[-1][0] == "user":
        merged.pop()
        warnings.append("The last pasted block was a player message with no narrator reply, so it was left out. Send it as your first message instead.")
    return [{"role": r, "text": t} for r, t in merged], warnings
