"""Image generation providers (Claude cannot make pictures, so a second AI does it).

    await generate(provider, prompt, negative, shape, refs, seed) -> (image bytes, extension)

`refs` is a list of (bytes, mime) reference images, e.g. a character's portrait, used to keep faces consistent.
Provider types:
  gemini   - Google Gemini image models (generateContent; reference images go in as inline data)
  openai   - OpenAI gpt-image and anything OpenAI-compatible (xAI Grok Imagine, ...)
  comfyui  - a local ComfyUI server running a workflow JSON you paste in (placeholders like {{prompt}} are filled in)
To add a provider type, write one function and register it in GENERATORS.
"""
import asyncio
import base64
import copy
import hashlib
import io
import json
import logging
import os
import random
import re
import uuid

import httpx

from . import localrt, usage

TIMEOUT = httpx.Timeout(connect=10, read=300, write=60, pool=10)

SHAPES = {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024), "wide": (1344, 768)}   # sizes for local SDXL-class models
OPENAI_SIZES = {"portrait": "1024x1536", "landscape": "1536x1024", "square": "1024x1024", "wide": "1536x1024"}
SHAPE_WORDS = {"portrait": "portrait orientation (2:3)", "landscape": "landscape orientation (3:2)", "square": "square (1:1)", "wide": "wide cinematic landscape (16:9)"}


class ImageError(Exception):
    pass


