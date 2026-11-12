"""AI Storyteller: local FastAPI server. Run with ./run.sh"""
import asyncio
import contextvars
import hashlib
import os
import io
import zipfile
import json
import zlib
import re
import shutil
import subprocess
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

from . import appstatus, backup, localfix, beats, bugreport, characters, clock, images, lore, rules, importer, localrt, portrait_import, prompting, providers, store, tracker, usage

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

APP_DIR = Path(__file__).resolve().parent


def _code_fp(folder: Path, pattern: str) -> str:
    """A fingerprint of the app's own files (names, sizes, change times), to notice when the files on disk are newer than what is running."""
    h = hashlib.sha1()
    for f in sorted(folder.glob(pattern)):
        try:
            st = f.stat()
            h.update(f"{f.name}:{st.st_size}:{int(st.st_mtime)}".encode())
        except OSError:
            continue
    return h.hexdigest()[:12]


RUNNING_FP = _code_fp(APP_DIR, "*.py")      # the server code this process loaded


@app.get("/api/code-status")
def code_status():
    """restart: the server's code changed on disk since it started (a restart loads it). page: a fingerprint of the page's files (the page compares it with
    the one it loaded with, and offers a reload when they differ)."""
    return {"restart": _code_fp(APP_DIR, "*.py") != RUNNING_FP, "page": _code_fp(APP_DIR.parent / "static", "*")}


@app.exception_handler(providers.ProviderError)
async def _provider_error(_request: Request, exc: providers.ProviderError):
    """An AI service refused or could not be reached: say so plainly instead of a bare "Internal Server Error"."""
    return JSONResponse({"detail": str(exc)}, status_code=502)


# ---------------- optional access PIN for other devices on your network (see run-lan.sh) ----------------
def _lan_pin(new: bool = False) -> str:
    """The 6-digit PIN phones and tablets enter (data/lan_pin, made on first use)."""
    import secrets
    f = store.DATA / "lan_pin"
    if new or not (f.exists() and f.read_text(encoding="utf-8").strip()):
        f.write_text("".join(secrets.choice("0123456789") for _ in range(6)) + "\n", encoding="utf-8")
    return f.read_text(encoding="utf-8").strip()


PIN = os.environ.get("STORYTELLER_PIN", "") or (_lan_pin() if store.load_config().get("lan_access") else "")
_pin_fail: dict[str, int] = {}
LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><title>AI Storyteller</title>
<body style="background:#12111c;color:#e8e5f2;font:18px system-ui;display:grid;place-items:center;height:100vh;margin:0"><form method=post action=/pin-login style="display:grid;gap:12px;width:min(320px,90vw)">
<b>AI Storyteller</b><input name=pin type=password inputmode=numeric placeholder="PIN" autofocus style="font-size:20px;padding:10px;border-radius:8px;border:1px solid #444;background:#1b1a2b;color:inherit">
<button style="font-size:18px;padding:10px;border-radius:8px;background:#8b6fd6;color:#fff;border:0">Open</button><small style="color:#999">@@MSG@@</small></form>
<script>try{var t=localStorage.getItem("st_dev");if(t)fetch("/pin-login",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:"dev="+encodeURIComponent(t)}).then(function(r){if(r.ok)location.reload();else localStorage.removeItem("st_dev")})}catch(e){}</script>"""
_ENTER_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><body style="background:#12111c"><script>try{localStorage.setItem("st_dev","@@TOK@@")}catch(e){}location.replace("/")</script>"""


def _login(msg: str = "", status: int = 200) -> Response:
    return Response(LOGIN_HTML.replace("@@MSG@@", msg), media_type="text/html", status_code=status)


# Devices that entered the PIN are remembered (data/lan_devices.json, only a hash of each device's token is kept), so a restart never asks again.
# A new PIN forgets them all; Settings -> Phone & tablet lists them and can forget one.
_devs: dict | None = None


def _dev_load() -> dict:
    global _devs
    if _devs is None:
        try:
            _devs = json.loads((store.DATA / "lan_devices.json").read_text(encoding="utf-8"))
        except Exception:
            _devs = {}
    return _devs


def _dev_save() -> None:
    try:
        (store.DATA / "lan_devices.json").write_text(json.dumps(_dev_load(), indent=1), encoding="utf-8")
    except OSError:
        pass


def _dev_ok(token: str | None, host: str = "") -> bool:
    if not token or len(token) > 100:
        return False
    d = _dev_load()
    e = d.get(hashlib.sha256(token.encode()).hexdigest())
    if not e:
        return False
    now = int(time.time())
    if now - e.get("seen", 0) > 3600:       # note when it was last used (at most once an hour)
        e["seen"], e["ip"] = now, host
        _dev_save()
    return True


def _dev_name(ua: str) -> str:
    os_ = next((n for k, n in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone/tablet"), ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("Linux", "Linux PC")) if k in ua), "Device")
    br = next((n for k, n in (("Edg", "Edge"), ("OPR", "Opera"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("Safari", "Safari")) if k in ua), "browser")
    return f"{os_} ({br})"


def _dev_add(host: str, ua: str) -> str:
    import secrets
    tok = secrets.token_urlsafe(24)
    now = int(time.time())
    _dev_load()[hashlib.sha256(tok.encode()).hexdigest()] = {"name": _dev_name(ua), "added": now, "seen": now, "ip": host}
    _dev_save()
    return tok


def _dev_response(tok: str) -> Response:
    r = Response(_ENTER_HTML.replace("@@TOK@@", tok), media_type="text/html")
    r.set_cookie("st_dev", tok, max_age=60 * 60 * 24 * 3650, httponly=False, samesite="lax")     # kept for years; "Forget" in Settings removes it
    return r


def _pin_cookie() -> str:
    return hashlib.sha256(("st|" + PIN).encode()).hexdigest()[:32]


_LOCAL_NAMES = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _home_network(host: str) -> bool:
    """This computer or a device on the home network (private addresses only: 192.168.x.x, 10.x.x.x, 172.16-31.x.x, link-local)."""
    import ipaddress
    try:
        ip = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        return host == "localhost"
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip in ipaddress.ip_network(n) for n in _HOME_NETS if ipaddress.ip_network(n).version == ip.version)


_HOME_NETS = ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "::1/128", "fc00::/7", "fe80::/10")


@app.middleware("http")
async def home_network_only(request: Request, call_next):
    """Phone and tablet access is for the home network only: a request from anywhere else (the internet, if a router ever forwarded the port) is refused."""
    host = request.client.host if request.client else ""
    if host and not _home_network(host):
        return Response("Only devices on your home network can use AI Storyteller.", media_type="text/plain", status_code=403)
    return await call_next(request)


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
    """When STORYTELLER_PIN is set, other computers must enter it first (once: the device is then remembered). This computer (localhost) never needs it."""
    host = request.client.host if request.client else ""
    if not PIN or host in ("127.0.0.1", "::1", "localhost") or request.cookies.get("st_pin") == _pin_cookie() or _dev_ok(request.cookies.get("st_dev"), host):
        return await call_next(request)
    if request.url.path == "/pin-login" and request.method == "POST":
        form = (await request.body()).decode()
        dev = re.search(r"(?:^|&)dev=([^&]*)", form)
        if dev and _dev_ok(unquote_plus(dev.group(1)), host):        # a remembered device whose cookie was cleared but kept its backup copy
            return _dev_response(unquote_plus(dev.group(1)))
        if dev:
            return Response("no", status_code=401)
        if _pin_fail.get(host, 0) >= 10:
            return _login("Too many wrong tries. Restart the app to reset.", 429)
        given = re.search(r"pin=([^&]*)", form)
        if given and hashlib.sha256(("st|" + unquote_plus(given.group(1))).encode()).hexdigest()[:32] == _pin_cookie():
            return _dev_response(_dev_add(host, request.headers.get("user-agent", "")))
        _pin_fail[host] = _pin_fail.get(host, 0) + 1
        await asyncio.sleep(1)
        return _login("Wrong PIN.", 401)
    if request.url.path.startswith("/api/"):
        return Response('{"detail":"PIN required"}', media_type="application/json", status_code=401)
    return _login()


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
        "local": bool(cfg.get("local_mode")),
    }


def _local_on(cfg: dict, prov: dict | None) -> bool:
    """Local-model mode applies to Ollama models only."""
    return bool(cfg.get("local_mode")) and (prov or {}).get("type") == "ollama"


def _steady_on(cfg: dict) -> bool:
    """The code-side writing fixes (cut-off trim, format repair, variety and length notes) work with any provider, so the one switch turns them on everywhere."""
    return bool(cfg.get("local_mode"))


def _est_tokens(text: str) -> int:
    return int(len(text) / 3.4) + 1          # rough: English prose and JSON average about 3.4 characters per token


def _fit_context(system, messages: list[dict], cfg: dict) -> tuple[int, bool]:
    """Local mode: drop the oldest history until the request fits the model's window (a full window makes Ollama silently cut the START, which is the
    story prompt). Returns (messages dropped, still too big). The newest exchange is always kept."""
    room = int(cfg.get("num_ctx", 8192)) - min(int(cfg.get("max_tokens", 1500)), 1500) - 200
    base = _est_tokens(providers.system_text(system))
    dropped = 0
    while len(messages) > 2 and base + sum(_est_tokens(m["content"]) for m in messages) > room:
        del messages[:2]
        dropped += 2
        if messages and messages[0]["role"] != "user":
            del messages[0]
            dropped += 1
    return dropped, base + sum(_est_tokens(m["content"]) for m in messages) > room


def _clean_alts(items) -> list[dict]:
    out = []
    for a in items if isinstance(items, list) else []:
        if isinstance(a, dict) and str(a.get("text", "")).strip():
            out.append({"name": " ".join(str(a.get("name", "")).split())[:60] or f"Start {len(out) + 2}", "text": str(a["text"]).strip()[:12000]})
    return out[:6]


def _apply_alt_openings(s: dict, body) -> None:
    if body.alt_openings is not None:
        s["alt_openings"] = _clean_alts(body.alt_openings)
    if body.opening_name is not None:
        s["opening_name"] = " ".join(body.opening_name.split())[:60]


class OpeningChoiceIn(BaseModel):
    index: int       # position in the story's alternate openings


@app.post("/api/stories/{sid}/opening/choose")
def choose_opening(sid: str, body: OpeningChoiceIn):
    """Begin with one of the author's alternate openings instead of the main one. Only before the first move; the two simply trade places, so you can switch back."""
    s = _story(sid)
    if s["turns"]:
        raise HTTPException(400, "The story has already begun, so its opening can no longer be changed.")
    alts = s.get("alt_openings") or []
    if not 0 <= body.index < len(alts):
        raise HTTPException(400, "There is no such opening.")
    cur = {"name": s.get("opening_name") or "Main opening", "text": s.get("opening", "")}
    s["opening"], s["opening_name"] = alts[body.index]["text"], alts[body.index]["name"]
    alts[body.index] = cur
    s["alt_openings"] = alts
    store.save_story(s)
    return s


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
    rule_check: bool | None = None        # after every reply, the small helper checks it against the story's own rules
    clock: dict | None = None             # world clock settings {mode: auto|on|off, start: "06:00", minutes: 30}
    app_status: bool | None = None        # the app writes the status line itself (the narrator leaves it out)
    format_fix: bool | None = None        # the app tidies a stray status line (moves it to the end, drops a duplicate) instead of flagging it
    code_check: bool | None = None        # the app checks each reply's format and numbers itself (free); on unless switched off
    narrator_acts: bool | None = None     # the narrator may decide some of the player character's routine actions (the story prompt says how far)
    nsfw: bool | None = None              # adult content setting
    description: str | None = None
    prologue: str | None = None
    opening_options: list[str] | None = None
    commands: list[dict] | None = None    # story-defined buttons above the input box: [{label, kind: send|edit|points, text}]
    alt_openings: list[dict] | None = None   # other ways to begin: [{name, text}], chosen before the first move
    opening_name: str | None = None          # what the main opening is called in the chooser
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
    prompt_derived: bool = False        # the story prompt was written by the AI from the text (so a restart can drop its "where the story stands" part)
    provider: str | None = None         # no prompt given: the AI that writes one from the summary
    model: str | None = None


AVATAR_FIELDS = ("name", "gender", "age", "appearance", "personality", "background", "abilities")
IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
MAX_IMAGE = 8 * 1024 * 1024


class TurnIn(BaseModel):
    text: str
    provider: str | None = None
    model: str | None = None
    force_budget: bool = False      # send even though a spending limit was reached
    note: str = ""                  # regenerate with changes: sent with this request only, never stored in the story
    cmds: list[str] = []            # story commands sent along with this move: "continue", "fight", "adult" (see COMMANDS)
    cmd_opts: dict = {}             # options for them, e.g. {"act_for_me": true} for "fight"
    roll: dict | None = None        # a dice check the app made for this move (see /api/stories/{sid}/roll); sent to the narrator as the final outcome


# Story commands: instructions the player can send along with a move. They are added to that request only (never stored as text in the story); the player's turn
# remembers which were sent, so a retry sends them again.
COMMANDS = {
    "continue": ("▶ Continue",
                 "Continue the story from where it stands, without waiting for the player. Advance events naturally from the story prompt, the world and its lore, the characters' "
                 "established personalities, goals and relationships, and the most recent turns. Stay consistent with everything that has happened. Do not decide the player "
                 "character's actions, words, thoughts or choices; stop at the next natural moment where the player needs to respond. If the player's message above says more than "
                 "'Continue', treat it as a hint about what should happen next."),
    "fight": ("⚔ Continue the fight",
              "Continue the ongoing battle naturally and creatively. Take the initiative to advance the fight without waiting for specific instructions: make enemies attack, dodge, "
              "counterattack, adapt their strategies and use their abilities intelligently. Introduce unexpected moves, tactical opportunities, environmental interactions and "
              "escalating tension so the fight stays engaging and unpredictable. Respect the established characters, personalities, abilities, power levels, injuries and events, and "
              "keep continuity with everything that has happened. Avoid repetitive attacks and unnecessary narration; keep the combat fluid, immersive and exciting."),
    "adult": ("🔞 Full adult scene",
              "Adult-content instruction for this reply. Everyone involved is an adult and a willing participant. When the fiction leads to an intimate or sexual moment, write it in "
              "full, explicit detail as it happens: do not fade to black, cut away, summarise or skip over it, and let it last as long as the scene naturally would. Never sanitise "
              "the vocabulary: use the plain, explicit words the scene calls for, and mirror the player's own wording and level of explicitness. Keep the characters' established "
              "personalities, desires, boundaries and consent in character. Intimacy is character-driven and makes the world feel lived in; it is not used to solve plots or to "
              "advance plot events."),
}
FIGHT_NO_ACT = " Do NOT control the player's character: do not dictate their actions, thoughts, dialogue or decisions; leave their reactions and choices entirely to the player."
FIGHT_ACT = (" You may also act for the player's character in this fight: choose fitting actions in keeping with their abilities, personality and the established rules, "
             "but never make irreversible story decisions for them (a death, a surrender, a betrayal).")


def _command_note(story: dict, cmds: list[str], opts: dict) -> str:
    """The (OOC: ...) text for the story commands sent with a move."""
    out = []
    for c in dict.fromkeys(cmds or []):
        if c not in COMMANDS:
            continue
        if c == "adult" and not story.get("nsfw"):
            raise HTTPException(400, "Adult content is switched off for this story (Settings → Story & narration).")
        out.append(COMMANDS[c][1] + ((FIGHT_ACT if (opts or {}).get("act_for_me") else FIGHT_NO_ACT) if c == "fight" else ""))
    return "(OOC: " + " ".join(out) + ")" if out else ""


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
    for k in ("retry_pending", "undone"):
        if k not in story:
            fresh.pop(k, None)
    store.save_story(fresh)
    return fresh


def _clean_roll(r: dict | None) -> dict | None:
    """A dice result sent by the page, made safe (it goes into the narrator's prompt)."""
    if not isinstance(r, dict):
        return None
    try:
        out = {"label": " ".join(str(r.get("label", "")).split())[:30], "value": float(r.get("value", 5)), "mod": int(r.get("mod", 0)), "die": int(r["die"]),
               "rolls": [int(x) for x in (r.get("rolls") or [r["die"]])][:2], "adv": r.get("adv") if r.get("adv") in ("adv", "dis") else "", "total": int(r["total"]),
               "dc": int(r["dc"]), "difficulty": " ".join(str(r.get("difficulty", "")).split())[:20], "outcome": str(r.get("outcome", "")),
               "margin": int(r.get("margin", 0)), "auto": bool(r.get("auto"))}
    except (KeyError, TypeError, ValueError):
        return None
    if out["outcome"] not in ("critical success", "success", "partial success, at a cost", "failure", "critical failure") or not (1 <= out["die"] <= 20):
        return None
    return out


def _hidden_notes(story: dict, beats_note: str, pend_pts: list, roll: dict | None) -> str:
    """Everything the app adds invisibly to the player's move: due story beats, triggered lore, points the player spent, the dice outcome."""
    return "\n\n".join(x for x in (beats_note, lore.note(story), tracker.spent_note(story, pend_pts), rules.roll_note(roll) if roll else "") if x)


def _notification_events(s: dict, idx: int) -> bool:
    """Key events announced in the reply's own notifications (Level 3 reached, Life lost, Feat recorded...) go on the player's card, with no AI call."""
    evs = rules.key_events(s["turns"][idx]["text"])
    name = prompting.player_name(s)
    if not evs or not name:
        return False
    ch = next((c for c in s.get("cast", []) if c.get("player")), None) or next((c for c in s.get("cast", []) if c["name"].lower() == name.lower()), None)
    if ch is None:
        ch = _ensure_char(s, name, "")
    ch["player"] = True
    raw = s["turns"][idx]["text"]
    m = re.search(r"Turn:\s*(\d+)", raw)
    label = f"Turn {m.group(1)}" if m else f"Reply {(idx + 1) // 2}"
    lst, changed = ch.setdefault("events", []), False
    for e in evs:
        if any(x["text"].lower() == e.lower() for x in lst[-10:]):
            continue
        lst.append(_ev(label, e, idx, True)); changed = True
    del lst[:-MAX_EVENTS]
    return changed


def _after_reply_free(saved: dict) -> None:
    """Work the app does itself after every reply, with no AI call: the format and number check, and the key events in the reply's notifications."""
    try:
        idx = len(saved["turns"]) - 1
        changed = False
        if saved.get("code_check", True):
            issues = rules.check(saved, idx)
            if issues:
                saved["turns"][idx]["check"] = {"issues": issues, "code_issues": issues, "ts": time.time()}; changed = True
        if _notification_events(saved, idx):
            changed = True
        if changed:
            store.save_story(saved)
    except Exception:
        pass


def _beats_persist(sid: str, story: dict) -> None:
    """Keep which beats have gone out (their `fired` move) without overwriting anything else the player changed meanwhile."""
    mine = {i["id"]: i.get("fired") for i in beats.get(story)["items"]}
    fresh = _story(sid)
    fb = beats.get(fresh)
    if any(i.get("fired") != mine.get(i["id"], i.get("fired")) for i in fb["items"]):
        for i in fb["items"]:
            if i["id"] in mine:
                i["fired"] = mine[i["id"]]
        store.save_story(fresh)


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


def _clean_commands(raw) -> list[dict]:
    """A story's own command buttons, made safe: up to 12, each {label, kind, text}. "send" puts the text in as the player's move, "edit" puts it in the input box to change first,
    "points" opens the stat-points window (the app does the arithmetic, no AI call)."""
    out = []
    for c in raw if isinstance(raw, list) else []:
        if not isinstance(c, dict):
            continue
        label = " ".join(str(c.get("label") or "").split())[:24]
        kind = c.get("kind") if c.get("kind") in ("send", "edit", "points") else "send"
        text = str(c.get("text") or "").strip()[:400]
        if label and (text or kind == "points"):
            out.append({"label": label, "kind": kind, "text": text})
    return out[:12]


def _apply_creation_fields(s: dict, body: "StoryIn") -> None:
    """The story-creation extras (everything an author can premake)."""
    if body.description is not None:
        s["description"] = body.description.strip()
    if body.prologue is not None:
        s["prologue"] = body.prologue.strip()
    if body.commands is not None:
        s["commands"] = _clean_commands(body.commands)
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
    _apply_alt_openings(s, body)
    if body.opening_in_context is not None:
        s["opening_in_context"] = body.opening_in_context
    if body.auto_replies is not None:
        s["auto_replies"] = body.auto_replies
    if body.rule_check is not None:
        s["rule_check"] = body.rule_check
    if body.narrator_acts is not None:
        s["narrator_acts"] = body.narrator_acts
    if body.code_check is not None:
        s["code_check"] = body.code_check
    if body.format_fix is not None:
        s["format_fix"] = body.format_fix
    if body.app_status is not None:
        s["app_status"] = body.app_status
    if body.clock is not None:
        c = body.clock
        s["clock"] = clock.clean(c)
        if not clock.is_on(s):
            (s.get("tracker") or {}).pop("_clock", None)
    _apply_creation_fields(s, body)
    tracker.seed_starts(s)
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
    _apply_alt_openings(s, body)
    if body.opening_in_context is not None:
        s["opening_in_context"] = body.opening_in_context
    if body.auto_replies is not None:
        s["auto_replies"] = body.auto_replies
    if body.rule_check is not None:
        s["rule_check"] = body.rule_check
    if body.narrator_acts is not None:
        s["narrator_acts"] = body.narrator_acts
    if body.code_check is not None:
        s["code_check"] = body.code_check
    if body.format_fix is not None:
        s["format_fix"] = body.format_fix
    if body.app_status is not None:
        s["app_status"] = body.app_status
    if body.clock is not None:
        c = body.clock
        s["clock"] = clock.clean(c)
        if not clock.is_on(s):
            (s.get("tracker") or {}).pop("_clock", None)
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
async def import_story_route(body: ImportIn):
    """The same, from a summary. With no prompt and no story to copy it from, the AI writes one from the summary and the recent turns (provider/model pick the AI)."""
    warnings = []
    if not (body.base_prompt.strip() or body.source_story_id or body.into_story_id):
        if not (body.summary.strip() or body.recent_turns.strip()):
            raise HTTPException(400, "Give the story prompt, or a summary or some text for the AI to write one from.")
        cfg = store.load_config()
        prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, None, "recaps")
        try:
            d = await _derive_prompt(cfg, prov, model, body.summary, "", body.recent_turns[-6000:])
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        body = body.model_copy(update={"prompt_derived": True, "base_prompt": d["prompt"], "title": body.title.strip() or d["title"], "subtitle": body.subtitle.strip() or d["subtitle"],
                                       "avatar": {**body.avatar, **({"name": d["player_name"]} if d["player_name"] and not (body.avatar.get("name") or "").strip() else {})}})
        warnings.append("No story prompt was given, so the AI wrote one from your summary. Read it and fix anything wrong under ✏️ Edit story, because the narrator follows it every turn."
                        + ("" if (body.avatar.get("name") or "").strip() else " The summary does not say who the player is: set your character under 👤 Player."))
    res = import_story(body)
    res["warnings"] = warnings + res["warnings"]
    return res


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
    if body.prompt_derived:
        s["prompt_derived"] = True
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


AVATAR_BUILD_SYSTEM = ("You fill in a player's character sheet for an interactive story, from the story's own prompt, for a player who only supplied a name or a few lines. "
    "Fit the character to the world, the setting and how newcomers begin in it. Do not give them power, rank, wealth or secrets the story prompt does not allow a newcomer; "
    "follow any starting-ability or stat rules the prompt gives (and its units: Imperial). Keep what the CURRENT SHEET already says and only fill what is empty or vague. "
    "Fields: gender, age (an adult), appearance (build, hair, eyes, usual clothes, one memorable detail; 2 to 3 sentences, visual facts only, safe for work), "
    "personality (2 sentences: under pressure, wants, one flaw), background (3 to 4 lines: where from, why they are here, one hook the story can use), "
    "abilities (stats, skills and starting inventory in the same form the story prompt uses for its player sheet; modest). "
    'Reply with ONLY JSON: {"gender": "", "age": "", "appearance": "", "personality": "", "background": "", "abilities": ""}. Write it in the third person; use the name given.')


class AvatarBuildIn(BaseModel):
    avatar: dict = {}


