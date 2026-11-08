"""AI Storyteller: local FastAPI server. Run with ./run.sh"""
import asyncio
import hashlib
import os
import io
import zipfile
import json
import re
import shutil
import signal
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote_plus, urlparse

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, Response, StreamingResponse
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


_fp: dict[str, dict] = {}   # per story: fingerprints of the last narrator request, to tell WHY the cache had to be written again


def _cache_why(sid: str, b, cfg: dict) -> str:
    """The most likely reason the next narrator call cannot read the cache (shown in Usage → cache rewrites). Compares this request with the last one."""
    h = lambda t: hashlib.sha1((t or "").encode()).hexdigest()[:12]
    cur = {"core": h(b.core), "extras": h(b.extras), "msgs": [h(m["content"]) for m in b.messages[:-1]], "t": time.time()}
    prev, _fp[sid] = _fp.get(sid), cur
    last_use = max(prev["t"] if prev else 0, _warm["touched"] if _warm["story_id"] == sid else 0)
    if not prev:
        return "first turn since the app started"
    if time.time() - last_use > _ttl_seconds(cfg):
        return "cache expired (idle longer than its lifetime)"
    if cur["core"] != prev["core"]:
        return "story prompt, cast or picture settings changed"
    if cur["extras"] != prev["extras"]:
        return "memory summary, notes or pinned facts changed"
    pm = prev["msgs"]
    if cur["msgs"][:len(pm)] != pm:
        return "earlier messages changed (undo, retry or edit)"
    return "no change expected (prefix identical)"


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
    await asyncio.to_thread(store.clean_scratch)      # unchosen "Draw 4 options" pictures older than a day
    task = asyncio.create_task(_keep_warm_loop())
    rt = asyncio.create_task(localrt.loop())
    bk = asyncio.create_task(_backup_loop())
    yield
    task.cancel()
    rt.cancel()
    bk.cancel()


app = FastAPI(title="AI Storyteller", lifespan=lifespan)


@app.exception_handler(providers.ProviderError)
async def _provider_error(_request: Request, exc: providers.ProviderError):
    """An AI service refused or could not be reached: say so plainly instead of a bare "Internal Server Error"."""
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------- optional access PIN for other devices on your network (see run-lan.sh) ----------------
PIN = os.environ.get("STORYTELLER_PIN", "")
_pin_fail: dict[str, int] = {}
LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>AI Storyteller</title>
<body style="background:#12111c;color:#e8e5f2;font:18px system-ui;display:grid;place-items:center;height:100vh;margin:0"><form method=post action=/pin-login style="display:grid;gap:12px;width:min(320px,90vw)">
<b>AI Storyteller</b><input name=pin type=password inputmode=numeric placeholder="PIN" autofocus style="font-size:20px;padding:10px;border-radius:8px;border:1px solid #444;background:#1b1a2b;color:inherit">
<button style="font-size:18px;padding:10px;border-radius:8px;background:#8b6fd6;color:#fff;border:0">Open</button><small style="color:#999">{msg}</small></form>"""


def _pin_cookie() -> str:
    return hashlib.sha256(("st|" + PIN).encode()).hexdigest()[:32]


_LOCAL_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


@app.middleware("http")
async def browser_guard(request: Request, call_next):
    """Keeps other websites in your browser from using the app. From this computer, the page must be opened as localhost / 127.0.0.1
    (blocks DNS-rebinding), and a browser request that changes something must come from the app's own page (blocks cross-site requests,
    for example a web page that tries to press Quit). Tools like curl send no Origin header and are unaffected."""
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1", "localhost"):
        hh = request.headers.get("host", "")
        name = hh.split("]")[0] + "]" if hh.startswith("[") else hh.rsplit(":", 1)[0]
        if name and name not in _LOCAL_NAMES:
            return Response('{"detail":"Open the app at http://127.0.0.1:8000"}', media_type="application/json", status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != "null" and request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        if urlparse(origin).netloc != request.headers.get("host", ""):
            return Response('{"detail":"Cross-site request blocked"}', media_type="application/json", status_code=403)
    elif origin == "null" and request.method in ("POST", "PUT", "PATCH", "DELETE") and request.url.path.startswith("/api/"):
        return Response('{"detail":"Cross-site request blocked"}', media_type="application/json", status_code=403)
    return await call_next(request)


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
    if role == "small":   # an optional second, cheaper helper for the small per-reply jobs; unset = the Recaps & helpers model
        return _role(cfg, s, "recaps", purpose)
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
    prov, model = _role(cfg, s, "small", "suggested replies")
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
    small: dict | None = None     # optional cheaper helper for the small per-reply jobs (empty = same as recaps)
    images: dict | None = None


def _clean_models(body: ModelsIn) -> dict:
    out = {}
    for role in ("story", "recaps", "small", "images"):
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
    """Per-story adult-content switch (off by default). Affects what the narrator writes and what scene/portrait pictures may show (adults only); covers and backdrops always stay safe for work."""
    s = _story(sid)
    s["nsfw"] = on
    store.save_story(s)
    return s


@app.put("/api/stories/{sid}/models")
def put_story_models(sid: str, body: ModelsIn):
    s = _story(sid)
    s["models"] = {**(s.get("models") or {}), **_clean_models(body)}
    if body.small is not None and not (body.small.get("provider") and body.small.get("model")):
        s["models"].pop("small", None)   # "same as Recaps & helpers"
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
    style = (store.load_config().get("default_art_direction") or "").strip()
    if style and not (s.get("art") or {}).get("direction", "").strip():   # an imported story with no art style starts with the player's chosen one
        s["art"] = {**(s.get("art") or {}), "direction": style}


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


AVATAR_LOOK_SYSTEM = ("From a story character's sheet, write a purely VISUAL description for an image generator, as one line of comma-separated details: "
                      "apparent age and gender, ethnicity or skin tone if stated, hair, eyes, build, usual clothing, distinctive features. "
                      "Leave out personality, background, abilities, and anything sexual, anatomical or explicit. Keep it safe for work. Output only the line.")


async def _avatar_plan(s: dict, cfg: dict, av: dict):
    """(picture maker, art settings, prompt, negative, visual description) for an avatar sheet, styled for the story's world."""
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
        prompt = images.portrait_prompt(art, name, look, None, False, nsfw=bool(s.get("nsfw")))
    return prov, art, prompt, negative, look


def _avatar_candidates_job(kind: str, owner: str, folder: Path, base: str, s: dict, cfg: dict, av: dict, count: int, fresh: bool) -> dict:
    """Draw several candidate avatar pictures in the background (the description and prompt are written once). Nothing is saved as the avatar until one is chosen."""
    count = max(1, min(int(count), 8))
    job = _new_job(kind, owner, count)
    job["result"] = {"candidates": [], "look": ""}

    async def work(j: dict) -> None:
        j["current"] = "Writing the picture prompt…"
        prov, art, prompt, negative, look = await _avatar_plan(s, cfg, av)
        j["result"]["look"] = look
        if fresh:
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            j["current"] = f"Drawing option {i + 1} of {count}…"
            try:
                data, ext = await images.generate(prov, prompt, negative, art.get("portrait_shape", "landscape"))
            except images.ImageError as e:
                raise HTTPException(502, str(e))
            f = folder / f"cand-{int(time.time() * 1000)}-{i}.{ext}"
            f.write_bytes(data)
            j["result"]["candidates"].append(f"{base}/{f.name}")
            j["done"] = i + 1

    asyncio.create_task(_run_job(job, work))
    return {"job": job["id"]}


class AvatarCandIn(BaseModel):
    avatar: dict[str, str] = {}      # the form as it is now (unsaved edits count)
    count: int = 4
    fresh: bool = True               # false = add to the options already drawn


@app.post("/api/stories/{sid}/avatar-image/candidates")
async def avatar_candidates(sid: str, body: AvatarCandIn):
    """Draw several candidate player pictures (default 4) to choose from. Nothing is saved as the avatar until .../use with the chosen url."""
    s, cfg = _story(sid), store.load_config()
    av = {k: v.strip() for k, v in {**(s.get("avatar") or {}), **body.avatar}.items() if k in AVATAR_FIELDS and v.strip()}
    return _avatar_candidates_job("avatar", sid, store.PORTRAITS / sid / "_avatar_candidates", f"/portraits/{sid}/_avatar_candidates", s, cfg, av, body.count, body.fresh)


class AvatarUseIn(BaseModel):
    look: str = ""
    url: str = ""                    # the chosen option from .../candidates


@app.post("/api/stories/{sid}/avatar-image/use")
def use_avatar_candidate(sid: str, body: AvatarUseIn):
    s = _story(sid)
    if not body.url.split("?")[0].startswith(f"/portraits/{sid}/_avatar_candidates/"):
        raise HTTPException(404, "That is not one of this story's avatar options")
    cand = _portrait_file(body.url)
    if not cand:
        raise HTTPException(404, "That option is gone. Draw new ones.")
    data, ext = cand.read_bytes(), cand.suffix.lstrip(".")
    for old in (store.PORTRAITS / sid).glob("avatar.*"):
        old.unlink()
    (store.PORTRAITS / sid / f"avatar.{ext}").write_bytes(data)
    s["avatar_image"] = f"/portraits/{sid}/avatar.{ext}?v={int(time.time())}"
    _set_player_portrait(s, data, ext, body.look)
    cand.unlink(missing_ok=True)
    shutil.rmtree(store.PORTRAITS / sid / "_avatar_candidates", ignore_errors=True)
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
    newchar: bool = False               # opt-in: the narrator flags new named characters (hidden tag) so art can be offered
    now_card: bool = False              # opt-in: the side panel shows who the player is talking to (picture, mood, clothing) in place of the avatar
    dynamic_backdrop: bool = False      # opt-in (with auto_backdrop): dramatic moments (alarm, fire, battle...) get their own variant of the background
    auto_backdrop: bool = False         # opt-in: the helper model changes the screen backdrop to match where the scene is
    scene_often: bool = False           # opt-in: the narrator adds a scene picture to most replies (Places pictures cover routine room-to-room moves)
    scene_where: str = "reply"          # where scene pictures show: "reply" (under the reply), "banner_top" / "banner_bottom" (a wide strip above / below the narration) or "panel" (the side panel's right-now card)
    extended_moods: bool = False        # opt-in: new characters get the extended set of mood pictures by default, and the narrator is reminded each turn (after the first) to use the full range
    exact_moods: bool = False           # opt-in: the narrator names the exact mood and only uses a listed one when it is an exact match (more "draw this mood" offers)


class CastMember(BaseModel):
    name: str
    appearance: str = ""
    bio: str | None = None   # None = keep what is saved
    slug: str | None = None  # keeps a character's pictures when you rename them
    personality: str | None = None
    author: bool | None = None   # tell the narrator about this character (bio + personality)
    aka: str | None = None       # other names the story uses for them, comma separated (None = keep what is saved)
    address: str | None = None   # who may use which name for them (sent to the narrator every turn when filled in)


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


_WIN_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"
    return s + "-x" if s in _WIN_RESERVED else s   # Windows cannot make a folder or file named CON, NUL, AUX...


def _img_provider(cfg: dict, override: str | None = None, s: dict | None = None) -> dict:
    chosen = ((s or {}).get("models") or {}).get("images") or {}
    pid = override or chosen.get("provider") or cfg.get("image_active")
    usage.set_ctx((s or {}).get("id"), "pictures")
    prov = next((p for p in cfg.get("image_providers", []) if p["id"] == pid), None)
    if not prov:
        raise HTTPException(400, "No image maker is selected. Choose one under Settings → AI & story → Pictures.")
    if chosen.get("model") and pid == chosen.get("provider") and not override:
        prov = {**prov, "model": chosen["model"]}   # this story's picture model (an online model name or a ComfyUI checkpoint)
    return prov