def sniff(data: bytes) -> tuple[str, str]:
    """(extension, mime) from the file's first bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[:4] == b"GIF8":
        return "gif", "image/gif"
    raise ImageError("The service returned something that is not a PNG, JPEG, WebP or GIF image.")


def _fail(resp: httpx.Response, who: str) -> None:
    if resp.status_code >= 400:
        raise ImageError(f"{who} returned {resp.status_code}: {resp.text[:400]}")


# ---------------- Gemini ----------------
async def _gemini(p, prompt, negative, shape, refs, seed):
    if not p.get("api_key"):
        raise ImageError("No API key set for the Gemini image provider. Add one in Settings.")
    base = (p.get("base_url") or "https://generativelanguage.googleapis.com").rstrip("/")
    model = p.get("model") or "gemini-2.5-flash-image"
    text = f"{prompt}\n\nImage orientation: {SHAPE_WORDS[shape]}." + (f"\nAvoid: {plain(negative)}" if negative else "")
    parts = [{"text": text}] + [{"inlineData": {"mimeType": m, "data": base64.b64encode(d).decode()}} for d, m in refs]
    body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}}
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.post(f"{base}/v1beta/models/{model}:generateContent", headers={"x-goog-api-key": p["api_key"]}, json=body)
    _fail(r, "Gemini")
    j = r.json()
    images, texts = [], []
    for cand in j.get("candidates", []):
        for part in (cand.get("content") or {}).get("parts", []):
            if part.get("thought"):
                continue
            inl = part.get("inlineData") or part.get("inline_data")
            if inl and inl.get("data"):
                images.append(base64.b64decode(inl["data"]))
            elif part.get("text"):
                texts.append(part["text"])
    if not images:
        why = (j.get("promptFeedback") or {}).get("blockReason") or " ".join(texts)[:300]
        raise ImageError("Gemini returned no image" + (f": {why}" if why else "."))
    return images[-1]


# ---------------- OpenAI and compatible ----------------
async def _openai(p, prompt, negative, shape, refs, seed):
    if not p.get("api_key"):
        raise ImageError(f"No API key set for the image provider '{p.get('name', 'openai')}'. Add one in Settings.")
    base = (p.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    model = p.get("model") or "gpt-image-2"
    headers = {"authorization": f"Bearer {p['api_key']}"}
    text = prompt + (f"\nAvoid: {plain(negative)}" if negative else "")
    use_refs = bool(refs) and p.get("allow_refs", True)
    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        if use_refs:   # edits endpoint takes the reference pictures as files
            data = {"model": model, "prompt": text}
            if p.get("send_size", True):
                data["size"] = OPENAI_SIZES[shape]
            files = [("image[]", (f"ref{i}.{sniff(d)[0]}", d, m)) for i, (d, m) in enumerate(refs)]
            r = await c.post(base + "/images/edits", headers=headers, data=data, files=files)
        else:
            body = {"model": model, "prompt": text, "n": 1}
            if p.get("send_size", True):
                body["size"] = OPENAI_SIZES[shape]
            r = await c.post(base + "/images/generations", headers=headers, json=body)
        _fail(r, p.get("name", "OpenAI"))
        items = r.json().get("data") or []
        if not items:
            raise ImageError("The image service returned no images.")
        item = items[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            g = await c.get(item["url"])
            _fail(g, "image download")
            return g.content
    raise ImageError("The image service's reply had neither image data nor a link.")


# ---------------- ComfyUI ----------------
STARTER_WORKFLOW = {
    "3": {"class_type": "KSampler", "inputs": {"seed": "{{seed}}", "steps": 28, "cfg": 6, "sampler_name": "euler_ancestral",
                                               "scheduler": "normal", "denoise": 1, "model": ["4", 0], "positive": ["6", 0],
                                               "negative": ["7", 0], "latent_image": ["5", 0]}},
    "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "{{model}}"}},
    "5": {"class_type": "EmptyLatentImage", "inputs": {"width": "{{width}}", "height": "{{height}}", "batch_size": 1}},
    "6": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{prompt}}", "clip": ["4", 1]}},
    "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "{{negative}}", "clip": ["4", 1]}},
    "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "storyteller", "images": ["8", 0]}},
}


def fill_workflow(node, values: dict):
    """Replace {{placeholders}} anywhere in a parsed workflow. A value that is exactly one placeholder keeps its type
    (so {{seed}} becomes a number); a placeholder inside longer text is substituted as text."""
    if isinstance(node, dict):
        return {k: fill_workflow(v, values) for k, v in node.items()}
    if isinstance(node, list):
        return [fill_workflow(v, values) for v in node]
    if isinstance(node, str) and "{{" in node:
        s = node.strip()
        if s.startswith("{{") and s.endswith("}}") and s[2:-2] in values and s.count("{{") == 1:
            return values[s[2:-2]]
        for k, v in values.items():
            node = node.replace("{{" + k + "}}", str(v))
    return node


def _tw_num(tw: dict, key: str, lo: float, hi: float):
    try:
        return min(hi, max(lo, float(tw.get(key))))
    except (TypeError, ValueError):
        return None


def _lora_lines(text) -> list[tuple[str, float, str | None]]:
    """The LoRA list from a checkpoint's settings: one per line, "file.safetensors @ 0.8 | trigger words" (no "@" = strength 1; no "|" = the trigger words are looked up in the file;
    "| none" = this LoRA needs none). Strength is kept between -2 and 2."""
    out = []
    for ln in re.split(r"[\n;]", str(text or "")):
        head, bar, trig = ln.strip().partition("|")
        name, _, st = head.partition("@")
        name = name.strip()
        if not name:
            continue
        try:
            s = max(-2.0, min(2.0, float(st))) if st.strip() else 1.0
        except ValueError:
            s = 1.0
        out.append((name, s, trig.strip() if bar else None))
    return out[:6]


def parse_loras(text) -> list[tuple[str, float]]:
    return [(n, s) for n, s, _ in _lora_lines(text)]


def lora_trigger_words(text) -> list[str]:
    """The words the LoRAs in this list need in the prompt to show their effect: the ones written after "|" on the line, else (nothing written) the file's own notes or header, read from
    ComfyUI's loras folder. A guess from a trainer's tag list is only used when it is written on the line (the settings screens fill it in, so the player can see and change it)."""
    out: list[str] = []
    for name, _, trig in _lora_lines(text):
        if trig is None:
            try:
                from . import catalog
                r = catalog.lora_triggers(name)
                trig = r["trigger"] if r.get("source") in ("notes", "header") else ""
            except Exception:
                trig = ""
        if trig.lower() in ("none", "-", "no"):
            continue
        for w in re.split(r"\s*,\s*", trig):
            if w.strip() and w.strip().lower() not in (x.lower() for x in out):
                out.append(w.strip())
    return out


def with_trigger_words(prompt: str, words: list[str]) -> str:
    """The trigger words go first (the way LoRAs are trained), except those the prompt already has."""
    low = (prompt or "").lower()
    add = [w for w in words if not re.search(r"(?<![\w-])" + re.escape(w.lower()) + r"(?![\w-])", low)]
    return (", ".join(add) + ", " + prompt) if add and prompt else ", ".join(add) if add else prompt


_MODEL_LOADERS = ("CheckpointLoaderSimple", "CheckpointLoader", "UNETLoader", "UnetLoaderGGUF", "DiffusionModelLoader")
_CLIP_LOADERS = ("CLIPLoader", "DualCLIPLoader", "CLIPLoaderGGUF", "TripleCLIPLoader")


def add_loras(wf: dict, loras: list[tuple[str, float]]) -> dict:
    """Put the chosen LoRAs between the workflow's model loader and everything that uses its model (and its text encoder, when it has one): a chain of LoraLoader nodes.
    Works for checkpoint workflows (SDXL, Pony, Illustrious) and for diffusion-model workflows (FLUX.2) alike; a workflow it cannot read is left as it was."""
    if not loras:
        return wf
    mid = next((k for k, n in wf.items() if isinstance(n, dict) and n.get("class_type") in _MODEL_LOADERS), None)
    if mid is None:
        return wf
    msrc = [mid, 0]
    if wf[mid]["class_type"].startswith("Checkpoint"):
        csrc = [mid, 1]
    else:
        cid = next((k for k, n in wf.items() if isinstance(n, dict) and n.get("class_type") in _CLIP_LOADERS), None)
        csrc = [cid, 0] if cid is not None else None
    prev_m, prev_c, made = msrc, csrc, []
    for i, (name, st) in enumerate(loras):
        nid = f"lora{i + 1}"
        inputs = {"lora_name": name, "strength_model": st, "model": prev_m}
        if prev_c:
            inputs.update({"strength_clip": st, "clip": prev_c})
        wf[nid] = {"class_type": "LoraLoader" if prev_c else "LoraLoaderModelOnly", "inputs": inputs}
        made.append(nid)
        prev_m, prev_c = [nid, 0], ([nid, 1] if prev_c else None)
    for k, n in wf.items():
        if k in made or not isinstance(n, dict) or not isinstance(n.get("inputs"), dict):
            continue
        for key, v in n["inputs"].items():
            if v == msrc:
                n["inputs"][key] = prev_m
            elif csrc and v == csrc:
                n["inputs"][key] = prev_c
    return wf


def tweak_workflow(wf: dict, tw: dict) -> dict:
    """Per-checkpoint settings applied to the workflow's own numbers (the workflow itself stays shared):
    ref_weight / ref_end go to the IPAdapter node that applies the reference to the model, hires_denoise to the second (refining)
    sampler (denoise below 1), steps to the first sampler. Anything left blank keeps the workflow's own value."""
    rw, re_, hd, st = _tw_num(tw, "ref_weight", 0, 2), _tw_num(tw, "ref_end", 0, 1), _tw_num(tw, "hires_denoise", 0, 1), _tw_num(tw, "steps", 1, 150)
    rs, cg = _tw_num(tw, "ref_start", 0, 1), _tw_num(tw, "cfg", 1, 30)
    rtype, rscale = str(tw.get("ref_type") or "").strip(), str(tw.get("ref_scaling") or "").strip()
    if str(tw.get("refine", "")).strip().lower() == "off":
        # skip the refine pass: whatever decodes the refined latent decodes the first sampler's result instead (unused nodes are not run)
        for rid, r in wf.items():
            ri = r.get("inputs") if isinstance(r, dict) else None
            if not (isinstance(ri, dict) and str(r.get("class_type", "")).startswith("KSampler") and isinstance(ri.get("denoise"), (int, float)) and ri["denoise"] < 1):
                continue
            up = wf.get((ri.get("latent_image") or [None])[0]) if isinstance(ri.get("latent_image"), list) else None
            src = (up or {}).get("inputs", {}).get("samples") if isinstance(up, dict) and "Upscale" in str(up.get("class_type", "")) else ri.get("latent_image")
            if not isinstance(src, list):
                continue
            for n in wf.values():
                ni = n.get("inputs") if isinstance(n, dict) else None
                if isinstance(ni, dict) and ni.get("samples") == [rid, 0]:
                    ni["samples"] = src
    cs = _tw_num(tw, "clip_skip", 1, 12)
    if cs and cs > 1:       # Pony models are trained with CLIP skip 2: the text encoders read the CLIP from a "set last layer" node
        ck = next((k for k, n in wf.items() if isinstance(n, dict) and n.get("class_type") == "CheckpointLoaderSimple"), None)
        if ck and "clipskip" not in wf:
            wf["clipskip"] = {"class_type": "CLIPSetLastLayer", "inputs": {"stop_at_clip_layer": -int(cs), "clip": [ck, 1]}}
            for n in wf.values():
                ni = n.get("inputs") if isinstance(n, dict) else None
                if isinstance(ni, dict) and ni.get("clip") == [ck, 1] and n.get("class_type") != "CLIPSetLastLayer":
                    ni["clip"] = ["clipskip", 0]
    for node in wf.values():
        inp = node.get("inputs") if isinstance(node, dict) else None
        if not isinstance(inp, dict):
            continue
        cls = str(node.get("class_type", ""))
        if "IPAdapter" in cls and "end_at" in inp:
            if rw is not None and isinstance(inp.get("weight"), (int, float)):
                inp["weight"] = rw
            if re_ is not None and isinstance(inp.get("end_at"), (int, float)):
                inp["end_at"] = re_
            if rs is not None and isinstance(inp.get("start_at"), (int, float)):
                inp["start_at"] = rs
            if rtype and "weight_type" in inp:
                inp["weight_type"] = rtype            # e.g. "ease in-out", "strong middle", "style transfer"
            if rscale and "embeds_scaling" in inp:
                inp["embeds_scaling"] = rscale        # "V only", "K+V", "K+V w/ C penalty", "K+mean(V) w/ C penalty"
        elif cls.startswith("KSampler") and isinstance(inp.get("denoise"), (int, float)):
            if inp["denoise"] < 1 and hd is not None:
                inp["denoise"] = hd
            elif inp["denoise"] >= 1 and st is not None:
                inp["steps"] = int(st)
            if inp["denoise"] >= 1 and cg is not None and isinstance(inp.get("cfg"), (int, float)):
                inp["cfg"] = cg
    return wf


