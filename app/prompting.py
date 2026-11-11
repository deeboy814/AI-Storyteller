"""Builds the prompt sent to the model each turn, and reads the state block back out."""
import copy
import json
import re

from . import appstatus, characters, localfix, store, tracker

ENGINE_CORE = """\
=== ENGINE RULES (added by the app; always follow) ===
You are the narrator / game master of an interactive story. Each turn the player says what their character does or says.
Narrate what happens next, following the world, characters, rules, stats and prose style defined above. Never decide the
player character's actions or words beyond what the player stated. Keep the world, characters and stats consistent with
the story so far. Use the exact response format the story prompt above defines (fill in real values; never copy
placeholders or templates literally).
"""

# Story option "narrator_acts": the story prompt lets the narrator decide some of the player character's routine, in-character actions (it says how far).
ACTS_RULE = ("You may decide some of the player character's routine, in-character actions, thoughts and short lines, as the story prompt above allows, based on their "
             "backstory, key events, stats, skills, quests and the story so far. Always follow what the player stated and build around it, and never make a major or "
             "irreversible decision for the player character, or spend or assign points, unless the story prompt says otherwise.")

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
placed just before the state block if there is one, otherwise at the very end. Most turns should have NO scene tag, unless the story prompt \
above asks for scene pictures more often: then follow the story prompt's frequency.
{CONTENT}
The tag is hidden from the player and read by an image generator."""
SCENE_SFW = "Keep it safe for work: no nudity or explicit content; for intimate moments pick a different frame (a hand, the room, a glance)."
SCENE_ADULT = "Adult content is allowed in this story: an intimate moment may be shown as it happens, between adults only."


SCENE_RULE_OFTEN = """\
=== SCENE IMAGES (app feature, this story asks for them often) ===
Add a scene picture to most replies: end the reply with
<scene>one sentence describing the single best frame: setting, the characters present by name (one or two is best), their poses and expressions, what they wear, the lighting</scene>
placed just before the state block if there is one, otherwise at the very end. Skip it only for a short back-and-forth with nothing new to look at, or when the reply only moves the
characters into one of the PLACES in the PICTURES AVAILABLE note (the place picture covers that). A place picture and a scene frame can appear in the same reply when something dramatic happens there.
{CONTENT}
The tag is hidden from the player and read by an image generator."""


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


# Compact portrait tag: the narrator writes [[Name/mood]] (fewer tokens than ![](portrait:Name/mood)); the app stores and shows the long form, accepts both,
# and turns the long form back into the short one when it sends the history to the narrator again.
_COMPACT_TAG = re.compile(r"\[\[([^\]/\n|]{1,40})/([^\]\n|]{1,40})\]\]")
_LONG_TAG = re.compile(r"!\[\]\(portrait:([^)/\n]+)/([^)\n]+)\)")


def expand_tags(text: str) -> str:
    return _COMPACT_TAG.sub(lambda m: f"![](portrait:{m.group(1).strip()}/{m.group(2).strip()})", text)


def compact_tags(text: str) -> str:
    return _LONG_TAG.sub(lambda m: f"[[{m.group(1).strip()}/{m.group(2).strip()}]]", text)


def portrait_lines(story: dict) -> list[str]:
    """Who has which portrait moods. Characters with exactly the same moods share one line (a big cast would repeat the same long list)."""
    groups: dict[tuple, list[str]] = {}
    avail = []
    for c in story.get("cast") or []:
        if not c.get("moods"):
            continue
        if c.get("player"):
            avail.append(f"{c['name']} (the player character: use this tag only if the story prompt above says to show the player's portrait): {', '.join(sorted(c['moods']))}")
        else:
            groups.setdefault(tuple(sorted(c["moods"])), []).append(c["name"])
    return [f"{', '.join(names)}: {', '.join(moods)}" for moods, names in sorted(groups.items(), key=lambda kv: kv[1][0])] + avail


def pictures_available(story: dict) -> str:
    """The lists that change as pictures get drawn (moods per character, places with pictures). They go at the END of the newest message, not in the
    cached start of the prompt, so drawing a new mood no longer makes the whole prompt be paid for again."""
    art = story.get("art") or {}
    out = []
    lines = portrait_lines(story)
    if lines and art.get("portraits", True):
        out.append("Portraits (characters and the moods available):\n" + "\n".join(lines))
    rows = _place_rows(story)
    if rows:
        out.append("Places (fixed pictures):\n" + "\n".join(rows))
    if out and extended_moods_on(story):
        out.append("(Extended mood set: prefer the most specific fitting mood over normal or smile.)")
    return ("[PICTURES AVAILABLE NOW, for the narrator only]\n" + "\n".join(out)) if out else ""


def images_block(story: dict) -> str:
    """Instructions for the app's image features. Stable text, so it does not disturb prompt caching (the changing lists are in pictures_available)."""
    art = story.get("art") or {}
    parts = [IMAGE_GUARD]
    avail = portrait_lines(story)
    if avail and art.get("portraits", True):
        if art.get("exact_moods"):
            mood_rule = ("Use the names and moods listed in that note, spelled exactly, whenever one of them is an exact match. "
                         "Name the character's real emotion in that line as precisely as you can, in one or two words. Use a listed mood ONLY when it is an exact match: "
                         "a listed mood that is merely close is NOT a match (for example smile is not mischievous, teasing, smug or proud; fear is not worried or anxious; "
                         "surprise is not shocked, stunned or flustered; sad is not guilty or lonely). In those cases use the precise word as a new mood name (one or two "
                         "lowercase words): the player will be offered to draw it. Reuse a mood name you have already asked for instead of inventing near-duplicates. ")
        else:
            mood_rule = ("Use the names and moods listed in that note, spelled exactly, whenever one of them fits. Only if NONE of a character's listed moods fits what they feel, "
                         "you may use a new one- or two-word mood name (for example terrified): the player will be offered to draw it. "
                         "Always prefer a listed mood, even if it is only the closest fit; ask for a new mood only when no listed one comes close. ")
        parts.append(
            "=== PORTRAITS (character images available in this app; this replaces any other instruction about images) ===\n"
            "Which characters have which moods is listed in the PICTURES AVAILABLE note at the end of each player message.\n"
            "Directly above each line of dialogue from a listed character, output one image tag on its own line: "
              "[[Name/mood]], choosing the mood that best fits what they feel or do in that line. "
            + mood_rule
            + (action_rule(story) + " " if action_moods_on(story) else "")
            + "Skip the tag for anyone not listed. If the story prompt above asks for portraits more often (for example for silent reactions "
              "or first appearances), follow it: the tag format is the same."
        )
    if art.get("newchar", False):
        parts.append(NEWCHAR_RULE)
    pl = places_block(story)
    if pl:
        parts.append(pl)
    if art.get("auto"):
        rule = SCENE_RULE_OFTEN if art.get("scene_often") else SCENE_RULE
        parts.append(rule.replace("{CONTENT}", SCENE_ADULT if story.get("nsfw") else SCENE_SFW))
    return "\n\n".join(parts)