@app.post("/api/stories/{sid}/avatar/suggest")
async def suggest_avatar(sid: str, body: AvatarBuildIn):
    """Build the player's sheet from the story prompt (one text-model call). Nothing is saved: the player reviews the boxes first."""
    s, cfg = _story(sid), store.load_config()
    keys = ("name", "gender", "age", "appearance", "personality", "background", "abilities")
    cur = {k: str((body.avatar or {}).get(k, "") or "").strip()[:1500] for k in keys}
    name = cur["name"] or s.get("player_name") or "the player"
    prompt = (s.get("base_prompt") or "").replace("{user}", name)[:24000]
    if not prompt.strip():
        raise HTTPException(400, "This story has no prompt to build a character from.")
    user = f"Player name: {name}\n\nCURRENT SHEET:\n{json.dumps(cur, ensure_ascii=False)}\n\nSTORY PROMPT:\n{prompt}"
    prov, model = _events_model(cfg, s, "player sheet")
    out = await providers.complete(prov, model, AVATAR_BUILD_SYSTEM, [{"role": "user", "content": user}], temperature=0.7, max_tokens=1500, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    if not isinstance(j, dict):
        raise HTTPException(502, "The text model did not return a character sheet. Try again.")
    lim = {"gender": 40, "age": 20, "appearance": 700, "personality": 400, "background": 900, "abilities": 900}
    fields = {k: ("\n".join(" ".join(ln.split()) for ln in str(j.get(k) or "").splitlines()).strip())[:n] for k, n in lim.items()}
    return {"fields": fields}


AVATAR_CLUES_SYSTEM = ("You help a player invent their own character for an interactive story, from the story's prompt. Do NOT write the character: give short CLUES about what fits this world, "
    "as example hints a player can read and then write their own. "
    "Reply with ONLY JSON: {\"fit\": \"one sentence: who a newcomer to this world can plausibly be\", \"hints\": {\"name\": \"name style of this world, 2 to 3 examples\", "
    "\"gender\": \"\", \"age\": \"the usual range\", \"appearance\": \"what people here look and dress like\", \"personality\": \"traits that suit the story's tone\", "
    "\"background\": \"where a newcomer could be from and why they are here\", \"abilities\": \"what a starting character can and cannot do, in the story's own terms and units\"}}. "
    "Each hint is under 120 characters, specific to THIS world, never generic. Respect any starting-ability or stat rules in the prompt.")


@app.post("/api/stories/{sid}/avatar/clues")
async def avatar_clues(sid: str):
    """Short hints for a blank player sheet, read from the story prompt (one helper call; nothing is saved)."""
    s, cfg = _story(sid), store.load_config()
    prompt = (s.get("base_prompt") or "").replace("{user}", "the player")[:20000]
    if not prompt.strip():
        raise HTTPException(400, "This story has no prompt to read clues from.")
    prov, model = _events_model(cfg, s, "avatar clues")
    out = await providers.complete(prov, model, AVATAR_CLUES_SYSTEM, [{"role": "user", "content": f"STORY: {s['title']}\n\n{prompt}"}], temperature=0.5, max_tokens=900, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    j = _json_in(out)
    if not isinstance(j, dict) or not isinstance(j.get("hints"), dict):
        raise HTTPException(502, "The helper model did not return clues. Try again.")
    hints = {k: " ".join(str(j["hints"].get(k) or "").split())[:160] for k in AVATAR_FIELDS}
    return {"fit": " ".join(str(j.get("fit") or "").split())[:240], "hints": hints}


class SetupIn(BaseModel):
    nsfw: bool | None = None
    art_direction: str | None = None
    avatar: dict | None = None        # a sheet written in the setup pop-up (empty = leave the story's own)
    clear_avatar: bool = False        # the player chose "decide later" or an empty blank sheet: the story's own starter character is removed (the plain "you")
    done: bool = True


@app.post("/api/stories/{sid}/setup")
def story_setup(sid: str, body: SetupIn):
    """The first-start pop-up of a story: art style, adult content and the player's sheet in one save, then the story counts as set up (the pop-up never returns)."""
    s = _story(sid)
    if body.nsfw is not None:
        s["nsfw"] = body.nsfw
    if body.art_direction is not None:
        s["art"] = {**(s.get("art") or {}), "direction": body.art_direction.strip()[:600]}
    if body.clear_avatar and not body.avatar:
        s["avatar"], s["player_name"], s["avatar_id"], s["avatar_image"] = {}, "", "", ""
    if body.avatar:
        sheet = {k: str(body.avatar.get(k, "")).strip()[:1500] for k in AVATAR_FIELDS if str(body.avatar.get(k, "")).strip()}
        if sheet:
            s["avatar"] = sheet
            s["player_name"] = sheet.get("name", s.get("player_name", ""))
    if body.done:
        s["setup_done"] = True
    store.save_story(s)
    return s


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
    now_pic: str = "portrait"           # the card's picture: "portrait" (their saved picture for the mood) or "live" (a new picture each turn of what they are doing and wearing)
    dynamic_backdrop: bool = False      # opt-in (with auto_backdrop): dramatic moments (alarm, fire, battle...) get their own variant of the background
    auto_backdrop: bool = False         # opt-in: the helper model changes the screen backdrop to match where the scene is
    scene_often: bool = False           # opt-in: the narrator adds a scene picture to most replies (Places pictures cover routine room-to-room moves)
    scene_display: str = "normal"       # how big scene pictures are shown in the story: "normal", "large" or "full" (the whole width)
    scene_scale: str = "normal"         # local ComfyUI only: draw scene pictures on a bigger canvas ("large" 1.25x, "xl" 1.5x)
    scene_gap: int = 1                  # fewest replies between scene pictures: 1 = no limit, 3 = at most one every third reply
    scene_where: str = "reply"          # where scene pictures show: "reply" (under the reply), "banner_top" / "banner_bottom" (a wide strip above / below the narration) or "panel" (the side panel's right-now card)
    extended_moods: bool = False        # opt-in: new characters get the extended set of mood pictures by default, and the narrator is reminded each turn (after the first) to use the full range
    now_free: bool = False              # the "right now" card is filled from the narrator's portrait tags only (who and their mood): no AI call
    scene_template: bool = False        # a scene picture's prompt is built from the reply's tags, place and text instead of asking the AI
    action_moods: bool = True           # the narrator prefers the action mood pictures in fights, chases and (adult stories) intimate scenes
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
    species: str | None = None   # "" or "human" = human; anything else is a species with its own reference picture and maker (None = keep what is saved)
    maker: str | None = None     # this character's own picture maker: "" = the story's, "flux" = FLUX.2 klein (reference pictures), "ckpt:<name>" = a ComfyUI checkpoint


class CastIn(BaseModel):
    cast: list[CastMember]


class GenPortraitIn(BaseModel):
    mood: str = "default"
    provider: str | None = None


class IllustrateIn(BaseModel):
    turn: int | None = None            # which reply (default: the latest)
    shape: str | None = None           # reframe: draw this one as "portrait", "landscape", "square" or "wide" instead of the story's usual scene shape
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
    if prov.get("type") == "comfyui" and _is_flux_model(prov.get("model", "")):
        return {**_flux_provider(cfg, s, prov["model"]), "id": prov["id"]}   # FLUX.2 chosen as the main maker: it needs the FLUX workflow, not the checkpoint one
    return prov


def _is_flux_model(name: str) -> bool:
    from . import catalog
    return name in catalog.UNETS or bool(catalog.diffusion_kind(name))


_WF_FILES = {"flux2": "comfyui-flux2-klein.json", "chroma": "comfyui-chroma.json", "qwen": "comfyui-qwen-image.json"}
_WF_WANT = {"flux2": (r"qwen[_-]?3", r"flux2.*vae|flux.?2"), "chroma": (r"t5xxl", r"^ae\.|flux.*ae|(^|[_-])ae[_.]"), "qwen": (r"qwen.*2\.?5.*vl", r"qwen.*vae")}


def _stock_workflow(model: str, plain_flux: bool = True) -> str:
    """The built-in workflow that suits this model, with the text-encoder and VAE file names ComfyUI actually has filled in:
    Chroma and Qwen-Image have their own, FLUX.2 uses the klein one, an ordinary checkpoint the plain starter. Qwen-Image Lightning gets its few-step settings."""
    from . import catalog
    kind = catalog.diffusion_kind(model) if (model in catalog.UNETS or catalog.diffusion_kind(model)) else ""
    if not kind:
        return json.dumps(images.STARTER_WORKFLOW, indent=2)
    wf = json.loads((ROOT / "workflows" / _WF_FILES[kind]).read_text(encoding="utf-8"))
    want = _WF_WANT[kind]
    if kind == "flux2" and "9b" in model.lower():
        want = (r"qwen[_-]?3.*8b", want[1])
    for n in wf.values():
        inp = n.get("inputs", {}) if isinstance(n, dict) else {}
        for cls, field, pool, pat in (("CLIPLoader", "clip_name", "clips", want[0]), ("VAELoader", "vae_name", "vaes", want[1])):
            have = catalog.FILES.get(pool) or []
            if n.get("class_type") == cls and have and (inp.get(field) not in have or not re.search(pat, inp.get(field) or "", re.IGNORECASE)):
                m = [f for f in have if re.search(pat, f, re.IGNORECASE)]
                if m:
                    inp[field] = m[0]
        if kind == "qwen" and n.get("class_type") == "KSampler" and re.search(r"light|4.?step|8.?step", model, re.IGNORECASE):
            inp["steps"] = 8 if re.search(r"8.?step", model, re.IGNORECASE) else 4
            inp["cfg"] = 1.0
    return json.dumps(wf, indent=2)


def _bg_provider(cfg: dict, s: dict | None = None) -> dict:
    """The picture maker for backgrounds and places. By default the same one as everything else; with "separate backdrop maker" on (Settings -> Pictures),
    a local ComfyUI model such as FLUX.2 klein (it uses the same ComfyUI as the picture maker below, with its own model and workflow)."""
    bg = cfg.get("image_bg") or {}
    if not bg.get("on"):
        return _img_provider(cfg, None, s)
    comfy = next((p for p in cfg.get("image_providers", []) if p.get("type") == "comfyui"), None)
    if not comfy:
        raise HTTPException(400, "The separate backdrop maker needs ComfyUI under Settings → Pictures.")
    workflow = (bg.get("workflow") or "").strip() or _stock_workflow(bg.get("model") or "flux-2-klein-4b-fp8.safetensors")   # empty = the one that suits the chosen model
    usage.set_ctx((s or {}).get("id"), "pictures")
    return {**comfy, "id": "comfyui-bg", "model": bg.get("model") or "flux-2-klein-4b-fp8.safetensors", "workflow": workflow, "workflow_ref": "",
            "prompt_style": "natural", "ref_mode": "characters", "extra_positive": "", "extra_negative": "", "model_tweaks": {}}


# ---------------- species and per-character picture makers ----------------
SPECIES_REF_MP = 0.03          # a species reference is shown to FLUX at this size (megapixels): only its broad look carries over
_model_ctx: contextvars.ContextVar = contextvars.ContextVar("pic_model", default="")


def _is_human(name: str | None) -> bool:
    return (name or "").strip().lower() in ("", "human", "humans", "person")


def _species_entry(s: dict, name: str | None) -> dict | None:
    return None if _is_human(name) else (s.get("species") or {}).get((name or "").strip().lower())


def _full_appearance(s: dict, ch: dict) -> str:
    """The character's looks plus what is true of their whole species (so every member of it is drawn with the same traits)."""
    sp = _species_entry(s, ch.get("species"))
    look = (ch.get("appearance") or "").strip()
    return f"{look} Species traits ({sp.get('name', ch.get('species'))}): {sp['description'].strip()}" if sp and (sp.get("description") or "").strip() else look


def _model_family(name: str) -> str:
    n = (name or "").lower()
    if "pony" in n:
        return "pony"
    if re.search(r"anim|illustri|noob|waifu|anything|counterfeit|meina|hassaku|danbooru|cartoon|toon", n):
        return "anime"
    return "real"


def _flux_provider(cfg: dict, s: dict | None = None, model: str | None = None) -> dict:
    """FLUX.2 klein through ComfyUI, with room for two reference pictures (it keeps a character steady and can copy a whole species' look)."""
    comfy = next((p for p in cfg.get("image_providers", []) if p.get("type") == "comfyui"), None)
    if not comfy:
        raise HTTPException(400, "A FLUX picture maker needs ComfyUI under 🖼 Art → Picture makers.")
    bg = cfg.get("image_bg") or {}
    model = model or bg.get("model") or "flux-2-klein-4b-fp8.safetensors"
    from . import catalog
    if catalog.diffusion_kind(model) in ("chroma", "qwen"):   # Chroma and Qwen-Image: their own workflows (text encoder, negative prompt); no reference pictures
        cw = _stock_workflow(model)
        lt0 = ((comfy.get("model_tweaks") or {}).get(model) or {}).get("loras") or ""
        if catalog.diffusion_kind(model) == "chroma" and re.search(r"flash|turbo|lightning|hyper", lt0, re.IGNORECASE):     # a speed LoRA (Chroma Flash) is on: it needs the few-step Heun settings (8 steps, cfg 1) it was made for; the negative prompt is not used at cfg 1
            wf = json.loads(cw)
            for n in wf.values():
                if n.get("class_type") == "KSampler":
                    n["inputs"].update(steps=8, cfg=1.0, sampler_name="heun")
            cw = json.dumps(wf, indent=2)
        return {**comfy, "id": "comfyui-flux", "model": model, "workflow": cw, "workflow_ref": "", "prompt_style": "natural", "extra_positive": "",
                "extra_negative": "", "model_tweaks": {model: {"loras": lt}} if (lt := ((comfy.get("model_tweaks") or {}).get(model) or {}).get("loras")) else {}, "ref_mode": "none"}
    plain = (bg.get("workflow") or "").strip() or (ROOT / "workflows" / "comfyui-flux2-klein.json").read_text(encoding="utf-8")
    ref = (ROOT / "workflows" / "comfyui-flux2-klein-ref.json").read_text(encoding="utf-8")
    return {**comfy, "id": "comfyui-flux", "model": model, "workflow": plain, "workflow_ref": ref, "prompt_style": "natural", "extra_positive": "", "extra_negative": "",
            "model_tweaks": {model: {"loras": lt}} if (lt := ((comfy.get("model_tweaks") or {}).get(model) or {}).get("loras")) else {}, "ref_mode": "characters"}   # only the LoRAs chosen for this model carry over


def _maker_spec(s: dict, ch: dict) -> str:
    """This character's picture maker: their own choice, else their species', else the story's ("")."""
    sp = (s.get("species") or {}).get("human") if _is_human(ch.get("species")) else _species_entry(s, ch.get("species"))   # humans can have a default maker too (a "Human" entry)
    return (ch.get("maker") or "").strip() or ((sp or {}).get("maker") or "").strip()


def _maker_provider(cfg: dict, s: dict, ch: dict | None, override: str | None = None) -> dict:
    """The picture provider for one character. "" = the story's own maker; "flux" = FLUX.2 klein with reference pictures; "ckpt:<name>" = another ComfyUI checkpoint
    (text prompts only, no face reference: right for a species the face model was never trained on)."""
    base = _img_provider(cfg, override, s)
    spec = "" if override else _maker_spec(s, ch or {})
    if spec == "flux":
        prov = _flux_provider(cfg, s)
    elif spec.startswith("ckpt:") and base.get("type") == "comfyui":
        name = spec[5:]
        raw = next((p for p in cfg.get("image_providers", []) if p.get("id") == base.get("id")), base)   # the checkpoint workflow, even when the story itself draws with FLUX
        prov = {**raw, "model": name, "workflow_ref": "", "prompt_style": _remembered_style(raw, name) or ("natural" if _model_family(name) == "real" else "tags")}
    else:
        prov = base
    _model_ctx.set(prov.get("model", ""))
    return prov


def _tag_quality() -> tuple[str, str]:
    """(quality tags, extra negative tags) for the checkpoint being drawn with: Pony models are trained on score tags."""
    if _model_family(_model_ctx.get()) == "pony":
        return "score_9, score_8_up, score_7_up", "score_6, score_5, score_4"
    return TAG_QUALITY, ""


def _copy_species_reference(s: dict, ch: dict, url: str) -> str:
    """Keep a copy of a character's picture as the reference picture of their species (it survives that character being removed)."""
    f = _portrait_file(url)
    if not f:
        return ""
    folder = store.PORTRAITS / s["id"] / "species"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{_slug(ch.get('species') or 'species')}-{int(time.time() * 1000)}{f.suffix}"
    shutil.copyfile(f, dest)
    return f"/portraits/{s['id']}/species/{dest.name}"


def _note_species_reference(s: dict, ch: dict, mood: str) -> None:
    """The first picture of a new species (its "normal 1" or default) becomes that species' reference for everyone drawn after."""
    name = (ch.get("species") or "").strip()
    if _is_human(name) or mood not in ("normal 1", "default", "normal"):
        return
    sp = (s.setdefault("species", {})).setdefault(name.lower(), {"name": name, "description": "", "maker": "", "reference_url": "", "from": ""})
    url = (ch.get("moods") or {}).get(mood)
    if url and not sp.get("reference_url"):
        ref = _copy_species_reference(s, ch, url)
        if ref:
            sp["reference_url"], sp["from"] = ref, ch.get("slug", "")


def _species_ref_urls(s: dict, ch: dict) -> list[str]:
    sp = _species_entry(s, ch.get("species"))
    return [sp["reference_url"]] if sp and sp.get("reference_url") else []


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
    _note_species_reference(s, ch, mood)
    tracker.apply_aliases(s)      # a generic side-panel label ("Innkeeper") becomes their name now that they have a picture


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


def _load_refs(urls: list[str], n: int, prov: dict | None = None) -> list[tuple[bytes, str]]:
    if prov and prov.get("ref_mode") == "none":      # a maker that cannot take reference pictures (Chroma, Qwen-Image): the prompt must not talk about references either
        return []
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
        elif k == "now_pic":
            art[k] = v if v in ("portrait", "live") else "portrait"
        elif k == "portrait_size":
            art[k] = v if v in ("small", "medium", "large", "xl") else "medium"
        elif k == "portrait_shape":
            art[k] = v if v in images.SHAPES else "landscape"
        elif k == "scene_display":
            art[k] = v if v in ("normal", "large", "full") else "normal"
        elif k == "scene_scale":
            art[k] = v if v in ("normal", "large", "xl") else "normal"
        elif k == "scene_gap":
            art[k] = max(1, min(int(v or 1), 20))
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
    """'Ann, Captain A' -> ['Ann', 'Captain A'] (no blanks, no repeats, not the character's own name)."""
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
    hits = [c for c in s.get("cast", []) if len(n) > 2 and n in [w.strip(".,").lower() for w in c["name"].split()]]   # a surname ("Smith")
    return hits[0] if len(hits) == 1 else None


def _merge_cast(s: dict, source_slug: str, into_slug: str) -> None:
    """The same person was in the cast twice (for example under a nickname and under their full name): fold the first into the second. The first one's name (and other names)
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
                    "species": prev.get("species", "") if m.species is None else m.species.strip()[:40],
                    "maker": prev.get("maker", "") if m.maker is None else m.maker.strip()[:120],
                    "aka": prev.get("aka", []) if m.aka is None else _split_aka(m.aka, name),
                    "address": prev.get("address", "") if m.address is None else m.address.strip()[:600],
                    "personality": prev.get("personality", "") if m.personality is None else m.personality.strip(),
                    "author": prev.get("author", False) if m.author is None else m.author})
    for c in new:      # every species named on a character is in the story's species list
        if not _is_human(c.get("species")):
            s.setdefault("species", {}).setdefault(c["species"].strip().lower(), {"name": c["species"].strip(), "description": "", "maker": "", "reference_url": "", "from": ""})
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


class GenericIn(BaseModel):
    items: list[dict] = []        # [{"name": "Waitress", "appearance": "..."}]: from the presets or typed by the player
    suggest: int = 0              # ask the helper AI for this many throwaway characters that suit the story's world (0 = none)


@app.get("/api/generic-presets")
def generic_presets():
    return [{"name": n, "appearance": a} for n, a in characters.GENERIC_PRESETS]


@app.post("/api/stories/{sid}/generic-characters")
async def add_generic_characters(sid: str, body: GenericIn):
    """Add throwaway characters for unnamed people (a waiter, a guard...). They are saved as ordinary cast members marked generic and shown as one card;
    the narrator is told to use them, by role, whenever an unnamed person speaks or appears."""
    s, cfg = _story(sid), store.load_config()
    items = [{"name": str(i.get("name", "")).strip(), "appearance": str(i.get("appearance", "")).strip()} for i in body.items if str(i.get("name", "")).strip()]
    if body.suggest > 0:
        prov, model = _role(cfg, s, "recaps", "describe look")
        system = ("You propose throwaway background characters for a story: the unnamed people who show up briefly (a waiter, a guard, a clerk, a passer-by). Reply with ONLY JSON: "
                  '{"characters": [{"name": "<a short ROLE, 1-3 words, capitalised, never a personal name>", "appearance": "<one line of comma-separated visual details: age and gender, hair, build, clothing that suits the story world>"}]}. '
                  f"Give exactly {min(body.suggest, 12)} different roles that fit THIS world and setting, adults only, safe for work, no famous characters, nobody who is already in the cast.")
        have = ", ".join(c["name"] for c in s["cast"][:40])
        ask = f"STORY: {s['title']}\nSETTING: {(s.get('art') or {}).get('setting', '')}\n\n{(s.get('base_prompt') or '')[:2500]}\n\nALREADY IN THE CAST: {have}"
        out = await providers.complete(prov, model, system, [{"role": "user", "content": ask}], temperature=0.7, max_tokens=1200, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
        j = _json_in(out)
        for c in (j.get("characters") if isinstance(j, dict) else None) or []:
            if isinstance(c, dict) and str(c.get("name", "")).strip():
                items.append({"name": str(c["name"]).strip(), "appearance": str(c.get("appearance", "")).strip()})
        if body.suggest and not items:
            raise HTTPException(502, "The helper AI returned no characters. Try again, or pick some from the list.")
    added = []
    for it in items:
        name = characters.clean_name(it["name"]) or it["name"][:30]
        if not name or any(c["name"].lower() == name.lower() for c in s["cast"]):
            continue
        ch = _ensure_char(s, name, it["appearance"])
        ch["generic"], ch["group"] = True, characters.GENERIC_GROUP
        added.append(ch["slug"])
    store.save_story(s)
    return {"story": s, "added": added}


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


class MoodRenameIn(BaseModel):
    to: str


@app.post("/api/stories/{sid}/cast/{slug}/mood/{mood}/rename")
def rename_mood(sid: str, slug: str, mood: str, body: MoodRenameIn):
    """Give a picture another name (its mood). If the new name is already used, this picture becomes the active one and the old one is kept as an earlier version."""
    s = _story(sid)
    ch = _char(s, slug)
    old, new = mood.strip().lower(), " ".join(body.to.strip().lower().split())
    if not re.fullmatch(r"[a-z0-9][a-z0-9 _-]{0,29}", new):
        raise HTTPException(400, "Use up to 30 letters, numbers, spaces, hyphens or underscores for a picture name.")
    moods = ch.setdefault("moods", {})
    if old not in moods:
        raise HTTPException(404, "That picture is not there any more.")
    if new == old:
        return s
    alts = ch.setdefault("alts", {})
    moved = [moods.pop(old)] + alts.pop(old, [])
    if moods.get(new):
        alts.setdefault(new, [])[:0] = moved[1:]
        alts[new].insert(0, moods[new])
    else:
        alts.setdefault(new, [])[:0] = moved[1:]
    moods[new] = moved[0]
    if not alts.get(new):
        alts.pop(new, None)
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


@app.delete("/api/stories/{sid}/cast/{slug}/pictures")
def delete_all_pictures(sid: str, slug: str):
    """Delete every picture this character has in the story: the active pictures for all moods, all earlier versions and any undecided draw options.
    Pictures borrowed from other stories (references) are kept, and so is the character themselves."""
    s = _story(sid)
    ch = _char(s, slug)
    urls = list((ch.get("moods") or {}).values()) + [u for lst in (ch.get("alts") or {}).values() for u in lst]
    for u in urls:
        f = _portrait_file(u)
        if f:
            f.unlink(missing_ok=True)
    ch["moods"], ch["alts"] = {}, {}
    shutil.rmtree(store.PORTRAITS / sid / "chars" / slug / "_candidates", ignore_errors=True)
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


_CLOTHES = re.compile(r"\b(blazer|blouse|skirt|shoes?|boots?|sandals|sneakers|heels|uniforms?|jackets?|coats?|hoodie|t-?shirt|shirts?|tee|dress(?:es|ed)?|gown|jeans|trousers|pants|shorts|slacks|suits?|neck-?ties?|ties?|scarf|hats?|caps?|beanie|sweater|cardigan|vest|apron|robes?|kimono|lanyard|badge|clipboard|backpack|satchel|belt|gloves?|stockings|tights|socks|leggings|bodysuit|jumpsuit|fatigues|outfit|clothes|clothing|wears?|wearing|worn|watch|bracelet|necklace|earrings?|holding|holds?|clutch(?:es|ing)?|carr(?:y|ies|ying)|tablet|phone|headset|name ?tag)\b", re.IGNORECASE)


def _body_look(look: str) -> str:
    """A character's looks without their everyday outfit and props (blazer, clipboard...): what the adult picture maker is given, so what they wear in this picture (or nothing) comes from the
    picture's own description and not from their usual clothes. Face, hair, eyes, build, skin and glasses stay."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", (look or "").strip()):
        keep = [c.strip(" .") for c in re.split(r"[,;]\s*", sent) if c.strip(" .") and not _CLOTHES.search(c)]
        if keep:
            out.append(", ".join(keep))
    return ". ".join(out).strip() + ("." if out else "")


_LOWER_BODY = re.compile(r"\b(jeans|trousers|pants|shorts|slacks|skirts?|leggings|tights|socks?|shoes?|boots?|sandals|sneakers|heels|loafers|legs?|knees?|waist|belt|full[- ]body|tall|height|\d ?(?:ft|feet|foot|cm|m)\b|grin\w*|smil\w*|laugh\w*|squint\w*|frown\w*|expression|smirk\w*)", re.IGNORECASE)


def _upper_look(look: str) -> str:
    """For close head-and-shoulders portraits: a look without the clauses about legs, trousers and shoes (a long list of outfit pieces pulls the model into a standing full-body picture)."""
    out = []
    for sent in re.split(r"(?<=[.!?])\s+", (look or "").strip()):
        keep = [c.strip(" .") for c in re.split(r"[,;]\s*", sent) if c.strip(" .") and not _LOWER_BODY.search(c)]
        if keep:
            out.append(", ".join(keep))
    return ". ".join(out).strip() + ("." if out else "")


CLOSE_FRAME = "Close head-and-shoulders portrait of ONE person only, cropped at the chest, the face large in the frame, facing the viewer, a single picture (not a collage, a sheet or several views)"


def _close_portrait(prompt: str) -> str:
    """Chroma / Qwen-Image portraits: one close head-and-shoulders picture. Without this they drift into a standing full-body figure or a contact sheet of several views of the same face."""
    prompt = prompt.replace("Upper body portrait (framed from the chest up), facing the viewer,", CLOSE_FRAME + ",")
    prompt = prompt.replace("Frame from the waist up so the hands and posture are visible.", "Frame tightly on the face and shoulders; one person, one picture.")
    prompt = prompt.replace(images.ADULT_LINE, "").replace(images.SAFE_LINE, "").strip()
    return "A single photograph of one person: one head, one face, one frame. " + prompt
PLAIN_BG_NEGATIVE = "street, city, buildings, shop signs, neon lights, vending machines, outdoors, crowd, side view, profile view, turned away, looking away, waxy skin, plastic skin"


async def _draw_mood(sid: str, slug: str, mood: str, provider: str | None = None) -> dict:
    """Draw one portrait. All the character's saved pictures are sent as references, so each saved one reinforces the next.
    Drawing a mood that already exists keeps the old picture as an alternate."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    prov = _maker_provider(cfg, s, ch, provider)
    mood = mood.strip().lower() or "default"
    look_of_mood = ((cfg.get("mood_looks") or {}).get(re.sub(r"\s*\d+$", "", mood)) or "")
    if not provider and (_is_explicit(mood + " " + look_of_mood, cfg) or (s.get("nsfw") and _mood_base(mood) in characters.ADULT_ONLY)) and (ns := _nsfw_provider(cfg, s)):
        prov = ns                # an explicit picture type (e.g. "nude", "aroused") in an adult story: the adult picture maker draws it
        _model_ctx.set(prov.get("model", ""))
    urls = _portrait_urls(ch, mood)
    species_only = False
    if not urls and prov.get("workflow_ref"):    # nothing of their own yet: the first picture of their species is a (tiny) reference for the look of the species
        urls = _species_ref_urls(s, ch)
        species_only = bool(urls)
        if species_only:
            prov = {**prov, "ref_megapixels": SPECIES_REF_MP}
    refs = _load_refs(urls, int(cfg.get("image_max_refs", 3)), prov)
    art = _nsfw_art(await _art_with_setting(s, cfg), prov)
    plain_bg = prov.get("ref_mode") == "none"       # Chroma / Qwen-Image: no reference pictures, and they paint any place named in the prompt behind the face, so the portrait leaves the world's setting out
    if plain_bg:
        art = {**art, "setting": ""}
    look = _full_appearance(s, ch)
    if plain_bg and not _is_action(mood):
        look = _upper_look(look) or look
    adult_sdxl = bool(prov.get("nsfw_maker")) and prov.get("type") == "comfyui" and not _is_flux_model(prov.get("model", ""))
    sex_pic = bool(s.get("nsfw")) and _mood_base(mood) in characters.ADULT_ONLY      # undressing, nude, positions...: what they wear comes from the picture, not their everyday outfit
    if sex_pic and not adult_sdxl:
        look = _body_look(look) or look
    if adult_sdxl:
        prov = {**prov, "_tight_face": True}     # only the face of the reference is used (its outfit and background must not carry over)
        look = _body_look(look) or look          # no everyday outfit or props: what they wear in this picture comes from the picture's own description
    ext_only = bool(refs) and not species_only and not _portrait_urls(ch, mood, ext=False)
    mood_text = None if mood == "default" else f"{mood} ({_mood_look(mood, bool(s.get('nsfw')), s)})"
    prompt = images.portrait_prompt(art, ch["name"], look, mood_text, bool(refs), ext_only, nsfw=bool(s.get("nsfw")),
                                    species_ref=(ch.get("species") or "") if species_only else "", framing=_framing(mood, s=s))
    negative = s["art"].get("negative", "")
    if plain_bg and not _is_action(mood):
        prompt = _close_portrait(prompt)
    if plain_bg:
        prompt = prompt.replace("simple soft background", "plain soft studio backdrop, soft even light").replace("Simple soft background.", "Plain soft studio backdrop, soft even light.")
        negative = (negative + ", " if negative else "") + PLAIN_BG_NEGATIVE
    if _wants_tags(prov):
        prompt, negative = await _portrait_tag_prompt(s, cfg, art, {**ch, "appearance": look}, mood, sex_pic=sex_pic)
    if prov.get("nsfw_maker"):
        if adult_sdxl and not _wants_tags(prov):
            _, negative = _add_nsfw_words("", negative, prov, "")           # the explicit words go into the short prompt below; this only clears words that forbid nudity from the negative
            prompt = images.nsfw_portrait_prompt(art, ch["name"], look, mood_text or "", _framing(mood, s=s), _nsfw_words(cfg, prov))
            negative = (negative + ", " if negative else "") + "street, city, buildings, cars, outdoors, clothes the picture does not call for"
        else:
            prompt, negative = _add_nsfw_words(prompt, negative, prov, _nsfw_words(cfg, prov))
    try:
        shape = ((prov.get("model_tweaks") or {}).get(prov.get("model", "")) or {}).get("shape") if prov.get("nsfw_maker") else None
        data, ext = await images.generate(prov, prompt, negative, shape if shape in images.SHAPES else s["art"].get("portrait_shape", "landscape"), refs, seed=zlib.crc32(slug.encode()) if plain_bg else None)       # a steady seed per character keeps the face alike across moods when there are no reference pictures
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


async def _events_after_turn(sid: str, idx: int, ts: float | None = None) -> None:
    """After a reply is saved: log key events for the characters in it. Runs in the background and must never disturb play."""
    try:
        cfg = store.load_config()
        if cfg.get("events_mode", "every") != "every":
            return
        s = store.load_story(sid)
        if idx >= len(s["turns"]) or s["turns"][idx]["role"] != "assistant" or not s.get("cast"):
            return
        if ts is not None and s["turns"][idx].get("ts") != ts:     # that reply was retried or swapped meanwhile: do not log its events
            return
        raw = s["turns"][idx]["text"]
        text = _clean_for_scene(raw)
        if _local_on(cfg, _events_model(cfg, s)[0]):
            # Local-model mode: one helper call for every third reply (or at once when the reply announces a feat, level or quest), covering the replies since the last one
            if ((idx + 1) // 2) % 3 and not rules.key_events(raw):
                return
            text = "\n\n---\n\n".join(_clean_for_scene(t["text"]) for t in [x for x in s["turns"][:idx + 1] if x["role"] == "assistant"][-3:])
        names = [c["name"] for c in s["cast"] if re.search(rf"\b{re.escape(c['name'].split()[0])}\b", text, re.IGNORECASE)]
        names = [n for n in names if not next((c.get("player") for c in s["cast"] if c["name"] == n), False)]       # the player's own events come from the notifications (free)
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


class SpeciesItemIn(BaseModel):
    name: str
    description: str = ""
    traits: str = ""
    maker: str = ""


class SpeciesIn(BaseModel):
    species: list[SpeciesItemIn]


@app.put("/api/stories/{sid}/species")
def update_species(sid: str, body: SpeciesIn):
    """The story's species: a description of what is true of the whole species (added to every member's picture prompt), the picture maker they use by default, and
    the reference picture (kept). A species removed from the list loses its entry; the characters keep their species name."""
    s = _story(sid)
    old = s.get("species") or {}
    new = {}
    for it in body.species:
        name = " ".join(it.name.split())[:40]
        if not name:
            continue
        key = name.lower()
        prev = old.get(key, {})
        new[key] = {"name": name, "description": it.description.strip()[:800], "traits": it.traits.strip()[:1200], "maker": it.maker.strip()[:120],
                    "reference_url": prev.get("reference_url", ""), "from": prev.get("from", "")}
    for key in old.keys() - new.keys():
        f = _portrait_file(old[key].get("reference_url", ""))
        if f:
            f.unlink(missing_ok=True)
    s["species"] = new
    store.save_story(s)
    return s


SPECIES_SYSTEM = ("You write the reference entry for one species (or race, or kind of being) of an interactive story, from the story prompt and what is known of its members. "
                  "Reply with ONLY JSON: {\"look\": \"...\", \"traits\": \"...\"}. "
                  "\"look\": what is visibly true of EVERY member, for an image generator: skin or covering, eyes, ears, horns or crests, build and proportions, typical clothing; under 60 words, no names, nothing explicit. "
                  "\"traits\": what the narrator must keep true of the species in the story: abilities, weaknesses, senses, lifespan, culture, how others treat them; under 90 words, plain sentences. "
                  "Use only what the story prompt and the members' descriptions support; if the story says little, keep it short rather than inventing a lot.")


@app.post("/api/stories/{sid}/species/{key}/write")
async def write_species(sid: str, key: str):
    """Draft a species' picture description and story traits from the story prompt and its members' looks (nothing is saved: the page puts them in the boxes to edit)."""
    s, cfg = _story(sid), store.load_config()
    sp = (s.get("species") or {}).get(key.strip().lower())
    if not sp:
        raise HTTPException(404, "That species is not in this story.")
    members = [c for c in s.get("cast", []) if (c.get("species") or "").strip().lower() == key.strip().lower()]
    who = "\n".join(f"- {c['name']}: {(c.get('appearance') or '').strip()[:300]}" for c in members[:8]) or "(no characters of this species yet)"
    user = (f"SPECIES: {sp['name']}\n\nMEMBERS SO FAR:\n{who}\n\nCURRENT NOTES (keep what is right):\nlook: {sp.get('description', '')}\ntraits: {sp.get('traits', '')}"
            f"\n\nSTORY: {s['title']}\n{s.get('base_prompt', '')[:7000]}")
    prov, model = _role(cfg, s, "recaps", "species")
    out = await providers.complete(prov, model, SPECIES_SYSTEM, [{"role": "user", "content": user}], temperature=0.3, max_tokens=500, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    data = _json_in(out)
    if not isinstance(data, dict) or not (data.get("look") or data.get("traits")):
        raise HTTPException(502, "The helper model did not return a usable description. Try again.")
    return {"description": " ".join(str(data.get("look") or "").split())[:800], "traits": " ".join(str(data.get("traits") or "").split())[:1200]}


class SpeciesRefIn(BaseModel):
    slug: str


@app.post("/api/stories/{sid}/species/{key}/reference")
def set_species_reference(sid: str, key: str, body: SpeciesRefIn):
    """Use one character's picture as the reference of this species (the first picture drawn does this by itself)."""
    s = _story(sid)
    sp = (s.get("species") or {}).get(key.lower())
    if not sp:
        raise HTTPException(404, "Unknown species")
    ch = _char(s, body.slug)
    moods = ch.get("moods") or {}
    url = next((moods[m] for m in ("normal 1", "default", "normal") if moods.get(m)), next(iter(moods.values()), ""))
    if not url:
        raise HTTPException(400, f"{ch['name']} has no picture yet.")
    ref = _copy_species_reference(s, {**ch, "species": sp["name"]}, url)
    if not ref:
        raise HTTPException(404, "That picture is missing.")
    old = _portrait_file(sp.get("reference_url", ""))
    if old:
        old.unlink(missing_ok=True)
    sp["reference_url"], sp["from"] = ref, ch["slug"]
    store.save_story(s)
    return s


@app.delete("/api/stories/{sid}/species/{key}/reference")
def clear_species_reference(sid: str, key: str):
    s = _story(sid)
    sp = (s.get("species") or {}).get(key.lower())
    if not sp:
        raise HTTPException(404, "Unknown species")
    f = _portrait_file(sp.get("reference_url", ""))
    if f:
        f.unlink(missing_ok=True)
    sp["reference_url"], sp["from"] = "", ""
    store.save_story(s)
    return s


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


SHEET_MIN_REPLIES = 15      # fewer narrator replies than this, or fewer appearances of the character than SHEET_MIN_MENTIONS: too early to fill a sheet from the story
SHEET_MIN_MENTIONS = 5


def _sheet_stats(s: dict, ch: dict) -> dict:
    pat = re.compile(rf"\b{re.escape(ch['name'].split()[0])}\b", re.IGNORECASE)
    replies = [t for t in s.get("turns", []) if t.get("role") == "assistant"]
    mentions = sum(1 for t in replies if pat.search(t["text"]))
    reasons = []
    if len(replies) < SHEET_MIN_REPLIES:
        reasons.append(f"only {len(replies)} narrator replies so far (at least {SHEET_MIN_REPLIES} are better)")
    if mentions < SHEET_MIN_MENTIONS:
        reasons.append(f"{ch['name']} appears in only {mentions} of them (at least {SHEET_MIN_MENTIONS} are better)")
    return {"replies": len(replies), "mentions": mentions, "too_early": bool(reasons), "reasons": reasons}


@app.get("/api/stories/{sid}/cast/{slug}/sheet-info")
def sheet_info(sid: str, slug: str):
    """How much story there is to fill a character's whole sheet from (free): the dialog warns when it is too early."""
    s = _story(sid)
    return _sheet_stats(s, _char(s, slug))


SHEET_SYSTEM = (
    "You fill in a story character's whole sheet from what the story itself has shown. Use ONLY what the material states or clearly implies; never invent. "
    "A field the story does not support must be an empty string (or an empty list). Fields: "
    "'appearance' = a purely visual description for a picture maker: apparent age, build, hair, eyes, face, usual clothing, distinguishing features; safe for work; "
    "'personality' = one short line on how they act and speak; "
    "'bio' = 3 to 5 sentences: who they are, their role, motives, history so far and key relationships; "
    "'address' = who calls them what and how they address others, only if the story shows it (for example 'Most people say Warden; only old friends say Ottilie'); "
    "'aka' = other names, nicknames or titles the story uses for them (a list); "
    "'species' = only when they are not human (otherwise an empty string). "
    "Keep what the CURRENT SHEET already says when the material agrees with it; correct it only when the story clearly shows otherwise. "
    'Reply with ONLY JSON: {"appearance": "...", "personality": "...", "bio": "...", "address": "...", "aka": ["..."], "species": ""}.')


@app.post("/api/stories/{sid}/cast/{slug}/sheet-suggest")
async def suggest_sheet(sid: str, slug: str):
    """Suggest every field of a character's sheet from the story so far (one text-model call). Nothing is saved: the player reviews each field first."""
    s, cfg = _story(sid), store.load_config()
    ch = _char(s, slug)
    ctx = _event_context(s, ch["name"], limit=14000) or _context_about(s, ch["name"], limit=6000)
    if not ctx:
        raise HTTPException(404, f"The story text doesn't mention {ch['name']} yet, so there is nothing to fill the sheet from.")
    cur = {k: ch.get(k, "") for k in ("appearance", "personality", "bio", "address", "species")}
    cur["aka"] = ch.get("aka", [])
    events = "\n".join(f"- {e.get('when', '')}: {e.get('text', '')}" for e in ch.get("events", [])[:30])
    lines = "\n".join(prompt_lines(sid, slug)["lines"])
    user = (f"Character: {ch['name']}\n\nCURRENT SHEET:\n{json.dumps(cur, ensure_ascii=False)}\n\nKEY EVENTS ALREADY LOGGED:\n{events or '(none)'}\n\n"
            f"WHAT THE STORY PROMPT SAYS ABOUT THEM:\n{lines or '(nothing)'}\n\nWHAT THE STORY HAS SHOWN (passages mentioning them):\n{ctx}")
    prov, model = _events_model(cfg, s, "character sheet")
    out = await providers.complete(prov, model, SHEET_SYSTEM, [{"role": "user", "content": user}], temperature=0.2, max_tokens=1500, num_ctx=cfg["num_ctx"], **_extras(cfg))
    j = _json_in(out)
    if not isinstance(j, dict):
        raise HTTPException(502, "The text model did not return a character sheet. Try again.")
    one = lambda v, n: " ".join(str(v or "").split())[:n]
    aka = [one(a, 40) for a in (j.get("aka") if isinstance(j.get("aka"), list) else []) if one(a, 40) and one(a, 40).lower() != ch["name"].lower()][:6]
    species = one(j.get("species"), 40)
    return {"fields": {"appearance": one(j.get("appearance"), 700), "personality": one(j.get("personality"), 300), "bio": one(j.get("bio"), 1000),
                       "address": one(j.get("address"), 600), "aka": ", ".join(aka), "species": "" if _is_human(species) else species},
            "current": {**{k: cur[k] for k in ("appearance", "personality", "bio", "address", "species")}, "aka": ", ".join(cur["aka"])}, **_sheet_stats(s, ch)}


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
            with localrt.hold():      # ComfyUI stays up for the whole job, not only while each picture is drawn
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
               '"same": for each side-panel label you are given (a generic tag such as "Innkeeper"), say which named character it is, known or new, when the passages '
               "make that clear; leave the label out when it is someone else or you are not sure. "
               'Also add "aka": [{"alias": "...", "of": "..."}] for any name in the passages (a first name, surname, nickname or title) that is just ANOTHER NAME for a '
               "character on the known list (for example the passages call a character by a nickname), whether or not the alias is itself on the known list. Only when the passages make it clear; "
               'never list such an alias under "new". This is a fiction-writing tool; just report names. Use [] for an empty list.')