FACE_REF_FILE = "anima_ref_adapter_v2_r64.safetensors"       # the Anima reference adapter (ComfyUI-Anima-Ref-Adapter add-on): keeps a character's face from one picture
FACE_REF_NODE = "AnimaRefAdapter"
FACE_REF_GUIDANCE = 2.0      # the add-on suggests 4.5; 2.0 left more room for expressions and clothing changes in tests with Vivid Gloom
HEAD_CROP = 0.40             # how far down a head-only reference reaches (share of the picture's height)


async def face_ref_status(base: str) -> tuple[bool, bool]:
    """(ComfyUI answers, it has both the add-on and the adapter file). The file is looked for in ComfyUI's own LoRA list: the add-on's list also names files it would download, which the app must not set off."""
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            node = (await c.get(f"{base}/object_info/{FACE_REF_NODE}")).json()
            loras = (await c.get(f"{base}/object_info/LoraLoaderModelOnly")).json()["LoraLoaderModelOnly"]["input"]["required"]["lora_name"][0]
    except Exception:
        return False, False
    return True, FACE_REF_NODE in node and FACE_REF_FILE in loras


def head_crop(data: bytes, bottom: float = HEAD_CROP) -> bytes:
    """The top of a portrait (head, hair, a little neck), so a reference keeps the face without the clothes. `bottom` = how far down it reaches; a deeper crop (for a close-up) is also wider."""
    from PIL import Image
    bottom = max(0.2, min(0.9, float(bottom)))
    im = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = im.size
    side = 0.2 if bottom <= 0.5 else 0.1
    out = io.BytesIO()
    im.crop((int(w * side), 0, int(w * (1 - side)), max(2, int(h * bottom)))).save(out, "PNG")
    return out.getvalue()


def add_face_ref(wf: dict, image: str) -> bool:
    """Put the reference adapter between each sampler and its model (after any LoRAs). False when the workflow has no sampler or VAE to wire it to."""
    samplers = [n for n in wf.values() if isinstance(n, dict) and str(n.get("class_type", "")).startswith("KSampler") and isinstance((n.get("inputs") or {}).get("model"), list)]
    vae = next(([k, 0] for k, n in wf.items() if isinstance(n, dict) and n.get("class_type") == "VAELoader"), None)
    if not vae:
        vae = next((n["inputs"]["vae"] for n in wf.values() if isinstance(n, dict) and n.get("class_type") == "VAEDecode" and isinstance(n.get("inputs", {}).get("vae"), list)), None)
    if not samplers or not vae:
        return False
    wf["faceref_image"] = {"class_type": "LoadImage", "inputs": {"image": image}}
    for i, n in enumerate(samplers):
        wf[f"faceref{i}"] = {"class_type": FACE_REF_NODE, "inputs": {"model": n["inputs"]["model"], "vae": vae, "image": ["faceref_image", 0], "lora_name": FACE_REF_FILE,
                                                                    "ref_guidance": FACE_REF_GUIDANCE, "correction": "apg"}}
        n["inputs"]["model"] = [f"faceref{i}", 0]
    return True


def face_region(data: bytes, tight: "bool | str" = False) -> bytes:
    """Crop a reference picture to where the face is: the app draws portraits upper-body, centred and facing the viewer, so the middle half
    of the width and the top 62% holds the head and shoulders. The face model only sees a small square of the whole picture, so a tight
    crop gives it much more of the face to match. Returns the picture unchanged if it can't be read."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        out = io.BytesIO()
        if tight == "head":     # the head with its hair and a little neck, but no clothes: a style and hair reference for pictures where the outfit must change (explicit pictures drawn by a FLUX-type maker)
            box = (0.20, 0.0, 0.80, 0.40)
            im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), max(2, int(h * box[3])))).save(out, "PNG")
        elif tight:     # only the face, about square: for pictures where the clothes and the place must change (explicit pictures), so the outfit and background of the reference do not carry over
            box = (0.37, 0.05, 0.63, 0.34) if w >= h else (0.30, 0.03, 0.70, 0.27)
            im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), max(2, int(h * box[3])))).save(out, "PNG")
        else:
            im.crop((int(w * 0.25), 0, int(w * 0.75), max(1, int(h * 0.62)))).save(out, "PNG")
        return out.getvalue()
    except Exception:
        return data


import contextvars
import time as _time
STOP = {"at": 0.0, "bases": set()}      # "Stop pictures": anything started before this moment is abandoned
JOB_STARTED = contextvars.ContextVar("job_started", default=None)   # a background job's start time, so every picture of a queued job stops, not just the one drawing


def _stopped(t0) -> bool:
    return bool(t0) and STOP["at"] > t0


async def stop_all() -> None:
    """Abandon queued and running pictures. Local ComfyUI is told to interrupt and clear its queue; an online service's request already in flight can't be recalled (it just finishes and is thrown away)."""
    STOP["at"] = _time.time()
    for base in list(STOP["bases"]):
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                await c.post(base + "/queue", json={"clear": True})
                await c.post(base + "/interrupt")
        except Exception:
            pass


def _warn_lora_family(model: str, loras: list[tuple[str, float]]) -> None:
    """A LoRA made for another kind of model is applied without any error and changes nothing: say so in the server log (only for models the app can tell the kind of)."""
    try:
        from . import catalog
        if not catalog.diffusion_kind(model):
            return
        mf = catalog.model_family(model)
        for name, _ in loras:
            lf = catalog.lora_family(name)
            if catalog.lora_fits(mf, lf) == "no":
                logging.getLogger("uvicorn.error").warning("LoRA %s was made for %s, not %s (%s): it will load without an error but change nothing.",
                                                         name, catalog.LORA_FAMILY_LABEL.get(lf, lf), catalog.LORA_FAMILY_LABEL.get(mf, mf), model)
    except Exception:
        pass


async def _comfyui(p, prompt, negative, shape, refs, seed):
    base = (p.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
    STOP["bases"].add(base)
    t0 = _time.time()
    text = p.get("workflow_ref") if (refs and p.get("workflow_ref")) else p.get("workflow")
    try:
        wf = json.loads(text) if text else copy.deepcopy(STARTER_WORKFLOW)
    except ValueError as e:
        raise ImageError(f"The ComfyUI workflow in Settings is not valid JSON: {e}")
    w, h = p["_size"] if p.get("_size") else SHAPES[shape]      # "_size": the animate (video) button picks its own, smaller canvas
    if p.get("_scale"):       # "Larger scene pictures" (story art options): a bigger canvas, rounded to what the model likes (multiples of 64)
        w, h = (int(round(v * float(p["_scale"]) / 64)) * 64 for v in (w, h))
    if p.get("ref_megapixels"):   # a species reference is shown at a tiny size, so only its broad look (colours, crests, ears, build) carries over, not the individual's face
        for n in wf.values():
            if isinstance(n, dict) and n.get("class_type") == "ImageScaleToTotalPixels":
                n.setdefault("inputs", {})["megapixels"] = float(p["ref_megapixels"])
    tw = (p.get("model_tweaks") or {}).get(p.get("model", "")) or {}   # settings for this checkpoint only (Settings -> Pictures -> Local)
    if tw:
        wf = tweak_workflow(wf, tw)
        n = _tw_num(tw, "ref_count", 1, 3)       # use only the first N references (the face you picked comes first; later ones may have drifted)
        if n:
            refs = refs[: int(n)]
        if str(tw.get("ref_crop", "")).strip().lower() == "face":
            refs = [(face_region(d, p.get("_tight_face") or False), "image/png") for d, m in refs]
    loras = parse_loras(tw.get("loras"))      # LoRAs chosen for this checkpoint (Settings -> Pictures -> Local)
    if loras:
        _warn_lora_family(p.get("model", ""), loras)
    if loras:
        wf = add_loras(wf, loras)
        prompt = with_trigger_words(prompt, lora_trigger_words(tw.get("loras")))      # a LoRA only shows its effect when its trigger words are in the prompt
    # optional tweaks added to every picture; a checkpoint's own words replace the shared ones
    xpos = (tw.get("extra_positive") or "").strip() or (p.get("extra_positive") or "").strip()
    xneg = (tw.get("extra_negative") or "").strip() or (p.get("extra_negative") or "").strip()
    if xpos:
        prompt = f"{prompt}, {xpos}"
    if xneg:
        negative = f"{negative}, {xneg}" if negative else xneg
    if seed is None and os.environ.get("STORYTELLER_TEST_SEED"):     # tuning runs only: the same seed for every picture, so settings can be compared fairly
        seed = int(os.environ["STORYTELLER_TEST_SEED"])
    values = {"prompt": prompt, "negative": negative, "seed": seed if seed is not None else random.randint(0, 2**32 - 1),
              "width": w, "height": h, "model": p.get("model", "")}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            names = []
            slots = [int(n) for n in re.findall(r"\{\{ref(\d)\}\}", json.dumps(wf))]   # don't upload pictures a workflow has no slot for
            for i, (d, m) in enumerate(refs[: min(3, max(slots, default=0))]):
                up = await c.post(base + "/upload/image", files={"image": (f"storyteller_ref{i}.{sniff(d)[0]}", d, m)},
                                  data={"overwrite": "true"})
                _fail(up, "ComfyUI upload")
                names.append(up.json()["name"])
            for i in range(3):   # a workflow with 3 reference slots still works with fewer pictures
                if names:
                    values[f"ref{i + 1}"] = names[min(i, len(names) - 1)]
            wf = fill_workflow(wf, values)
            ref = p.get("_face_ref")      # {"data": picture bytes, "used": False}: keep this face. Skipped (an ordinary picture) when the add-on or its file is missing
            if ref and (await face_ref_status(base))[1]:
                name = "storyteller_face_" + hashlib.sha1(ref["data"]).hexdigest()[:12] + ".png"
                wired = copy.deepcopy(wf)
                if add_face_ref(wired, name):
                    up = await c.post(base + "/upload/image", files={"image": (name, ref["data"], "image/png")}, data={"overwrite": "true"})
                    _fail(up, "ComfyUI upload")
                    wf, ref["used"] = wired, True
            sub = await c.post(base + "/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex})
            if sub.status_code >= 400 and "lora_name" in sub.text and "not in list" in sub.text.lower():
                raise ImageError("A LoRA chosen for this checkpoint is not in ComfyUI's loras folder (or its name changed). Remove it or pick another under 🖼 Art → Picture makers → Picture settings (Settings for this model).")
            if sub.status_code >= 400:
                raise ImageError(f"ComfyUI rejected the workflow ({sub.status_code}): {sub.text[:600]}")
            pid = sub.json()["prompt_id"]
            for _ in range(int(p.get("timeout_s", 300))):
                if _stopped(JOB_STARTED.get() or t0):
                    raise ImageError("Stopped.")
                hist = (await c.get(f"{base}/history/{pid}")).json()
                if pid in hist:
                    entry = hist[pid]
                    if (entry.get("status") or {}).get("status_str") == "error":
                        raise ImageError("ComfyUI reported an error while running the workflow (check its console).")
                    if p.get("_video"):       # animate: the workflow's SaveVideo / SaveWEBM / VHS node writes an .mp4 or .webm (listed under images, videos or gifs)
                        for out in (entry.get("outputs") or {}).values():
                            for img in [*out.get("videos", []), *out.get("gifs", []), *out.get("images", [])]:
                                if str(img.get("filename", "")).lower().endswith((".mp4", ".webm")) and img.get("type", "output") == "output":
                                    v = await c.get(f"{base}/view", params={"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": "output"})
                                    _fail(v, "ComfyUI video fetch")
                                    return v.content
                        raise ImageError("The workflow finished but saved no video. It needs a SaveVideo (or SaveWEBM) node ending in an .mp4 or .webm.")
                    for out in (entry.get("outputs") or {}).values():
                        for img in out.get("images", []):
                            if img.get("type", "output") == "output":
                                v = await c.get(f"{base}/view", params={"filename": img["filename"], "subfolder": img.get("subfolder", ""), "type": "output"})
                                _fail(v, "ComfyUI image fetch")
                                return v.content
                    raise ImageError("The workflow finished but produced no saved image. It needs a SaveImage node.")
                await asyncio.sleep(1)
    except httpx.ConnectError:
        raise ImageError(f"Can't reach ComfyUI at {base}. Is it running?")
    raise ImageError("ComfyUI took too long to finish.")


def sniff_video(data: bytes) -> tuple[str, str]:
    if data[4:8] == b"ftyp":
        return "mp4", "video/mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm", "video/webm"
    raise ImageError("That is not an MP4 or WebM video.")


# ---------------- imported workflows and the video model / LoRAs ----------------
_TEXT_HOPS = ("positive", "negative", "conditioning", "conditioning_1", "cond", "prompt")


def _text_node(wf: dict, link, want: str, depth: int = 0):
    """The node holding the prompt text that a sampler's positive / negative input comes from (through conditioning nodes that only pass it on)."""
    if depth > 8 or not isinstance(link, list) or not link:
        return None
    n = wf.get(str(link[0]))
    inp = n.get("inputs") if isinstance(n, dict) else None
    if not isinstance(inp, dict):
        return None
    if isinstance(inp.get("text"), str):
        return n
    for key in ((want,) if want in inp else ()) + tuple(k for k in _TEXT_HOPS if k in inp and k not in ("positive", "negative")):
        r = _text_node(wf, inp.get(key), want, depth + 1)
        if r:
            return r
    return None


