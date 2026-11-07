"""Tiny JSON-file storage for stories and app config."""
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path

from . import tracker
from .characters import DEFAULT_SETS

DATA = Path(os.environ.get("STORYTELLER_DATA") or Path(__file__).resolve().parent.parent / "data")   # override to test on separate data
STORIES = DATA / "stories"
PORTRAITS = DATA / "portraits"
IMAGES = DATA / "images"        # generated scene illustrations
IMAGES.mkdir(parents=True, exist_ok=True)
PORTRAITS.mkdir(parents=True, exist_ok=True)
STORIES.mkdir(parents=True, exist_ok=True)
CONFIG = DATA / "config.json"

DEFAULT_CONFIG = {
    "providers": [
        {
            "id": "ollama",
            "type": "ollama",
            "name": "Ollama (local)",
            "base_url": "http://localhost:11434",
            "api_key": "",
            "models": [],
        },
        {
            "id": "claude",
            "type": "anthropic",
            "name": "Claude",
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "models": ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5"],
        },
        {"id": "openai", "type": "openai", "name": "ChatGPT (OpenAI)", "base_url": "https://api.openai.com/v1", "api_key": "", "models": []},
        {"id": "gemini", "type": "openai", "name": "Gemini (Google)", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
         "api_key": "", "models": []},
        {"id": "xai", "type": "openai", "name": "Grok (xAI)", "base_url": "https://api.x.ai/v1", "api_key": "", "models": []},
    ],
    "budget_daily": 0,          # spending limit per day in dollars (0 = none); estimates from the usage log
    "budget_monthly": 0,        # ...per month
    "budget_action": "warn",    # "warn" = show a warning; "stop" = stop paid calls once a limit is reached (you can still override)
    "backup_auto_days": 0,      # automatic whole-app backup every N days (0 = off)
    "backup_dir": "",           # where automatic backups go ("" = data/backups/full)
    "backup_keep": 5,           # how many automatic backups to keep
    "ui_bg": "",                # appearance: background colour of the reading area ("" = default theme)
    "ui_text": "",              # appearance: text colour
    "ui_backdrop": False,       # show the story's generated backdrop on the sides of the screen
    "ui_backdrop_dim": 55,      # how much to darken the backdrop (percent)
    "sidebar_default_mode": "prompt",   # side panel for new stories: "prompt" (the story prompt decides), "player" (you pick fields), "ai" (the AI decides)
    "rt_text": False,           # Home settings: load the story's local text model when selected, unload ones nothing uses
    "rt_comfy": False,          # ...and start ComfyUI when needed, stop (or free) it after it has been idle
    "rt_comfy_idle_min": 15,    # minutes of no picture-drawing before ComfyUI stops
    "rt_comfy_mode": "stop",    # "stop" = quit ComfyUI; "free" = keep it running but release its video memory
    "rt_comfy_cmd": "",         # how to start ComfyUI ("" = ~/ComfyUI/start.sh)
    "rt_swap": True,            # unload the text model before drawing a local picture when video memory is short
    "avatar_library": False,    # Home settings: several player avatars you can reuse across stories (one selected per story)
    "reuse_art": False,         # Home settings: use a character's pictures from another story as references
    "prices": [],               # your own price overrides for the usage report (see usage.py)
    "default_models": {},       # the choice offered first for each new story: {"story": {provider, model}, "recaps": {...}, "images": {...}}
    "active": {"provider": "claude", "model": "claude-sonnet-5-5"},
    "temperature": 0.9,
    "max_tokens": 2048,
    "num_ctx": 8192,
    "claude_effort": "low",     # how hard Claude thinks before replying: low / medium / high (low = shorter hidden thinking)
    "thinking_headroom": 4096,  # extra token ceiling so hidden thinking cannot cut the visible reply short (only used tokens are billed)
    "claude_thinking": False,  # let Claude "think" before replying (costs extra output tokens)
    "events_mode": "every",     # key events: "every" = after each reply a small text-model call logs important moments per character; "off"
    "events_model": "",         # "provider|model" for that bookkeeping call ("" = the story's own model; a cheaper or local one is fine)
    "default_art_direction": "",   # art style new stories start with (chosen under Settings → AI & story)
    "mood_looks": {},   # what to draw for moods you add yourself: {"blushing": "embarrassed, red cheeks, hand over mouth"}
    "mood_sets": DEFAULT_SETS,  # which extra pictures to draw for a new character: basic / extended (editable in Settings)
    "image_active": "gemini",   # which image provider makes pictures (Claude cannot)
    "image_max_refs": 3,        # reference portraits sent with a scene so faces stay consistent
    "image_providers": [
        {"id": "gemini", "type": "gemini", "name": "Gemini", "base_url": "https://generativelanguage.googleapis.com",
         "api_key": "", "model": "gemini-2.5-flash-image"},
        {"id": "openai", "type": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1",
         "api_key": "", "model": "gpt-image-1", "send_size": True, "allow_refs": True},
        {"id": "xai", "type": "openai", "name": "xAI Grok Imagine", "base_url": "https://api.x.ai/v1",
         "api_key": "", "model": "grok-imagine-image", "send_size": False, "allow_refs": False},
        {"id": "comfyui", "type": "comfyui", "name": "ComfyUI (local)", "base_url": "http://127.0.0.1:8188",
         "api_key": "", "model": "", "workflow": "", "workflow_ref": "", "prompt_style": "tags"},
    ],
    "prompt_cache": True,      # Claude prompt caching on/off
    "cache_ttl": "5m",         # how long Claude keeps the cached prompt: "5m" (cheaper writes) or "1h"
    "keep_warm": False,        # re-send the story shortly before the Claude cache expires, so it stays cached while you are away
    "keep_warm_hours": 2,      # ...but stop this many hours after your last real turn
    "keep_alive": "30m",       # how long Ollama keeps the model + its KV cache loaded between turns
    "summarize_when": 24,      # fold older turns into the memory summary once history exceeds this many messages
    "keep_recent": 10,         # ...leaving this many recent messages verbatim
}


