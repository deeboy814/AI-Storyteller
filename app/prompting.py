"""Builds the prompt sent to the model each turn, and reads the state block back out."""
import copy
import json
import re

from . import tracker

ENGINE_CORE = """\
=== ENGINE RULES (added by the app; always follow) ===
You are the narrator / game master of an interactive story. Each turn the player says what their character does or says.
Narrate what happens next, following the world, characters, rules, stats and prose style defined above. Never decide the
player character's actions or words beyond what the player stated. Keep the world, characters and stats consistent with
the story so far. Use the exact response format the story prompt above defines (fill in real values; never copy
placeholders or templates literally).
"""

# Optional extra: lets the app fill its sidebar. Turn off per story when the prompt has its own status panel.
ENGINE_STATE = """\
End EVERY reply with a machine-readable state block on its own, exactly like this (valid JSON, one line):
<state>{"when": "...", "location": "...", "hp": [current, max], "sheet": "short status/stat line", "affinity": {"Character Name": 0-100}, "inventory": ["..."], "suggestions": ["action 1", "action 2", "action 3"]}</state>
- Include a field only if it applies to this story and has changed or needs setting; "suggestions" must always be present (3 short, distinct next actions the player could take).
- "affinity" only lists characters whose value changed. Use the stats and rules from the story prompt above if it defines them.
- The state block is read by the app and hidden from the player. Never mention it or put anything after it.
"""

SCENE_RE = re.compile(r"<scene>(.*?)(?:</scene>|\Z)", re.DOTALL | re.IGNORECASE)
NEWCHAR_RE = re.compile(r"<newchar>(.*?)(?:</newchar>|\Z)", re.DOTALL | re.IGNORECASE)
STATE_RE = re.compile(r"<state>(.*?)(?:</state>|\Z)", re.DOTALL)
FENCE_RE = re.compile(r"\s*```(?:json)?\s*\Z", re.IGNORECASE)
LABEL_RE =re.compile(r"\n[\s*_#>`-]*(?:current\s+)?state[\s*_`]*:?[\s*_`]*\Z", re.IGNORECASE)


AVATAR_LABELS = [
    ("name", "Name"), ("gender", "Gender"), ("age", "Age"), ("appearance", "Appearance"),
    ("personality", "Personality"), ("background", "Background"), ("abilities", "Abilities / stats / inventory"),
]


SCENE_RULE = """\
=== SCENE IMAGES (app feature) ===
Only when this turn is visually striking (a new location, a dramatic moment, an important emotional beat), also end the reply with
<scene>one sentence describing the single best frame: setting, the characters present by name, their poses and expressions, the lighting</scene>
placed just before the state block if there is one, otherwise at the very end. Most turns should have NO scene tag.
{CONTENT}
The tag is hidden from the player and read by an image generator."""
SCENE_SFW = "Keep it safe for work: no nudity or explicit content; for intimate moments pick a different frame (a hand, the room, a glance)."
SCENE_ADULT = "Adult content is allowed in this story: an intimate moment may be shown as it happens, between adults only."


IMAGE_GUARD = """\
=== IMAGES IN THIS APP ===
The app draws and shows all pictures itself. Ignore any instruction in the story prompt above (or its opening) to print image keywords, \
image-site links, http(s) picture links or markdown pictures: they cannot be shown here. Pictures appear only through the app tags described below, \
if there are any."""

NEWCHAR_RULE = """\
=== NEW CHARACTERS (app feature) ===
When a NEW named character who may appear again (not a one-off extra) shows up for the first time, also end the reply with one hidden tag for each:
<newchar>Full Name | one line of purely visual details: age, hair, eyes, build, clothing, distinguishing features</newchar>
placed before the scene tag and the state block if there are any, otherwise at the very end. Once per character; never for the player character or for
anyone already named earlier in the story. Keep the details safe for work. The tag is hidden from the player; the app uses it to offer art for that character."""


BEGIN_NOTE = ("(The player pressed Begin. Write the story's next scene now: continue directly from the opening scene above, using the story prompt, "
              "the player's notes and the player character. Do not repeat or summarize the opening, and do not decide the player character's actions or words.)")


def _is_begin(story: dict, turn: dict) -> bool:
    """The Begin button sends 'Begin the story.' as the very first player message; the narrator gets a real instruction instead."""
    return turn["role"] == "user" and story["turns"] and turn is story["turns"][0] and turn["text"].strip().lower().rstrip(".!") == "begin the story"