def prepare_workflow(wf: dict, kind: str = "image") -> tuple[dict, list[str]]:
    """Take a workflow exported from ComfyUI ("Save (API Format)") and put the app's placeholders in the right places, so it can be used as it is:
    {{prompt}} / {{negative}} on the text the sampler reads, {{seed}} on the sampler's seed, {{width}} / {{height}} on the canvas size, and {{ref1}} on the first
    LoadImage (kind "ref" = a face-matching picture workflow, "video" = the picture that gets animated). Places that already hold a placeholder, or that
    cannot be found, are left alone. Returns (workflow, plain-language notes)."""
    notes, done = [], set()
    nodes = {k: n for k, n in wf.items() if isinstance(n, dict) and isinstance(n.get("inputs"), dict)}
    samplers = [n for n in nodes.values() if "positive" in n["inputs"] and "negative" in n["inputs"]] or [n for n in nodes.values() if str(n.get("class_type", "")).startswith("KSampler")]
    for sm in samplers:
        for want, ph in (("positive", "{{prompt}}"), ("negative", "{{negative}}")):
            if ph in done:
                continue
            tn = _text_node(wf, sm["inputs"].get(want), want)
            if tn and "{{" not in str(tn["inputs"].get("text", "")):
                tn["inputs"]["text"] = ph
                done.add(ph)
                notes.append(f"the {want} prompt text now reads {ph}")
            elif tn:
                done.add(ph)
        for key in ("seed", "noise_seed"):
            if "{{seed}}" not in done and isinstance(sm["inputs"].get(key), (int, float)) and not isinstance(sm["inputs"].get(key), bool):
                sm["inputs"][key] = "{{seed}}"
                done.add("{{seed}}")
                notes.append("the sampler's seed now reads {{seed}}")
    for n in nodes.values():
        i = n["inputs"]
        if "{{width}}" not in done and isinstance(i.get("width"), (int, float)) and isinstance(i.get("height"), (int, float)) and not isinstance(i.get("width"), bool) \
                and re.search(r"latent|ImageToVideo|Video", str(n.get("class_type", "")), re.IGNORECASE):
            i["width"], i["height"] = "{{width}}", "{{height}}"
            done.add("{{width}}")
            notes.append("the canvas size now reads {{width}} and {{height}}")
    if kind in ("ref", "video"):
        li = next((n for n in nodes.values() if n.get("class_type") == "LoadImage"), None)
        if li and "{{ref1}}" not in str(li["inputs"].get("image", "")):
            li["inputs"]["image"] = "{{ref1}}"
            notes.append("the first Load Image node now reads {{ref1}} (the picture the app hands over)")
        elif not li:
            notes.append("WARNING: there is no Load Image node, so no picture can be handed to this workflow" + (" (a text-to-video workflow cannot animate a picture)" if kind == "video" else ""))
    classes = {str(n.get("class_type", "")) for n in nodes.values()}
    if kind == "video":
        if not any(re.search(r"SaveVideo|SaveWEBM|VideoCombine|SaveAnimated", c) for c in classes):
            notes.append("WARNING: there is no Save Video node (SaveVideo, SaveWEBM or Video Combine), so the finished clip cannot be found")
    elif not any(c.startswith("SaveImage") or c == "PreviewImage" for c in classes):
        notes.append("WARNING: there is no Save Image node")
    if "{{prompt}}" not in done and "{{prompt}}" not in json.dumps(wf):
        notes.append("WARNING: no prompt text was found to fill in, so every picture would use the prompt typed in the file")
    return wf, notes


