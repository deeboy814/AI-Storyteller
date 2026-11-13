"""Image categories and the picture types in them. A picture type (a "mood": smile, combat, doggy-style...) has a look (what to draw), a frame, tag-style words, a note for the
narrator, an adult (NSFW) flag and a category. The categories (Expressions, Actions, Positions, Finishes, Other adult by default) are the player's to rename, change, add and delete; each one is also
a batch-generation set ("Draw all of …"). The built-in picture types live in main.py (MOOD_LOOKS and friends) and characters.py; what the player changes is kept in the settings as
cfg["mood_defs"] (only the changed fields) and cfg["image_categories"]. This module answers the questions every other part of the app asks: which category is it in, is it adult,
is it an action shot, which names does the narrator get."""
import copy
import re

from . import characters

SHOTS = ("face", "body")          # a face-and-shoulders portrait, or a three-quarter action shot
SHOT_NAMES = {"face": "Portrait (a face and a feeling)", "body": "Action shot (a body in motion)"}
EXPRESSION: set = set()           # main.py fills this with its EXPRESSION_MOODS (the built-in face-and-feeling names)
BUILTIN_NAMES: set = set()        # main.py fills this with every name it has a built-in look for
_OLD_KINDS = {"expression": "expressions", "action": "actions", "position": "positions", "finish": "finishes", "adult": "adult"}     # the first version of this feature called them kinds
_ALIASES = {"moods": "expressions"}                                                                                                            # an id used briefly in an earlier version


def base(mood: str) -> str:
    return re.sub(r"\s*\d+$", "", str(mood).strip().lower())


def clean_name(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 '\-]", "", str(name).lower())).strip()[:30]


def default_categories() -> list[dict]:
    return copy.deepcopy(characters.DEFAULT_CATEGORIES)


# ---------------- categories ----------------
def categories(cfg: dict | None) -> list[dict]:
    """The player's categories (or the defaults), cleaned."""
    raw = (cfg or {}).get("image_categories")
    out, seen = [], set()
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        cid = re.sub(r"[^a-z0-9_-]", "", str(c.get("id") or "").lower())[:24]
        if not cid or cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": re.sub(r"\s+", " ", str(c.get("name") or cid)).strip()[:30] or cid, "nsfw": bool(c.get("nsfw")),
                    "shot": c.get("shot") if c.get("shot") in SHOTS else "body", "note": re.sub(r"\s+", " ", str(c.get("note") or "")).strip()[:900]})
    return out or default_categories()


def category_def(cfg: dict | None, cid: str) -> dict | None:
    return next((c for c in categories(cfg) if c["id"] == cid), None)


def def_of(cfg: dict | None, mood: str) -> dict:
    d = ((cfg or {}).get("mood_defs") or {}).get(base(mood))
    return d if isinstance(d, dict) else {}


def builtin_category(name: str) -> str:
    b = base(name)
    if b in characters.ADULT_POSITIONS:
        return "positions"
    if b in characters.ADULT_FINISHES:
        return "finishes"
    if b in characters.BUILTIN_ACTIONS_ADULT:
        return "adult"
    if b in {base(m) for m in characters.ACTION_MOODS}:
        return "actions"
    if b in EXPRESSION or not EXPRESSION:
        return "expressions"
    return "actions"          # a name the app has never heard of is drawn as an action picture


def category_of(cfg: dict | None, mood: str) -> str:
    """The id of the category a picture type is in (the player's choice, else where it is built in). A deleted category sends its types to the first one that is left."""
    d = def_of(cfg, mood)
    cid = d.get("category") or _OLD_KINDS.get(d.get("kind", ""), "") or builtin_category(mood)
    cats = categories(cfg)
    ids = {c["id"] for c in cats}
    if cid not in ids and _ALIASES.get(cid) in ids:
        cid = _ALIASES[cid]
    if cid in ids:
        return cid
    return builtin_category(mood) if builtin_category(mood) in ids else cats[0]["id"]


def is_builtin(name: str) -> bool:
    return base(name) in BUILTIN_NAMES


def is_nsfw(cfg: dict | None, mood: str) -> bool:
    """An adult picture type: drawn with the adult wording (and by the adult picture maker when one is set), and shown to the narrator only in stories with adult content on."""
    d = def_of(cfg, mood)
    if "nsfw" in d:
        return bool(d["nsfw"])
    cid = category_of(cfg, mood)
    c = category_def(cfg, cid)
    if c and c["nsfw"]:
        return True                                  # the whole category is adult
    if cid in ("positions", "finishes", "adult"):
        return False                                 # the player took the adult flag off these categories
    return base(mood) in characters.ADULT_ONLY       # the built-in adult actions (nude, undressing...)


def is_action(cfg: dict | None, mood: str) -> bool:
    """Drawn as a three-quarter action shot (the category's shot is "body") rather than a face portrait."""
    c = category_def(cfg, category_of(cfg, mood))
    return (c["shot"] if c else "body") == "body"