def _bg_provider(cfg: dict, s: dict | None = None) -> dict:
    """The picture maker for backgrounds and places. By default the same one as everything else; with "separate backdrop maker" on (Settings -> Pictures),
    a local ComfyUI model such as FLUX.2 klein (it uses the same ComfyUI as the picture maker below, with its own model and workflow)."""
    bg = cfg.get("image_bg") or {}
    if not bg.get("on"):
        return _img_provider(cfg, None, s)
    comfy = next((p for p in cfg.get("image_providers", []) if p.get("type") == "comfyui"), None)
    if not comfy:
        raise HTTPException(400, "The separate backdrop maker needs ComfyUI under Settings → Pictures.")
    workflow = (bg.get("workflow") or "").strip()
    if not workflow:
        f = ROOT / "workflows" / "comfyui-flux2-klein.json"
        workflow = f.read_text(encoding="utf-8") if f.is_file() else ""
    usage.set_ctx((s or {}).get("id"), "pictures")
    return {**comfy, "id": "comfyui-bg", "model": bg.get("model") or "flux-2-klein-4b-fp8.safetensors", "workflow": workflow, "workflow_ref": "",
            "prompt_style": "natural", "ref_mode": "characters", "extra_positive": "", "extra_negative": "", "model_tweaks": {}}


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
    tracker.apply_aliases(s)      # a generic side-panel label ("Shop Girl") becomes their name now that they have a picture


def _portrait_file(url: str) -> Path | None:
    return _local_file(url, store.PORTRAITS, "/portraits/")


def _portrait_urls(ch: dict, hint: str | None = None, ext: bool = True) -> list[str]:
    """All of a character's saved pictures, best reference first: "normal 1" (the one the player picked, so it is always the
    first reference), the mood that fits, the default, other moods, alternates, then (ext=True) pictures borrowed from another story."""
    moods, alts = ch.get("moods") or {}, ch.get("alts") or {}
    out: list[str] = []
    def add(u):
        if u and u not in out:
            out.append(u)
    add(moods.get("normal 1"))
    add(moods.get("normal 2"))   # first variant drawn from it: a second view of the same face (only used where several references fit)
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


@app.put("/api/stories/{sid}/art")
def update_art(sid: str, body: ArtIn):
    """Save the Art tab. Only the fields the page actually sent are changed, so a page opened before an update (which does not know the newer switches)
    can never switch them off by saving."""
    s = _story(sid)
    old, sent = dict(s.get("art") or {}), body.model_fields_set
    art = {**store.STORY_DEFAULTS["art"], **old}
    for k in sent:
        v = getattr(body, k)
        if k in ("direction", "negative"):
            art[k] = (v or "").strip()
        elif k == "size":
            art[k] = v if v in images.SHAPES else "portrait"
        elif k == "portrait_size":
            art[k] = v if v in ("small", "medium", "large") else "medium"
        elif k == "portrait_shape":
            art[k] = v if v in images.SHAPES else "landscape"
        elif k == "scene_where":
            art[k] = v if v in ("reply", "panel", "banner_top", "banner_bottom") else "reply"
        elif k == "setting":
            if v is not None:
                art[k] = v.strip()
        else:
            art[k] = v
    s["art"] = art
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


def _split_aka(text: str, own_name: str = "") -> list[str]:
    """'Naomi, Dr. N' -> ['Naomi', 'Dr. N'] (no blanks, no repeats, not the character's own name)."""
    out: list[str] = []
    for a in re.split(r"[,\n;]", text or ""):
        a = " ".join(a.split())[:40]
        if a and a.lower() != own_name.lower() and a.lower() not in {x.lower() for x in out}:
            out.append(a)
    return out


def _find_cast(s: dict, name: str) -> dict | None:
    """A cast member by full name, first name or one of their other names (case-insensitive)."""
    n = (name or "").strip().lower()
    if not n:
        return None
    for c in s.get("cast", []):
        if c["name"].lower() == n or n in {a.lower() for a in c.get("aka") or []}:
            return c
    for c in s.get("cast", []):
        if c["name"].lower().split()[0] == n:
            return c
    hits = [c for c in s.get("cast", []) if len(n) > 2 and n in [w.strip(".,").lower() for w in c["name"].split()]]   # a surname ("Okafor")
    return hits[0] if len(hits) == 1 else None


def _merge_cast(s: dict, source_slug: str, into_slug: str) -> None:
    """The same person was in the cast twice (for example "Naomi" and "Dr. Okafor"): fold the first into the second. The first one's name (and other names)
    become the second's "also called", their pictures join the second as earlier versions, their key events and side-panel row move over."""
    src, dst = _char(s, source_slug), _char(s, into_slug)
    if src is dst:
        raise HTTPException(400, "Pick a different character to merge into.")
    if src.get("player"):
        raise HTTPException(400, "The player character can't be merged into another character (merge the other one into the player instead).")
    aka = list(dst.get("aka") or [])
    for n in [src["name"]] + list(src.get("aka") or []):
        if n.lower() != dst["name"].lower() and n.lower() not in {a.lower() for a in aka}:
            aka.append(n)
    dst["aka"] = aka
    folder_src, folder_dst = store.PORTRAITS / s["id"] / "chars" / src["slug"], store.PORTRAITS / s["id"] / "chars" / dst["slug"]
    dst_alts = dst.setdefault("alts", {})
    for mood, url in list((src.get("moods") or {}).items()) + [(m, u) for m, us in (src.get("alts") or {}).items() for u in us]:
        f = _portrait_file(url)
        if not f:
            continue
        folder_dst.mkdir(parents=True, exist_ok=True)
        new = folder_dst / f"merged-{src['slug']}-{f.name}"
        shutil.copyfile(f, new)
        dst_alts.setdefault(mood, []).append(f"/portraits/{s['id']}/chars/{dst['slug']}/{new.name}")
    dst["events"] = (dst.get("events") or []) + (src.get("events") or [])
    for k in ("appearance", "bio", "personality"):
        if not (dst.get(k) or "").strip() and (src.get(k) or "").strip():
            dst[k] = src[k]
    s["cast"] = [c for c in s["cast"] if c["slug"] != src["slug"]]
    shutil.rmtree(folder_src, ignore_errors=True)
    alias = s.setdefault("char_alias", {})   # side-panel rows filed under the old name move to the real character's row
    for n in [src["name"]] + list(src.get("aka") or []):
        alias[n] = dst["name"]
    tracker.apply_aliases(s)


class MergeIn(BaseModel):
    into: str                            # slug of the character to keep


@app.post("/api/stories/{sid}/cast/{slug}/merge")
def merge_character(sid: str, slug: str, body: MergeIn):
    """Fold a duplicate cast entry into the character it really is."""
    s = _story(sid)
    store.snapshot_story(sid, "before merging characters")
    _merge_cast(s, slug, body.into)
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
                    "group": prev.get("group", ""), "player": prev.get("player", False), "ext_refs": prev.get("ext_refs", []), "tags": prev.get("tags", ""),
                    "aka": prev.get("aka", []) if m.aka is None else _split_aka(m.aka, name),
                    "address": prev.get("address", "") if m.address is None else m.address.strip()[:600],
                    "personality": prev.get("personality", "") if m.personality is None else m.personality.strip(),
                    "author": prev.get("author", False) if m.author is None else m.author})
    for slug in old.keys() - seen:
        shutil.rmtree(store.PORTRAITS / sid / "chars" / slug, ignore_errors=True)
        gone = old[slug]["name"]       # a character the player removed must not come back by itself from the next reply
        if gone.lower() not in {n.lower() for n in s.get("art_dismissed", [])}:
            s.setdefault("art_dismissed", []).append(gone)
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
    prompt = images.portrait_prompt(art, ch["name"], ch["appearance"], None if mood == "default" else mood, bool(refs), ext_only, nsfw=bool(s.get("nsfw")))
    negative = s["art"].get("negative", "")
    if _wants_tags(prov):
        prompt, negative = await _portrait_tag_prompt(s, cfg, art, ch, mood)
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
    s = await _draw_mood(sid, slug, body.mood, body.provider)
    cfg = store.load_config()
    if characters.add_to_extended(cfg, body.mood):   # a new look the narrator wanted: keep it for every later character
        store.save_config(cfg)
    return s


@app.get("/api/art-styles")
def art_styles():
    return images.STYLE_PRESETS


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
    return _role(cfg, s, "small" if purpose == "key events" else "recaps", purpose)   # only the per-reply log uses the small helper; condense/rebuild/bio keep the better one


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
            await _condense_after_turn(sid)    # a long log is tidied even on a reply with nothing new to log
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
        await _condense_after_turn(sid)
    except Exception:
        pass


CONDENSE_AT = 20      # a character with more than this many loose automatic events gets the older ones folded into a short history
KEEP_LOOSE = 8        # ...keeping this many newest ones word for word
MAX_DIGEST = 8        # lines in a character's condensed history
CONDENSE_SYSTEM = (
    "You tidy one character's log in an interactive story. Merge the OLD HISTORY lines and the OLDER EVENTS into at most {n} short lines of "
    "condensed history, oldest first. Keep first meetings, injuries, deaths and transformations, betrayals, confessions, promises, changes in a "
    "relationship, important decisions, discoveries and items gained; merge repeated or minor ones; drop trivia. Never invent anything. "
    "Each line is one or two short past-tense sentences naming who was involved; 'when' is the turn or range it covers (like 'Turns 3-20'). "
    'Reply with ONLY JSON: {{"history": [{{"when": "Turns 3-20", "text": "..."}}]}}'
)