_VIDEO_LOADERS = ("UNETLoader", "UnetLoaderGGUF", "DiffusionModelLoader", "CheckpointLoaderSimple")


def _noise_tag(name: str) -> str:
    n = str(name).lower()
    return "high" if "high" in n else "low" if "low" in n else ""


_MOTION_LORA = re.compile(r"motion|vbvr|boost", re.IGNORECASE)


def scale_motion_loras(wf: dict, factor: float) -> dict:
    """Turn the motion-adding LoRAs (Motion Booster, VBVR...) up or down by a factor. Repair/turbo/style LoRAs are left alone."""
    if abs(factor - 1.0) < 0.01:
        return wf
    for n in wf.values():
        if isinstance(n, dict) and str(n.get("class_type", "")).startswith("LoraLoader"):
            name = str(n.get("inputs", {}).get("lora_name", ""))
            if _MOTION_LORA.search(name) and not re.search(r"repair", name, re.IGNORECASE):
                for key in ("strength_model", "strength_clip"):
                    if isinstance(n["inputs"].get(key), (int, float)):
                        n["inputs"][key] = round(n["inputs"][key] * factor, 3)
    return wf


def apply_video_settings(wf: dict, model: str, loras: list[tuple[str, float]]) -> dict:
    """The app-wide video model and LoRAs over the video workflow. A workflow with one model loader gets the chosen model; one with a high-noise and a low-noise
    loader (Wan 2.2) gets it in the loader of the same kind (or in both when the file name says neither). LoRAs are added to every model loader (model only)."""
    loaders = [k for k, n in wf.items() if isinstance(n, dict) and n.get("class_type") in _VIDEO_LOADERS and isinstance(n.get("inputs"), dict)]
    field = lambda k: "ckpt_name" if wf[k]["class_type"].startswith("Checkpoint") else "unet_name"
    if model:
        tag = _noise_tag(model)
        for k in loaders:
            if len(loaders) == 1 or not tag or _noise_tag(wf[k]["inputs"].get(field(k), "")) == tag:
                wf[k]["inputs"][field(k)] = model
    for k in loaders:
        if wf[k]["class_type"].startswith("Checkpoint"):
            continue
        prev = [k, 0]
        made = []
        for i, (name, st) in enumerate(loras):
            nid = f"vlora{k}_{i + 1}"
            wf[nid] = {"class_type": "LoraLoaderModelOnly", "inputs": {"lora_name": name, "strength_model": st, "model": prev}}
            made.append(nid)
            prev = [nid, 0]
        if made:
            for k2, n in wf.items():
                if k2 in made or not isinstance(n, dict) or not isinstance(n.get("inputs"), dict):
                    continue
                for key, v in n["inputs"].items():
                    if v == [k, 0]:
                        n["inputs"][key] = prev
    return wf


async def animate(provider: dict, still: bytes, prompt: str, size: tuple[int, int], negative: str | None = None, motion_scale: float = 1.0) -> tuple[bytes, str]:
    """Turn one still picture into a short looping clip with the ComfyUI video workflow (Settings -> Pictures -> Local -> Video workflow).
    The workflow gets {{ref1}} (the picture), {{prompt}}, {{negative}}, {{seed}}, {{width}}, {{height}}."""
    wf = (provider.get("video_workflow") or "").strip()
    if provider.get("type") != "comfyui" or not wf:
        raise ImageError("Animating needs a ComfyUI picture maker with a Video workflow filled in (🖼 Art → Picture makers → Video generation).")
    vm, vl = (provider.get("video_model") or "").strip(), parse_loras(provider.get("video_loras"))
    if vl:
        prompt = with_trigger_words(prompt, lora_trigger_words(provider.get("video_loras")))      # video LoRAs that need a trigger word get it in the motion prompt
    if vm or vl:       # the app-wide video model and LoRAs (Settings -> Picture makers -> Local)
        try:
            wf = json.dumps(apply_video_settings(json.loads(wf), vm, vl))
        except ValueError:
            pass
    if abs(motion_scale - 1.0) >= 0.01:
        try:
            wf = json.dumps(scale_motion_loras(json.loads(wf), motion_scale))
        except ValueError:
            pass
    p = {**provider, "workflow": wf, "workflow_ref": wf, "_video": True, "_size": size, "model": "", "timeout_s": 1800, "model_tweaks": {}}
    await localrt.before_image(provider)
    with localrt.hold():
        data = await _comfyui(p, prompt, negative if negative is not None else "static, still image, blurry, distorted, text, watermark", "portrait", [(still, sniff(still)[1])], None)
    if _stopped(JOB_STARTED.get()):
        raise ImageError("Stopped.")
    return data, sniff_video(data)[0]