def images_block(story: dict) -> str:
    """Instructions for the app's image features. Stable text (sorted), so it does not disturb prompt caching."""
    art = story.get("art") or {}
    parts = [IMAGE_GUARD]
    # Characters that have exactly the same moods share one line (a big cast would otherwise repeat the same long list for each).
    groups: dict[tuple, list[str]] = {}
    avail = []
    for c in story.get("cast") or []:
        if not c.get("moods"):
            continue
        if c.get("player"):
            avail.append(f"{c['name']} (the player character: use this tag only if the story prompt above says to show the player's portrait): {', '.join(sorted(c['moods']))}")
        else:
            groups.setdefault(tuple(sorted(c["moods"])), []).append(c["name"])
    avail = [f"{', '.join(names)}: {', '.join(moods)}" for moods, names in sorted(groups.items(), key=lambda kv: kv[1][0])] + avail
    if avail and art.get("portraits", True):
        parts.append(
            "=== PORTRAITS (character images available in this app; this replaces any other instruction about images) ===\n"
            + "\n".join(avail)
            + "\nDirectly above each line of dialogue from a listed character, output one image tag on its own line: "
              "![](portrait:Name/mood), choosing the mood that best fits what they feel or do in that line. Use the names and moods "
              "listed above, spelled exactly, whenever one of them fits. Only if NONE of a character's listed moods fits what they feel, "
              "you may use a new one- or two-word mood name (for example terrified): the player will be offered to draw it. "
              "Always prefer a listed mood, even if it is only the closest fit; ask for a new mood only when no listed one comes close. "
              "Skip the tag for anyone not listed."
        )
    if art.get("newchar", False):
        parts.append(NEWCHAR_RULE)
    if art.get("auto"):
        parts.append(SCENE_RULE.replace("{CONTENT}", SCENE_ADULT if story.get("nsfw") else SCENE_SFW))
    return "\n\n".join(parts)


def split_scene(text: str) -> tuple[str, str]:
    """(text without <scene> tag, scene description or '')."""
    m = list(SCENE_RE.finditer(text))
    scene = m[-1].group(1).strip() if m else ""
    return NEWCHAR_RE.sub("", SCENE_RE.sub("", text)).strip(), scene


def extract_newchars(text: str) -> list[tuple[str, str]]:
    """[(name, visual description)] from the hidden <newchar>Name | looks</newchar> tags of a raw reply."""
    out = []
    for m in NEWCHAR_RE.finditer(text):
        name, _, look = m.group(1).partition("|")
        name = re.sub(r"[*_`\s]+$|^[*_`\s]+", "", name)
        if name:
            out.append((name, look.strip()))
    return out