def _tracker_labels(s: dict) -> list[str]:
    """Side-panel character rows that do not match anyone in the cast by name (usually a generic label like "Innkeeper")."""
    first = {c["name"].lower().split()[0] for c in s.get("cast", [])} | {c["name"].lower() for c in s.get("cast", [])}
    first |= {a.lower() for c in s.get("cast", []) for a in c.get("aka") or []}
    asked = s.get("alias_asked") or {}
    now = len(s.get("turns", []))
    return [n for n in ((s.get("tracker") or {}).get("chars") or {}) if n.lower() not in first and n.lower().split()[0] not in first
            and now - asked.get(n, -99) >= 8 and n not in (s.get("char_alias") or {})]


def _apply_aka(story: dict, items: list[dict]) -> None:
    """The helper model says a name is just another name for a known character: remember it as "also called". If that name is already a separate cast entry
    with no pictures (made earlier by mistake, like a nickname next to the full name), it is folded into the real character; one with pictures is left for the player."""
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
    any named character missing from the cast, and works out which named character a generic side-panel label ("Innkeeper") really is. Failures are
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
        ch = _ensure_char(story, n, look[:400])
        known.add(n.lower())
        _file_species(story, ch)


def _file_species(story: dict, ch: dict) -> None:
    """A new character whose look starts with a species ("Elf: tall, pointed ears..." or "Elf, tall...") is filed under it. A known species may be written either way;
    a species the story has not met yet needs the colon form, and is added to the story's species list."""
    if ch.get("species"):
        return
    m = re.match(r"\s*([A-Z][a-z]{2,19})\s*([:,])\s", ch.get("appearance", ""))
    if not m:
        return
    word, sep = m.group(1), m.group(2)
    species = story.get("species") or {}
    if word.lower() in species or (sep == ":" and word.lower() not in ("human", "humans", "tall", "short", "young", "old", "male", "female", "man", "woman", "large", "small", "broad")):
        ch["species"] = species[word.lower()]["name"] if word.lower() in species else word
        ch["appearance"] = ch["appearance"][m.end():].strip() or ch["appearance"]
        story.setdefault("species", {}).setdefault(word.lower(), {"name": word, "description": "", "maker": "", "reference_url": "", "from": ""})


@app.post("/api/stories/{sid}/cast/candidates")
async def cast_candidates(sid: str, body: CandidatesIn):
    """Draw a few independent candidate pictures of a character (added to the cast if new). Choose one with .../choose."""
    s, cfg = _story(sid), store.load_config()
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Give the character a name")
    ch = _ensure_char(s, name, body.appearance)
    prov = _maker_provider(cfg, s, ch, body.provider)
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
        _model_ctx.set(prov.get("model", ""))
        look = _full_appearance(st, c) + (f". Extra notes: {notes}" if notes else "")
        species_urls = _species_ref_urls(st, c) if (not c.get("moods") and prov.get("workflow_ref")) else []   # a new individual of a known species: its first picture is a small reference
        cprov = {**prov, "ref_megapixels": SPECIES_REF_MP} if species_urls else prov
        if _wants_tags(prov):
            prompt, negative = await _portrait_tag_prompt(st, cf, art, {**c, "appearance": _full_appearance(st, c)}, "default", notes)
        else:
            prompt = images.portrait_prompt(art, c["name"], look, None, bool(species_urls), False, nsfw=bool(st.get("nsfw")), species_ref=(c.get("species") or "") if species_urls else "")
            negative = st["art"].get("negative", "")
        folder = store.PORTRAITS / sid / "chars" / slug / "_candidates"
        folder.mkdir(parents=True, exist_ok=True)
        cand_refs = _load_refs([r["url"] for r in c.get("ext_refs") or []], int(cf.get("image_max_refs", 3)), prov) if not c.get("moods") else []
        if species_urls:
            cand_refs = _load_refs(species_urls, 1, prov)
        elif cand_refs and not _wants_tags(prov):
            prompt = images.portrait_prompt(art, c["name"], look, None, True, True, nsfw=bool(st.get("nsfw")))
        for i in range(count):
            j["current"] = f"Drawing option {i + 1} of {count}…"
            try:
                data, ext = await images.generate(cprov, prompt, negative, shape, cand_refs)
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
    preset: str = "basic"            # basic / extended / action / action_adult (editable in Settings) / match (the moods your other characters have)
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
    elif body.preset == "action_adult" and not s.get("nsfw"):
        raise HTTPException(400, "The adult action set is only for stories with adult content switched on (Settings → Story & narration).")
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


@app.post("/api/stories/{sid}/cast/{slug}/redraw-all")
async def redraw_all_pictures(sid: str, slug: str):
    """After a new "normal 1" is chosen: delete every other picture of this character (all moods and earlier versions) and redraw those moods from the new face."""
    s = _story(sid)
    ch = _char(s, slug)
    moods = ch.get("moods") or {}
    keep = moods.get("normal 1")
    if not keep:
        raise HTTPException(400, "This character has no \"normal 1\" picture to redraw from.")
    todo = [m for m in moods if m != "normal 1"]
    if not todo:
        raise HTTPException(400, "This character has no other pictures to redraw.")
    folder = store.PORTRAITS / sid / "chars" / slug
    keep_file = _portrait_file(keep)
    for f in list(folder.iterdir()) if folder.exists() else []:
        if f.is_file() and f != keep_file:
            f.unlink(missing_ok=True)
    ch["moods"] = {"normal 1": keep}
    ch["alts"] = {}
    store.save_story(s)
    return await draw_mood_set(sid, slug, DrawSetIn(moods=todo))


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


_ACTION_WORDS = re.compile(r"\b(walk\w*|run\w*|ran|stand\w*|stood|sit\w*|sat|lean\w*|turn\w*|reach\w*|grab\w*|hold\w*|held|lift\w*|point\w*|smil\w*|frown\w*|glar\w*|laugh\w*|step\w*|kneel\w*|climb\w*|"
                          r"swing\w*|strike\w*|struck|raise\w*|draw\w*|drew|open\w*|close\w*|push\w*|pull\w*|look\w*|watch\w*|cross\w*|fold\w*)\b", re.IGNORECASE)


def _template_scene(s: dict, text: str) -> tuple[str, list[str], dict]:
    """A scene description with no AI call (art.scene_template): who is shown (the narrator's portrait tags, else cast names in the reply) with their moods, the place (a place
    tag or the Place tracker) and the most active narration sentence. Simpler than the model's, and free."""
    tags = [(a.strip(), b.strip().lower()) for a, b in _PORTRAIT_TAG.findall(text)]
    player = prompting.player_name(s).lower()
    names, moods = [], {}
    for n, m in tags:
        if n.lower() != player and n not in names:
            names.append(n)
        moods[n.lower()] = re.sub(r"\s*\d+$", "", m)
    clean = _clean_for_scene(text)
    if not names:
        names = [c["name"] for c in s.get("cast", []) if not c.get("player") and re.search(rf"\b{re.escape(c['name'].split()[0])}\b", clean, re.IGNORECASE)][:3]
    place = ""
    pm = re.findall(r"!\[\]\(place:([^)\n]+)\)", text)
    if pm:
        p = next((x for x in s.get("places", []) if x["name"].lower() == pm[-1].strip().lower()), None)
        place = (p.get("description") or p["name"]) if p else pm[-1].strip()
    else:
        for f in tracker.active_fields(s):
            if f["label"].strip().lower() in ("place", "location") and f["id"].split(".")[0] in ("scene", "me"):
                place = str(((s.get("tracker") or {}).get(f["id"].split(".")[0]) or {}).get(f["id"].split(".")[1]) or "")
    sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n+", re.sub(r"[\"“”][^\"“”]{0,400}[\"“”]", "", clean)) if 25 <= len(x.strip()) <= 260]
    act = next((x for x in sents if _ACTION_WORDS.search(x) and any(n.split()[0].lower() in x.lower() for n in names)), None) or next((x for x in sents if _ACTION_WORDS.search(x)), sents[0] if sents else "")
    who = ", ".join(f"{n}" + (f" ({moods[n.lower()]})" if moods.get(n.lower()) and moods[n.lower()] not in ("normal", "default") else "") for n in names[:3])
    scene = " ".join(x for x in ((f"In {place}." if place else ""), (f"{who}." if who else ""), act) if x).strip()
    return scene[:600], names[:3], moods


TAG_QUALITY = "masterpiece, high score, great score, absurdres"
TAG_NEGATIVE = ("lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, cropped, worst quality, "
                "low quality, low score, bad score, signature, watermark, blurry")


MOOD_LOOKS = {   # what to draw for common mood names (numbers like "angry 2" are stripped first): the face AND the body language
    "normal": "calm neutral expression, relaxed posture, arms at sides",
    "smile": "warm genuine smile reaching the eyes, relaxed open shoulders, head slightly tilted",
    "shy": "shy and embarrassed, blushing cheeks, eyes looking away, head ducked, shoulders drawn in, fingers fidgeting at chest",
    "surprise": "surprised, wide eyes, raised eyebrows, open mouth, leaning back slightly, hands raised",
    "sulky": "sulking, pouting lips, puffed cheeks, eyes cut sideways toward the side, brows low, facing the viewer",
    "fear": "terrified, wide frightened eyes, pale face, trembling lips, shoulders hunched, hands clutched to chest, shrinking back",
    "sad": "sad, teary eyes, trembling lower lip, eyebrows angled up in the middle, downcast gaze, slumped shoulders, head bowed",
    "angry": "furious, deep furrowed brows, glaring eyes, clenched teeth, tense shoulders, fists clenched, leaning forward",
    "scorn": "scornful, half-lidded eyes looking down the nose, one eyebrow raised, curled disdainful smirk, chin raised, arms crossed",
    "despair": "despair, tears running down the cheeks, hollow lifeless eyes, mouth slightly open, slumped shoulders, hands limp or covering the face",
    "combat": "in the middle of a fight: striking or lunging with a weapon or fists, body twisted in motion, teeth gritted, eyes locked on an enemy, hair and clothes flying, sweat and dirt, motion on the strike",
    "cute": "cute pose, blushing, playful smile, winking or wide sparkling eyes, head tilted, hands near face",
    "dead": "dead: a lifeless body lying limp on the ground, eyes half-open and glassy, slack jaw, pale grey skin, limbs sprawled awkwardly",
    "zombie": "undead, pale grey skin, glowing red eyes, torn uniform, slack jaw, hunched lurching posture",
    # ---- action moods (one character in the middle of physical action)
    "strike": "throwing a fierce blow: a punch, kick or sword swing, body twisted into the strike, teeth gritted, eyes locked on the target, motion blur on the strike",
    "guard": "guarding: bracing behind raised forearms or a shield, feet planted, eyes narrowed, flinching as the blow lands, debris flying",
    "dodge": "dodging: twisting sharply out of the way of an attack, hair and clothes whipping, wide alert eyes, off-balance but fast",
    "charge": "charging forward at a run, leaning into the sprint, mouth open in a shout, weapon raised or fists ready, wind in the hair",
    "leap": "leaping through the air, limbs extended, weapon or fists ready, clothes and hair streaming, the ground far below",
    "aim": "aiming a ranged weapon (bow, rifle, pistol or spell focus), one eye narrowed, steady grip, intense calm focus",
    "cast": "casting a spell: hands raised and glowing with magical light, eyes lit, wind lifting hair and clothes, deep concentration",
    "draw weapon": "drawing a weapon: hand on the hilt or grip, blade half out, narrowed eyes, low ready stance, tense",
    "wounded": "badly wounded: clutching a bleeding side, grimacing in pain, torn dirty clothes, sweat, bracing on one knee or against a wall",
    "knocked down": "knocked down: thrown backwards by a blow, mid-fall, mouth open in a gasp, arms flung out, dust and debris around",
    "defeated": "defeated: on one knee, head bowed, weapon dropped or broken, battered and bruised, panting, hair hanging over the face",
    "exhausted": "exhausted after the fight: bent forward with hands on knees, panting hard, sweat running down, hair stuck to the face",
    "flee": "running for their life: sprinting, looking back over a shoulder in fear, hair and clothes streaming, desperate",
    "sneak": "sneaking: crouched low and silent, one finger to the lips or a hand on a blade, eyes scanning the shadows",
    "battle cry": "roaring a battle cry: head thrown back, mouth wide open, veins standing out, fist or weapon raised, fierce glare",
    "victory": "triumphant after winning: fist or weapon raised high, fierce grin, chest heaving, standing over the battlefield",
    # ---- intimate moods, softened (the adult versions below are used only in stories with adult content on)
    "aroused": "flustered and blushing, flushed cheeks, nervous quick breathing, shy sideways glance",
    "seductive": "confident flirtatious pose, sultry half-lidded gaze, soft smile, head tilted, leaning forward",
    "undressing": "taking off a coat or jacket, hands at the collar, glancing aside",
    "lingerie": "in a silky robe and sleepwear, hand on hip, soft bedroom light",
    "showering": "stepping out of a steamy bathroom wrapped in a towel, wet hair, shoulders covered",
    "bathing": "relaxing in a bubble bath, shoulders above the foam, steam, hair pinned up",
    "nude": "wrapped in a bedsheet, bare shoulders, tasteful and fully covered",
    "climaxing": "overwhelmed with joy, eyes shut, head thrown back, radiant smile",
    "afterglow": "resting in bed, drowsy smile, tousled hair, blanket pulled up",
}


