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
import json
import random
import re
import uuid

import httpx

from . import localrt, usage

TIMEOUT = httpx.Timeout(connect=10, read=300, write=60, pool=10)

SHAPES = {"portrait": (832, 1216), "landscape": (1216, 832), "square": (1024, 1024)}   # sizes for local SDXL-class models
OPENAI_SIZES = {"portrait": "1024x1536", "landscape": "1536x1024", "square": "1024x1024"}
SHAPE_WORDS = {"portrait": "portrait orientation (2:3)", "landscape": "landscape orientation (3:2)", "square": "square (1:1)"}


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
    model = p.get("model") or "gpt-image-1"
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


async def _comfyui(p, prompt, negative, shape, refs, seed):
    base = (p.get("base_url") or "http://127.0.0.1:8188").rstrip("/")
    text = p.get("workflow_ref") if (refs and p.get("workflow_ref")) else p.get("workflow")
    try:
        wf = json.loads(text) if text else copy.deepcopy(STARTER_WORKFLOW)
    except ValueError as e:
        raise ImageError(f"The ComfyUI workflow in Settings is not valid JSON: {e}")
    w, h = SHAPES[shape]
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
            sub = await c.post(base + "/prompt", json={"prompt": wf, "client_id": uuid.uuid4().hex})
            if sub.status_code >= 400:
                raise ImageError(f"ComfyUI rejected the workflow ({sub.status_code}): {sub.text[:600]}")
            pid = sub.json()["prompt_id"]
            for _ in range(int(p.get("timeout_s", 300))):
                hist = (await c.get(f"{base}/history/{pid}")).json()
                if pid in hist:
                    entry = hist[pid]
                    if (entry.get("status") or {}).get("status_str") == "error":
                        raise ImageError("ComfyUI reported an error while running the workflow (check its console).")
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


GENERATORS = {"gemini": _gemini, "openai": _openai, "comfyui": _comfyui}


async def generate(provider: dict, prompt: str, negative: str = "", shape: str = "portrait", refs=None, seed=None) -> tuple[bytes, str]:
    fn = GENERATORS.get(provider.get("type"))
    if not fn:
        raise ImageError(f"Unknown image provider type: {provider.get('type')}")
    shape = shape if shape in SHAPES else "portrait"
    local = provider.get("type") == "comfyui"
    if local:
        await localrt.before_image(provider)     # starts ComfyUI / makes room if the player turned that on (Settings -> Local models)
    try:
        data = await fn(provider, prompt, negative, shape, list(refs or []), seed)
    finally:
        if local:
            localrt.touch("comfy")
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


def scene_prompt(art: dict, scene: str, chars: list[dict], max_refs_used: int) -> str:
    """Scene prompt: art direction + the scene + who is in it. `chars` items: {"name", "appearance", "ref": bool}."""
    lines = [plain(art.get("direction") or "").strip(), f"Scene: {scene.strip()}"]
    if chars:
        lines.append("Characters in this scene:")
        refd = [c for c in chars if c.get("ref")][:max_refs_used]
        for c in chars:
            n = refd.index(c) + 1 if c in refd else None
            lines.append(f"- {c['name']}: {c.get('appearance', '').strip()}" + (f" (reference image {n})" if n else ""))
        if refd:
            lines.append("Reference images are attached. For each character, copy the face, hair and clothing from their reference "
                         "image. Do NOT copy the reference's background, pose, camera angle or lighting: those come from the scene above.")
    lines.append(SAFE_LINE)
    return "\n\n".join(x for x in lines if x)


def portrait_prompt(art: dict, name: str, appearance: str, mood: str | None, has_ref: bool, ext_only: bool = False) -> str:
    head = plain(art.get("direction") or "").strip()
    if ext_only:   # the only references come from ANOTHER story, drawn in a different style and outfit
        body = (f"Same character as the reference images ({name}: {appearance}). Keep their face, hair and identity. The references come from a different story with a different "
                f"art style, outfit and setting, so do NOT copy their style, clothing, background or lighting; redraw the character in the style and setting described here. "
                + (f"Expression: {mood}. " if mood else "Neutral expression. ") + "Upper body, facing the viewer, simple soft background.")
    elif mood and has_ref:
        body = (f"Same character as the reference images ({name}: {appearance}). Copy the face, hair and clothing exactly from the "
                f"references; do NOT copy their backgrounds or lighting. New expression/mood: {mood}. Upper body, facing the viewer, simple soft background.")
    else:
        body = f"Character portrait of {name}: {appearance}. Upper body, facing the viewer, " + (f"expression: {mood}." if mood else "neutral expression.") + " Simple soft background."
    setting = plain(art.get("setting") or "").strip()
    fit = f"The character belongs to this world; dress and style them to fit it: {setting}" if setting and (ext_only or not (mood and has_ref)) else ""
    return "\n\n".join(x for x in (head, body, fit, SAFE_LINE) if x)