def _write(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# ---------- config ----------
def load_config() -> dict:
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, v)
    have = {p["id"] for p in cfg["providers"]}
    cfg["providers"] += [json.loads(json.dumps(p)) for p in DEFAULT_CONFIG["providers"] if p["id"] not in have]   # new built-in providers
    # one key serves both the text and the picture side of the same company
    img = {p["id"]: p for p in cfg.get("image_providers", [])}
    for p in cfg["providers"]:
        q = img.get(p["id"])
        if q and p["type"] != "ollama":
            key = p.get("api_key") or q.get("api_key", "")
            p["api_key"], q["api_key"] = key, key
    # Allow the key to come from the environment so it never has to be saved to disk.
    for p in cfg["providers"]:
        if p["type"] == "anthropic" and not p.get("api_key"):
            p["api_key"] = os.environ.get("ANTHROPIC_API_KEY", "")
    return cfg


def save_config(cfg: dict) -> None:
    _write(CONFIG, cfg)


def public_config(cfg: dict) -> dict:
    """Config safe to send to the browser: API keys are never sent back."""
    out = json.loads(json.dumps(cfg))
    for p in out["providers"] + out["image_providers"]:
        p["has_key"] = bool(p.get("api_key"))
        p["api_key"] = ""
    return out


# ---------- stories ----------
def _path(sid: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+", sid):
        raise KeyError(sid)
    return STORIES / f"{sid}.json"


def new_story(title: str, subtitle: str, base_prompt: str, player_name: str = "") -> dict:
    story = {
        "id": uuid.uuid4().hex[:12],
        "player_name": player_name,
        "avatar": {},          # the player's character sheet: name, gender, age, appearance, ...
        "avatar_id": "",       # the avatar-library entry this story uses (when the avatar library is on)
        "avatar_image": "",    # URL of the uploaded avatar picture
        "nsfw": False,         # adult content: off = sexual content stays non-explicit; on = explicit content between adults is allowed (text only)
        "sidebar": tracker.default_sidebar(),   # what the side panel tracks: mode prompt / player / ai (+ chosen fields)
        "tracker": tracker.blank(),             # the side panel's data, with each turn's changes
        "backdrop": {},        # generated backdrop picture for the screen sides: {"url", "source"}
        "prologue": "",        # events leading up to the story (shown at the start; the narrator knows them)
        "opening_options": [], # suggested first moves shown before the first turn
        "endings": [],         # author's possible endings [{"title", "text"}]; hidden notes for the narrator
        "target_ending": -1,   # index of the ending the player asked the story to work toward (-1 = none)
        "models": {},          # this story's AI choices: story / recaps / images -> {"provider", "model"} (empty = use app defaults)
        "user_notes": "",      # standing instructions from the player, sent every turn
        "art": {"direction": "", "negative": "low quality, blurry, deformed hands, watermark, text",
                "size": "portrait", "auto": False, "portraits": True, "portrait_size": "medium", "portrait_shape": "landscape", "suggest": True, "newchar": False},
        "art_dismissed": [],   # names the player said never to ask about
        "cast": [],            # characters: name, appearance (for image prompts), moods {mood: portrait url}
        "opening": "",         # the story's first scene (markdown), shown before the first move; ![](intro:It1) shows an intro panel
        "opening_in_context": True,   # also show the opening to the narrator as the story's first message
        "intro_images": [],    # intro/webtoon panels: [{"name": "It1", "url": ...}], in order
        "title": title or "Untitled story",
        "subtitle": subtitle or "",
        "base_prompt": base_prompt or "",
        "created": time.time(),
        "updated": time.time(),
        "turns": [],
        "state": {},
        "suggestions": [],
        "summary": "",
        "summary_upto": 0,
    }
    save_story(story)
    return story


STORY_DEFAULTS = {
    "prologue": "", "opening_options": [], "endings": [], "target_ending": -1,
    "backdrop": {},
    "sidebar": tracker.default_sidebar(), "tracker": tracker.blank(),
    "avatar_id": "",
    "nsfw": False,
    "pinned_facts": [],
    "cover": "", "description": "",
    "auto_replies": False, "reply_options": {}, "group_cast": True, "models": {}, "avatar": {}, "avatar_image": "", "user_notes": "", "cast": [], "opening": "", "opening_in_context": True, "intro_images": [],
    "art": {"direction": "", "negative": "low quality, blurry, deformed hands, watermark, text",
            "size": "portrait", "auto": False, "portraits": True, "portrait_size": "medium", "portrait_shape": "landscape", "suggest": True, "newchar": False},
    "art_dismissed": [],
}


def load_story(sid: str) -> dict:
    p = _path(sid)
    if not p.exists():
        raise KeyError(sid)
    s = json.loads(p.read_text(encoding="utf-8"))
    for k, v in STORY_DEFAULTS.items():   # stories made before a feature existed get its defaults
        s.setdefault(k, json.loads(json.dumps(v)))
    return s


def save_story(story: dict) -> None:
    story["updated"] = time.time()
    _write(_path(story["id"]), story)


# ---------- automatic backups ----------
AUTO = DATA / "backups" / "auto"
KEEP_SNAPSHOTS = 40


def snapshot_story(sid: str, reason: str) -> str | None:
    """Copy the story file into data/backups/auto/<id>/ before something risky. Keeps the newest 40 per story. Never raises."""
    try:
        src = _path(sid)
        if not src.exists():
            return None
        d = AUTO / sid
        d.mkdir(parents=True, exist_ok=True)
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^a-z0-9]+', '-', reason.lower()).strip('-')[:30]}.json"
        shutil.copy2(src, d / name)
        for old in sorted(d.glob("*.json"))[:-KEEP_SNAPSHOTS]:
            old.unlink(missing_ok=True)
        return name
    except Exception:
        return None