MOOD_LOOKS_ADULT = {   # stories with adult content on: the same picture types, shown without softening
    "aroused": "aroused: flushed cheeks, parted lips, heavy-lidded eyes, quickened breathing, a hand at the throat or in the hair",
    "seductive": "seductive pose: sultry half-lidded gaze, a knowing smile, head tilted, one shoulder bared, leaning toward the viewer",
    "undressing": "undressing: pulling clothes off, shirt or dress half off, bare shoulders and chest, glancing at the viewer",
    "lingerie": "wearing revealing lingerie, confident pose, hand on hip, soft bedroom light",
    "showering": "in the shower, wet skin and hair, water running over bare shoulders and chest, steam, eyes closed",
    "bathing": "relaxing in a bath, wet bare shoulders and chest above the water, steam, flushed cheeks, hair pinned up",
    "nude": "nude, unclothed upper body, relaxed confident pose, soft light",
    "climaxing": "in the throes of pleasure: eyes shut or rolled back, mouth open in a gasp, flushed skin, back arched, tangled hair",
    "afterglow": "in the afterglow: lying back, flushed and tousled, a sheet pulled loosely over the body, drowsy smile, damp hair",
    # sexual positions: ONLY this character is in the picture; the partner is never shown (out of frame, or only a hand or the edge of a body at the border)
    "riding cowgirl": "in the riding cowgirl position, straddling a partner who is out of frame, knees wide and hips low, seen from the front, body upright with the back arched, hands braced on the partner below the frame or in their own hair, head tilted back, flushed face, parted lips, half-closed eyes, bare skin glistening with sweat",
    "riding reverse cowgirl": "in the reverse cowgirl position, straddling a partner who is out of frame, seen from behind and slightly from the side, back arched, hips low, one hand braced on the partner's legs below the frame, face turned over the shoulder toward the viewer, flushed, lips parted, glistening with sweat",
    "doggy-style": "in the doggy-style position, on hands and knees on a bed, hips raised and back arched, seen from the side and slightly behind, gripping the sheets, face turned over the shoulder toward the viewer, flushed, mouth open, glistening with sweat, a partner behind them who is out of frame",
    "blowjob": "giving oral sex, kneeling, face tilted up and looking up at the viewer with flushed cheeks and half-closed eyes, mouth around the partner's cock at the bottom edge of the picture, the partner themself out of frame, saliva, one hand braced on the partner's thigh",
    "missionary": "in the missionary position, lying on their back on a bed, legs raised and parted, seen from above, back arched, head pressed into the pillow, gripping the sheets, flushed face, open mouth, eyes shut or half-closed, glistening with sweat, a partner above them who is out of frame",
    "anal": "in an anal sex scene, on hands and knees on a bed with the hips raised and the back arched, seen from the side and slightly behind, face turned over the shoulder toward the viewer, flushed, mouth open, gripping the sheets, glistening with sweat, a partner behind them who is out of frame",
    # how a scene ends: ONLY this character is in the picture, the partner is never shown (out of frame)
    "creampie": "after a creampie, lying on their back on a bed with the legs parted, seen from above and in front, semen leaking from the pussy, flushed and panting, half-closed eyes, glistening with sweat, the partner out of frame",
    "anal creampie": "after an anal creampie, on hands and knees with the hips raised, seen from behind and slightly from the side, semen leaking from the ass, face turned over the shoulder toward the viewer, flushed and panting, glistening with sweat, the partner out of frame",
    "face cum": "with semen on the face, kneeling, face tilted up toward the viewer, semen on the cheeks, lips and eyelashes, flushed, half-closed eyes, a tired satisfied smile, the partner out of frame",
    "ass cum": "with semen on the ass, on hands and knees with the hips raised, seen from behind and slightly from the side, semen over the buttocks and lower back, face turned over the shoulder toward the viewer, flushed, glistening with sweat, the partner out of frame",
    "pussy cum": "with semen on the pussy and inner thighs, lying back with the legs parted, seen from above and in front, flushed and panting, half-closed eyes, glistening with sweat, the partner out of frame",
    "swallow": "swallowing, kneeling, head tipped slightly back, throat working, an empty open mouth shown to the viewer with the tongue out, a trace of semen on the lips, flushed, half-closed eyes, the partner out of frame",
    "mouth cum": "with semen in the open mouth, kneeling, face tilted up toward the viewer, tongue out, semen on the tongue and lips, flushed, half-closed eyes, the partner out of frame",
    "dead": "dead: a lifeless body lying in a pool of blood, a visible fatal wound, blood on the face and clothes, eyes half-open and glassy, slack jaw, pale grey skin, limbs sprawled awkwardly",
    "combat": "in the middle of a brutal fight: striking or lunging with a weapon or fists, body twisted in motion, teeth gritted, eyes locked on an enemy, cuts and blood spatter, sweat and dirt, torn clothes, motion on the strike",
}
MOOD_FRAMING = {   # picture types that need a different frame than a chest-up portrait
    "combat": "Dynamic three-quarter action shot, slightly low camera angle, motion in the scene, battlefield or ruins softly in the background. The face, shoulders, arms and weapon are the focus and fill the upper half of the picture.",
    "dead": "Upper body seen from above, lying on their back on the ground, the face clearly visible and turned toward the viewer.",
    "riding cowgirl": "Three-quarter shot from the front and slightly below, showing the face, chest and hips of the one person in the picture; the partner is not shown. Bedroom, soft light.",
    "riding reverse cowgirl": "Three-quarter shot from behind and to the side, the back, hips and the face turned over the shoulder visible; the partner is not shown. Bedroom, soft light.",
    "doggy-style": "Three-quarter shot from the side and slightly behind, on hands and knees, the arched back, hips and the face turned toward the viewer visible; the partner is not shown. Bedroom, soft light.",
    "blowjob": "Close shot from above and in front, the face, shoulders and the top of the body visible, looking up at the viewer; the partner is cropped out of the picture. Bedroom, soft light.",
    "missionary": "Shot from above, lying on a bed, the face, chest and raised legs visible; the partner is not shown. Bedroom, soft light.",
    "anal": "Three-quarter shot from the side and slightly behind, on hands and knees, the arched back, hips and the face turned toward the viewer visible; the partner is not shown. Bedroom, soft light.",
    "creampie": "Shot from above and in front, lying on a bed, the face, chest and parted legs visible; the partner is not shown. Bedroom, soft light.",
    "anal creampie": "Three-quarter shot from behind and slightly to the side, on hands and knees, the hips and the face turned over the shoulder visible; the partner is not shown. Bedroom, soft light.",
    "face cum": "Close shot from slightly above, the face and shoulders filling the picture, looking up at the viewer; the partner is cropped out of the picture. Bedroom, soft light.",
    "ass cum": "Three-quarter shot from behind and slightly to the side, on hands and knees, the lower back, hips and the face turned over the shoulder visible; the partner is not shown. Bedroom, soft light.",
    "pussy cum": "Shot from above and in front, lying back on a bed, the face, chest, hips and thighs visible; the partner is not shown. Bedroom, soft light.",
    "swallow": "Close shot from slightly above, the face and shoulders filling the picture, looking up at the viewer; the partner is cropped out of the picture. Bedroom, soft light.",
    "mouth cum": "Close shot from slightly above, the face and shoulders filling the picture, looking up at the viewer; the partner is cropped out of the picture. Bedroom, soft light.",
}
# Picture types that are a face and a feeling. Anything else (combat, dead, stripping, showering, running, sleeping...) is an ACTION picture: drawn as a
# three-quarter shot that shows the action, with the face and upper body as the focus (and shown cropped to the upper body in the story).
EXPRESSION_MOODS = {"default", "normal", "smile", "happy", "laugh", "laughing", "shy", "surprise", "surprised", "sulky", "fear", "scared", "sad", "angry", "scorn",
                    "despair", "cute", "stern", "flustered", "unimpressed", "trusting", "determined", "worried", "confused", "bored", "disgusted",
                    "embarrassed", "blush", "blushing", "cry", "crying", "smug", "curious", "proud", "nervous", "tired", "excited", "love", "flirty",
                    "calm", "serious", "thinking", "pout", "annoyed", "suspicious", "relieved", "hopeful", "guilty", "jealous", "lonely", "grim", "cold", "zombie",
                    "grin", "grinning", "smirk", "glare", "pensive", "sheepish", "amused", "wary", "resigned", "stunned", "awkward", "smug", "cheerful", "playful", "mischievous", "teasing", "dazed"}
ACTION_FRAMING = ("Three-quarter shot that shows the whole action and the body, with the face, shoulders and hands as the focus in the upper half of the picture.")
ACTION_FRAMING_TAGS = "cowboy shot, face focus, dynamic pose"


def _is_action(mood: str) -> bool:
    return _mood_base(mood) not in EXPRESSION_MOODS


def _story_mood(s: dict | None, mood: str, key: str) -> str:
    """A story can reword a picture type for itself: story["art"]["mood_looks"] / ["mood_framing"] / ["mood_framing_tags"] = {"dead": "..."} (the mood tag stays the same)."""
    return str((((s or {}).get("art") or {}).get(key) or {}).get(_mood_base(mood), "")).strip()


def _framing(mood: str, tags: bool = False, s: dict | None = None) -> str:
    base = _mood_base(mood)
    if (own := _story_mood(s, mood, "mood_framing_tags" if tags else "mood_framing")):
        return own
    if tags:
        return MOOD_FRAMING_TAGS.get(base) or (ACTION_FRAMING_TAGS if _is_action(mood) else "")
    return MOOD_FRAMING.get(base) or (ACTION_FRAMING if _is_action(mood) else "")


MOOD_FRAMING_TAGS = {"combat": "fighting, action shot, dynamic pose, dynamic angle, motion blur, cowboy shot, face focus", "dead": "upper body, lying, on back, from above, on ground, face visible",
                     "riding cowgirl": "cowboy shot, from front, from below, solo focus, bedroom",
                     "riding reverse cowgirl": "cowboy shot, from behind, from side, looking back, solo focus, bedroom",
                     "doggy-style": "cowboy shot, from side, from behind, looking back, solo focus, bedroom",
                     "blowjob": "close-up, from above, pov, looking up, solo focus, bedroom",
                     "missionary": "cowboy shot, from above, lying, on bed, solo focus, bedroom",
                     "anal": "cowboy shot, from side, from behind, looking back, solo focus, bedroom",
                     "creampie": "cowboy shot, from above, lying, on bed, solo focus, bedroom",
                     "anal creampie": "cowboy shot, from behind, looking back, solo focus, bedroom",
                     "face cum": "close-up, from above, pov, looking up, solo focus, bedroom",
                     "ass cum": "cowboy shot, from behind, looking back, solo focus, bedroom",
                     "pussy cum": "cowboy shot, from above, lying, on bed, solo focus, bedroom",
                     "swallow": "close-up, from above, pov, looking up, solo focus, bedroom",
                     "mouth cum": "close-up, from above, pov, looking up, solo focus, bedroom"}


def _mood_base(mood: str) -> str:
    return re.sub(r"\s*\d+$", "", mood.strip().lower())


def _mood_look(mood: str, adult: bool = False, s: dict | None = None) -> str:
    base = _mood_base(mood)
    if (own := _story_mood(s, mood, "mood_looks")):
        return own
    mine = {str(k).strip().lower(): str(v).strip() for k, v in (store.load_config().get("mood_looks") or {}).items() if str(v).strip()}
    return mine.get(base) or (adult and MOOD_LOOKS_ADULT.get(base)) or MOOD_LOOKS.get(base, f"{base} expression")


# ---------------- adult (NSFW) picture maker: off by default ----------------
EXPLICIT_RE = re.compile(
    r"\b(nude|nudity|naked|topless|bottomless|unclothed|undress(?:ed|ing)?|breasts?|nipples?|genitals?|penis|vagina|pussy|"
    r"sex|sexual(?:ly)?|intercourse|making love|orgasm\w*|aroused|arousal|erotic|explicit|nsfw|lewd|"
    r"blowjob|fellatio|cunnilingus|masturbat\w*|cum|stripping|stripped|strips off|showering|in the shower|bathing|bathes|undressing)\b", re.IGNORECASE)
# The editable list (🖼 Art → Picture makers → Adult pictures). Used whenever that box is empty. Whole words or phrases; chosen so ordinary scenes
# ("a shower of sparks", "thrusts his sword", "oral report", "the strip mall") do not trigger it.
NSFW_TYPES_DEFAULT = ("nude, naked, nudity, topless, bottomless, undressed, undressing, unclothed, disrobing, disrobed, stripping, stripped, strips off, "
                      "lingerie, underwear, panties, bra, thong, see-through, wet t-shirt, towel only, covered only by a sheet, "
                      "breasts, nipples, buttocks, bare bottom, genitals, penis, vagina, crotch, "
                      "aroused, arousal, horny, lust, lustful, sexy, seductive, sensual, bedroom eyes, "
                      "sex, sexual, intercourse, making love, lovemaking, foreplay, orgasm, climaxing, "
                      "erotic, explicit, nsfw, lewd, kinky, fetish, bondage, bdsm, "
                      "showering, in the shower, bathing, skinny dipping, in bed together, under the covers, "
                      "straddling, spread legs, groping, fondling, "
                      "oral sex, blowjob, fellatio, cunnilingus, handjob, masturbating, fingering herself, fingering himself, "
                      "cum, ejaculation, threesome, orgy")
NSFW_WORDS_TAGS = "nsfw, explicit, uncensored, nude"
NSFW_WORDS_PONY = "rating_explicit, nsfw, explicit, uncensored, nude"
NSFW_WORDS_NATURAL = "Explicit, uncensored adult image: nudity and sexual content shown openly, adults only."


def _is_explicit(text: str, cfg: dict | None = None) -> bool:
    """True when a scene description or mood names nudity or sexual content (only checked in stories with adult content on). Your own words from
    🖼 Art → Picture makers → Adult (NSFW) pictures count too (whole words or phrases, any case)."""
    if EXPLICIT_RE.search(text or ""):
        return True
    own = [w.strip().lower() for w in re.split(r"[,\n]", str(((cfg or {}).get("image_nsfw") or {}).get("types") or NSFW_TYPES_DEFAULT)) if w.strip()]
    low = (text or "").lower()
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in own)


def _nsfw_provider(cfg: dict, s: dict) -> dict | None:
    """The separate maker for explicit pictures (🖼 Art → Picture makers), or None when it is off, not set, or the story has adult content off."""
    ns = cfg.get("image_nsfw") or {}
    if not (ns.get("on") and s.get("nsfw") and "|" in (ns.get("maker") or "")):
        return None
    pid, model = ns["maker"].split("|", 1)
    prov = next((p for p in cfg.get("image_providers", []) if p["id"] == pid), None)
    if not prov:
        return None
    if prov.get("type") == "comfyui":
        if _is_flux_model(model):
            return {**_flux_provider(cfg, s, model), "id": pid, "nsfw_maker": True}
        # Explicit pictures use their OWN settings for this model (🖼 Art → Picture makers → Adult pictures), never the model's ordinary ones: tuned so the face
        # follows the character's first picture (Normal 1). Defaults: a strong face match from that one picture, cropped to the face.
        own = dict(((ns.get("tweaks") or {}).get(model)) or {})
        direction = ((s.get("art") or {}).get("direction") or "").lower()
        if (ns.get("style_mode") or "match") == "match" and re.search(r"photo|realistic", direction) and not re.search(r"semi-?realistic|paint|illustrat|anime|cartoon|comic|watercolor", direction):
            # A photorealistic story: this model's extra style words (kept per model, tuned for another story) must not fight it: painterly words are dropped from the extra positive
            # words, and "photo" words from the extra negative ones.
            split = lambda t: [w for w in re.split(r"\s*,\s*", t or "") if w]
            own["extra_positive"] = ", ".join(w for w in split(own.get("extra_positive")) if not re.search(r"paint|illustrat|anime|cartoon|semi-?realistic|brush|cel|sketch", w, re.IGNORECASE))
            own["extra_negative"] = ", ".join(w for w in split(own.get("extra_negative")) if not re.fullmatch(r"(photos?|photograph|photographic|photorealistic|realistic|3d(?: render)?|render)", w, re.IGNORECASE))
        tw = {"ref_end": "0.95", "ref_crop": "face", "ref_count": "1", **own}
        if not own.get("ref_weight"):
            tw["ref_weight"] = str(float(ns.get("face") or 0.85))
        if not (own.get("style_override") or "").strip() and (ns.get("style_mode") or "match") == "own" and (ns.get("style") or "").strip():
            tw["style_override"] = ns["style"].strip()      # (older setting: one style for every model)
        if _remembered_style(prov, model):
            tw["prompt_style"] = _remembered_style(prov, model)       # the explicit settings of a model replace its ordinary ones, but its remembered prompt style carries over
        return {**prov, "model": model, "prompt_style": _remembered_style(prov, model) or ("natural" if _model_family(model) == "real" else "tags"), "nsfw_maker": True,
                "ref_mode": "one_character", "model_tweaks": {**(prov.get("model_tweaks") or {}), model: tw}}
    return {**prov, "model": model or prov.get("model", ""), "nsfw_maker": True}


NSFW_START = {"ref_weight": "0.85", "ref_end": "0.95", "ref_crop": "face", "ref_count": "1"}   # where a model's explicit-picture settings start


def _nsfw_art(art: dict, prov: dict) -> dict:
    """Explicit pictures with "use a different art style" on: the style you typed replaces the story's art direction (the face still follows Normal 1)."""
    o = ((prov.get("model_tweaks") or {}).get(prov.get("model", "")) or {}).get("style_override") if prov.get("nsfw_maker") else ""
    return {**art, "direction": o} if o else art


def _nsfw_words(cfg: dict, prov: dict) -> str:
    own = ((cfg.get("image_nsfw") or {}).get("words") or "").strip()
    if own:
        return own
    if _wants_tags(prov):
        return NSFW_WORDS_PONY if _model_family(prov.get("model", "")) == "pony" else NSFW_WORDS_TAGS
    return NSFW_WORDS_NATURAL


def _add_nsfw_words(prompt: str, negative: str, prov: dict, words: str) -> tuple[str, str]:
    """Explicit words go first for tag models (they weigh early tags most) and at the end for sentence models; words that forbid nudity leave the negative prompt."""
    prompt = f"{words}, {prompt}" if _wants_tags(prov) else f"{prompt}\n\n{words}"
    prompt = prompt.replace(images.SAFE_LINE, "").strip()
    keep = [x for x in re.split(r"\s*,\s*", negative or "") if x and not re.search(r"nsfw|nud|naked|explicit|sexual|cleavage|suggestive", x, re.IGNORECASE)]
    return prompt, ", ".join(keep)


def _remembered_style(prov: dict, model: str | None = None) -> str:
    """The prompt style (tags or natural sentences) remembered for this picture model (Art → Picture makers: choose it once and it follows the model), or ''."""
    m = model if model is not None else prov.get("model", "")
    v = ((prov.get("model_tweaks") or {}).get(m) or {}).get("prompt_style")
    return v if v in ("tags", "natural") else ""


def _wants_tags(prov: dict) -> bool:
    """Anime SDXL models (Animagine, Illustrious, NoobAI...) follow short Danbooru-style tags better than sentences. What was remembered for the model wins over the service-wide setting."""
    mem = _remembered_style(prov)
    if mem:
        return mem == "tags"
    st, was = prov.get("prompt_style"), prov.get("prompt_style_model")
    if st and (not was or was == prov.get("model")):        # the service-wide style was set for this very model (or before models were told apart)
        return st == "tags"
    if prov.get("type") == "comfyui":                       # another model than the one the style was set for: the usual style for its kind
        return _model_family(prov.get("model", "")) != "real"
    return False


async def _tag_prompt(s: dict, cfg: dict, art: dict, scene: str, chars: list[dict]) -> tuple[str, str]:
    """(positive, negative) for a tag-style image model: the text model turns the description into tags."""
    tags = await _tag_line(s, cfg, art, scene, chars)
    quality, neg_extra = _tag_quality()
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), tags, quality) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE, neg_extra) if x)
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
    "combat": "fighting, attacking, lunging, weapon swing, clenched teeth, glaring, battle damage, dirt on face, sweat, motion lines",
    "cute": "cute, blush, playful smile, wink, head tilt, hands near face",
    "dead": "dead, corpse, lifeless eyes, empty eyes, half-closed eyes, open mouth, pale skin, limp, sprawled",
    "zombie": "undead, grey skin, glowing red eyes, torn clothes, slack jaw, hunched, lurching",
    "strike": "attacking, punching, kicking, sword swing, clenched teeth, glaring, dynamic pose, motion lines, motion blur",
    "guard": "blocking, guarding, raised arms, crossed arms in front, bracing, narrowed eyes, flinching, debris",
    "dodge": "dodging, leaning back, twisting body, wide eyes, hair flying, dynamic pose, motion lines",
    "charge": "running, charging, leaning forward, open mouth, shouting, weapon raised, wind, motion lines, dynamic pose",
    "leap": "jumping, leaping, midair, outstretched limbs, wind, flying hair, dynamic angle",
    "aim": "aiming, holding weapon, one eye closed, focused, steady stance, serious",
    "cast": "casting spell, magic, glowing hands, magic circle, glowing eyes, wind, floating hair, concentrating",
    "draw weapon": "drawing weapon, hand on hilt, unsheathing, narrowed eyes, tense, ready stance",
    "wounded": "injury, bleeding, clutching side, pain, grimace, torn clothes, dirt, sweat, kneeling on one knee",
    "knocked down": "falling, knocked back, open mouth, outstretched arms, dust, debris, dynamic angle",
    "defeated": "kneeling, head down, defeated, bruises, panting, hair over face, broken weapon",
    "exhausted": "exhausted, panting, hands on knees, bent over, sweat, hair stuck to face, tired",
    "flee": "running, fleeing, looking back, scared, wide eyes, hair flying, motion lines",
    "sneak": "crouching, sneaking, finger to lips, shadows, narrowed eyes, tense",
    "battle cry": "shouting, battle cry, open mouth, roaring, clenched fist, raised weapon, glaring, veins",
    "victory": "triumphant, victory pose, raised fist, grin, panting, standing, wind",
    "aroused": "blush, flushed face, shy, looking aside, nervous",
    "seductive": "smile, half-closed eyes, looking at viewer, head tilt, confident",
    "undressing": "taking off jacket, hands at collar, looking aside",
    "lingerie": "robe, pajamas, hand on hip, bedroom",
    "showering": "towel, wet hair, steam, bathroom, covered",
    "bathing": "bubble bath, steam, wet hair, foam",
    "nude": "wrapped in sheet, bedsheet, bare shoulders, tasteful, covered",
    "climaxing": "happy, laughing, closed eyes, head back, big smile",
    "afterglow": "lying, bed, blanket, messy hair, smile, half-closed eyes",
}
MOOD_TAGS_ADULT = {"aroused": "blush, flushed face, parted lips, heavy-lidded eyes, panting, looking at viewer",
                   "seductive": "seductive smile, half-closed eyes, looking at viewer, head tilt, bare shoulder, leaning forward",
                   "undressing": "undressing, clothes pull, shirt lift, bare shoulders, looking at viewer",
                   "lingerie": "lingerie, hand on hip, looking at viewer, bedroom",
                   "showering": "shower, wet, wet hair, steam, water drops, bare shoulders, closed eyes",
                   "bathing": "bath, bathing, wet, steam, bare shoulders, water, flushed",
                   "nude": "nude, bare shoulders, looking at viewer, soft lighting",
                   "climaxing": "ahegao, closed eyes, open mouth, blush, sweat, arched back, flushed",
                   "afterglow": "lying, bed, blush, sweat, messy hair, sheet, smile, half-closed eyes",
                   "riding cowgirl": "cowgirl position, girl on top, straddling, spread legs, arched back, sweat, blush, open mouth, half-closed eyes, looking at viewer, bare skin",
                   "riding reverse cowgirl": "reverse cowgirl position, girl on top, straddling, from behind, looking back, arched back, sweat, blush, bare back",
                   "doggy-style": "doggystyle, all fours, hips up, arched back, looking back, gripping sheets, sweat, blush, open mouth, bare back",
                   "blowjob": "fellatio, oral, kneeling, looking up, cheek bulge, saliva, blush, half-closed eyes, hand on thigh",
                   "missionary": "missionary, lying, on back, on bed, legs up, spread legs, arched back, gripping sheets, sweat, blush, open mouth, half-closed eyes",
                   "anal": "anal, doggystyle, all fours, hips up, arched back, looking back, gripping sheets, sweat, blush, open mouth, bare back",
                   "creampie": "creampie, cum in pussy, cum drip, cum on thighs, lying, on back, spread legs, on bed, sweat, blush, panting, half-closed eyes",
                   "anal creampie": "anal creampie, cum in ass, cum drip, all fours, hips up, looking back, sweat, blush, panting",
                   "face cum": "facial, cum on face, cum on mouth, cum on eyelashes, kneeling, looking up, blush, half-closed eyes, tired smile",
                   "ass cum": "cum on ass, cum on back, all fours, hips up, from behind, looking back, sweat, blush",
                   "pussy cum": "cum on pussy, cum on thighs, cum drip, lying, spread legs, on bed, sweat, blush, panting, half-closed eyes",
                   "swallow": "swallowing, open mouth, tongue out, after fellatio, kneeling, looking up, saliva, blush, half-closed eyes",
                   "mouth cum": "cum in mouth, open mouth, tongue out, cum on tongue, kneeling, looking up, blush, half-closed eyes",
                   "dead": "dead, corpse, blood, blood pool, blood on face, wound, injury, lifeless eyes, empty eyes, pale skin, limp, sprawled",
                   "combat": "fighting, attacking, lunging, weapon swing, clenched teeth, glaring, blood, blood splatter, cuts, injury, battle damage, torn clothes, dirt, sweat, motion lines"}
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


