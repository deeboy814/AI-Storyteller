"""Helpers for importing a story that was played elsewhere (summary + recent turns + latest status panel)."""
import re

_ROLES = {"player": "user", "user": "user", "me": "user", "human": "user", "you": "user", "narrator": "assistant", "gm": "assistant", "dm": "assistant", "ai": "assistant",
          "assistant": "assistant", "bot": "assistant", "storyteller": "assistant", "game master": "assistant",
          "you said": "user", "chatgpt said": "assistant", "claude said": "assistant", "gemini said": "assistant", "assistant said": "assistant", "chatgpt": "assistant", "claude": "assistant", "gemini": "assistant"}
# "PLAYER:", "**You:**", "> Assistant:", "## Narrator:" ... (the labels other apps' exports use; markdown decoration around the label is ignored)
_LABEL = re.compile(r"^[ \t>#*_]*(player|user|me|human|you[ \t]+said|you|narrator|gm|dm|ai|assistant|chatgpt[ \t]+said|claude[ \t]+said|gemini[ \t]+said|assistant[ \t]+said|chatgpt|claude|gemini|bot|storyteller|game[ \t]+master)[ \t*_]*:[ \t*_]*", re.IGNORECASE | re.MULTILINE)
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
                raw.append([_ROLES[" ".join(parts[i].lower().split())], body])
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


def is_labelled(text: str) -> bool:
    """True when the text is a transcript with labels for both sides (a single stray "Me:" inside prose does not make it one)."""
    roles = {_ROLES[" ".join(m.group(1).lower().split())] for m in _LABEL.finditer(text or "")}
    return len(roles) == 2 and len(_LABEL.findall(text)) >= 2


def split_tail(text: str, size: int = 3500) -> tuple[str, str]:
    """Plain prose with no labels: (everything before, the last whole paragraphs up to about `size` characters). The tail is kept word for word so the narrator keeps the
    voice and the scene in progress; the rest gets summarized. A text too short to be worth splitting is returned whole."""
    text = (text or "").strip()
    if len(text) < 2500:
        return text, ""
    size = min(size, int(len(text) * 0.4))      # a shorter text keeps less (at most 40% of it) word for word
    paras = []
    for p in re.split(r"\n\s*\n", text):
        if len(p) <= size:
            paras.append(p)
        else:           # no blank lines (a chat export with single line breaks, or one wall of text): break at line ends, then sentence ends
            for ln in p.split("\n"):
                paras += [ln] if len(ln) <= size else [x for x in re.split(r"(?<=[.!?])\s+", ln) if x]
    tail, n = [], 0
    for p in reversed(paras):
        if n + len(p) > size and tail:
            break
        tail.insert(0, p)
        n += len(p) + 2
    head = "\n".join(paras[:len(paras) - len(tail)]).strip()
    return head, "\n\n".join(tail).strip()


# ---------------- chat exports from other apps (JSON / JSONL) turned into a labelled transcript ----------------
import json as _json

_USER = {"user", "human", "player", "me", "you", "customer"}
_BOT = {"assistant", "ai", "bot", "model", "gpt", "chatgpt", "claude", "narrator", "gm", "dm", "character", "system_assistant"}


def _text_of(c) -> str:
    """The text of a message body in the shapes chat exports use: a string, a list of parts, or {"parts": [...]} / {"text": ...}."""
    if c is None:
        return ""
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(x for x in (_text_of(p) for p in c) if x)
    if isinstance(c, dict):
        for k in ("text", "content", "value"):
            if isinstance(c.get(k), (str, list, dict)):
                return _text_of(c[k])
        if "parts" in c:
            return _text_of(c["parts"])
    return ""