GENERATORS = {"gemini": _gemini, "openai": _openai, "comfyui": _comfyui}


async def generate(provider: dict, prompt: str, negative: str = "", shape: str = "portrait", refs=None, seed=None) -> tuple[bytes, str]:
    fn = GENERATORS.get(provider.get("type"))
    if not fn:
        raise ImageError(f"Unknown image provider type: {provider.get('type')}")
    shape = shape if shape in SHAPES else "portrait"
    t0 = JOB_STARTED.get() or _time.time()
    if _stopped(t0):
        raise ImageError("Stopped.")
    local = provider.get("type") == "comfyui"
    if local:
        await localrt.before_image(provider)     # starts ComfyUI / makes room if the player turned that on (Settings -> Local models)
    held = localrt.hold() if local else None
    if held:
        held.__enter__()       # never stopped by the idle timer while drawing (a slow first load can take minutes)
    try:
        data = await fn(provider, prompt, negative, shape, list(refs or []), seed)
        if local and provider.get("nsfw_maker") and refs and not provider.get("_video") and provider.get("check_faces", True):
            # A reference-picture maker sometimes draws the character twice (two heads): count the faces and redraw with another seed, up to twice
            from . import facecheck
            for _try in range(2):
                n = await facecheck.count_faces(data)
                if n is None or n < 2 or _stopped(t0):
                    break
                logging.getLogger("uvicorn.error").info("Adult picture showed %d faces; redrawing with another seed.", n)
                data = await fn(provider, prompt, negative, shape, list(refs or []), random.randint(0, 2**32 - 1))
    finally:
        if held:
            held.__exit__(None, None, None)
    if _stopped(t0):
        raise ImageError("Stopped.")
    try:
        usage.record({"purpose": "pictures", "kind": "image", "provider": provider.get("id", ""), "model": provider.get("model", ""), "images": 1})
    except Exception:
        pass
    return data, sniff(data)[0]


# ---------------- prompt composition ----------------
_WEIGHT_RE = re.compile(r"\(([^():]+):[0-9.]+\)")


def plain(text: str) -> str:
    """Remove Stable-Diffusion emphasis syntax like (dark atmosphere:1.3): fine for tag models, noise for sentence models."""
    return _WEIGHT_RE.sub(r"\1", text or "")


SAFE_LINE = "Safe for work: no nudity or explicit content."
ADULT_LINE = ("Adult content is allowed. Every character shown is an adult (18 or older) with clearly adult features and proportions; "
              "never depict anyone who looks like a minor.")


def content_line(nsfw: bool) -> str:
    """What the picture prompt says about content: follows the story's adult-content switch (adults only, always)."""
    return ADULT_LINE if nsfw else SAFE_LINE

STYLE_PRESETS = [   # id, label, art direction (added to every picture prompt of the story)
    {"id": "anime", "family": "anime", "label": "Anime (clean cel shading)", "direction": "anime illustration, clean line art, soft cel shading, vibrant colors, detailed eyes"},
    {"id": "lightnovel", "family": "anime", "label": "Light-novel illustration", "direction": "anime light-novel illustration, soft cel shading, muted pastel palette, delicate lighting, detailed background"},
    {"id": "manga", "family": "anime", "label": "Manga (black and white)", "direction": "black and white manga art, screentone shading, bold ink lines, expressive faces"},
    {"id": "semireal", "family": "real", "label": "Semi-realistic digital painting", "direction": "semi-realistic digital painting, soft painterly shading, natural proportions, cinematic lighting"},
    {"id": "realistic", "family": "real", "label": "Realistic (photographic)", "direction": "photorealistic portrait photography, natural skin texture, realistic lighting, shallow depth of field"},
    {"id": "comic", "family": "any", "label": "Western comic book", "direction": "western comic book art, bold inks, dynamic shading, saturated colors, halftone texture"},
    {"id": "watercolor", "family": "any", "label": "Watercolor storybook", "direction": "soft watercolor illustration, gentle washes, visible paper texture, warm storybook palette"},
    {"id": "painterly", "family": "any", "label": "Painterly fantasy (oil)", "direction": "painterly fantasy oil painting, rich colors, dramatic lighting, visible brush strokes"},
    {"id": "scifi", "family": "any", "label": "Futuristic sci-fi concept art (cinematic, painterly)",
     "direction": "unified cinematic science-fiction concept art, hand-painted digital illustration, grounded industrial design, colossal scale, intricate but readable mechanical detail, atmospheric depth, dramatic rim lighting, muted gunmetal and deep navy with restrained amber highlights, subtle film grain, cohesive color grading, painterly realism, consistent materials and proportions, layered depth, strong focal point, readable action",
     "negative": "anime, cartoon styling, glossy toy-like surfaces, excessive neon, cluttered composition, inconsistent lighting, illegible details, text, watermark"},
    {"id": "chibi", "family": "anime", "label": "Cute chibi", "direction": "cute chibi illustration, big heads, simple shapes, bright colors, clean outlines"},
]
STYLE_MATCH = "Draw in exactly the same art style as the reference pictures (their style, line work and colors take priority over any style words above)."


