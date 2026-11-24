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
NOT_CHAT = re.compile(r"embed|tts|whisper|audio|realtime|transcribe|moderation|dall-e|imagen|image|imagine|nano-banana|video|veo|sora|lyria|music|robotics|antigravity|gpt-live|search|computer-use|davinci|babbage|aqa|guard|rerank|ocr", re.I)   # not story writers: pictures, video, music, voice, robots, agents...
IS_IMAGE = re.compile(r"image|imagen|imagine|nano-banana|dall-e", re.I)
NOT_IMAGE = re.compile(r"video|veo|sora", re.I)      # video makers are not picture makers


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


FILES: dict = {"clips": [], "vaes": []}     # text encoders and VAEs ComfyUI listed last time (so a built-in workflow can use the file names you actually have)
LORAS: set[str] = set()     # LoRA files ComfyUI listed last time
UNETS: set[str] = set()     # diffusion models ComfyUI listed last time (FLUX.2 and the like): drawn with the FLUX workflow, not the checkpoint one


async def comfy_checkpoints(p: dict) -> dict:
    """Checkpoints (all-in-one SDXL-style files) and diffusion models (FLUX.2 klein and other models loaded on their own) that ComfyUI has."""
    try:
        j = await _get(p["base_url"].rstrip("/") + "/object_info/CheckpointLoaderSimple", timeout=3)
        ids = j["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except Exception:
        return {"ok": False, "ids": [], "unets": [], "loras": []}
    try:
        u = await _get(p["base_url"].rstrip("/") + "/object_info/UNETLoader", timeout=3)
        unets = [x for x in u["UNETLoader"]["input"]["required"]["unet_name"][0] if diffusion_kind(x)]
    except Exception:
        unets = []
    try:
        l = await _get(p["base_url"].rstrip("/") + "/object_info/LoraLoader", timeout=3)
        loras = [x for x in l["LoraLoader"]["input"]["required"]["lora_name"][0] if not re.search(r"ip-?adapter", x, re.IGNORECASE)]   # IPAdapter FaceID files are loaded by their own node, not as style LoRAs
    except Exception:
        loras = []
    for node, field, key in (("CLIPLoader", "clip_name", "clips"), ("VAELoader", "vae_name", "vaes")):
        try:
            o = await _get(p["base_url"].rstrip("/") + f"/object_info/{node}", timeout=3)
            FILES[key] = list(o[node]["input"]["required"][field][0])
        except Exception:
            pass
    UNETS.clear()
    UNETS.update(unets)
    LORAS.clear()
    LORAS.update(loras)
    return {"ok": True, "ids": ids, "unets": unets, "loras": loras, "lora_families": {l: lora_family(l) for l in loras}}


_KIND_CACHE: dict = {}


def _header_kind(name: str) -> str:
    """Reads the layer names in a diffusion model file (only its small header) to tell what it is, whatever it is called: "chroma" (has a distilled guidance layer,
    like Chroma1-HD and its finetunes), "flux2" (FLUX.2 modulation layers), or "" (unknown or not found). ComfyUI's folder is found from the usual places."""
    import json, struct
    from pathlib import Path
    roots = [Path.home() / "ComfyUI"]
    try:
        from . import store
        cmd = (store.load_config().get("rt_comfy_cmd") or "").strip()
        if cmd:
            roots.insert(0, Path(cmd.split()[0]).expanduser().parent)
    except Exception:
        pass
    for r in roots:
        for sub in ("diffusion_models", "unet"):
            f = r / "models" / sub / name
            if not f.is_file():
                continue
            key = (str(f), f.stat().st_mtime)
            if key in _KIND_CACHE:
                return _KIND_CACHE[key]
            kind = ""
            try:
                with open(f, "rb") as fh:
                    n = struct.unpack("<Q", fh.read(8))[0]
                    keys = json.loads(fh.read(n)).keys() if n < 50_000_000 else []
                ks = " ".join(keys)
                kind = ("zimage" if ("cap_embedder.0.weight" in ks and "context_refiner.0.attention" in ks) else "krea2" if "txtfusion.projector.weight" in ks else "anima" if "adaln_modulation_cross_attn" in ks else "chroma" if "distilled_guidance_layer" in ks else "flux2" if "double_stream_modulation_img" in ks
                        else "qwen" if ("txt_norm" in ks and "transformer_blocks.0.img_mod" in ks and not re.search(r"edit", name, re.IGNORECASE)) else "")
            except Exception:
                kind = ""
            _KIND_CACHE[key] = kind
            return kind
    return ""


# ---------- which models a LoRA works with ----------
LORA_FAMILY_LABEL = {"sdxl": "SDXL / Illustrious / Pony checkpoints", "chroma": "Chroma", "flux1": "FLUX.1-style (works on Chroma, partly)", "flux2": "FLUX.2 klein",
                     "anima": "Anima / Vivid Gloom", "krea2": "Krea 2", "zimage": "Z-Image", "qwen": "Qwen-Image", "unknown": "Unknown (not sure which model)"}
_LORA_CACHE: dict = {}


def lora_file(name: str):
    """The LoRA's file in ComfyUI's loras folder on this computer (None when ComfyUI is somewhere the app cannot see, or the name is not a file there)."""
    from pathlib import Path
    roots = [Path.home() / "ComfyUI"]
    try:
        from . import store
        cmd = (store.load_config().get("rt_comfy_cmd") or "").strip()
        if cmd:
            roots.insert(0, Path(cmd.split()[0]).expanduser().parent)
    except Exception:
        pass
    root = next((r / "models" / "loras" for r in roots if (r / "models" / "loras" / name).is_file()), None)
    return (root / name) if root and ".." not in Path(name).parts else None


def lora_family(name: str) -> str:
    """Which kind of model a LoRA file was trained for: read from its own header (the architecture and base model the trainer wrote, else the layer names), then its name.
    "sdxl" (SDXL, Illustrious, Pony checkpoints), "chroma", "flux1" (FLUX.1-shaped: partly works on Chroma), "flux2", "anima", "krea2", "zimage", "qwen" or "unknown"."""
    import json, struct
    f = lora_file(name)
    low = str(name).lower()
    meta, ks = {}, ""
    if f:
        key = (str(f), f.stat().st_mtime)
        if key in _LORA_CACHE:
            return _LORA_CACHE[key]
        try:
            with open(f, "rb") as fh:
                n = struct.unpack("<Q", fh.read(8))[0]
                h = json.loads(fh.read(n)) if n < 80_000_000 else {}
            meta = h.pop("__metadata__", None) or {}
            ks = " ".join(list(h)[:400])
        except Exception:
            pass
    def from_words(txt: str) -> str:
        if re.search(r"z[-_ ]?image|zimage", txt):
            return "zimage"
        if re.search(r"krea", txt):
            return "krea2"
        if re.search(r"anima(?!gine)", txt):
            return "anima"
        if re.search(r"flux2|klein", txt):
            return "flux2"
        if re.search(r"chroma", txt):
            return "chroma"
        if re.search(r"flux", txt):
            return "flux1"
        if re.search(r"qwen", txt):
            return "qwen"
        if re.search(r"sdxl|stable-diffusion-xl", txt):
            return "sdxl"
        return ""
    fam = from_words(" ".join(str(meta.get(k, "")) for k in ("modelspec.architecture", "ss_base_model_version")).lower()) or from_words(str(meta.get("ss_sd_model_name", "")).lower())
    if not fam and re.search(r"flux[-_ ]?2|klein", low):      # the file name says FLUX.2 klein (the layer names of its LoRAs look just like FLUX.1's)
        fam = "flux2"
    if not fam and ks:           # no usable header words: the layer names say what it was trained on
        if re.search(r"context_refiner|cap_embedder|diffusion_model\.layers\.\d+\.(attention\.to_[qk]|feed_forward\.w[123]|adaLN_modulation)", ks):      # Z-Image (a Lumina-style DiT): its extra layers, or its main layer names
            fam = "zimage"
        elif re.search(r"txtfusion|blocks\.\d+\.attn\.gate|blocks_\d+_attn_gate", ks):
            fam = "krea2"
        elif re.search(r"cross_attn|adaln", ks):
            fam = "anima"
        elif re.search(r"input_blocks|output_blocks|down_blocks|up_blocks|middle_block|mid_block", ks):
            fam = "sdxl"
        elif re.search(r"single_blocks|single_transformer_blocks|double_blocks", ks):
            fam = "flux1"
    if not fam:
        fam = "zimage" if re.search(r"z[-_ ]?image|zimage", low) else "krea2" if re.search(r"krea", low) else "anima" if re.search(r"anima(?!gine)", low) else "chroma" if "chroma" in low else "flux2" if re.search(r"flux2|klein", low) else "flux1" if "flux" in low else "sdxl" if re.search(r"illustrious|pony|sdxl|xl\b|ilxl|\bill\b", low) else "unknown"
    if fam == "flux1" and "chroma" in low:       # a "chroma" LoRA trained with the FLUX.1 trainer is still a Chroma LoRA
        fam = "chroma"
    if f:
        _LORA_CACHE[key] = fam
    return fam


def model_family(name: str, is_checkpoint: bool = False) -> str:
    """The LoRA family a picture model takes: checkpoints are SDXL-style; diffusion models are what their file says they are."""
    if is_checkpoint:
        return "sdxl"
    k = diffusion_kind(name)
    return {"flux2": "flux2", "chroma": "chroma", "anima": "anima", "krea2": "krea2", "zimage": "zimage", "qwen": "qwen"}.get(k, "unknown")


def lora_fits(model_fam: str, lora_fam: str) -> str:
    """"yes", "partly" (a FLUX.1-shaped LoRA on Chroma) or "no"."""
    if lora_fam == "unknown" or model_fam == "unknown":
        return "unknown"
    if lora_fam == model_fam:
        return "yes"
    if model_fam == "chroma" and lora_fam == "flux1":
        return "partly"
    return "no"


def diffusion_kind(name: str) -> str:
    """"flux2", "chroma" or "" for a diffusion model the app can draw with: found from the file's own layers first, the file name second."""
    k = _header_kind(str(name))
    if k:
        return k
    n = str(name)
    if re.search(r"flux[._ -]?2", n, re.IGNORECASE):
        return "flux2"
    if re.search(r"z[._ -]?image", n, re.IGNORECASE):
        return "zimage"
    if re.search(r"krea[._ -]?2", n, re.IGNORECASE):
        return "krea2"
    if re.search(r"anima|vividgloom", n, re.IGNORECASE):
        return "anima"
    if re.search(r"chroma|uncanny", n, re.IGNORECASE):
        return "chroma"
    if re.search(r"qwen[._ -]?image", n, re.IGNORECASE) and not re.search(r"edit", n, re.IGNORECASE):
        return "qwen"       # (Qwen-Image-Edit needs a picture to edit, so it is not a text-to-picture model)
    return ""


def is_flux(name: str) -> bool:
    """A FLUX.2 diffusion model the app's FLUX workflow can draw with."""
    return diffusion_kind(name) == "flux2"


def is_qwen(name: str) -> bool:
    """A Qwen-Image diffusion model (strong prompt following and scenery; Qwen2.5-VL text encoder)."""
    return diffusion_kind(name) == "qwen"


def is_chroma(name: str) -> bool:
    """A Chroma-type diffusion model (uncensored, FLUX-based, with its own workflow: T5 text encoder, real negative prompt)."""
    return diffusion_kind(name) == "chroma"


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
        e["ready"] = r["ok"] and bool(r["ids"] or r["unets"]) and hw["vram_gb"] >= hardware.SDXL_VRAM_GB
        if not r["ok"]:
            e["error"] = "ComfyUI isn't running (start it with ~/ComfyUI/start.sh)."
        elif hw["vram_gb"] < hardware.SDXL_VRAM_GB:
            e["error"] = f"Local picture making needs about {hardware.SDXL_VRAM_GB} GB of video memory."
        e["loras"] = r.get("loras", [])
        e["lora_families"] = r.get("lora_families", {})          # LoRA file -> the kind of model it was trained for
        e["lora_labels"] = LORA_FAMILY_LABEL
        e["models"] = ([{"id": i, "tier": "local", "family": "sdxl"} for i in r["ids"]]
                       + [{"id": i, "tier": "local", "kind": "flux" if is_flux(i) else diffusion_kind(i), "family": model_family(i)} for i in r["unets"]])
    elif e["has_key"]:
        r = await live_ids(p)
        ids = [i for i in r["ids"] if IS_IMAGE.search(i) and not NOT_IMAGE.search(i)] if r["ok"] else []
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
    def best_img(p: dict) -> str:
        """The provider's own default model when it has one; else a general-purpose model (not the first file in alphabetical order, which may be a niche style)."""
        ids = [m["id"] for m in p["models"]]
        if p.get("default_model") in ids:
            return p["default_model"]
        for pat in (r"flux-2-klein-9b", r"flux-2-klein", r"krea2_turbo", r"illustrious-xl", r"realvis", r"gpt-image-2$", r"gemini-3.*image$"):
            hit = next((i for i in ids if re.search(pat, i, re.I)), None)
            if hit:
                return hit
        return ids[0]
    if img_local:
        recs["images"] = {"provider": img_local["id"], "model": best_img(img_local), "why": "Free and private: draws on your own graphics card."}
    elif img_online:
        recs["images"] = {"provider": img_online["id"], "model": best_img(img_online), "why": f"{img_online['name']} makes the pictures (billed per image)."}
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


# ---------- a LoRA's trigger words ----------
# Most LoRAs only show their effect when the prompt holds the words they were trained with. They are in the file's own header (the trainer's tag list), in a notes
# file beside it (Civitai's .civitai.info "trainedWords", the Lora Manager's .json "activation text") or, for some, a plain "trigger" field.
_TRIG_CACHE: dict = {}
_GENERIC_TAGS = {"1girl", "1boy", "2girls", "2boys", "solo", "multiple girls", "multiple boys", "looking at viewer", "simple background", "white background", "portrait", "upper body",
                 "smile", "open mouth", "closed mouth", "standing", "sitting", "realistic", "photorealistic", "photo", "masterpiece", "best quality", "highres", "absurdres", "male focus",
                 "female focus", "1other", "blurry", "outdoors", "indoors", "solo focus", "full body", "cowboy shot", "close-up", "from side", "from behind", "from above", "from below",
                 "looking away", "looking to the side", "male", "female", "man", "woman", "boy", "girl", "nsfw", "background", "grey background", "dark background", "no humans", "monochrome", "greyscale", "lips", "blush", "long hair", "short hair", "breasts", "english text", "text", "watermark", "signature"}


def _clean_triggers(words) -> list[str]:
    out = []
    for w in words:
        w = re.sub(r"\s+", " ", str(w or "")).strip(" ,;\"'")
        if 1 < len(w) <= 40 and len(w.split()) <= 5 and w.lower() not in (x.lower() for x in out):
            out.append(w)
    return out


def _notes_beside(f) -> list[str]:
    """Trigger words from the notes files people keep next to a LoRA (Civitai Helper / Lora Manager / A1111 style)."""
    import json
    out = []
    for suffix in (".civitai.info", ".json", ".info"):
        n = f.with_name(f.stem + suffix)
        if not n.is_file() or n.stat().st_size > 2_000_000:
            continue
        try:
            d = json.loads(n.read_text(encoding="utf-8"))
        except Exception:
            continue
        words = list(d.get("trainedWords") or []) if isinstance(d, dict) else []
        if isinstance(d, dict):
            for key in ("activation text", "activation_text", "trigger words", "trigger_words", "trigger"):
                v = d.get(key)
                words += (v if isinstance(v, list) else re.split(r"\s*,\s*", v) if isinstance(v, str) else [])
        if words:
            out = _clean_triggers(words)
            break
    return out


def lora_triggers(name: str) -> dict:
    """The words that make a LoRA work, read from the file: {"trigger": "word, word" or "", "source": "notes" | "header" | "tags" | "", "known": whether the file could be read}.
    A guess from the trainer's tag list: the tags that were in (nearly) every training picture and are not everyday tags like 1girl or solo. Captions written as sentences give none."""
    import json, struct
    f = lora_file(name)
    if not f:
        return {"trigger": "", "source": "", "known": False}
    key = (str(f), f.stat().st_mtime)
    if key in _TRIG_CACHE:
        return _TRIG_CACHE[key]
    res = {"trigger": "", "source": "", "known": True}
    words = _notes_beside(f)
    if words:
        res.update(trigger=", ".join(words[:6]), source="notes")
    else:
        try:
            with open(f, "rb") as fh:
                n = struct.unpack("<Q", fh.read(8))[0]
                meta = (json.loads(fh.read(n)) if n < 80_000_000 else {}).get("__metadata__") or {}
        except Exception:
            meta = {}
        for k in ("modelspec.trigger_phrase", "trigger_phrase", "ss_trigger_words", "trigger_words", "activation text", "activation_text", "modelspec.trigger_word"):
            v = meta.get(k)
            if v and (words := _clean_triggers(v if isinstance(v, list) else re.split(r"\s*,\s*", str(v)))):
                res.update(trigger=", ".join(words[:6]), source="header")
                break
        else:
            try:
                freq = json.loads(meta.get("ss_tag_frequency") or "{}")
            except Exception:
                freq = {}
            counts: dict[str, int] = {}
            spelled: dict[str, str] = {}      # the trainer's own capitalisation (vulc4nST)
            for d in freq.values() if isinstance(freq, dict) else []:
                for tag, c in (d or {}).items():
                    # a dataset folder called "1_H4Lib,alien,female," was tagged as one string: split it into its tags
                    for part in (re.split(r"\s*,\s*", str(tag)) if "," in str(tag) else [str(tag)]):
                        part = part.strip()
                        if part:
                            counts[part.lower()] = counts.get(part.lower(), 0) + int(c or 0)
                            spelled.setdefault(part.lower(), part)
            if counts:
                top = max(counts.values())
                lead = [t for t, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= 0.9 * top and t not in _GENERIC_TAGS]
                lead = _clean_triggers([spelled.get(x, x) for x in lead])
                if lead and len(lead) <= 4:       # more than a handful of tags in every picture means a general dataset, not a trigger
                    res.update(trigger=", ".join(lead), source="tags")
    _TRIG_CACHE[key] = res
    return res