async def _portrait_tag_prompt(s: dict, cfg: dict, art: dict, ch: dict, mood: str, notes: str = "", sex_pic: bool = False) -> tuple[str, str]:
    """(positive, negative) for a tag-style portrait without a per-picture text-model call: the character's saved tags + the tags for the mood. Moods that
    are not built in, or that you reworded in Settings, and drawings with extra notes still go through the text model."""
    base = re.sub(r"\s*\d+$", "", mood.strip().lower())
    mine = {str(k).strip().lower(): str(v).strip() for k, v in (cfg.get("mood_looks") or {}).items() if str(v).strip()}
    adult = bool(s.get("nsfw"))
    frame = _framing(mood, tags=True, s=s) or "upper body, looking at viewer"
    def framed(pn: tuple[str, str]) -> tuple[str, str]:
        """Action pictures written by the helper AI: its portrait-framing tags go, the action framing is always there."""
        if not _is_action(mood):
            return pn
        tags = [x for x in re.split(r"\s*,\s*", pn[0]) if x and not re.search(r"^(upper body|portrait|close-up|looking at viewer|cowboy shot|face focus)$", x, re.IGNORECASE)]
        return ", ".join(tags + [frame]), pn[1]
    if notes or (mood != "default" and (base in mine or (base not in MOOD_TAGS and not (adult and base in MOOD_TAGS_ADULT)) or _story_mood(s, mood, "mood_looks"))):
        look = f"character portrait, {frame}" + ("" if mood == "default" else f", {_mood_look(mood, adult, s)}") + (f", {notes}" if notes else "")
        return framed(await _tag_prompt(s, cfg, art, look, [ch]))
    tags = await _character_tags(s, cfg, art, ch)
    if not tags:
        return framed(await _tag_prompt(s, cfg, art, f"character portrait, {frame}" + ("" if mood == "default" else f", {_mood_look(mood, adult, s)}"), [ch]))
    parts = [t.strip() for t in tags.split(",") if t.strip()]
    if sex_pic:          # the character's everyday clothing tags would dress a picture that is meant to be undressed
        parts = [t for t in parts if not _CLOTHES.search(t) and not re.search(r"\b(bag|purse|necklace|bracelet|backpack|earrings?|watch)\b", t, re.I)]
    head = [parts.pop(0)] if parts and _SUBJECT_TAG.match(parts[0]) else []
    parts = [t for t in parts if not _SUBJECT_TAG.match(t)]
    mood_tags = (adult and MOOD_TAGS_ADULT.get(base)) or MOOD_TAGS["normal" if mood == "default" else base]
    quality, neg_extra = _tag_quality()
    if _is_action(mood):      # an action shot or a body on the ground: drop the saved portrait-framing tags
        parts = [t for t in parts if not re.search(r"upper body|portrait|close-up|cowboy shot|looking at viewer", t, re.IGNORECASE)]
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), ", ".join(head), mood_tags, ", ".join(parts), frame, quality) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE, neg_extra) if x)
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
    quality, neg_extra = _tag_quality()
    positive = ", ".join(x for x in ((art.get("direction") or "").strip(), _subject_count(heads), *bodies, scene_tags, quality) if x)
    negative = ", ".join(x for x in ((art.get("negative") or "").strip(), TAG_NEGATIVE, neg_extra) if x)
    return positive, negative


def _scene_chars(s: dict, text: str, names: list[str] | None) -> list[dict]:
    """Who is in the picture: the named characters (or any whose name appears in the text), plus the player's avatar."""
    want = {n.lower() for n in (names or [])}
    low = text.lower()
    out = []
    for c in s.get("cast", []):
        firsts = [c["name"].split()[0].lower()] + [a.split()[0].lower() for a in c.get("aka") or []]
        if c["name"].lower() in want or any(f in want or re.search(rf"\b{re.escape(f)}\b", low) for f in firsts) or any(a.lower() in want for a in c.get("aka") or []):
            out.append({"name": c["name"], "appearance": _full_appearance(s, c), "slug": c["slug"], "ref_url": None, "ref": False, "firsts": firsts})
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
    if not scene and ((s.get("art") or {}).get("scene_template") or _local_on(cfg, _role(cfg, s, "small")[0])):         # no AI call: built from the reply's portrait tags, place and text
        scene, found, hints = _template_scene(s, turn["text"])
        names = names or found
    if not scene:
        scene, found, hints = await _describe_scene(s, cfg, turn["text"])
        names = names or found
    chars = _scene_chars(s, scene + " " + " ".join(names or []), names)
    by_slug = {c["slug"]: c for c in s.get("cast", [])}
    if not body.provider:      # a character with their own picture maker (an alien drawn by FLUX, say) decides how the whole picture is drawn
        own = next((by_slug[c["slug"]] for c in chars if c.get("slug") and _maker_spec(s, by_slug[c["slug"]])), None)
        if own:
            prov = _maker_provider(cfg, s, own)
        if _is_explicit(scene, cfg) and (ns := _nsfw_provider(cfg, s)):
            prov = ns           # an explicit moment in an adult story: drawn by the adult picture maker
    _model_ctx.set(prov.get("model", ""))
    max_refs = int(cfg.get("image_max_refs", 3))
    mode = prov.get("ref_mode") or ("one_character" if prov.get("type") == "comfyui" else "characters")
    refs: list[tuple[bytes, str]] = []
    if mode == "one_character":
        for c in chars:
            ch = by_slug.get(c.get("slug"))
            got = _load_refs(_portrait_urls(ch, hints.get(c["name"].lower())), max_refs, prov) if ch else []
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
            got = _load_refs(urls, 1, prov)
            if got:
                refs.append(got[0]); c["ref"] = True
    art = _nsfw_art(s["art"], prov)
    adult_sdxl = bool(prov.get("nsfw_maker")) and prov.get("type") == "comfyui" and not _is_flux_model(prov.get("model", ""))
    if adult_sdxl:      # an explicit picture: what they wear comes from the scene, not from their everyday outfit
        prov = {**prov, "_tight_face": True}
        chars = [{**c, "appearance": _body_look(c.get("appearance", "")) or c.get("appearance", "")} for c in chars]
    prompt = images.scene_prompt(art, scene, chars, max_refs, nsfw=bool(s.get("nsfw")), clothes_from_ref=not adult_sdxl)
    negative = art.get("negative", "")
    if _wants_tags(prov):
        prompt, negative = await _scene_tag_prompt(s, cfg, art, scene, chars)
    if prov.get("nsfw_maker"):
        prompt, negative = _add_nsfw_words(prompt, negative, prov, _nsfw_words(cfg, prov))
    try:
        shape = "landscape" if str(art.get("scene_where", "")).startswith("banner") else art.get("size", "portrait")   # a wide strip needs a wide picture
        if body.shape in images.SHAPES:
            shape = body.shape
        scale = {"large": 1.25, "xl": 1.5}.get(art.get("scene_scale"), 0)
        data, ext = await images.generate({**prov, "_scale": scale} if scale and prov.get("type") == "comfyui" else prov, prompt, negative, shape, refs)
    except images.ImageError as e:
        raise HTTPException(502, str(e))
    img = {"url": _save_scene_image(sid, data, ext), "scene": scene, "provider": prov["id"], "ts": time.time(), **({"shape": body.shape} if body.shape in images.SHAPES else {})}
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


@app.get("/api/images/nsfw-types")
def nsfw_types_default():
    """The built-in list of picture types and words that count as explicit (shown in the Adult pictures box)."""
    return {"types": NSFW_TYPES_DEFAULT}


@app.get("/api/images/nsfw-tuned")
def nsfw_tuned_for(model: str):
    """The starting settings for explicit pictures with this model (the tuned ones for its family), shown in the Adult pictures settings."""
    return {"family": _model_family(model), "flux": _is_flux_model(model), "tweaks": NSFW_START}


@app.get("/api/images/bg-models")
async def bg_models():
    """Models the local ComfyUI has that a separate backdrop maker can use: diffusion models the app knows (FLUX.2, Chroma, Qwen-Image) and ordinary checkpoints."""
    from . import catalog
    cfg = store.load_config()
    comfy = next((p for p in cfg.get("image_providers", []) if p.get("type") == "comfyui"), None)
    base = ((comfy or {}).get("base_url") or "http://127.0.0.1:8188").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            info = (await c.get(f"{base}/object_info/UNETLoader")).json()
        unets = [m for m in info["UNETLoader"]["input"]["required"]["unet_name"][0] if catalog.diffusion_kind(m)]
        cks = []
        try:
            async with httpx.AsyncClient(timeout=6) as c:
                cks = (await c.get(f"{base}/object_info/CheckpointLoaderSimple")).json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
        except Exception:
            pass
        await catalog.comfy_checkpoints({"base_url": base})       # also refreshes the text-encoder / VAE lists the workflows are fitted to
        stock = [_stock_workflow(m) for m in unets + list(cks)]
        return {"models": sorted(unets) + sorted(cks), "kinds": {m: catalog.diffusion_kind(m) or "checkpoint" for m in unets + list(cks)}, "stock": stock, "up": True}
    except Exception:
        return {"models": [], "kinds": {}, "stock": [], "up": False}


@app.get("/api/images/model-workflow")
def model_workflow(model: str):
    """The built-in workflow for one model (what the backdrop-maker box fills in when you pick it)."""
    return json.loads(_stock_workflow(model))


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
    if kind in ("face", "flux", "fluxref"):
        f = ROOT / "workflows" / {"face": "comfyui-ipadapter-face.json", "flux": "comfyui-flux2-klein.json", "fluxref": "comfyui-flux2-klein-ref.json"}[kind]
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
def delete_story(sid: str, art: int = 1):
    """art=1 (default) also deletes the story's pictures; art=0 keeps them on disk."""
    _story(sid)
    store.delete_story(sid, art=bool(art))
    return {"ok": True}


@app.get("/api/stories/{sid}/art-count")
def story_art_count(sid: str):
    """How many picture files this story has (portraits, earlier versions, places, scenes, cover, backdrop, intro panels), for the delete dialog."""
    _story(sid)
    n = 0
    for d in (store.PORTRAITS / sid, store.IMAGES / sid):
        n += sum(1 for f in d.rglob("*") if f.is_file() and "_candidates" not in f.parts) if d.exists() else 0
    return {"files": n}


def _dir_size(d: Path, scratch: bool | None = None) -> int:
    """Bytes in a folder. scratch=True counts only unchosen option pictures, False everything else, None all."""
    n = 0
    if d.exists():
        for f in d.rglob("*"):
            if f.is_file() and (scratch is None or store.is_scratch(f.relative_to(d)) == scratch):
                try:
                    n += f.stat().st_size
                except OSError:
                    pass
    return n


@app.get("/api/deleted-stories")
def deleted_stories():
    """Stories that were deleted but still have a backup (made automatically before every delete), newest first."""
    out = []
    for d in store.AUTO.iterdir() if store.AUTO.exists() else []:
        if not d.is_dir() or (store.STORIES / f"{d.name}.json").exists():
            continue
        snaps = sorted(d.glob("*.json"))
        if not snaps:
            continue
        try:
            s = json.loads(snaps[-1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"id": d.name, "title": s.get("title", "?"), "turns": len(s.get("turns", [])) // 2, "when": snaps[-1].stat().st_mtime,
                    "pictures_kept": (store.PORTRAITS / d.name).exists() or (store.IMAGES / d.name).exists()})
    return sorted(out, key=lambda x: -x["when"])


def _deleted_dir(sid: str) -> Path:
    if not re.fullmatch(r"[0-9a-z]+", sid) or (store.STORIES / f"{sid}.json").exists() or not (store.AUTO / sid).is_dir():
        raise HTTPException(404, "No deleted story with that id")
    return store.AUTO / sid


def _picture_exists(url: str) -> bool:
    u = str(url or "").split("?")[0]
    f = _portrait_file(u) if u.startswith("/portraits/") else _local_file(u, store.IMAGES, "/images/") if u.startswith("/images/") else None
    return bool(f and f.exists())


def _strip_missing_pictures(s: dict) -> int:
    """Forget picture links whose files are gone (for example a story restored after its pictures were deleted), so nothing shows as a broken image and a package can put them back."""
    n = 0
    def keep(u):
        nonlocal n
        ok = _picture_exists(u)
        n += 0 if ok else 1
        return ok
    for c in s.get("cast", []):
        c["moods"] = {m: u for m, u in (c.get("moods") or {}).items() if keep(u)}
        c["alts"] = {m: [u for u in us if keep(u)] for m, us in (c.get("alts") or {}).items()}
        c["alts"] = {m: us for m, us in c["alts"].items() if us}
    for p in s.get("places", []):
        if p.get("url") and not keep(p["url"]):
            p["url"] = ""
    for sp in (s.get("species") or {}).values():
        if sp.get("reference_url") and not keep(sp["reference_url"]):
            sp["reference_url"] = ""
    for key in ("cover", "avatar_image"):
        if s.get(key) and not keep(s[key]):
            s[key] = ""
    if (s.get("backdrop") or {}).get("url") and not keep(s["backdrop"]["url"]):
        s["backdrop"] = {}
    s["backdrops"] = {k: v for k, v in (s.get("backdrops") or {}).items() if not (isinstance(v, dict) and v.get("url")) or keep(v["url"])}
    s["intro_images"] = [i for i in s.get("intro_images", []) if keep(i.get("url"))]
    for t in s.get("turns", []):
        for holder in [t] + list(t.get("attempts") or []):
            if holder.get("images"):
                holder["images"] = [im for im in holder["images"] if keep(im.get("url"))]
    if (s.get("now") or {}).get("image") and not keep(s["now"]["image"]):
        s["now"]["image"] = ""
    return n


@app.post("/api/deleted-stories/{sid}/restore")
def restore_deleted_story(sid: str):
    """Bring a deleted story back from its newest backup (its pictures too, if they were kept)."""
    d = _deleted_dir(sid)
    snap = sorted(d.glob("*.json"))[-1]
    s = json.loads(snap.read_text(encoding="utf-8"))
    s["id"], s["updated"] = sid, time.time()
    _strip_missing_pictures(s)
    store.save_story(s)
    return _story(sid)


@app.delete("/api/deleted-stories/{sid}")
def forget_deleted_story(sid: str):
    """Permanently remove a deleted story's backups and any pictures kept for it."""
    d = _deleted_dir(sid)
    shutil.rmtree(d, ignore_errors=True)
    shutil.rmtree(store.PORTRAITS / sid, ignore_errors=True)
    shutil.rmtree(store.IMAGES / sid, ignore_errors=True)
    return {"ok": True}


@app.get("/api/storage")
def storage_report():
    """Disk use per story and the things that can be cleaned up."""
    stories = []
    for p in store.STORIES.glob("*.json"):
        sid = p.stem
        try:
            title = json.loads(p.read_text(encoding="utf-8")).get("title", sid)
        except (OSError, ValueError):
            title = sid
        stories.append({"id": sid, "title": title, "story": p.stat().st_size, "pictures": _dir_size(store.PORTRAITS / sid, False) + _dir_size(store.IMAGES / sid),
                        "options": _dir_size(store.PORTRAITS / sid, True), "backups": _dir_size(store.AUTO / sid)})
    live = {s["id"] for s in stories}
    kept = [{"id": d.name, "bytes": _dir_size(d) + _dir_size(store.IMAGES / d.name)} for d in store.PORTRAITS.iterdir()
            if d.is_dir() and not d.name.startswith("_") and d.name not in live]
    kept += [{"id": d.name, "bytes": _dir_size(d)} for d in store.IMAGES.iterdir() if d.is_dir() and d.name not in live and not (store.PORTRAITS / d.name).exists()]
    return {"stories": sorted(stories, key=lambda x: -(x["pictures"] + x["story"])), "kept_pictures": kept,
            "options": _dir_size(store.PORTRAITS, True), "thumbs": _dir_size(store.DATA / "thumbs"),
            "avatars": _dir_size(store.PORTRAITS / "_avatars")}


class CleanIn(BaseModel):
    what: str      # options | thumbs | kept


@app.post("/api/storage/clean")
def storage_clean(body: CleanIn):
    """options: unchosen picture options; thumbs: the small-picture cache (rebuilt as needed); kept: pictures kept from deleted stories."""
    if body.what == "options":
        store.clean_scratch(0)
    elif body.what == "thumbs":
        shutil.rmtree(store.DATA / "thumbs", ignore_errors=True)
    elif body.what == "kept":
        for k in storage_report()["kept_pictures"]:
            shutil.rmtree(store.PORTRAITS / k["id"], ignore_errors=True)
            shutil.rmtree(store.IMAGES / k["id"], ignore_errors=True)
    else:
        raise HTTPException(400, "Unknown clean-up")
    return storage_report()


@app.get("/api/stories/{sid}/export", response_class=PlainTextResponse)
def export_story(sid: str):
    s = _story(sid)
    lines = [f"# {s['title']}", s.get("subtitle", ""), ""]
    for t in s["turns"]:
        lines.append(("> " + t["text"]) if t["role"] == "user" else t["text"])
        lines.append("")
    return "\n".join(lines)


def _drop_undone(s: dict) -> None:
    """Forget the exchange kept for Redo (and delete its pictures)."""
    u = s.pop("undone", None)
    for t in (u or {}).get("turns", []):
        for im in t.get("images", []) + [i for a in t.get("attempts", []) for i in a.get("images", [])]:
            _delete_image_file(im.get("url", ""))


@app.post("/api/stories/{sid}/redo")
def redo(sid: str):
    """Put back the exchange the last Undo removed (only until a new move is made)."""
    s = _story(sid)
    u = s.get("undone")
    if not u:
        raise HTTPException(400, "Nothing to redo.")
    s.pop("undone")
    s["turns"].extend(u["turns"])
    ex = u.get("extras") or {}
    known = {c["slug"] for c in s.get("cast", [])}
    s.setdefault("cast", []).extend(c for c in ex.get("chars", []) if c["slug"] not in known)
    by = {c["slug"]: c for c in s.get("cast", [])}
    for slug, evs in (ex.get("events") or {}).items():
        if slug in by:
            by[slug].setdefault("events", []).extend(evs)
    if u.get("after"):
        prompting.restore(s, u["after"])
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/undo")
def undo(sid: str):
    """Remove the last exchange (player input + narrator reply) and restore the state from before it."""
    s = _story(sid)
    _drop_undone(s)
    if s["turns"] and s["turns"][-1]["role"] == "assistant":
        last = s["turns"].pop()
        idx = len(s["turns"])
        user = s["turns"].pop() if s["turns"] and s["turns"][-1]["role"] == "user" else None
        # kept for Redo until the next move: the exchange, its pictures, the side-panel state after it, and what it left on the cast
        s["undone"] = {"turns": [x for x in (user, last) if x], "after": prompting.snapshot(s), "extras": _reply_extras(s, last, idx, True)}
        if "before" in last:
            prompting.restore(s, last["before"])
    elif s["turns"] and s["turns"][-1]["role"] == "user":
        s["turns"].pop()
    s["summary_upto"] = min(s.get("summary_upto", 0), len(s["turns"]))
    store.save_story(s)
    return s


class RetryIn(BaseModel):
    keep_chars: bool = False        # keep characters the discarded reply introduced
    keep_events: bool = False       # keep what it logged on character cards
    keep_state: bool = False        # keep its side-panel / status changes


def _reply_extras(s: dict, turn: dict, idx: int, remove: bool, keep_chars: bool = False, keep_events: bool = False) -> dict:
    """What an assistant reply left behind outside its text: new cast members and automatic card events. With remove=True they are taken out of the story
    (so a retry starts from the previous turn only) and returned, so a swap can put them back."""
    out = {"chars": [], "events": {}}
    if not keep_chars and "cast_before" in turn:
        known = set(turn["cast_before"])
        gone = [c for c in s.get("cast", []) if c["slug"] not in known and not c.get("moods") and not c.get("player")]
        out["chars"] = gone
        if remove:
            s["cast"] = [c for c in s["cast"] if c not in gone]
    if not keep_events:
        for c in s.get("cast", []):
            mine = [e for e in c.get("events", []) if e.get("auto") and e.get("turn") == idx]
            if mine:
                out["events"][c["slug"]] = mine
                if remove:
                    c["events"] = [e for e in c["events"] if e not in mine]
    return out


@app.post("/api/stories/{sid}/retry")
def retry_prepare(sid: str, body: RetryIn):
    """Step back to just before the last narrator reply for a retry. The discarded reply is kept as an attempt on the next reply (so it can be swapped back).
    The retry starts from the previous turn's information only: the discarded reply's side-panel changes, new characters and card events are taken out
    unless the player chose to keep them."""
    s = _story(sid)
    if not (s["turns"] and s["turns"][-1]["role"] == "assistant" and len(s["turns"]) >= 2):
        raise HTTPException(400, "There is no narrator reply to retry.")
    _drop_undone(s)
    idx = len(s["turns"]) - 1
    last = s["turns"].pop()
    user = s["turns"].pop()
    prev = last.get("attempts") or []
    for n, a in enumerate(prev):
        a.setdefault("attempt_no", n + 1)
    last.setdefault("attempt_no", len(prev) + 1)
    attempt = {k: v for k, v in last.items() if k != "attempts"}
    attempt["after"] = prompting.snapshot(s)
    attempt["extras"] = _reply_extras(s, last, idx, True, body.keep_chars, body.keep_events)
    if "before" in last and not body.keep_state:
        prompting.restore(s, last["before"])
    s["retry_pending"] = {"text": user["text"], "attempts": (last.get("attempts") or []) + [attempt]}
    s["summary_upto"] = min(s.get("summary_upto", 0), len(s["turns"]))
    store.save_story(s)
    return s


@app.post("/api/stories/{sid}/turns/{i}/attempt-step")
def step_attempt(sid: str, i: int, dir: int = 1):
    """◀ / ▶ on the newest reply: show the previous or next attempt (in the order they were written, wrapping around)."""
    s = _story(sid)
    if i != len(s["turns"]) - 1 or s["turns"][i]["role"] != "assistant" or not s["turns"][i].get("attempts"):
        raise HTTPException(400, "This reply has no other attempts.")
    cur, atts = s["turns"][i], s["turns"][i]["attempts"]
    ring = sorted([cur] + atts, key=lambda a: a.get("attempt_no", 0))
    target = ring[(ring.index(cur) + (1 if dir >= 0 else -1)) % len(ring)]
    return swap_attempt(sid, i, atts.index(target))


@app.post("/api/stories/{sid}/turns/{i}/attempt/{k}")
def swap_attempt(sid: str, i: int, k: int):
    """Swap the newest reply with one of its earlier attempts (restores that attempt's side-panel state, characters and card events)."""
    s = _story(sid)
    if i != len(s["turns"]) - 1 or s["turns"][i]["role"] != "assistant":
        raise HTTPException(400, "Only the newest reply can be swapped.")
    cur = s["turns"][i]
    atts = list(cur.get("attempts") or [])
    if not (0 <= k < len(atts)):
        raise HTTPException(404, "No such attempt.")
    chosen = atts.pop(k)
    # park the current reply
    parked = {x: v for x, v in cur.items() if x != "attempts"}
    parked["after"] = prompting.snapshot(s)
    parked["extras"] = _reply_extras(s, cur, i, True)
    atts.insert(k, parked)
    # bring the chosen one back
    ex = chosen.get("extras") or {}
    known = {c["slug"] for c in s.get("cast", [])}
    for c in ex.get("chars", []):
        if c["slug"] not in known:
            s["cast"].append(c)
    by = {c["slug"]: c for c in s.get("cast", [])}
    for slug, evs in (ex.get("events") or {}).items():
        if slug in by:
            by[slug].setdefault("events", []).extend(evs)
    if chosen.get("after"):
        prompting.restore(s, chosen["after"])
    new = {x: v for x, v in chosen.items() if x not in ("after", "extras")}
    new["attempts"] = atts
    s["turns"][i] = new
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
        if not ch and words:        # "Captain Ann Smith" for the cast entry "Captain Smith": every word of one name is in the other
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
        local = bool(cfg.get("local_mode")) and (narrator or {}).get("type") == "ollama"
        if not (local and over > keep + 2):
            return
        b = prompting.build(story)           # local mode: also fold history early when it would fill most of the model's window
        if _est_tokens(b.system_text) + sum(_est_tokens(m["content"]) for m in b.messages) < int(cfg.get("num_ctx", 8192)) * 0.7:
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


async def _draw_status(story: dict, cfg: dict, move: str, narration: str) -> str:
    """App-drawn status line (see appstatus.py): the reply with its status block added, or the reply unchanged when anything goes wrong."""
    try:
        labels = rules.expected_labels(story)
        if appstatus.find_block(narration, labels):
            return narration                      # the narrator wrote its own: leave it
        block = appstatus.last_block(story)
        if not block:
            return narration
        prov, model = _role(cfg, story, "small", "status line")
        system, user = appstatus.request(story, block, move, narration)
        out = await asyncio.wait_for(providers.complete(prov, model, system, [{"role": "user", "content": user}], temperature=0.1, max_tokens=500,
                                                        num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False}), 30)
        new, _n = appstatus.apply_reply(block, _json_in(out))
        return narration.rstrip() + "\n\n" + new
    except Exception:
        return narration


@app.post("/api/stories/{sid}/turn")
async def turn(sid: str, body: TurnIn):
    story = _story(sid)
    cfg = store.load_config()
    prov, model = _pick(cfg, body.provider, body.model) if (body.provider and body.model) else _role(cfg, story, "story")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty input")
    _command_note(story, body.cmds, body.cmd_opts)       # refuses "adult" while the story's adult content is off, before anything starts

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
            if story.get("undone"):
                _drop_undone(story)
            before = prompting.snapshot(story)
            cast_before = [c["slug"] for c in story.get("cast", [])]
            pend = story.get("retry_pending")
            if pend and pend.get("text", "").strip() != text:
                story.pop("retry_pending", None)       # a different move: the discarded attempts (and their pictures) are dropped
                for im in [i for a in pend.get("attempts", []) for i in a.get("images", [])]:
                    _delete_image_file(im.get("url", ""))
                pend = None
            cmds = [c for c in dict.fromkeys(body.cmds or []) if c in COMMANDS][:3]
            cmd_note = _command_note(story, cmds, body.cmd_opts)
            tr_ = story.setdefault("tracker", tracker.blank())
            pend_pts = tr_.pop("_spent", [])               # points the player spent since the last move: the narrator is told the final numbers
            tr_.pop("_lock", None)
            if pend_pts and (pf_ := tracker.points_field(story)):
                psec_, pk_ = pf_["id"].split(".", 1)
                after_ = tracker._num(tr_[psec_].get(pk_))
                tr_["_lock"] = {"stats": {r["id"]: tracker._num(tr_[r["id"].split(".", 1)[0]].get(r["id"].split(".", 1)[1])) for r in pend_pts if r["n"] > 0},
                                "points_before": (after_ or 0) + sum(r["n"] for r in pend_pts), "points_after": after_}
            roll = _clean_roll(body.roll) if body.roll else (rules.auto_roll(story, text) if (rules.dice_mode(story) == "auto" and not cmds) else None)
            story["turns"].append({"role": "user", "text": text, "ts": time.time(), **({"cmds": cmds, "cmd_opts": {"act_for_me": bool((body.cmd_opts or {}).get("act_for_me"))}} if cmds else {}),
                                   **({"pts": pend_pts} if pend_pts else {}), **({"roll": roll} if roll else {})})
            beats_note = beats.prepare(story, beats.moves(story))       # story beats that are due (hidden from the player; sent with this request only)
            _beats_persist(sid, story)
            built = prompting.build(story, beats_note=_hidden_notes(story, beats_note, pend_pts, roll), local=_steady_on(cfg))
            usage.set_ctx(sid, "story narration", why=_cache_why(sid, built, cfg))
            system, messages = built.request()
            trimmed, too_big = _fit_context(system, messages, cfg) if (cfg.get("local_mode") and prov.get("type") == "ollama") else (0, False)
            if cmd_note and messages and messages[-1]["role"] == "user":
                messages[-1]["content"] += "\n\n" + cmd_note
            if body.note.strip() and messages and messages[-1]["role"] == "user":
                messages[-1]["content"] += ("\n\n(OOC: This is a rewrite of your previous reply to the message above, which the player discarded. "
                                           "Write the reply again with these changes: " + body.note.strip()[:1500] + ")")
            full, use, cut_note = "", {}, ""
            async for chunk in providers.stream_chat(
                prov, model, system, messages,
                temperature=cfg["temperature"], max_tokens=cfg["max_tokens"], num_ctx=cfg["num_ctx"],
                **{**_extras(cfg), "narrator": True},
            ):
                if isinstance(chunk, providers.Usage):
                    use = dict(chunk)
                    continue
                full += chunk
                yield _sse({"t": "delta", "v": chunk})
            if use.get("cut") and _steady_on(cfg):
                full, did = localfix.trim_unfinished(full)
                if did:
                    cut_note = "The reply hit the length limit, so its unfinished last sentence was removed. Raise Max reply tokens in Settings if replies are being cut often."
            if cut_note:
                yield _sse({"t": "note", "v": cut_note})
            if use.get("truncated") or too_big:
                yield _sse({"t": "note", "v": "The story prompt is bigger than the model's window, so part of it may be cut off. Raise the context size in Settings (Models), or use a shorter story prompt."})
            elif trimmed:
                yield _sse({"t": "note", "v": f"Local mode left out the {trimmed} oldest messages of this move's history so the prompt fits the model's window. Raising the context size or summarizing sooner keeps more."})
            narration, upd = prompting.split_state(prompting.expand_tags(full))
            _add_new_chars(story, prompting.extract_newchars(narration))
            narration, scene = prompting.split_scene(narration)
            if scene and not prompting.scene_due(story):
                scene = ""                       # too soon after the last scene picture (Art → Scene pictures → turns between)
            if not narration:
                hint = " It spent its whole reply budget thinking: raise Max reply tokens or turn Claude thinking off in Settings." if use.get("thinking") else ""
                raise providers.ProviderError("The model returned an empty reply." + hint)
            untidied, tidy_notes = narration, []
            if story.get("format_fix", True):
                narration, tidy_notes = rules.tidy(story, narration)          # a stray or doubled status line is put where the story prompt wants it (free)
            if _steady_on(cfg):
                narration, _fixed = localfix.fix_format(narration)           # unclosed italics / bold and picture tags are closed
            if story.get("app_status"):
                narration = await _draw_status(story, cfg, text, narration)   # the app writes the status line (the narrator was told to leave it out)
            story["turns"].append(
                {"role": "assistant", "text": narration, "ts": time.time(), "model": f"{prov['id']}/{model}", "before": before, "cast_before": cast_before,
                 "usage": use, **({"tidied": tidy_notes, "untidied": untidied} if tidy_notes else {}), **({"scene": scene} if scene else {}), **({"attempts": pend["attempts"], "attempt_no": 1 + max(a.get("attempt_no", 0) for a in pend["attempts"])} if pend else {})}
            )
            story.pop("retry_pending", None)
            prompting.apply_state(story, upd)
            got_panel = tracker.update_from_reply(story, narration, len(story["turns"]) // 2)
            clock.advance(story, len(story["turns"]) // 2)        # the world clock moves on (a no-op when it is off, or already moved by the panel update)
            saved = _save_turn(story)
            _after_reply_free(saved)
            _maybe_track_with_ai(saved, len(saved["turns"]) - 1, got_panel)
            _start_auto_backdrop(saved["id"])
            _start_now(saved["id"])
            if (len(saved["turns"]) // 2) % 5 == 0:
                store.snapshot_story(saved["id"], "every 5 turns")
            asyncio.create_task(_events_after_turn(saved["id"], len(saved["turns"]) - 1, saved["turns"][-1].get("ts")))
            if saved.get("rule_check"):
                _check_tasks[saved["id"]] = asyncio.create_task(_rule_check(saved["id"], len(saved["turns"]) - 1, saved["turns"][-1].get("ts")))
            asyncio.create_task(_find_new_chars(saved["id"]))
            _note_cache_use(saved["id"], prov, model, cfg, use)
            yield _sse({"t": "done", "story": saved})
        except Exception as e:  # shown to the player; the turn is not saved
            bugreport.note("turn", str(e) or e.__class__.__name__, story=sid, error=e.__class__.__name__)
            yield _sse({"t": "error", "v": str(e) or e.__class__.__name__})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------------- share packs: prompt + cast + art settings, no save ----------------
PACK_FIELDS = ("title", "subtitle", "description", "base_prompt", "opening", "opening_in_context", "use_state_block", "app_status", "art", "art_dismissed", "group_cast",
               "prologue", "opening_options", "alt_openings", "opening_name", "endings", "sidebar", "beats", "lore", "clock", "dice", "commands")


@app.get("/api/stories/{sid}/pack")
def export_pack(sid: str, pictures: str = "all", avatar: int = 0, webp: int = 0):
    """A story package you can share: prompt, prologue, opening scene and suggested first moves, cast (descriptions, bios, personalities, portraits), suggested side-panel
    trackers, endings, art settings, cover, intro panels and backdrop. It never contains turns, memory, notes (your avatar sheet only if avatar=1, text only), API keys or usage. pictures = none | normal | all."""
    s = _story(sid)
    meta = {k: s.get(k) for k in PACK_FIELDS}
    b = beats.get(s)
    meta["beats"] = {"on": b["on"], "hold": b["hold"], "lookahead": b["lookahead"], "extracted": b["extracted"],
                     "items": [{k: it.get(k, "") for k in ("id", "title", "text", "field", "op", "value", "source", "off")} for it in b["items"]]}   # never which beats have gone out
    lb = lore.get(s)
    meta["lore"] = {"on": lb["on"], "items": [{k: it.get(k, "") for k in ("id", "title", "keys", "text", "always", "on", "priority")} for it in lb["items"]]}
    meta["format"] = "storyteller-pack-2"
    meta["app_version"] = APP_VERSION
    meta["cast"], files = [], []
    if avatar and (sheet := _clean_sheet(s.get("avatar") or {})):     # opt-in: the avatar sheet (text only, no picture) as a starter character
        meta["starter_avatar"] = sheet
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
                             "address": c.get("address", ""), "tags": c.get("tags", ""), "species": c.get("species", ""), "maker": c.get("maker", ""), "group": c.get("group", ""), "moods": moods})
    meta["species"] = []
    for key, sp in (s.get("species") or {}).items():
        item = {"name": sp.get("name", key), "description": sp.get("description", ""), "maker": sp.get("maker", "")}
        f = _portrait_file(sp["reference_url"]) if sp.get("reference_url") and pictures != "none" else None
        if f:
            item["file"] = f"species/{_slug(key)}{f.suffix}"
            files.append((item["file"], f))
        meta["species"].append(item)
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
    blobs: dict[str, bytes] = {}
    if webp:     # smaller package: pictures as WebP (high quality); the references inside story.json follow the new names
        from PIL import Image
        renamed = {}
        for arc, f in files:
            if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                try:
                    with Image.open(f) as im:
                        out = io.BytesIO()
                        im.convert("RGBA" if "A" in im.getbands() else "RGB").save(out, "WEBP", quality=90, method=4)
                    renamed[arc] = str(Path(arc).with_suffix(".webp"))
                    blobs[renamed[arc]] = out.getvalue()
                except Exception:
                    pass
        def swap(o):
            if isinstance(o, dict):
                return {k: swap(v) for k, v in o.items()}
            if isinstance(o, list):
                return [swap(v) for v in o]
            return renamed.get(o, o) if isinstance(o, str) else o
        meta = swap(meta)
        files = [(renamed.get(arc, arc), f) for arc, f in files]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("story.json", json.dumps(meta, indent=2, ensure_ascii=False))
        for arc, f in files:
            if arc in blobs:
                z.writestr(arc, blobs[arc])
            else:
                z.write(f, arc)
    name = _slug(s["title"])[:40] or "story"
    return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}-pack.zip"'})


APP_VERSION = (APP_DIR.parent / "VERSION").read_text(encoding="utf-8").strip() if (APP_DIR.parent / "VERSION").exists() else "0"


def _ver(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))[:3]) or (0,)


UPDATE_REPO = "deeboy814/AI-Storyteller"


@app.get("/api/update-check")
async def update_check():
    """Ask GitHub for the newest release. Only the player's own action asks for this (the Updates setting at start-up, or the Check now button);
    nothing about the player or their stories is sent, only an ordinary public request for the latest release."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.get(f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest", headers={"accept": "application/vnd.github+json", "user-agent": "AI-Storyteller-update-check"})
    except Exception:
        raise HTTPException(502, "Could not reach GitHub. Check your internet connection and try again.")
    if r.status_code == 404:
        return {"current": APP_VERSION, "latest": APP_VERSION, "newer": False, "url": f"https://github.com/{UPDATE_REPO}/releases", "notes": ""}
    if r.status_code != 200:
        raise HTTPException(502, f"GitHub answered {r.status_code}. Try again later.")
    d = r.json()
    latest = str(d.get("tag_name") or "").lstrip("vV")
    return {"current": APP_VERSION, "latest": latest, "newer": _ver(latest) > _ver(APP_VERSION), "url": d.get("html_url") or f"https://github.com/{UPDATE_REPO}/releases",
            "notes": str(d.get("body") or "")[:1500]}


def _open_pack(data: bytes) -> tuple[zipfile.ZipFile, dict, list[str]]:
    """Open a story package; also returns warnings (made by a newer app version)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        meta = json.loads(z.read("story.json"))
    except Exception:
        raise HTTPException(400, "That isn't a story pack made by this app.")
    if meta.get("format") not in ("storyteller-pack-1", "storyteller-pack-2"):
        raise HTTPException(400, "Unknown pack version.")
    notes = []
    if meta.get("app_version") and _ver(meta["app_version"]) > _ver(APP_VERSION):
        notes.append(f"This package was made with a newer version of AI Storyteller ({meta['app_version']}; you have {APP_VERSION}). Some parts may be missing: update the app and import it again.")
    raw = (meta.get("sidebar") or {}).get("custom") if isinstance(meta.get("sidebar"), dict) else None
    if isinstance(raw, list) and len(raw) > len(tracker.clean_custom(raw)):
        notes.append(f"The package has {len(raw)} side-panel trackers; this version keeps {len(tracker.clean_custom(raw))}.")
    return z, meta, notes


def _pack_saver(z: zipfile.ZipFile):
    def save_file(arc: str, dest: Path) -> bool:
        arc = str(arc)
        if ".." in arc or arc.startswith("/") or arc not in z.namelist():
            return False
        if z.getinfo(arc).file_size > MAX_IMAGE:
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(z.read(arc))
        return True
    return save_file


def _pack_char(c: dict, sid: str, save_file) -> dict:
    slug = _slug(c.get("slug") or c["name"])
    moods = {}
    for mood, arc in (c.get("moods") or {}).items():
        fname = f"{_slug(mood)}-{int(time.time() * 1000)}{Path(str(arc)).suffix}"
        if save_file(arc, store.PORTRAITS / sid / "chars" / slug / fname):
            moods[mood] = f"/portraits/{sid}/chars/{slug}/{fname}"
    return {"name": c["name"], "slug": slug, "appearance": c.get("appearance", ""), "bio": c.get("bio", ""), "personality": c.get("personality", ""),
            "author": bool(c.get("author")), "address": str(c.get("address", ""))[:600], "tags": str(c.get("tags", ""))[:1500], "species": str(c.get("species", ""))[:40],
            "maker": str(c.get("maker", ""))[:120], "group": c.get("group", ""), "events": [], "moods": moods, "alts": {}}


def _pack_species(meta: dict, s: dict, save_file, only_missing: bool = False) -> None:
    for sp in meta.get("species", []) if isinstance(meta.get("species"), list) else []:
        if not isinstance(sp, dict) or not str(sp.get("name", "")).strip():
            continue
        name = " ".join(str(sp["name"]).split())[:40]
        old = (s.get("species") or {}).get(name.lower())
        item = {"name": name, "description": str(sp.get("description", "")).strip()[:800], "maker": str(sp.get("maker", ""))[:120],
                "reference_url": (old or {}).get("reference_url", ""), "from": (old or {}).get("from", "")}
        if sp.get("file") and not item["reference_url"]:
            fname = f"{_slug(name)}-{int(time.time() * 1000)}{Path(str(sp['file'])).suffix}"
            if save_file(str(sp["file"]), store.PORTRAITS / s["id"] / "species" / fname):
                item["reference_url"] = f"/portraits/{s['id']}/species/{fname}"
        if old and only_missing:
            continue
        s.setdefault("species", {})[name.lower()] = item


def _pack_places(meta: dict, s: dict, save_file) -> None:
    have = {p["name"].lower(): p for p in s.get("places", [])}
    for pl in meta.get("places", []) if isinstance(meta.get("places"), list) else []:
        if not isinstance(pl, dict) or not str(pl.get("name", "")).strip():
            continue
        name = " ".join(str(pl["name"]).split())[:60]
        item = have.get(name.lower()) or {"name": name, "description": "", "url": ""}
        item["description"] = str(pl.get("description", "")).strip()[:500] or item["description"]
        if pl.get("file") and not item.get("url"):
            fname = f"{_slug(name)}{Path(str(pl['file'])).suffix}"
            if save_file(str(pl["file"]), store.PORTRAITS / s["id"] / "places" / fname):
                item["url"] = f"/portraits/{s['id']}/places/{fname}"
        if name.lower() not in have:
            s.setdefault("places", []).append(item)
            have[name.lower()] = item


def _pack_sidebar(meta: dict) -> dict | None:
    if not isinstance(meta.get("sidebar"), dict):
        return None
    sb = store.STORY_DEFAULTS["sidebar"] | {k: meta["sidebar"][k] for k in ("mode", "fields") if k in meta["sidebar"]}
    sb["schema"] = tracker.clean_schema(meta["sidebar"].get("schema")) or None
    sb["custom"] = tracker.clean_custom(meta["sidebar"].get("custom"))
    return sb


@app.post("/api/stories/import-pack")
async def import_pack(request: Request):
    """Body is the raw .zip made by 'Share pack'. Creates a new story from it."""
    z, meta, notes = _open_pack(await request.body())
    s = store.new_story(meta.get("title") or "Shared story", meta.get("subtitle", ""), meta.get("base_prompt", ""))
    sid = s["id"]
    for k in PACK_FIELDS:
        if meta.get(k) is not None and k not in ("title", "subtitle", "base_prompt", "sidebar", "beats", "lore", "clock", "dice"):
            s[k] = meta[k]
    save_file = _pack_saver(z)
    s["cast"] = [_pack_char(c, sid, save_file) for c in meta.get("cast", []) if isinstance(c, dict) and c.get("name")]
    _pack_species(meta, s, save_file)
    for im in meta.get("intro", []):
        fname = f"{_slug(im['name'])}{Path(im['file']).suffix}"
        if save_file(im["file"], store.PORTRAITS / sid / "intro" / fname):
            s.setdefault("intro_images", []).append({"name": im["name"], "url": f"/portraits/{sid}/intro/{fname}"})
    _pack_places(meta, s, save_file)
    if (sb := _pack_sidebar(meta)):
        s["sidebar"] = sb
        tracker.seed_starts(s)
    s["commands"] = _clean_commands(meta.get("commands"))
    if isinstance(meta.get("clock"), dict):       # the author's world clock setting (a story whose narrator keeps the time itself ships it off)
        c = meta["clock"]
        s["clock"] = clock.clean(c)
    if isinstance(meta.get("dice"), dict):        # and its dice setting (auto | manual | off)
        d = meta["dice"]
        s["dice"] = {k: v for k, v in (("mode", d.get("mode") if d.get("mode") in ("auto", "manual", "off") else None), ("difficulty", d.get("difficulty") if d.get("difficulty") in rules.DIFFICULTY else None)) if v}
    if isinstance(meta.get("lore"), dict):        # the author's lore entries
        s["lore"] = {"on": bool(meta["lore"].get("on", True)), "items": lore.clean_list(meta["lore"].get("items"))}
    if isinstance(meta.get("beats"), dict):       # the author's story beats (a package never says which have gone out)
        pb, nb = meta["beats"], beats.default()
        nb.update({"on": bool(pb.get("on", True)), "hold": max(1, min(int(pb.get("hold") or 3), 12)), "lookahead": bool(pb.get("lookahead", True)), "extracted": bool(pb.get("extracted", True))})
        nb["items"] = [{**beats.clean_item(i), "fired": None} for i in (pb.get("items") or []) if isinstance(i, dict)][:beats.MAX_ITEMS]
        s["beats"] = nb
    if isinstance(meta.get("starter_avatar"), dict) and (sheet := _clean_sheet(meta["starter_avatar"])):
        s["avatar"], s["player_name"] = sheet, sheet.get("name", "")
    _give_default_models(s)
    if meta.get("backdrop") and save_file(meta["backdrop"], store.PORTRAITS / sid / f"backdrop{Path(meta['backdrop']).suffix}"):
        s["backdrop"] = {"url": f"/portraits/{sid}/backdrop{Path(meta['backdrop']).suffix}?v={int(time.time())}", "source": "package"}
    if meta.get("cover") and save_file(meta["cover"], store.PORTRAITS / sid / f"cover{Path(meta['cover']).suffix}"):
        s["cover"] = f"/portraits/{sid}/cover{Path(meta['cover']).suffix}?v={int(time.time())}"
    store.save_story(s)
    return {**s, "import_notes": notes}


@app.post("/api/stories/{sid}/update-from-pack")
async def update_from_pack(sid: str, request: Request, parts: str = "text,trackers"):
    """Bring a newer version of a story package into a game already in progress. Turns, memory, the player's avatar, notes and existing characters are kept.
    parts (comma list): text = story prompt, prologue, opening, suggested first moves, endings; trackers = the side-panel tracker list (current values kept);
    art = art settings; world = places and species (new ones added, descriptions updated, existing pictures kept); cast = characters from the package that
    this game does not have yet."""
    s = _story(sid)
    z, meta, notes = _open_pack(await request.body())
    want = {p.strip() for p in parts.split(",") if p.strip()}
    store.snapshot_story(sid, "before package update")
    save_file = _pack_saver(z)
    changed = []
    _strip_missing_pictures(s)         # links to pictures that no longer exist are dropped, so the package can put its pictures back
    if "text" in want:
        for k in ("base_prompt", "prologue", "opening", "opening_name", "alt_openings", "opening_options", "endings", "opening_in_context", "use_state_block"):
            if meta.get(k) is not None:
                s[k] = meta[k]
        changed.append("story text")
        if isinstance(meta.get("lore"), dict):        # lore entries the game does not have yet are added (yours are never overwritten)
            lb = lore.get(s)
            have = {i["title"].lower() for i in lb["items"]}
            new = [i for i in lore.clean_list(meta["lore"].get("items")) if i["title"].lower() not in have]
            if new:
                lb["items"] += new
                changed.append(f"{len(new)} lore entries")
        if isinstance(meta.get("beats"), dict):       # beats the game does not have yet are added by title (the ones it has, and which have gone out, are left alone)
            bb = beats.get(s)
            have = {i["title"].lower() for i in bb["items"]}
            nb = [{**beats.clean_item(i), "fired": None} for i in (meta["beats"].get("items") or []) if isinstance(i, dict) and str(i.get("title", "")).lower() not in have]
            if nb:
                bb["items"] = (bb["items"] + nb)[:beats.MAX_ITEMS]
                bb["extracted"] = True
                changed.append(f"{len(nb)} story beats")
        if meta.get("commands") is not None:
            s["commands"] = _clean_commands(meta["commands"])
            changed.append("command buttons")
        if isinstance(meta.get("clock"), dict) and (s.get("clock") or {}).get("mode", "auto") == "auto" and meta["clock"].get("mode") in ("on", "off"):
            s["clock"] = {**(s.get("clock") or {}), **{k: meta["clock"][k] for k in ("mode", "start", "minutes", "weekdays", "start_day") if k in meta["clock"]}}
            changed.append("world clock")
    if "trackers" in want and (sb := _pack_sidebar(meta)):
        s["sidebar"] = {**(s.get("sidebar") or {}), **sb}
        tracker.seed_starts(s)                         # starting values for trackers the game did not have
        changed.append(f"{len(sb['custom'])} trackers")
    if "art" in want and isinstance(meta.get("art"), dict):
        s["art"] = {**(s.get("art") or {}), **meta["art"]}
        changed.append("art settings")
    if "world" in want:
        _pack_places(meta, s, save_file)
        _pack_species(meta, s, save_file)
        changed.append("places and species")
        if meta.get("cover") and not s.get("cover") and save_file(meta["cover"], store.PORTRAITS / sid / f"cover{Path(meta['cover']).suffix}"):
            s["cover"] = f"/portraits/{sid}/cover{Path(meta['cover']).suffix}?v={int(time.time())}"
            changed.append("cover")
        if meta.get("backdrop") and not (s.get("backdrop") or {}).get("url") and save_file(meta["backdrop"], store.PORTRAITS / sid / f"backdrop{Path(meta['backdrop']).suffix}"):
            s["backdrop"] = {"url": f"/portraits/{sid}/backdrop{Path(meta['backdrop']).suffix}?v={int(time.time())}", "source": "package"}
            changed.append("backdrop")
    if "cast" in want:
        known = {c["name"].lower() for c in s.get("cast", [])} | {c["slug"] for c in s.get("cast", [])}
        new = [c for c in meta.get("cast", []) if isinstance(c, dict) and c.get("name") and c["name"].lower() not in known and _slug(c.get("slug") or c["name"]) not in known]
        s.setdefault("cast", []).extend(_pack_char(c, sid, save_file) for c in new)
        changed.append(f"{len(new)} new character{'s' if len(new) != 1 else ''}")
        n_pic = 0                          # characters the game has but with no pictures (for example after their pictures were deleted): the package's pictures are put back
        by = {c["name"].lower(): c for c in s["cast"]} | {c["slug"]: c for c in s["cast"]}
        for pc in meta.get("cast", []):
            ex = by.get(str(pc.get("name", "")).lower()) or by.get(_slug(pc.get("slug") or pc.get("name") or ""))
            if ex and not ex.get("moods") and pc.get("moods"):
                got = _pack_char(pc, sid, save_file)
                if got["moods"]:
                    ex["moods"] = got["moods"]
                    ex["appearance"] = ex.get("appearance") or got["appearance"]
                    n_pic += 1
        if n_pic:
            changed.append(f"pictures for {n_pic} character{'s' if n_pic != 1 else ''}")
    store.save_story(s)
    return {**_story(sid), "import_notes": notes, "updated_parts": changed}


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
    if t["text"] != body.text.strip():      # the earlier wording is kept (newest first, up to 10) so an edit can be taken back
        t.setdefault("versions", []).insert(0, {"text": t["text"], "ts": time.time()})
        del t["versions"][10:]
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


@app.get("/api/lan")
def lan_status(request: Request):
    """Phone & tablet access (this computer only): the setting, whether the running app already listens on the home network, the addresses to open, the PIN."""
    _local_only(request, "Phone and tablet settings can only be seen")
    import socket
    ips = []
    try:
        out = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=3).stdout.split() if os.name != "nt" else socket.gethostbyname_ex(socket.gethostname())[2]
        ips = [i for i in out if ":" not in i and _home_network(i) and not i.startswith("127.")]
    except Exception:
        pass
    port = request.url.port or 8000
    on = bool(store.load_config().get("lan_access"))
    return {"on": on, "active": os.environ.get("STORYTELLER_LAN") == "1", "urls": [f"http://{i}:{port}" for i in ips], "pin": _lan_pin() if on or PIN else "", "port": port}


@app.post("/api/lan/new-pin")
def lan_new_pin(request: Request):
    """A new PIN (phones then have to enter it again after the next restart); every remembered device is forgotten."""
    _local_only(request, "The PIN can only be changed")
    _dev_load().clear()
    _dev_save()
    return {"pin": _lan_pin(new=True)}


@app.get("/api/lan/devices")
def lan_devices(request: Request):
    """The devices that entered the PIN and are remembered (this computer only)."""
    _local_only(request, "Remembered devices can only be seen")
    return [{"id": k[:12], "name": v.get("name", "Device"), "added": v.get("added", 0), "seen": v.get("seen", 0), "ip": v.get("ip", "")}
            for k, v in sorted(_dev_load().items(), key=lambda kv: -kv[1].get("seen", 0))]


@app.delete("/api/lan/devices/{dev_id}")
def lan_forget_device(dev_id: str, request: Request):
    """Forget one remembered device (it has to enter the PIN again)."""
    _local_only(request, "Remembered devices can only be changed")
    d = _dev_load()
    for k in [k for k in d if k.startswith(dev_id)]:
        del d[k]
    _dev_save()
    return {"ok": True}


@app.post("/api/quit")
async def quit_app(request: Request, restart: int = 0):
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
    if restart:     # a detached helper waits for this process to end, then starts the app again with the same settings (data folder, PIN, port)
        import subprocess, sys
        lan = bool(store.load_config().get("lan_access"))
        host = "0.0.0.0" if lan else "127.0.0.1"         # the phone & tablet setting decides, so switching it takes effect on this restart
        if lan:
            os.environ["STORYTELLER_PIN"], os.environ["STORYTELLER_LAN"] = _lan_pin(), "1"
        else:
            os.environ.pop("STORYTELLER_PIN", None)
            os.environ.pop("STORYTELLER_LAN", None)
        code = ("import subprocess,sys,time;time.sleep(3);"
                "subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host',sys.argv[1],'--port',sys.argv[2]],"
                f"cwd={str(Path(__file__).resolve().parent.parent)!r},stdin=subprocess.DEVNULL,stdout=open({str(store.DATA / 'server.log')!r},'ab'),stderr=subprocess.STDOUT)")
        kw = {"creationflags": 0x00000008 | 0x00000200 | 0x08000000} if os.name == "nt" else {"start_new_session": True}
        subprocess.Popen([sys.executable, "-c", code, host, str(request.url.port or 8000)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
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


def _free_place(s: dict, last: str, places: dict, cache: dict) -> str:
    """The key of a known location this reply is set in, found without an AI: a place tag, else the Place tracker's value, else the one known place named in the reply's
    opening words. "" when unsure (then the helper model is asked)."""
    usable = lambda k: k in places or (k in cache and not cache[k].get("moment") and cache[k].get("url"))
    for t in reversed(re.findall(r"!\[\]\(place:([^)\n]+)\)", last)):
        if usable(_bg_key(t.strip())):
            return _bg_key(t.strip())
    tr = s.get("tracker") or {}
    for f in tracker.active_fields(s):
        if f["label"].strip().lower() in ("place", "location", "where", "setting") and f["id"].split(".")[0] in ("scene", "me"):
            val = (tr.get(f["id"].split(".")[0]) or {}).get(f["id"].split(".")[1])
            if val:
                k = _bg_key(str(val))
                if usable(k):
                    return k
                hits = [x for x in list(places) + list(cache) if usable(x) and (x in k or k in x)]
                if len(set(hits)) == 1:
                    return hits[0]
    head = _clean_for_scene(last)[:300].lower()
    hits = {k for k in list(places) + list(cache) if usable(k) and k and re.search(r"\b" + re.escape(k) + r"\b", head)}
    return next(iter(hits)) if len(hits) == 1 else ""


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
        if not dynamic and (fk := _free_place(s, last, places, cache)):          # a place tag, the Place tracker or the opening words name a known location: no model call
            if fk in places:
                fname, furl, fsrc = places[fk]["name"], places[fk]["url"], "place"
            else:
                fname, furl, fsrc = cache[fk]["name"], cache[fk]["url"], "auto"
            if not (_bg_key(cur_base) == _bg_key(fname) and not cur_moment and cur.get("url")):
                _set_backdrop(sid, furl, fname, fsrc, base=fname)
            return
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
              "(use the hint if one is given and it fits). \"doing\": what that character is physically doing at the end of the reply, as a short phrase about their pose, hands or gesture (for example \"hiding one hand behind her back\" or \"leaning on the table, arms crossed\"). \"wearing\": what that character is wearing now, in a short phrase (for example \"travel cloak over a linen shirt\"), "
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
        if (s.get("art") or {}).get("now_free") or _local_on(cfg, _role(cfg, s, "small")[0]):          # tags only: who and their mood come from the narrator's own portrait tags, with no AI call
            if tags:
                s2 = _story(sid)
                old_now = s2.get("now") or {}
                same = old_now.get("name") == tags[-1][0]
                s2["now"] = {"name": tags[-1][0], "mood": tags[-1][1].lower().strip()[:30], "doing": old_now.get("doing", "") if same else "", "wearing": old_now.get("wearing", "") if same else "",
                             "turn": len(s2["turns"]), **{k: old_now[k] for k in ("image", "image_key") if old_now.get(k)}}
                store.save_story(s2)
            elif _local_on(cfg, _role(cfg, s, "small")[0]):      # no portrait tag: read the mood from the words around a character's name
                best = None
                for c in s.get("cast", []):
                    if c.get("player") or c["name"].lower() == player or not c.get("moods"):
                        continue
                    n = len(re.findall(rf"\b{re.escape(c['name'].split()[0])}\b", last, re.I))
                    if n and (best is None or n > best[0]):
                        best = (n, c)
                if best:
                    mood = localfix.guess_mood(last, best[1]["name"], list(best[1]["moods"]))
                    s2 = _story(sid)
                    old_now = s2.get("now") or {}
                    same = old_now.get("name") == best[1]["name"]
                    s2["now"] = {"name": best[1]["name"], "mood": mood, "doing": old_now.get("doing", "") if same else "", "wearing": old_now.get("wearing", "") if same else "",
                                 "turn": len(s2["turns"]), **{k: old_now[k] for k in ("image", "image_key") if old_now.get(k)}}
                    store.save_story(s2)
            return
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
        old = s2.get("now") or {}
        s2["now"] = {"name": real, "mood": mood, "doing": " ".join(str(j.get("doing", "")).split())[:140] if real else "", "wearing": " ".join(str(j.get("wearing", "")).split())[:120] if real else "", "turn": len(s2["turns"])}
        for k in ("image", "image_key"):           # the last picture stays until a new one is drawn
            if old.get(k):
                s2["now"][k] = old[k]
        key = _now_key(s2["now"])
        live = (s2.get("art") or {}).get("now_pic") == "live"
        if live and key and key != old.get("image_key") and old.get("image_failed_key") != key:
            s2["now"]["drawing"] = True
        store.save_story(s2)
        if s2["now"].get("drawing"):
            asyncio.create_task(_draw_now(sid, key))
    except Exception:
        pass


def _now_key(now: dict) -> str:
    """What the 'right now' picture shows: who, doing what, wearing what (the mood alone does not redraw it)."""
    if not (now.get("name") and (now.get("doing") or now.get("wearing"))):
        return ""
    return hashlib.sha1(f"{now['name']}|{now.get('doing', '').lower()}|{now.get('wearing', '').lower()}".encode()).hexdigest()[:12]


async def _draw_now(sid: str, key: str) -> None:
    """Draw the character on the 'right now' card doing what the card says and wearing what it says, with their saved portraits as the face reference.
    Runs in the background after the card's text is known; one picture at a time with everything else; failures leave the previous picture."""
    async with _image_lock:
        try:
            s, cfg = _story(sid), store.load_config()
            now = s.get("now") or {}
            if _now_key(now) != key or not (s.get("art") or {}).get("now_card") or (s.get("art") or {}).get("now_pic") != "live":
                return               # the card moved on while this waited
            ch = next((c for c in s.get("cast", []) if c["name"] == now["name"]), None)
            if not ch:
                return
            prov = _maker_provider(cfg, s, ch)
            mood = (now.get("mood") or "").strip().lower()
            scene = " ".join(x for x in (f"{ch['name']} {now.get('doing', '')}".strip(), f"looking {mood}" if mood else "") if x)
            if _is_explicit(scene + " " + now.get("wearing", ""), cfg) and (ns := _nsfw_provider(cfg, s)):
                prov = ns
            _model_ctx.set(prov.get("model", ""))
            refs = _load_refs(_portrait_urls(ch, mood or None), int(cfg.get("image_max_refs", 3)), prov)
            art = _nsfw_art(await _art_with_setting(s, cfg), prov)
            where = ((s.get("backdrop") or {}).get("name") or "").strip()
            look = _full_appearance(s, ch)
            wearing = now.get("wearing", "").strip()
            if _wants_tags(prov):
                prompt, negative = await _portrait_tag_prompt(s, cfg, art, {**ch, "appearance": f"{look}. Wearing {wearing}" if wearing else look},
                                                              mood or "normal", notes=f"{now.get('doing', '')}; wearing {wearing}".strip("; "))
            else:
                lines = [images.plain(art.get("direction") or "").strip(),
                         f"{ch['name']}, right now: {now.get('doing', '').strip() or 'standing'}." + (f" In {where}." if where else ""),
                         f"Who: {look}",
                         f"Clothing right now (this replaces any clothing in the description or the reference): {wearing}." if wearing else "",
                         f"Expression and body language: {mood}." if mood else "",
                         ("Reference images are attached: copy the face, hair and build only. The clothing, pose, action and background come from the text above. "
                          + images.STYLE_MATCH) if refs else "",
                         "One character, shown from the waist up or three-quarter length so the action and the clothing are visible.",
                         images.content_line(bool(s.get("nsfw")))]
                prompt, negative = "\n\n".join(x for x in lines if x), (s.get("art") or {}).get("negative", "")
            if prov.get("nsfw_maker"):
                prompt, negative = _add_nsfw_words(prompt, negative, prov, _nsfw_words(cfg, prov))
            data, ext = await images.generate(prov, prompt, negative, "portrait", refs)
            folder = store.PORTRAITS / sid / "now"
            folder.mkdir(parents=True, exist_ok=True)
            name = f"now-{int(time.time() * 1000)}.{ext}"
            (folder / name).write_bytes(data)
            s = _story(sid)
            n = s.get("now") or {}
            for f in folder.glob("now-*"):           # only the newest picture is kept
                if f.name != name and f"/{f.name}" not in json.dumps(n):
                    f.unlink(missing_ok=True)
            if _now_key(n) == key:
                n.update({"image": f"/portraits/{sid}/now/{name}", "image_key": key})
            n.pop("drawing", None)
            s["now"] = n
            store.save_story(s)
        except Exception as e:
            try:
                s = _story(sid)
                n = s.get("now") or {}
                n.pop("drawing", None)
                n["image_failed_key"] = key          # do not retry the same picture every turn
                n["image_error"] = (getattr(e, "detail", None) or str(e))[:200]
                s["now"] = n
                store.save_story(s)
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
_check_tasks: dict[str, asyncio.Task] = {}

RULE_CHECK_SYSTEM = (
    "You check one reply of an interactive story's narrator against the story's own rules. Report only clear, concrete slips of explicit, checkable "
    "rules in the story prompt: required lines or notifications that are missing, doubled or in the wrong place; numbers that contradict the rules or the "
    "previous values; choices, speech or spending made for the player when the rules say the player decides; a forbidden thing the rules name. "
    "Ignore style, taste, length and anything the rules do not state. Each issue is one short sentence naming the rule. "
    'Reply with ONLY JSON: {"issues": ["..."]} (at most 3; an empty list when the reply follows the rules).')


async def _rule_check(sid: str, idx: int, ts: float | None) -> None:
    """Opt-in (story option): the small helper reads the story's rules, the player's move and the reply, and notes clear rule slips on the reply.
    The player can then fix it with one click (a retry that names the slips). Failures are silent."""
    try:
        s, cfg = _story(sid), store.load_config()
        if idx >= len(s["turns"]) or s["turns"][idx]["role"] != "assistant" or s["turns"][idx].get("ts") != ts:
            return
        move = s["turns"][idx - 1]["text"] if idx >= 1 and s["turns"][idx - 1]["role"] == "user" else ""
        prev = next((t["text"] for t in reversed(s["turns"][:idx - 1]) if t["role"] == "assistant"), "")
        prov, model = _role(cfg, s, "small", "rule check")
        user = (f"STORY RULES (the story prompt):\n{s.get('base_prompt', '')[:40000]}\n\nPREVIOUS REPLY (end, for the earlier values):\n{prev[-1500:]}\n\n"
                f"PLAYER'S MOVE:\n{move[:1500]}\n\nREPLY TO CHECK:\n{s['turns'][idx]['text'][:14000]}")
        out = await providers.complete(prov, model, RULE_CHECK_SYSTEM, [{"role": "user", "content": user}], temperature=0.1, max_tokens=300,
                                       num_ctx=cfg["num_ctx"], **_extras(cfg))
        j = _json_in(out)
        issues = [" ".join(str(x).split())[:240] for x in (j.get("issues") or [])][:3] if isinstance(j, dict) else []
        s = _story(sid)        # freshest copy
        if idx < len(s["turns"]) and s["turns"][idx].get("ts") == ts:
            code = (s["turns"][idx].get("check") or {}).get("code_issues", [])
            s["turns"][idx]["check"] = {"issues": (code + [i for i in issues if i not in code])[:6], "code_issues": code, "ts": time.time()}
            store.save_story(s)
    except Exception:
        pass


@app.get("/api/stories/{sid}/turns/{i}/check")
async def get_rule_check(sid: str, i: int, wait: bool = False):
    """The rule check of a reply (wait=true lets a running check finish first). issues is null while none has run."""
    if wait:
        t = _check_tasks.get(sid)
        if t and not t.done():
            await asyncio.wait({t}, timeout=60)
    s = _story(sid)
    t = _turn_at(s, i)
    return {"issues": (t.get("check") or {}).get("issues")}


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
              "character or characters in a striking, cinematic composition framed for a tall portrait (vertical) cover. No text, letters, logos or UI. Keep it safe for work: no nudity or explicit content. "
              + ("Reply with ONE line of comma-separated lowercase Danbooru-style tags (subject count first, hair, eyes, clothing, pose, setting, lighting, framing). No names, no sentences."
                 if tags else "Reply with 2 or 3 descriptive sentences. No names of real people.") + " Output only the prompt.")
    basis = (s.get("description") or "") + "\n\n" + s["base_prompt"][:5000] + ("\n\nCharacters:\n" + "\n".join(f"{c['name']}: {c['appearance']}" for c in s["cast"][:6] if c.get("appearance")) if s.get("cast") else "")
    return (await providers.complete(tprov_model[0], tprov_model[1], system, [{"role": "user", "content": basis}], temperature=0.8, max_tokens=300,
                                     num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})).strip().strip('"')


def _cover_prompt(s: dict, prov: dict, text: str) -> str:
    """The full prompt sent to the picture maker: the story's art style, plus the (editable) cover description."""
    art = s["art"]
    if _wants_tags(prov):
        return ", ".join(x for x in (images.plain(art.get("direction") or "").strip(), "masterpiece, best quality, cover art, vertical composition, portrait orientation", text) if x)
    return "\n\n".join(x for x in (images.plain(art.get("direction") or "").strip(), text, images.SAFE_LINE) if x)


def _cover_provider(cfg: dict, s: dict, provider: str | None, model: str | None) -> dict:
    """The picture maker for a cover: the one chosen in the cover dialog (any picture service and model, so different ones can be tried), else the story's own."""
    if not provider:
        return _img_provider(cfg, None, s)
    prov = next((p for p in cfg.get("image_providers", []) if p["id"] == provider), None)
    if not prov:
        raise HTTPException(400, "That picture maker is not set up. Choose another one, or set it up under 🖼 Picture makers.")
    usage.set_ctx(s.get("id"), "pictures")
    if model:
        prov = {**prov, "model": model}
    if prov.get("type") == "comfyui" and _is_flux_model(prov.get("model", "")):
        return {**_flux_provider(cfg, s, prov["model"]), "id": prov["id"]}
    return prov


@app.get("/api/stories/{sid}/cover/prompt")
def get_cover_prompt(sid: str):
    """The last cover picture description (written by the text model or edited by the player), or ''. Also the picture maker last used for a cover."""
    s = _story(sid)
    return {"prompt": s.get("cover_prompt", ""), "maker": s.get("cover_maker") or {}}


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
    provider: str = ""               # the picture service to draw with (empty = the story's own maker) and its model: lets different makers be tried for a cover
    model: str = ""


@app.post("/api/stories/{sid}/cover/candidates")
async def cover_candidates(sid: str, body: CoverCandIn):
    """Draw several candidate cover pictures (default 4) from the editable description. Nothing changes on the card until .../cover/choose."""
    s, cfg = _story(sid), store.load_config()
    prov = _cover_provider(cfg, s, body.provider.strip() or None, body.model.strip() or None)
    s["cover_maker"] = {"provider": body.provider.strip(), "model": body.model.strip()} if body.provider.strip() else {}
    store.save_story(s)
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
                data, ext = await images.generate(prov, prompt, negative, "portrait")     # covers are portrait, like the cards on the Home screen
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
    """Cut text into parts of about `size` characters at paragraph breaks (a paragraph that is too long is cut at a line end or a sentence end)."""
    def cut(para: str) -> int:
        window = para[:size]
        for sep in ("\n", ". ", "! ", "? ", " "):
            k = window.rfind(sep, size // 2)
            if k > 0:
                return k + len(sep)
        return size
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text):
        while len(para) > size:             # one enormous paragraph (or a text with no blank lines)
            if cur:
                out.append(cur); cur = ""
            k = cut(para)
            out.append(para[:k]); para = para[k:]
        if len(cur) + len(para) + 2 > size and cur:
            out.append(cur); cur = ""
        cur += ("\n\n" if cur else "") + para
    if cur.strip():
        out.append(cur)
    return out


_INFO_RE = re.compile(r"```info\b.*?```", re.IGNORECASE | re.DOTALL)

DERIVE_PROMPT_SYSTEM = (
    "You are given a story that was played elsewhere (an interactive story narrated by an AI, or a story told as plain text): a summary of what happened, the beginning, and the most recent text. "
    "It comes without a story prompt. Write the story prompt a narrator AI needs to continue it well, in second person to the narrator, with short headings and bullet points: "
    "the opening line (who the narrator is and who the player is; write {user} for the player's character), World (concrete facts the text shows: places, rules, powers, technology, tone), "
    "Main characters (name, role, how they talk, what they want, their relationship to {user}; only characters in the text), The player ({user}'s role and situation now), "
    "Rules (rules and consistency facts the text clearly establishes, secrets and open plot threads), Writing style (person, tense, typical reply length, tone, how dialogue is set out, "
    "and any recurring status line or format used in the text, copied exactly), and Where the story stands (one short paragraph). "
    "Use ONLY what the text shows; never invent plot, characters or rules. Never describe what {user} says, thinks or does. About 600 to 1500 words. "
    "Also choose a title (two to five words), a one-line subtitle, and the player character's name if the text makes it clear (otherwise an empty string). "
    'Reply with ONLY JSON: {"title": "...", "subtitle": "...", "player_name": "...", "prompt": "..."}.')


async def _derive_prompt(cfg: dict, prov, model, summary: str, opening: str, recent: str) -> dict:
    """A story prompt written from the story itself (for a save brought over from another app that has none)."""
    usage.set_ctx(None, "import prompt")
    user = f"SUMMARY OF WHAT HAPPENED:\n{summary[:14000] or '(none)'}\n\nTHE BEGINNING:\n{opening}\n\nTHE MOST RECENT TEXT:\n{recent}"
    out = await providers.complete(prov, model, DERIVE_PROMPT_SYSTEM, [{"role": "user", "content": user}], temperature=0.3, max_tokens=4000, num_ctx=cfg["num_ctx"], **{**_extras(cfg), "thinking": False})
    j = _json_in(out)
    if not isinstance(j, dict) or len(str(j.get("prompt", "")).strip()) < 200:
        raise RuntimeError("The AI could not write a story prompt from this text. Paste a prompt yourself, or pick a different AI and try again.")
    return {"prompt": str(j["prompt"]).strip(), "title": " ".join(str(j.get("title", "")).split())[:80] or "Imported story", "subtitle": " ".join(str(j.get("subtitle", "")).split())[:160],
            "player_name": " ".join(str(j.get("player_name", "")).split())[:60]}


@app.post("/api/stories/import-text")
async def import_story_text(body: ImportTextIn):
    """Import a whole story played elsewhere. A background job summarizes it in parts with the chosen AI, then builds the story."""
    cfg = store.load_config()
    src = _story(body.source_story_id) if body.source_story_id else None
    derive = not (body.base_prompt.strip() or src or body.into_story_id)       # no prompt at all (text brought from another app): the AI writes one from the text
    if len(body.text.strip()) < 20:
        raise HTTPException(400, "Paste the story text first.")
    prov, model = _pick(cfg, body.provider, body.model)
    text, export_notes = importer.normalize_export(body.text.strip())          # a JSON / JSONL chat export (ChatGPT, Claude, SillyTavern...) becomes a PLAYER:/NARRATOR: transcript
    text = text.strip()
    recent, warnings = [], list(export_notes)
    synthetic_tail = False
    if importer.is_labelled(text):                         # labelled PLAYER:/NARRATOR: transcript (also You:/Assistant:, Human:/AI: ... as other apps write them)
        turns, parse_warnings = importer.parse_turns(text)
        warnings = list(export_notes) + parse_warnings
        keep = max(2, min(body.keep_last, len(turns)))
        keep -= keep % 2
        if keep and len(turns) > keep and turns[-keep]["role"] != "user":
            keep += 1
        older, recent = turns[:-keep] if keep else turns, turns[-keep:] if keep else []
        source = "\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in older)
    else:
        source, tail = importer.split_tail(text)           # plain prose: the last paragraphs stay word for word as the scene in progress, the rest is summarized
        if tail:
            recent, synthetic_tail = [{"role": "assistant", "text": tail}], True
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
        title, subtitle, avatar, base_prompt, notes = body.title, body.subtitle, dict(body.avatar), body.base_prompt, []
        if derive:
            j["current"] = "Writing a story prompt from the text"
            d = await _derive_prompt(cfg, prov, model, summary, text[:5000], text[-6000:])
            base_prompt, title, subtitle = d["prompt"], title.strip() or d["title"], subtitle.strip() or d["subtitle"]
            if d["player_name"] and not (avatar.get("name") or "").strip():
                avatar["name"] = d["player_name"]
            notes.append("No story prompt was given, so the AI wrote one from your text. Read it and fix anything wrong under ✏️ Edit story, because the narrator follows it every turn."
                         + ("" if avatar.get("name") else " The text does not say who the player is: set your character under 👤 Player."))
        j["current"] = "Building the story"
        res = import_story(ImportIn(prompt_derived=derive, title=title, subtitle=subtitle, source_story_id=body.source_story_id, into_story_id=body.into_story_id, base_prompt=base_prompt,
                                    avatar=avatar, user_notes=body.user_notes, summary=summary, status_panel=panel,
                                    recent_turns="\n\n".join(("PLAYER: " if t["role"] == "user" else "NARRATOR: ") + t["text"] for t in recent)))
        rw = [w for w in res["warnings"] if not (synthetic_tail and w.startswith("Recent turns started with a narrator message"))]
        j["result"] = {"story_id": res["story"]["id"], "warnings": notes + warnings + rw, "summary_chars": len(summary), "prompt_written": derive}

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


# ---------------- continuity check: audit the whole story for contradictions, repair them minimally ----------------
CONTINUITY_SYSTEM = (
    "You are the continuity editor of an interactive story. You are given the story's rules (prompt), character cards, pinned facts, the memory summary and the story's "
    "turns, each marked [#n NARRATOR] or [#n PLAYER]. Audit EVERY turn you are given, not just the latest. Track characters, names, appearances, ages, personalities, "
    "relationships, locations, the timeline, events, powers, items, factions, lore, secrets, who knows what, injuries, deaths and the story's rules. "
    "Find ALL real contradictions: accidental retcons, timeline or location errors, impossible knowledge, character conflicts, broken cause and effect, "
    "and violations of the story's own rules or pinned facts. "
    "PRESERVE intentional changes (a death, a healed wound, new clothes, ageing, a revealed secret, a deliberate correction). "
    "When two statements conflict, prefer: explicit canon (the story prompt, pinned facts, character cards), then later deliberate corrections, then earlier established facts. "
    "Do NOT flag style, taste, pacing or things the story never stated. "
    "Repair each conflict with the SMALLEST logical change, and keep important content. A repair edits one NARRATOR turn: give 'find', a short passage copied EXACTLY, "
    "character for character, from that turn (it must occur in that turn exactly once, 5 to 300 characters), and 'replace', its corrected wording. "
    "If a repair would need a change to a PLAYER turn, to several turns, or to a rule or card, set fix to null (the player decides). "
    "If the conflict is in the memory summary, use target \"summary\" with 'find'/'replace' copied from the summary. "
    "Optionally give 'canon': one short sentence stating the corrected fact, to be pinned so it stays true. "
    'Reply with ONLY JSON: {"issues": [{"turns": [n, ...], "kind": "<character|timeline|location|knowledge|item|injury|relationship|rule|other>", '
    '"severity": "<major|minor>", "problem": "<one or two sentences naming what conflicts with what>", '
    '"fix": {"target": "turn", "turn": n, "find": "...", "replace": "..."} or null, "canon": "<optional>"}], "notes": "<one short sentence>"} '
    "(at most 25 issues, most important first; an empty list when the story is consistent).")

_cont_jobs: dict[str, str] = {}      # story id -> running job id


def _continuity_parts(s: dict, budget: int) -> tuple[str, list[str]]:
    """The fixed context (rules, cards, pinned facts, summary) and the turns cut into parts of about `budget` characters (the last two turns of a part repeat in the next)."""
    cast = "\n".join(f"- {c.get('name', '')}" + (f" ({c['species']})" if c.get("species") else "") + f": {c.get('appearance', '')[:400]} {c.get('bio', '')[:500]}".rstrip() for c in s.get("cast", []))
    head = (f"STORY RULES (the story prompt):\n{s.get('base_prompt', '')[:14000]}\n\nCHARACTER CARDS:\n{cast or '(none)'}\n\n"
            f"PINNED FACTS:\n" + ("\n".join(s.get("pinned_facts") or []) or "(none)") + (f"\n\nLORE ENTRIES (the story's own world notes; flag text that contradicts them):\n" + "\n".join(f"- {i['title']}: {i['text']}" for i in lore.get(s)["items"] if i.get("on", True))[:6000] if lore.get(s)["items"] else "") + (f"\n\nPLANNED STORY BEATS (the story's own schedule; flag a story that contradicts or has clearly drifted from it):\n{beats.prompt_context(s)}" if beats.prompt_context(s) else "") + f"\n\nMEMORY SUMMARY (older events; it may itself contain errors):\n{(s.get('summary') or '(none)')[:9000]}")
    lines = [f"[#{i} {'NARRATOR' if t['role'] == 'assistant' else 'PLAYER'}] {t['text']}" for i, t in enumerate(s["turns"])]
    parts, cur, size = [], [], 0
    for ln in lines:
        if cur and size + len(ln) > budget:
            parts.append("\n\n".join(cur))
            cur = cur[-2:] if len(cur) > 2 else []
            size = sum(len(x) for x in cur)
        cur.append(ln[:budget]); size += len(cur[-1])
    if cur:
        parts.append("\n\n".join(cur))
    return head, parts


def _continuity_budget(prov: dict, cfg: dict, head: str) -> int:
    if prov.get("type") == "ollama":        # a local model has a small window: cut the turns to fit it
        return max(6000, int(cfg.get("num_ctx", 8192)) * 3 - len(head) - 9000)
    return 300000