def scene_prompt(art: dict, scene: str, chars: list[dict], max_refs_used: int, nsfw: bool = False, clothes_from_ref: bool = True) -> str:
    """Scene prompt: art direction + the scene + who is in it. `chars` items: {"name", "appearance", "ref": bool}."""
    lines = [plain(art.get("direction") or "").strip(), f"Scene: {scene.strip()}"]
    if chars:
        lines.append("Characters in this scene:")
        refd = [c for c in chars if c.get("ref")][:max_refs_used]
        for c in chars:
            n = refd.index(c) + 1 if c in refd else None
            lines.append(f"- {c['name']}: {c.get('appearance', '').strip()}" + (f" (reference image {n})" if n else ""))
        if refd:
            lines.append(("Reference images are attached. For each character, copy the face, hair and clothing from their reference "
                          "image. Do NOT copy the reference's background, pose, camera angle or lighting: those come from the scene above. " if clothes_from_ref else
                          "Reference images are attached. For each character, copy only the face and hair from their reference image. Their clothing, pose, camera angle, "
                          "background and lighting all come from the scene above (what they wear, or do not wear, is decided there). ") + STYLE_MATCH)
    lines.append(content_line(nsfw))
    return "\n\n".join(x for x in lines if x)


def nsfw_portrait_prompt(art: dict, name: str, look: str, mood: str, framing: str, words: str) -> str:
    """A short, subject-first prompt for the adult picture maker (an SDXL-style model that reads words, not instructions): the story's style, who (face and body only, no outfit),
    what the picture shows (the mood and its explicit words) and the framing. Long instruction sentences and the character's everyday outfit are left out: they pull the picture back to
    a clothed, neutral portrait."""
    head = plain(art.get("direction") or "").strip()
    who = f"{name}: {look}".strip(": ")
    return "\n\n".join(x for x in (head, who, (mood + ". Make the expression and body language show it clearly.") if mood else "", words, framing, "Plain, softly lit, simple indoor background.", ADULT_LINE) if x)


MOOD_EMPHASIS = ("The mood must be unmistakable at a glance: show it strongly in the face (eyes, eyebrows, mouth) AND in the body language (head tilt, "
                 "shoulders, posture, arms and hands). Exaggerate it a little rather than understate it, and do not let a neutral reference expression carry over. "
                 "Frame from the waist up so the hands and posture are visible.")


def portrait_prompt(art: dict, name: str, appearance: str, mood: str | None, has_ref: bool, ext_only: bool = False, nsfw: bool = False, species_ref: str = "", framing: str = "", own_clothes: bool = True, species_clothes: str = "") -> str:
    head = plain(art.get("direction") or "").strip()
    if species_ref:   # the only reference is the first picture of this species: a different individual of the same kind
        if species_clothes:     # two references: the first shows a member of the species, the second the clothing it wears
            lead = (f"The first reference picture shows another member of the same species ({species_ref}). The second reference picture shows the {species_clothes} clothing this species wears: "
                    f"dress {name} in clothing of that style, cut and colours (do not copy that person's face or body). ")
        else:
            lead = f"The reference picture shows another member of the same species ({species_ref}). "
        body = (lead + f"Draw a DIFFERENT individual of that species: {name}: {appearance}. "
                f"Keep what is true of the whole species (skin or covering, ears, crests, eyes, build, proportions, clothing style) but give this individual their own face and features; "
                f"do NOT copy the reference's face. " + (f"Expression: {mood}. " if mood else "Neutral expression. ") + "Upper body portrait (framed from the chest up), facing the viewer, simple soft background.")
    elif ext_only:   # the only references come from ANOTHER story, drawn in a different style and outfit
        body = (f"Same character as the reference images ({name}: {appearance}). Keep their face, hair and identity. The references come from a different story with a different "
                f"art style, outfit and setting, so do NOT copy their style, clothing, background or lighting; redraw the character in the style and setting described here. "
                + (f"Expression: {mood}. " if mood else "Neutral expression. ") + "Upper body portrait (framed from the chest up), facing the viewer, simple soft background.")
    elif mood and has_ref and not own_clothes:     # an adult picture: the references give the face, hair and skin; what she wears (or not) comes from this description, never from the references
        body = (f"Same character as the reference images ({name}: {appearance}). Copy only the face, hair, skin tone and body build from the references. Do NOT copy their clothing: "
                f"the references show everyday clothes, but in this picture the clothing is exactly what the description says, and if it says nude or naked she wears nothing at all. "
                f"Do NOT copy their backgrounds, lighting or pose: the pose, body language and camera must change to show the new mood. New expression/mood: {mood}. Upper body portrait (framed from the chest up), facing the viewer, simple soft background. " + STYLE_MATCH)
    elif mood and has_ref:
        body = (f"Same character as the reference images ({name}: {appearance}). Copy the face, hair and clothing exactly from the "
                f"references; do NOT copy their backgrounds, lighting or pose: the pose, body language and camera must change to show the new mood. New expression/mood: {mood}. Upper body portrait (framed from the chest up), facing the viewer, simple soft background. " + STYLE_MATCH)
    else:
        body = f"Character portrait of {name}: {appearance}. Upper body portrait (framed from the chest up), facing the viewer, " + (f"expression: {mood}." if mood else "neutral expression.") + " Simple soft background."
    if framing:     # an action shot or a body on the ground instead of a chest-up portrait
        for f in ("Upper body portrait (framed from the chest up), facing the viewer, simple soft background.", "Upper body portrait (framed from the chest up), facing the viewer, "):
            body = body.replace(f, "")
        body = body.replace(" Simple soft background.", "").strip() + " " + framing
    if mood and mood.split()[0].lower() not in ("normal", "default", "neutral"):
        body += " " + (MOOD_EMPHASIS.replace(" Frame from the waist up so the hands and posture are visible.", "") if framing else MOOD_EMPHASIS)
    setting = plain(art.get("setting") or "").strip()
    fit = f"The character belongs to this world; dress and style them to fit it, but keep the background a plain soft backdrop (the setting only decides clothing and style): {setting}" if setting and (ext_only or not (mood and has_ref)) else ""
    return "\n\n".join(x for x in (head, body, fit, content_line(nsfw)) if x)