async def _condense_character(sid: str, slug: str, force: bool = False) -> bool:
    """Fold a character's older automatic events into a short history (helper model). Your own entries are never touched."""
    cfg = store.load_config()
    s = store.load_story(sid)
    ch = next((c for c in s.get("cast", []) if c["slug"] == slug), None)
    if not ch:
        return False
    loose = [e for e in ch.get("events", []) if e.get("auto") and not e.get("digest")]
    digests = [e for e in ch.get("events", []) if e.get("digest")]
    long_digest = any(len(d["text"]) > 1200 for d in digests)     # e.g. a summary paragraph moved onto the card
    if not force and len(loose) <= CONDENSE_AT and len(digests) <= MAX_DIGEST + 2 and not long_digest:
        return False
    older = loose[:-KEEP_LOOSE] if len(loose) > KEEP_LOOSE else []
    if not older and len(digests) <= MAX_DIGEST and not long_digest:
        return False
    prov, model = _events_model(cfg, s, "key events condense")
    mat = "OLD HISTORY:\n" + "\n".join(f"({d['when']}) {d['text']}" for d in digests) + "\n\nOLDER EVENTS:\n" + "\n".join(f"({e['when']}) {e['text']}" for e in older)
    out = await providers.complete(prov, model, CONDENSE_SYSTEM.format(n=MAX_DIGEST), [{"role": "user", "content": f"Character: {ch['name']}\n\n{mat}"}],
                                   temperature=0.2, max_tokens=1200, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    hist = j.get("history") if isinstance(j, dict) else None
    lines = [(str(h.get("when", "")).strip(), str(h.get("text", "")).strip()) for h in (hist or []) if isinstance(h, dict) and str(h.get("text", "")).strip()]
    if not lines:
        return False
    s = store.load_story(sid)   # freshest copy before saving
    ch = next((c for c in s.get("cast", []) if c["slug"] == slug), None)
    if not ch:
        return False
    gone = {e["id"] for e in older} | {d["id"] for d in digests}
    keep = [e for e in ch.get("events", []) if e["id"] not in gone]
    ch["events"] = [{**_ev(w, t, None, True), "digest": True} for w, t in lines[:MAX_DIGEST]] + keep
    store.save_story(s)
    return True


async def _condense_after_turn(sid: str) -> None:
    """Periodic tidy: after a reply, any character whose log has grown long gets their older entries condensed."""
    if not store.load_config().get("char_memory", True):
        return
    s = store.load_story(sid)
    for c in s.get("cast", []):
        n = len([e for e in c.get("events", []) if e.get("auto") and not e.get("digest")])
        if n > CONDENSE_AT or any(e.get("digest") and len(e["text"]) > 1200 for e in c.get("events", [])):
            try:
                usage.set_ctx(sid, "key events condense")
                await _condense_character(sid, c["slug"])
            except Exception:
                pass


@app.post("/api/stories/{sid}/cast/{slug}/events/condense")
async def condense_events(sid: str, slug: str):
    """Condense this character's older automatic events into a short history now (one text-model call)."""
    _char(_story(sid), slug)
    if not await _condense_character(sid, slug, force=True):
        raise HTTPException(400, "Nothing to condense yet: there are no older automatic events, or the text model did not return a usable history.")
    return _story(sid)


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


class TagsIn(BaseModel):
    tags: str = ""


def _tags_info(s: dict, slug: str) -> dict:
    ch = _char(s, slug)
    own = (ch.get("tags") or "").strip()
    cached = (s.get("tag_cache") or {}).get(_tag_key(s, s.get("art") or {}, ch), "") if not own else ""
    return {"tags": own or cached, "custom": bool(own), "saved": bool(own or cached)}


@app.get("/api/stories/{sid}/cast/{slug}/tags")
def get_tags(sid: str, slug: str):
    """The picture tags used for this character with tag-style picture models (written once by the helper model, or by you). No model call."""
    return _tags_info(_story(sid), slug)


@app.put("/api/stories/{sid}/cast/{slug}/tags")
def put_tags(sid: str, slug: str, body: TagsIn):
    """Your own tags for this character (replaces the helper's; empty = go back to the helper's)."""
    s = _story(sid)
    ch = _char(s, slug)
    ch["tags"] = ", ".join(t.strip() for t in body.tags.replace("\n", ",").split(",") if t.strip())[:1500]
    store.save_story(s)
    return _tags_info(s, slug)


@app.post("/api/stories/{sid}/cast/{slug}/tags/rebuild")
async def rebuild_tags(sid: str, slug: str):
    """Have the helper model write this character's tags again from their description (one small call). Your own tags are replaced."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    if not (ch.get("appearance") or "").strip():
        raise HTTPException(400, "Describe how this character looks first.")
    ch["tags"] = ""
    art = await _art_with_setting(s, cfg)
    (s.get("tag_cache") or {}).pop(_tag_key(s, art, ch), None)
    store.save_story(s)
    usage.set_ctx(sid, "image prompt")
    await _character_tags(_story(sid), cfg, art, _char(s, slug))
    return _tags_info(_story(sid), slug)


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


_NEWCHAR_AI = ("You read story passages and report on the named characters. Reply with ONLY JSON: "
               '{"new": [{"name": "...", "appearance": "..."}], "same": [{"label": "...", "name": "..."}], "aka": [{"alias": "...", "of": "..."}]}. '
               '"new": EVERY character called by a personal name in the text (narration or dialogue) who is not on the known list; the player character and the known '
               "list are excluded. Only people with a name count: skip unnamed extras, groups and places. For each give a short purely visual description for an image "
               "generator, drawn only from what the text says (age, hair, eyes, build, clothing); leave out anything not stated. Keep descriptions safe for work. "
               '"same": for each side-panel label you are given (a generic tag such as "Shop Girl"), say which named character it is, known or new, when the passages '
               "make that clear; leave the label out when it is someone else or you are not sure. "
               'Also add "aka": [{"alias": "...", "of": "..."}] for any name in the passages (a first name, surname, nickname or title) that is just ANOTHER NAME for a '
               "character on the known list (for example the passages call Dr. Okafor \"Naomi\"), whether or not the alias is itself on the known list. Only when the passages make it clear; "
               'never list such an alias under "new". This is a fiction-writing tool; just report names. Use [] for an empty list.')


def _tracker_labels(s: dict) -> list[str]:
    """Side-panel character rows that do not match anyone in the cast by name (usually a generic label like "Shop Girl")."""
    first = {c["name"].lower().split()[0] for c in s.get("cast", [])} | {c["name"].lower() for c in s.get("cast", [])}
    first |= {a.lower() for c in s.get("cast", []) for a in c.get("aka") or []}
    asked = s.get("alias_asked") or {}
    now = len(s.get("turns", []))
    return [n for n in ((s.get("tracker") or {}).get("chars") or {}) if n.lower() not in first and n.lower().split()[0] not in first
            and now - asked.get(n, -99) >= 8 and n not in (s.get("char_alias") or {})]


def _apply_aka(story: dict, items: list[dict]) -> None:
    """The helper model says a name is just another name for a known character: remember it as "also called". If that name is already a separate cast entry
    with no pictures (made earlier by mistake, like "Naomi" next to "Dr. Okafor"), it is folded into the real character; one with pictures is left for the player."""
    for m in items:
        alias, of = " ".join(str(m.get("alias", "")).split())[:40], str(m.get("of", "")).strip()
        target = _find_cast(story, of)
        if not alias or not target or alias.lower() == target["name"].lower():
            continue
        dup = next((c for c in story["cast"] if c["name"].lower() == alias.lower()), None)
        if dup is target:
            continue
        if dup:
            if dup.get("moods") or dup.get("player") or dup.get("alts"):
                continue
            _merge_cast(story, dup["slug"], target["slug"])
        elif alias.lower() not in {a.lower() for a in target.get("aka") or []} and not _find_cast(story, alias):
            target["aka"] = (target.get("aka") or []) + [alias]
            story.setdefault("char_alias", {})[alias] = target["name"]


_CAP_WORD = re.compile(r"\b[A-Z][a-z]{2,}\b")


def _unseen_names(sid: str) -> bool:
    """Free pre-check for the new-character helper: does the newest reply contain a capitalised word in mid-sentence (a possible name) that is not a known
    character and was never offered to the helper before? Each such word is offered once, so the helper only runs when something new appears."""
    s = store.load_story(sid)
    last = next((t["text"] for t in reversed(s["turns"]) if t["role"] == "assistant"), "")
    text = prompting.split_scene(prompting.split_state(last)[0])[0]
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    known = characters.known_names(s) | characters.GENERIC
    seen = set(s.get("nc_seen") or [])
    new = set()
    for m in _CAP_WORD.finditer(text):
        before = text[:m.start()].rstrip(" \t*_\"“”'‘’(")
        if not before or before[-1] in ".!?…:\n|":    # first word of a sentence: could be any word
            continue
        w = m.group(0).lower()
        if w not in known and w not in seen:
            new.add(w)
    for n in characters.detect_new(s):
        w = n["name"].lower()
        if w not in seen:
            new.add(w)
    if not new:
        return False
    s["nc_seen"] = sorted(seen | new)[-600:]
    store.save_story(s)
    return True


async def _find_new_chars(sid: str) -> None:
    """After a reply (opt-in art.newchar): the narrator does not always write the hidden <newchar> tag, so the helper model reads the last replies, adds
    any named character missing from the cast, and works out which named character a generic side-panel label ("Shop Girl") really is. Failures are
    silent: it is a convenience, not part of the turn."""
    try:
        s, cfg = _story(sid), store.load_config()
        want_new = bool((s.get("art") or {}).get("newchar"))
        labels = _tracker_labels(s)
        if not want_new and not labels:
            return
        if not labels and not _unseen_names(sid):   # nothing new to look at: no helper call (it used to run after every reply)
            return
        n = 6 if labels else 3
        texts = [prompting.split_scene(prompting.split_state(t["text"])[0])[0] for t in s["turns"] if t["role"] == "assistant"][-n:]
        if not texts:
            return
        known = sorted({c["name"] + (f" (also called {', '.join(c['aka'])})" if c.get("aka") else "") for c in s.get("cast", [])} | {prompting.player_name(s)})
        prov, model = _role(cfg, s, "small", "new characters")
        ask = "Known characters: " + (", ".join(known) or "none") + ("\nSide-panel labels to identify: " + ", ".join(labels) if labels else "") + "\n\n" + "\n\n---\n\n".join(texts)[-12000:]
        out = await providers.complete(prov, model, _NEWCHAR_AI, [{"role": "user", "content": ask}], temperature=0.2, max_tokens=900, num_ctx=cfg["num_ctx"], **_extras(cfg))
        j = _json_in(out)
        if isinstance(j, list):
            j = {"new": j}
        if not isinstance(j, dict):
            return
        fresh = _story(sid)
        if want_new:
            _apply_aka(fresh, [m for m in (j.get("aka") or []) if isinstance(m, dict)])
            _add_new_chars(fresh, [(str(c.get("name", "")), str(c.get("appearance", ""))) for c in (j.get("new") or []) if isinstance(c, dict)])
        names = {c["name"].lower(): c["name"] for c in fresh["cast"]}
        names.update({c["name"].lower().split()[0]: c["name"] for c in fresh["cast"]})
        alias = fresh.setdefault("char_alias", {})
        for m in j.get("same") or []:
            if isinstance(m, dict) and m.get("label") in labels and str(m.get("name", "")).lower() in names:
                alias[m["label"]] = names[str(m["name"]).lower()]
        asked = fresh.setdefault("alias_asked", {})
        for lb in labels:
            asked[lb] = len(fresh["turns"])
        tracker.apply_aliases(fresh)
        store.save_story(fresh)
    except Exception:
        pass


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


def _add_new_chars(story: dict, items: list[tuple[str, str]]) -> None:
    """Opt-in (art.newchar): characters the narrator flagged with <newchar> join the cast with their look, so the art banner offers pictures."""
    if not (story.get("art") or {}).get("newchar"):
        return
    known = characters.known_names(story)
    for raw, look in items:
        n = characters.clean_name(raw)
        if not n or n.lower().startswith(("the ", "her ", "his ", "their ", "my ", "your ", "a ", "an ")) or re.search(r"['’]s ", n) or n.lower() in known or n.lower().split()[0] in known:
            continue
        _ensure_char(story, n, look[:400])
        known.add(n.lower())


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
            prompt, negative = await _portrait_tag_prompt(st, cf, art, c, "default", notes)
        else:
            prompt = images.portrait_prompt(art, c["name"], c["appearance"] + (f". Extra notes: {notes}" if notes else ""), None, False, nsfw=bool(st.get("nsfw")))
            negative = st["art"].get("negative", "")
        folder = store.PORTRAITS / sid / "chars" / slug / "_candidates"
        folder.mkdir(parents=True, exist_ok=True)
        cand_refs = _load_refs([r["url"] for r in c.get("ext_refs") or []], int(cf.get("image_max_refs", 3))) if not c.get("moods") else []
        if cand_refs and not _wants_tags(prov):
            prompt = images.portrait_prompt(art, c["name"], c["appearance"] + (f". Extra notes: {notes}" if notes else ""), None, True, True, nsfw=bool(st.get("nsfw")))
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
        wanted = [str(m).strip().lower() for m in (cfg.get("mood_sets") or characters.DEFAULT_SETS).get(body.preset, []) if str(m).strip()]   # the player's own lists: used as written
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


def _for_tracker(text: str, cap: int = 12000) -> str:
    """A reply as the side-panel helper should read it. Unlike the picture cleaner this KEEPS the fenced status / [SYSTEM NOTIFICATION]
    blocks (that is where totals and quest progress are written), and when it has to cut, it keeps the end of the reply, where they usually are."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"^\[//\]:.*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= cap else text[: cap // 3] + "\n[…]\n" + text[-(cap - cap // 3):]


def _json_in(text: str):
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


async def _describe_scene(s: dict, cfg: dict, turn_text: str) -> tuple[str, list[str], dict]:
    """Ask the story's text model for one image prompt from a reply, naming the characters in it."""
    prov, model = _role(cfg, s, "small", "scene prompt")
    names = [c["name"] for c in s.get("cast", [])] + [prompting.player_name(s)]
    system = ("You write image-generation prompts from story passages. Pick the single most visual moment. Reply with ONLY JSON: "
              '{"scene": "<one or two sentences: setting, who is present, their poses and expressions, lighting; make each character’s mood clear in both face and body language>", '
              '"characters": ["<names of the characters visible>"], "moods": {"<character name>": "<their expression in one word>"}}. '
              "Use only names from the known list. "
              + ("Adult content is allowed in this story: an intimate or explicit moment may be described as it happens, between adults only "
                 "(everyone shown is 18 or older with adult features)." if s.get("nsfw") else
                 "Keep it safe for work: no nudity or explicit content; for intimate moments choose a different frame."))
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


MOOD_LOOKS = {   # what to draw for common mood names (numbers like "angry 2" are stripped first): the face AND the body language
    "normal": "calm neutral expression, relaxed posture, arms at sides",
    "smile": "warm genuine smile reaching the eyes, relaxed open shoulders, head slightly tilted",
    "shy": "shy and embarrassed, blushing cheeks, eyes looking away, head ducked, shoulders drawn in, fingers fidgeting at chest",
    "surprise": "surprised, wide eyes, raised eyebrows, open mouth, leaning back slightly, hands raised",
    "sulky": "sulking, pouting lips, puffed cheeks, eyes cut sideways, arms crossed tightly, head turned away",
    "fear": "terrified, wide frightened eyes, pale face, trembling lips, shoulders hunched, hands clutched to chest, shrinking back",
    "sad": "sad, teary eyes, trembling lower lip, eyebrows angled up in the middle, downcast gaze, slumped shoulders, head bowed",
    "angry": "furious, deep furrowed brows, glaring eyes, clenched teeth, tense shoulders, fists clenched, leaning forward",
    "scorn": "scornful, half-lidded eyes looking down the nose, one eyebrow raised, curled disdainful smirk, chin raised, arms crossed",
    "despair": "despair, tears running down the cheeks, hollow lifeless eyes, mouth slightly open, slumped shoulders, hands limp or covering the face",
    "combat": "fierce determined glare, battle-ready stance, knees bent, fists or weapon raised, dynamic action pose",
    "cute": "cute pose, blushing, playful smile, winking or wide sparkling eyes, head tilted, hands near face",
    "dead": "collapsed on the floor, eyes closed, pale, motionless",
    "zombie": "undead, pale grey skin, glowing red eyes, torn uniform, slack jaw, hunched lurching posture",
}


def _mood_look(mood: str) -> str:
    base = re.sub(r"\s*\d+$", "", mood.strip().lower())
    mine = {str(k).strip().lower(): str(v).strip() for k, v in (store.load_config().get("mood_looks") or {}).items() if str(v).strip()}
    return mine.get(base) or MOOD_LOOKS.get(base, f"{base} expression")


def _wants_tags(prov: dict) -> bool:
    """Anime SDXL models (Animagine, Illustrious, NoobAI...) follow short Danbooru-style tags better than sentences."""
    return (prov.get("prompt_style") or ("tags" if prov.get("type") == "comfyui" else "natural")) == "tags"


async def _tag_prompt(s: dict, cfg: dict, art: dict, scene: str, chars: list[dict]) -> tuple[str, str]:
    """(positive, negative) for a tag-style image model: the text model turns the description into tags."""
    tags = await _tag_line(s, cfg, art, scene, chars)
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), tags, TAG_QUALITY) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE) if x)
    return positive, negative


