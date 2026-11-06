"""LLM providers. Every provider exposes the same async generator:

    stream_chat(provider, model, system, messages, temperature, max_tokens, num_ctx, **extra)

which yields text chunks (str) and, at the end, one Usage dict with token counts.
To add a new provider type, write one function and register it in STREAMERS.

`system` is a str or a list of {"text": ..., "cache": bool} blocks; `messages` may carry "cache": True
on a message. Those flags mark prompt-cache breakpoints (Claude). Other providers ignore them, but the
prompt layout is the same, so OpenAI-style automatic prefix caching and Ollama's KV-cache reuse also hit.
"""
import json
from typing import AsyncIterator

import httpx

from . import usage

TIMEOUT = httpx.Timeout(connect=10, read=300, write=30, pool=10)


class ProviderError(Exception):
    pass


class Usage(dict):
    """Token accounting for one request: input (new, uncached), cache_read, cache_write, output."""


def system_text(system) -> str:
    if isinstance(system, str):
        return system
    return "\n\n".join(b["text"] for b in system if b.get("text"))


def _plain(messages) -> list[dict]:
    return [{"role": m["role"], "content": m["content"]} for m in messages]


async def _check(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        body = (await resp.aread()).decode("utf-8", "replace")[:500]
        raise ProviderError(f"{resp.status_code} from {resp.url.host}: {body}")


_MERGE_SYSTEM: dict[str, bool] = {}


async def _merge_system(base: str, model: str) -> bool:
    """Mistral-style chat templates ([INST]...) paste the system prompt into the NEWEST user message, which moves it
    every turn and makes Ollama re-read the whole prompt. For those models we put it at the top of the FIRST user
    message instead, so the start of the prompt never changes and Ollama can reuse its cached copy."""
    if model not in _MERGE_SYSTEM:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.post(base + "/api/show", json={"model": model})
                _MERGE_SYSTEM[model] = "[INST]" in (r.json().get("template") or "")
        except Exception:
            return False  # don't cache a failed lookup
    return _MERGE_SYSTEM[model]


async def _ollama(p, model, system, messages, temperature, max_tokens, num_ctx, extra):
    base = p["base_url"].rstrip("/")
    url = base + "/api/chat"
    msgs = _plain(messages)
    if await _merge_system(base, model) and msgs and msgs[0]["role"] == "user":
        msgs[0]["content"] = system_text(system) + "\n\n" + msgs[0]["content"]
    else:
        msgs = [{"role": "system", "content": system_text(system)}] + msgs
    body = {
        "model": model,
        "messages": msgs,
        "stream": True,
        **({"think": False} if "qwen3" in model.lower() else {}),  # Qwen3 "thinks" first by default: slow, and not wanted in a story
        # Keep the model (and its KV cache of the story so far) in memory between turns.
        "keep_alive": extra.get("keep_alive") or "30m",
        "options": {"temperature": temperature, "num_predict": max_tokens, "num_ctx": num_ctx},
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            async with c.stream("POST", url, json=body) as r:
                await _check(r)
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    if d.get("error"):
                        raise ProviderError(d["error"])
                    chunk = d.get("message", {}).get("content", "")
                    if chunk:
                        yield chunk
                    if d.get("done"):
                        # prompt_eval_count is the TOTAL prompt size; how long it took to read is what shows reuse.
                        ev = d.get("eval_duration") or 0
                        yield Usage(
                            input=d.get("prompt_eval_count", 0),
                            prompt_ms=round((d.get("prompt_eval_duration") or 0) / 1e6),
                            output=d.get("eval_count", 0),
                            tok_s=round(d.get("eval_count", 0) / (ev / 1e9), 1) if ev else 0,
                        )
    except httpx.ConnectError:
        raise ProviderError("Can't reach Ollama. Is it installed and running? (try: ollama serve)")


async def _anthropic(p, model, system, messages, temperature, max_tokens, num_ctx, extra):
    if not p.get("api_key"):
        raise ProviderError("No API key set for this provider. Add one in Settings.")
    url = p["base_url"].rstrip("/") + "/v1/messages"
    headers = {
        "x-api-key": p["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    # Prompt caching: extra["cache_ttl"] is "5m", "1h", or falsy for off.
    ttl = extra.get("cache_ttl")
    cc = None
    if ttl:
        cc = {"type": "ephemeral"}
        if ttl == "1h":
            cc["ttl"] = "1h"
            headers["anthropic-beta"] = "extended-cache-ttl-2025-04-11"
    blocks = [{"text": system, "cache": True}] if isinstance(system, str) else system
    sys_out = []
    for b in blocks:
        blk = {"type": "text", "text": b["text"]}
        if cc and b.get("cache"):
            blk["cache_control"] = cc
        sys_out.append(blk)
    msgs = []
    for m in messages:
        content = [{"type": "text", "text": m["content"], "cache_control": cc}] if (cc and m.get("cache")) else m["content"]
        msgs.append({"role": m["role"], "content": content})
    # Claude's hidden thinking counts toward max_tokens, so a long think can use up the whole reply budget. The headroom
    # is only a ceiling (you pay for tokens actually used), added so thinking cannot cut the visible reply short.
    body = {
        "model": model,
        "system": sys_out,
        "messages": msgs,
        "max_tokens": max_tokens + int(extra.get("thinking_headroom") or 0),
        "temperature": min(temperature, 1.0),
        "stream": True,
    }
    # Newer Claude models "think" privately before answering by default. That is billed as output and can use up
    # the whole reply budget, so it is off unless the user turns it on in Settings.
    if extra.get("thinking") is False:
        body["thinking"] = {"type": "disabled"}

    # Newer Claude models "think" privately before answering by default. That is billed as output and can use up
    # the whole reply budget, so it is off unless the user turns it on in Settings.

    async def once():
        use = Usage(input=0, cache_read=0, cache_write=0, output=0, thinking=0)
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                await _check(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    d = json.loads(line[5:].strip())
                    t = d.get("type")
                    if t == "message_start":
                        u = d["message"].get("usage", {})
                        use["input"] = u.get("input_tokens", 0)
                        use["cache_read"] = u.get("cache_read_input_tokens", 0) or 0
                        use["cache_write"] = u.get("cache_creation_input_tokens", 0) or 0
                    elif t == "content_block_delta" and d["delta"].get("type") == "text_delta":
                        yield d["delta"]["text"]
                    elif t == "message_delta":
                        u = d.get("usage", {})
                        use["output"] = u.get("output_tokens", use["output"])
                        use["thinking"] = (u.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0
                    elif t == "error":
                        raise ProviderError(d["error"].get("message", "unknown error"))
        yield use

    if extra.get("effort"):
        body["output_config"] = {"effort": extra["effort"]}   # low/medium/high: how much Claude thinks before answering

    # Models differ on what they accept, so learn from the error and remember it per model:
    #  - newer models reject `temperature`;
    #  - "thinking off" is {"type": "disabled"} on most models but {"type": "between_tools"} on the newest.
    for attempt in range(4):
        if model in _NO_TEMPERATURE:
            body.pop("temperature", None)
        if model in _NO_EFFORT:
            body.pop("output_config", None)
        if extra.get("thinking") is False:
            body["thinking"] = _THINKING_OFF.get(model, {"type": "disabled"})
        try:
            async for item in once():
                yield item
            return
        except ProviderError as e:
            msg = str(e).lower()
            if attempt < 3 and "temperature" in body and "temperature" in msg and "deprecated" in msg:
                _NO_TEMPERATURE.add(model)
                continue
            if attempt < 3 and "thinking" in body and "between_tools" in msg:
                _THINKING_OFF[model] = {"type": "between_tools"}
                continue
            if attempt < 3 and "output_config" in body and ("effort" in msg or "output_config" in msg):
                _NO_EFFORT.add(model)   # this model does not take an effort setting
                continue
            raise


_NO_TEMPERATURE: set[str] = set()
_THINKING_OFF: dict[str, dict] = {}
_NO_EFFORT: set[str] = set()


_OAI_QUIRKS: dict[str, set] = {}   # "host|model" -> {"no_temp", "completion_tokens", "no_effort"} learned from 400 errors
_REASONING = ("gpt-5", "o1", "o3", "o4")


async def _openai(p, model, system, messages, temperature, max_tokens, num_ctx, extra):
    """Any OpenAI-compatible API: OpenAI, Gemini's compat endpoint, xAI, OpenRouter, LM Studio, etc.
    These cache identical prompt prefixes automatically, so the stable layout is all that is needed.
    Newer models are picky (max_completion_tokens instead of max_tokens, no temperature, reasoning effort), so the first 400 error
    teaches the app what the model dislikes and the request is retried."""
    base = p["base_url"].rstrip("/")
    url = base + "/chat/completions"
    headers = {"content-type": "application/json"}
    if p.get("api_key"):
        headers["authorization"] = f"Bearer {p['api_key']}"
    key = f"{base}|{model}"
    q = _OAI_QUIRKS.setdefault(key, set())
    reasoning = model.lower().startswith(_REASONING)
    if reasoning:
        q.add("completion_tokens")
    for _ in range(5):
        body = {"model": model, "messages": [{"role": "system", "content": system_text(system)}] + _plain(messages), "stream": True}
        if "no_usage" not in q:
            body["stream_options"] = {"include_usage": True}
        if "no_temp" not in q and not reasoning:
            body["temperature"] = temperature
        if "completion_tokens" in q:
            body["max_completion_tokens"] = max_tokens + (int(extra.get("thinking_headroom") or 0) if reasoning else 0)
        else:
            body["max_tokens"] = max_tokens
        if reasoning and "no_effort" not in q and extra.get("effort") in ("low", "medium", "high"):
            body["reasoning_effort"] = extra["effort"]
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            async with c.stream("POST", url, headers=headers, json=body) as r:
                if r.status_code == 400:
                    text = (await r.aread()).decode("utf-8", "replace")
                    low = text.lower()
                    if "max_completion_tokens" in low and "completion_tokens" not in q:
                        q.add("completion_tokens"); continue
                    if "stream_options" in low and "no_usage" not in q:
                        q.add("no_usage"); continue
                    if "temperature" in low and "no_temp" not in q:
                        q.add("no_temp"); continue
                    if "reasoning" in low and "no_effort" not in q:
                        q.add("no_effort"); continue
                    raise ProviderError(f"400 from {r.url.host}: {text[:500]}")
                await _check(r)
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    d = json.loads(data)
                    u = d.get("usage")
                    if u:
                        cached = ((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
                        yield Usage(input=max(0, (u.get("prompt_tokens") or 0) - cached), cache_read=cached, cache_write=0, output=u.get("completion_tokens") or 0)
                    for ch in d.get("choices", []):
                        chunk = (ch.get("delta") or {}).get("content")
                        if chunk:
                            yield chunk
                return
    raise ProviderError("The model kept rejecting the request settings.")


STREAMERS = {"ollama": _ollama, "anthropic": _anthropic, "openai": _openai}


async def stream_chat(provider, model, system, messages, temperature=0.9, max_tokens=2048, num_ctx=8192, **extra) -> AsyncIterator:
    fn = STREAMERS.get(provider["type"])
    if not fn:
        raise ProviderError(f"Unknown provider type: {provider['type']}")
    async for chunk in fn(provider, model, system, messages, temperature, max_tokens, num_ctx, extra):
        if isinstance(chunk, Usage):
            try:   # every call is logged for the usage report (see usage.py); never let that break a reply
                usage.record({"kind": "text", "provider": provider["id"], "model": model, "ttl": extra.get("cache_ttl"),
                              **{k: int(chunk.get(k, 0) or 0) for k in ("input", "cache_read", "cache_write", "output")}})
            except Exception:
                pass
        yield chunk


async def complete(provider, model, system, messages, **kw) -> str:
    out = ""
    async for chunk in stream_chat(provider, model, system, messages, **kw):
        if isinstance(chunk, str):
            out += chunk
    return out


async def list_ollama_models(provider) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(provider["base_url"].rstrip("/") + "/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []
