"""AI Storyteller: local FastAPI server. Run with ./run.sh"""
import asyncio
import hashlib
import os
import io
import zipfile
import json
import re
import shutil
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote_plus

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import backup, characters, images, importer, localrt, portrait_import, prompting, providers, store, tracker, usage

ROOT = Path(__file__).resolve().parent.parent
# ---------------- keep-warm: refresh Claude's prompt cache while you're away ----------------
# Claude's cache expires after its lifetime (5 min or 1 h) unless it is used again; every use resets the clock. A tiny
# request that re-sends the story (1 token back) costs only the cheap cache-read price, so with the 1-hour lifetime a ping
# roughly every 54 minutes keeps the story cached for as long as keep-warm is on. It stops by itself after
# `keep_warm_hours` without a real turn from you, so it can never run forever.
_warm = {"story_id": None, "provider": None, "model": None, "touched": 0.0, "activity": 0.0, "ttl": 300,
         "pings": 0, "error": "", "retry_at": 0.0}


def _ttl_seconds(cfg: dict) -> int:
    return 3600 if cfg.get("cache_ttl") == "1h" else 300


def _note_cache_use(story_id: str, prov: dict, model: str, cfg: dict, usage: dict, real: bool = True) -> None:
    """Remember when this story's cache was last used (only counts if Claude actually reported cache activity)."""
    if prov["type"] != "anthropic" or not cfg.get("prompt_cache", True):
        return
    if not (usage.get("cache_read") or usage.get("cache_write")):
        return
    now = time.time()
    _warm.update(story_id=story_id, provider=prov["id"], model=model, touched=now, ttl=_ttl_seconds(cfg), error="")
    if real:
        _warm.update(activity=now, pings=0)


async def _ping(cfg: dict) -> None:
    s = store.load_story(_warm["story_id"])
    if not s["turns"] or s["turns"][-1]["role"] != "assistant":
        return
    prov = next((p for p in cfg["providers"] if p["id"] == _warm["provider"]), None)
    if not prov:
        return
    usage.set_ctx(s["id"], "keep-warm")
    probe = {**s, "turns": s["turns"] + [{"role": "user", "text": "(cache keep-alive check; no reply needed)"}]}
    system, messages = prompting.build(probe).request()  # same story + a throwaway final message; nothing is saved
    used: dict = {}
    async for chunk in providers.stream_chat(prov, _warm["model"], system, messages, max_tokens=1,
                                             num_ctx=cfg["num_ctx"], **_extras(cfg)):
        if isinstance(chunk, providers.Usage):
            used = dict(chunk)
    _note_cache_use(s["id"], prov, _warm["model"], cfg, used, real=False)
    _warm["pings"] += 1


async def _keep_warm_loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            cfg = store.load_config()
            if not (cfg.get("keep_warm") and cfg.get("prompt_cache", True) and _warm["story_id"]):
                continue
            now = time.time()
            if now - _warm["activity"] > float(cfg.get("keep_warm_hours", 2)) * 3600:
                continue  # you've been away longer than the limit: let the cache lapse
            ttl = _ttl_seconds(cfg)
            if now < _warm["touched"] + ttl - max(60, ttl * 0.1) or now < _warm["retry_at"]:
                continue  # not due yet (pings go out shortly before the cache would expire)
            await _ping(cfg)
        except Exception as e:  # never let a failed ping kill the loop; wait 5 minutes before trying again
            _warm["error"] = str(e)[:200]
            _warm["retry_at"] = time.time() + 300


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(_keep_warm_loop())
    rt = asyncio.create_task(localrt.loop())
    bk = asyncio.create_task(_backup_loop())
    yield
    task.cancel()
    rt.cancel()
    bk.cancel()


app = FastAPI(title="AI Storyteller", lifespan=lifespan)


# ---------------- optional access PIN for other devices on your network (see run-lan.sh) ----------------
PIN = os.environ.get("STORYTELLER_PIN", "")
_pin_fail: dict[str, int] = {}
LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>AI Storyteller</title>
<body style="background:#12111c;color:#e8e5f2;font:18px system-ui;display:grid;place-items:center;height:100vh;margin:0"><form method=post action=/pin-login style="display:grid;gap:12px;width:min(320px,90vw)">
<b>AI Storyteller</b><input name=pin type=password inputmode=numeric placeholder="PIN" autofocus style="font-size:20px;padding:10px;border-radius:8px;border:1px solid #444;background:#1b1a2b;color:inherit">
<button style="font-size:18px;padding:10px;border-radius:8px;background:#8b6fd6;color:#fff;border:0">Open</button><small style="color:#999">{msg}</small></form>"""


def _pin_cookie() -> str:
    return hashlib.sha256(("st|" + PIN).encode()).hexdigest()[:32]


@app.middleware("http")
async def pin_gate(request: Request, call_next):
    """When STORYTELLER_PIN is set, other computers must enter it first. This computer (localhost) never needs it."""
    host = request.client.host if request.client else ""
    if not PIN or host in ("127.0.0.1", "::1", "localhost") or request.cookies.get("st_pin") == _pin_cookie():
        return await call_next(request)
    if request.url.path == "/pin-login" and request.method == "POST":
        if _pin_fail.get(host, 0) >= 10:
            return Response(LOGIN_HTML.format(msg="Too many wrong tries. Restart the app to reset."), media_type="text/html", status_code=429)
        form = (await request.body()).decode()
        given = re.search(r"pin=([^&]*)", form)
        if given and hashlib.sha256(("st|" + unquote_plus(given.group(1))).encode()).hexdigest()[:32] == _pin_cookie():
            r = Response(status_code=303, headers={"Location": "/"})
            r.set_cookie("st_pin", _pin_cookie(), max_age=60 * 60 * 24 * 90, httponly=True, samesite="lax")
            return r
        _pin_fail[host] = _pin_fail.get(host, 0) + 1
        await asyncio.sleep(1)
        return Response(LOGIN_HTML.format(msg="Wrong PIN."), media_type="text/html", status_code=401)
    if request.url.path.startswith("/api/"):
        return Response('{"detail":"PIN required"}', media_type="application/json", status_code=401)
    return Response(LOGIN_HTML.format(msg=""), media_type="text/html")


@app.middleware("http")
async def _always_fresh_app_files(request: Request, call_next):
    """The page and its scripts change when the app is updated: never let a browser keep an old copy."""
    resp = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp



def _extras(cfg: dict) -> dict:
    """Per-request provider options derived from settings (caching, keep-alive)."""
    return {
        "cache_ttl": cfg.get("cache_ttl", "5m") if cfg.get("prompt_cache", True) else None,
        "keep_alive": cfg.get("keep_alive", "30m"),
        "thinking": bool(cfg.get("claude_thinking", False)),
        "effort": cfg.get("claude_effort", "low") or None,
        "thinking_headroom": int(cfg.get("thinking_headroom", 4096)),
    }


class StoryIn(BaseModel):
    title: str = ""
    subtitle: str = ""
    base_prompt: str = ""
    player_name: str | None = None  # None = leave unchanged
    use_state_block: bool = True
    opening: str | None = None            # None = leave unchanged
    opening_in_context: bool | None = None
    models: dict | None = None            # {story|recaps|images: {provider, model}}
    auto_replies: bool | None = None      # suggest three replies after every narrator reply
    nsfw: bool | None = None              # adult content setting
    description: str | None = None
    prologue: str | None = None
    opening_options: list[str] | None = None
    endings: list[dict] | None = None
    sidebar: dict | None = None           # suggested side panel: {mode, fields, schema}
    art: dict | None = None               # art direction / setting / negative suggested by the author
    cast: list[dict] | None = None        # premade characters: [{name, appearance, bio, personality}]


class PlayerIn(BaseModel):
    avatar: dict[str, str] = {}
    user_notes: str = ""
    summary: str | None = None  # the story's memory; None = leave unchanged


class ImportIn(BaseModel):
    title: str = ""
    subtitle: str = ""
    source_story_id: str | None = None  # copy the story prompt, notes and settings from this story
    base_prompt: str = ""               # or give the prompt directly
    avatar: dict[str, str] = {}
    user_notes: str = ""
    summary: str = ""                   # memory: everything that happened so far
    status_panel: str = ""              # the latest status panel (copied exactly)
    recent_turns: str = ""              # optional: last few turns, word for word
    into_story_id: str | None = None    # put the save into this unplayed story (e.g. one just imported as a package) instead of making a new one


AVATAR_FIELDS = ("name", "gender", "age", "appearance", "personality", "background", "abilities")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_IMAGE = 8 * 1024 * 1024


class TurnIn(BaseModel):
    text: str
    provider: str | None = None
    model: str | None = None
    force_budget: bool = False      # send even though a spending limit was reached


def _story(sid: str) -> dict:
    try:
        return store.load_story(sid)
    except KeyError:
        raise HTTPException(404, "Story not found")


def _pick(cfg: dict, provider_id: str | None, model: str | None):
    pid = provider_id or cfg["active"]["provider"]
    prov = next((p for p in cfg["providers"] if p["id"] == pid), None)
    if not prov:
        raise HTTPException(400, f"Unknown provider: {pid}")
    return prov, model or cfg["active"]["model"]


def _role(cfg: dict, s: dict | None, role: str, purpose: str | None = None):
    """(provider, model) for a text job of this story. role: "story" (the narrator) or "recaps" (summaries, key events, bios, picture prompts).
    The story's own choice wins; older stories fall back to the app-wide settings."""
    usage.set_ctx((s or {}).get("id"), purpose or role)   # the usage report files the next model call under this story and job
    c = ((s or {}).get("models") or {}).get(role) or {}
    if c.get("provider") and c.get("model") and any(p["id"] == c["provider"] for p in cfg["providers"]):
        return _pick(cfg, c["provider"], c["model"])
    if role == "recaps":
        spec = (cfg.get("events_model") or "").strip()
        if "|" in spec:
            pid, model = spec.split("|", 1)
            if any(p["id"] == pid for p in cfg["providers"]):
                return _pick(cfg, pid, model)
        return _role(cfg, s, "story", purpose)
    return _pick(cfg, None, None)


def _save_turn(story: dict) -> dict:
    """Save only what a reply owns (turns, state, memory) onto the freshest copy of the story. A reply can take a minute; cast, art or
    picture changes made meanwhile (including by background drawing jobs) must not be overwritten by this older copy."""
    fresh = _story(story["id"])
    for k in ("turns", "state", "suggestions", "summary", "summary_upto", "tracker"):
        fresh[k] = story[k]
    store.save_story(fresh)
    return fresh


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@app.get("/api/cache-status")
def cache_status():
    """What the UI shows about Claude's cache: how long it stays warm and whether keep-warm is running."""
    cfg = store.load_config()
    now = time.time()
    on = bool(cfg.get("prompt_cache", True)) and bool(_warm["story_id"])
    keep = bool(cfg.get("keep_warm"))
    stop = _warm["activity"] + float(cfg.get("keep_warm_hours", 2)) * 3600
    return {
        "caching": bool(cfg.get("prompt_cache", True)),
        "story_id": _warm["story_id"],
        "warm_seconds": max(0, int(_warm["touched"] + _warm["ttl"] - now)) if on else 0,
        "keep_warm": keep,
        "keep_warm_stops_in": max(0, int(stop - now)) if (on and keep) else 0,
        "pings": _warm["pings"],
        "error": _warm["error"],
    }


# ---------------- config / models ----------------
@app.get("/api/config")
def get_config():
    return store.public_config(store.load_config())


@app.put("/api/config")
def put_config(new: dict, request: Request):
    old = store.load_config()
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost"):
        new["rt_comfy_cmd"] = old.get("rt_comfy_cmd", "")   # only this computer may change what program the app launches
    old_keys = {p["id"]: p.get("api_key", "") for p in old["providers"]}
    for p in new.get("providers", []):
        # blank key from the browser means "keep the saved one"
        if not p.get("api_key"):
            p["api_key"] = old_keys.get(p["id"], "")
        p.pop("has_key", None)
    old_img_keys = {p["id"]: p.get("api_key", "") for p in old.get("image_providers", [])}
    for p in new.get("image_providers", []):
        if not p.get("api_key"):
            p["api_key"] = old_img_keys.get(p["id"], "")
        p.pop("has_key", None)
    store.save_config(new)
    return store.public_config(store.load_config())


@app.get("/api/models")
async def models():
    cfg = store.load_config()
    out = []
    for p in cfg["providers"]:
        names = list(p.get("models", []))
        if p["type"] == "ollama":
            names = await providers.list_ollama_models(p) or names
        elif p.get("api_key"):   # online services: whatever this key can actually use
            from . import catalog
            r = await catalog.live_ids(p)
            if r["ok"]:
                names = [i for i in r["ids"] if not catalog.NOT_CHAT.search(i)] or names
        out += [{"provider": p["id"], "provider_name": p["name"], "model": m} for m in names]
    return out


# ---------------- suggested player replies ----------------
REPLIES_SYSTEM = """You write the PLAYER's possible next moves in an interactive story, like the reply suggestions in text-adventure and AI role-play apps.
Write exactly 3 options, each from the player character's point of view in first person, ready to send as the player's turn.
- Format each as: *a short action or inner beat in italics* 'spoken line' (the action and/or the line; dialogue in single quotes).
- Make them clearly different in approach (for example caring, decisive, cunning/cautious), and each a natural reaction to what JUST happened.
- Match the player's own voice and length from their earlier moves (examples below). Never speak or act for other characters beyond the player's own attempt.
- Safe for work. No meta commentary.
Reply with ONLY a JSON array of 3 strings."""