def opening_for_model(story: dict) -> str:
    """The opening scene as the narrator should see it: text only (pictures and hidden comment lines removed)."""
    if not story.get("opening") or not story.get("opening_in_context", True):
        return ""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)\s*", "", story["opening"])
    text = re.sub(r"^\[//\]:.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text.replace("{user}", player_name(story))


def player_name(story: dict) -> str:
    return story.get("avatar", {}).get("name") or story.get("player_name") or "the player character"


def avatar_block(story: dict) -> str:
    av = story.get("avatar") or {}
    lines = [f"{label}: {av[key]}" for key, label in AVATAR_LABELS if av.get(key)]
    if not lines:
        return ""
    return (
        "=== PLAYER AVATAR (the player's own character; this is {user}) ===\n"
        + "\n".join(lines)
        + "\nKeep this character consistent. Only the player decides what this character does, says or thinks."
    )


class Built:
    """One turn's request, laid out so the START of it is identical from turn to turn.

    Both Claude's prompt cache and a local model's KV-cache reuse work on matching prefixes, so:
      1. core      - story prompt + avatar + engine rules   (changes almost never)
      2. extras    - user notes, then the memory summary    (changes rarely)
      3. messages  - history, append-only between summaries (grows by one exchange per turn)
      4. tail      - live state, attached to the NEWEST user message only (changes every turn)
    """

    def __init__(self, core: str, extras: str, messages: list[dict], tail: str):
        self.core, self.extras, self.messages, self.tail = core, extras, messages, tail

    def request(self) -> tuple[list[dict], list[dict]]:
        """(system blocks, messages) for a provider. `cache: True` marks a cache breakpoint."""
        system = [{"text": self.core, "cache": True}]
        if self.extras:
            system.append({"text": self.extras, "cache": True})
        msgs = [dict(m) for m in self.messages]
        # Breakpoint on the last narrator reply: everything up to it was sent last turn, so it is a cache hit.
        for i in range(len(msgs) - 2, -1, -1):
            if msgs[i]["role"] == "assistant":
                msgs[i]["cache"] = True
                break
        if self.tail and msgs and msgs[-1]["role"] == "user":
            msgs[-1]["content"] += "\n\n" + self.tail
        return system, msgs

    @property
    def system_text(self) -> str:
        return "\n\n".join(p for p in (self.core, self.extras) if p)


CONTENT_OFF = """\
=== CONTENT SETTING: standard (added by the app; always follow) ===
Keep sexual content non-explicit: romance, flirting and intimacy may be implied, but cut away or fade to black before anything graphic. Violence and horror follow the story prompt.
Sexual content involving anyone under 18 never appears, in any setting."""

CONTENT_ON = """\
=== CONTENT SETTING: adult (the player turned this on for this story; added by the app) ===
Mature content is allowed: explicit sexual content between consenting adult characters may be written when the player's moves lead there, with the same prose style as the rest of the story. Violence, gore and dark themes follow the story prompt.
Every character who appears in a sexual scene must be an adult (18 or older). Sexual content involving anyone under 18 never appears, in any setting, whatever the story prompt says."""


def content_block(story: dict) -> str:
    return CONTENT_ON if story.get("nsfw") else CONTENT_OFF


def cast_block(story: dict) -> str:
    """The author's characters: the narrator is told who they are (only for characters marked as author-defined)."""
    rows = []
    for c in story.get("cast") or []:
        if not c.get("author") or not (c.get("personality") or c.get("bio")):
            continue
        bits = [f"{c['name']}"]
        if c.get("appearance"):
            bits.append(f"Looks: {c['appearance']}")
        if c.get("personality"):
            bits.append(f"Personality: {c['personality']}")
        if c.get("bio"):
            bits.append(f"Background: {c['bio']}")
        rows.append(" | ".join(bits))
    return ("=== CAST (the story author's characters; keep them true to these notes) ===\n" + "\n".join(rows)) if rows else ""


def endings_block(story: dict) -> str:
    ends = [e for e in story.get("endings") or [] if e.get("title") or e.get("text")]
    if not ends:
        return ""
    lines = [f"{i + 1}. {e.get('title', '')}: {e.get('text', '')}".strip() for i, e in enumerate(ends)]
    out = ("=== POSSIBLE ENDINGS (the author's hidden notes: never reveal or announce them; let the story drift toward one only when the player's choices "
           "and the events lead there, otherwise ignore them) ===\n" + "\n".join(lines))
    t = story.get("target_ending", -1)
    if isinstance(t, int) and 0 <= t < len(ends):
        out += f"\nThe player has asked the story to work toward ending {t + 1} ({ends[t].get('title', '')}). Steer toward it gradually through events the player's choices can influence; never force it."
    return out


def build(story: dict) -> Built:
    use_state = story.get("use_state_block", True)
    rules = ENGINE_CORE + ("\n" + ENGINE_STATE if use_state else "")
    name = player_name(story)
    core = "\n\n".join(p for p in (story.get("base_prompt", "").strip(), avatar_block(story), cast_block(story), endings_block(story), images_block(story), rules, content_block(story)) if p)
    extras = []
    if story.get("prologue", "").strip():
        extras.append("=== PROLOGUE (events before the story begins; established background the narrator knows) ===\n" + story["prologue"].strip())
    if story.get("user_notes"):
        extras.append(
            "=== USER NOTES (standing instructions from the player; follow them every turn unless they "
            "would break the story's core rules) ===\n" + story["user_notes"]
        )
    if story.get("pinned_facts"):
        extras.append("=== PINNED FACTS (the player's corrections; they are true and override anything below) ===\n" + "\n".join("- " + f for f in story["pinned_facts"]))
    if story.get("summary"):
        extras.append("=== STORY SO FAR (memory: summary of older events) ===\n" + story["summary"])
    tail = ""
    if use_state and story.get("state"):
        tail = "[CURRENT STATE, for the narrator only; do not repeat it]\n" + json.dumps(story["state"], ensure_ascii=False)
    panel = tracker.tail_text(story)   # what the side panel shows, so the narrator keeps it consistent
    if panel:
        tail = (tail + "\n\n" if tail else "") + panel
    start = min(story.get("summary_upto", 0), len(story["turns"]))
    messages = [{"role": t["role"], "content": BEGIN_NOTE if _is_begin(story, t) else t["text"]} for t in story["turns"][start:]]
    opening = opening_for_model(story)
    if opening and start == 0:   # the opening is the story's first message (older, summarized stories no longer need it)
        messages = [{"role": "user", "content": "(The story begins.)"}, {"role": "assistant", "content": opening}] + messages
    # Prompts written for other platforms use {user} for the player's character.
    sub = lambda t: t.replace("{user}", name)
    return Built(sub(core), sub("\n\n".join(extras)), messages, sub(tail))


def split_state(full: str) -> tuple[str, dict]:
    """Return (narration without the state block, parsed state dict)."""
    matches = list(STATE_RE.finditer(full))
    update: dict = {}
    if matches:
        raw = matches[-1].group(1).strip()
        try:
            update = json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                try:
                    update = json.loads(m.group(0))
                except Exception:
                    update = {}
    text = STATE_RE.sub("", full).strip()
    if not matches:
        # Fallback: model forgot the <state> tags and ended the reply with a bare JSON object.
        for m in re.finditer(r"\{", text):
            try:
                obj = json.loads(text[m.start():].strip().rstrip("`").strip())
            except Exception:
                continue
            if isinstance(obj, dict) and obj:
                update, text = obj, text[: m.start()].rstrip()
                break
    # Smaller models sometimes put a label like "**State:**" in front of the block; drop it.
    text = FENCE_RE.sub("", text).strip()
    text = LABEL_RE.sub("", text).strip()
    return text, update if isinstance(update, dict) else {}


def apply_state(story: dict, upd: dict) -> None:
    st = story.setdefault("state", {})
    for key in ("when", "location", "sheet"):
        if isinstance(upd.get(key), str) and upd[key].strip():
            st[key] = upd[key].strip()
    hp = upd.get("hp")
    if isinstance(hp, (list, tuple)) and len(hp) == 2 and all(isinstance(x, (int, float)) for x in hp):
        st["hp"] = [hp[0], hp[1]]
    elif isinstance(hp, dict) and {"cur", "max"} <= hp.keys():
        st["hp"] = [hp["cur"], hp["max"]]
    if isinstance(upd.get("affinity"), dict):
        aff = st.setdefault("affinity", {})
        for name, val in upd["affinity"].items():
            if isinstance(val, (int, float)):
                aff[str(name)] = max(0, min(100, int(val)))
    if isinstance(upd.get("inventory"), list):
        st["inventory"] = [str(x) for x in upd["inventory"]]
    sug = upd.get("suggestions")
    story["suggestions"] = [str(s) for s in sug][:4] if isinstance(sug, list) else []


def snapshot(story: dict) -> dict:
    return copy.deepcopy({"state": story.get("state", {}), "suggestions": story.get("suggestions", []), "tracker": story.get("tracker") or tracker.blank()})


def restore(story: dict, snap: dict) -> None:
    story["tracker"] = snap.get("tracker") or tracker.blank()
    story["state"] = snap.get("state", {})
    story["suggestions"] = snap.get("suggestions", [])


SUMMARY_SYSTEM = (
    "You maintain the long-term memory of an interactive story. Given the existing summary and new story events, "
    "write the updated summary. Keep EVERY fact from the existing summary (who is who, relationships and bond levels, "
    "promises, injuries, items, abilities and milestones, plot threads, locations, deaths, time of day, turn markers like "
    "'[Turn:50]'); add the new events in order in the same style. Only if the whole summary would exceed about 2,500 words, "
    "compress the OLDEST entries (never the recent ones, and never drop names, numbers or consequences). Output only the summary."
)


def summary_request(old_summary: str, turns: list[dict]) -> list[dict]:
    transcript = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in turns)
    content = f"EXISTING SUMMARY:\n{old_summary or '(none yet)'}\n\nNEW EVENTS:\n{transcript}\n\nWrite the updated summary."
    return [{"role": "user", "content": content}]
