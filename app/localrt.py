"""Optional management of local AI programs, so they run only while needed and free video memory otherwise.

Text (Ollama): the app loads the story's local model when you open or select it, unloads local models no story is using, and
Ollama itself unloads idle ones after the idle time. Pictures (ComfyUI): the app starts ComfyUI when a story that draws locally is
opened (or a picture is requested), and stops it (or just frees its memory) after it has been idle for a set time. Everything is
off unless switched on in Settings -> Local models."""
import asyncio
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

import httpx

from . import store

_last = {"comfy": 0.0, "ollama": 0.0}
_proc: subprocess.Popen | None = None
_starting: asyncio.Lock | None = None
LOG = store.DATA / "comfyui.log"


def default_cmd() -> str:
    return str(Path.home() / "ComfyUI" / "start.sh")


def touch(service: str) -> None:
    _last[service] = time.time()


def _cfg() -> dict:
    return store.load_config()


def _base(cfg: dict, kind: str, pid: str) -> str:
    plist = cfg["providers"] if kind == "text" else cfg.get("image_providers", [])
    p = next((x for x in plist if x["id"] == pid), None)
    return (p or {}).get("base_url", "").rstrip("/") or ("http://127.0.0.1:11434" if pid == "ollama" else "http://127.0.0.1:8188")


# ---------------- hardware ----------------
def vram() -> dict:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=4).stdout
        t, u, f = [int(x) for x in out.splitlines()[0].split(",")]
        return {"total_gb": round(t / 1024, 1), "used_gb": round(u / 1024, 1), "free_gb": round(f / 1024, 1)}
    except Exception:
        return {}


# ---------------- Ollama ----------------
async def ollama_loaded(cfg: dict | None = None) -> list[dict]:
    cfg = cfg or _cfg()
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(_base(cfg, "text", "ollama") + "/api/ps")
            return [{"name": m["name"], "size_gb": round((m.get("size_vram") or m.get("size", 0)) / 1024**3, 1), "expires": m.get("expires_at", "")}
                    for m in r.json().get("models", [])]
    except Exception:
        return []


async def ollama_unload(name: str, cfg: dict | None = None) -> None:
    cfg = cfg or _cfg()
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            await c.post(_base(cfg, "text", "ollama") + "/api/generate", json={"model": name, "keep_alive": 0})
    except Exception:
        pass


async def ollama_load(name: str, cfg: dict | None = None) -> None:
    cfg = cfg or _cfg()
    try:
        async with httpx.AsyncClient(timeout=300) as c:   # an empty request just loads the model; num_ctx must match chat requests or Ollama reloads
            await c.post(_base(cfg, "text", "ollama") + "/api/generate",
                         json={"model": name, "keep_alive": cfg.get("keep_alive", "30m"), "options": {"num_ctx": cfg.get("num_ctx", 8192)}})
        touch("ollama")
    except Exception:
        pass


# ---------------- ComfyUI ----------------
async def comfy_up(cfg: dict | None = None) -> bool:
    cfg = cfg or _cfg()
    try:
        async with httpx.AsyncClient(timeout=2) as c:
            return (await c.get(_base(cfg, "image", "comfyui") + "/system_stats")).status_code == 200
    except Exception:
        return False


def _port(cfg: dict) -> int:
    try:
        return int(_base(cfg, "image", "comfyui").rsplit(":", 1)[1])
    except Exception:
        return 8188


def _pid_on_port(port: int) -> int | None:
    try:
        out = subprocess.run(["ss", "-ltnp", f"sport = :{port}"], capture_output=True, text=True, timeout=4).stdout
        if "pid=" in out:
            return int(out.split("pid=")[1].split(",")[0])
    except Exception:
        pass
    return None


async def comfy_start(cfg: dict | None = None, wait: float = 150) -> bool:
    """Start ComfyUI (if it is not already up) and wait until it answers."""
    global _proc, _starting
    cfg = cfg or _cfg()
    if await comfy_up(cfg):
        touch("comfy")
        return True
    if _starting is None:
        _starting = asyncio.Lock()
    async with _starting:
        if await comfy_up(cfg):
            return True
        cmd = (cfg.get("rt_comfy_cmd") or default_cmd()).strip()
        argv = shlex.split(os.path.expanduser(cmd))
        if not argv or not Path(argv[0]).exists():
            return False
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _proc = subprocess.Popen(argv, stdout=open(LOG, "ab"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(Path(argv[0]).parent))
        t0 = time.time()
        while time.time() - t0 < wait:
            if await comfy_up(cfg):
                touch("comfy")
                return True
            if _proc.poll() is not None:
                return False
            await asyncio.sleep(1.5)
    return False


async def comfy_free(cfg: dict | None = None) -> None:
    cfg = cfg or _cfg()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(_base(cfg, "image", "comfyui") + "/free", json={"unload_models": True, "free_memory": True})
    except Exception:
        pass


async def comfy_stop(cfg: dict | None = None) -> bool:
    """Stop ComfyUI completely (the one we started, or whatever ComfyUI process listens on its port). Returns True if it is down."""
    global _proc
    cfg = cfg or _cfg()
    pid = None
    if _proc and _proc.poll() is None:
        pid = _proc.pid
    else:
        p = _pid_on_port(_port(cfg))
        try:
            if p and b"main.py" in Path(f"/proc/{p}/cmdline").read_bytes():
                pid = p
        except OSError:
            pass
    if pid is None:
        return not await comfy_up(cfg)
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM) if (_proc and pid == _proc.pid) else os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    for _ in range(20):
        await asyncio.sleep(0.5)
        if not await comfy_up(cfg):
            _proc = None
            return True
    return False


# ---------------- policy ----------------
async def select(needed_models: list[str], needs_comfy: bool) -> dict:
    """A story was opened or its AI choices changed: bring up what it needs, release what nothing needs."""
    cfg = _cfg()
    did = {"loaded": [], "unloaded": [], "comfy": ""}
    if cfg.get("rt_text"):
        loaded = {m["name"] for m in await ollama_loaded(cfg)}
        for name in loaded:
            if name not in needed_models and not any(name.split(":")[0] == n.split(":")[0] and ":" not in n for n in needed_models):
                await ollama_unload(name, cfg)
                did["unloaded"].append(name)
        if needed_models and needed_models[0] not in loaded:
            asyncio.create_task(ollama_load(needed_models[0], cfg))   # takes a few seconds; do not make the page wait
            did["loaded"].append(needed_models[0])
    if cfg.get("rt_comfy"):
        if needs_comfy:
            touch("comfy")
            if not await comfy_up(cfg):
                asyncio.create_task(comfy_start(cfg))
                did["comfy"] = "starting"
        elif await comfy_up(cfg):
            if cfg.get("rt_comfy_mode", "stop") == "stop":
                await comfy_stop(cfg)
                did["comfy"] = "stopped"
            else:
                await comfy_free(cfg)
                did["comfy"] = "memory freed"
    return did


async def before_image(provider: dict) -> None:
    """Called before every local picture: start ComfyUI if needed, and make room by unloading the text model if video memory is short."""
    cfg = _cfg()
    if cfg.get("rt_comfy") and not await comfy_up(cfg):
        if not await comfy_start(cfg):
            return
    if cfg.get("rt_text") and cfg.get("rt_swap", True):
        v = vram()
        if v and v["free_gb"] < 8:
            for m in await ollama_loaded(cfg):
                await ollama_unload(m["name"], cfg)
    touch("comfy")


async def free_all() -> dict:
    cfg = _cfg()
    n = 0
    for m in await ollama_loaded(cfg):
        await ollama_unload(m["name"], cfg)
        n += 1
    stopped = False
    if await comfy_up(cfg):
        stopped = (await comfy_stop(cfg)) if cfg.get("rt_comfy_mode", "stop") == "stop" else (await comfy_free(cfg)) is None
    return {"text_models_unloaded": n, "comfy": "stopped" if stopped else "kept"}


async def status() -> dict:
    cfg = _cfg()
    up = await comfy_up(cfg)
    now = time.time()
    idle = int(now - _last["comfy"]) if _last["comfy"] else None
    limit = int(float(cfg.get("rt_comfy_idle_min", 15)) * 60)
    return {"ollama": {"loaded": await ollama_loaded(cfg)},
            "comfy": {"up": up, "idle_s": idle if up else None, "stops_in_s": max(0, limit - idle) if (up and cfg.get("rt_comfy") and idle is not None) else None},
            "vram": vram()}


async def loop() -> None:
    """Every 30 seconds: stop (or free) ComfyUI once it has been idle long enough."""
    while True:
        await asyncio.sleep(30)
        try:
            cfg = _cfg()
            if not cfg.get("rt_comfy") or not await comfy_up(cfg):
                continue
            idle = time.time() - _last["comfy"] if _last["comfy"] else 0
            if _last["comfy"] == 0:
                touch("comfy")   # it was already running when the app started: count idle time from now
                continue
            if idle >= float(cfg.get("rt_comfy_idle_min", 15)) * 60:
                if cfg.get("rt_comfy_mode", "stop") == "stop":
                    await comfy_stop(cfg)
                else:
                    await comfy_free(cfg)
                    touch("comfy")
        except Exception:
            pass