@app.get("/api/stories/{sid}/continuity/estimate")
def continuity_estimate(sid: str, role: str = "story"):
    s, cfg = _story(sid), store.load_config()
    prov, model = _role(cfg, s, "story" if role != "recaps" else "recaps", "continuity check")
    head, parts = _continuity_parts(s, 300000)
    head, parts = _continuity_parts(s, _continuity_budget(prov, cfg, head))
    chars = sum(len(p) + len(head) for p in parts)
    return {"model": f"{prov.get('name', prov['id'])}: {model}", "parts": len(parts), "tokens": chars // 4, "turns": len(s["turns"])}


class ContinuityIn(BaseModel):
    role: str = "story"


@app.post("/api/stories/{sid}/continuity/audit")
async def continuity_audit(sid: str, body: ContinuityIn):
    """Read the whole story (in parts when it is long) and list real contradictions with a minimal repair for each (a background job; poll /api/jobs/<id>).
    Nothing is changed by this; /continuity/apply makes the repairs the player ticks."""
    s, cfg = _story(sid), store.load_config()
    if len(s["turns"]) < 2:
        raise HTTPException(400, "There is nothing to check yet: play a few turns first.")
    if _cont_jobs.get(sid) and _jobs.get(_cont_jobs[sid], {}).get("status") == "running":
        raise HTTPException(409, "A continuity check is already running for this story.")
    prov, model = _role(cfg, s, "story" if body.role != "recaps" else "recaps", "continuity check")
    head, _ = _continuity_parts(s, 300000)
    head, parts = _continuity_parts(s, _continuity_budget(prov, cfg, head))
    job = _new_job("continuity", sid, len(parts))
    _cont_jobs[sid] = job["id"]

    async def run() -> None:
        usage.set_ctx(sid, "continuity check")
        try:
            issues, notes = [], []
            for n, part in enumerate(parts):
                job["current"] = f"Reading part {n + 1} of {len(parts)}"
                out = await providers.complete(prov, model, CONTINUITY_SYSTEM, [{"role": "user", "content": f"{head}\n\nTURNS (part {n + 1} of {len(parts)}):\n{part}"}],
                                               temperature=0.1, max_tokens=7000, num_ctx=cfg["num_ctx"], **_extras(cfg))
                j = _json_in(out)
                if not isinstance(j, dict):
                    raise RuntimeError("The AI's answer could not be read. Try again, or pick a different model.")
                issues += [x for x in (j.get("issues") or []) if isinstance(x, dict)]
                if j.get("notes"):
                    notes.append(str(j["notes"])[:300])
                job["done"] = n + 1
            fresh, clean, seen = _story(sid), [], set()
            for x in issues:
                turns = sorted({int(t) for t in (x.get("turns") or []) if str(t).lstrip("-").isdigit() and 0 <= int(t) < len(fresh["turns"])})
                prob = " ".join(str(x.get("problem", "")).split())[:500]
                if not prob or (tuple(turns), prob[:60]) in seen:
                    continue
                seen.add((tuple(turns), prob[:60]))
                fix, f = None, x.get("fix")
                if isinstance(f, dict) and str(f.get("find", "")).strip() and "replace" in f:
                    tgt = "summary" if f.get("target") == "summary" else "turn"
                    src, ti = (fresh.get("summary", ""), None) if tgt == "summary" else (None, None)
                    if tgt == "turn":
                        ti = int(f["turn"]) if str(f.get("turn", "")).lstrip("-").isdigit() else None
                        src = fresh["turns"][ti]["text"] if ti is not None and 0 <= ti < len(fresh["turns"]) and fresh["turns"][ti]["role"] == "assistant" else None
                    find = str(f["find"])
                    if src is not None and src.count(find) == 1 and str(f["replace"]) != find:
                        fix = {"target": tgt, "turn": ti, "find": find, "replace": str(f["replace"])}
                clean.append({"id": len(clean), "turns": turns, "kind": str(x.get("kind", "other"))[:20], "severity": "major" if x.get("severity") == "major" else "minor",
                              "problem": prob, "fix": fix, "canon": " ".join(str(x.get("canon", "")).split())[:240]})
            job["result"] = {"issues": clean[:40], "notes": " ".join(notes)[:600], "parts": len(parts), "turns": len(fresh["turns"]), "ts": time.time()}
            job["status"] = "done"
        except Exception as e:
            job["status"], job["error"] = "error", str(e)[:300] or e.__class__.__name__
        job["current"] = ""

    asyncio.create_task(run())
    return job


class ContinuityFix(BaseModel):
    target: str = "turn"
    turn: int | None = None
    find: str
    replace: str


class ContinuityApplyIn(BaseModel):
    fixes: list[ContinuityFix] = []
    canon: list[str] = []


@app.post("/api/stories/{sid}/continuity/apply")
def continuity_apply(sid: str, body: ContinuityApplyIn):
    """Make the repairs the player ticked: each edits one narrator turn (the old wording is kept in its edit history) or the memory summary, only where the text still
    matches exactly once. The corrected facts are pinned so they stay true. A backup is saved first."""
    s = _story(sid)
    store.snapshot_story(sid, "before continuity repairs")
    applied, skipped = 0, []
    for f in body.fixes:
        if f.target == "summary":
            if (s.get("summary") or "").count(f.find) == 1:
                s["summary"] = s["summary"].replace(f.find, f.replace); applied += 1
            else:
                skipped.append({"turn": None, "reason": "the summary changed since the check"})
            continue
        if f.turn is None or not 0 <= f.turn < len(s["turns"]) or s["turns"][f.turn]["role"] != "assistant":
            skipped.append({"turn": f.turn, "reason": "not a narrator turn"}); continue
        t = s["turns"][f.turn]
        if t["text"].count(f.find) != 1:
            skipped.append({"turn": f.turn, "reason": "the text changed since the check"}); continue
        new = t["text"].replace(f.find, f.replace)
        if not new.strip():
            skipped.append({"turn": f.turn, "reason": "would leave the turn empty"}); continue
        t.setdefault("versions", []).insert(0, {"text": t["text"], "ts": time.time()})
        del t["versions"][10:]
        t["text"], t["edited"] = new, True
        applied += 1
    pins = s.setdefault("pinned_facts", [])
    for c in body.canon:
        c = " ".join(c.split())[:240]
        if c and c.lower() not in [p.lower() for p in pins]:
            pins.append(c)
    store.save_story(s)
    return {"applied": applied, "skipped": skipped, "story": s}


# ---------------- story beats (the story's own schedule of key events; see app/beats.py) ----------------
_beats_running: set[str] = set()
_beats_tried: set[str] = set()


async def _extract_beats(sid: str) -> tuple[int, int]:
    """Read the story prompt for scheduled key events and merge them into the story's beats. Raises when the AI's answer cannot be used."""
    if sid in _beats_running:
        raise HTTPException(409, "The story prompt is already being read.")
    _beats_running.add(sid)
    try:
        s, cfg = _story(sid), store.load_config()
        events = []
        if s.get("base_prompt", "").strip():
            prov, model = _role(cfg, s, "recaps", "story beats")
            out = await providers.complete(prov, model, beats.EXTRACT_SYSTEM, [{"role": "user", "content": beats.extract_user(s)}], temperature=0.1, max_tokens=6000,
                                           num_ctx=cfg["num_ctx"], **_extras(cfg))
            j = _json_in(out)
            if not isinstance(j, dict) or not isinstance(j.get("events"), list):
                raise RuntimeError("The AI's answer could not be read. Try again, or pick a different helper model.")
            events = j["events"]
        fresh = _story(sid)
        res = beats.merge(fresh, events)
        store.save_story(fresh)
        return res
    finally:
        _beats_running.discard(sid)


async def _ensure_beats(sid: str) -> None:
    try:
        await _extract_beats(sid)
    except Exception as e:      # shown in the dialog; tried again next time the story is opened or when the player asks
        try:
            s = _story(sid)
            beats.get(s)["error"] = (e.detail if isinstance(e, HTTPException) else str(e))[:200] or e.__class__.__name__
            store.save_story(s)
        except Exception:
            pass


def _beats_view(sid: str) -> dict:
    return {**beats.view(_story(sid)), "running": sid in _beats_running}


@app.get("/api/stories/{sid}/beats")
def get_beats(sid: str):
    return _beats_view(sid)


class BeatsSettingsIn(BaseModel):
    on: bool | None = None
    hold: int | None = None
    lookahead: bool | None = None


@app.put("/api/stories/{sid}/beats")
def put_beats(sid: str, body: BeatsSettingsIn):
    s = _story(sid)
    b = beats.get(s)
    if body.on is not None:
        b["on"] = body.on
    if body.hold is not None:
        b["hold"] = max(1, min(int(body.hold), 12))
    if body.lookahead is not None:
        b["lookahead"] = body.lookahead
    store.save_story(s)
    return _beats_view(sid)


@app.post("/api/stories/{sid}/beats/ensure")
async def ensure_beats(sid: str):
    """Called when a story is opened: the first time (beats on, never read), the story prompt is read for key events in the background."""
    s = _story(sid)
    b = beats.get(s)
    if b["on"] and not b["extracted"] and s.get("base_prompt", "").strip() and sid not in _beats_tried and sid not in _beats_running:
        _beats_tried.add(sid)
        asyncio.create_task(_ensure_beats(sid))
        return {"started": True}
    return {"started": False}


@app.post("/api/stories/{sid}/beats/read")
async def read_beats(sid: str):
    """Read the story prompt again: new events are added, ones you have not edited are refreshed, your own and edited ones are left alone."""
    _story(sid)
    try:
        added, updated = await _extract_beats(sid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e)[:300] or e.__class__.__name__)
    return {**_beats_view(sid), "added": added, "updated": updated}


class BeatIn(BaseModel):
    title: str | None = None
    text: str | None = None
    field: str | None = None
    op: str | None = None
    value: str | int | float | None = None
    off: bool | None = None


def _beat(s: dict, iid: str) -> dict:
    it = next((i for i in beats.get(s)["items"] if i["id"] == iid), None)
    if not it:
        raise HTTPException(404, "No such story beat")
    return it


@app.post("/api/stories/{sid}/beats/items")
def add_beat(sid: str, body: BeatIn):
    s = _story(sid)
    b = beats.get(s)
    if len(b["items"]) >= beats.MAX_ITEMS:
        raise HTTPException(400, f"A story can have up to {beats.MAX_ITEMS} beats.")
    it = beats.clean_item({**body.model_dump(exclude_none=True), "source": "player"})
    if not (it["title"] or it["text"]):
        raise HTTPException(400, "Give the event a title or a description.")
    it["title"] = it["title"] or it["text"][:60]
    it["edited"] = True
    b["items"].append(it)
    store.save_story(s)
    return _beats_view(sid)


@app.put("/api/stories/{sid}/beats/items/{iid}")
def edit_beat(sid: str, iid: str, body: BeatIn):
    s = _story(sid)
    it = _beat(s, iid)
    new = beats.clean_item({**it, **body.model_dump(exclude_none=True)}, old=it)
    new["edited"] = True
    if body.field is not None or body.value is not None or body.op is not None:
        new["fired"] = None          # a changed trigger starts over
    it.update(new)
    store.save_story(s)
    return _beats_view(sid)


@app.delete("/api/stories/{sid}/beats/items/{iid}")
def delete_beat(sid: str, iid: str):
    s = _story(sid)
    it = _beat(s, iid)
    beats.get(s)["items"].remove(it)
    store.save_story(s)
    return _beats_view(sid)


@app.post("/api/stories/{sid}/beats/items/{iid}/reset")
def reset_beat(sid: str, iid: str):
    """Make a beat wait for its trigger again (it goes out again when due)."""
    s = _story(sid)
    it = _beat(s, iid)
    it["fired"], it["off"], it["why"] = None, False, ""
    store.save_story(s)
    return _beats_view(sid)


# ---------------- stat points the player spends, dice, lore, duplicate characters (all done by the app itself, no AI) ----------------
def _points_view(s: dict) -> dict:
    pf = tracker.points_field(s)
    tr = s.get("tracker") or tracker.blank()
    pend = {r["id"]: r["n"] for r in tr.get("_spent", [])}
    if not pf:
        return {"on": False}
    psec, pk = pf["id"].split(".", 1)
    return {"on": True, "id": pf["id"], "label": pf["label"], "points": tracker._num(tr[psec].get(pk)) or 0, "cap": pf.get("spend_cap"),
            "targets": [{"id": t["id"], "label": t["label"], "value": tracker._num(tr[t["id"].split(".", 1)[0]].get(t["id"].split(".", 1)[1])) or 0, "pending": pend.get(t["id"], 0)}
                        for t in tracker.spend_targets(s)]}


@app.get("/api/stories/{sid}/points")
def get_points(sid: str):
    """The points the player can spend and what they can be spent on. The first time, starting values are read from the avatar sheet ("Strength 8 ... Free points: 5")."""
    s = _story(sid)
    if tracker.points_field(s):
        av = s.get("avatar") or {}
        text = " ".join(str(v) for v in av.values())
        if tracker.seed_from_text(s, text):
            store.save_story(s)
    return _points_view(s)


class SpendIn(BaseModel):
    id: str
    n: int = 1


@app.post("/api/stories/{sid}/spend")
def spend_points(sid: str, body: SpendIn):
    """Spend (n > 0) or take back (n < 0, only since the last move) points on a stat or skill. The narrator is told the final numbers with the player's next move."""
    s = _story(sid)
    if not tracker.points_field(s):
        raise HTTPException(400, "This story has no points to spend (set up a Points tracker under Settings → Story & narration → Side panel).")
    try:
        tracker.spend(s, body.id, max(-20, min(body.n, 20)))
    except ValueError as e:
        raise HTTPException(400, str(e))
    store.save_story(s)
    return {"points": _points_view(s), "story": s}


class DiceSettingsIn(BaseModel):
    mode: str | None = None          # auto | manual | off ("" = let the story prompt decide)
    difficulty: str | None = None


@app.get("/api/stories/{sid}/dice")
def get_dice(sid: str):
    s = _story(sid)
    d = s.get("dice") or {}
    return {"mode": d.get("mode", ""), "effective": rules.dice_mode(s), "prompt_calls_for_checks": rules.prompt_calls_for_checks(s), "difficulty": d.get("difficulty", "medium"),
            "stats": rules.check_stats(s), "difficulties": rules.DIFFICULTY, "offer": rules.dice_offer(s)}


class DiceOfferIn(BaseModel):
    mode: str = "off"               # auto = the app rolls for risky moves (and the dice rule is added to the prompt if it has none) | manual = only the 🎲 button | off


@app.post("/api/stories/{sid}/dice/offer")
def answer_dice_offer(sid: str, body: DiceOfferIn):
    """The answer to the one-time question shown for a story that has stats and levels. It is never asked again."""
    s = _story(sid)
    d = s.setdefault("dice", {})
    d["offered"] = True
    d["mode"] = body.mode if body.mode in ("auto", "manual", "off") else "off"
    if d["mode"] == "auto":
        rules.add_dice_rule(s)
    store.save_story(s)
    return get_dice(sid)


@app.put("/api/stories/{sid}/dice")
def put_dice(sid: str, body: DiceSettingsIn):
    s = _story(sid)
    d = s.setdefault("dice", {})
    if body.mode is not None:
        if body.mode in ("auto", "manual", "off"):
            d["mode"] = body.mode
        else:
            d.pop("mode", None)
    if body.difficulty is not None and body.difficulty in rules.DIFFICULTY:
        d["difficulty"] = body.difficulty
    store.save_story(s)
    return get_dice(sid)


class RollIn(BaseModel):
    id: str = ""                    # the stat or skill (a number tracker of the player); empty = a plain d20
    difficulty: str = "medium"
    dc: int | None = None
    adv: str = ""                   # adv | dis | ""


@app.post("/api/stories/{sid}/roll")
def roll_dice(sid: str, body: RollIn):
    """The app rolls a d20 + (stat - 5) against a difficulty. The page then sends the result with the player's move; the narrator must narrate that outcome."""
    s = _story(sid)
    st = next((x for x in rules.check_stats(s) if x["id"] == body.id), None)
    if body.id and not st:
        raise HTTPException(400, "That cannot be rolled against.")
    return rules.roll(st["value"] if st else None, body.difficulty, body.dc, body.adv, st["label"] if st else "")


class LoreIn(BaseModel):
    on: bool | None = None
    items: list[dict] | None = None


@app.get("/api/stories/{sid}/lore")
def get_lore(sid: str):
    return lore.get(_story(sid))


@app.put("/api/stories/{sid}/lore")
def put_lore(sid: str, body: LoreIn):
    """Replace the story's lore entries (the page sends the whole edited list)."""
    s = _story(sid)
    lb = lore.get(s)
    if body.on is not None:
        lb["on"] = body.on
    if body.items is not None:
        lb["items"] = lore.clean_list(body.items)
    store.save_story(s)
    return lb


class LorePreviewIn(BaseModel):
    text: str | None = None


@app.post("/api/stories/{sid}/lore/preview")
def preview_lore(sid: str, body: LorePreviewIn):
    """What would be sent to the narrator right now (or for a sample text): lets the player test their trigger words."""
    s = _story(sid)
    return {"note": lore.note(s, body.text), "matched": [i["title"] for i in lore.triggered(s, body.text)]}


@app.get("/api/stories/{sid}/cast/duplicates")
def cast_duplicates(sid: str):
    """Characters that are probably the same person (free): the page offers to merge them."""
    return characters.find_duplicates(_story(sid))


class SkillIn(BaseModel):
    name: str


@app.post("/api/stories/{sid}/points/skill")
def add_skill(sid: str, body: SkillIn):
    """A new skill (for example one a class gives) that points can be spent on: it becomes a number tracker of the player, listed under the points tracker's spend list."""
    s = _story(sid)
    pf = tracker.points_field(s)
    name = " ".join(body.name.split())[:30]
    if not pf or not name:
        raise HTTPException(400, "Give the skill a name (and set up a Points tracker first).")
    sb = s.setdefault("sidebar", tracker.default_sidebar())
    custom = [dict(f) for f in (sb.get("custom") or [])]
    if any(f["label"].lower() == name.lower() for f in custom):
        raise HTTPException(400, f"There is already a tracker called {name}.")
    if len(custom) >= 24:
        raise HTTPException(400, "A story can have up to 24 side-panel trackers.")
    for f in custom:
        if f["id"] == pf["id"]:
            f["spend_on"] = list(f.get("spend_on") or []) + [name]
    custom.append({"label": name, "kind": "number", "scope": "me", "note": "A skill: ranks are bought with points."})
    sb["custom"] = tracker.clean_custom(custom, sb.get("custom") or [])
    tr = s.setdefault("tracker", tracker.blank())
    new = next((f for f in sb["custom"] if f["label"].lower() == name.lower()), None)
    if new and tr["me"].get(new["id"].split(".", 1)[1]) in (None, ""):
        tr["me"][new["id"].split(".", 1)[1]] = 0
    store.save_story(s)
    return {"points": _points_view(s), "story": s}


# ---------------- world clock (optional per story; see app/clock.py) ----------------
def _clock_view(s: dict) -> dict:
    c = clock.settings(s)
    m = clock.current(s)
    return {"mode": c["mode"], "start": c["start"], "minutes": c["minutes"], "detected": clock.prompt_uses_clock(s), "on": clock.is_on(s),
            "time": clock.fmt(m) if m is not None else "", "part": clock.part_of_day(m) if m is not None else "",
            "weekdays": c["weekdays"], "start_day": c["start_day"], "weekdays_on": clock.weekdays_on(s), "weekdays_detected": clock.prompt_uses_weekdays(s),
            "weekday": clock.WEEKDAYS[clock.current_weekday(s)] if clock.current_weekday(s) is not None else ""}


class ClockIn(BaseModel):
    mode: str | None = None          # auto | on | off
    start: str | None = None         # "06:00": where a new day starts
    minutes: int | None = None       # how far each reply moves time on
    weekdays: str | None = None      # auto | on | off: keep the day of the week (Monday, Tuesday...)
    start_day: str | None = None     # the weekday of Day 1 ("" = what the story prompt says, else Monday)


@app.get("/api/stories/{sid}/clock")
def get_clock(sid: str):
    return _clock_view(_story(sid))


@app.put("/api/stories/{sid}/clock")
def put_clock(sid: str, body: ClockIn):
    s = _story(sid)
    c = s.setdefault("clock", {})
    if body.mode in ("auto", "on", "off"):
        c["mode"] = body.mode
    if body.start is not None and clock.parse_time(body.start) is not None:
        c["start"] = clock.fmt(clock.parse_time(body.start))
    if body.minutes is not None:
        c["minutes"] = max(1, min(int(body.minutes), 720))
    if body.weekdays in ("auto", "on", "off"):
        c["weekdays"] = body.weekdays
    if body.start_day is not None:
        i = clock.weekday_index(body.start_day)
        c["start_day"] = clock.WEEKDAYS[i] if i is not None else ""
    if not clock.is_on(s):
        (s.get("tracker") or {}).pop("_clock", None)
    if not clock.weekdays_on(s) or body.start_day is not None:
        (s.get("tracker") or {}).pop("_wd", None)
    store.save_story(s)
    return _clock_view(s)


class ClockSetIn(BaseModel):
    time: str


@app.post("/api/stories/{sid}/clock/set")
def set_clock(sid: str, body: ClockSetIn):
    """Set the in-world time and/or day of the week by hand ("14:30", "Tuesday", "Tuesday 2:30 pm"), for example after a time skip the narrator did not mention.
    The narrator is told the new time with the next move."""
    s = _story(sid)
    text = body.time or ""
    wd = clock.weekday_index(text) if clock._WD_FULL.search(text) or clock._WD.search(text) else None
    rest = clock._WD.sub(" ", text).strip(" ,;-")
    m = clock.parse_time(rest) if rest else None
    if m is None and wd is None:
        raise HTTPException(400, "Type a time like 14:30 or 2:30 pm, a day like Tuesday, or both.")
    tr = s.setdefault("tracker", tracker.blank())
    if m is not None:
        if not clock.is_on(s):
            raise HTTPException(400, "The world clock is off for this story (✏️ Edit story → Options).")
        st = tr.get("_clock") or {"day": clock._day(s), "t": len(s["turns"]) // 2, "shown": None}
        st["min"] = m
        tr["_clock"] = st
        tf = clock._field(s, ("time", "clock", "time of day"))
        if tf:
            sec, k = tf["id"].split(".", 1)
            tr.setdefault(sec, {})[k] = clock.fmt(m); st["shown"] = clock.fmt(m)
    if wd is not None:
        if not clock.weekdays_on(s):
            raise HTTPException(400, "Days of the week are off for this story (✏️ Edit story → Options → World clock).")
        st = tr.get("_wd") or {"day": clock._day(s), "t": len(s["turns"]) // 2, "shown": None}
        st["i"] = wd
        tr["_wd"] = st
        wf = clock._wd_field(s)
        if wf:
            sec, k = wf["id"].split(".", 1)
            tr.setdefault(sec, {})[k] = clock.WEEKDAYS[wd]; st["shown"] = clock.WEEKDAYS[wd]
    store.save_story(s)
    return {"clock": _clock_view(s), "story": s}


# ---------------- restart a story from the beginning (as a separate copy) ----------------
class RestartIn(BaseModel):
    title: str = ""


@app.post("/api/stories/{sid}/restart")
def restart_story(sid: str, body: RestartIn):
    """A fresh copy of this story that starts at the beginning. It keeps the setup (story prompt, prologue, opening, options, trackers, art settings, places, species, lore,
    story beats, your character, and the cast with their pictures) and drops everything that came from playing: turns, memory, side-panel values, key events, pinned
    facts, notes' progress, bookmarks and characters the story introduced. The original is untouched."""
    s = _story(sid)
    nid = uuid.uuid4().hex[:12]
    new = json.loads(json.dumps(s))
    first = next((t for t in s["turns"] if t.get("role") == "assistant"), None)
    original = set(first["cast_before"]) if first and isinstance(first.get("cast_before"), list) else None     # who was in the cast before the story began
    old_folder = store.PORTRAITS / sid
    if old_folder.exists():
        shutil.copytree(old_folder, store.PORTRAITS / nid)
    new = json.loads(json.dumps(new).replace(f"/portraits/{sid}/", f"/portraits/{nid}/"))
    new["id"], new["title"], new["created"] = nid, body.title.strip() or f"{s['title']} (restarted)", time.time()
    keep, gone = [], []
    for c in new.get("cast", []):
        (keep if (original is None or c.get("slug") in original or c.get("player")) else gone).append(c)
        c["events"] = []
    for c in gone:
        shutil.rmtree(store.PORTRAITS / nid / "chars" / c.get("slug", "_"), ignore_errors=True)
    new["cast"] = keep
    imported = bool(s.get("prompt_derived")) or any(t.get("imported") for t in s["turns"]) or bool(s.get("summary_upto") and not s["turns"])
    trimmed = False
    if imported:       # an imported save: a prompt the AI wrote from the text ends with where the story stood; a restart must not start from there
        cut = re.sub(r"(?ims)^[ \t]*#{1,6}[ \t]*where the story stands[^\n]*\n.*?(?=^[ \t]*#{1,6}[ \t]|\Z)", "", new.get("base_prompt", ""))
        if cut != new.get("base_prompt", ""):
            new["base_prompt"], trimmed = cut.rstrip() + "\n", True
    new.update(turns=[], state={}, summary="", summary_upto=0, suggestions=[], pinned_facts=[], reply_options={}, now={}, tracker=tracker.blank())
    for k in ("retry_pending", "undone", "target_ending", "cache_why"):
        new.pop(k, None)
    if new.get("opening"):
        new["opening_in_context"] = True           # an imported save skipped the opening: the new game begins with it
    for it in beats.get(new)["items"]:
        it["fired"] = None
        if it.get("off") and "passed" in str(it.get("why", "")):
            it["off"], it["why"] = False, ""
    store.save_story(new)
    return {"story": new, "removed_characters": [c["name"] for c in gone], "imported": imported, "prompt_trimmed": trimmed}


# ---------------- bug report: one .zip a player can attach to a problem report (see app/bugreport.py) ----------------
@app.middleware("http")
async def bug_trace(request: Request, call_next):
    """Remember failed requests and unhandled errors, for the next bug report (memory only)."""
    try:
        response = await call_next(request)
    except Exception as e:
        bugreport.note("exception", f"{e.__class__.__name__}: {e}", path=request.url.path, method=request.method)
        raise
    if response.status_code >= 400 and response.status_code != 401 and request.url.path.startswith("/api/") and not request.url.path.startswith("/api/bug-report"):
        bugreport.note("http", f"{request.method} {request.url.path} -> {response.status_code}")
    return response


class BugReportIn(BaseModel):
    story_id: str | None = None
    description: str = ""
    include_text: bool = True          # the story's prompt, memory and replies (the recent ones); left out, the report has structure and problems only
    last_turns: int = 10
    hide_names: bool = True            # the player's and the characters' names become Player / Character A, B...
    include_logs: bool = True
    client: dict = {}                  # what the page saw: browser, errors


_bug_reports: dict[str, tuple[float, bytes, dict]] = {}


@app.post("/api/bug-report/preview")
def bug_report_preview(body: BugReportIn, request: Request):
    """Build the report and return what is in it, for the player to read before downloading. Nothing leaves this computer."""
    _local_only(request, "A bug report can only be made")
    s = _story(body.story_id) if body.story_id else None
    data, manifest = bugreport.build(s, store.load_config(), APP_VERSION, body.description, body.include_text, body.last_turns, body.hide_names, body.include_logs, body.client or {}, list(_jobs.values()))
    for k in [k for k, v in _bug_reports.items() if time.time() - v[0] > 900]:
        _bug_reports.pop(k)
    while len(_bug_reports) >= 5:
        _bug_reports.pop(next(iter(_bug_reports)))
    rid = uuid.uuid4().hex[:10]
    _bug_reports[rid] = (time.time(), data, manifest)
    return {"id": rid, **manifest, "filename": f"storyteller-bug-report-{time.strftime('%Y%m%d-%H%M%S')}.zip"}


@app.get("/api/bug-report/{rid}/download")
def bug_report_download(rid: str, request: Request):
    _local_only(request, "A bug report can only be downloaded")
    if rid not in _bug_reports:
        raise HTTPException(404, "That report has expired. Make it again.")
    return Response(_bug_reports[rid][1], media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="storyteller-bug-report-{time.strftime("%Y%m%d-%H%M%S")}.zip"'})