async def _tag_line(s: dict, cfg: dict, art: dict, scene: str, chars: list[dict]) -> str:
    prov, model = _role(cfg, s, "small", "image prompt")
    system = ("You write prompts for anime image models in Danbooru tag style. Reply with ONE line of comma-separated lowercase tags: "
              "subject count first (1girl, 2girls, 1boy, ...), then for each character hair, eyes, build and clothing, then expression, "
              "pose, action, setting, lighting and framing (e.g. upper body, from side, wide shot). When a mood or expression is given, make it unmistakable: use several "
              "specific tags for the eyes, eyebrows, mouth AND body language (posture, shoulders, hands), put them early, and prefer strong over subtle. No sentences, no character names, "
              "no quality tags, no markdown. " + ("Adult content is allowed here (adults only: everyone shown is 18 or older); use explicit tags when the scene calls for them. " if s.get("nsfw")
                                                  else "Keep it safe for work: no nudity or explicit content. ") + "If a Setting is given, clothing, props and style must suit that world.")
    who = "\n".join(f"- {c['name']}: {c.get('appearance', '')}" for c in chars) or "(none given)"
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Scene: {scene}\nCharacters:\n{who}" + (f"\nSetting: {art['setting']}" if art.get("setting") else "")}],
                                   temperature=0.3, max_tokens=300, num_ctx=cfg["num_ctx"], **_extras(cfg))
    tags = " ".join(out.replace("\n", " ").replace("`", "").split()).strip(" ,")
    if not tags:
        raise HTTPException(502, "The text model returned no tags. Try again, or switch this image provider to sentence-style prompts.")
    return tags


MOOD_TAGS = {   # the tag-style version of MOOD_LOOKS, so a mood portrait needs no text-model call
    "normal": "neutral expression, closed mouth, relaxed, arms at sides",
    "smile": "smile, warm smile, happy, relaxed shoulders, head tilt",
    "shy": "shy, embarrassed, blush, looking away, head down, shoulders in, fidgeting, hands at chest",
    "surprise": "surprised, wide eyes, raised eyebrows, open mouth, leaning back, hands up",
    "sulky": "sulking, pout, puffed cheeks, sideways glance, crossed arms, looking away",
    "fear": "scared, terrified, wide eyes, pale, trembling lips, hunched shoulders, hands clutched to chest, shrinking back",
    "sad": "sad, teary eyes, trembling lips, furrowed brow angled up, downcast eyes, slumped shoulders, head down",
    "angry": "angry, furious, furrowed brow, glaring, clenched teeth, tense shoulders, clenched fists, leaning forward",
    "scorn": "scornful, half-closed eyes, looking down at viewer, raised eyebrow, smirk, chin up, crossed arms",
    "despair": "despair, crying, tears streaming, empty eyes, open mouth, slumped shoulders, hands over face",
    "combat": "fierce glare, determined, fighting stance, bent knees, clenched fists, dynamic pose",
    "cute": "cute, blush, playful smile, wink, head tilt, hands near face",
    "dead": "lying on floor, closed eyes, pale, motionless",
    "zombie": "undead, grey skin, glowing red eyes, torn clothes, slack jaw, hunched, lurching",
}
_SUBJECT_TAG = re.compile(r"^(\d+(girl|boy|girls|boys|other|others)|solo|no humans)$")
_EXPRESSION_TAG = re.compile(r"expression|smil|frown|pout|blush|looking|mouth|upper body|portrait|pose|standing|sitting|arms |hands|framing|close-up|wide shot", re.IGNORECASE)


def _tag_key(s: dict, art: dict, ch: dict) -> str:
    return hashlib.sha1(f"{ch.get('appearance', '')}|{art.get('setting', '')}|{bool(s.get('nsfw'))}|v2".encode()).hexdigest()[:16]


async def _character_tags(s: dict, cfg: dict, art: dict, ch: dict) -> str:
    """The tags that describe WHO a character is (subject count, hair, eyes, build, clothing, setting), written by the text model once and kept with the
    story until the appearance, the story's setting or the adult-content switch changes."""
    if (ch.get("tags") or "").strip():
        return ch["tags"].strip()                      # tags the player wrote or edited on the character's page
    key = _tag_key(s, art, ch)
    cached = (s.get("tag_cache") or {}).get(key)
    if cached:
        return cached
    tags = await _tag_line(s, cfg, art, "character design sheet: write ONLY the subject count (1girl or 1boy), then this character's hair, eyes, skin, build, age and clothing tags "
                                         "(clothing that suits the story's world). Do NOT write any setting, background, location, lighting, expression, pose, action, hands, gaze or framing tags, and no 'solo'", [ch])
    kept = [t.strip() for t in tags.split(",") if t.strip() and not _EXPRESSION_TAG.search(t) and t.strip() != "solo"]
    seen_t, uniq = set(), []
    for t in kept:
        if t.lower() not in seen_t:
            seen_t.add(t.lower()); uniq.append(t)
    tags = ", ".join(uniq)
    if tags:
        fresh = _story(s["id"])
        cache = fresh.setdefault("tag_cache", {})
        cache[key] = tags
        for old in list(cache)[:-80]:
            cache.pop(old, None)
        store.save_story(fresh)
    return tags


async def _portrait_tag_prompt(s: dict, cfg: dict, art: dict, ch: dict, mood: str, notes: str = "") -> tuple[str, str]:
    """(positive, negative) for a tag-style portrait without a per-picture text-model call: the character's saved tags + the tags for the mood. Moods that
    are not built in, or that you reworded in Settings, and drawings with extra notes still go through the text model."""
    base = re.sub(r"\s*\d+$", "", mood.strip().lower())
    mine = {str(k).strip().lower(): str(v).strip() for k, v in (cfg.get("mood_looks") or {}).items() if str(v).strip()}
    if notes or (mood != "default" and (base in mine or base not in MOOD_TAGS)):
        look = "character portrait, upper body, looking at viewer" + ("" if mood == "default" else f", {_mood_look(mood)}") + (f", {notes}" if notes else "")
        return await _tag_prompt(s, cfg, art, look, [ch])
    tags = await _character_tags(s, cfg, art, ch)
    if not tags:
        return await _tag_prompt(s, cfg, art, "character portrait, upper body, looking at viewer" + ("" if mood == "default" else f", {_mood_look(mood)}"), [ch])
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    head = [parts.pop(0)] if parts and _SUBJECT_TAG.match(parts[0]) else []
    parts = [t for t in parts if not _SUBJECT_TAG.match(t)]
    mood_tags = MOOD_TAGS["normal" if mood == "default" else base]
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), ", ".join(head), mood_tags, ", ".join(parts), "upper body, looking at viewer", TAG_QUALITY) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE) if x)
    return positive, negative


SCENE_TAGS_SYSTEM = ("You write the SCENE part of a prompt for anime image models in Danbooru tag style. Reply with ONE line of comma-separated lowercase tags: "
                     "each character's expression and body language (specific eye, eyebrow, mouth, posture and hand tags, strong rather than subtle), then pose, action, interaction, "
                     "setting, lighting and framing (e.g. upper body, from side, wide shot, two shot). The characters' looks (hair, eyes, build, clothing) and the subject count are "
                     "added separately: do NOT describe them, no character names, no subject-count tags, no quality tags, no sentences, no markdown. ")


def _subject_count(heads: list[str]) -> str:
    g = sum(int(m.group(1)) for h in heads for m in [re.match(r"^(\d+)girls?$", h)] if m)
    b = sum(int(m.group(1)) for h in heads for m in [re.match(r"^(\d+)boys?$", h)] if m)
    out = ([f"{g}girl" + ("s" if g > 1 else "")] if g else []) + ([f"{b}boy" + ("s" if b > 1 else "")] if b else [])
    return ", ".join(out)


async def _scene_tag_prompt(s: dict, cfg: dict, art: dict, scene: str, chars: list[dict]) -> tuple[str, str]:
    """(positive, negative) for a scene picture with a tag-style model. The characters' looks come from their saved tags (the same words in every picture,
    which also keeps faces steadier); the text model is only asked for the expressions, action, setting and framing, so it reads and writes much less."""
    if not chars:
        return await _tag_prompt(s, cfg, art, scene, chars)
    heads, bodies, seen_n, uniq_chars = [], [], set(), []
    for c in chars:                                       # the player can appear twice (cast entry and avatar)
        if c["name"].lower() not in seen_n:
            seen_n.add(c["name"].lower()); uniq_chars.append(c)
    chars = uniq_chars
    for c in chars:
        mine = next((x for x in s.get("cast", []) if x["slug"] == c.get("slug")), {})
        tags = await _character_tags(s, cfg, art, {"name": c["name"], "appearance": c.get("appearance", ""), "slug": c.get("slug"), "tags": mine.get("tags", "")})
        if not tags or not (c.get("appearance", "").strip() or mine.get("tags")):
            return await _tag_prompt(s, cfg, art, scene, chars)         # a character with no description: the model has to improvise their looks
        parts = [t.strip() for t in tags.split(",") if t.strip()]
        heads.append(parts.pop(0) if parts and _SUBJECT_TAG.match(parts[0]) else "")
        bodies.append(", ".join(t for t in parts if not _SUBJECT_TAG.match(t)))
    prov, model = _role(cfg, s, "small", "image prompt")
    system = SCENE_TAGS_SYSTEM + ("Adult content is allowed here (adults only: everyone shown is 18 or older); use explicit tags when the scene calls for them. " if s.get("nsfw")
                                  else "Keep it safe for work: no nudity or explicit content. ") + "If a Setting is given, props and style must suit that world."
    who = ", ".join(f"{c['name']} ({h or 'person'})" for c, h in zip(chars, heads))
    out = await providers.complete(prov, model, system, [{"role": "user", "content": f"Scene: {scene}\nCharacters in the picture: {who}" + (f"\nSetting: {art['setting']}" if art.get("setting") else "")}],
                                   temperature=0.3, max_tokens=220, num_ctx=cfg["num_ctx"], **_extras(cfg))
    scene_tags = " ".join(out.replace("\n", " ").replace("`", "").split()).strip(" ,")
    names = sorted({w for c in chars for w in re.findall(r"[A-Za-z][\w'’-]{2,}", c["name"]) if w.lower() not in ("the", "and")}, key=len, reverse=True)
    if names:      # models still sometimes write the names in; image models read them as noise
        scene_tags = re.sub(r"\b(" + "|".join(map(re.escape, names)) + r")\b\s*", "", scene_tags, flags=re.IGNORECASE)
    scene_tags = ", ".join(t.strip() for t in scene_tags.split(",") if t.strip() and not re.fullmatch(r"(one|two|three|four|\d+) (people|persons|girls|boys)", t.strip(), re.I))
    if not scene_tags:
        raise HTTPException(502, "The text model returned no tags. Try again, or switch this image provider to sentence-style prompts.")
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), _subject_count(heads), *bodies, scene_tags, TAG_QUALITY) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE) if x)
    return positive, negative


def _scene_chars(s: dict, text: str, names: list[str] | None) -> list[dict]:
    """Who is in the picture: the named characters (or any whose name appears in the text), plus the player's avatar."""
    want = {n.lower() for n in (names or [])}
    low = text.lower()
    out = []
    for c in s.get("cast", []):
        firsts = [c["name"].split()[0].lower()] + [a.split()[0].lower() for a in c.get("aka") or []]
        if c["name"].lower() in want or any(f in want or re.search(rf"\b{re.escape(f)}\b", low) for f in firsts) or any(a.lower() in want for a in c.get("aka") or []):
            out.append({"name": c["name"], "appearance": c.get("appearance", ""), "slug": c["slug"], "ref_url": None, "ref": False, "firsts": firsts})
    av, pn = s.get("avatar") or {}, prompting.player_name(s)
    if pn != "the player character" and (pn.lower() in want or pn.split()[0].lower() in want or re.search(rf"\b{re.escape(pn.split()[0].lower())}\b", low)):
        out.append({"name": pn, "appearance": av.get("appearance", ""), "ref_url": s.get("avatar_image") or None, "ref": bool(s.get("avatar_image"))})
    # Most prominent first (earliest mention): a face-matching workflow uses the first reference as the main character.
    def first_seen(c: dict) -> int:
        hits = [i for i in (low.find(f) for f in c.get("firsts") or [c["name"].split()[0].lower()]) if i >= 0]
        return min(hits) if hits else len(low)
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
    prompt = images.scene_prompt(s["art"], scene, chars, max_refs, nsfw=bool(s.get("nsfw")))
    art = s["art"]
    negative = art.get("negative", "")
    if _wants_tags(prov):
        prompt, negative = await _scene_tag_prompt(s, cfg, art, scene, chars)
    try:
        shape = "landscape" if str(art.get("scene_where", "")).startswith("banner") else art.get("size", "portrait")   # a wide strip needs a wide picture
        data, ext = await images.generate(prov, prompt, negative, shape, refs)
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    img = {"url": _save_scene_image(sid, data, ext), "scene": scene, "provider": prov["id"], "ts": time.time()}
    # Drawing takes a while: add the picture to the freshest copy of the story. Saving the copy loaded before drawing would undo anything that changed meanwhile
    # (the side panel, the "right now" card, the background, edits).
    fresh = _story(sid)
    if 0 <= idx < len(fresh["turns"]) and fresh["turns"][idx]["role"] == "assistant":
        fresh["turns"][idx].setdefault("images", []).append(img)
        store.save_story(fresh)
    return fresh


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


@app.get("/api/images/bg-models")
async def bg_models():
    """Diffusion models (the UNET loader's list) that the local ComfyUI has: what a separate backdrop maker like FLUX can use."""
    cfg = store.load_config()
    comfy = next((p for p in cfg.get("image_providers", []) if p.get("type") == "comfyui"), None)
    base = ((comfy or {}).get("base_url") or "http://127.0.0.1:8188").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            info = (await c.get(f"{base}/object_info/UNETLoader")).json()
        return {"models": sorted(info["UNETLoader"]["input"]["required"]["unet_name"][0]), "up": True}
    except Exception:
        return {"models": [], "up": False}


class BgTestIn(BaseModel):
    settings: dict = {}      # unsaved edits from the settings screen: {"model": ..., "workflow": ...}


@app.post("/api/image-test-bg")
async def image_test_bg(body: BgTestIn):
    """Draw one small test backdrop with the separate backdrop maker, using the settings as they are on screen."""
    import base64
    cfg = store.load_config()
    cfg["image_bg"] = {**(cfg.get("image_bg") or {}), **{k: v for k, v in body.settings.items() if v not in ("", None)}, "on": True}
    prov = _bg_provider(cfg, None)
    try:
        data, ext = await images.generate(prov, "an empty cold white laboratory room with a reclined chair and a glass window, wide establishing shot, soft light", "", "square")
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    return {"image": f"data:{images.sniff(data)[1]};base64," + base64.b64encode(data).decode()}


@app.get("/api/images/starter-workflow")
def starter_workflow(kind: str = "plain"):
    """kind=plain: text-to-image. kind=face: IPAdapter face-matching (needs the IPAdapter nodes and models in ComfyUI). kind=flux: FLUX.2 klein 4B text-to-image."""
    if kind in ("face", "flux"):
        f = ROOT / "workflows" / ("comfyui-ipadapter-face.json" if kind == "face" else "comfyui-flux2-klein.json")
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


def _migrate_characters(story: dict) -> int:
    """One-time: the "Key characters:" part of an old-style summary moves onto the characters' own cards (as condensed history entries), so it is sent
    only while they are in the scene instead of in every turn. Bullets that match nobody stay in the summary. Returns how many were moved."""
    parts = prompting.split_summary(story.get("summary", ""))
    if not parts:
        return 0
    head, chars, events = parts
    if not chars:
        return 0
    bullets: list[str] = []
    for line in chars.splitlines()[1:]:
        if line.startswith("- "):
            bullets.append(line[2:].strip())
        elif line.strip() and bullets:
            bullets[-1] += " " + line.strip()
    cast = story.get("cast") or []
    moved, left = 0, []
    for b in bullets:
        who, _, body = b.partition(":")
        key = who.strip().lower()
        words = set(re.findall(r"[\w'’-]+", key))
        ch = next((c for c in cast if key in {c["name"].lower(), c["name"].lower().split()[0], *(a.lower() for a in c.get("aka") or [])}), None)
        if not ch and words:        # "Dr. Naomi Okafor" for the cast entry "Dr. Okafor": every word of one name is in the other
            ch = next((c for c in cast if (cw := set(re.findall(r"[\w'’-]+", c["name"].lower()))) and (cw <= words or words <= cw)), None)
        if ch and body.strip():
            ch.setdefault("events", []).insert(0, {**_ev("Earlier", body.strip()[:3000], None, True), "digest": True})
            moved += 1
        else:
            left.append("- " + b)
    story["summary"] = prompting.join_summary(head, ("Key characters:\n" + "\n".join(left)) if left else "", events)
    return moved


async def _summarize_append(story: dict, cfg: dict, prov: dict, model: str, start: int, cut: int) -> bool:
    """Memory update that only writes what is NEW: a refreshed status paragraph and numbered entries for the new turns, which the app appends to the
    event list. Older entries are merged into a short paragraph once the list grows long. False = the model's answer did not fit; the caller falls back."""
    import re as _re
    parts = prompting.split_summary(story.get("summary", ""))
    if not parts:
        return False
    if not story.get("summary_v2"):
        store.snapshot_story(story["id"], "before summary restructure")
        _migrate_characters(story)
        story["summary_v2"] = True
        parts = prompting.split_summary(story["summary"])
    head, chars, events = parts
    intro, entries = prompting.event_entries(events)
    last_no = max([int(m.group(1)) for e in entries for m in [_re.match(r"(\d+)\.", e)] if m] or [0])
    out = await providers.complete(prov, model, prompting.SUMMARY_APPEND_SYSTEM,
                                   prompting.summary_append_request(head, entries[-3:], story["turns"][start:cut], last_no + 1),
                                   temperature=0.3, max_tokens=1800, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    m = _re.search(r"STATUS:\s*(.*?)\s*EVENTS:\s*(.+)$", out.strip(), _re.DOTALL)
    if not m or not _re.search(r"(?m)^\s*\d+\.\s", m.group(2)):
        return False
    status, new_events = m.group(1).strip(), m.group(2).strip()
    new_entries = [e.strip() for e in _re.split(r"(?m)^(?=\s*\d+\.\s)", new_events) if _re.match(r"\s*\d+\.\s", e)]
    entries += new_entries
    if sum(len(e) for e in entries) > 7000 and len(entries) > 14:           # compact the oldest entries into the intro paragraph
        old, entries = entries[:-10], entries[-10:]
        try:
            cm = await providers.complete(prov, model, prompting.SUMMARY_COMPACT_SYSTEM, [{"role": "user", "content": (intro + "\n" if intro else "") + "\n".join(old)}],
                                          temperature=0.2, max_tokens=1200, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
            intro = "Earlier events (condensed): " + " ".join(cm.split())
        except Exception:
            entries = old + entries
    head_new = "SUMMARY OF STORY SO FAR\n\n" + (status if status.lower().startswith("setting") else "Setting and status: " + status)
    story["summary"] = prompting.join_summary(head_new, chars, ((intro + "\n") if intro else "") + "\n".join(entries))
    return True


SUMMARY_DEFER = 1.5     # with Claude's prompt cache warm, a memory update waits until the unsummarized history is this many times the usual limit


async def _maybe_summarize(story: dict, cfg: dict, prov: dict, model: str, narrator: dict | None = None) -> None:
    start = story.get("summary_upto", 0)
    when = max(int(cfg.get("summarize_when", 24)), 4)
    keep = max(min(int(cfg.get("keep_recent", 10)), when - 2), 2)
    over = len(story["turns"]) - start
    if over <= when:
        return
    # Rewriting the summary changes the cached part of the prompt, so the next reply has to pay to write it again. While the cache is warm that is a
    # real cost; once it has gone cold (you were away) it costs nothing extra. So a warm cache gets a grace period of extra history (which is cheap, cached).
    if (narrator and narrator.get("type") == "anthropic" and cfg.get("prompt_cache", True) and over <= when * SUMMARY_DEFER
            and _warm["story_id"] == story["id"] and _warm["touched"] + _warm["ttl"] > time.time()):
        return
    cut = len(story["turns"]) - keep
    cut -= cut % 2  # keep the recent window starting on a player turn
    if cut <= start:
        return
    store.snapshot_story(story["id"], "before summarize")
    if prompting.char_memory_on() and prompting.split_summary(story.get("summary", "")):
        try:
            if await _summarize_append(story, cfg, prov, model, start, cut):
                story["summary_upto"] = cut
                store.save_story(story)
                return
        except Exception:
            pass                                       # fall back to the full rewrite below
    summary = await providers.complete(
        prov,
        model,
        prompting.summary_system(),
        prompting.summary_request(story.get("summary", ""), story["turns"][start:cut], prompting.logs_text(story) if prompting.char_memory_on() else ""),
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
            await _maybe_summarize(story, cfg, rprov, rmodel, prov)
            usage.set_ctx(sid, "story narration")
            before = prompting.snapshot(story)
            story["turns"].append({"role": "user", "text": text, "ts": time.time()})
            built = prompting.build(story)
            usage.set_ctx(sid, "story narration", why=_cache_why(sid, built, cfg))
            system, messages = built.request()
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
            narration, upd = prompting.split_state(prompting.expand_tags(full))
            _add_new_chars(story, prompting.extract_newchars(narration))
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
            _start_auto_backdrop(saved["id"])
            _start_now(saved["id"])
            if (len(saved["turns"]) // 2) % 5 == 0:
                store.snapshot_story(saved["id"], "every 5 turns")
            asyncio.create_task(_events_after_turn(saved["id"], len(saved["turns"]) - 1))
            asyncio.create_task(_find_new_chars(saved["id"]))
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
                             "address": c.get("address", ""), "tags": c.get("tags", ""), "group": c.get("group", ""), "moods": moods})
    meta["intro"] = []
    for im in s.get("intro_images", []) if pictures != "none" else []:
        f = _portrait_file(im["url"])
        if f:
            arc = f"intro/{_slug(im['name'])}{f.suffix}"
            meta["intro"].append({"name": im["name"], "file": arc})
            files.append((arc, f))
    meta["places"] = []
    for pl in s.get("places", []):
        item = {"name": pl["name"], "description": pl.get("description", "")}
        f = _portrait_file(pl["url"]) if pl.get("url") and pictures != "none" else None
        if f:
            item["file"] = f"places/{_slug(pl['name'])}{f.suffix}"
            files.append((item["file"], f))
        meta["places"].append(item)
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
                          "author": bool(c.get("author")), "address": str(c.get("address", ""))[:600], "tags": str(c.get("tags", ""))[:1500], "group": c.get("group", ""), "events": [], "moods": moods, "alts": {}})
    for im in meta.get("intro", []):
        fname = f"{_slug(im['name'])}{Path(im['file']).suffix}"
        if save_file(im["file"], store.PORTRAITS / sid / "intro" / fname):
            s.setdefault("intro_images", []).append({"name": im["name"], "url": f"/portraits/{sid}/intro/{fname}"})
    for pl in meta.get("places", []) if isinstance(meta.get("places"), list) else []:
        if not isinstance(pl, dict) or not str(pl.get("name", "")).strip():
            continue
        name = " ".join(str(pl["name"]).split())[:60]
        item = {"name": name, "description": str(pl.get("description", "")).strip()[:500], "url": ""}
        if pl.get("file"):
            fname = f"{_slug(name)}{Path(str(pl['file'])).suffix}"
            if save_file(str(pl["file"]), store.PORTRAITS / sid / "places" / fname):
                item["url"] = f"/portraits/{sid}/places/{fname}"
        s.setdefault("places", []).append(item)
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