def _place_rows(story: dict) -> list[str]:
    return sorted(f"- {p['name']}" + (f": {p['description']}" if (p.get("description") or "").strip() else "") for p in story.get("places") or [] if p.get("url"))


def species_block(story: dict) -> str:
    """What is true of each species that has story traits (Art → Species), so the narrator keeps abilities, weaknesses and customs consistent."""
    rows = [f"- {sp.get('name', k)}: {sp['traits'].strip()}" for k, sp in (story.get("species") or {}).items() if (sp.get("traits") or "").strip()]
    return ("=== SPECIES (what is true of each kind of being in this story; keep it consistent) ===\n" + "\n".join(rows)) if rows else ""


def places_block(story: dict) -> str:
    """Static place pictures the narrator can show: rooms and locations the story returns to again and again (only places that have a picture)."""
    if not _place_rows(story):
        return ""
    return ("=== PLACES (fixed pictures of locations the story returns to often; the list is in the PICTURES AVAILABLE note at the end of each player message) ===\n"
            "When a scene takes place in, or moves into, one of these places, put one tag on its own line near the start of that scene: ![](place:Name) "
              "(the name spelled exactly as in that list). Use it once when the characters arrive there, not again in every reply while they stay, and again when they come back. "
              "These are for routine, repeated settings. For anything else, or a dramatic moment inside one of them, use a scene frame instead if the app asks for those.")


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
    return text.replace("{{user}}", player_name(story)).replace("{user}", player_name(story))


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
        + ("\nKeep this character consistent. The player decides what this character does, says or thinks; the narrator may fill in routine, in-character actions as the story prompt allows."
           if story.get("narrator_acts") else "\nKeep this character consistent. Only the player decides what this character does, says or thinks.")
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


