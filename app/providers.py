"""LLM providers. Every provider exposes the same async generator:

    stream_chat(provider, model, system, messages, temperature, max_tokens, num_ctx, **extra)

which yields text chunks (str) and, at the end, one Usage dict with token counts.
To add a new provider type, write one function and register it in STREAMERS.

`system` is a str or a list of {"text": ..., "cache": bool} blocks; `messages` may carry "cache": True
on a message. Those flags mark prompt-cache breakpoints (Claude). Other providers ignore them, but the
prompt layout is the same, so OpenAI-style automatic prefix caching and Ollama's KV-cache reuse also hit.
"""
import asyncio
import json
import re
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


def _plain(messages, style: str = "ollama") -> list[dict]:
    """Messages as plain role/content pairs. A message may carry "pics": [(mime, base64)] (a picture for a model that can see): sent as that API expects."""
    out = []
    for m in messages:
        pics = m.get("pics") or []
        if pics and style == "openai":
            out.append({"role": m["role"], "content": [{"type": "text", "text": m["content"]}] + [{"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b}"}} for mt, b in pics]})
        elif pics:
            out.append({"role": m["role"], "content": m["content"], "images": [b for _, b in pics]})
        else:
            out.append({"role": m["role"], "content": m["content"]})
    return out


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
    if extra.get("local"):      # Local mode (Settings): steadier sampling for prose, and valid JSON forced for the helpers that ask for a JSON object
        if JSON_ASK.search(system_text(system)):
            body["format"] = "json"
        elif temperature >= 0.6:
            body["options"].update({"top_p": 0.92, "min_p": 0.05, "repeat_penalty": 1.08})
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
                            cut=d.get("done_reason") == "length",      # stopped by the reply-length limit, not finished
                            truncated=bool(extra.get("local") and d.get("prompt_eval_count", 0) >= num_ctx - 8),   # the model read a full window: the oldest text was cut off
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
        if m.get("pics"):
            content = [{"type": "image", "source": {"type": "base64", "media_type": mt, "data": b}} for mt, b in m["pics"]] + ([{"type": "text", "text": content}] if isinstance(content, str) else content)
        msgs.append({"role": m["role"], "content": content})
    # Claude's hidden thinking counts toward max_tokens, so a long think can use up the whole reply budget. The headroom
    # is only a ceiling (you pay for tokens actually used), added so thinking cannot cut the visible reply short.
    body = {
        "model": model,
        "system": sys_out,
        "messages": msgs,
        "max_tokens": max_tokens + (int(extra.get("thinking_headroom") or 0) if extra.get("thinking") else 0),   # headroom only when Claude thinks; otherwise Max reply tokens is the real limit
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
                        if (d.get("delta") or {}).get("stop_reason") == "max_tokens":
                            use["cut"] = True          # stopped by the reply-length limit, not finished
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
    busy = 0   # "server busy" answers (503/529) seen so far: wait a moment and ask again, up to 3 times (nothing has been shown yet)
    for _ in range(8):
        body = {"model": model, "messages": [{"role": "system", "content": system_text(system)}] + _plain(messages, "openai"), "stream": True}
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
                if r.status_code in (503, 529) and busy < 3:
                    busy += 1
                    await r.aread()
                    await asyncio.sleep(2 * busy)
                    continue
                await _check(r)
                cut = False
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
                        yield Usage(input=max(0, (u.get("prompt_tokens") or 0) - cached), cache_read=cached, cache_write=0, output=u.get("completion_tokens") or 0, cut=cut)
                    for ch in d.get("choices", []):
                        if ch.get("finish_reason") == "length":
                            cut = True
                        chunk = (ch.get("delta") or {}).get("content")
                        if chunk:
                            yield chunk
                return
    raise ProviderError("The model kept rejecting the request settings.")


STREAMERS = {"ollama": _ollama, "anthropic": _anthropic, "openai": _openai}


# Local mode: one local GPU serves one request well. The narrator goes first; helper jobs (side panel, key events...) run one at a time and wait
# until no reply is being written.
JSON_ASK = re.compile(r"ONLY (?:a )?JSON(?: patch)?(?: in the shape)?:?\s*\{")
_pace = {"narr": 0, "lock": None}


async def _paced(provider, extra, gen):
    if not (extra.get("local") and provider.get("type") == "ollama"):
        async for c in gen:
            yield c
        return
    if extra.get("narrator"):
        _pace["narr"] += 1
        try:
            async for c in gen:
                yield c
        finally:
            _pace["narr"] -= 1
        return
    if _pace["lock"] is None:
        _pace["lock"] = asyncio.Lock()
    async with _pace["lock"]:
        while _pace["narr"] > 0:
            await asyncio.sleep(0.3)
        async for c in gen:
            yield c


async def stream_chat(provider, model, system, messages, temperature=0.9, max_tokens=2048, num_ctx=8192, **extra) -> AsyncIterator:
    fn = STREAMERS.get(provider["type"])
    if not fn:
        raise ProviderError(f"Unknown provider type: {provider['type']}")
    async for chunk in _paced(provider, extra, fn(provider, model, system, messages, temperature, max_tokens, num_ctx, extra)):
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


BATCH_CHUNK = 500          # requests per Message Batch
BATCH_GIVE_UP = 5400       # seconds: a batch that has not ended by then is cancelled (they usually take minutes; the service allows up to 24 hours)


async def batch_complete(provider, model, system: str, jobs: list[tuple[str, str]], *, max_tokens: int = 700, temperature: float = 0.2, concurrency: int = 4,
                         progress=None, poll: float = 15.0, use_batch: bool = True, **extra) -> tuple[dict[str, str], dict[str, str]]:
    """Many independent one-message jobs at once: [(id, user text)] with one shared system prompt. Returns ({id: reply text}, {id: error text}).
    Claude (type anthropic) uses the Message Batches service: half the price, results usually within minutes (slower than a normal call), so only for work
    nobody is waiting on live. Other providers (and use_batch=False) run the jobs as normal calls, a few at a time.
    `progress(done, total)` is called as results arrive."""
    total = len(jobs)
    out: dict[str, str] = {}
    err: dict[str, str] = {}
    if not jobs:
        return out, err
    peak = [0]
    _report, progress = progress, (lambda d, t: (peak.__setitem__(0, max(peak[0], d)), _report and _report(peak[0], t)))
    if not (use_batch and provider.get("type") == "anthropic" and provider.get("api_key")):
        sem = asyncio.Semaphore(max(1, concurrency))

        async def one(jid: str, text: str):
            async with sem:
                try:
                    out[jid] = await complete(provider, model, system, [{"role": "user", "content": text}], temperature=temperature, max_tokens=max_tokens, **extra)
                except Exception as e:
                    err[jid] = str(e)[:300] or e.__class__.__name__
                if progress:
                    progress(len(out) + len(err), total)
        await asyncio.gather(*(one(j, t) for j, t in jobs))
        return out, err

    base = provider["base_url"].rstrip("/") + "/v1/messages/batches"
    headers = {"x-api-key": provider["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"}
    todo = dict(jobs)
    for attempt in range(3):                       # a second and third round only for requests the model refused over a setting it does not take
        retry: dict[str, str] = {}
        ids = list(todo)
        for k in range(0, len(ids), BATCH_CHUNK):
            part = ids[k:k + BATCH_CHUNK]
            reqs = []
            for jid in part:
                params = {"model": model, "max_tokens": max_tokens, "system": [{"type": "text", "text": system}],
                          "messages": [{"role": "user", "content": todo[jid]}], "thinking": _THINKING_OFF.get(model, {"type": "disabled"})}
                if model not in _NO_TEMPERATURE:
                    params["temperature"] = min(temperature, 1.0)
                reqs.append({"custom_id": jid, "params": params})
            async with httpx.AsyncClient(timeout=TIMEOUT) as c:
                r = await c.post(base, headers=headers, json={"requests": reqs})
                await _check(r)
                bid = r.json()["id"]
                waited = 0.0
                while True:
                    st = (await c.get(f"{base}/{bid}", headers=headers)).json()
                    rc = st.get("request_counts") or {}
                    if progress:
                        progress(len(out) + len(err) + sum(rc.get(x, 0) for x in ("succeeded", "errored", "canceled", "expired")), total)
                    if st.get("processing_status") == "ended":
                        break
                    if waited > BATCH_GIVE_UP:
                        try:
                            await c.post(f"{base}/{bid}/cancel", headers=headers)
                        except Exception:
                            pass
                        raise ProviderError(f"The batch (id {bid}) was still not finished after {BATCH_GIVE_UP // 60} minutes, so it was cancelled. Try again later, or untick the cheaper batch option.")
                    await asyncio.sleep(poll)
                    waited += poll
                res = await c.get(st.get("results_url") or f"{base}/{bid}/results", headers=headers)
                await _check(res)
                for line in res.text.splitlines():
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    jid, rs = d.get("custom_id"), d.get("result") or {}
                    if rs.get("type") == "succeeded":
                        m = rs.get("message") or {}
                        out[jid] = "".join(b.get("text", "") for b in m.get("content", []) if b.get("type") == "text")
                        u = m.get("usage") or {}
                        try:
                            usage.record({"kind": "text", "provider": provider["id"], "model": model, "ttl": None, "batch": True,
                                          "input": int(u.get("input_tokens", 0) or 0), "cache_read": int(u.get("cache_read_input_tokens", 0) or 0),
                                          "cache_write": int(u.get("cache_creation_input_tokens", 0) or 0), "output": int(u.get("output_tokens", 0) or 0)})
                        except Exception:
                            pass
                    else:
                        e = ((rs.get("error") or {}).get("error") or rs.get("error") or {})
                        msg = (e.get("message") if isinstance(e, dict) else str(e)) or rs.get("type", "failed")
                        low = msg.lower()
                        if attempt < 2 and "temperature" in low and "deprecated" in low:
                            _NO_TEMPERATURE.add(model); retry[jid] = todo[jid]
                        elif attempt < 2 and "between_tools" in low:
                            _THINKING_OFF[model] = {"type": "between_tools"}; retry[jid] = todo[jid]
                        else:
                            err[jid] = str(msg)[:300]
        if not retry:
            break
        todo = retry
    for jid in todo:
        if jid not in out and jid not in err:
            err[jid] = "no result"
    return out, err


async def list_ollama_models(provider) -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(provider["base_url"].rstrip("/") + "/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []
