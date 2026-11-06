"""What AI is available to this user, and what to recommend for each job.

Three jobs ("roles") are chosen per story:
  story   - the narrator that writes the story
  recaps  - memory summaries, key events, bios, picture prompts and other small helper tasks
  images  - the picture maker
Model names change constantly, so the app never trusts a built-in list: once an API key is saved it asks the provider what it offers
(cached for 10 minutes) and sorts the answer into tiers by name. Local models are read from Ollama and ComfyUI.
"""
import asyncio
import hashlib
import re
import time

import httpx

from . import hardware

TIER_LABEL = {"top": "Flagship", "balanced": "Balanced", "budget": "Budget / fast", "local": "On this computer"}
TIER_ORDER = {"top": 0, "balanced": 1, "budget": 2, "local": 3}
HINTS = {
    "claude": "Pro = Opus-class, Balanced = Sonnet, Budget = Haiku. Anthropic has no image maker.",
    "openai": "ChatGPT models: flagship, then 'mini' (balanced) and 'nano' (cheapest). Also makes pictures.",
    "gemini": "Gemini models: Pro (flagship), Flash (balanced), Flash-Lite (cheapest). Also makes pictures.",
    "xai": "Grok models: the main model, plus 'fast' variants that are cheaper. Also makes pictures.",
    "ollama": "Runs on your own computer: free, private, quality depends on your graphics card.",
    "comfyui": "Runs on your own computer: free, private, needs a graphics card with 6 GB or more.",
}
KEY_HELP = {
    "claude": "console.anthropic.com → API Keys (billed separately from a claude.ai subscription)",
    "openai": "platform.openai.com → API keys",
    "gemini": "aistudio.google.com → Get API key",
    "xai": "console.x.ai → API keys",
}
NOT_CHAT = re.compile(r"embed|tts|whisper|audio|realtime|transcribe|moderation|dall-e|imagen|image|veo|sora|search|computer-use|davinci|babbage|aqa|guard|rerank|ocr", re.I)
IS_IMAGE = re.compile(r"image|imagen|imagine|dall-e", re.I)


def tier_of(provider_id: str, model: str) -> str:
    m = model.lower()
    if provider_id == "claude":
        return "budget" if "haiku" in m else "balanced" if "sonnet" in m else "top"
    if provider_id == "gemini":
        return "budget" if "lite" in m else "balanced" if "flash" in m else "top"
    if provider_id == "openai":
        return "budget" if "nano" in m else "balanced" if "mini" in m else "top"
    if provider_id == "xai":
        return "budget" if ("fast" in m or "mini" in m) else "top"
    return "balanced"


_cache: dict[str, tuple[float, dict]] = {}


async def _get(url: str, headers: dict | None = None, timeout: float = 6) -> dict:
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url, headers=headers or {})
        r.raise_for_status()
        return r.json()