def names_block(story: dict) -> str:
    """The author's rules for who may use which name for a character (a private first name, titles for leadership...). Sent every turn when filled in."""
    rows = []
    for c in story.get("cast") or []:
        a = (c.get("address") or "").strip()
        if not a:
            continue
        aka = c.get("aka") or []
        rows.append(f"- {c['name']}" + (f" (also known as: {', '.join(aka)})" if aka else "") + f": {a}")
    return ("=== NAMES AND FORMS OF ADDRESS (the story author's rules; follow them strictly, in dialogue AND in narration) ===\n" + "\n".join(rows)) if rows else ""


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


def scene_due(story: dict) -> bool:
    """False while the story is inside its "turns between scene pictures" gap (art.scene_gap, default 1 = no limit), counting replies since the last one that carried a scene."""
    gap = int((story.get("art") or {}).get("scene_gap") or 1)
    if gap <= 1:
        return True
    since = 0
    for t in reversed(story.get("turns") or []):
        if t.get("role") != "assistant":
            continue
        if t.get("scene"):
            break
        since += 1
    else:
        return True                          # no scene picture yet
    return since + 1 >= gap


def _past_first_turn(story: dict) -> bool:
    """True once the narrator has already replied: the story prompt is no longer fresh, so the app has to carry the reminder."""
    return any(t.get("role") == "assistant" for t in story.get("turns") or [])


def extended_moods_on(story: dict) -> bool:
    art = story.get("art") or {}
    return bool(art.get("extended_moods") and art.get("portraits", True) and _past_first_turn(story)
                and any(len(c.get("moods") or {}) >= 3 for c in story.get("cast") or []))


def _fancy_moods(story: dict) -> list[str]:
    """Mood names the characters have beyond the plain ones (normal / smile), for the reminder's examples."""
    out: dict[str, int] = {}
    for c in story.get("cast") or []:
        for m in c.get("moods") or {}:
            b = re.sub(r"\s*\d+$", "", m.strip().lower())
            if b not in ("normal", "default", "smile"):
                out[b] = out.get(b, 0) + 1
    return [m for m, _ in sorted(out.items(), key=lambda kv: -kv[1])][:8]


def action_moods_on(story: dict) -> bool:
    """Art → Style & options → "Action moods" (on unless the player switched it off): in action scenes the narrator prefers the action mood pictures."""
    art = story.get("art") or {}
    return bool(art.get("action_moods", True) and art.get("portraits", True))


def _action_names(story: dict, short: bool = False) -> str:
    base = []
    for m in characters.ACTION_MOODS:
        b = re.sub(r"\s*\d+$", "", m)
        if b not in base:
            base.append(b)
    if story.get("nsfw"):
        base += [m for m in characters.ADULT_ACTION_MOODS if m not in base]
    return ", ".join(base)