@app.post("/api/stories/{sid}/replies")
async def suggest_replies(sid: str):
    """Three possible next moves for the player, based on the latest events. The player picks one, edits it, and sends it."""
    s, cfg = _story(sid), store.load_config()
    turns = s["turns"]
    idx = next((i for i in range(len(turns) - 1, -1, -1) if turns[i]["role"] == "assistant"), None)
    if idx is None:
        scene = _clean_for_scene(s.get("opening") or "")
        if not scene:
            raise HTTPException(400, "There is nothing to react to yet.")
    else:
        scene = _clean_for_scene(turns[idx]["text"])
    mine = [t["text"] for t in turns if t["role"] == "user"][-3:]
    av = prompting.avatar_block(s)
    ctx = [f"Player character: {prompting.player_name(s)}"]
    if av:
        ctx.append(av)
    if s.get("user_notes"):
        ctx.append("Player's standing notes:\n" + s["user_notes"][:800])
    if s.get("summary"):
        ctx.append("Story so far (short):\n" + s["summary"][-1500:])
    if mine:
        ctx.append("The player's earlier moves (match this voice):\n" + "\n".join(f"- {t[:300]}" for t in mine))
    ctx.append("What just happened (the narrator's latest message):\n" + scene[-6000:])
    prov, model = _role(cfg, s, "recaps", "suggested replies")
    blocked = _budget_block(cfg, prov)
    if blocked:
        raise HTTPException(402, blocked.replace("BUDGET: ", ""))
    out = await providers.complete(prov, model, REPLIES_SYSTEM + ("\nAdult content is allowed in this story; options may be romantic or sexual between adults." if s.get("nsfw") else "\nKeep every option non-explicit."), [{"role": "user", "content": "\n\n".join(ctx)}],
                                   temperature=0.9, max_tokens=700, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    data = _json_in(out)
    opts = [str(x).strip() for x in data if str(x).strip()][:4] if isinstance(data, list) else []
    if not opts:
        raise HTTPException(502, "The model didn't return usable options. Try again.")
    s = _story(sid)
    s["reply_options"] = {"for": len(s["turns"]), "options": opts}
    store.save_story(s)
    return s["reply_options"]


# ---------------- AI choices (per story) ----------------
_hw: dict = {}


class KeyIn(BaseModel):
    provider: str
    api_key: str


class ModelsIn(BaseModel):
    story: dict | None = None
    recaps: dict | None = None
    images: dict | None = None


def _clean_models(body: ModelsIn) -> dict:
    out = {}
    for role in ("story", "recaps", "images"):
        c = getattr(body, role)
        if c and c.get("provider") and c.get("model"):
            out[role] = {"provider": str(c["provider"]), "model": str(c["model"])}
    return out


@app.get("/api/model-options")
async def model_options(refresh: bool = False):
    """Everything the AI chooser needs: this computer's hardware, what each provider offers, recommendations, and the saved defaults."""
    from . import catalog, hardware
    if refresh or not _hw:
        _hw.clear()
        _hw.update(hardware.detect(store.DATA))
    cfg = store.load_config()
    out = await catalog.build(cfg, _hw)
    out["defaults"] = cfg.get("default_models") or {}
    return out


@app.post("/api/provider-key")
async def provider_key(body: KeyIn):
    """Save an API key typed into the chooser. It serves both the text and picture side of the same company. Never echoed back."""
    from . import catalog
    cfg = store.load_config()
    hit = False
    for p in cfg["providers"] + cfg.get("image_providers", []):
        if p["id"] == body.provider and p["type"] != "ollama" and p["type"] != "comfyui":
            p["api_key"] = body.api_key.strip()
            hit = True
    if not hit:
        raise HTTPException(400, "That provider doesn't use a key.")
    store.save_config(cfg)
    catalog.forget(body.provider)
    return {"ok": True}


@app.put("/api/default-models")
def put_default_models(body: ModelsIn):
    cfg = store.load_config()
    cfg["default_models"] = _clean_models(body)
    store.save_config(cfg)
    return cfg["default_models"]


@app.put("/api/stories/{sid}/nsfw")
def put_nsfw(sid: str, on: bool):
    """Per-story adult-content switch (off by default). Affects what the narrator writes; pictures always stay safe for work."""
    s = _story(sid)
    s["nsfw"] = on
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/models")
def put_story_models(sid: str, body: ModelsIn):
    s = _story(sid)
    s["models"] = {**(s.get("models") or {}), **_clean_models(body)}
    store.save_story(s)
    return s["models"]


# ---------------- stories ----------------
@app.get("/api/stories")
def stories():
    return store.list_stories()


def _give_default_models(s: dict) -> None:
    """A story that arrives without AI choices (an import) starts with the player's default choices, like a newly created one."""
    if not (s.get("models") or {}):
        d = store.load_config().get("default_models") or {}
        if d:
            s["models"] = dict(d)


def _apply_creation_fields(s: dict, body: "StoryIn") -> None:
    """The story-creation extras (everything an author can premake)."""
    if body.description is not None:
        s["description"] = body.description.strip()
    if body.prologue is not None:
        s["prologue"] = body.prologue.strip()
    if body.opening_options is not None:
        s["opening_options"] = [o.strip() for o in body.opening_options if o.strip()][:6]
    if body.endings is not None:
        s["endings"] = [{"title": str(e.get("title", "")).strip()[:80], "text": str(e.get("text", "")).strip()[:1200]} for e in body.endings if (e.get("title") or e.get("text"))][:12]
        s["target_ending"] = -1
    if body.sidebar:
        sb = s["sidebar"]
        if body.sidebar.get("mode") in ("prompt", "player", "ai"):
            sb["mode"] = body.sidebar["mode"]
        if isinstance(body.sidebar.get("fields"), list):
            sb["fields"] = [f for f in body.sidebar["fields"] if any(f == d["id"] for d in tracker.DEFAULT_FIELDS)]
        if body.sidebar.get("schema"):
            sb["schema"] = tracker.clean_schema(body.sidebar["schema"]) or sb.get("schema")
        if isinstance(body.sidebar.get("custom"), list):
            sb["custom"] = tracker.clean_custom(body.sidebar["custom"], sb.get("custom"))
    if body.art:
        a = dict(s.get("art") or {})
        for k in ("direction", "negative", "setting"):
            if isinstance(body.art.get(k), str):
                a[k] = body.art[k].strip()
        s["art"] = a
    if body.cast:
        for c in body.cast:
            name = str(c.get("name", "")).strip()
            if not name:
                continue
            ch = _ensure_char(s, name, str(c.get("appearance", "")))
            ch["bio"] = str(c.get("bio", "")).strip()
            ch["personality"] = str(c.get("personality", "")).strip()
            ch["author"] = True


@app.post("/api/stories")
def create_story(body: StoryIn):
    s = store.new_story(body.title, body.subtitle, body.base_prompt, body.player_name)
    s["sidebar"]["mode"] = store.load_config().get("sidebar_default_mode", "prompt")
    if body.opening:
        s["opening"] = body.opening.strip()
    if body.opening_in_context is not None:
        s["opening_in_context"] = body.opening_in_context
    if body.auto_replies is not None:
        s["auto_replies"] = body.auto_replies
    _apply_creation_fields(s, body)
    store.save_story(s)
    if body.nsfw:
        s["nsfw"] = True
        store.save_story(s)
    chosen = _clean_models(ModelsIn(**(body.models or {}))) or store.load_config().get("default_models") or {}
    if chosen:
        s["models"] = chosen
        store.save_story(s)
    if not body.use_state_block:
        s["use_state_block"] = False
        store.save_story(s)
    return s


@app.get("/api/stories/{sid}")
def get_story(sid: str):
    s = _story(sid)
    tr = s.get("tracker") or {}
    if s["turns"] and not any(tr.get(k) for k in ("scene", "me", "chars")) and s["sidebar"]["mode"] != "ai":
        tracker.rebuild(s)          # older stories: read the panels already in the story
        if any(s["tracker"][k] for k in ("scene", "me", "chars")):
            store.save_story(s)
    return s


@app.put("/api/stories/{sid}")
def update_story(sid: str, body: StoryIn):
    s = _story(sid)
    s["title"], s["subtitle"], s["base_prompt"] = body.title or s["title"], body.subtitle, body.base_prompt
    if body.player_name is not None:
        s["player_name"] = body.player_name
    s["use_state_block"] = body.use_state_block
    if body.opening is not None:
        s["opening"] = body.opening.strip()
    if body.opening_in_context is not None:
        s["opening_in_context"] = body.opening_in_context
    if body.auto_replies is not None:
        s["auto_replies"] = body.auto_replies
    if body.nsfw is not None:
        s["nsfw"] = body.nsfw
    cast_before = body.cast
    body.cast = None                    # the cast is managed in the Cast screen for existing stories
    _apply_creation_fields(s, body)
    body.cast = cast_before
    if body.sidebar:
        tracker.rebuild(s)              # the side panel now tracks different fields
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/player")
def update_player(sid: str, body: PlayerIn):
    """Save the player's Avatar sheet and User Notes. Both are sent to the model every turn."""
    s = _story(sid)
    s["avatar"] = {k: body.avatar.get(k, "").strip() for k in AVATAR_FIELDS if body.avatar.get(k, "").strip()}
    s["user_notes"] = body.user_notes.strip()
    s["player_name"] = s["avatar"].get("name", s.get("player_name", ""))  # {user} in the prompt = avatar name
    if body.summary is not None:
        s["summary"] = body.summary.strip()
        s["summary_upto"] = min(s.get("summary_upto", 0), len(s["turns"]))
    store.save_story(s)
    return s


@app.post("/api/stories/import")
def import_story(body: ImportIn):
    """Create a story that continues one played elsewhere: memory summary + latest status panel (+ optional recent turns)."""
    if body.into_story_id:
        s = _story(body.into_story_id)
        if s["turns"]:
            raise HTTPException(400, "That story already has turns, so a save can't be imported into it.")
        src = None
        if body.title.strip():
            s["title"] = body.title.strip()
        s["opening_in_context"] = False     # the save continues past the opening, so the narrator must not see it as the first message
        s["opening_options"] = []
        s["avatar"] = {k: v.strip() for k, v in body.avatar.items() if k in AVATAR_FIELDS and v.strip()} or s.get("avatar", {})
        s["player_name"] = s["avatar"].get("name", s.get("player_name", ""))
        s["user_notes"] = body.user_notes.strip() or s.get("user_notes", "")
    else:
        src = _story(body.source_story_id) if body.source_story_id else None
        base = body.base_prompt.strip() or (src["base_prompt"] if src else "")
        if not base:
            raise HTTPException(400, "Give the story prompt directly or pick a story to copy it from")
        s = store.new_story(body.title or "Imported story", body.subtitle, base)
        s["avatar"] = {k: v.strip() for k, v in body.avatar.items() if k in AVATAR_FIELDS and v.strip()}
        s["player_name"] = s["avatar"].get("name", "")
        s["user_notes"] = body.user_notes.strip() or (src.get("user_notes", "") if src else "")
        s["use_state_block"] = src.get("use_state_block", True) if src else False
        _give_default_models(s)
    s["summary"] = body.summary.strip()
    turns, warnings = importer.parse_turns(body.recent_turns)
    panel = body.status_panel.strip()
    name = prompting.player_name(s)
    comment = f"[//]: # (Never describe the {name} actions, dialogues, or feelings predictively.)"
    if panel and not turns:
        # No pasted turns: give the model the latest panel as "its previous message" so it continues the numbers.
        turns = [{"role": "user", "text": importer.RESUME_NOTE}, {"role": "assistant", "text": f"{comment}\n\n{panel}"}]
    elif panel and "```info" not in turns[-1]["text"].lower():
        turns[-1]["text"] = f"{comment}\n\n{panel}\n\n{turns[-1]['text']}"
    now = time.time()
    s["turns"] = [{**t, "ts": now, "imported": True} for t in turns]
    s["summary_upto"] = 0
    store.save_story(s)
    return {"story": s, "warnings": warnings}


@app.post("/api/stories/{sid}/avatar-image")
async def upload_avatar_image(sid: str, request: Request):
    """Body is the raw image file; Content-Type says which kind (png/jpeg/webp/gif)."""
    s = _story(sid)
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    if not ext:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image")
    data = await request.body()
    if not data or len(data) > MAX_IMAGE:
        raise HTTPException(413, "Image must be between 1 byte and 8 MB")
    folder = store.PORTRAITS / sid
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("avatar.*"):
        old.unlink()
    (folder / f"avatar.{ext}").write_bytes(data)
    s["avatar_image"] = f"/portraits/{sid}/avatar.{ext}?v={int(time.time())}"
    _set_player_portrait(s, data, ext)
    store.save_story(s)
    return s


def _set_player_portrait(s: dict, data: bytes, ext: str, look: str = "") -> None:
    """Keep the player's picture in the cast too (flagged player), so scenes can use it as a face reference and the prompt can show it if it wants to."""
    name = (s.get("avatar") or {}).get("name") or s.get("player_name") or ""
    if not name:
        return
    ch = _ensure_char(s, name, look)
    ch["player"] = True
    _store_mood(s, ch, "normal", data, ext)


class AvatarGenIn(BaseModel):
    avatar: dict[str, str] = {}      # the form as it is now (unsaved edits count)


AVATAR_LOOK_SYSTEM = ("From a story character's sheet, write a purely VISUAL description for an image generator, as one line of comma-separated details: "
                      "apparent age and gender, ethnicity or skin tone if stated, hair, eyes, build, usual clothing, distinctive features. "
                      "Leave out personality, background, abilities, and anything sexual, anatomical or explicit. Keep it safe for work. Output only the line.")


async def _draw_avatar(s: dict, cfg: dict, av: dict) -> tuple[bytes, str, str]:
    """(image bytes, extension, visual description) for an avatar sheet, styled for the story's world."""
    if not (av.get("appearance") or av.get("gender") or av.get("age")):
        raise HTTPException(400, "Fill in at least the appearance (or gender and age) in the Avatar section first.")
    prov = _img_provider(cfg, None, s)
    rprov, rmodel = _role(cfg, s, "recaps", "avatar picture")
    art = await _art_with_setting(s, cfg) if s.get("id") else dict(s["art"])
    sheet = "\n".join(f"{k}: {v}" for k, v in av.items() if k in ("name", "gender", "age", "appearance", "background"))
    if art.get("setting"):
        sheet += f"\nThe story's world (dress them to fit it unless the sheet says otherwise): {art['setting']}"
    look = (await providers.complete(rprov, rmodel, AVATAR_LOOK_SYSTEM, [{"role": "user", "content": sheet}], temperature=0.3, max_tokens=250,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')
    name = av.get("name", "the player")
    negative = art.get("negative", "")
    if _wants_tags(prov):
        prompt, negative = await _tag_prompt(s, cfg, art, "character portrait, upper body, looking at viewer", [{"name": name, "appearance": look}])
    else:
        prompt = images.portrait_prompt(art, name, look, None, False)
    async with _image_lock:
        try:
            data, ext = await images.generate(prov, prompt, negative, art.get("portrait_shape", "landscape"))
        except images.ImageError as e:
            raise HTTPException(502, str(e))
    return data, ext, look


@app.post("/api/stories/{sid}/avatar-image/generate")
async def generate_avatar(sid: str, body: AvatarGenIn):
    """Draw a candidate player picture from the Avatar sheet. Nothing is saved as the avatar until .../use."""
    s, cfg = _story(sid), store.load_config()
    av = {k: v.strip() for k, v in {**(s.get("avatar") or {}), **body.avatar}.items() if k in AVATAR_FIELDS and v.strip()}
    data, ext, look = await _draw_avatar(s, cfg, av)
    folder = store.PORTRAITS / sid
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("_avatar_candidate.*"):
        old.unlink()
    (folder / f"_avatar_candidate.{ext}").write_bytes(data)
    return {"candidate": f"/portraits/{sid}/_avatar_candidate.{ext}?v={int(time.time())}", "look": look}


class AvatarUseIn(BaseModel):
    look: str = ""


@app.post("/api/stories/{sid}/avatar-image/use")
def use_avatar_candidate(sid: str, body: AvatarUseIn):
    s = _story(sid)
    cand = next((f for f in (store.PORTRAITS / sid).glob("_avatar_candidate.*")), None)
    if not cand:
        raise HTTPException(404, "There is no generated picture to use. Generate one first.")
    data, ext = cand.read_bytes(), cand.suffix.lstrip(".")
    for old in (store.PORTRAITS / sid).glob("avatar.*"):
        old.unlink()
    (store.PORTRAITS / sid / f"avatar.{ext}").write_bytes(data)
    s["avatar_image"] = f"/portraits/{sid}/avatar.{ext}?v={int(time.time())}"
    _set_player_portrait(s, data, ext, body.look)
    cand.unlink(missing_ok=True)
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/avatar-image")
def delete_avatar_image(sid: str):
    s = _story(sid)
    for old in (store.PORTRAITS / sid).glob("avatar.*"):
        old.unlink()
    s["avatar_image"] = ""
    store.save_story(s)
    return s


# ---------------- images: art direction, cast & mood portraits, illustrations ----------------
class ArtIn(BaseModel):
    portrait_size: str = "medium"   # how big character portraits show in the story: small / medium / large
    setting: str | None = None          # the story's world as a picture-maker should see it (None = keep)
    portrait_shape: str = "landscape"   # shape of NEW character portraits: landscape (3:2 cards), portrait or square
    suggest: bool = True                # offer to create art for named characters that have none
    direction: str = ""
    negative: str = ""
    size: str = "portrait"
    auto: bool = False
    portraits: bool = True


class CastMember(BaseModel):
    name: str
    appearance: str = ""
    bio: str | None = None   # None = keep what is saved
    slug: str | None = None  # keeps a character's pictures when you rename them
    personality: str | None = None
    author: bool | None = None   # tell the narrator about this character (bio + personality)


class CastIn(BaseModel):
    cast: list[CastMember]


class GenPortraitIn(BaseModel):
    mood: str = "default"
    provider: str | None = None


class IllustrateIn(BaseModel):
    turn: int | None = None            # which reply (default: the latest)
    scene: str | None = None           # override the scene description
    characters: list[str] | None = None
    provider: str | None = None


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def _img_provider(cfg: dict, override: str | None = None, s: dict | None = None) -> dict:
    chosen = ((s or {}).get("models") or {}).get("images") or {}
    pid = override or chosen.get("provider") or cfg.get("image_active")
    usage.set_ctx((s or {}).get("id"), "pictures")
    prov = next((p for p in cfg.get("image_providers", []) if p["id"] == pid), None)
    if not prov:
        raise HTTPException(400, "No image maker is selected. Choose one under Settings → AI choices.")
    if chosen.get("model") and pid == chosen.get("provider") and not override:
        prov = {**prov, "model": chosen["model"]}   # this story's picture model (an online model name or a ComfyUI checkpoint)
    return prov


def _local_file(url: str, root: Path, prefix: str) -> Path | None:
    """Map one of our /portraits/... or /images/... URLs to a file, refusing anything outside its folder."""
    rel = url.split("?")[0]
    if not rel.startswith(prefix):
        return None
    f = (root / rel[len(prefix):]).resolve()
    return f if f.is_relative_to(root.resolve()) and f.is_file() else None


def _read_ref(url: str) -> tuple[bytes, str] | None:
    f = _local_file(url, store.PORTRAITS, "/portraits/")
    if not f:
        return None
    data = f.read_bytes()
    return data, images.sniff(data)[1]


def _delete_image_file(url: str) -> None:
    f = _local_file(url, store.IMAGES, "/images/")
    if f:
        f.unlink(missing_ok=True)


def _save_scene_image(sid: str, data: bytes, ext: str) -> str:
    folder = store.IMAGES / sid
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{int(time.time() * 1000)}.{ext}"
    (folder / name).write_bytes(data)
    return f"/images/{sid}/{name}"


def _store_mood(s: dict, ch: dict, mood: str, data: bytes, ext: str) -> None:
    """Save a picture as this character's `mood`. A picture already stored under that mood is kept as an alternate:
    every saved picture of a character is reference material that helps later pictures look like them."""
    mood = mood.strip().lower()
    folder = store.PORTRAITS / s["id"] / "chars" / ch["slug"]
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{_slug(mood)}-{int(time.time() * 1000)}.{ext}"
    (folder / name).write_bytes(data)
    moods = ch.setdefault("moods", {})
    if moods.get(mood):
        ch.setdefault("alts", {}).setdefault(mood, []).insert(0, moods[mood])
    moods[mood] = f"/portraits/{s['id']}/chars/{ch['slug']}/{name}"


def _portrait_file(url: str) -> Path | None:
    return _local_file(url, store.PORTRAITS, "/portraits/")


def _portrait_urls(ch: dict, hint: str | None = None, ext: bool = True) -> list[str]:
    """All of a character's saved pictures, best reference first: the mood that fits, the default, other moods, alternates,
    then (ext=True) pictures borrowed from another story."""
    moods, alts = ch.get("moods") or {}, ch.get("alts") or {}
    out: list[str] = []
    def add(u):
        if u and u not in out:
            out.append(u)
    if hint:
        add(moods.get(hint.strip().lower()))
    # Neutral pictures first: expressive ones (angry, combat, ...) carry their own lighting and colour, and using them as references
    # for later pictures makes the whole set drift. Identity is stable in the neutral ones.
    for name in ("default", "normal 1", "normal", "normal 2", "smile 1", "smile 2"):
        add(moods.get(name))
    for m in sorted(moods):
        add(moods[m])
    for m in sorted(alts):
        for u in alts[m]:
            add(u)
    if ext:
        for r in ch.get("ext_refs") or []:
            add(r.get("url"))
    return out


def _load_refs(urls: list[str], n: int) -> list[tuple[bytes, str]]:
    refs = []
    for u in urls:
        r = _read_ref(u)
        if r:
            refs.append(r)
        if len(refs) >= n:
            break
    return refs


def _char(s: dict, slug: str) -> dict:
    ch = next((c for c in s.get("cast", []) if c["slug"] == slug), None)
    if not ch:
        raise HTTPException(404, "Character not found")
    return ch


def _cast_ref_url(ch: dict) -> str | None:
    moods = ch.get("moods") or {}
    return moods.get("default") or next(iter(sorted(moods.values())), None)


@app.put("/api/stories/{sid}/art")
def update_art(sid: str, body: ArtIn):
    s = _story(sid)
    s["art"] = {"direction": body.direction.strip(), "negative": body.negative.strip(),
                "size": body.size if body.size in images.SHAPES else "portrait", "auto": body.auto, "portraits": body.portraits,
                "portrait_size": body.portrait_size if body.portrait_size in ("small", "medium", "large") else "medium",
                "portrait_shape": body.portrait_shape if body.portrait_shape in images.SHAPES else "landscape", "suggest": body.suggest,
                "setting": (body.setting if body.setting is not None else (s.get("art") or {}).get("setting", "")).strip()}
    store.save_story(s)
    return s


SETTING_SYSTEM = ("From a story prompt, describe the WORLD that a character in this story belongs to, for an image generator: era and place, tone and atmosphere, "
                  "typical clothing and technology, the kind of colours and mood. Visual only, under 45 words, one paragraph. No plot, no character names, nothing sexual or explicit. Output only the description.")


async def _make_setting(s: dict, cfg: dict) -> str:
    prov, model = _role(cfg, s, "recaps", "story setting")
    out = await providers.complete(prov, model, SETTING_SYSTEM, [{"role": "user", "content": f"{s['title']}\n\n{s['base_prompt'][:7000]}"}],
                                   temperature=0.3, max_tokens=200, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    return " ".join(out.split()).strip('"')


async def _art_with_setting(s: dict, cfg: dict) -> dict:
    """The story's art settings plus its setting line, so every generated character fits the world. The line is written once from the story prompt, then kept (editable in Player → Art style)."""
    art = dict(s.get("art") or {})
    if not art.get("setting") and s.get("base_prompt", "").strip():
        try:
            art["setting"] = await _make_setting(s, cfg)
            fresh = _story(s["id"])
            fresh["art"] = {**fresh["art"], "setting": art["setting"]}
            store.save_story(fresh)
        except Exception:
            art["setting"] = ""
    return art


@app.post("/api/stories/{sid}/art/setting")
async def regenerate_setting(sid: str):
    s, cfg = _story(sid), store.load_config()
    s["art"] = {**s["art"], "setting": await _make_setting(s, cfg)}
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/cast")
def update_cast(sid: str, body: CastIn):
    """Replace the character list (names + appearance). Pictures are kept for characters that keep their slug."""
    s = _story(sid)
    old = {c["slug"]: c for c in s.get("cast", [])}
    new, seen = [], set()
    for m in body.cast:
        name = m.name.strip()
        slug = m.slug if (m.slug in old) else _slug(name)
        if not name or slug in seen:
            continue
        seen.add(slug)
        prev = old.get(slug, {})
        new.append({"name": name, "slug": slug, "appearance": m.appearance.strip(),
                    "bio": prev.get("bio", "") if m.bio is None else m.bio.strip(),
                    "events": prev.get("events", []), "moods": prev.get("moods", {}), "alts": prev.get("alts", {}),
                    "group": prev.get("group", ""), "player": prev.get("player", False), "ext_refs": prev.get("ext_refs", []),
                    "personality": prev.get("personality", "") if m.personality is None else m.personality.strip(),
                    "author": prev.get("author", False) if m.author is None else m.author})
    for slug in old.keys() - seen:
        shutil.rmtree(store.PORTRAITS / sid / "chars" / slug, ignore_errors=True)
    s["cast"] = new
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/group-cast")
def set_group_cast(sid: str, on: bool):
    """Per-story switch: show grouped characters as one card (on) or every character separately (off). Groups themselves are kept."""
    s = _story(sid)
    s["group_cast"] = on
    store.save_story(s)
    return s


class GroupIn(BaseModel):
    slugs: list[str]
    group: str = ""     # "" = take them out of any group


@app.put("/api/stories/{sid}/cast-group")
def set_cast_group(sid: str, body: GroupIn):
    """Show several characters as ONE card in the Cast (e.g. "Generic Characters" for extras). Each keeps its own pictures and bio."""
    s = _story(sid)
    for c in s["cast"]:
        if c["slug"] in body.slugs:
            c["group"] = body.group.strip()
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/{slug}/mood/{mood}")
async def upload_mood(sid: str, slug: str, mood: str, request: Request):
    """Body is the raw image file. The mood name is the picture's name, e.g. default, angry, smile."""
    s = _story(sid)
    ch = _char(s, slug)
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    _store_mood(s, ch, mood, data, ext)
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/cast/{slug}/mood/{mood}")
def delete_mood(sid: str, slug: str, mood: str):
    """Remove the active picture for a mood. If an earlier version exists it becomes the active one."""
    s = _story(sid)
    ch = _char(s, slug)
    mood = mood.strip().lower()
    cur = (ch.get("moods") or {}).get(mood)
    f = _portrait_file(cur) if cur else None
    if f:
        f.unlink(missing_ok=True)
    alts = (ch.get("alts") or {}).get(mood, [])
    if alts:
        ch["moods"][mood] = alts.pop(0)
        if not alts:
            ch["alts"].pop(mood)
    else:
        (ch.get("moods") or {}).pop(mood, None)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/{slug}/mood/{mood}/use")
def use_alternate(sid: str, slug: str, mood: str, url: str):
    """Make an earlier picture the active one for this mood (the current one becomes an alternate)."""
    s = _story(sid)
    ch = _char(s, slug)
    mood = mood.strip().lower()
    alts = (ch.get("alts") or {}).get(mood, [])
    if url not in alts:
        raise HTTPException(404, "That earlier picture is not saved for this mood")
    alts.remove(url)
    if ch.get("moods", {}).get(mood):
        alts.insert(0, ch["moods"][mood])
    ch["moods"][mood] = url
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/cast/{slug}/alt")
def delete_alternate(sid: str, slug: str, url: str):
    s = _story(sid)
    ch = _char(s, slug)
    for mood, urls in list((ch.get("alts") or {}).items()):
        if url in urls:
            urls.remove(url)
            if not urls:
                ch["alts"].pop(mood)
            f = _portrait_file(url)
            if f:
                f.unlink(missing_ok=True)
    store.save_story(s)
    return s


class FromImageIn(BaseModel):
    url: str
    mood: str = "default"


@app.post("/api/stories/{sid}/cast/{slug}/from-image")
def portrait_from_scene(sid: str, slug: str, body: FromImageIn):
    """Keep a scene picture as one of this character's portraits (it also becomes reference material)."""
    s = _story(sid)
    ch = _char(s, slug)
    f = _local_file(body.url, store.IMAGES, "/images/")
    if not f or not body.url.startswith(f"/images/{sid}/"):
        raise HTTPException(404, "That picture is not part of this story")
    data = f.read_bytes()
    _store_mood(s, ch, body.mood.strip() or "default", data, images.sniff(data)[0])
    store.save_story(s)
    return s


async def _draw_mood(sid: str, slug: str, mood: str, provider: str | None = None) -> dict:
    """Draw one portrait. All the character's saved pictures are sent as references, so each saved one reinforces the next.
    Drawing a mood that already exists keeps the old picture as an alternate."""
    s, cfg = _story(sid), store.load_config()
    ch, prov = _char(s, slug), _img_provider(store.load_config(), provider, s)
    mood = mood.strip().lower() or "default"
    refs = _load_refs(_portrait_urls(ch, mood), int(cfg.get("image_max_refs", 3)))
    art = await _art_with_setting(s, cfg)
    ext_only = bool(refs) and not _portrait_urls(ch, mood, ext=False)
    prompt = images.portrait_prompt(art, ch["name"], ch["appearance"], None if mood == "default" else mood, bool(refs), ext_only)
    negative = s["art"].get("negative", "")
    if _wants_tags(prov):
        look = "character portrait, upper body, looking at viewer" + ("" if mood == "default" else f", {_mood_look(mood)}")
        prompt, negative = await _tag_prompt(s, cfg, art, look, [ch])
    try:
        data, ext = await images.generate(prov, prompt, negative, s["art"].get("portrait_shape", "landscape"), refs)
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    s = _story(sid)           # freshest copy right before saving: edits made while this was drawing are kept
    ch = _char(s, slug)
    _store_mood(s, ch, mood, data, ext)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/{slug}/generate")
async def generate_portrait(sid: str, slug: str, body: GenPortraitIn):
    return await _draw_mood(sid, slug, body.mood, body.provider)


# ---------------- character bio and key events ----------------
EVENTS_SYSTEM = (
    "You keep a character log for an interactive story. From the narrator's latest reply, list the KEY events for the named characters: "
    "things that change the story for that character (first meeting, injury, death, transformation, betrayal, confession, promise, "
    "a change in a relationship, an important decision, discovery or acquisition). Ignore routine actions, small talk and scenery. "
    "At most 4 events; one short sentence each, past tense, naming the character. Use ONLY these names: {names}. "
    'Reply with ONLY JSON: {{"events": [{{"character": "<name>", "event": "<sentence>"}}]}}. Use {{"events": []}} when nothing significant happened.'
)
MAX_EVENTS = 200


def _ev(when: str, text: str, turn: int | None, auto: bool) -> dict:
    return {"id": uuid.uuid4().hex[:8], "when": when.strip(), "turn": turn, "text": text.strip(), "auto": auto}


def _events_model(cfg: dict, s: dict | None = None, purpose: str = "key events"):
    return _role(cfg, s, "recaps", purpose)


async def _events_after_turn(sid: str, idx: int) -> None:
    """After a reply is saved: log key events for the characters in it. Runs in the background and must never disturb play."""
    try:
        cfg = store.load_config()
        if cfg.get("events_mode", "every") != "every":
            return
        s = store.load_story(sid)
        if idx >= len(s["turns"]) or s["turns"][idx]["role"] != "assistant" or not s.get("cast"):
            return
        raw = s["turns"][idx]["text"]
        text = _clean_for_scene(raw)
        names = [c["name"] for c in s["cast"] if re.search(rf"\b{re.escape(c['name'].split()[0])}\b", text, re.IGNORECASE)]
        if not names:
            return
        move = s["turns"][idx - 1]["text"] if idx >= 1 and s["turns"][idx - 1]["role"] == "user" else ""
        prov, model = _events_model(cfg, s)
        out = await providers.complete(prov, model, EVENTS_SYSTEM.format(names=", ".join(names)),
                                       [{"role": "user", "content": f"Player's move: {move[:600]}\n\nNarrator's reply:\n{text[:12000]}"}],
                                       temperature=0.2, max_tokens=500, num_ctx=cfg["num_ctx"], **_extras(cfg))
        j = _json_in(out)
        events = j.get("events") if isinstance(j, dict) else None
        if not events:
            return
        m = re.search(r"Turn:\s*(\d+)", raw)
        label = f"Turn {m.group(1)}" if m else f"Reply {(idx + 1) // 2}"
        s = store.load_story(sid)   # freshest copy right before saving
        by_name = {c["name"].lower(): c for c in s["cast"]}
        changed = False
        for e in events[:6]:
            ch = by_name.get(str(e.get("character", "")).strip().lower())
            line = str(e.get("event", "")).strip()
            if not ch or not line:
                continue
            lst = ch.setdefault("events", [])
            if any(x["text"].lower() == line.lower() for x in lst[-8:]):
                continue
            lst.append(_ev(label, line, idx, True))
            del lst[:-MAX_EVENTS]
            changed = True
        if changed:
            store.save_story(s)
    except Exception:
        pass


def _event_context(s: dict, name: str, limit: int = 7000) -> str:
    """Passages that mention a character, each tagged with the [Turn N] marker of the summary entry or reply it came from."""
    first = name.split()[0]
    pat = re.compile(rf"\b{re.escape(first)}\b", re.IGNORECASE)
    out, size = [], 0
    marker = ""
    for line in (s.get("summary", "") or "").splitlines():
        m = re.search(r"Turn:\s*(\d+)", line)
        if m:
            marker = f"[Turn {m.group(1)}] "
        elif line.strip() and pat.search(line):
            out.append(marker + line.strip()[:900])
            size += len(out[-1])
    for i, t in enumerate(s.get("turns", [])):
        if t["role"] != "assistant":
            continue
        raw = t["text"]
        m = re.search(r"Turn:\s*(\d+)", raw)
        tag = f"[Turn {m.group(1)}] " if m else f"[Reply {(i + 1) // 2}] "
        for line in re.split(r"\n+", _clean_for_scene(raw)):
            if pat.search(line) and len(line) > 30:
                out.append(tag + line.strip()[:400])
                size += len(out[-1])
    text = "\n".join(out)
    return text[-limit:] if len(text) > limit else text


class EventIn(BaseModel):
    when: str = ""
    text: str


@app.post("/api/stories/{sid}/cast/{slug}/events")
def add_event(sid: str, slug: str, body: EventIn):
    s = _story(sid)
    ch = _char(s, slug)
    if not body.text.strip():
        raise HTTPException(400, "Write what happened")
    ch.setdefault("events", []).append(_ev(body.when or "", body.text, None, False))
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/cast/{slug}/events/{eid}")
def edit_event(sid: str, slug: str, eid: str, body: EventIn):
    s = _story(sid)
    ev = next((e for e in _char(s, slug).get("events", []) if e["id"] == eid), None)
    if not ev:
        raise HTTPException(404, "Event not found")
    ev["when"], ev["text"], ev["auto"] = body.when.strip(), body.text.strip(), False   # an edited event is yours now
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/cast/{slug}/events/{eid}")
def delete_event(sid: str, slug: str, eid: str):
    s = _story(sid)
    ch = _char(s, slug)
    ch["events"] = [e for e in ch.get("events", []) if e["id"] != eid]
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/{slug}/events/rebuild")
async def rebuild_events(sid: str, slug: str):
    """Build this character's event log from the story so far (summary + replies). Replaces automatic entries; yours are kept."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    ctx = _event_context(s, ch["name"])
    if not ctx:
        raise HTTPException(404, f"The story text doesn't mention {ch['name']} yet.")
    prov, model = _events_model(cfg, s, "key events rebuild")
    system = ("From story material, write the KEY events in one character's story, in story order: first meeting, injuries, deaths or "
              "transformations, betrayals, confessions, promises, relationship changes, important decisions and discoveries. Skip routine actions. "
              "Each passage is tagged with its [Turn N]; use those tags for 'when'. At most 25 events, one short past-tense sentence each. "
              'Reply with ONLY JSON: {"events": [{"when": "Turn 40", "event": "..."}]}')
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Character: {ch['name']}\n\nMaterial:\n{ctx}"}],
                                   temperature=0.2, max_tokens=2500, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    events = j.get("events") if isinstance(j, dict) else None
    if not isinstance(events, list):
        raise HTTPException(502, "The text model did not return an event list. Try again.")
    s = _story(sid)
    ch = _char(s, slug)
    mine = [e for e in ch.get("events", []) if not e.get("auto")]
    built = []
    for e in events[:25]:
        txt = str(e.get("event", "")).strip()
        if txt:
            built.append(_ev(str(e.get("when", "")).strip(), txt, None, True))
    key = lambda e: (int(re.search(r"\d+", e["when"]).group()) if re.search(r"\d+", e["when"] or "") else 10**6)
    ch["events"] = sorted(mine + built, key=key)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/{slug}/bio-suggest")
async def suggest_bio(sid: str, slug: str):
    """Write a short story bio (role, personality, relationships) from what the story says about this character."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    ctx = _context_about(s, ch["name"])
    if not ctx:
        raise HTTPException(404, f"The story text doesn't mention {ch['name']} yet, so there is nothing to base a bio on. Type one in.")
    prov, model = _events_model(cfg, s, "bio")
    system = ("You write a short character bio for a story's cast list: who they are, their role, personality, motives and key relationships, "
              "in 2 to 4 sentences. Use only what the text states or clearly implies. Reply with ONLY JSON: {\"bio\": \"...\"}.")
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Character: {ch['name']}\n\nWhat the story says:\n{ctx}"}],
                                   temperature=0.3, max_tokens=400, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    text = str(j.get("bio", "")).strip() if isinstance(j, dict) else ""
    if not text:
        raise HTTPException(502, "The text model did not return a bio. Try again, or type one in.")
    return {"bio": text}


@app.get("/api/stories/{sid}/cast/{slug}/prompt-lines")
def prompt_lines(sid: str, slug: str):
    """What the story prompt itself says about this character (free): a starting point for the bio."""
    s = _story(sid)
    ch = _char(s, slug)
    pat = re.compile(rf"\b{re.escape(ch['name'].split()[0])}\b", re.IGNORECASE)
    lines = [re.sub(r"[*_`]", "", ln).strip(" -•") for ln in re.split(r"\n+", s.get("base_prompt", "")) if pat.search(ln) and 20 < len(ln) < 900]
    return {"lines": lines[:3]}


# ---------------- new-character art: suggestions, candidates, choosing, mood sets (background jobs) ----------------
_jobs: dict[str, dict] = {}
_image_lock = asyncio.Lock()   # one picture at a time, so a local GPU is never asked for two at once


def _new_job(kind: str, sid: str, total: int) -> dict:
    for k in [k for k, j in _jobs.items() if time.time() - j["started"] > 3600]:
        _jobs.pop(k)
    job = {"id": uuid.uuid4().hex[:10], "kind": kind, "story": sid, "status": "running", "done": 0, "total": total,
           "current": "", "error": "", "result": {}, "started": time.time()}
    _jobs[job["id"]] = job
    return job


async def _run_job(job: dict, work) -> None:
    async with _image_lock:
        try:
            await work(job)
            job["status"] = "done"
        except HTTPException as e:
            job["status"], job["error"] = "error", str(e.detail)
        except Exception as e:  # shown to the player instead of failing silently
            job["status"], job["error"] = "error", str(e)[:300] or e.__class__.__name__
        job["current"] = ""


@app.get("/api/jobs/{jid}")
def get_job(jid: str):
    if jid not in _jobs:
        raise HTTPException(404, "Unknown job")
    return _jobs[jid]


class DismissIn(BaseModel):
    names: list[str]


def _suggestions(s: dict) -> dict:
    on = bool((s.get("art") or {}).get("suggest", True))
    return {"enabled": on, "missing_art": characters.missing_art(s) if on else [], "new": characters.detect_new(s) if on else []}


@app.get("/api/stories/{sid}/art-suggestions")
def art_suggestions(sid: str):
    """Named characters with no art: cast entries that have a description but no pictures, plus new names seen in the story. Free (no text model)."""
    return _suggestions(_story(sid))


@app.post("/api/stories/{sid}/art-suggestions/dismiss")
def dismiss_art_suggestions(sid: str, body: DismissIn):
    """'Never ask about this character': remembered with the story."""
    s = _story(sid)
    have = {n.lower() for n in s.get("art_dismissed", [])}
    s["art_dismissed"] = s.get("art_dismissed", []) + [n.strip() for n in body.names if n.strip() and n.strip().lower() not in have]
    store.save_story(s)
    return _suggestions(s)


class DescribeIn(BaseModel):
    name: str


def _context_about(s: dict, name: str, limit: int = 3200) -> str:
    """Lines of the story prompt, summary, opening and recent replies that mention this character."""
    pat = re.compile(rf"\b{re.escape(name.split()[0])}\b", re.IGNORECASE) if " " not in name else re.compile(re.escape(name), re.IGNORECASE)
    sources = [s.get("base_prompt", ""), s.get("summary", ""), s.get("opening", "")] + [t["text"] for t in s.get("turns", [])[-8:] if t["role"] == "assistant"]
    seen, out, size = set(), [], 0
    for src in sources:
        for line in re.split(r"\n+", re.sub(r"```.*?```", "", src, flags=re.DOTALL)):
            line = line.strip()
            if line and pat.search(line) and line not in seen:
                seen.add(line)
                out.append(line[:700])
                size += len(out[-1])
                if size >= limit:
                    return "\n".join(out)
    return "\n".join(out)


@app.post("/api/stories/{sid}/cast/describe")
async def describe_character(sid: str, body: DescribeIn):
    """Write a visual description of a character from what the story says about them (one small text-model call)."""
    s, cfg = _story(sid), store.load_config()
    name = body.name.strip()
    ctx = _context_about(s, name)
    if not ctx:
        raise HTTPException(404, f"The story text doesn't mention {name} yet, so there is nothing to base a description on. Type one in.")
    prov, model = _role(cfg, s, "recaps", "describe look")
    system = ("You write visual descriptions of story characters for an image generator. Reply with ONLY JSON: {\"appearance\": \"...\"}. "
              "The appearance is one line of comma-separated visual details: age and gender, ethnicity if stated, hair, eyes, build, clothing and "
              "distinctive features. Use only what the text states or clearly implies. No personality, plot or abilities. Keep it safe for work.")
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Character: {name}\n\nWhat the story says:\n{ctx}"}],
                                   temperature=0.3, max_tokens=300, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    text = str(j.get("appearance", "")).strip() if isinstance(j, dict) else ""
    if not text:
        raise HTTPException(502, "The text model did not return a description. Try again, or type one in.")
    return {"appearance": text}


class CandidatesIn(BaseModel):
    name: str
    appearance: str = ""
    notes: str = ""      # extra wishes for this drawing only ("glasses, scar over left eye")
    count: int = 4
    provider: str | None = None


def _ensure_char(s: dict, name: str, appearance: str) -> dict:
    ch = next((c for c in s["cast"] if c["name"].lower() == name.lower()), None)
    if ch is None:
        slug, n = _slug(name), 2
        while any(c["slug"] == slug for c in s["cast"]):
            slug, n = f"{_slug(name)}-{n}", n + 1
        ch = {"name": name, "slug": slug, "appearance": "", "bio": "", "events": [], "moods": {}, "alts": {}}
        s["cast"].append(ch)
    if appearance.strip():
        ch["appearance"] = appearance.strip()
    return ch


@app.post("/api/stories/{sid}/cast/candidates")
async def cast_candidates(sid: str, body: CandidatesIn):
    """Draw a few independent candidate pictures of a character (added to the cast if new). Choose one with .../choose."""
    s, cfg = _story(sid), store.load_config()
    prov = _img_provider(cfg, body.provider, s)
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the character a name")
    ch = _ensure_char(s, name, body.appearance)
    if not ch["appearance"].strip():
        raise HTTPException(400, "Describe how this character looks first (or use 'Suggest from the story')")
    store.save_story(s)
    slug, count, notes = ch["slug"], max(1, min(int(body.count), 8)), body.notes.strip()
    job = _new_job("candidates", sid, count)
    job["result"] = {"slug": slug, "candidates": []}

    async def work(j: dict) -> None:
        st, cf = _story(sid), store.load_config()
        c = _char(st, slug)
        shape = st["art"].get("portrait_shape", "landscape")
        j["current"] = "Writing the picture prompt…"
        art = await _art_with_setting(st, cf)
        if _wants_tags(prov):
            look = "character portrait, upper body, looking at viewer, neutral relaxed expression" + (f", {notes}" if notes else "")
            prompt, negative = await _tag_prompt(st, cf, art, look, [c])
        else:
            prompt = images.portrait_prompt(art, c["name"], c["appearance"] + (f". Extra notes: {notes}" if notes else ""), None, False)
            negative = st["art"].get("negative", "")
        folder = store.PORTRAITS / sid / "chars" / slug / "_candidates"
        folder.mkdir(parents=True, exist_ok=True)
        cand_refs = _load_refs([r["url"] for r in c.get("ext_refs") or []], int(cf.get("image_max_refs", 3))) if not c.get("moods") else []
        if cand_refs and not _wants_tags(prov):
            prompt = images.portrait_prompt(art, c["name"], c["appearance"] + (f". Extra notes: {notes}" if notes else ""), None, True, True)
        for i in range(count):
            j["current"] = f"Drawing option {i + 1} of {count}…"
            try:
                data, ext = await images.generate(prov, prompt, negative, shape, cand_refs)
            except images.ImageError as e:
                raise HTTPException(502, str(e))
            f = folder / f"cand-{int(time.time() * 1000)}-{i}.{ext}"
            f.write_bytes(data)
            j["result"]["candidates"].append(f"/portraits/{sid}/chars/{slug}/_candidates/{f.name}")
            j["done"] = i + 1

    asyncio.create_task(_run_job(job, work))
    return {"job": job["id"], "slug": slug}


class ChooseIn(BaseModel):
    url: str
    mood: str = "normal 1"


@app.post("/api/stories/{sid}/cast/{slug}/choose")
def choose_candidate(sid: str, slug: str, body: ChooseIn):
    """Keep one candidate as the character's picture (named 'normal 1' by default) and discard the other candidates."""
    s = _story(sid)
    ch = _char(s, slug)
    if not body.url.startswith(f"/portraits/{sid}/chars/{slug}/_candidates/"):
        raise HTTPException(404, "That is not one of this character's candidates")
    f = _portrait_file(body.url)
    if not f:
        raise HTTPException(404, "That candidate is gone. Draw new options.")
    data = f.read_bytes()
    _store_mood(s, ch, body.mood.strip() or "normal 1", data, images.sniff(data)[0])
    shutil.rmtree(store.PORTRAITS / sid / "chars" / slug / "_candidates", ignore_errors=True)
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/cast/{slug}/candidates")
def discard_candidates(sid: str, slug: str):
    _char(_story(sid), slug)
    shutil.rmtree(store.PORTRAITS / sid / "chars" / slug / "_candidates", ignore_errors=True)
    return {"ok": True}


class DrawSetIn(BaseModel):
    preset: str = "basic"            # basic / extended (editable in Settings) / match (the moods your other characters have)
    moods: list[str] | None = None   # or an explicit list
    provider: str | None = None


@app.post("/api/stories/{sid}/cast/{slug}/draw-set")
async def draw_mood_set(sid: str, slug: str, body: DrawSetIn):
    """Draw the rest of a character's pictures in the background, using what they already have as the face reference."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    _img_provider(cfg, body.provider, s)
    if body.moods:
        wanted = [m.strip().lower() for m in body.moods if m.strip()]
    elif body.preset == "match":
        wanted = characters.vocabulary_from_cast(s, slug)
    else:
        wanted = characters.drawable((cfg.get("mood_sets") or characters.DEFAULT_SETS).get(body.preset, []))
    todo = [m for m in dict.fromkeys(wanted) if m not in (ch.get("moods") or {})]
    if not todo:
        raise HTTPException(400, "Nothing to draw: this character already has all of those pictures (or the set is empty).")
    job = _new_job("set", sid, len(todo))
    job["result"] = {"slug": slug, "moods": todo, "drawn": [], "failed": []}

    async def work(j: dict) -> None:
        for i, mood in enumerate(todo):
            j["current"] = f"Drawing “{mood}” ({i + 1} of {len(todo)})…"
            try:
                await _draw_mood(sid, slug, mood, body.provider)
                j["result"]["drawn"].append(mood)
            except HTTPException as e:
                j["result"]["failed"].append(f"{mood}: {e.detail}")
                if len(j["result"]["failed"]) >= 3:   # something is wrong (service down, bad key): stop instead of failing 20 times
                    raise HTTPException(502, "Stopped after 3 failures. " + j["result"]["failed"][-1])
            j["done"] = i + 1

    asyncio.create_task(_run_job(job, work))
    return {"job": job["id"], "moods": todo}


def _clean_for_scene(text: str) -> str:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)           # status panels
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)                  # image tags
    text = re.sub(r"^\[//\]:.*$", "", text, flags=re.MULTILINE)       # hidden comment line
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _json_in(text: str):
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


async def _describe_scene(s: dict, cfg: dict, turn_text: str) -> tuple[str, list[str], dict]:
    """Ask the story's text model for one image prompt from a reply, naming the characters in it."""
    prov, model = _role(cfg, s, "recaps", "scene prompt")
    names = [c["name"] for c in s.get("cast", [])] + [prompting.player_name(s)]
    system = ("You write image-generation prompts from story passages. Pick the single most visual moment. Reply with ONLY JSON: "
              '{"scene": "<one or two sentences: setting, who is present, their poses and expressions, lighting>", '
              '"characters": ["<names of the characters visible>"], "moods": {"<character name>": "<their expression in one word>"}}. '
              "Use only names from the known list. "
              "Keep it safe for work: no nudity or explicit content; for intimate moments choose a different frame.")
    user = f"Known characters: {', '.join(names)}\n\nPassage:\n{_clean_for_scene(turn_text)[:6000]}"
    out = await providers.complete(prov, model, system, [{"role": "user", "content": user}], temperature=0.3,
                                   max_tokens=400, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    if not isinstance(j, dict) or not j.get("scene"):
        raise HTTPException(502, "The text model did not return a usable scene description. Try again, or type one in.")
    moods = {str(k).lower(): str(v).lower() for k, v in (j.get("moods") or {}).items()} if isinstance(j.get("moods"), dict) else {}
    return str(j["scene"]), [str(x) for x in j.get("characters", [])], moods


TAG_QUALITY = "masterpiece, high score, great score, absurdres"
TAG_NEGATIVE = ("lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, cropped, worst quality, "
                "low quality, low score, bad score, signature, watermark, blurry")


MOOD_LOOKS = {   # what to draw for common mood names (numbers like "angry 2" are stripped first)
    "normal": "calm neutral expression", "smile": "gentle warm smile", "shy": "shy, blushing, looking away", "surprise": "surprised, wide eyes, open mouth",
    "sulky": "sulking, pouting, arms crossed", "fear": "frightened, wide eyes, trembling", "sad": "sad, teary eyes, downcast gaze",
    "angry": "angry, furrowed brows, clenched teeth", "scorn": "scornful look, half-lidded eyes, disdainful smirk", "despair": "despair, tears, hollow eyes",
    "combat": "dynamic fighting stance, intense determined expression, action pose", "cute": "cute pose, blushing, playful smile, hands near face",
    "dead": "collapsed on the floor, eyes closed, pale, motionless", "zombie": "undead, pale grey skin, glowing red eyes, torn uniform",
}


def _mood_look(mood: str) -> str:
    base = re.sub(r"\s*\d+$", "", mood.strip().lower())
    return MOOD_LOOKS.get(base, f"{base} expression")


def _wants_tags(prov: dict) -> bool:
    """Anime SDXL models (Animagine, Illustrious, NoobAI...) follow short Danbooru-style tags better than sentences."""
    return (prov.get("prompt_style") or ("tags" if prov.get("type") == "comfyui" else "natural")) == "tags"


async def _tag_prompt(s: dict, cfg: dict, art: dict, scene: str, chars: list[dict]) -> tuple[str, str]:
    """(positive, negative) for a tag-style image model: the text model turns the description into tags."""
    prov, model = _role(cfg, s, "recaps", "image prompt")
    system = ("You write prompts for anime image models in Danbooru tag style. Reply with ONE line of comma-separated lowercase tags: "
              "subject count first (1girl, 2girls, 1boy, ...), then for each character hair, eyes, build and clothing, then expression, "
              "pose, action, setting, lighting and framing (e.g. upper body, from side, wide shot). No sentences, no character names, "
              "no quality tags, no markdown. Keep it safe for work: no nudity or explicit content. If a Setting is given, clothing, props and style must suit that world.")
    who = "\n".join(f"- {c['name']}: {c.get('appearance', '')}" for c in chars) or "(none given)"
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Scene: {scene}\nCharacters:\n{who}" + (f"\nSetting: {art['setting']}" if art.get("setting") else "")}],
                                   temperature=0.3, max_tokens=300, num_ctx=cfg["num_ctx"], **_extras(cfg))
    tags = " ".join(out.replace("\n", " ").replace("`", "").split()).strip(" ,")
    if not tags:
        raise HTTPException(502, "The text model returned no tags. Try again, or switch this image provider to sentence-style prompts.")
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), tags, TAG_QUALITY) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE) if x)
    return positive, negative


def _scene_chars(s: dict, text: str, names: list[str] | None) -> list[dict]:
    """Who is in the picture: the named characters (or any whose name appears in the text), plus the player's avatar."""
    want = {n.lower() for n in (names or [])}
    low = text.lower()
    out = []
    for c in s.get("cast", []):
        first = c["name"].split()[0].lower()
        if c["name"].lower() in want or first in want or re.search(rf"\b{re.escape(first)}\b", low):
            out.append({"name": c["name"], "appearance": c.get("appearance", ""), "slug": c["slug"], "ref_url": None, "ref": False})
    av, pn = s.get("avatar") or {}, prompting.player_name(s)
    if pn != "the player character" and (pn.lower() in want or pn.split()[0].lower() in want or re.search(rf"\b{re.escape(pn.split()[0].lower())}\b", low)):
        out.append({"name": pn, "appearance": av.get("appearance", ""), "ref_url": s.get("avatar_image") or None, "ref": bool(s.get("avatar_image"))})
    # Most prominent first (earliest mention): a face-matching workflow uses the first reference as the main character.
    def first_seen(c: dict) -> int:
        i = low.find(c["name"].split()[0].lower())
        return i if i >= 0 else len(low)
    return sorted(out, key=first_seen)


@app.post("/api/stories/{sid}/illustrate")
async def illustrate(sid: str, body: IllustrateIn):
    """Make a picture of a reply, using the characters' saved portraits as references so faces stay consistent.
    Reference mode per image provider: "characters" = one picture of each character (online services);
    "one_character" = several saved pictures of the main character (for IPAdapter workflows)."""
    s, cfg = _story(sid), store.load_config()
    prov = _img_provider(cfg, body.provider, s)
    idx = body.turn if body.turn is not None else next((i for i in range(len(s["turns"]) - 1, -1, -1) if s["turns"][i]["role"] == "assistant"), None)
    if idx is None or not (0 <= idx < len(s["turns"])) or s["turns"][idx]["role"] != "assistant":
        raise HTTPException(400, "There is no narrator reply to illustrate yet.")
    turn = s["turns"][idx]
    scene, names, hints = (body.scene or turn.get("scene") or "").strip(), body.characters, {}
    if not scene:
        scene, found, hints = await _describe_scene(s, cfg, turn["text"])
        names = names or found
    chars = _scene_chars(s, scene + " " + " ".join(names or []), names)
    max_refs = int(cfg.get("image_max_refs", 3))
    by_slug = {c["slug"]: c for c in s.get("cast", [])}
    mode = prov.get("ref_mode") or ("one_character" if prov.get("type") == "comfyui" else "characters")
    refs: list[tuple[bytes, str]] = []
    if mode == "one_character":
        for c in chars:
            ch = by_slug.get(c.get("slug"))
            got = _load_refs(_portrait_urls(ch, hints.get(c["name"].lower())), max_refs) if ch else []
            if got:
                refs, c["ref"] = got, True   # the first character with pictures is the one whose face is matched
                break
    else:
        for c in chars:
            if len(refs) >= max_refs:
                break
            ch = by_slug.get(c.get("slug"))
            urls = _portrait_urls(ch, hints.get(c["name"].lower())) if ch else ([c["ref_url"]] if c.get("ref_url") else [])
            if not urls and c.get("slug") is None and s.get("avatar_image"):
                urls = [s["avatar_image"]]
            got = _load_refs(urls, 1)
            if got:
                refs.append(got[0]); c["ref"] = True
    prompt = images.scene_prompt(s["art"], scene, chars, max_refs)
    art = s["art"]
    negative = art.get("negative", "")
    if _wants_tags(prov):
        prompt, negative = await _tag_prompt(s, cfg, art, scene, chars)
    try:
        data, ext = await images.generate(prov, prompt, negative, art.get("size", "portrait"), refs)
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    turn.setdefault("images", []).append({"url": _save_scene_image(sid, data, ext), "scene": scene, "provider": prov["id"], "ts": time.time()})
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/images")
def delete_scene_image(sid: str, url: str):
    s = _story(sid)
    for t in s["turns"]:
        t["images"] = [im for im in t.get("images", []) if im.get("url") != url] if t.get("images") else t.get("images")
        if t.get("images") == []:
            t.pop("images")
    _delete_image_file(url)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/suggest")