def shows_body(cfg: dict | None, mood: str) -> bool:
    """An adult picture of the body: no everyday clothes in the look (positions, finishes, nude, undressing...). An adult face picture (aroused) keeps their usual outfit."""
    return is_nsfw(cfg, mood) and is_action(cfg, mood) and not (base(mood) in characters.BUILTIN_CLOTHED and "nsfw" not in def_of(cfg, mood))


def naked_kind(cfg: dict | None, mood: str) -> bool:
    """Picture types that show the character without clothes (the positions and finishes categories, and the plain nude ones)."""
    return category_of(cfg, mood) in ("positions", "finishes") or base(mood) in ("nude", "climaxing", "showering", "bathing", "afterglow")


def builtin_names_in(cid: str) -> list[str]:
    if cid == "positions":
        return list(characters.ADULT_POSITIONS)
    if cid == "finishes":
        return list(characters.ADULT_FINISHES)
    if cid == "adult":
        return list(characters.BUILTIN_ACTIONS_ADULT)
    if cid == "actions":
        out = []
        for m in characters.ACTION_MOODS:
            if base(m) not in out:
                out.append(base(m))
        return out
    if cid == "expressions":
        out = []
        for lst in (characters.DEFAULT_SETS["basic"], characters.DEFAULT_SETS["extended"]):
            for m in lst:
                b = base(m)
                if b not in out and builtin_category(b) == "expressions":
                    out.append(b)
        return out
    return []


def names_in(cfg: dict | None, cid: str, nsfw_story: bool) -> list[str]:
    """The picture types in a category (built in, then the player's own), without the adult ones when the story does not allow adult content."""
    out = [m for m in builtin_names_in(cid) if category_of(cfg, m) == cid]
    for k in ((cfg or {}).get("mood_defs") or {}):
        if category_of(cfg, k) == cid and k not in out:
            out.append(k)
    # built-in names that exist only in the draw sets the player kept (for example a mood the narrator asked for) are not categories' business: they stay where they are
    if not nsfw_story:
        out = [m for m in out if not is_nsfw(cfg, m)]
    return out


def draw_names(cfg: dict | None, cid: str, nsfw_story: bool) -> list[str]:
    """What a batch ("All: <category>") draws: the picture names as the draw sets write them (smile 1, smile 2, combat 1...), then every other picture in the category."""
    sets = (cfg or {}).get("mood_sets") or characters.DEFAULT_SETS
    out = []
    for key in [k for k in ("basic", "extended", "action", "action_adult") if k in sets] + [k for k in sets if k not in ("basic", "extended", "action", "action_adult")]:
        for m in sets.get(key, []):
            m = str(m).strip().lower()
            if m and m not in out and category_of(cfg, m) == cid and (nsfw_story or not is_nsfw(cfg, m)):
                out.append(m)
    have = {base(m) for m in out}
    out += [b for b in names_in(cfg, cid, nsfw_story) if b not in have]
    return out


def set_names(cfg: dict | None, set_id: str, nsfw_story: bool) -> list[str]:
    """The names to draw for a batch-generation set: the player's Basic / Extended lists, or every picture of a category ("cat:<id>")."""
    if set_id.startswith("cat:"):
        return draw_names(cfg, set_id[4:], nsfw_story)
    return [str(m).strip().lower() for m in ((cfg or {}).get("mood_sets") or characters.DEFAULT_SETS).get(set_id, []) if str(m).strip()]


def custom_names(cfg: dict | None, cid: str | None = None) -> list[str]:
    """Picture types the player made (not built in), optionally of one category."""
    out = []
    for k, d in ((cfg or {}).get("mood_defs") or {}).items():
        if isinstance(d, dict) and (cid is None or category_of(cfg, k) == cid) and not is_builtin(k):
            out.append(k)
    return out


def notes(cfg: dict | None, nsfw_story: bool) -> list[tuple[str, str, str]]:
    """(name, category name, note) for every picture type that has a note for the narrator."""
    out = []
    for k, d in ((cfg or {}).get("mood_defs") or {}).items():
        if isinstance(d, dict) and str(d.get("note") or "").strip() and (nsfw_story or not is_nsfw(cfg, k)):
            c = category_def(cfg, category_of(cfg, k))
            out.append((k, c["name"] if c else "", str(d["note"]).strip()))
    return out


def clean_def(d: dict) -> dict:
    """Only the fields the editor knows, trimmed."""
    out = {}
    for f, n in (("look", 600), ("framing", 600), ("tags", 600), ("framing_tags", 300), ("note", 300)):
        v = re.sub(r"\s+", " ", str((d or {}).get(f) or "")).strip()[:n]
        if v:
            out[f] = v
    cat = (d or {}).get("category") or _OLD_KINDS.get((d or {}).get("kind", ""), "")
    if cat:
        out["category"] = re.sub(r"[^a-z0-9_-]", "", str(cat).lower())[:24]
    if "nsfw" in (d or {}):
        out["nsfw"] = bool(d["nsfw"])
    return out