def list_snapshots(sid: str) -> list[dict]:
    d = AUTO / sid
    out = []
    for f in sorted(d.glob("*.json"), reverse=True) if d.exists() else []:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": f.name, "when": f.stat().st_mtime, "turns": len(s.get("turns", [])) // 2, "reason": f.stem.split("-", 2)[-1].replace("-", " ")})
        except Exception:
            continue
    return out


def restore_snapshot(sid: str, name: str) -> dict:
    f = AUTO / sid / name
    if not re.fullmatch(r"[0-9a-z-]+\.json", name) or not f.exists():
        raise KeyError(name)
    snapshot_story(sid, "before restore")
    story = json.loads(f.read_text(encoding="utf-8"))
    story["updated"] = time.time()
    _write(_path(sid), story)
    return load_story(sid)


def delete_story(sid: str) -> None:
    snapshot_story(sid, "before delete")
    p = _path(sid)
    if p.exists():
        p.unlink()
    shutil.rmtree(PORTRAITS / sid, ignore_errors=True)
    shutil.rmtree(IMAGES / sid, ignore_errors=True)


def list_stories() -> list[dict]:
    out = []
    for p in STORIES.glob("*.json"):
        try:
            s = json.loads(p.read_text(encoding="utf-8"))
            out.append(
                {
                    "id": s["id"],
                    "title": s["title"],
                    "subtitle": s.get("subtitle", ""),
                    "turns": len(s.get("turns", [])) // 2,
                    "cover": s.get("cover", ""),
                    "description": s.get("description", ""),
                    "player": (s.get("avatar") or {}).get("name") or s.get("player_name") or "",
                    "cast": len(s.get("cast") or []),
                    "updated": s.get("updated", 0),
                }
            )
        except Exception:
            continue
    return sorted(out, key=lambda s: s["updated"], reverse=True)