async def live_ids(p: dict) -> dict:
    """Ask a provider which models this key can use. {"ok": bool, "ids": [...], "error": str}. Cached for 10 minutes."""
    key = p.get("api_key", "")
    ck = f"{p['id']}|{p.get('base_url')}|{hashlib.sha1(key.encode()).hexdigest()[:8]}"
    if ck in _cache and time.time() - _cache[ck][0] < 600:
        return _cache[ck][1]
    out = {"ok": False, "ids": [], "error": ""}
    try:
        base = (p.get("base_url") or "").rstrip("/")
        if p["type"] == "anthropic":
            j = await _get(f"{base}/v1/models?limit=100", {"x-api-key": key, "anthropic-version": "2023-06-01"})
            out["ids"] = [m["id"] for m in j.get("data", [])]
        elif "generativelanguage.googleapis.com" in base:   # Gemini: its own list endpoint says which models can generate
            j = await _get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200", {"x-goog-api-key": key})
            out["ids"] = [m["name"].split("/", 1)[-1] for m in j.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        else:   # OpenAI and compatibles (xAI, ...)
            j = await _get(f"{base}/models", {"authorization": f"Bearer {key}"})
            out["ids"] = [m["id"] for m in j.get("data", [])]
        out["ok"] = bool(out["ids"])
    except httpx.HTTPStatusError as e:
        out["error"] = "That key was rejected." if e.response.status_code in (401, 403) else f"The provider answered {e.response.status_code}."
    except Exception as e:
        out["error"] = f"Could not reach the provider ({e.__class__.__name__})."
    _cache[ck] = (time.time(), out)
    return out


def forget(provider_id: str | None = None) -> None:
    for k in [k for k in _cache if provider_id is None or k.startswith(provider_id + "|")]:
        _cache.pop(k)


async def ollama_models(p: dict) -> dict:
    try:
        j = await _get(p["base_url"].rstrip("/") + "/api/tags", timeout=3)
        return {"ok": True, "models": [{"id": m["name"], "size_gb": round(m.get("size", 0) / 1024**3, 1)} for m in j.get("models", [])]}
    except Exception:
        return {"ok": False, "models": []}


async def comfy_checkpoints(p: dict) -> dict:
    try:
        j = await _get(p["base_url"].rstrip("/") + "/object_info/CheckpointLoaderSimple", timeout=3)
        return {"ok": True, "ids": j["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]}
    except Exception:
        return {"ok": False, "ids": []}


def _version(model_id: str) -> tuple:
    nums = re.findall(r"\d+(?:\.\d+)?", re.sub(r"\d{6,}", "", model_id))   # ignore date stamps like 20251001
    return tuple(float(n) for n in nums)[:4] + (0.0,) * (4 - len(nums[:4]))


def _sorted(models: list[dict]) -> list[dict]:
    """By tier, then newest version first (so the first model of a tier is the one to recommend), dated snapshots after plain names."""
    return sorted(models, key=lambda m: (TIER_ORDER.get(m["tier"], 9), tuple(-v for v in _version(m["id"])), bool(re.search(r"\d{6,}", m["id"])), m["id"]))


async def _text_provider(p: dict, hw: dict) -> dict:
    e = {"id": p["id"], "name": p["name"], "local": p["type"] == "ollama", "has_key": bool(p.get("api_key")), "ready": False,
         "models": [], "error": "", "hint": HINTS.get(p["id"], ""), "key_help": KEY_HELP.get(p["id"], "")}
    if e["local"]:
        r = await ollama_models(p)
        e["ready"] = r["ok"] and bool(r["models"])
        e["error"] = "" if r["ok"] else "Ollama isn't running (start it, or install it from ollama.com)."
        e["models"] = [{"id": m["id"], "tier": "local", "size_gb": m["size_gb"], "fit": hardware.fit(m["size_gb"], hw)} for m in r["models"]
                       if not NOT_CHAT.search(m["id"])]
    elif e["has_key"]:
        r = await live_ids(p)
        ids = [i for i in r["ids"] if not NOT_CHAT.search(i)] if r["ok"] else list(p.get("models", []))
        e["models"] = _sorted([{"id": i, "tier": tier_of(p["id"], i)} for i in ids])
        e["ready"] = bool(e["models"])
        e["error"] = r["error"] if not r["ok"] else ""
    else:   # no key yet: only whatever the config already lists (Claude ships with a short list), unverified
        e["models"] = _sorted([{"id": i, "tier": tier_of(p["id"], i), "unverified": True} for i in p.get("models", [])])
    return e


async def _image_provider(p: dict, hw: dict) -> dict:
    e = {"id": p["id"], "name": p["name"], "local": p["type"] == "comfyui", "has_key": bool(p.get("api_key")), "ready": False,
         "models": [], "error": "", "hint": HINTS.get(p["id"], ""), "key_help": KEY_HELP.get(p["id"], ""), "default_model": p.get("model", "")}
    if e["local"]:
        r = await comfy_checkpoints(p)
        e["ready"] = r["ok"] and bool(r["ids"]) and hw["vram_gb"] >= hardware.SDXL_VRAM_GB
        if not r["ok"]:
            e["error"] = "ComfyUI isn't running (start it with ~/ComfyUI/start.sh)."
        elif hw["vram_gb"] < hardware.SDXL_VRAM_GB:
            e["error"] = f"Local picture making needs about {hardware.SDXL_VRAM_GB} GB of video memory."
        e["models"] = [{"id": i, "tier": "local"} for i in r["ids"]]
    elif e["has_key"]:
        r = await live_ids(p)
        ids = [i for i in r["ids"] if IS_IMAGE.search(i)] if r["ok"] else []
        if not ids and p.get("model"):
            ids = [p["model"]]
        e["models"] = _sorted([{"id": i, "tier": tier_of(p["id"], i)} for i in ids])
        e["ready"] = bool(e["models"])
        e["error"] = r["error"] if not r["ok"] else ""
    elif p.get("model"):
        e["models"] = [{"id": p["model"], "tier": tier_of(p["id"], p["model"]), "unverified": True}]
    return e


def _pick_model(p: dict, prefer: tuple[str, ...]) -> dict | None:
    for t in prefer:
        for m in p["models"]:
            if m["tier"] == t:
                return m
    return p["models"][0] if p["models"] else None


def recommend(text: list[dict], images: list[dict], hw: dict) -> tuple[dict, list[str]]:
    """One recommendation per role plus warnings, from what is actually usable on this machine."""
    notes: list[str] = []
    recs: dict = {}
    online = [p for p in text if not p["local"] and p["ready"]]
    local = next((p for p in text if p["local"] and p["ready"]), None)

    def local_best(small: bool = False):
        if not local:
            return None
        ok = [m for m in local["models"] if m["fit"] in ("fits", "partial", "cpu")]
        ok.sort(key=lambda m: (m["fit"] != "fits", m["size_gb"] if small else -m["size_gb"]))
        return ok[0] if ok else None

    # --- story: best online quality we trust, else the biggest local model that fits
    order = ["claude", "openai", "gemini", "xai"]
    online.sort(key=lambda p: order.index(p["id"]) if p["id"] in order else 9)
    if online:
        m = _pick_model(online[0], ("balanced", "top", "budget"))
        recs["story"] = {"provider": online[0]["id"], "model": m["id"],
                         "why": ("Claude wrote the best results in the tests run for this app; this is its balanced tier (good quality, moderate cost)."
                                 if online[0]["id"] == "claude" else f"The best online option you have a key for ({online[0]['name']}), balanced tier.")}
    elif local_best():
        m = local_best()
        recs["story"] = {"provider": local["id"], "model": m["id"], "why": f"The biggest installed local model that runs well on {hardware.describe(hw)}."}
    # --- recaps/helpers: the cheapest capable model (these jobs are small and run often)
    if online:
        same = online[0]
        m = _pick_model(same, ("budget", "balanced", "top"))
        recs["recaps"] = {"provider": same["id"], "model": m["id"], "why": "A budget-tier model is plenty for summaries, bios and picture prompts, and keeps the cost per reply tiny."}
    elif local_best(small=True):
        m = local_best(small=True)
        recs["recaps"] = {"provider": local["id"], "model": m["id"], "why": "A small local model is fine for these helper jobs."}
    elif "story" in recs:
        recs["recaps"] = dict(recs["story"], why="Uses the same model as the story.")
    # --- images: local if it can run, else an online image maker
    img_local = next((p for p in images if p["local"] and p["ready"]), None)
    img_online = next((p for p in images if not p["local"] and p["ready"]), None)
    if img_local:
        recs["images"] = {"provider": img_local["id"], "model": img_local["models"][0]["id"], "why": "Free and private: draws on your own graphics card."}
    elif img_online:
        recs["images"] = {"provider": img_online["id"], "model": img_online["models"][0]["id"], "why": f"{img_online['name']} makes the pictures (billed per image)."}
    # --- warnings
    if hw["vram_gb"] <= 0:
        notes.append("No dedicated graphics card was detected, so local models would run on the CPU and be slow. Online models are the better choice for the story.")
    st, im = recs.get("story"), recs.get("images")
    if st and im and st["provider"] == "ollama" and im["provider"] == "comfyui":
        sz = next((m.get("size_gb", 0) for m in (local or {"models": []})["models"] if m["id"] == st["model"]), 0)
        if sz + 7 > hw["vram_gb"]:
            notes.append(f"A local story model (~{sz:g} GB) and local pictures (~7 GB) can't both stay loaded in {hw['vram_gb']:g} GB of video memory. They will swap in and out, which is slow. Use an online story model, or online pictures.")
    if not online and not local:
        notes.append("Nothing is set up yet: add an API key for Claude, ChatGPT, Gemini or Grok below, or install Ollama (ollama.com) for free local models.")
    return recs, notes


def local_suggestions(text: list[dict], hw: dict) -> list[dict]:
    """Models worth installing for this hardware that are not installed yet."""
    have = {m["id"].split(":")[0] + ":" + m["id"].split(":")[-1] if ":" in m["id"] else m["id"] for p in text if p["local"] for m in p["models"]}
    out = []
    for name, size, note in hardware.local_picks(hw):
        base = name.split(":")[0]
        if not any(h == name or h.startswith(base) and name.endswith(h.split(":")[-1]) or h == base + ":latest" for h in have):
            out.append({"model": name, "size_gb": size, "note": note, "command": f"ollama pull {name}"})
    return out


async def build(cfg: dict, hw: dict) -> dict:
    text_task = [_text_provider(p, hw) for p in cfg["providers"]]
    img_task = [_image_provider(p, hw) for p in cfg.get("image_providers", [])]
    res = await asyncio.gather(*text_task, *img_task)
    text, images = list(res[: len(text_task)]), list(res[len(text_task):])
    recs, notes = recommend(text, images, hw)
    return {"hardware": {**hw, "summary": hardware.describe(hw)}, "text": text, "images": images, "recommended": recs, "notes": notes,
            "install": local_suggestions(text, hw), "tier_labels": TIER_LABEL}