def action_rule(story: dict) -> str:
    """The standing rule (stable text, so it does not disturb prompt caching)."""
    adult = " and, because adult content is allowed in this story, intimate scenes" if story.get("nsfw") else ""
    return ("ACTION SCENES: during fights, chases, danger and other physical action" + adult + ", prefer the ACTION moods over calm expression moods in the portrait tags of the "
            "characters involved, choosing the one that matches what that character is physically doing in that line. The standard action moods are: " + _action_names(story) + ". "
            "Use one the character already has (see the PICTURES AVAILABLE note); if they have none that fits, use the standard name from this list (the player will be offered to draw it). "
            "Keep using action moods through the whole action sequence, and return to expression moods (smile, sad, angry...) when it is over.")


_ACTION_RE = re.compile(r"\b(fight\w*|attack\w*|struck|strikes?|striking|slash\w*|stab\w*|punch\w*|kick\w*|dodg\w*|parr(?:y|ied|ies)|block\w*|charg\w*|chas(?:e|ed|es|ing)|flee\w*|fled|"
                        r"battle\w*|combat|duel\w*|ambush\w*|explo\w+|gunfire|shoot\w*|shot|arrows?|spells?|blood\w*|wound\w*|sword|blade|knife|weapons?|ambush|brawl\w*|clash\w*|lunge\w*|swing\w*|"
                        r"tackle\w*|grappl\w*|collaps\w*|screams?|roar\w*)\b", re.IGNORECASE)
_ADULT_SCENE_RE = re.compile(r"\b(kiss\w*|undress\w*|naked|nude|moan\w*|gasp\w*|caress\w*|arous\w*|lust\w*|intimate|intimacy|sex\w*|bed|lingerie|shower\w*|bath\w*|stripp?\w*|climax\w*|thrust\w*|embrac\w*)\b", re.IGNORECASE)


def _in_action_scene(story: dict) -> bool:
    """A cheap guess from the player's last move and the narrator's last reply: is the story in an action scene (or, in an adult story, an intimate one) right now?"""
    turns = story.get("turns") or []
    recent = " ".join(t.get("text", "")[-1800:] for t in turns[-2:])
    hits = len(_ACTION_RE.findall(recent))
    if story.get("nsfw"):
        hits += len(_ADULT_SCENE_RE.findall(recent))
    return hits >= 3


def picture_reminder(story: dict) -> str:
    """A short per-turn reminder placed right before the narrator writes (the long rules sit far back and a long history of replies that
    followed the listed moods pulls the narrator toward repeating them). Only for stories with "Ask for exact moods" and/or "Scene picture most turns" on, or with Places pictures."""
    art = story.get("art") or {}
    lines = []
    if art.get("exact_moods") and art.get("portraits", True) and any(c.get("moods") for c in story.get("cast") or []):
        lines.append("In each portrait tag name the character's exact feeling. A listed mood that is only close does not count: "
                     "for teasing, mischievous or smug do not use a smile mood; for worried or anxious do not use fear; for shocked, flustered or stunned do not use surprise; "
                     "for guilty or lonely do not use sad. Use the precise word as a new mood instead (for example [[Name/flustered]]) whenever the moment clearly "
                     "calls for one, and reuse names you already asked for.")
    if extended_moods_on(story):
        ex = _fancy_moods(story)
        lines.append("The characters have an extended range of mood pictures (see PICTURES AVAILABLE). Use the whole range: for every portrait tag pick the most specific mood that fits that line"
                     + (f" (for example {', '.join(ex[:6])})" if ex else "") + " instead of falling back on normal or smile, and vary the moods from line to line.")
    if action_moods_on(story) and any(c.get("moods") for c in story.get("cast") or []) and _past_first_turn(story) and _in_action_scene(story):
        lines.append("The story is in an action scene right now: for the portrait tags of the characters involved use ACTION moods (" + _action_names(story) + ") that match what each is physically "
                     "doing, not calm expression moods, until the action is over.")
    if art.get("auto") and not scene_due(story):
        lines.append("Do NOT end this reply with a <scene> frame; the app is spacing scene pictures out.")
    elif art.get("auto") and art.get("scene_often"):
        has_places = any(p.get("url") for p in story.get("places") or [])
        lines.append("End this reply with a <scene>…</scene> frame unless it is only a short back-and-forth" + (" or only moves the characters into a listed place" if has_places else "")
                     + ". One or two named characters, their poses, expressions, clothing and the lighting.")
    if any(p.get("url") for p in story.get("places") or []):
        lines.append("If this reply starts a scene in (or returns to) one of the listed PLACES, put its ![](place:Name) tag near the start.")
    return ("[PICTURE REMINDER, for the narrator only] " + " ".join(lines)) if lines else ""


