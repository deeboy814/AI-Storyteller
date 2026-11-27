"""The picture bin: a character picture or video that is removed or replaced is moved here instead of being deleted,
and can be put back for KEEP_DAYS days.

One folder per item: data/picture_bin/<story id>/<item id>/ holds meta.json and the moved files (the picture and/or its
clips). meta.json remembers where the picture was (which list, name, outfit or place), its saved prompt, its video links
and enough of the character's card to bring the character back if they were removed too.

The caller changes the story; this module only moves files, the video lists and (in restore) puts the links back."""
import contextvars
import json
import re
import secrets
import shutil
import time
from pathlib import Path

from . import store

KEEP_DAYS = 30
PICTURE_FIELDS = ("moods", "alts", "outfit_pics", "place_pics", "extras", "pic_meta", "cover")
_CLIP_SUFFIXES = (".mp4", ".webm")

# What the current request put in the bin on the player's say-so (so the page can offer Undo). The web layer sets a fresh list per request.
removed_now: contextvars.ContextVar[list | None] = contextvars.ContextVar("removed_now", default=None)


def root() -> Path:
    return store.DATA / "picture_bin"


def _index(name: str) -> dict:
    try:
        return json.loads((store.DATA / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _local(url: str) -> tuple[Path | None, str]:
    """(path a /portraits/ or /images/ address points at, the address without ?query). The file need not exist."""
    clean = str(url or "").split("?")[0]
    for base, prefix in ((store.PORTRAITS, "/portraits/"), (store.IMAGES, "/images/")):
        if clean.startswith(prefix):
            f = (base / clean[len(prefix):]).resolve()
            return (f if f.is_relative_to(base.resolve()) else None), clean
    return None, clean


def _url_of(f: Path) -> str:
    for base, prefix in ((store.PORTRAITS, "/portraits/"), (store.IMAGES, "/images/")):
        if f.is_relative_to(base.resolve()):
            return prefix + f.relative_to(base.resolve()).as_posix()
    return ""


def clips_of(f: Path) -> list[Path]:
    """Every clip kept beside a picture: the one in use and the earlier ones."""
    if not f.parent.is_dir():
        return []
    stem = re.escape(f.stem)
    want = re.compile(rf"{stem}(\.earlier[^.]*)?\.(mp4|webm)")
    return sorted(p for p in f.parent.iterdir() if want.fullmatch(p.name))


def slots(ch: dict) -> dict:
    """picture address -> where the character keeps it. Take this BEFORE changing the character's lists."""
    out: dict[str, dict] = {}

    def add(url, **slot):
        if url and url not in out:
            if ch.get("cover") and slot["field"] == "moods" and ch["cover"] == slot["mood"]:
                slot["cover"] = True
            out[url] = slot

    for m, u in (ch.get("moods") or {}).items():
        add(u, field="moods", key="", mood=m)
    for m, urls in (ch.get("alts") or {}).items():
        for i, u in enumerate(urls):
            add(u, field="alts", key="", mood=m, index=i)
    for field in ("outfit_pics", "place_pics"):
        for key, pics in (ch.get(field) or {}).items():
            for m, u in (pics or {}).items():
                add(u, field=field, key=key, mood=m)
    for x in ch.get("extras") or []:
        add(x.get("url"), field="extras", key="", mood=x.get("mood") or "", item=dict(x))
    return out


def _card(ch: dict, outfits: dict | None = None) -> dict:
    card = {k: v for k, v in ch.items() if k not in PICTURE_FIELDS}
    if outfits is not None:
        card["outfits"] = outfits
    return card


def _new_item(sid: str, meta: dict) -> tuple[str, Path]:
    iid = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
    d = root() / sid / iid
    d.mkdir(parents=True, exist_ok=True)
    meta.update(id=iid, sid=sid, deleted=time.time())
    return iid, d


def _save_meta(d: Path, meta: dict) -> None:
    store._write(d / "meta.json", meta)


def _tell(iid: str, reason: str) -> None:
    got = removed_now.get()
    if got is not None and reason != "replaced":
        got.append(iid)


def put(sid: str, ch: dict, url: str, slot: dict | None, reason: str = "removed", card: dict | None = None) -> str | None:
    """Move one picture (with its clips) into the bin. `slot` is where it was (from slots()). Returns the item id, or None if there is no such file."""
    f, clean = _local(url)
    if not f or not f.is_file():
        return None
    idx, hid, prompts = _index("videos.json"), _index("videos_hidden.json"), _index("video_prompts.json")
    clips = clips_of(f)
    meta = {"kind": "picture", "reason": reason, "slug": ch.get("slug", ""), "char": ch.get("name", ""), "url": url,
            "slot": slot or {"field": "extras", "key": "", "mood": ""}, "picture": f.name, "clips": [c.name for c in clips],
            "shown": _name_of(idx.get(clean)), "hidden": _name_of(hid.get(clean)), "video_prompt": prompts.get(clean, ""),
            "pic_meta": (ch.get("pic_meta") or {}).get(url), "card": card or _card(ch)}
    iid, d = _new_item(sid, meta)
    _save_meta(d, meta)            # written first, so a file is never moved without a note saying where it came from
    shutil.move(str(f), str(d / f.name))
    for c in clips:
        shutil.move(str(c), str(d / c.name))
    _drop_links(clean)
    _tell(iid, reason)
    return iid


def put_video(sid: str, url: str, only: str = "", label: str = "", slug: str = "", char: str = "") -> str | None:
    """Move a picture's clips into the bin (all of them, or just the one file named `only`). The picture stays."""
    f, clean = _local(url)
    if not f:
        return None
    clips = [c for c in clips_of(f) if not only or c.name == only]
    if not clips:
        return None
    idx, hid = _index("videos.json"), _index("videos_hidden.json")
    names = [c.name for c in clips]
    shown, hidden = _name_of(idx.get(clean)), _name_of(hid.get(clean))
    meta = {"kind": "video", "reason": "removed", "slug": slug, "char": char, "url": clean, "slot": {"field": "video", "key": "", "mood": label},
            "picture": f.name, "clips": names, "shown": shown if shown in names else "", "hidden": hidden if hidden in names else ""}
    iid, d = _new_item(sid, meta)
    _save_meta(d, meta)
    for c in clips:
        shutil.move(str(c), str(d / c.name))
    if not only:
        _drop_links(clean)
    _tell(iid, "removed")
    return iid


def _name_of(vurl: str | None) -> str:
    return vurl.split("?")[0].rsplit("/", 1)[-1] if vurl else ""


def _drop_links(clean: str) -> None:
    for name in ("videos.json", "videos_hidden.json"):
        d = _index(name)
        if d.pop(clean, None) is not None:
            store._write(store.DATA / name, d)


def _set_link(name: str, key: str, value: str) -> None:
    d = _index(name)
    d[key] = value
    store._write(store.DATA / name, d)


def _item(sid: str, iid: str) -> tuple[Path, dict] | None:
    if not re.fullmatch(r"[0-9a-z]+", sid) or not re.fullmatch(r"[0-9]+-[0-9a-f]+", iid):
        return None
    d = root() / sid / iid
    try:
        return d, json.loads((d / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def items(sid: str) -> list[dict]:
    """The bin's contents for one story, newest first."""
    base = root() / sid
    got = []
    for d in base.iterdir() if base.is_dir() else []:
        found = _item(sid, d.name)
        if found:
            got.append(found[1])
    return sorted(got, key=lambda m: -m["deleted"])


def picture_file(sid: str, iid: str) -> Path | None:
    """The still to show for an item: the binned picture, or (for a video) the picture it belongs to."""
    found = _item(sid, iid)
    if not found:
        return None
    d, meta = found
    f = d / meta["picture"] if meta["kind"] == "picture" else _local(meta["url"])[0]
    return f if f and f.is_file() else None


def delete(sid: str, ids: list[str]) -> int:
    n = 0
    for iid in ids:
        found = _item(sid, iid)
        if found:
            shutil.rmtree(found[0], ignore_errors=True)
            n += 1
    return n


def forget_story(sid: str) -> None:
    if re.fullmatch(r"[0-9a-z]+", sid):
        shutil.rmtree(root() / sid, ignore_errors=True)


def purge(days: float = KEEP_DAYS) -> int:
    """Delete items older than `days` for good (days=0 empties the whole bin). Never raises."""
    cutoff, n = time.time() - days * 86400, 0
    try:
        for story_dir in list(root().iterdir()) if root().is_dir() else []:
            if not story_dir.is_dir():
                continue
            for d in list(story_dir.iterdir()):
                found = _item(story_dir.name, d.name)
                when = found[1]["deleted"] if found else d.stat().st_mtime
                if days <= 0 or when < cutoff:
                    shutil.rmtree(d, ignore_errors=True)
                    n += 1
            try:
                story_dir.rmdir()          # only if empty now
            except OSError:
                pass
    except Exception:
        pass
    return n


def restore_order(meta: dict) -> tuple:
    """Sort key for putting several items back: current pictures first, then earlier versions in their old order, videos last."""
    slot = meta.get("slot") or {}
    return ({"moods": 0, "alts": 1, "video": 3}.get(slot.get("field"), 2), slot.get("index", 0), meta["deleted"])


def _free_file(f: Path) -> Path:
    n, out = 1, f
    while out.exists() or clips_of(out):
        n += 1
        out = f.with_name(f"{f.stem}-back{n}{f.suffix}")
    return out


def restore(s: dict, iid: str, place_names: set[str], place_key) -> str:
    """Put one item back and link it into the story `s` (the caller saves it). Returns the character's id. Raises ValueError with a message for the player."""
    found = _item(s["id"], iid)
    if not found:
        raise ValueError("That is not in the picture bin any more.")
    d, meta = found
    return _restore_video(d, meta) if meta["kind"] == "video" else _restore_picture(s, d, meta, place_names, place_key)


def _restore_video(d: Path, meta: dict) -> str:
    f, clean = _local(meta["url"])
    if not f or not f.is_file():
        raise ValueError("The picture this video belongs to is not there. Restore the picture first.")
    now = _index("videos.json").get(clean) or _index("videos_hidden.json").get(clean)
    back: dict[str, Path] = {}
    for name in meta["clips"]:
        src = d / name
        if not src.is_file():
            continue
        dest = f.parent / name
        clash = dest.exists() or (name == meta.get("shown") and now)
        if clash:            # a newer video is in use: the one coming back is kept beside it as an earlier video
            dest = f.parent / f"{f.stem}.earlier-{int(time.time() * 1000)}{src.suffix}"
            while dest.exists():
                dest = f.parent / f"{f.stem}.earlier-{int(time.time() * 1000) + 1}{src.suffix}"
        shutil.move(str(src), str(dest))
        back[name] = dest
    if not now:
        for key, index in (("shown", "videos.json"), ("hidden", "videos_hidden.json")):
            if meta.get(key) in back:
                _set_link(index, clean, f"{_url_of(back[meta[key]])}?v={int(time.time())}")
    shutil.rmtree(d, ignore_errors=True)
    return meta.get("slug", "")


def _restore_picture(s: dict, d: Path, meta: dict, place_names: set[str], place_key) -> str:
    src = d / meta["picture"]
    want, clean = _local(meta["url"])
    if not src.is_file() or not want:
        raise ValueError("That picture's file is missing from the bin.")
    cast = s.setdefault("cast", [])
    ch = next((c for c in cast if c.get("slug") == meta["slug"]), None)
    if ch is None:          # the character was removed as well: bring their card back
        ch = {**json.loads(json.dumps(meta.get("card") or {})), "slug": meta["slug"], "name": meta["char"], "moods": {}, "alts": {}}
        cast.append(ch)
        s["art_dismissed"] = [n for n in s.get("art_dismissed", []) if n.lower() != meta["char"].lower()]
    want.parent.mkdir(parents=True, exist_ok=True)
    f = _free_file(want)
    url = meta["url"] if f == want else _url_of(f)
    clean = url.split("?")[0]
    shutil.move(str(src), str(f))
    renamed = {}
    for name in meta["clips"]:
        c = d / name
        if c.is_file():
            dest = f.parent / (f.stem + name[len(want.stem):])
            shutil.move(str(c), str(dest))
            renamed[name] = dest
    for key, index in (("shown", "videos.json"), ("hidden", "videos_hidden.json")):
        if meta.get(key) in renamed:
            _set_link(index, clean, f"{_url_of(renamed[meta[key]])}?v={int(time.time())}")
    if meta.get("video_prompt"):
        _set_link("video_prompts.json", clean, meta["video_prompt"])
    if meta.get("pic_meta"):
        ch.setdefault("pic_meta", {})[url] = meta["pic_meta"]
    _link(s, ch, url, meta, place_names, place_key)
    shutil.rmtree(d, ignore_errors=True)
    return ch["slug"]


def _link(s: dict, ch: dict, url: str, meta: dict, place_names: set[str], place_key) -> None:
    slot = meta["slot"]
    field, key, mood = slot["field"], slot.get("key", ""), slot.get("mood", "")
    outfit = key if field == "outfit_pics" else key.partition("@")[2] if field == "place_pics" else ""
    if outfit and outfit not in (ch.get("outfits") or {}):      # the outfit was removed too: put it back with its description
        ch.setdefault("outfits", {})[outfit] = ((meta.get("card") or {}).get("outfits") or {}).get(outfit, "")
    if field == "place_pics" and place_key(key.partition("@")[0]) not in place_names:
        field, slot = "extras", {"item": {"url": url, "label": f"{mood} ({key})", "mood": mood, "ts": time.time()}}
    if field == "moods":
        moods = ch.setdefault("moods", {})
        if moods.get(mood) and moods[mood] != url:      # another picture has the name now: it becomes the earlier version again
            ch.setdefault("alts", {}).setdefault(mood, []).insert(0, moods[mood])
        moods[mood] = url
        if slot.get("cover") and not ch.get("cover"):
            ch["cover"] = mood
    elif field == "alts":
        lst = ch.setdefault("alts", {}).setdefault(mood, [])
        lst.insert(min(slot.get("index", len(lst)), len(lst)), url)
    elif field in ("outfit_pics", "place_pics"):
        pics = ch.setdefault(field, {}).setdefault(key, {})
        if pics.get(mood) and pics[mood] != url:        # another picture is in that place now: they swap, so neither is lost
            old = pics[mood]
            put(s["id"], ch, old, {"field": field, "key": key, "mood": mood}, "replaced")
        pics[mood] = url
    else:
        item = {**(slot.get("item") or {"label": mood, "mood": mood, "ts": time.time()}), "url": url}
        ch.setdefault("extras", []).append(item)