async def suggest_cast(sid: str):
    """Read the story prompt and propose a cast list with visual descriptions (nothing is saved until you accept)."""
    s, cfg = _story(sid), store.load_config()
    prov, model = _role(cfg, s, "recaps", "suggest cast")
    system = ("From a story prompt, list the named recurring characters with a purely VISUAL description for an image generator: "
              "age/gender, hair, eyes, build, usual clothing, distinctive items. Skip personality, plot and abilities. "
              'Reply with ONLY JSON: [{"name": "...", "appearance": "..."}]. At most 40 characters.')
    out = await providers.complete(prov, model, system, [{"role": "user", "content": s.get("base_prompt", "")[:24000]}], temperature=0.2,
                                   max_tokens=4000, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    if not isinstance(j, list):
        raise HTTPException(502, "The text model did not return a character list. Try again.")
    return {"cast": [{"name": str(c.get("name", "")).strip(), "appearance": str(c.get("appearance", "")).strip()} for c in j if isinstance(c, dict) and c.get("name")]}


class ImageTestIn(BaseModel):
    provider: str
    settings: dict = {}  # unsaved edits from the settings screen; blank values mean "use what is saved"


@app.post("/api/image-test")
async def image_test(body: ImageTestIn):
    """Draw one small picture with a saved image provider, to check that the key/URL/model work."""
    import base64
    prov = _img_provider(store.load_config(), body.provider)
    prov = {**prov, **{k: v for k, v in body.settings.items() if v not in ("", None)}}
    try:
        data, ext = await images.generate(prov, "a simple red apple on a wooden table, soft daylight", "", "square")
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    return {"image": f"data:{images.sniff(data)[1]};base64," + base64.b64encode(data).decode()}


@app.get("/api/images/starter-workflow")
def starter_workflow(kind: str = "plain"):
    """kind=plain: text-to-image. kind=face: IPAdapter face-matching (needs the IPAdapter nodes and models in ComfyUI)."""
    if kind == "face":
        f = ROOT / "workflows" / "comfyui-ipadapter-face.json"
        if f.is_file():
            return json.loads(f.read_text(encoding="utf-8"))
    return images.STARTER_WORKFLOW


class ImportFolderIn(BaseModel):
    path: str
    apply: bool = False   # false = preview only


def _same_bytes(url: str, data: bytes) -> bool:
    f = _portrait_file(url)
    return bool(f and f.stat().st_size == len(data) and f.read_bytes() == data)


def _set_intro(s: dict, files: list[Path]) -> int:
    """Make these pictures the story's intro panels, in order. Unchanged panels are kept as they are."""
    folder = store.PORTRAITS / s["id"] / "intro"
    folder.mkdir(parents=True, exist_ok=True)
    old = {im["name"].lower(): im for im in s.get("intro_images", [])}
    new = []
    for i, f in enumerate(files, 1):
        data = f.read_bytes()
        name = f.stem.strip()
        cur = old.get(name.lower())
        cf = _portrait_file(cur["url"]) if cur else None
        if cf and cf.read_bytes() == data:
            new.append(cur)
            continue
        out = folder / f"{i:02d}-{_slug(name)}-{int(time.time() * 1000)}.{images.sniff(data)[0]}"
        out.write_bytes(data)
        new.append({"name": name, "url": f"/portraits/{s['id']}/intro/{out.name}"})
    keep = {im["url"] for im in new}
    for im in s.get("intro_images", []):
        if im["url"] not in keep:
            f = _portrait_file(im["url"])
            if f:
                f.unlink(missing_ok=True)
    s["intro_images"] = new
    return len(new)


@app.post("/api/stories/{sid}/intro")
async def add_intro_panel(sid: str, name: str, request: Request):
    """Append one intro panel (raw image body). Its name is what ![](intro:name) in the opening refers to."""
    s = _story(sid)
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    folder = store.PORTRAITS / sid / "intro"
    folder.mkdir(parents=True, exist_ok=True)
    n = len(s.get("intro_images", [])) + 1
    out = folder / f"{n:02d}-{_slug(name)}-{int(time.time() * 1000)}.{ext}"
    out.write_bytes(data)
    s.setdefault("intro_images", []).append({"name": name.strip() or f"panel{n}", "url": f"/portraits/{sid}/intro/{out.name}"})
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/intro")
def delete_intro_panel(sid: str, url: str):
    s = _story(sid)
    s["intro_images"] = [im for im in s.get("intro_images", []) if im["url"] != url]
    f = _portrait_file(url)
    if f:
        f.unlink(missing_ok=True)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cast/import-folder")
def import_portrait_folder(sid: str, body: ImportFolderIn):
    """Import a portrait pack from a folder on this computer: <folder>/<Character>/<mood>.webp. Files are copied, never moved.
    Characters are matched to the cast by name (new ones are added). Pictures already imported are skipped."""
    s = _story(sid)
    try:
        pack = portrait_import.scan(body.path, s.get("title", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    cast = s.setdefault("cast", [])
    groups = [(c["name"], c["moods"]) for c in pack["characters"]] + [(x["name"], {"default": x["file"]}) for x in pack["singles"]]
    report = {"root": str(pack["root"]), "skipped": pack["skipped"], "characters": [], "applied": body.apply, "added": 0, "duplicates": 0, "failed": [],
              "intro": len(pack["intro"])}
    for name, moods in groups:
        ch = next((c for c in cast if c["name"].lower() == name.lower()), None)
        report["characters"].append({"name": name, "pictures": len(moods), "existing": ch is not None})
        if not body.apply:
            continue
        if ch is None:
            slug, n = _slug(name), 2
            while any(c["slug"] == slug for c in cast):
                slug, n = f"{_slug(name)}-{n}", n + 1
            ch = {"name": name, "slug": slug, "appearance": "", "bio": "", "events": [], "moods": {}, "alts": {}}
            cast.append(ch)
        for mood, f in moods.items():
            try:
                data = f.read_bytes()
                ext = images.sniff(data)[0]
            except (OSError, images.ImageError) as e:
                report["failed"].append(f"{name}/{f.name}: {e}")
                continue
            known = [(ch.get("moods") or {}).get(mood)] + (ch.get("alts") or {}).get(mood, [])
            if any(u and _same_bytes(u, data) for u in known):
                report["duplicates"] += 1
                continue
            _store_mood(s, ch, mood, data, ext)
            report["added"] += 1
    if body.apply:
        if pack["intro"]:
            report["intro_set"] = _set_intro(s, pack["intro"])
        store.save_story(s)
        report["story"] = s
        import threading
        threading.Thread(target=_warm_thumbs, args=([u for c in cast for u in _portrait_urls(c)],), daemon=True).start()
    return report


def _make_thumb(f: Path, w: int) -> Path:
    from PIL import Image
    key = hashlib.sha1(f"{f}|{f.stat().st_mtime_ns}|{w}".encode()).hexdigest()
    out = store.DATA / "thumbs" / f"{key}.webp"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(f) as im:
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.thumbnail((w, w))
            im.save(out, "WEBP", quality=82)
    return out


def _warm_thumbs(urls: list[str]) -> None:
    """Pre-generate the sizes the screens ask for, in the background, so the first open is instant."""
    for u in urls:
        f = _portrait_file(u)
        for w in (260, 420, 640):
            try:
                if f:
                    _make_thumb(f, w)
            except Exception:
                pass


@app.get("/api/thumb")
def thumbnail(url: str, w: int = 240):
    """A small cached copy of one of our pictures (w = longest side), so screens with many big images stay fast."""
    w = max(64, min(int(w), 1600))
    f = _local_file(url, store.PORTRAITS, "/portraits/") or _local_file(url, store.IMAGES, "/images/")
    if not f:
        raise HTTPException(404, "Picture not found")
    return FileResponse(_make_thumb(f, w), media_type="image/webp", headers={"Cache-Control": "public, max-age=86400"})


@app.delete("/api/stories/{sid}")
def delete_story(sid: str):
    _story(sid)
    store.delete_story(sid)
    return {"ok": True}


@app.get("/api/stories/{sid}/export", response_class=PlainTextResponse)
def export_story(sid: str):
    s = _story(sid)
    lines = [f"# {s['title']}", s.get("subtitle", ""), ""]
    for t in s["turns"]:
        lines.append(("> " + t["text"]) if t["role"] == "user" else t["text"])
        lines.append("")
    return "\n".join(lines)


@app.post("/api/stories/{sid}/undo")
def undo(sid: str):
    """Remove the last exchange (player input + narrator reply) and restore the state from before it."""
    s = _story(sid)
    if s["turns"] and s["turns"][-1]["role"] == "assistant":
        last = s["turns"].pop()
        for im in last.get("images", []):
            _delete_image_file(im.get("url", ""))
        if "before" in last:
            prompting.restore(s, last["before"])
    if s["turns"] and s["turns"][-1]["role"] == "user":
        s["turns"].pop()
    s["summary_upto"] = min(s.get("summary_upto", 0), len(s["turns"]))
    store.save_story(s)
    return s


async def _maybe_summarize(story: dict, cfg: dict, prov: dict, model: str) -> None:
    start = story.get("summary_upto", 0)
    when = max(int(cfg.get("summarize_when", 24)), 4)
    keep = max(min(int(cfg.get("keep_recent", 10)), when - 2), 2)
    if len(story["turns"]) - start <= when:
        return
    cut = len(story["turns"]) - keep
    cut -= cut % 2  # keep the recent window starting on a player turn
    if cut <= start:
        return
    store.snapshot_story(story["id"], "before summarize")
    summary = await providers.complete(
        prov,
        model,
        prompting.SUMMARY_SYSTEM,
        prompting.summary_request(story.get("summary", ""), story["turns"][start:cut]),
        temperature=0.3,
        max_tokens=3500,
        num_ctx=cfg["num_ctx"],
        **_extras(cfg),
    )
    story["summary"], story["summary_upto"] = summary.strip(), cut
    store.save_story(story)


@app.post("/api/stories/{sid}/turn")
async def turn(sid: str, body: TurnIn):
    story = _story(sid)
    cfg = store.load_config()
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, story, "story")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty input")

    async def gen():
        usage.set_ctx(sid, "story narration")
        try:
            blocked = None if body.force_budget else _budget_block(cfg, prov)
            if blocked:
                yield _sse({"t": "error", "v": blocked})
                return
            pending = _tracker_tasks.get(sid)
            if pending and not pending.done():
                await asyncio.wait({pending}, timeout=60)      # the side panel must be up to date before the narrator writes
                story.update({k: _story(sid)[k] for k in ("tracker",)})
            rprov, rmodel = _role(cfg, story, "recaps", "memory summary")
            await _maybe_summarize(story, cfg, rprov, rmodel)
            usage.set_ctx(sid, "story narration")
            before = prompting.snapshot(story)
            story["turns"].append({"role": "user", "text": text, "ts": time.time()})
            system, messages = prompting.build(story).request()
            full, use = "", {}
            async for chunk in providers.stream_chat(
                prov, model, system, messages,
                temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], num_ctx=cfg["num_ctx"],
                **_extras(cfg),
            ):
                if isinstance(chunk, providers.Usage):
                    use = dict(chunk)
                    continue
                full += chunk
                yield _sse({"t": "delta", "v": chunk})
            narration, upd = prompting.split_state(full)
            narration, scene = prompting.split_scene(narration)
            if not narration:
                hint = " It spent its whole reply budget thinking: raise Max reply tokens or turn Claude thinking off in Settings." if use.get("thinking") else ""
                raise providers.ProviderError("The model returned an empty reply." + hint)
            story["turns"].append(
                {"role": "assistant", "text": narration, "ts": time.time(), "model": f"{prov['id']}/{model}", "before": before,
                 "usage": use, **({"scene": scene} if scene else {})}
            )
            prompting.apply_state(story, upd)
            got_panel = tracker.update_from_reply(story, narration, len(story["turns"]) // 2)
            saved = _save_turn(story)
            _maybe_track_with_ai(saved, len(saved["turns"]) - 1, got_panel)
            if (len(saved["turns"]) // 2) % 5 == 0:
                store.snapshot_story(saved["id"], "every 5 turns")
            asyncio.create_task(_events_after_turn(saved["id"], len(saved["turns"]) - 1))
            _note_cache_use(saved["id"], prov, model, cfg, use)
            yield _sse({"t": "done", "story": saved})
        except Exception as e:  # shown to the player; the turn is not saved
            yield _sse({"t": "error", "v": str(e) or e.__class__.__name__})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------- share packs: prompt + cast + art settings, no save ----------------
PACK_FIELDS = ("title", "subtitle", "description", "base_prompt", "opening", "opening_in_context", "use_state_block", "art", "art_dismissed", "group_cast",
               "prologue", "opening_options", "endings", "sidebar")


@app.get("/api/stories/{sid}/pack")
def export_pack(sid: str, pictures: str = "all"):
    """A story package you can share: prompt, prologue, opening scene and suggested first moves, cast (descriptions, bios, personalities, portraits), suggested side-panel
    trackers, endings, art settings, cover, intro panels and backdrop. It never contains turns, memory, your avatar, notes, API keys or usage. pictures = none | normal | all."""
    s = _story(sid)
    meta = {k: s.get(k) for k in PACK_FIELDS}
    meta["format"] = "storyteller-pack-2"
    meta["cast"], files = [], []
    for c in s.get("cast", []):
        if c.get("player"):      # the player's own avatar stays out of a story package
            continue
        moods = {}
        have = c.get("moods") or {}
        chosen = {} if pictures == "none" else dict(have) if pictures == "all" else (
            {k: have[k] for k in next(([k] for k in ("normal", "default", "normal 1") if k in have), list(have)[:1])})
        for mood, url in chosen.items():
            f = _portrait_file(url)
            if f:
                arc = f"portraits/{c['slug']}/{_slug(mood)}{f.suffix}"
                moods[mood] = arc
                files.append((arc, f))
        meta["cast"].append({"name": c["name"], "slug": c["slug"], "appearance": c.get("appearance", ""), "bio": c.get("bio", ""), "personality": c.get("personality", ""), "author": bool(c.get("author")),
                             "group": c.get("group", ""), "moods": moods})
    meta["intro"] = []
    for im in s.get("intro_images", []) if pictures != "none" else []:
        f = _portrait_file(im["url"])
        if f:
            arc = f"intro/{_slug(im['name'])}{f.suffix}"
            meta["intro"].append({"name": im["name"], "file": arc})
            files.append((arc, f))
    if s.get("cover") and (f := _portrait_file(s["cover"])):
        meta["cover"] = f"cover{f.suffix}"
        files.append((meta["cover"], f))
    if s.get("backdrop", {}).get("url") and (bf := _portrait_file(s["backdrop"]["url"])):
        meta["backdrop"] = f"backdrop{bf.suffix}"
        files.append((meta["backdrop"], bf))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("story.json", json.dumps(meta, indent=2, ensure_ascii=False))
        for arc, f in files:
            z.write(f, arc)
    name = _slug(s["title"])[:40] or "story"
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}-pack.zip"'})


@app.post("/api/stories/import-pack")
async def import_pack(request: Request):
    """Body is the raw .zip made by 'Share pack'. Creates a new story from it."""
    data = await request.body()
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        meta = json.loads(z.read("story.json"))
    except Exception:
        raise HTTPException(400, "That isn't a story pack made by this app.")
    if meta.get("format") not in ("storyteller-pack-1", "storyteller-pack-2"):
        raise HTTPException(400, "Unknown pack version.")
    s = store.new_story(meta.get("title") or "Shared story", meta.get("subtitle", ""), meta.get("base_prompt", ""))
    sid = s["id"]
    for k in PACK_FIELDS:
        if meta.get(k) is not None and k not in ("title", "subtitle", "base_prompt"):
            s[k] = meta[k]

    def save_file(arc: str, dest: Path) -> bool:
        if ".." in arc or arc.startswith("/") or arc not in z.namelist():
            return False
        info = z.getinfo(arc)
        if info.file_size > MAX_IMAGE:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(arc))
        return True

    s["cast"] = []
    for c in meta.get("cast", []):
        slug = _slug(c.get("slug") or c["name"])
        moods = {}
        for mood, arc in (c.get("moods") or {}).items():
            fname = f"{_slug(mood)}-{int(time.time() * 1000)}{Path(arc).suffix}"
            if save_file(arc, store.PORTRAITS / sid / "chars" / slug / fname):
                moods[mood] = f"/portraits/{sid}/chars/{slug}/{fname}"
        s["cast"].append({"name": c["name"], "slug": slug, "appearance": c.get("appearance", ""), "bio": c.get("bio", ""), "personality": c.get("personality", ""),
                          "author": bool(c.get("author")), "group": c.get("group", ""), "events": [], "moods": moods, "alts": {}})
    for im in meta.get("intro", []):
        fname = f"{_slug(im['name'])}{Path(im['file']).suffix}"
        if save_file(im["file"], store.PORTRAITS / sid / "intro" / fname):
            s.setdefault("intro_images", []).append({"name": im["name"], "url": f"/portraits/{sid}/intro/{fname}"})
    if isinstance(meta.get("sidebar"), dict):
        sb = store.STORY_DEFAULTS["sidebar"] | {k: meta["sidebar"][k] for k in ("mode", "fields") if k in meta["sidebar"]}
        sb["schema"] = tracker.clean_schema(meta["sidebar"].get("schema")) or None
        sb["custom"] = tracker.clean_custom(meta["sidebar"].get("custom"))
        s["sidebar"] = sb
    _give_default_models(s)
    if meta.get("backdrop") and save_file(meta["backdrop"], store.PORTRAITS / sid / f"backdrop{Path(meta['backdrop']).suffix}"):
        s["backdrop"] = {"url": f"/portraits/{sid}/backdrop{Path(meta['backdrop']).suffix}?v={int(time.time())}", "source": "package"}
    if meta.get("cover") and save_file(meta["cover"], store.PORTRAITS / sid / f"cover{Path(meta['cover']).suffix}"):
        s["cover"] = f"/portraits/{sid}/cover{Path(meta['cover']).suffix}?v={int(time.time())}"
    store.save_story(s)
    return s


# ---------------- avatar library & cross-story references (both optional, set in Home → Settings → Libraries) ----------------
AVATARS = store.DATA / "avatars.json"


def _avatars() -> list[dict]:
    try:
        return json.loads(AVATARS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_avatars(items: list[dict]) -> None:
    store._write(AVATARS, items)


def _avatar(aid: str) -> dict:
    a = next((x for x in _avatars() if x["id"] == aid), None)
    if not a:
        raise HTTPException(404, "Avatar not found")
    return a


def _avatar_folder(aid: str) -> Path:
    return store.PORTRAITS / "_avatars" / aid


class AvatarSheetIn(BaseModel):
    sheet: dict[str, str] = {}
    name: str = ""


def _clean_sheet(sheet: dict) -> dict:
    return {k: str(v).strip() for k, v in sheet.items() if k in AVATAR_FIELDS and str(v).strip()}


@app.get("/api/avatars")
def list_avatars():
    return _avatars()


@app.post("/api/avatars")
def create_avatar(body: AvatarSheetIn):
    sheet = _clean_sheet(body.sheet)
    a = {"id": uuid.uuid4().hex[:10], "name": body.name.strip() or sheet.get("name") or "New avatar", "sheet": sheet, "image": "", "created": time.time()}
    _save_avatars(_avatars() + [a])
    return a


@app.put("/api/avatars/{aid}")
def update_avatar(aid: str, body: AvatarSheetIn):
    items = _avatars()
    a = next((x for x in items if x["id"] == aid), None)
    if not a:
        raise HTTPException(404, "Avatar not found")
    a["sheet"] = _clean_sheet(body.sheet)
    a["name"] = body.name.strip() or a["sheet"].get("name") or a["name"]
    _save_avatars(items)
    return a


@app.delete("/api/avatars/{aid}")
def delete_avatar(aid: str):
    items = [x for x in _avatars() if x["id"] != aid]
    _save_avatars(items)
    shutil.rmtree(_avatar_folder(aid), ignore_errors=True)
    return {"ok": True}


def _set_avatar_image(aid: str, data: bytes, ext: str) -> dict:
    items = _avatars()
    a = next((x for x in items if x["id"] == aid), None)
    if not a:
        raise HTTPException(404, "Avatar not found")
    folder = _avatar_folder(aid)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("avatar.*"):
        old.unlink()
    (folder / f"avatar.{ext}").write_bytes(data)
    a["image"] = f"/portraits/_avatars/{aid}/avatar.{ext}?v={int(time.time())}"
    _save_avatars(items)
    return a


@app.post("/api/avatars/{aid}/image")
async def upload_library_avatar_image(aid: str, request: Request):
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    return _set_avatar_image(aid, data, ext)


class LibGenIn(BaseModel):
    sheet: dict[str, str] = {}
    story_id: str | None = None        # style it for this story's world and picture maker (optional)


@app.post("/api/avatars/{aid}/generate")
async def generate_library_avatar(aid: str, body: LibGenIn):
    _avatar(aid)
    cfg = store.load_config()
    s = _story(body.story_id) if body.story_id else {"id": None, "title": "", "base_prompt": "", "models": {}, "cast": [], "art": {**store.STORY_DEFAULTS["art"]}}
    data, ext, look = await _draw_avatar(s, cfg, _clean_sheet(body.sheet))
    folder = _avatar_folder(aid)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("_candidate.*"):
        old.unlink()
    (folder / f"_candidate.{ext}").write_bytes(data)
    return {"candidate": f"/portraits/_avatars/{aid}/_candidate.{ext}?v={int(time.time())}", "look": look}


@app.post("/api/avatars/{aid}/use")
def use_library_candidate(aid: str):
    _avatar(aid)
    cand = next(iter(_avatar_folder(aid).glob("_candidate.*")), None)
    if not cand:
        raise HTTPException(404, "Generate a picture first.")
    a = _set_avatar_image(aid, cand.read_bytes(), cand.suffix.lstrip("."))
    cand.unlink(missing_ok=True)
    return a


class SelectAvatarIn(BaseModel):
    id: str


@app.post("/api/stories/{sid}/avatar/select")
def select_story_avatar(sid: str, body: SelectAvatarIn):
    """Use a library avatar in this story (only one at a time). The story gets its own copy of the sheet and picture."""
    s, a = _story(sid), _avatar(body.id)
    store.snapshot_story(sid, "before avatar change")
    for c in [c for c in s.get("cast", []) if c.get("player")]:   # the previous avatar leaves the cast
        s["cast"].remove(c)
        shutil.rmtree(store.PORTRAITS / sid / "chars" / c["slug"], ignore_errors=True)
    for old in (store.PORTRAITS / sid).glob("avatar.*"):
        old.unlink()
    s["avatar"], s["avatar_id"] = dict(a["sheet"]), a["id"]
    s["player_name"] = a["sheet"].get("name", "")
    s["avatar_image"] = ""
    img = next(iter(_avatar_folder(a["id"]).glob("avatar.*")), None)
    if img:
        (store.PORTRAITS / sid).mkdir(parents=True, exist_ok=True)
        data, ext = img.read_bytes(), img.suffix.lstrip(".")
        (store.PORTRAITS / sid / f"avatar.{ext}").write_bytes(data)
        s["avatar_image"] = f"/portraits/{sid}/avatar.{ext}?v={int(time.time())}"
        _set_player_portrait(s, data, ext)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/avatar/save-to-library")
def save_story_avatar(sid: str):
    """Save this story's avatar (sheet + picture) to the library; updates the library entry it came from, if any."""
    s = _story(sid)
    sheet = _clean_sheet(s.get("avatar") or {})
    if not sheet:
        raise HTTPException(400, "Fill in the avatar sheet first.")
    items = _avatars()
    a = next((x for x in items if x["id"] == s.get("avatar_id")), None)
    if a:
        a["sheet"], a["name"] = sheet, sheet.get("name") or a["name"]
        _save_avatars(items)
    else:
        a = create_avatar(AvatarSheetIn(sheet=sheet, name=sheet.get("name", "")))
        s["avatar_id"] = a["id"]
        store.save_story(s)
    f = _portrait_file(s.get("avatar_image", ""))
    if f:
        a = _set_avatar_image(a["id"], f.read_bytes(), f.suffix.lstrip("."))
    return {"avatar": a, "story": s}


@app.get("/api/ref-sources")
def ref_sources(exclude: str = ""):
    """Characters with pictures in your other stories, for borrowing as references."""
    out = []
    for st in store.list_stories():
        if st["id"] == exclude:
            continue
        s = store.load_story(st["id"])
        chars = [{"slug": c["slug"], "name": c["name"], "thumb": (_portrait_urls(c, ext=False) or [""])[0], "count": len(c.get("moods") or {})}
                 for c in s.get("cast", []) if c.get("moods")]
        if chars:
            out.append({"story_id": s["id"], "title": s["title"], "characters": chars})
    return out


class ExtRefIn(BaseModel):
    from_story: str
    from_slug: str


@app.post("/api/stories/{sid}/cast/{slug}/ext-refs")
def add_ext_refs(sid: str, slug: str, body: ExtRefIn):
    """Copy up to 3 of a character's pictures from another story into this character as REFERENCES (identity only; the new story's own style applies)."""
    if not store.load_config().get("reuse_art"):
        raise HTTPException(400, "Turn on 'Reuse character art across stories' in Settings → Libraries first.")
    s = _story(sid)
    ch = _char(s, slug)
    src = _char(_story(body.from_story), body.from_slug)
    src_story = _story(body.from_story)
    label = f"{src_story['title']} / {src['name']}"
    ch["ext_refs"] = [r for r in ch.get("ext_refs") or [] if r.get("source") != label]
    folder = store.PORTRAITS / sid / "chars" / slug / "_ext"
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for url in _portrait_urls(src, ext=False)[:3]:
        f = _portrait_file(url)
        if not f:
            continue
        name = f"{_slug(src_story['id'])}-{n}-{int(time.time() * 1000)}{f.suffix}"
        (folder / name).write_bytes(f.read_bytes())
        ch["ext_refs"].append({"url": f"/portraits/{sid}/chars/{slug}/_ext/{name}", "source": label})
        n += 1
    if not n:
        raise HTTPException(404, "That character has no pictures to borrow.")
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/cast/{slug}/ext-refs")
def remove_ext_ref(sid: str, slug: str, url: str):
    s = _story(sid)
    ch = _char(s, slug)
    ch["ext_refs"] = [r for r in ch.get("ext_refs") or [] if r.get("url") != url]
    f = _portrait_file(url)
    if f:
        f.unlink(missing_ok=True)
    store.save_story(s)
    return s


# ---------------- prompt tester & model comparison ----------------
class PromptTestIn(BaseModel):
    base_prompt: str
    opening: str = ""
    move: str = "I look around and take stock of what's happening."
    use_state_block: bool = True
    avatar: dict[str, str] = {}
    provider: str | None = None
    model: str | None = None


TEST_CHECK_SYSTEM = """You review a story-engine prompt by checking ONE sample reply from the narrator against it. Reply with ONLY JSON:
{"verdict": "good" or "issues", "findings": [{"severity": "high"|"medium"|"low", "issue": "what is wrong, quoting the reply briefly", "suggestion": "how to fix the PROMPT (one sentence)"}]}
Check: (1) does the reply follow any required format, status panel or structure the prompt demands; (2) does it speak, act or feel for the player's character when the prompt forbids it;
(3) does it leak out-of-character or system text, tags or JSON; (4) does it contradict the prompt's lore, rules or tone; (5) length and pacing versus what the prompt asks.
If everything is fine, return {"verdict": "good", "findings": []}. Be specific and brief; at most 6 findings."""


@app.post("/api/prompt-test")
async def prompt_test(body: PromptTestIn):
    """Dry run: play one move against the (possibly unsaved) prompt and have a second pass check the reply for problems. Costs two small calls."""
    cfg = store.load_config()
    s = {**json.loads(json.dumps(store.STORY_DEFAULTS)), "title": "test", "base_prompt": body.base_prompt, "opening": body.opening, "turns": [], "state": {},
         "summary": "", "summary_upto": 0, "use_state_block": body.use_state_block, "avatar": body.avatar, "player_name": body.avatar.get("name", "")}
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, None, "story")
    usage.set_ctx(None, "prompt test")
    s["turns"].append({"role": "user", "text": body.move})
    system, messages = prompting.build(s).request()
    raw = await providers.complete(prov, model, system, messages, temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], num_ctx=cfg["num_ctx"], **_extras(cfg))
    narration, _ = prompting.split_state(raw)
    narration, _ = prompting.split_scene(narration)
    cprov, cmodel = _role(cfg, None, "recaps", "prompt test")
    out = await providers.complete(cprov, cmodel, TEST_CHECK_SYSTEM, [{"role": "user", "content": f"STORY PROMPT:\n{body.base_prompt[:14000]}\n\nPLAYER'S MOVE:\n{body.move}\n\nNARRATOR'S REPLY:\n{narration[:8000]}"}],
                                   temperature=0.2, max_tokens=900, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    data = _json_in(out) or {}
    return {"reply": narration, "verdict": data.get("verdict", "unknown"), "findings": data.get("findings", []), "model": f"{prov['id']}/{model}"}


class CompareIn(BaseModel):
    text: str
    a: dict
    b: dict


@app.post("/api/stories/{sid}/compare")
async def compare_models(sid: str, body: CompareIn):
    """Send the next move to two models at once, exactly as a real turn would (same prompt, memory and state), without saving anything."""
    s, cfg = _story(sid), store.load_config()
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Type the move to compare first.")
    probe = {**s, "turns": s["turns"] + [{"role": "user", "text": text}]}
    system, messages = prompting.build(probe).request()
    for spec in (body.a, body.b):
        blocked = _budget_block(cfg, _pick(cfg, spec.get("provider"), spec.get("model"))[0])
        if blocked:
            raise HTTPException(402, blocked.replace("BUDGET: ", ""))

    async def one(spec: dict) -> dict:
        prov, model = _pick(cfg, spec.get("provider"), spec.get("model"))
        usage.set_ctx(sid, "model comparison")
        t0, use, raw = time.time(), {}, ""
        try:
            async for chunk in providers.stream_chat(prov, model, system, messages, temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], num_ctx=cfg["num_ctx"], **_extras(cfg)):
                if isinstance(chunk, providers.Usage):
                    use = dict(chunk)
                else:
                    raw += chunk
        except Exception as e:
            return {"provider": prov["id"], "model": model, "error": str(e)[:300] or e.__class__.__name__}
        narration, _ = prompting.split_state(raw)
        narration, _ = prompting.split_scene(narration)
        cost = usage.cost_of(cfg, {"provider": prov["id"], "model": model, "ttl": cfg.get("cache_ttl"), **{k: use.get(k, 0) for k in ("input", "cache_read", "cache_write", "output")}})
        return {"provider": prov["id"], "model": model, "raw": raw, "reply": narration, "seconds": round(time.time() - t0, 1), "usage": use, "cost": cost}

    a, b = await asyncio.gather(one(body.a), one(body.b))
    return {"a": a, "b": b}