def build(story: dict, beats_note: str = "", local: bool = False) -> Built:
    use_state = story.get("use_state_block", True)
    core_rules = ENGINE_CORE.replace("Never decide the\nplayer character's actions or words beyond what the player stated.", ACTS_RULE) if story.get("narrator_acts") else ENGINE_CORE
    rules = core_rules + ("\n" + ENGINE_STATE if use_state else "")
    drawn = appstatus.last_block(story) if story.get("app_status") else None     # the app writes the status line, so the narrator leaves it out
    if drawn:
        rules += "\n\n" + appstatus.RULE
    name = player_name(story)
    core = "\n\n".join(p for p in (story.get("base_prompt", "").strip(), avatar_block(story), cast_block(story), species_block(story), names_block(story), endings_block(story), images_block(story), rules, content_block(story)) if p)
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
    if beats_note:     # story beats that are due (the app's schedule); hidden from the player
        tail = (tail + "\n\n" if tail else "") + beats_note
    if drawn:      # the narrator no longer sees the status line in its earlier replies, so it is told the current values here
        tail = (tail + "\n\n" if tail else "") + "[CURRENT STATUS LINE, drawn by the app: never write it, but keep the story consistent with these values]\n" + drawn
    pic = pictures_available(story)
    if pic:
        tail = (tail + "\n\n" if tail else "") + pic
    mem = memory_block(story)
    if mem:
        tail = (tail + "\n\n" if tail else "") + mem
    rem = picture_reminder(story)
    if rem:
        tail = (tail + "\n\n" if tail else "") + rem
    if local:      # Local-model mode: steadier prose from code (no AI call)
        for note in (localfix.repeat_note(story), localfix.length_note(story)):
            if note:
                tail = (tail + "\n\n" if tail else "") + note
    start = min(story.get("summary_upto", 0), len(story["turns"]))
    labels = appstatus.rules.expected_labels(story) if drawn else []
    messages = [{"role": t["role"], "content": BEGIN_NOTE if _is_begin(story, t) else (compact_tags(appstatus.strip(t["text"], labels) if drawn else t["text"]) if t["role"] == "assistant" else t["text"])} for t in story["turns"][start:]]
    opening = opening_for_model(story)
    if opening and start == 0:   # the opening is the story's first message (older, summarized stories no longer need it)
        messages = [{"role": "user", "content": "(The story begins.)"}, {"role": "assistant", "content": opening}] + messages
    # Prompts written for other platforms use {user} for the player's character.
    sub = lambda t: t.replace("{{user}}", name).replace("{user}", name)          # {{user}} is how other apps write it
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


# Character memory (Settings → Memory & cost): each character's key events live on their own card and are sent to the narrator only while
# that character is in the scene, so the shared summary can stay short and cover the plot, places, items and open threads.
SUMMARY_SYSTEM_LEAN = (
    "You maintain the long-term memory of an interactive story. Given the existing summary and new story events, write the updated summary. "
    "Keep the plot, locations, time of day, items, abilities, groups and open plot threads, who is who, deaths and turn markers like '[Turn:50]', "
    "and add the new events in order in the same style. Each character's personal key events are also logged separately (shown below as CHARACTER LOGS) "
    "and given to the narrator whenever that character is present: do NOT repeat details that appear in those logs, and drop them from the existing "
    "summary when the logs already hold them (keep a relationship only if it matters to the plot as a whole). "
    "Keep the whole summary under about 1,200 words: compress the OLDEST entries first, never the recent ones, and never drop names, numbers or consequences "
    "that are not in the logs. Lay it out exactly as: a first line 'SUMMARY OF STORY SO FAR', then 'Setting and status:' (a short paragraph: place, time, stats, "
    "relationships, open plot threads), then 'Events in order:' followed by numbered entries (1., 2., 3. ...), one per story beat. Output only the summary."
)

