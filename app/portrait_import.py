"""Find portrait packs on disk: <root>/[<story title>/]<Character>/<mood>.<ext>, one folder per character.

Rules (shown to the user in a preview before anything is copied):
  - a folder of pictures named for moods ("Angry 1.webp", "Arm Link.webp") is a CHARACTER; the folder name is their name;
  - a folder named like "Sub-NPCS" / "Extras" holds ONE picture per minor character; each file name is the character's name;
  - folders named like "Intro", "Webtoon", "Gallery", "CG", "Scenes" are not characters and are skipped.
Mood names are the file names, lower-cased. "scorn" next to "scorn 2" is read as "scorn 1".
"""
import re
from pathlib import Path

IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SINGLE_RE = re.compile(r"sub[\s_-]*npcs?|extras?|minor|crowd|side[\s_-]*characters?", re.IGNORECASE)
INTRO_RE = re.compile(r"intro|webtoon|prologue|opening|prelude", re.IGNORECASE)
SKIP_RE = re.compile(r"\bcgs?\b|gallery|scenes?|backgrounds?|logos?|cover|icons?|ui\b", re.IGNORECASE)


def natural_key(p: Path):
    """It2 before It10."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def _images(d: Path) -> list[Path]:
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix.lower() in IMG_EXT)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def norm_mood(stem: str) -> str:
    return re.sub(r"[\s_]+", " ", stem.strip()).lower()


def fix_numbering(moods: dict[str, Path]) -> dict[str, Path]:
    out = {}
    for m, p in moods.items():
        if not re.search(r"\s\d+$", m) and f"{m} 2" in moods and f"{m} 1" not in moods:
            out[f"{m} 1"] = p
        else:
            out[m] = p
    return out


def _has_character_folders(d: Path) -> bool:
    return any(c.is_dir() and _images(c) for c in d.iterdir())


def find_root(path: str, title: str = "") -> Path:
    """The folder that directly holds the character folders. Steps into a story-title folder when needed."""
    root = Path(path).expanduser()
    if not root.is_dir():
        raise ValueError(f"That folder does not exist: {path}")
    if _has_character_folders(root):
        return root
    cands = [c for c in root.iterdir() if c.is_dir() and _has_character_folders(c)]
    if not cands:
        raise ValueError("No character folders with pictures were found there. Expected <folder>/<Character>/<mood>.webp")
    match = [c for c in cands if _alnum(c.name) == _alnum(title)] or ([cands[0]] if len(cands) == 1 else [])
    if not match:
        raise ValueError("Several story folders found (" + ", ".join(c.name for c in cands) + "). Give the path of the one to import.")
    return match[0]


def scan(path: str, title: str = "") -> dict:
    root = find_root(path, title)
    characters, singles, skipped, intro = [], [], [], []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        imgs = _images(d)
        if not imgs:
            continue
        if INTRO_RE.search(d.name):
            intro += sorted(imgs, key=natural_key)
        elif SKIP_RE.search(d.name):
            skipped.append({"folder": d.name, "pictures": len(imgs), "why": "not a character (intro / gallery / scenes)"})
        elif SINGLE_RE.search(d.name):
            singles += [{"name": f.stem.strip(), "file": f} for f in imgs]
        else:
            moods: dict[str, Path] = {}
            for f in imgs:
                moods.setdefault(norm_mood(f.stem), f)
            characters.append({"name": d.name.strip(), "moods": fix_numbering(moods)})
    return {"root": root, "characters": characters, "singles": singles, "skipped": skipped, "intro": intro}