class CompareApplyIn(BaseModel):
    text: str
    raw: str
    provider: str
    model: str


@app.post("/api/stories/{sid}/compare/apply")
async def compare_apply(sid: str, body: CompareApplyIn):
    """Keep one of the compared replies as the real next turn."""
    story = _story(sid)
    before = prompting.snapshot(story)
    narration, upd = prompting.split_state(body.raw)
    narration, scene = prompting.split_scene(narration)
    if not narration:
        raise HTTPException(400, "That reply is empty.")
    now = time.time()
    story["turns"].append({"role": "user", "text": body.text.strip(), "ts": now})
    story["turns"].append({"role": "assistant", "text": narration, "ts": now, "model": f"{body.provider}/{body.model}", "before": before, **({"scene": scene} if scene else {})})
    prompting.apply_state(story, upd)
    got_panel = tracker.update_from_reply(story, narration, len(story["turns"]) // 2)
    saved = _save_turn(story)
    _maybe_track_with_ai(saved, len(saved["turns"]) - 1, got_panel)
    asyncio.create_task(_events_after_turn(saved["id"], len(saved["turns"]) - 1))
    return saved


# ---------------- relationships ----------------
_HEART = re.compile(r"❤️?\s*(-?\d+)")


def _panel_rows(text: str):
    """(name, heart value, location, thought) for each row in a status panel's Characters section."""
    inside = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Characters──"):
            inside = True
        elif s == "──":
            inside = False
        elif inside:
            m = re.match(r"^([A-Z][^\n:/|(]{0,28}?):(.*)$", s)
            h = _HEART.search(s)
            if m and h:
                parts = m.group(2).split("/")
                loc = next((p.strip() for p in parts[2:3]), "")
                quote = next((p.strip().strip('"') for p in parts if p.strip().startswith('"')), "")
                yield m.group(1).strip(), int(h.group(1)), loc, quote


@app.get("/api/stories/{sid}/relationships")
def relationships(sid: str):
    """How each character feels about the player over time, read from the story's status panels (❤️ values) or the state block's affinity."""
    s = _story(sid)
    chars: dict[str, dict] = {}
    n = 0
    for i, t in enumerate(s["turns"]):
        if t["role"] != "assistant":
            continue
        n += 1
        turn_no = i // 2 + 1
        rows = list(_panel_rows(t["text"]))
        if not rows:
            aff = ((t.get("before") or {}).get("state") or {}).get("affinity") or {}
            rows = [(k, int(v), "", "") for k, v in aff.items()]
        for name, val, loc, quote in rows:
            c = chars.setdefault(name, {"name": name, "series": []})
            c["series"].append([turn_no, val])
            if loc:
                c["location"] = loc
            if quote:
                c["thought"] = quote
    for name, v in ((s.get("state") or {}).get("affinity") or {}).items():
        c = chars.setdefault(name, {"name": name, "series": []})
        if not c["series"] or c["series"][-1][1] != v:
            c["series"].append([len(s["turns"]) // 2, v])
    out = []
    for c in chars.values():
        ser = c["series"][-60:]
        c["series"] = ser
        c["value"] = ser[-1][1]
        c["delta"] = ser[-1][1] - ser[-2][1] if len(ser) > 1 else 0
        c["slug"] = next((x["slug"] for x in s.get("cast", []) if x["name"].lower().split()[0] == c["name"].lower().split()[0]), "")
        out.append(c)
    out.sort(key=lambda c: -c["value"])
    return {"characters": out, "max": max([c["value"] for c in out] + [100])}


# ---------------- editing turns, bookmarks, branching ----------------
class TurnEditIn(BaseModel):
    text: str


class BookmarkIn(BaseModel):
    label: str = ""


def _turn_at(s: dict, i: int) -> dict:
    if not 0 <= i < len(s["turns"]):
        raise HTTPException(404, "No such turn")
    return s["turns"][i]


@app.put("/api/stories/{sid}/turns/{i}")
def edit_turn(sid: str, i: int, body: TurnEditIn):
    s = _story(sid)
    t = _turn_at(s, i)
    if not body.text.strip():
        raise HTTPException(400, "A turn can't be empty")
    store.snapshot_story(sid, "before turn edit")
    t["text"] = body.text.strip()
    t["edited"] = True
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/turns/{i}/bookmark")
def bookmark_turn(sid: str, i: int, body: BookmarkIn):
    s = _story(sid)
    t = _turn_at(s, i)
    if body.label.strip():
        t["bookmark"] = body.label.strip()[:80]
    else:
        t.pop("bookmark", None)
    store.save_story(s)
    return s


class BranchIn(BaseModel):
    upto: int            # index of the player turn to branch at: it and everything after it are left out of the copy
    title: str = ""


@app.post("/api/stories/{sid}/branch")
def branch_story(sid: str, body: BranchIn):
    """Copy this story up to (not including) one of your moves, so you can try something different. Returns the new story and the move's text."""
    s = _story(sid)
    t = _turn_at(s, body.upto)
    if t["role"] != "user":
        raise HTTPException(400, "Branch from one of your own moves.")
    if s.get("summary_upto", 0) > body.upto:
        raise HTTPException(400, "That point is older than the memory summary, which already includes later events. Branch from a later move, or rebuild the memory first (🧠).")
    new = json.loads(json.dumps(s))
    nid = uuid.uuid4().hex[:12]
    old_folder = store.PORTRAITS / sid
    if old_folder.exists():
        shutil.copytree(old_folder, store.PORTRAITS / nid)
    new = json.loads(json.dumps(new).replace(f"/portraits/{sid}/", f"/portraits/{nid}/"))
    new["id"], new["title"], new["created"] = nid, body.title.strip() or f"{s['title']} (branch)", time.time()
    new["turns"] = new["turns"][: body.upto]
    new["reply_options"] = {}
    nxt = s["turns"][body.upto + 1] if body.upto + 1 < len(s["turns"]) else None
    if nxt and nxt.get("before"):
        prompting.restore(new, nxt["before"])
    elif body.upto > 0 and new["turns"][-1].get("before") is not None:
        pass   # state stays as the last kept reply left it
    store.save_story(new)
    return {"story": new, "text": t["text"]}


# ---------------- memory editor, pinned facts, backups ----------------
class MemoryIn(BaseModel):
    summary: str | None = None
    pinned_facts: list[str] | None = None


@app.put("/api/stories/{sid}/memory")
def put_memory(sid: str, body: MemoryIn):
    s = _story(sid)
    store.snapshot_story(sid, "before memory edit")
    if body.summary is not None:
        s["summary"] = body.summary.strip()
        s["summary_upto"] = min(s.get("summary_upto", 0), len(s["turns"]))
    if body.pinned_facts is not None:
        s["pinned_facts"] = [f.strip() for f in body.pinned_facts if f.strip()]
    store.save_story(s)
    return s


class RebuildIn(BaseModel):
    upto_turn: int | None = None        # summarize the story's own turns before this player turn (1-based); None = all but the recent window
    keep_existing: bool = True          # start from the current summary (right for imported stories: their early history exists only there)
    provider: str | None = None
    model: str | None = None


@app.post("/api/stories/{sid}/memory/rebuild")
async def rebuild_memory(sid: str, body: RebuildIn):
    """Write the memory summary again from the story's turns, part by part (a background job). Use it after fixing a turn, or to re-do a bad summary."""
    s, cfg = _story(sid), store.load_config()
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, s, "recaps")
    keep = max(min(int(cfg.get("keep_recent", 10)), 40), 2)
    cut = (body.upto_turn - 1) * 2 if body.upto_turn else len(s["turns"]) - keep
    cut = max(0, min(cut, len(s["turns"]))); cut -= cut % 2
    start = s.get("summary_upto", 0) if body.keep_existing else 0
    if cut <= start and body.keep_existing:
        raise HTTPException(400, "Nothing new to summarize before that turn. Pick a later turn, or untick 'start from the current summary'.")
    texts = ["\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in s["turns"][start:cut])]
    parts = _chunks(texts[0]) if texts[0].strip() else []
    if not parts:
        raise HTTPException(400, "Nothing to summarize yet: the story's turns are all still within the recent window. Pick an earlier turn number, or play a bit more.")
    job = _new_job("memory", sid, len(parts))
    store.snapshot_story(sid, "before memory rebuild")

    async def run() -> None:
        usage.set_ctx(sid, "memory summary")
        try:
            summary = s.get("summary", "") if body.keep_existing else ""
            for i, part in enumerate(parts):
                job["current"] = f"Summarizing part {i + 1} of {len(parts)}"
                summary = (await providers.complete(prov, model, prompting.SUMMARY_SYSTEM, [{"role": "user", "content": f"EXISTING SUMMARY:\n{summary or '(none yet)'}\n\nNEW EVENTS:\n{part}\n\nWrite the updated summary."}],
                                                    temperature=0.3, max_tokens=3500, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip()
                job["done"] = i + 1
            fresh = _story(sid)
            fresh["summary"], fresh["summary_upto"] = summary, cut
            store.save_story(fresh)
            job["status"] = "done"
        except Exception as e:
            job["status"], job["error"] = "error", str(e)[:300] or e.__class__.__name__
        job["current"] = ""

    asyncio.create_task(run())
    return job


@app.get("/api/stories/{sid}/snapshots")
def get_snapshots(sid: str):
    _story(sid)
    return store.list_snapshots(sid)


@app.post("/api/stories/{sid}/snapshots")
def make_snapshot(sid: str):
    _story(sid)
    return {"name": store.snapshot_story(sid, "manual")}


class RestoreIn(BaseModel):
    name: str


@app.post("/api/stories/{sid}/snapshots/restore")
def restore_snapshot(sid: str, body: RestoreIn):
    _story(sid)
    try:
        return store.restore_snapshot(sid, body.name)
    except KeyError:
        raise HTTPException(404, "That backup no longer exists")


# ---------------- whole-app backup ----------------
async def _backup_loop() -> None:
    """Every 15 minutes: make an automatic backup if the player turned that on and one is due."""
    while True:
        await asyncio.sleep(60)
        try:
            await asyncio.to_thread(backup.run_auto, store.load_config())
        except Exception:
            pass
        await asyncio.sleep(14 * 60)


def _local_only(request: Request) -> None:
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "Backups can only be downloaded or restored on the computer the app runs on.")


@app.get("/api/backup/status")
def backup_status():
    cfg = store.load_config()
    return {"dir": str(backup.backup_dir(cfg)), "auto_days": cfg.get("backup_auto_days", 0), "keep": cfg.get("backup_keep", 5), "backups": backup.list_backups(cfg)}


@app.post("/api/backup/now")
async def backup_now(request: Request, keys: bool = False):
    """Save a backup into the backup folder (see Settings -> Backup)."""
    _local_only(request)
    cfg = store.load_config()
    dest = backup.backup_dir(cfg) / f"storyteller-backup-{backup.stamp()}.zip"
    return await asyncio.to_thread(backup.make_zip, dest, keys)


@app.get("/api/backup/download")
async def backup_download(request: Request, keys: bool = False):
    """A backup as a download. API keys are left out unless keys=true."""
    _local_only(request)
    tmp = store.DATA / "backups" / "tmp" / f"storyteller-backup-{backup.stamp()}.zip"
    info = await asyncio.to_thread(backup.make_zip, tmp, keys)
    return FileResponse(info["path"], media_type="application/zip", filename=tmp.name, background=BackgroundTask(lambda: tmp.unlink(missing_ok=True)))


@app.post("/api/backup/restore")
async def backup_restore(request: Request):
    """Body is a backup .zip. A safety copy of the current data is saved first; files with the same names are replaced; saved API keys are kept."""
    _local_only(request)
    cfg = store.load_config()
    up = store.DATA / "backups" / "tmp" / f"upload-{backup.stamp()}.zip"
    up.parent.mkdir(parents=True, exist_ok=True)
    with open(up, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    try:
        safety = backup.backup_dir(cfg) / f"storyteller-backup-{backup.stamp()}-before-restore.zip"
        await asyncio.to_thread(backup.make_zip, safety, False)
        counts = await asyncio.to_thread(backup.restore, up, cfg)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Could not read that file as a backup.")
    except Exception:
        raise HTTPException(400, "Could not read that file as a backup.")
    finally:
        up.unlink(missing_ok=True)
    return {**counts, "safety_copy": str(safety)}


class TargetEndingIn(BaseModel):
    index: int = -1


@app.put("/api/stories/{sid}/target-ending")
def set_target_ending(sid: str, body: TargetEndingIn):
    """Ask the story to work toward one of the author's endings (-1 = none)."""
    s = _story(sid)
    s["target_ending"] = body.index if 0 <= body.index < len(s.get("endings") or []) else -1
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/backdrop-image")
async def upload_backdrop(sid: str, request: Request):
    """Use your own picture as this story's backdrop (raw image body)."""
    s = _story(sid)
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    folder = store.PORTRAITS / sid
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("backdrop.*"):
        old.unlink()
    (folder / f"backdrop.{ext}").write_bytes(data)
    s["backdrop"] = {"url": f"/portraits/{sid}/backdrop.{ext}?v={int(time.time())}", "source": "upload"}
    store.save_story(s)
    return s


# ---------------- AI help while creating a story (nothing is saved) ----------------
SUGGEST_SYSTEMS = {
    "cast": ("From a story prompt, list the named recurring characters (at most 8) a player will meet. Reply with ONLY JSON: "
             '[{"name": "...", "appearance": "visual description for an image generator: age, gender, hair, eyes, build, clothing (safe for work)", '
             '"bio": "2 or 3 sentences: role, history, relationships", "personality": "a short comma-separated list of traits and speech habits"}]. '
             "Use what the prompt states; invent only what is needed to fill gaps, in keeping with its tone."),
    "prologue": ("Write the PROLOGUE for a story: the events that lead up to where it begins (150 to 300 words, past tense, in the story's prose style). "
                 "Establish the situation and key relationships without resolving anything and without deciding what the player's character does. Output only the text."),
    "opening": ("Write the OPENING SCENE for a story (250 to 450 words, in the story's prose style and voice). Start in the moment the story begins and end at a point "
                "where the player must decide what to do. Never decide the player's character's actions, words or feelings. Write only the narrative: no status panels or tags. Output only the text."),
    "opening_options": ("Write 3 or 4 suggested FIRST MOVES for the player, first person, ready to send: each *a short action or beat in italics* then 'a spoken line' in single quotes where fitting. "
                        'Make them clearly different in approach. Reply with ONLY a JSON array of strings.'),
    "endings": ('Suggest 4 possible ENDINGS for this story (for example a triumphant one, a tragic one, a bittersweet one, a surprising one). Each should follow from the premise and characters. '
                'Reply with ONLY JSON: [{"title": "short name", "text": "2 or 3 sentences describing how it ends"}].'),
    "description": "Write a 1 to 2 sentence blurb (under 40 words) for a story's menu card: setting, premise and tone, present tense, no spoilers. Output only the blurb.",
}


class SuggestIn(BaseModel):
    kind: str
    title: str = ""
    base_prompt: str = ""
    opening: str = ""
    prologue: str = ""
    cast: list[dict] = []
    provider: str | None = None
    model: str | None = None


@app.post("/api/suggest")
async def suggest(body: SuggestIn):
    """The AI drafts one part of a story from the prompt (for the story creator). Nothing is saved; the player edits what they like."""
    cfg = store.load_config()
    if not body.base_prompt.strip():
        raise HTTPException(400, "Write the story prompt first.")
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, None, "recaps", "story creation help")
    usage.set_ctx(None, "story creation help")
    ctx = f"Title: {body.title}\n\nSTORY PROMPT:\n{body.base_prompt[:9000]}"
    if body.cast:
        ctx += "\n\nCHARACTERS:\n" + "\n".join(f"- {c.get('name', '')}: {c.get('appearance', '')} {c.get('personality', '')}" for c in body.cast[:12])
    if body.prologue.strip():
        ctx += f"\n\nPROLOGUE:\n{body.prologue[:3000]}"
    if body.opening.strip():
        ctx += f"\n\nOPENING SCENE:\n{body.opening[:3500]}"
    if body.kind == "sidebar":
        out = await providers.complete(prov, model, tracker.SCHEMA_SYSTEM, [{"role": "user", "content": ctx}], temperature=0.3, max_tokens=900,
                                       num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        schema = tracker.clean_schema(_json_in(out))
        if not schema:
            raise HTTPException(502, "The AI didn't return usable fields. Try again.")
        return {"result": schema}
    system = SUGGEST_SYSTEMS.get(body.kind)
    if not system:
        raise HTTPException(400, "Unknown kind")
    long = body.kind in ("prologue", "opening")
    out = await providers.complete(prov, model, system, [{"role": "user", "content": ctx}], temperature=0.8 if long else 0.6, max_tokens=1600 if long else 1400,
                                   num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    if body.kind in ("cast", "opening_options", "endings"):
        data = _json_in(out)
        if not isinstance(data, list) or not data:
            raise HTTPException(502, "The AI didn't return a usable list. Try again.")
        return {"result": data}
    return {"result": out.strip().strip('"')}


# ---------------- spending limit ----------------
def _budget_status(cfg: dict) -> dict:
    day0, month0 = usage.period_starts()
    out = {"action": cfg.get("budget_action", "warn"), "state": "ok"}
    for key, since in (("daily", day0), ("monthly", month0)):
        limit = float(cfg.get(f"budget_{key}") or 0)
        spent = usage.spend(cfg, since) if limit > 0 or key == "daily" else 0.0
        pct = round(100 * spent / limit) if limit > 0 else None
        out[key] = {"limit": limit, "spent": round(spent, 4), "pct": pct}
        if limit > 0:
            if spent >= limit:
                out["state"] = "over"
            elif pct >= 80 and out["state"] != "over":
                out["state"] = "warn"
    return out


def _budget_block(cfg: dict, prov: dict) -> str | None:
    """A message if a spending limit stops this paid call (local models are free and never blocked)."""
    if prov["type"] == "ollama" or cfg.get("budget_action") != "stop":
        return None
    b = _budget_status(cfg)
    for key, label in (("daily", "today's"), ("monthly", "this month's")):
        if b[key]["limit"] > 0 and b[key]["spent"] >= b[key]["limit"]:
            return f"BUDGET: You have reached {label} spending limit (${b[key]['spent']:.2f} of ${b[key]['limit']:.2f}, estimated). Raise it under 💲 Usage or Settings, or send anyway."
    return None


@app.get("/api/budget")
def budget():
    return _budget_status(store.load_config())


# ---------------- scene backdrop: a generated picture for the sides of the screen ----------------
BACKDROP_SYSTEM = ("You write one prompt for an image generator: a wide BACKGROUND picture of a place, to sit behind a story's text. Show the environment only: "
                   "no people, no characters, no faces, no text, no logos. Atmospheric, with depth, a calm composition that works when darkened. Safe for work. ")


class BackdropIn(BaseModel):
    source: str = "setting"     # "setting" = the story's world, "scene" = where the latest reply takes place


@app.post("/api/stories/{sid}/backdrop/generate")
async def generate_backdrop(sid: str, body: BackdropIn):
    """Draw a static backdrop from the story's setting or its latest scene. Shown on the sides of the screen if the player turns that on."""
    s, cfg = _story(sid), store.load_config()
    prov = _img_provider(cfg, None, s)
    art = await _art_with_setting(s, cfg)
    rprov, rmodel = _role(cfg, s, "recaps", "backdrop picture")
    basis = f"Story: {s['title']}\nThe story's world: {art.get('setting') or s['base_prompt'][:1500]}"
    if body.source == "scene":
        last = next((t["text"] for t in reversed(s["turns"]) if t["role"] == "assistant"), "")
        if last:
            basis += "\n\nThe latest scene (show the PLACE where it happens):\n" + _clean_for_scene(last)[-3500:]
    tags = _wants_tags(prov)
    system = BACKDROP_SYSTEM + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags starting with: no humans, scenery, ... then place, time of day, weather, lighting, mood, framing (wide shot)."
                                if tags else "Reply with 2 or 3 descriptive sentences. Output only the prompt.")
    text = (await providers.complete(rprov, rmodel, system, [{"role": "user", "content": basis}], temperature=0.7, max_tokens=300,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')
    direction = images.plain(art.get("direction") or "").strip() if not tags else (art.get("direction") or "").strip()
    prompt = ", ".join(x for x in (direction, text, "wide shot, detailed background") if x) if tags else "\n\n".join(x for x in (direction, text, images.SAFE_LINE) if x)
    negative = ", ".join(x for x in (art.get("negative", ""), "person, people, character, face, text, watermark") if x)
    async with _image_lock:
        try:
            data, ext = await images.generate(prov, prompt, negative, "landscape")
        except images.ImageError as e:
            raise HTTPException(502, str(e))
    folder = store.PORTRAITS / sid
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("backdrop.*"):
        old.unlink()
    (folder / f"backdrop.{ext}").write_bytes(data)
    s = _story(sid)
    s["backdrop"] = {"url": f"/portraits/{sid}/backdrop.{ext}?v={int(time.time())}", "source": body.source}
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/backdrop")
def delete_backdrop(sid: str):
    s = _story(sid)
    for old in (store.PORTRAITS / sid).glob("backdrop.*"):
        old.unlink()
    s["backdrop"] = {}
    store.save_story(s)
    return s


# ---------------- side panel (tracker) ----------------
_tracker_tasks: dict[str, asyncio.Task] = {}


def _maybe_track_with_ai(story: dict, idx: int, got_panel: bool) -> None:
    """The AI fills the side panel in the background when the player chose 'AI decides', or chose fields but the reply has no status panel."""
    mode = (story.get("sidebar") or {}).get("mode", "prompt")
    if mode == "ai" or (mode == "player" and (not got_panel or tracker.custom_fields(story))):
        _tracker_tasks[story["id"]] = asyncio.create_task(_track_with_ai(story["id"], idx, got_panel and mode == "player"))


async def _repair_values(prov, model, cfg, s: dict, bad: list) -> dict:
    """Values the AI wrote that don't fit their field (e.g. words in a number tracker): ask once more for a corrected value. Returns a patch."""
    fields = {f["id"]: f for f in tracker.active_fields(s)}
    items = "\n".join(f"{i}. {fid} ({fields[fid]['label']}, {fields[fid]['kind']}"
                      + (f", one of {'/'.join(fields[fid]['options'])}" if fields[fid].get("options") else "")
                      + (f", {fields[fid].get('min', 0)} to {fields[fid].get('max', 100)}" if fields[fid]["kind"] == "slider" else "")
                      + ")" + (f" for {n}" if n else "") + f": got {json.dumps(v, ensure_ascii=False)}" for i, (fid, n, v) in enumerate(bad, 1))
    recent = "\n\n".join(_clean_for_scene(t["text"])[:2500] for t in s["turns"][-2:])
    out = await providers.complete(prov, model, tracker.REPAIR_SYSTEM, [{"role": "user", "content": f"ITEMS:\n{items}\n\nLATEST STORY TEXT:\n{recent}"}],
                                   temperature=0.0, max_tokens=400, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    fixes, patch = _json_in(out), {"scene": {}, "me": {}, "chars": {}}
    for f in fixes if isinstance(fixes, list) else []:
        try:
            fid, n, _ = bad[int(f["i"]) - 1]
        except (KeyError, ValueError, TypeError, IndexError):
            continue
        sec, k = fid.split(".")
        if f.get("value") is None:
            continue
        if sec == "chars":
            patch["chars"].setdefault(n or "", {})[k] = f["value"]
        else:
            patch[sec][k] = f["value"]
    return patch


async def _track_with_ai(sid: str, idx: int, custom_only: bool = False) -> None:
    try:
        s, cfg = _story(sid), store.load_config()
        if idx >= len(s["turns"]) or s["turns"][idx]["role"] != "assistant":
            return
        fields = tracker.custom_fields(s) if custom_only else tracker.active_fields(s)     # the narrator's own panel already covers the ticked fields
        if not fields:
            return
        prov, model = _role(cfg, s, "recaps", "side panel")
        move = s["turns"][idx - 1]["text"] if idx >= 1 and s["turns"][idx - 1]["role"] == "user" else ""
        user = (f"TRACKED FIELDS:\n{tracker.field_lines(fields)}\n\nCURRENT PANEL:\n{json.dumps(tracker.visible(s), ensure_ascii=False)}\n\n"
                f"PLAYER'S MOVE:\n{move[:800]}\n\nNARRATOR'S REPLY:\n{_clean_for_scene(s['turns'][idx]['text'])[:12000]}")
        out = await providers.complete(prov, model, tracker.EXTRACT_SYSTEM, [{"role": "user", "content": user}], temperature=0.1, max_tokens=900,
                                       num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        patch = _json_in(out)
        fresh = _story(sid)
        bad: list = []
        first = tracker.apply_patch(fresh, patch if isinstance(patch, dict) else {}, idx // 2 + 1, bad)
        if bad:        # a value didn't fit its tracker: ask the helper AI once for a corrected one
            try:
                fix = await _repair_values(prov, model, cfg, fresh, bad)
                extra = tracker.apply_patch(fresh, fix, idx // 2 + 1)
                fresh["tracker"]["changes"] = first + extra
            except Exception:
                pass
        store.save_story(fresh)
    except Exception:
        pass


@app.get("/api/tracker-fields")
def tracker_fields():
    return tracker.DEFAULT_FIELDS


@app.get("/api/stories/{sid}/tracker")
async def get_tracker(sid: str, wait: bool = False):
    """The side panel's data. wait=true first lets a background AI update finish (used right after a reply)."""
    if wait:
        t = _tracker_tasks.get(sid)
        if t and not t.done():
            await asyncio.wait({t}, timeout=60)
    s = _story(sid)
    return {"tracker": s["tracker"], "sidebar": s["sidebar"]}


class SidebarIn(BaseModel):
    mode: str
    fields: list[str] | None = None
    custom: list[dict] | None = None


@app.put("/api/stories/{sid}/sidebar")
async def put_sidebar(sid: str, body: SidebarIn):
    """Choose what the side panel tracks: mode prompt (the story prompt decides), player (these fields), ai (the AI decides)."""
    if body.mode not in ("prompt", "player", "ai"):
        raise HTTPException(400, "mode must be prompt, player or ai")
    s = _story(sid)
    store.snapshot_story(sid, "before side panel change")
    sb = s["sidebar"]
    sb["mode"] = body.mode
    if body.fields is not None:
        sb["fields"] = [f for f in body.fields if any(f == d["id"] for d in tracker.DEFAULT_FIELDS)]
    before = {f["id"] for f in sb.get("custom") or []}
    if body.custom is not None:
        sb["custom"] = tracker.clean_custom(body.custom, sb.get("custom"))
    if body.mode == "ai" and not sb.get("schema"):
        await _decide_schema(s)
    tracker.rebuild(s)
    new = [f for f in tracker.custom_fields(s) if f["id"] not in before]
    if new and s["turns"]:         # trackers added mid-story: fill them in from what has already happened (best effort)
        try:
            await _fill_fields(s, store.load_config(), new)
        except Exception:
            pass
    store.save_story(s)
    return s


async def _fill_fields(s: dict, cfg: dict, fields: list[dict]) -> None:
    prov, model = _role(cfg, s, "recaps", "side panel")
    recent = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + _clean_for_scene(t["text"])[:3000] for t in s["turns"][-6:])
    user = f"TRACKED FIELDS:\n{tracker.field_lines(fields)}\n\nSTORY SO FAR (summary):\n{s.get('summary', '')[-6000:]}\n\nRECENT TURNS:\n{recent}"
    out = await providers.complete(prov, model, tracker.BUILD_SYSTEM, [{"role": "user", "content": user}], temperature=0.1, max_tokens=1500,
                                   num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    patch = _json_in(out)
    bad: list = []
    tracker.apply_patch(s, patch if isinstance(patch, dict) else {}, len(s["turns"]) // 2, bad)
    if bad:
        tracker.apply_patch(s, await _repair_values(prov, model, cfg, s, bad), len(s["turns"]) // 2)
    s["tracker"]["changes"] = []        # an initial fill is not a "change"


class AssistIn(BaseModel):
    label: str
    scope: str = ""
    story_id: str | None = None
    title: str = ""
    base_prompt: str = ""
    provider: str | None = None
    model: str | None = None


@app.post("/api/tracker-assist")
async def tracker_assist(body: AssistIn):
    """The helper AI suggests how a custom tracker should work (type, range, options, what it means) from its name. Nothing is saved."""
    label = body.label.strip()
    if not label:
        raise HTTPException(400, "Type a name for the tracker first.")
    cfg = store.load_config()
    s = _story(body.story_id) if body.story_id else None
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, s, "recaps", "side panel")
    usage.set_ctx(body.story_id, "side panel")
    prompt = (s["base_prompt"] if s else body.base_prompt)[:5000]
    ttl = s["title"] if s else body.title
    out = await providers.complete(prov, model, tracker.ASSIST_SYSTEM, [{"role": "user", "content": f"Tracker name: {label}\nStory: {ttl}\n\n{prompt}"}],
                                   temperature=0.2, max_tokens=400, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    d = _json_in(out)
    got = tracker.clean_custom([{**(d if isinstance(d, dict) else {}), "label": label}])
    if not got or not isinstance(d, dict):
        raise HTTPException(502, "The AI didn't return a usable suggestion. Choose the type yourself, or try again.")
    f = got[0]
    f["scope"] = d.get("scope") if d.get("scope") in tracker.SCOPES else body.scope or f["id"].split(".")[0]
    return f


async def _decide_schema(s: dict) -> None:
    cfg = store.load_config()
    prov, model = _role(cfg, s, "recaps", "side panel")
    out = await providers.complete(prov, model, tracker.SCHEMA_SYSTEM, [{"role": "user", "content": f"{s['title']}\n\n{s['base_prompt'][:9000]}"}],
                                   temperature=0.3, max_tokens=900, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    schema = tracker.clean_schema(_json_in(out))
    if not schema:
        raise HTTPException(502, "The AI didn't return usable fields. Try again.")
    s["sidebar"]["schema"] = schema


@app.post("/api/stories/{sid}/sidebar/schema")
async def redecide_schema(sid: str):
    s = _story(sid)
    await _decide_schema(s)
    s["sidebar"]["mode"] = "ai"
    tracker.rebuild(s)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/sidebar/build")
async def build_tracker(sid: str):
    """Fill the side panel from the story so far (the memory summary and the latest replies). Uses the AI when the story has no status panel."""
    s, cfg = _story(sid), store.load_config()
    store.snapshot_story(sid, "before side panel build")
    tracker.rebuild(s)
    if s["sidebar"]["mode"] != "prompt" and not any(s["tracker"][k] for k in ("scene", "me", "chars")):
        fields = tracker.active_fields(s)
        prov, model = _role(cfg, s, "recaps", "side panel")
        recent = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + _clean_for_scene(t["text"])[:3000] for t in s["turns"][-6:])
        user = f"TRACKED FIELDS:\n{tracker.field_lines(fields)}\n\nSTORY SO FAR (summary):\n{s.get('summary', '')[-6000:]}\n\nRECENT TURNS:\n{recent}"
        out = await providers.complete(prov, model, tracker.BUILD_SYSTEM, [{"role": "user", "content": user}], temperature=0.1, max_tokens=1500,
                                       num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        patch = _json_in(out)
        bad: list = []
        tracker.apply_patch(s, patch if isinstance(patch, dict) else {}, len(s["turns"]) // 2, bad)
        if bad:
            tracker.apply_patch(s, await _repair_values(prov, model, cfg, s, bad), len(s["turns"]) // 2)
        s["tracker"]["changes"] = []     # an initial fill is not a "change"
    elif tracker.custom_fields(s) and s["turns"]:
        def has_value(f):
            sec, k = f["id"].split(".")
            return k in s["tracker"][sec] if sec != "chars" else any(k in c for c in s["tracker"]["chars"].values())
        miss = [f for f in tracker.custom_fields(s) if not has_value(f)]
        if miss:
            await _fill_fields(s, cfg, miss)
    store.save_story(s)
    return s


# ---------------- local models: start/stop on demand (Settings -> Local models) ----------------
@app.get("/api/runtime")
async def runtime_status():
    return await localrt.status()


class RuntimeSelectIn(BaseModel):
    story_id: str | None = None


@app.post("/api/runtime/select")
async def runtime_select(body: RuntimeSelectIn):
    """A story was opened or its AI choices changed. If auto-management is on, load what it needs and release what nothing needs."""
    cfg = store.load_config()
    if not (cfg.get("rt_text") or cfg.get("rt_comfy")) or not body.story_id:
        return {"managed": False}
    s = _story(body.story_id)
    needed = []
    for role in ("story", "recaps"):
        try:
            prov, model = _role(cfg, s, role)
        except HTTPException:
            continue
        if prov["type"] == "ollama" and model not in needed:
            needed.append(model)
    try:
        needs_comfy = _img_provider(cfg, None, s)["type"] == "comfyui"
    except HTTPException:
        needs_comfy = False
    return {"managed": True, **(await localrt.select(needed, needs_comfy))}


@app.post("/api/runtime/free")
async def runtime_free():
    """Unload local text models and stop (or free) ComfyUI right now."""
    return await localrt.free_all()


@app.post("/api/runtime/comfy/{action}")
async def runtime_comfy(action: str):
    if action == "start":
        return {"up": await localrt.comfy_start()}
    if action == "stop":
        return {"stopped": await localrt.comfy_stop()}
    raise HTTPException(400, "start or stop")


# ---------------- usage & cost ----------------
@app.get("/api/usage")
def usage_report(days: int = 30, story: str | None = None):
    """Token use and ESTIMATED cost, grouped by story, job, model and day. days=0 means all time."""
    cfg = store.load_config()
    since = time.time() - days * 86400 if days else 0
    titles = {s["id"]: s["title"] for s in store.list_stories()}
    return usage.report(cfg, since, titles, story)


@app.put("/api/prices")
def put_prices(body: dict):
    """Your own prices ($ per 1M tokens: match, input, output, cache_read; images: kind "image", match, per_image). They win over the built-in ones."""
    cfg = store.load_config()
    rows = []
    for r in body.get("prices", []):
        if not str(r.get("match", "")).strip():
            continue
        row = {"match": str(r["match"]).strip()}
        if r.get("kind") == "image":
            row.update(kind="image", per_image=float(r.get("per_image") or 0))
        else:
            row.update(input=float(r.get("input") or 0), output=float(r.get("output") or 0))
            if r.get("cache_read") not in (None, ""):
                row["cache_read"] = float(r["cache_read"])
        rows.append(row)
    cfg["prices"] = rows
    store.save_config(cfg)
    return {"prices": rows}


# ---------------- home screen: covers, descriptions, importing a whole story ----------------
class HomeIn(BaseModel):
    description: str = ""


@app.put("/api/stories/{sid}/home")
def set_home_card(sid: str, body: HomeIn):
    s = _story(sid)
    s["description"] = body.description.strip()
    store.save_story(s)
    return s


def _save_cover(s: dict, data: bytes, ext: str) -> None:
    folder = store.PORTRAITS / s["id"]
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("cover.*"):
        old.unlink()
    (folder / f"cover.{ext}").write_bytes(data)
    s["cover"] = f"/portraits/{s['id']}/cover.{ext}?v={int(time.time())}"
    store.save_story(s)


@app.post("/api/stories/{sid}/cover")
async def upload_cover(sid: str, request: Request):
    """Body is the raw image file: the picture shown on this story's Home card."""
    s = _story(sid)
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    _save_cover(s, data, ext)
    return s


@app.delete("/api/stories/{sid}/cover")
def delete_cover(sid: str):
    s = _story(sid)
    for old in (store.PORTRAITS / sid).glob("cover.*"):
        old.unlink()
    s["cover"] = ""
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/cover/generate")
async def generate_cover(sid: str):
    """Make cover art from the story prompt with the story's picture maker (the text model first writes a safe, text-free picture description)."""
    s, cfg = _story(sid), store.load_config()
    prov, tprov_model = _img_provider(cfg, None, s), _role(cfg, s, "recaps", "cover art")
    tags = _wants_tags(prov)
    system = ("You write one prompt for an image generator: key art for the cover of an interactive story. Show the setting and the main "
              "character or characters in a striking, cinematic composition. No text, letters, logos or UI. Keep it safe for work: no nudity or explicit content. "
              + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags (subject count first, hair, eyes, clothing, pose, setting, lighting, framing). No names, no sentences."
                 if tags else "Reply with 2 or 3 descriptive sentences. No names of real people.") + " Output only the prompt.")
    basis = (s.get("description") or "") + "\n\n" + s["base_prompt"][:5000] + ("\n\nCharacters:\n" + "\n".join(f"{c['name']}: {c['appearance']}" for c in s["cast"][:6] if c.get("appearance")) if s.get("cast") else "")
    text = (await providers.complete(tprov_model[0], tprov_model[1], system, [{"role": "user", "content": basis}], temperature=0.8, max_tokens=300,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')
    art = s["art"]
    if tags:
        prompt = ", ".join(x for x in (images.plain(art.get("direction") or "").strip(), "masterpiece, best quality, cover art, wide shot", text) if x)
    else:
        prompt = "\n\n".join(x for x in (images.plain(art.get("direction") or "").strip(), text, images.SAFE_LINE) if x)
    async with _image_lock:
        try:
            data, ext = await images.generate(prov, prompt, art.get("negative", ""), "landscape")
        except images.ImageError as e:
            raise HTTPException(502, str(e))
    s = _story(sid)
    _save_cover(s, data, ext)
    return s


@app.post("/api/stories/{sid}/description/generate")
async def generate_description(sid: str):
    """A short blurb for the Home card, written from the story prompt (and the story so far)."""
    s, cfg = _story(sid), store.load_config()
    prov, model = _role(cfg, s, "recaps", "description")
    basis = s["base_prompt"][:6000] + (("\n\nStory so far:\n" + s["summary"][-1500:]) if s.get("summary") else "")
    out = await providers.complete(prov, model, "Write a 1 to 2 sentence blurb (under 40 words) for a story's menu card: the setting, the premise and the tone, in present tense. "
                                   "No spoilers, no markdown, no quotes. Output only the blurb.", [{"role": "user", "content": f"Title: {s['title']}\n\n{basis}"}],
                                   temperature=0.6, max_tokens=200, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    s = _story(sid)
    s["description"] = out.strip().strip('"')
    store.save_story(s)
    return s


class ImportTextIn(BaseModel):
    title: str = ""
    subtitle: str = ""
    source_story_id: str | None = None
    into_story_id: str | None = None
    base_prompt: str = ""
    avatar: dict[str, str] = {}
    user_notes: str = ""
    text: str                           # the whole story so far (or a long part of it)
    status_panel: str = ""              # optional; otherwise the last ```Info block in the text is used
    keep_last: int = 6                  # labelled PLAYER:/NARRATOR: text: this many final messages stay word for word
    provider: str | None = None         # the AI that writes the summary (the player's choice)
    model: str | None = None


def _chunks(text: str, size: int = 14000) -> list[str]:
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > size:             # one enormous paragraph
            if cur:
                out.append(cur); cur = ""
            out.append(para[:size]); para = para[size:]
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + para
    if cur.strip():
        out.append(cur)
    return out


_INFO_RE = re.compile(r"```info\b.*?```", re.IGNORECASE | re.DOTALL)


@app.post("/api/stories/import-text")
async def import_story_text(body: ImportTextIn):
    """Import a whole story played elsewhere. A background job summarizes it in parts with the chosen AI, then builds the story."""
    cfg = store.load_config()
    src = _story(body.source_story_id) if body.source_story_id else None
    if not (body.base_prompt.strip() or src or body.into_story_id):
        raise HTTPException(400, "Give the story prompt directly or pick a story to copy it from")
    if len(body.text.strip()) < 20:
        raise HTTPException(400, "Paste the story text first.")
    prov, model = _pick(cfg, body.provider, body.model)
    text = body.text.strip()
    recent, warnings = [], []
    if importer._LABEL.search(text):                       # labelled PLAYER:/NARRATOR: transcript
        turns, warnings = importer.parse_turns(text)
        keep = max(2, min(body.keep_last, len(turns)))
        keep -= keep % 2
        if keep and len(turns) > keep and turns[-keep]["role"] != "user":
            keep += 1
        older, recent = turns[:-keep] if keep else turns, turns[-keep:] if keep else []
        source = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in older)
    else:
        source = text
    panel = body.status_panel.strip()
    if not panel:
        found = _INFO_RE.findall(text)
        panel = found[-1] if found else ""
    parts = _chunks(source) if source.strip() else []
    job = _new_job("import", "", len(parts))

    async def work(j: dict) -> None:
        usage.set_ctx(None, "import summary")
        summary = ""
        for i, part in enumerate(parts):
            j["current"] = f"Summarizing part {i + 1} of {len(parts)}"
            summary = (await providers.complete(prov, model, prompting.SUMMARY_SYSTEM, [{"role": "user", "content": f"EXISTING SUMMARY:\n{summary or '(none yet)'}\n\nNEW EVENTS:\n{part}\n\nWrite the updated summary."}],
                                                temperature=0.3, max_tokens=3500, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip()
            j["done"] = i + 1
        j["current"] = "Building the story"
        res = import_story(ImportIn(title=body.title, subtitle=body.subtitle, source_story_id=body.source_story_id, into_story_id=body.into_story_id, base_prompt=body.base_prompt,
                                    avatar=body.avatar, user_notes=body.user_notes, summary=summary, status_panel=panel,
                                    recent_turns="\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in recent)))
        j["result"] = {"story_id": res["story"]["id"], "warnings": warnings + res["warnings"], "summary_chars": len(summary)}

    async def run() -> None:
        try:
            await work(job)
            job["status"] = "done"
        except HTTPException as e:
            job["status"], job["error"] = "error", str(e.detail)
        except Exception as e:
            job["status"], job["error"] = "error", str(e)[:300] or e.__class__.__name__
        job["current"] = ""

    asyncio.create_task(run())
    return job


# ---------------- static UI ----------------
@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.mount("/portraits", StaticFiles(directory=store.PORTRAITS), name="portraits")
app.mount("/images", StaticFiles(directory=store.IMAGES), name="images")
