"""Usage and cost tracking. Every model call and every picture is logged to data/usage.jsonl (append-only).
Costs are ESTIMATES computed when you look at the report, from prices you can edit in Settings (providers change prices often).
Local models (Ollama, ComfyUI) cost nothing."""
import contextvars
import json
import time

from . import store

LOG = store.DATA / "usage.jsonl"
CTX: contextvars.ContextVar = contextvars.ContextVar("usage_ctx", default=None)

# $ per 1M tokens: (match substring of the model name, input, output, cache_read). More specific matches first. Claude prices from Anthropic's list;
# other companies' prices are not built in (they change too often): add them in Settings -> Usage prices.
DEFAULT_PRICES = [
    {"match": "fable", "input": 10, "output": 50, "cache_read": 0.25},
    {"match": "opus-5-5", "input": 4, "output": 20, "cache_read": 0.2},
    {"match": "opus", "input": 5, "output": 25, "cache_read": 0.5},
    {"match": "sonnet-4", "input": 3, "output": 15, "cache_read": 0.3},
    {"match": "sonnet", "input": 2, "output": 10, "cache_read": 0.2},
    {"match": "haiku-5", "input": 0.10, "output": 0.50, "cache_read": 0.01},
    {"match": "haiku", "input": 1, "output": 5, "cache_read": 0.1},
]
LOCAL = {"ollama", "comfyui"}


def set_ctx(story: str | None, purpose: str, why: str | None = None) -> None:
    CTX.set({"story": story or "", "purpose": purpose, **({"why": why} if why else {})})


def record(entry: dict) -> None:
    c = CTX.get() or {}
    entry = {"ts": round(time.time()), "story": c.get("story", ""), "purpose": c.get("purpose", "other"), **({"why": c["why"]} if c.get("why") else {}), **entry}
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _price(cfg: dict, model: str, kind: str = "text"):
    table = (cfg.get("prices") or []) + (DEFAULT_PRICES if kind == "text" else [])
    m = model.lower()
    return next((p for p in table if p.get("match") and p["match"].lower() in m and p.get("kind", "text") == kind), None)


def cost_of(cfg: dict, e: dict):
    """Estimated dollars for one log entry, or None when no price is known."""
    if e["provider"] in LOCAL:
        return 0.0
    if e.get("kind") == "image":
        p = _price(cfg, e["model"], "image")
        return float(p["per_image"]) * e.get("images", 1) if p and "per_image" in p else None
    p = _price(cfg, e["model"])
    if not p:
        return None
    rd = p.get("cache_read", p["input"] * 0.1)
    wr = p["input"] * (2.0 if e.get("ttl") == "1h" else 1.25)
    return (e.get("input", 0) * p["input"] + e.get("cache_read", 0) * rd + e.get("cache_write", 0) * wr + e.get("output", 0) * p["output"]) / 1e6


def entries(since: float = 0, story: str | None = None) -> list[dict]:
    out = []
    try:
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e["ts"] >= since and (not story or e.get("story") == story):
                out.append(e)
    except OSError:
        pass
    return out


def report(cfg: dict, since: float, titles: dict[str, str], story: str | None = None) -> dict:
    es = entries(since, story)
    tot = {"calls": 0, "input": 0, "cache_read": 0, "cache_write": 0, "output": 0, "images": 0, "cost": 0.0, "unpriced": 0}
    groups: dict[str, dict] = {"story": {}, "purpose": {}, "model": {}, "day": {}}

    def bump(d: dict, key: str, e: dict, c):
        g = d.setdefault(key, {"calls": 0, "tokens": 0, "images": 0, "cost": 0.0, "unpriced": 0})
        g["calls"] += 1
        g["tokens"] += e.get("input", 0) + e.get("cache_read", 0) + e.get("cache_write", 0) + e.get("output", 0)
        g["images"] += e.get("images", 0)
        if c is None:
            g["unpriced"] += 1
        else:
            g["cost"] += c

    for e in es:
        c = cost_of(cfg, e)
        tot["calls"] += 1
        for k in ("input", "cache_read", "cache_write", "output", "images"):
            tot[k] += e.get(k, 0)
        if c is None:
            tot["unpriced"] += 1
        else:
            tot["cost"] += c
        bump(groups["story"], titles.get(e.get("story"), "(deleted or no story)" if e.get("story") else "(app)"), e, c)
        bump(groups["purpose"], e.get("purpose", "other"), e, c)
        bump(groups["model"], f"{e['provider']}: {e['model']}", e, c)
        bump(groups["day"], time.strftime("%Y-%m-%d", time.localtime(e["ts"])), e, c)
    # Why the narrator's prompt was written to the cache again (turns that wrote a large part of it): by reason
    nar = [e for e in es if e.get("purpose") == "story narration"]
    why: dict[str, dict] = {}
    for e in nar:
        if e.get("cache_write", 0) >= 3000:
            w = why.setdefault(e.get("why") or "reason not recorded (older entries)", {"calls": 0, "tokens": 0})
            w["calls"] += 1
            w["tokens"] += e.get("cache_write", 0)
    out = {k: sorted(({"name": n, **v} for n, v in g.items()), key=lambda x: (x["name"] if k == "day" else -x["cost"], x["name"])) for k, g in groups.items()}
    cache = {"turns": len(nar), "hits": sum(1 for e in nar if e.get("cache_read", 0) > 0),
             "rewrites": sorted(({"name": n, **v} for n, v in why.items()), key=lambda x: -x["tokens"])}
    return {"total": tot, **out, "cache": cache, "prices": (cfg.get("prices") or []), "default_prices": DEFAULT_PRICES}


def spend(cfg: dict, since: float) -> float:
    """Estimated dollars spent since a time (calls with no known price count as zero)."""
    return sum(c for c in (cost_of(cfg, e) for e in entries(since)) if c)


def period_starts() -> tuple[float, float]:
    """(start of today, start of this month) in local time."""
    lt = time.localtime()
    return (time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)), time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1)))