def _role_of(m: dict, user_name: str = "") -> str:
    """'user' / 'assistant' / '' (skip) for one exported message."""
    if isinstance(m.get("is_user"), bool):           # SillyTavern
        return "user" if m["is_user"] else ("" if m.get("is_system") else "assistant")
    r = ""
    for k in ("role", "sender", "author", "speaker", "from", "type"):
        v = m.get(k)
        if isinstance(v, dict):
            v = v.get("role") or v.get("name")
        if isinstance(v, str) and v.strip():
            r = v.strip().lower()
            break
    if r in _USER or r == "human":
        return "user"
    if r in _BOT:
        return "assistant"
    if r in ("system", "tool", "function"):
        return ""
    if user_name and str(m.get("name", "")).strip().lower() == user_name.lower():
        return "user"
    return "assistant" if r else ""


def _messages(obj) -> tuple[list[tuple[str, str]], str]:
    """(role, text) pairs and a description of the format, from a parsed export. Empty when the shape is not recognised."""
    if isinstance(obj, dict):
        if isinstance(obj.get("chat_messages"), list):                    # Claude export
            obj = obj["chat_messages"]; fmt = "Claude"
        elif isinstance(obj.get("messages"), list):
            obj = obj["messages"]; fmt = "chat"
        elif isinstance(obj.get("history"), list) or isinstance(obj.get("chat"), list):
            obj = obj.get("history") or obj.get("chat"); fmt = "chat"
        elif isinstance(obj.get("mapping"), dict):
            obj = [obj]; fmt = "ChatGPT"
        else:
            return [], ""
    else:
        fmt = "chat"
    if not isinstance(obj, list):
        return [], ""
    if obj and all(isinstance(c, dict) and isinstance(c.get("mapping"), dict) for c in obj):        # ChatGPT conversations.json: take the longest conversation
        best, bestn = [], 0
        for conv in obj:
            nodes = [n.get("message") for n in conv["mapping"].values() if isinstance(n, dict) and isinstance(n.get("message"), dict)]
            nodes.sort(key=lambda m: m.get("create_time") or 0)
            msgs = [(_role_of({"role": (m.get("author") or {}).get("role", "")}), _text_of(m.get("content"))) for m in nodes]
            msgs = [(r, t) for r, t in msgs if r and t.strip()]
            if len(msgs) > bestn:
                best, bestn = msgs, len(msgs)
        return best, "ChatGPT"
    user_name = ""
    for m in obj:
        if isinstance(m, dict) and ("user_name" in m) and "mes" not in m:      # SillyTavern's first line
            user_name = str(m.get("user_name") or "")
    out = []
    for m in obj:
        if not isinstance(m, dict) or ("mes" not in m and not any(k in m for k in ("content", "text", "message", "body"))):
            continue
        r = _role_of(m, user_name)
        t = _text_of(m.get("mes") if "mes" in m else m.get("content") if "content" in m else m.get("text") if "text" in m else m.get("message") or m.get("body"))
        if r and t.strip():
            out.append((r, t.strip()))
    return out, ("SillyTavern" if any(isinstance(m, dict) and "mes" in m for m in obj) else fmt)


def normalize_export(text: str) -> tuple[str, list[str]]:
    """If the pasted text is a JSON or JSONL chat export (ChatGPT, Claude, SillyTavern, OpenAI-style message lists...), turn it into a PLAYER:/NARRATOR: transcript the
    importer already understands. Anything else is returned unchanged."""
    t = (text or "").lstrip("﻿").strip()
    if not t or t[0] not in "[{":
        return text, []
    obj = None
    try:
        obj = _json.loads(t)
    except ValueError:
        rows = []
        for ln in t.splitlines():
            ln = ln.strip()
            if ln:
                try:
                    rows.append(_json.loads(ln))
                except ValueError:
                    return text, []
        obj = rows or None
    if obj is None:
        return text, []
    msgs, fmt = _messages(obj)
    if len(msgs) < 2:
        return text, []
    body = "\n\n".join(("PLAYER: " if r == "user" else "NARRATOR: ") + t for r, t in msgs)
    return body, [f"Read {len(msgs)} messages from a {fmt} export." if fmt == "chat" else f"Read {len(msgs)} messages from a {fmt} chat export."]