class LibCandIn(BaseModel):
    sheet: dict[str, str] = {}
    story_id: str | None = None
    count: int = 4
    fresh: bool = True


@app.post("/api/avatars/{aid}/candidates")
async def library_avatar_candidates(aid: str, body: LibCandIn):
    """Draw several candidate pictures (default 4) for a library avatar to choose from."""
    _avatar(aid)
    cfg = store.load_config()
    s = _story(body.story_id) if body.story_id else {"id": None, "title": "", "base_prompt": "", "models": {}, "cast": [], "art": {**store.STORY_DEFAULTS["art"]}}
    return _avatar_candidates_job("avatar", aid, _avatar_folder(aid) / "_candidates", f"/portraits/_avatars/{aid}/_candidates", s, cfg, _clean_sheet(body.sheet), body.count, body.fresh)


class LibUseIn(BaseModel):
    url: str = ""                    # the chosen option from .../candidates


@app.post("/api/avatars/{aid}/use")
def use_library_candidate(aid: str, body: LibUseIn | None = None):
    _avatar(aid)
    url = (body.url if body else "").split("?")[0]
    if not url.startswith(f"/portraits/_avatars/{aid}/_candidates/"):
        raise HTTPException(404, "That is not one of this avatar's options")
    cand = _portrait_file(url)
    if not cand:
        raise HTTPException(404, "That option is gone. Draw new ones.")
    a = _set_avatar_image(aid, cand.read_bytes(), cand.suffix.lstrip("."))
    cand.unlink(missing_ok=True)
    shutil.rmtree(_avatar_folder(aid) / "_candidates", ignore_errors=True)
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
    narration, _ = prompting.split_state(prompting.expand_tags(raw))
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
        narration, _ = prompting.split_state(prompting.expand_tags(raw))
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
    narration, upd = prompting.split_state(prompting.expand_tags(body.raw))
    _add_new_chars(story, prompting.extract_newchars(narration))
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
    _start_auto_backdrop(saved["id"])
    _start_now(saved["id"])
    asyncio.create_task(_events_after_turn(saved["id"], len(saved["turns"]) - 1))
    asyncio.create_task(_find_new_chars(saved["id"]))
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
                summary = (await providers.complete(prov, model, prompting.summary_system(), [{"role": "user", "content": f"EXISTING SUMMARY:\n{summary or '(none yet)'}\n\nNEW EVENTS:\n{part}\n\nWrite the updated summary."}],
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


def _local_only(request: Request, what: str = "Backups can only be downloaded or restored") -> None:
    if (request.client.host if request.client else "") not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, f"{what} on the computer the app runs on.")


@app.post("/api/quit")
async def quit_app(request: Request):
    """Close the app from its own screen (the same as the Stop launcher). Stories are saved as they go, so nothing is lost.
    When the app manages local models it also frees their memory first."""
    _local_only(request, "The app can only be closed")
    cfg = store.load_config()
    freed = None
    if cfg.get("rt_text") or cfg.get("rt_comfy"):
        try:
            freed = await localrt.free_all()
        except Exception:
            freed = None
    asyncio.get_running_loop().call_later(0.6, lambda: os.kill(os.getpid(), signal.SIGTERM))   # (on Windows this ends the process at once; stories are already saved)   # after this reply has been sent
    return {"ok": True, "freed": freed}


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
def _scenery_direction(direction: str) -> str:
    """The story's art style without the parts that are about people: a style written for character pictures ("portrait photography, natural skin texture")
    would otherwise turn a backdrop into a portrait, especially on models like FLUX that do not use a negative prompt."""
    d = re.sub(r"\bportraits?\b\s*(photography)?", lambda m: "photography" if m.group(1) else "", direction or "", flags=re.I)
    parts = [x.strip() for x in d.split(",")]
    return ", ".join(x for x in parts if x and not re.search(r"\b(skin|face|faces|facial|headshot|close-?up|selfie|people|person|human|eyes)\b", x, re.I))


def _backdrop_prompt(art: dict, text: str, tags: bool) -> str:
    """The prompt for an environment-only picture (backdrops, places, locations): scenery style + the description + an explicit 'nobody in it'."""
    direction = _scenery_direction(art.get("direction") or "")
    if tags:
        return ", ".join(x for x in (direction, text, "no humans, scenery, wide shot, detailed background") if x)
    return "\n\n".join(x for x in (images.plain(direction), text, "An empty scene with no people, no characters and no faces. Wide establishing shot.", images.SAFE_LINE) if x)


BACKDROP_SYSTEM = ("You write one prompt for an image generator: a wide BACKGROUND picture of a place, to sit behind a story's text. Show the environment only: "
                   "no people, no characters, no faces, no text, no logos. Atmospheric, with depth, a calm composition that works when darkened. Safe for work. ")


class BackdropIn(BaseModel):
    source: str = "setting"     # "setting" = the story's world, "scene" = where the latest reply takes place


@app.post("/api/stories/{sid}/backdrop/generate")
async def generate_backdrop(sid: str, body: BackdropIn):
    """Draw a static backdrop from the story's setting or its latest scene. Shown on the sides of the screen if the player turns that on."""
    s, cfg = _story(sid), store.load_config()
    prov = _bg_provider(cfg, s)
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
    prompt = _backdrop_prompt(art, text, tags)
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


# ---------------- places: static pictures of rooms and locations the story returns to often ----------------
PLACE_SYSTEM = ("You write one prompt for an image generator: a wide establishing picture of ONE specific place in a story, used again and again whenever the story "
                "returns there. Show the environment only: no people, no characters, no faces, no text, no logos. Distinctive, with depth and believable detail "
                "from the place's description. Safe for work. ")


def _place_key(name: str) -> str:
    return " ".join((name or "").lower().split())


@app.get("/api/stories/{sid}/places")
def get_places(sid: str):
    return {"places": _story(sid).get("places", [])}


class PlaceIn(BaseModel):
    name: str
    description: str = ""


class PlacesIn(BaseModel):
    places: list[PlaceIn]


@app.put("/api/stories/{sid}/places")
def update_places(sid: str, body: PlacesIn):
    """Replace the list of places (names and descriptions). A place that keeps its name keeps its picture; removed places lose theirs."""
    s = _story(sid)
    old = {_place_key(p["name"]): p for p in s.get("places", [])}
    new, seen = [], set()
    for p in body.places:
        name = " ".join(p.name.split())[:60]
        k = _place_key(name)
        if not name or k in seen:
            continue
        seen.add(k)
        new.append({"name": name, "description": p.description.strip()[:500], "url": (old.get(k) or {}).get("url", "")})
    for k, p in old.items():
        if k not in seen:
            for f in (store.PORTRAITS / sid / "places").glob(f"{_slug(p['name'])}.*"):
                f.unlink(missing_ok=True)
    s["places"] = new
    store.save_story(s)
    return s


class PlaceCandIn(BaseModel):
    name: str
    count: int = 4
    fresh: bool = True


@app.post("/api/stories/{sid}/places/candidates")
async def place_candidates(sid: str, body: PlaceCandIn):
    """Draw several candidate pictures (default 4) of a place from its description (environment only). Nothing changes until .../places/choose."""
    s, cfg = _story(sid), store.load_config()
    place = next((p for p in s.get("places", []) if _place_key(p["name"]) == _place_key(body.name)), None)
    if not place:
        raise HTTPException(404, "Unknown place. Save the place first.")
    if not (place.get("description") or "").strip():
        raise HTTPException(400, "Describe the place first (what it looks like).")
    prov = _bg_provider(cfg, s)
    count = max(1, min(int(body.count), 8))
    job = _new_job("place", sid, count)
    job["result"] = {"candidates": [], "name": place["name"]}
    folder = store.PORTRAITS / sid / "_place_candidates" / _slug(place["name"])

    async def work(j: dict) -> None:
        st = _story(sid)
        j["current"] = "Writing the picture prompt…"
        art = await _art_with_setting(st, cfg)
        rprov, rmodel = _role(cfg, st, "recaps", "place picture")
        tags = _wants_tags(prov)
        system = PLACE_SYSTEM + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags starting with: no humans, scenery, ... then the place, its notable features, "
                                 "lighting, mood, framing (wide shot)." if tags else "Reply with 2 or 3 descriptive sentences. Output only the prompt.")
        basis = f"Story: {st['title']}\nThe story's world: {art.get('setting') or st['base_prompt'][:1200]}\n\nThe place: {place['name']}. {place['description']}"
        text = (await providers.complete(rprov, rmodel, system, [{"role": "user", "content": basis}], temperature=0.7, max_tokens=300,
                                         num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')
        prompt = _backdrop_prompt(art, text, tags)
        negative = ", ".join(x for x in (art.get("negative", ""), "person, people, character, face, text, watermark") if x)
        if body.fresh:
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            j["current"] = f"Drawing option {i + 1} of {count}…"
            try:
                data, ext = await images.generate(prov, prompt, negative, "landscape")
            except images.ImageError as e:
                raise HTTPException(502, str(e))
            f = folder / f"cand-{int(time.time() * 1000)}-{i}.{ext}"
            f.write_bytes(data)
            j["result"]["candidates"].append(f"/portraits/{sid}/_place_candidates/{_slug(place['name'])}/{f.name}")
            j["done"] = i + 1

    asyncio.create_task(_run_job(job, work))
    return {"job": job["id"]}


class PlaceChooseIn(BaseModel):
    name: str
    url: str


@app.post("/api/stories/{sid}/places/choose")
def choose_place_picture(sid: str, body: PlaceChooseIn):
    """Use one of the drawn options as this place's picture and discard the rest."""
    s = _story(sid)
    place = next((p for p in s.get("places", []) if _place_key(p["name"]) == _place_key(body.name)), None)
    if not place:
        raise HTTPException(404, "Unknown place")
    key = _slug(place["name"])
    if not body.url.split("?")[0].startswith(f"/portraits/{sid}/_place_candidates/{key}/"):
        raise HTTPException(404, "That is not one of this place's options")
    f = _portrait_file(body.url)
    if not f:
        raise HTTPException(404, "That option is gone. Draw new ones.")
    data = f.read_bytes()
    ext = images.sniff(data)[0]
    folder = store.PORTRAITS / sid / "places"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{key}.*"):
        old.unlink(missing_ok=True)
    (folder / f"{key}.{ext}").write_bytes(data)
    place["url"] = f"/portraits/{sid}/places/{key}.{ext}?v={int(time.time())}"
    shutil.rmtree(store.PORTRAITS / sid / "_place_candidates" / key, ignore_errors=True)
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/places/{key}/picture")
async def upload_place_picture(sid: str, key: str, request: Request):
    """Use your own picture for a place (raw image body)."""
    s = _story(sid)
    place = next((p for p in s.get("places", []) if _place_key(p["name"]) == _place_key(key)), None)
    if not place:
        raise HTTPException(404, "Unknown place")
    ext = IMAGE_TYPES.get(request.headers.get("content-type", "").split(";")[0].strip())
    data = await request.body()
    if not ext or not data or len(data) > MAX_IMAGE:
        raise HTTPException(415, "Use a PNG, JPEG, WebP or GIF image under 8 MB")
    folder = store.PORTRAITS / sid / "places"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob(f"{_slug(place['name'])}.*"):
        old.unlink(missing_ok=True)
    (folder / f"{_slug(place['name'])}.{ext}").write_bytes(data)
    place["url"] = f"/portraits/{sid}/places/{_slug(place['name'])}.{ext}?v={int(time.time())}"
    store.save_story(s)
    return s


# ---------------- automatic backdrop: the helper model says where the scene is, and the screen background follows ----------------
_bg_tasks: dict[str, asyncio.Task] = {}

AUTO_BG_SYSTEM = ("You pick the background picture for a story scene. Reply with ONLY JSON: "
                  '{"where": "...", "new": false, "look": "..."}. "where" is the name of the location where the LATEST reply takes place. If it is one of the '
                  'known locations, give that name exactly as listed and "new": false. If it is a different place, give a short name (2 to 4 words, for example "Mess hall '
                  'at night") and "new": true, and "look": one sentence describing that place visually (the environment only, no people, no names). If the characters '
                  "stay where they were, or you cannot tell, reply with the current location's name and \"new\": false. This is a fiction-writing tool; just report the place.")
AUTO_BG_MOMENT = (' Also add "moment" and "moment_look". "moment" is "" almost always. Only when the latest reply is a visually dramatic moment that changes how that place '
                  'looks (an alarm, fire, power failure, launch, battle, storm, a surge of light or energy), give a short label of 1 to 3 words (for example "alarm"), and '
                  '"moment_look": one sentence on how the place looks during it (lighting, atmosphere, damage; the environment only, no people). When that moment has ended, '
                  'or nothing dramatic is happening, "moment" is "".')


_MOVE_CUES = re.compile(r"\b(walk|walks|walked|head|heads|headed|heading|enter|enters|entered|arriv\w*|step|steps|stepped|leave|leaves|left|leaving|go|goes|went|going|run|runs|ran|"
                        r"dash\w*|follow\w*|door|doors|corridor|hall|hallway|elevator|lift|outside|inside|exit\w*|cross\w*|climb\w*|descend\w*|ride|rode|drive|drove|"
                        r"fly|flew|land\w*|board\w*|return\w*|later|morning|evening|night|dawn|dusk|next day|hours? later|meanwhile)\b", re.IGNORECASE)
_DRAMA_CUES = re.compile(r"\b(alarm\w*|siren\w*|klaxon|fire|flames?|smoke|explo\w*|blast\w*|power|blackout|dark(ness)?|launch\w*|storm|attack\w*|battle|surge|crash\w*|alert|breach\w*|emergency|"
                         r"collaps\w*|shatter\w*|sparks?|flash\w*|glow\w*|energy|quake|tremor)\b", re.IGNORECASE)


def _bg_quiet(text: str, known: list[str], cur_base: str, has_current: bool, dynamic: bool) -> bool:
    """Free check before the background helper: True when the reply names no other known location, shows no sign of moving or of a new time of day, and (with
    dramatic-moment backgrounds on) no sign of drama, so the scene is surely still where it was. Anything unclear goes to the helper."""
    if not (has_current and cur_base):
        return False
    low = _clean_for_scene(text)
    for name in known:
        if _bg_key(name) != _bg_key(cur_base) and re.search(rf"\b{re.escape(name)}\b", low, re.IGNORECASE):
            return False
    if _MOVE_CUES.search(low):
        return False
    return not (dynamic and _DRAMA_CUES.search(low))


def _bg_key(name: str) -> str:
    return " ".join((name or "").lower().split())


def _set_backdrop(sid: str, url: str, name: str, source: str, base: str = "", moment: str = "", cache: dict | None = None) -> None:
    s = _story(sid)
    s["backdrop"] = {"url": url, "name": name, "source": source, "base": base or name, "moment": moment}
    if cache:
        s.setdefault("backdrops", {})[_bg_key(name)] = cache
    store.save_story(s)


async def _auto_backdrop(sid: str) -> None:
    """After a reply (opt-in art.auto_backdrop, and only while the screen backdrop is shown): the helper model reads the reply and says where it takes place.
    A fixed Places picture or an earlier drawn backdrop for that location is used as it is; a new location gets a fresh one from the backdrop maker. With
    art.dynamic_backdrop, a dramatic moment (alarm, fire, battle...) gets its own variant of the place, and the plain picture returns afterwards.
    Failures are silent: it is decoration, not part of the turn."""
    try:
        s, cfg = _story(sid), store.load_config()
        art = s.get("art") or {}
        if not (art.get("auto_backdrop") and cfg.get("ui_backdrop")):
            return
        dynamic = bool(art.get("dynamic_backdrop"))
        last = next((prompting.split_scene(prompting.split_state(t["text"])[0])[0] for t in reversed(s["turns"]) if t["role"] == "assistant"), "")
        if not last.strip():
            return
        places = {_bg_key(p["name"]): p for p in s.get("places", []) if p.get("url")}
        cache = s.get("backdrops") or {}
        known = sorted({p["name"] for p in places.values()} | {v.get("base") or v["name"] for v in cache.values()})
        cur = s.get("backdrop") or {}
        cur_base, cur_moment = cur.get("base") or cur.get("name", ""), cur.get("moment", "")
        if _bg_quiet(last, known, cur_base, bool(cur.get("url")), dynamic):
            return                     # nothing in the reply suggests a move or a dramatic moment: no model call
        prov, model = _role(cfg, s, "small", "background")
        ask = ("Known locations: " + (", ".join(known) or "none") + f"\nCurrent location: {cur_base or 'none yet'}"
               + (f" (currently showing the moment: {cur_moment})" if cur_moment else "") + "\n\nLATEST REPLY:\n" + _clean_for_scene(last)[-3500:])
        out = await providers.complete(prov, model, AUTO_BG_SYSTEM + (AUTO_BG_MOMENT if dynamic else ""), [{"role": "user", "content": ask}], temperature=0.2, max_tokens=350,
                                       num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        j = _json_in(out)
        if not isinstance(j, dict):
            return
        where = " ".join(str(j.get("where", "")).split())[:60] or cur_base
        moment = " ".join(str(j.get("moment", "")).split())[:30] if dynamic else ""
        moment_look = str(j.get("moment_look", "")).strip()[:300]
        if not where:
            return
        k = _bg_key(where)
        # the plain picture of this location (a Places picture, or one drawn earlier)
        if k in places:
            base_name, base_url, base_look = places[k]["name"], places[k]["url"], places[k].get("description", "")
        elif k in cache and not cache[k].get("moment"):
            base_name, base_url, base_look = cache[k]["name"], cache[k]["url"], cache[k].get("look", "")
        elif j.get("new") and str(j.get("look", "")).strip():
            base_look = str(j["look"]).strip()[:400]
            if k == _bg_key(cur_base) and cur.get("url") and not cur_moment:
                return
            await _draw_location_backdrop(sid, cfg, where, base_look, base=where, show=not moment)
            base_name, base_url = where, (_story(sid).get("backdrops") or {}).get(k, {}).get("url", "")
            if not base_url:
                return
        else:
            return
        if moment and moment_look:
            vname = f"{base_name} — {moment}"
            vk = _bg_key(vname)
            if _bg_key(cur.get("name", "")) == vk:
                return
            hit = (_story(sid).get("backdrops") or {}).get(vk)
            if hit:
                _set_backdrop(sid, hit["url"], hit["name"], "auto", base=base_name, moment=moment)
            else:
                await _draw_location_backdrop(sid, cfg, vname, f"{base_look} During this moment: {moment_look}", base=base_name, moment=moment)
        elif not (_bg_key(cur_base) == _bg_key(base_name) and not cur_moment and cur.get("url")):
            _set_backdrop(sid, base_url, base_name, "place" if k in places else "auto", base=base_name)
    except Exception:
        pass


async def _draw_location_backdrop(sid: str, cfg: dict, name: str, look: str, base: str = "", moment: str = "", show: bool = True) -> None:
    """Draw a backdrop for a location (or a dramatic-moment variant of one) with the backdrop maker, keep it for the next visit, and (show) make it the background."""
    s = _story(sid)
    prov = _bg_provider(cfg, s)
    art = await _art_with_setting(s, cfg)
    rprov, rmodel = _role(cfg, s, "recaps", "background picture")
    tags = _wants_tags(prov)
    system = BACKDROP_SYSTEM + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags starting with: no humans, scenery, ... then place, time of day, lighting, mood, framing (wide shot)."
                                if tags else "Reply with 2 or 3 descriptive sentences. Output only the prompt.")
    basis = f"Story: {s['title']}\nThe story's world: {art.get('setting') or s['base_prompt'][:1200]}\n\nThe place: {name}. {look}"
    text = (await providers.complete(rprov, rmodel, system, [{"role": "user", "content": basis}], temperature=0.7, max_tokens=300,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')
    prompt = _backdrop_prompt(art, text, tags)
    negative = ", ".join(x for x in (art.get("negative", ""), "person, people, character, face, text, watermark") if x)
    async with _image_lock:
        data, ext = await images.generate(prov, prompt, negative, "landscape")
    folder = store.PORTRAITS / sid / "backdrops"
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{_slug(name)}.{ext}"
    f.write_bytes(data)
    url = f"/portraits/{sid}/backdrops/{f.name}?v={int(time.time())}"
    entry = {"name": name, "url": url, "look": look[:400], "base": base or name, "moment": moment}
    if show:
        _set_backdrop(sid, url, name, "auto", base=base or name, moment=moment, cache=entry)
    else:                                   # drawn for later (the moment variant is shown instead): just keep it
        st = _story(sid)
        st.setdefault("backdrops", {})[_bg_key(name)] = entry
        store.save_story(st)


def _start_auto_backdrop(sid: str) -> None:
    t = _bg_tasks.get(sid)
    if t and not t.done():
        return     # still working on the last one; the next reply tries again
    _bg_tasks[sid] = asyncio.create_task(_auto_backdrop(sid))


@app.get("/api/stories/{sid}/backdrop")
async def get_backdrop(sid: str, wait: bool = False):
    """The story's current backdrop. wait=true lets a background "where is the scene" job finish first (up to about a minute and a half)."""
    t = _bg_tasks.get(sid)
    if wait and t and not t.done():
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=90)
        except Exception:
            pass
    return {"backdrop": _story(sid).get("backdrop") or {}, "busy": bool(t and not t.done())}


# ---------------- "right now" card: who the player is talking to, their mood and what they are wearing ----------------
_now_tasks: dict[str, asyncio.Task] = {}
_PORTRAIT_TAG = re.compile(r"!\[\]\(portrait:([^)/]+)/([^)]+)\)")

NOW_SYSTEM = ("You keep a small 'right now' card for a story's side panel. Reply with ONLY JSON: "
              '{"name": "...", "mood": "...", "doing": "...", "wearing": "..."}. "name": the one character the player character is mainly interacting with at the end of the LATEST '
              "reply (never the player character), exactly as written in the known list; \"\" if nobody. \"mood\": that character's feeling right now in 1 to 2 lowercase words "
              "(use the hint if one is given and it fits). \"doing\": what that character is physically doing at the end of the reply, as a short phrase about their pose, hands or gesture (for example \"hiding one hand behind her back\" or \"leaning on the table, arms crossed\"). \"wearing\": what that character is wearing now, in a short phrase (for example \"grey cadet uniform, jacket open\"), "
              "taken from the text; if the text does not mention their clothing, keep the previous clothing if it was for the same character, otherwise give the most "
              "plausible outfit from the story. Visual facts only. This is a fiction-writing tool; just report what the text says.")


async def _update_now(sid: str) -> None:
    """After a reply (opt-in art.now_card): work out who the player is talking to, their mood and clothing. The mood comes from the narrator's own portrait tag when
    there is one; the helper model fills in the clothing. Failures are silent: it is decoration, not part of the turn."""
    try:
        s, cfg = _story(sid), store.load_config()
        if not (s.get("art") or {}).get("now_card"):
            return
        last = next((t["text"] for t in reversed(s["turns"]) if t["role"] == "assistant"), "")
        if not last.strip():
            return
        player = prompting.player_name(s).lower()
        cast = {c["name"].lower(): c["name"] for c in s.get("cast", []) if not c.get("player") and c["name"].lower() != player}
        def canon(n: str) -> str:
            n = n.strip().lower()
            return cast.get(n) or next((v for k, v in cast.items() if k.split()[0] == n.split()[0]), "") if n else ""
        tags = [(canon(a), b.strip()) for a, b in _PORTRAIT_TAG.findall(last)]
        tags = [(n, m) for n, m in tags if n]
        hint = f"Hint from the narrator's portrait tags (the last one is the latest): {tags[-1][0]} looks {tags[-1][1]}." if tags else ""
        prev = s.get("now") or {}
        prov, model = _role(cfg, s, "small", "side panel card")
        ask = ("Known characters: " + ", ".join(sorted(cast.values())) + ("\n" + hint if hint else "")
               + (f"\nPrevious card: {prev.get('name')} wearing {prev.get('wearing')}" if prev.get("name") else "") + "\n\nLATEST REPLY:\n" + _clean_for_scene(last)[-3500:])
        out = await providers.complete(prov, model, NOW_SYSTEM, [{"role": "user", "content": ask}], temperature=0.2, max_tokens=200,
                                       num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        j = _json_in(out)
        if not isinstance(j, dict):
            return
        name = " ".join(str(j.get("name", "")).split())
        real = canon(name) or (tags[-1][0] if tags else "")
        own = [m for n, m in tags if n == real]       # the narrator's own mood tag for that character wins over the helper's guess
        mood = own[-1].lower() if own else " ".join(str(j.get("mood", "")).lower().split())[:30]
        s2 = _story(sid)
        s2["now"] = {"name": real, "mood": mood, "doing": " ".join(str(j.get("doing", "")).split())[:140] if real else "", "wearing": " ".join(str(j.get("wearing", "")).split())[:120] if real else "", "turn": len(s2["turns"])}
        store.save_story(s2)
    except Exception:
        pass


def _start_now(sid: str) -> None:
    t = _now_tasks.get(sid)
    if t and not t.done():
        return
    _now_tasks[sid] = asyncio.create_task(_update_now(sid))


@app.get("/api/stories/{sid}/now")
async def get_now(sid: str, wait: bool = False):
    """The side panel's 'right now' card. wait=true lets the background job for the latest reply finish first."""
    t = _now_tasks.get(sid)
    if wait and t and not t.done():
        try:
            await asyncio.wait_for(asyncio.shield(t), timeout=40)
        except Exception:
            pass
    return {"now": _story(sid).get("now") or {}}


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
    mode = tracker.effective_mode(story.get("sidebar") or {})
    if mode == "ai" or (mode == "player" and (not got_panel or tracker.custom_fields(story))):
        _tracker_tasks[story["id"]] = asyncio.create_task(_track_with_ai(story["id"], idx, got_panel and mode == "player"))


async def _repair_values(prov, model, cfg, s: dict, bad: list) -> dict:
    """Values the AI wrote that don't fit their field (e.g. words in a number tracker): ask once more for a corrected value. Returns a patch."""
    fields = {f["id"]: f for f in tracker.active_fields(s)}
    items = "\n".join(f"{i}. {fid} ({fields[fid]['label']}, {fields[fid]['kind']}"
                      + (f", one of {'/'.join(fields[fid]['options'])}" if fields[fid].get("options") else "")
                      + (f", {fields[fid].get('min', 0)} to {fields[fid].get('max', 100)}" if fields[fid]["kind"] == "slider" else "")
                      + ")" + (f" for {n}" if n else "") + f": got {json.dumps(v, ensure_ascii=False)}" for i, (fid, n, v) in enumerate(bad, 1))
    recent = "\n\n".join(_for_tracker(t["text"], 3500) for t in s["turns"][-2:])
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
        prov, model = _role(cfg, s, "small", "side panel")
        move = s["turns"][idx - 1]["text"] if idx >= 1 and s["turns"][idx - 1]["role"] == "user" else ""
        reply = _for_tracker(s["turns"][idx]["text"], 12000)
        free, fields = tracker.read_labeled(fields, reply)     # values the narrator already prints as "Label: value" need no AI call
        patch = {}
        if fields:
            user = (f"TRACKED FIELDS:\n{tracker.field_lines(fields)}\n\nCURRENT PANEL:\n{json.dumps(tracker.visible(s), ensure_ascii=False)}\n\n"
                    f"PLAYER'S MOVE:\n{move[:800]}\n\nNARRATOR'S REPLY:\n{reply}")
            out = await providers.complete(prov, model, tracker.EXTRACT_SYSTEM, [{"role": "user", "content": user}], temperature=0.1, max_tokens=900,
                                           num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
            patch = _json_in(out)
        patch = patch if isinstance(patch, dict) else {}
        for sec in ("scene", "me"):
            if free.get(sec):
                patch[sec] = {**(patch.get(sec) or {}), **free[sec]}
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
    recent = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + _for_tracker(t["text"], 3500) for t in s["turns"][-6:])
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
async def build_tracker(sid: str, fresh: bool = False):
    """Fill the side panel from the story so far (the memory summary and the latest replies). Uses the AI when the story has no status panel.
    fresh=true first forgets the values of the story's own trackers and reads them again (use it to correct a panel that went wrong)."""
    s, cfg = _story(sid), store.load_config()
    store.snapshot_story(sid, "before side panel build")
    tracker.rebuild(s)
    if fresh:
        tracker.clear_custom_values(s)
    if tracker.effective_mode(s["sidebar"]) != "prompt" and not any(s["tracker"][k] for k in ("scene", "me", "chars")):
        fields = tracker.active_fields(s)
        prov, model = _role(cfg, s, "recaps", "side panel")
        recent = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + _for_tracker(t["text"], 3500) for t in s["turns"][-6:])
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
    for role in ("story", "recaps", "small"):
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
    description: str | None = None
    title: str | None = None            # rename the story (the saved files keep their id, so nothing else moves)


@app.put("/api/stories/{sid}/home")
def set_home_card(sid: str, body: HomeIn):
    s = _story(sid)
    if body.description is not None:
        s["description"] = body.description.strip()
    if body.title is not None:
        title = " ".join(body.title.split())[:120]
        if not title:
            raise HTTPException(400, "A story needs a title.")
        s["title"] = title
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


async def _cover_text(s: dict, cfg: dict, prov: dict) -> str:
    """The picture description the text model writes for the story's cover (tags or sentences, to suit the picture maker)."""
    tprov_model = _role(cfg, s, "recaps", "cover art")
    tags = _wants_tags(prov)
    system = ("You write one prompt for an image generator: key art for the cover of an interactive story. Show the setting and the main "
              "character or characters in a striking, cinematic composition. No text, letters, logos or UI. Keep it safe for work: no nudity or explicit content. "
              + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags (subject count first, hair, eyes, clothing, pose, setting, lighting, framing). No names, no sentences."
                 if tags else "Reply with 2 or 3 descriptive sentences. No names of real people.") + " Output only the prompt.")
    basis = (s.get("description") or "") + "\n\n" + s["base_prompt"][:5000] + ("\n\nCharacters:\n" + "\n".join(f"{c['name']}: {c['appearance']}" for c in s["cast"][:6] if c.get("appearance")) if s.get("cast") else "")
    return (await providers.complete(tprov_model[0], tprov_model[1], system, [{"role": "user", "content": basis}], temperature=0.8, max_tokens=300,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')


def _cover_prompt(s: dict, prov: dict, text: str) -> str:
    """The full prompt sent to the picture maker: the story's art style, plus the (editable) cover description."""
    art = s["art"]
    if _wants_tags(prov):
        return ", ".join(x for x in (images.plain(art.get("direction") or "").strip(), "masterpiece, best quality, cover art, wide shot", text) if x)
    return "\n\n".join(x for x in (images.plain(art.get("direction") or "").strip(), text, images.SAFE_LINE) if x)


@app.get("/api/stories/{sid}/cover/prompt")
def get_cover_prompt(sid: str):
    """The last cover picture description (written by the text model or edited by the player), or ''."""
    return {"prompt": _story(sid).get("cover_prompt", "")}


@app.post("/api/stories/{sid}/cover/describe")
async def describe_cover(sid: str):
    """Have the text model write the cover's picture description from the story prompt, so the player can read and edit it before anything is drawn."""
    s, cfg = _story(sid), store.load_config()
    text = await _cover_text(s, cfg, _img_provider(cfg, None, s))
    s = _story(sid)
    s["cover_prompt"] = text
    store.save_story(s)
    return {"prompt": text}


class CoverCandIn(BaseModel):
    prompt: str = ""                 # the cover description (as edited by the player); empty = have the text model write one first
    count: int = 4
    fresh: bool = True               # false = add to the options already drawn


@app.post("/api/stories/{sid}/cover/candidates")
async def cover_candidates(sid: str, body: CoverCandIn):
    """Draw several candidate cover pictures (default 4) from the editable description. Nothing changes on the card until .../cover/choose."""
    s, cfg = _story(sid), store.load_config()
    prov = _img_provider(cfg, None, s)
    count, text = max(1, min(int(body.count), 8)), body.prompt.strip()
    job = _new_job("cover", sid, count)
    job["result"] = {"candidates": [], "prompt": text}
    folder = store.PORTRAITS / sid / "_cover_candidates"

    async def work(j: dict) -> None:
        st = _story(sid)
        txt = text
        if not txt:
            j["current"] = "Writing the picture description…"
            txt = await _cover_text(st, cfg, prov)
        st["cover_prompt"] = txt
        store.save_story(st)
        j["result"]["prompt"] = txt
        prompt, negative = _cover_prompt(st, prov, txt), st["art"].get("negative", "")
        if body.fresh:
            shutil.rmtree(folder, ignore_errors=True)
        folder.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            j["current"] = f"Drawing option {i + 1} of {count}…"
            try:
                data, ext = await images.generate(prov, prompt, negative, "landscape")
            except images.ImageError as e:
                raise HTTPException(502, str(e))
            f = folder / f"cand-{int(time.time() * 1000)}-{i}.{ext}"
            f.write_bytes(data)
            j["result"]["candidates"].append(f"/portraits/{sid}/_cover_candidates/{f.name}")
            j["done"] = i + 1

    asyncio.create_task(_run_job(job, work))
    return {"job": job["id"]}


class CoverChooseIn(BaseModel):
    url: str


@app.post("/api/stories/{sid}/cover/choose")
def choose_cover(sid: str, body: CoverChooseIn):
    """Use one of the drawn options as the story's Home card picture and discard the rest."""
    s = _story(sid)
    if not body.url.split("?")[0].startswith(f"/portraits/{sid}/_cover_candidates/"):
        raise HTTPException(404, "That is not one of this story's cover options")
    f = _portrait_file(body.url)
    if not f:
        raise HTTPException(404, "That option is gone. Draw new ones.")
    data = f.read_bytes()
    _save_cover(s, data, images.sniff(data)[0])
    shutil.rmtree(store.PORTRAITS / sid / "_cover_candidates", ignore_errors=True)
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
            summary = (await providers.complete(prov, model, prompting.summary_system(), [{"role": "user", "content": f"EXISTING SUMMARY:\n{summary or '(none yet)'}\n\nNEW EVENTS:\n{part}\n\nWrite the updated summary."}],
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