# Append-only summary: the app keeps the "Events in order:" list and only asks the model for the NEW entries plus a refreshed status paragraph.
EVENTS_HEAD = "Events in order:"
SUMMARY_APPEND_SYSTEM = (
    "You maintain the long-term memory of an interactive story. You are given the CURRENT STATUS paragraph, the LAST FEW EVENT ENTRIES, and NEW STORY TEXT. "
    "Reply in exactly this form and nothing else:\n"
    "STATUS:\n<the status paragraph, updated: keep everything still true and add what changed (place, time of day, stats and numbers, relationships and bond "
    "levels, promises, injuries, items, open plot threads); no more than about 250 words; do not describe characters' personalities, those are kept elsewhere>\n"
    "EVENTS:\n<one numbered entry per distinct story beat in the NEW STORY TEXT, continuing the numbering from the last entry, one to three sentences each; "
    "keep names, numbers, exact promises and consequences; do not repeat earlier entries>"
)
SUMMARY_COMPACT_SYSTEM = (
    "You compress the older part of an interactive story's event list. Merge the given numbered entries into at most 8 short numbered entries (or one short "
    "paragraph if there are few), keeping order, names, numbers, promises, deaths, injuries, discoveries and consequences; drop small talk and scenery. Output only the result."
)


def split_summary(text: str):
    """(head, characters section, events section) of a summary laid out with 'Events in order:'; None when the summary has another layout."""
    import re as _re
    if not text or EVENTS_HEAD not in text:
        return None
    pre, _, events = text.partition(EVENTS_HEAD)
    m = _re.search(r"^Key characters:\s*$", pre, _re.MULTILINE)
    head, chars = (pre[:m.start()], pre[m.start():]) if m else (pre, "")
    return head.strip(), chars.strip(), events.strip()


def join_summary(head: str, chars: str, events: str) -> str:
    return "\n\n".join(p for p in (head.strip(), chars.strip(), EVENTS_HEAD + "\n" + events.strip()) if p)


def event_entries(events: str) -> tuple[str, list[str]]:
    """(condensed intro paragraph, the numbered entries) of an events section."""
    import re as _re
    parts = _re.split(r"(?m)^(?=\d+\.\s)", events)
    intro = parts[0].strip() if parts and not _re.match(r"\d+\.\s", parts[0]) else ""
    nums = [p.strip() for p in parts if _re.match(r"\d+\.\s", p)]
    return intro, nums


def summary_append_request(head: str, last_entries: list[str], turns: list[dict], next_no: int) -> list[dict]:
    transcript = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in turns)
    content = (f"CURRENT STATUS:\n{head}\n\nLAST FEW EVENT ENTRIES:\n{chr(10).join(last_entries) or '(none yet)'}\n\n"
               f"The next entry is number {next_no}.\n\nNEW STORY TEXT:\n{transcript}")
    return [{"role": "user", "content": content}]


def char_memory_on() -> bool:
    cfg = store.load_config()
    return bool(cfg.get("char_memory", True)) and cfg.get("events_mode", "every") == "every"


def summary_system() -> str:
    return SUMMARY_SYSTEM_LEAN if char_memory_on() else SUMMARY_SYSTEM


def _ev_lines(ch: dict, recent: int) -> list[str]:
    """A character's condensed history lines, then their newest `recent` single events."""
    evs = ch.get("events") or []
    old = [e for e in evs if e.get("digest")]
    new = [e for e in evs if not e.get("digest")][-recent:]
    out = []
    for e in old + new:
        when = (e.get("when") or "").strip()
        out.append(f"- {'(' + when + ') ' if when else ''}{(e.get('text') or '').strip()[:1300 if e.get('digest') else 260]}")
    return out


def logs_text(story: dict, recent: int = 12) -> str:
    """Every character's log, for the summary writer (so it can leave those details out of the shared summary)."""
    rows = []
    for c in story.get("cast") or []:
        lines = _ev_lines(c, recent)
        if lines:
            rows.append(f"{c['name']}:\n" + "\n".join(lines))
    return "\n".join(rows)


def scene_cast(story: dict) -> list[dict]:
    """Characters with a log who are in the scene now, most relevant first: named (full name, first name or an 'also called' name) in the player's newest move
    (counts six times) or the narrator's reply before it. The player's own character is always first."""
    turns = story.get("turns") or []
    user = turns[-1]["text"] if turns and turns[-1]["role"] == "user" else ""
    reply = " ".join(t["text"] for t in turns[-2:] if t["role"] == "assistant")
    scored = []
    for c in story.get("cast") or []:
        if not c.get("events"):
            continue
        if c.get("player"):
            scored.append((10**6, c))
            continue
        pat = "|".join(re.escape(n) for n in {c["name"], c["name"].split()[0], *(c.get("aka") or [])} if n)
        n = 6 * len(re.findall(rf"\b(?:{pat})\b", user, re.IGNORECASE)) + len(re.findall(rf"\b(?:{pat})\b", reply, re.IGNORECASE)) if pat else 0
        if n:
            scored.append((n, c))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])]


MEMORY_SIZES = {"small": (2000, 700), "medium": (3200, 1000), "large": (5000, 1500)}   # (whole note, one character) in characters


def _fmt_ev(e: dict, cap: int) -> str:
    when = (e.get("when") or "").strip()
    return f"- {'(' + when + ') ' if when else ''}{(e.get('text') or '').strip()[:cap]}"


def memory_block(story: dict) -> str:
    """What the narrator is reminded of about the characters in this scene (newest turn only, so the cached part of the prompt stays unchanged). Each
    character gets their newest events first, then their condensed history from the newest line backwards, until their share of the size runs out."""
    if not char_memory_on():
        return ""
    total, per = MEMORY_SIZES.get(store.load_config().get("char_memory_size", "medium"), MEMORY_SIZES["medium"])
    rows, size = [], 0
    for c in scene_cast(story)[:7]:
        evs = c.get("events") or []
        loose = [_fmt_ev(e, 240) for e in evs if not e.get("digest")][-(3 if c.get("player") else 4):]
        room = per - sum(len(x) for x in loose) - len(c["name"]) - 2
        hist: list[str] = []
        for e in reversed([e for e in evs if e.get("digest")]):
            line = _fmt_ev(e, 420)
            if room - len(line) < 0:
                break
            hist.append(line); room -= len(line)
        lines = hist[::-1] + loose
        if not lines:
            continue
        row = f"{c['name']}:\n" + "\n".join(lines)
        if size + len(row) > total:
            continue                 # does not fit: a less relevant character with a shorter entry still may
        rows.append(row); size += len(row)
    return ("[CHARACTER MEMORY, for the narrator only: key past events of the characters in this scene; stay consistent with them, do not recite them]\n"
            + "\n".join(rows)) if rows else ""


def summary_request(old_summary: str, turns: list[dict], logs: str = "") -> list[dict]:
    transcript = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in turns)
    extra = f"CHARACTER LOGS (kept separately; do not repeat these details):\n{logs}\n\n" if logs else ""
    content = f"EXISTING SUMMARY:\n{old_summary or '(none yet)'}\n\n{extra}NEW EVENTS:\n{transcript}\n\nWrite the updated summary."
    return [{"role": "user", "content": content}]
