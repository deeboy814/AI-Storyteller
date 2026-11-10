"""Bug reports: one .zip a player can attach to a problem report. It captures how far the game got (settings, side-panel state, the recent turns), the problems the app can
find by itself (broken structure, missing pictures, format slips, recent errors, unreachable services) and the environment, and it removes personal data before anything is
written: API keys and tokens, e-mail addresses, phone numbers, links, IP addresses, file paths, the computer's user and host name, and (by default) the player's and the
characters' names. Nothing is sent anywhere: the app only builds the file, the player attaches it.

Scrubbing is the last step and checks itself: after the files are written the known secrets are searched for once more, and a file that still contains one is replaced by a
note instead of being shipped."""
import collections
import getpass
import io
import json
import os
import platform
import re
import socket
import sys
import time
import zipfile
from pathlib import Path

from . import beats, characters, clock, hardware, lore, rules, store, tracker, usage

RECENT: collections.deque = collections.deque(maxlen=200)       # errors the app noticed while running (filled by main.py)
MAX_TURN_TEXT = 20000
MAX_ZIP = 8 * 1024 * 1024


def note(kind: str, message, **ctx) -> None:
    """Remember an error for the next bug report (kept in memory only, newest last)."""
    try:
        RECENT.append({"t": round(time.time(), 1), "kind": kind, "message": str(message)[:600], **{k: str(v)[:200] for k, v in ctx.items()}})
    except Exception:
        pass


# ------------------------------------------------------------------ scrubbing
_KEYS = re.compile(r"\b(?:sk-ant-[\w-]{8,}|sk-[A-Za-z0-9_-]{16,}|AIza[\w-]{20,}|xai-[\w-]{16,}|gsk_\w{16,}|ghp_\w{20,}|github_pat_\w{20,}|hf_\w{20,}|glpat-[\w-]{16,})")
_BEARER = re.compile(r"(?i)\b(Bearer|Basic)\s+[\w.~+/=-]{12,}")
_KV = re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization|x-api-key|pin)\b(\"?\s*[:=]\s*\"?)([^\s\",}\]]{4,})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s)>\"'\]]+", re.IGNORECASE)
_PHONE = re.compile(r"(?<![\w.:/-])\+?\d[\d ().-]{7,}\d(?![\w])")
_IP4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]*\d)")
_IP6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){5,7}[0-9a-fA-F]{1,4}(?![\w:])|(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,}:[0-9a-fA-F]{0,4}(?![\w:])")
_MAC = re.compile(r"\b(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}\b")
_HOMEPATH = re.compile(r"(?:/home/[^/\s\"']+|/Users/[^/\s\"']+|[A-Za-z]:\\+Users\\+[^\\\s\"']+|/root)")
_LOOPBACK = ("127.", "0.0.0.0", "localhost")
_COMMON = {"the", "and", "you", "she", "her", "him", "his", "are", "was", "but", "for", "not", "all", "can", "had", "may", "one", "our", "out", "day", "new", "old", "now", "see", "who", "will", "mark"}


class Scrubber:
    """Replaces personal data in text and in JSON-like data, and counts what it removed (by kind) so the report can say so."""

    def __init__(self, secrets: list[str] | None = None, names: dict[str, str] | None = None, paths: list[tuple[str, str]] | None = None, hide_names: bool = True):
        self.counts: collections.Counter = collections.Counter()
        self.secrets = sorted({s for s in (secrets or []) if s and len(s) >= 4}, key=len, reverse=True)
        self.paths = sorted([(a, b) for a, b in (paths or []) if a], key=lambda p: -len(p[0]))
        user = ""
        try:
            user = getpass.getuser()
        except Exception:
            pass
        host = ""
        try:
            host = socket.gethostname()
        except Exception:
            pass
        self.user = user if len(user) >= 3 else ""
        self.host = host if len(host) >= 3 else ""
        self.name_map = {k.lower(): v for k, v in (names or {}).items() if len(k) >= 3 and k.lower() not in _COMMON} if hide_names else {}
        self._name_re = None
        if self.name_map:
            alts = sorted(self.name_map, key=len, reverse=True)
            self._name_re = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(a) for a in alts) + r")(?!\w)", re.IGNORECASE)

    def _n(self, kind: str, n: int = 1) -> None:
        self.counts[kind] += n

    def text(self, s) -> str:
        if not isinstance(s, str) or not s:
            return s if isinstance(s, str) else ("" if s is None else str(s))
        for sec in self.secrets:
            if sec in s:
                self._n("keys and secrets", s.count(sec)); s = s.replace(sec, "<secret>")
        s, n = _KEYS.subn("<secret>", s); self._n("keys and secrets", n)
        s, n = _BEARER.subn(r"\1 <secret>", s); self._n("keys and secrets", n)
        def kv(m):
            if m.group(3) in ("true", "false", "null", "None") or m.group(3).startswith("<"):
                return m.group(0)
            self._n("keys and secrets"); return m.group(1) + m.group(2) + "<secret>"
        s = _KV.sub(kv, s)
        s, n = _EMAIL.subn("<email>", s); self._n("e-mail addresses", n)

        def url(m):
            host = re.sub(r"^https?://", "", m.group(0), flags=re.IGNORECASE).split("/")[0].split(":")[0].lower()
            if host.startswith(_LOOPBACK):
                return m.group(0)
            self._n("links"); return "<link>"
        s = _URL.sub(url, s)
        for a, b in self.paths:                       # the app's own folders and the home folder come out as <app>, <data>, ~
            for variant in {a, a.replace("/", "\\"), a.replace("\\", "/")}:
                if variant in s:
                    self._n("file paths", s.count(variant)); s = s.replace(variant, b)
        s, n = _HOMEPATH.subn("~", s); self._n("file paths", n)
        if self.user:
            s, n = re.subn(r"(?<!\w)" + re.escape(self.user) + r"(?!\w)", "<user>", s, flags=re.IGNORECASE); self._n("computer user and host names", n)
        if self.host:
            s, n = re.subn(r"(?<!\w)" + re.escape(self.host) + r"(?!\w)", "<host>", s, flags=re.IGNORECASE); self._n("computer user and host names", n)
        s, n = _MAC.subn("<mac>", s); self._n("ip addresses", n)

        def ip4(m):
            parts = m.group(0).split(".")
            if not all(p.isdigit() and int(p) <= 255 for p in parts) or m.group(0).startswith(_LOOPBACK):
                return m.group(0)
            self._n("ip addresses"); return "<ip>"
        s = _IP4.sub(ip4, s)
        s, n = _IP6.subn(lambda m: m.group(0) if m.group(0).count(":") < 2 else "<ip>", s); self._n("ip addresses", n)

        def phone(m):
            d = re.sub(r"\D", "", m.group(0))
            if not 9 <= len(d) <= 15 or re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", m.group(0)):
                return m.group(0)
            self._n("phone numbers"); return "<phone>"
        s = _PHONE.sub(phone, s)
        if self._name_re:
            s = self._name_re.sub(lambda m: (self._n("names"), self.name_map[m.group(0).lower()])[1], s)
            s = re.sub(r"\b(Player|Character [A-Z]\d*)(?: \1\b)+", r"\1", s)          # "First Last" came out as two placeholders: keep one
        return s

    def data(self, obj):
        """The same for nested data; dictionary keys are scrubbed too (a character's name can be a key)."""
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, list):
            return [self.data(x) for x in obj]
        if isinstance(obj, dict):
            return {self.text(k) if isinstance(k, str) else k: self.data(v) for k, v in obj.items()}
        return obj

    def clean_of(self, text: str) -> bool:
        """True when none of the known secrets, the user name or the home folder are left in the text."""
        if any(sec in text for sec in self.secrets):
            return False
        if self.user and re.search(r"(?<!\w)" + re.escape(self.user) + r"(?!\w)", text, re.IGNORECASE):
            return False
        return not _HOMEPATH.search(text)


def _name_map(story: dict | None) -> dict[str, str]:
    """Real name -> placeholder: the player's character is Player, the others Character A, B, C… (full names and their separate parts)."""
    if not story:
        return {}
    out: dict[str, str] = {}

    def add(full: str, label: str):
        full = (full or "").strip()
        if len(full) < 3:
            return
        out.setdefault(full, label)
        slug = re.sub(r"[^a-z0-9]+", "-", full.lower()).strip("-")          # picture links carry a character's name as a slug (ann-marsh)
        if len(slug) >= 3 and slug != full.lower():
            out.setdefault(slug, label)
        for part in re.split(r"[\s,.]+", full):
            if len(part) >= 3 and part.lower() not in _COMMON and part.lower() not in {"the", "dr", "mr", "mrs", "ms", "sir", "captain"}:
                out.setdefault(part, label)
    pn = (story.get("avatar") or {}).get("name") or story.get("player_name") or ""
    add(pn, "Player")
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i, c in enumerate(story.get("cast") or []):
        label = "Player" if c.get("player") else f"Character {letters[i % 26]}{'' if i < 26 else i // 26}"
        add(c.get("name", ""), label)
        for a in c.get("aka") or []:
            add(a, label)
    return out


# ------------------------------------------------------------------ what to capture
def _redact_url(u: str) -> str:
    m = re.match(r"(https?://)([^/:]+)(:\d+)?", u or "")
    if not m:
        return ""
    if m.group(2).startswith(_LOOPBACK):
        return m.group(1) + m.group(2) + (m.group(3) or "")
    return f"remote server ({m.group(1).rstrip(':/')})"


def config_summary(cfg: dict) -> dict:
    """The settings that matter for a bug, without keys, word lists, folders or addresses."""
    provs = [{"id": p.get("id"), "type": p.get("type"), "has_key": bool(p.get("api_key")), "models": (p.get("models") or [])[:12], "base_url": _redact_url(p.get("base_url", ""))} for p in cfg.get("providers", [])]
    imgs = [{"id": p.get("id"), "type": p.get("type"), "model": p.get("model"), "prompt_style": p.get("prompt_style"), "has_key": bool(p.get("api_key")), "base_url": _redact_url(p.get("base_url", ""))} for p in cfg.get("image_providers", [])]
    keep = ("active", "default_models", "temperature", "max_tokens", "num_ctx", "prompt_cache", "cache_ttl", "keep_warm", "summarize_when", "keep_recent", "events_mode", "events_model", "char_memory",
            "char_memory_size", "claude_thinking", "claude_effort", "image_active", "image_max_refs", "rt_text", "rt_comfy", "rt_comfy_mode", "rt_swap", "lan_access", "budget_action", "budget_daily",
            "budget_monthly", "ui_backdrop", "ui_see", "ui_twotone")
    out = {k: cfg.get(k) for k in keep if k in cfg}
    ns = cfg.get("image_nsfw") or {}
    out.update(providers=provs, image_providers=imgs, image_bg={"on": (cfg.get("image_bg") or {}).get("on"), "model": (cfg.get("image_bg") or {}).get("model")},
               image_nsfw={"on": ns.get("on"), "maker": ns.get("maker"), "custom_words": bool(ns.get("words")), "tuned_models": sorted((ns.get("tweaks") or {}).keys())},
               mood_sets={k: len(v) for k, v in (cfg.get("mood_sets") or {}).items()}, custom_mood_looks=len(cfg.get("mood_looks") or {}))
    return out


def _turn_flags(t: dict) -> dict:
    f = {}
    for k in ("model", "ts", "edited", "bookmark", "imported", "attempt_no"):
        if t.get(k) not in (None, "", False):
            f[k] = t[k]
    if t.get("usage"):
        u = t["usage"]
        f["usage"] = {k: u.get(k) for k in ("input", "output", "cache_read", "cache_write", "thinking") if u.get(k) is not None}
    for k in ("cmds", "roll", "pts", "tidied", "check"):
        if t.get(k):
            f[k] = t[k] if k != "check" else (t["check"].get("issues") if isinstance(t.get("check"), dict) else t["check"])
    for k, n in (("versions", "versions"), ("attempts", "attempts"), ("images", "pictures")):
        if t.get(k):
            f[n] = len(t[k])
    if t.get("scene"):
        f["has_scene_frame"] = True
    return f


def story_snapshot(story: dict, include_text: bool, last_turns: int) -> dict:
    """How far the game got and how it is set up. Texts (prompt, summary, replies) are included only when asked."""
    turns = story.get("turns") or []
    tr = story.get("tracker") or {}
    txt = (lambda v: v) if include_text else (lambda v: f"<{len(v)} characters left out>" if isinstance(v, str) and v else v)
    b = beats.get(dict(story)) if story else {}
    lb = lore.get(dict(story)) if story else {}
    cast = []
    for c in story.get("cast") or []:
        cast.append({"name": c.get("name"), "player": bool(c.get("player")), "species": c.get("species") or "", "pictures": len(c.get("moods") or {}), "moods": sorted((c.get("moods") or {}).keys())[:40],
                     "earlier_versions": sum(len(v) for v in (c.get("alts") or {}).values()), "key_events": len(c.get("events") or []), "also_called": len(c.get("aka") or []), "maker": c.get("maker") or "",
                     "appearance": txt(c.get("appearance", "")), "bio": txt(c.get("bio", "")), "personality": txt(c.get("personality", ""))})
    first = max(0, len(turns) - max(0, min(last_turns, 60)))
    recent = []
    for i in range(first, len(turns)):
        t = turns[i]
        rec = {"index": i, "role": t.get("role"), "chars": len(t.get("text", "")), **_turn_flags(t)}
        if include_text:
            rec["text"] = (t.get("text") or "")[:MAX_TURN_TEXT]
        recent.append(rec)
    overview = [{"i": i, "role": t.get("role")[:1] if t.get("role") else "?", "chars": len(t.get("text", "")), "model": (t.get("model") or "")[:40]} for i, t in enumerate(turns)][-300:]
    return {
        "title": story.get("title"), "id": story.get("id"), "created": story.get("created"),
        "counts": {"turns": len(turns), "your_moves": sum(1 for t in turns if t.get("role") == "user"), "replies": sum(1 for t in turns if t.get("role") == "assistant"), "summary_upto": story.get("summary_upto", 0),
                   "summary_chars": len(story.get("summary") or ""), "prompt_chars": len(story.get("base_prompt") or ""), "opening_chars": len(story.get("opening") or ""), "pinned_facts": len(story.get("pinned_facts") or []),
                   "cast": len(cast), "places": len(story.get("places") or []), "species": len(story.get("species") or {}), "lore_entries": len(lb.get("items", [])), "beats": len(b.get("items", []))},
        "options": {k: story.get(k) for k in ("nsfw", "rule_check", "code_check", "format_fix", "narrator_acts", "auto_replies", "use_state_block", "opening_in_context", "group_cast", "dice", "clock", "prompt_derived") if story.get(k) is not None},
        "models": story.get("models"), "art": {k: v for k, v in (story.get("art") or {}).items() if k not in ("direction", "setting", "negative") or include_text},
        "sidebar": {"mode": (story.get("sidebar") or {}).get("mode"), "fields": (story.get("sidebar") or {}).get("fields"), "custom": [{k: v for k, v in f.items() if k != "note"} for f in (story.get("sidebar") or {}).get("custom", [])]},
        "tracker": {k: v for k, v in tr.items()}, "state": story.get("state") if include_text else "<left out>",
        "beats": {"on": b.get("on"), "hold": b.get("hold"), "lookahead": b.get("lookahead"), "extracted": b.get("extracted"), "error": b.get("error", ""),
                  "items": [{"title": it.get("title") if include_text else "", "trigger": f"{it.get('field')} {it.get('op')} {it.get('value')}", "fired": it.get("fired"), "off": it.get("off"), "why": it.get("why", "")} for it in b.get("items", [])]},
        "lore": {"on": lb.get("on"), "entries": [{"title": it.get("title") if include_text else "", "keys": it.get("keys") if include_text else len(it.get("keys", [])), "always": it.get("always"), "on": it.get("on")} for it in lb.get("items", [])]},
        "cast": cast, "places": [{"name": p.get("name"), "has_picture": bool(p.get("url"))} for p in story.get("places") or []],
        "avatar": {k: txt(v) for k, v in (story.get("avatar") or {}).items()}, "user_notes": txt(story.get("user_notes", "")),
        "prompt": story.get("base_prompt") if include_text else f"<{len(story.get('base_prompt') or '')} characters left out>",
        "summary": txt(story.get("summary", "")), "pinned_facts": story.get("pinned_facts") if include_text else len(story.get("pinned_facts") or []),
        "retry_pending": bool(story.get("retry_pending")), "undone_turns": len(story.get("undone") or []),
        "turn_overview": overview, "recent_turns": recent,
    }


def _file_for(url: str) -> Path | None:
    rel = (url or "").split("?")[0]
    if rel.startswith("/portraits/"):
        return store.PORTRAITS / rel[len("/portraits/"):]
    if rel.startswith("/images/"):
        return store.DATA / "images" / rel[len("/images/"):]
    return None


def diagnose(story: dict | None, cfg: dict, health: dict, jobs: list[dict], logs: dict) -> list[dict]:
    """Problems the app can find by itself, each {level, where, message}. Level: error (something is broken), warn (looks wrong), info (worth knowing)."""
    out: list[dict] = []
    add = lambda level, where, msg: out.append({"level": level, "where": where, "message": msg})
    if story:
        turns = story.get("turns") or []
        for i, t in enumerate(turns):
            if t.get("role") not in ("user", "assistant"):
                add("error", f"turn {i}", f"unknown role {t.get('role')!r}")
            elif i and t.get("role") == turns[i - 1].get("role"):
                add("warn", f"turn {i}", f"two {t['role']} turns in a row (turns {i - 1} and {i}); the model expects them to alternate")
            if not (t.get("text") or "").strip():
                add("warn", f"turn {i}", "empty text")
        if story.get("summary_upto", 0) > len(turns):
            add("error", "memory", f"the memory summary covers {story['summary_upto']} turns but the story has only {len(turns)}")
        if story.get("retry_pending"):
            add("info", "retry", "a retry is pending (the discarded reply is kept as an attempt)")
        tr = story.get("tracker") or {}
        if tr.get("_spent") and not tracker.points_field(story):
            add("warn", "stat points", "points were spent but the story has no points tracker")
        if turns and clock.is_on(story) and "_clock" not in tr:
            add("info", "world clock", "the clock is on but has not started yet (it starts with the next reply)")
        moves = beats.moves(story)
        for it in beats.get(dict(story))["items"]:
            if it.get("fired") is not None and it["fired"] > moves:
                add("warn", "story beats", f"beat '{it.get('title', '')[:40]}' was marked as sent on move {it['fired']}, but only {moves} moves exist")
        slugs = [c.get("slug") for c in story.get("cast") or []]
        if len(slugs) != len(set(slugs)):
            add("error", "cast", "two characters share the same slug")
        missing = 0
        for c in story.get("cast") or []:
            for u in list((c.get("moods") or {}).values()) + [x for v in (c.get("alts") or {}).values() for x in v]:
                f = _file_for(u)
                if f is not None and not f.is_file():
                    missing += 1
        if missing:
            add("warn", "pictures", f"{missing} character picture file(s) are missing from the data folder")
        pm = [p["name"] for p in story.get("places") or [] if p.get("url") and (_file_for(p["url"]) is not None) and not _file_for(p["url"]).is_file()]
        if pm:
            add("warn", "pictures", f"{len(pm)} place picture file(s) are missing")
        for d in characters.find_duplicates(story):
            add("info", "cast", f"{d['names'][0]} and {d['names'][1]} may be the same character ({d['reason']})")
        f = store.STORIES / f"{story.get('id')}.json"
        try:
            if f.stat().st_size > 5 * 1024 * 1024:
                add("warn", "story file", f"the story file is large ({f.stat().st_size // 1024 // 1024} MB); a memory rebuild or removing old pictures from the turns may help")
        except OSError:
            pass
        replies = [i for i, t in enumerate(turns) if t.get("role") == "assistant"][-5:]
        for i in replies:
            try:
                for msg in rules.check(story, i):
                    add("warn", f"reply {i}", msg)
            except Exception:
                pass
        for role, m in (story.get("models") or {}).items():
            if isinstance(m, dict) and m.get("provider") in {p["id"] for p in cfg.get("providers", [])}:
                p = next(p for p in cfg["providers"] if p["id"] == m["provider"])
                if p.get("type") in ("anthropic", "openai") and not p.get("api_key") and not str(p.get("base_url", "")).startswith(("http://localhost", "http://127.")):
                    add("error", f"models.{role}", f"the story uses provider '{p['id']}' but it has no API key")
            elif isinstance(m, dict) and m.get("provider"):
                add("error", f"models.{role}", f"the story uses provider '{m.get('provider')}', which is not set up")
    for name, h in health.items():
        if h.get("configured") and not h.get("reachable"):
            add("warn", name, "is set up but could not be reached just now")
    errs = [e for e in RECENT if e["kind"] in ("exception", "turn")]
    if errs:
        add("error", "recent errors", f"{len(errs)} error(s) since the app started; the newest: {errs[-1]['message'][:160]}")
    http = [e for e in RECENT if e["kind"] == "http"]
    if http:
        add("info", "recent errors", f"{len(http)} request(s) failed since the app started (listed in diagnostics.json)")
    for j in jobs:
        if j.get("status") == "error":
            add("warn", f"background job ({j.get('kind')})", f"failed: {str(j.get('error'))[:160]}")
    tb = sum(v.count("Traceback (most recent call last)") for v in logs.values())
    if tb:
        add("error", "server log", f"{tb} Python traceback(s) in the log tail (see logs.txt)")
    return out


def health(cfg: dict) -> dict:
    """Whether the local services the story could use answer (a one-second ping each)."""
    import httpx
    out = {}
    probes = [("ollama", next((p.get("base_url") for p in cfg.get("providers", []) if p.get("type") == "ollama"), ""), "/api/tags"),
              ("comfyui", next((p.get("base_url") for p in cfg.get("image_providers", []) if p.get("id") == "comfyui"), ""), "/system_stats")]
    for name, base, path in probes:
        if not base:
            out[name] = {"configured": False}
            continue
        try:
            r = httpx.get(base.rstrip("/") + path, timeout=1.5)
            out[name] = {"configured": True, "reachable": r.status_code < 500, "status": r.status_code}
        except Exception as e:
            out[name] = {"configured": True, "reachable": False, "error": e.__class__.__name__}
    return out


def _tail(path: Path, lines: int) -> str:
    try:
        data = path.read_bytes()[-200_000:].decode("utf-8", "replace").splitlines()
        return "\n".join(data[-lines:])
    except OSError:
        return ""


def _usage_summary(story_id: str | None) -> dict:
    try:
        rows = usage.entries(0, story_id)[-400:]
    except Exception:
        return {}
    by: dict[str, dict] = {}
    for e in rows:
        k = f"{e.get('model')} · {e.get('purpose')}"
        d = by.setdefault(k, {"calls": 0, "input": 0, "output": 0})
        d["calls"] += 1; d["input"] += int(e.get("input") or 0); d["output"] += int(e.get("output") or 0)
    return dict(sorted(by.items(), key=lambda kv: -kv[1]["calls"])[:25])


# ------------------------------------------------------------------ the report
def build(story: dict | None, cfg: dict, app_version: str, description: str, include_text: bool, last_turns: int, hide_names: bool, include_logs: bool,
          client: dict, jobs: list[dict]) -> tuple[bytes, dict]:
    """Make the .zip. Returns (zip bytes, manifest for the review screen)."""
    secrets = [p.get("api_key") for p in cfg.get("providers", []) + cfg.get("image_providers", []) if p.get("api_key")]
    for f in ("lan_pin",):
        try:
            secrets.append((store.DATA / f).read_text(encoding="utf-8").strip())
        except OSError:
            pass
    if os.environ.get("STORYTELLER_PIN"):
        secrets.append(os.environ["STORYTELLER_PIN"])
    app_dir = Path(__file__).resolve().parent.parent
    paths = [(str(store.DATA.resolve()), "<data>"), (str(app_dir), "<app>"), (str(Path.home()), "~")]
    sc = Scrubber(secrets, _name_map(story), paths, hide_names)
    hw = {}
    try:
        hw = hardware.detect(store.DATA)
    except Exception:
        pass
    hl = health(cfg)
    logs = {}
    if include_logs:
        from urllib.parse import unquote
        logs = {"server.log": unquote(_tail(store.DATA / "server.log", 250)), "comfyui.log": unquote(_tail(store.DATA / "comfyui.log", 80))}      # decoded, so a name inside a link is found
    issues = diagnose(story, cfg, hl, jobs, logs)
    env = {"app_version": app_version, "python": sys.version.split()[0], "os": f"{platform.system()} {platform.release()}", "machine": platform.machine(), "hardware": hw,
           "settings": config_summary(cfg), "services": hl, "lan_access": bool(cfg.get("lan_access")), "created": time.strftime("%Y-%m-%d %H:%M:%S %z")}
    snap = story_snapshot(story, include_text, last_turns) if story else None
    diag = {"problems_found": issues, "recent_errors": list(RECENT)[-80:], "background_jobs": [{k: j.get(k) for k in ("kind", "status", "error", "current", "done", "total")} for j in jobs][-20:],
            "usage_by_job": _usage_summary(story.get("id") if story else None)}
    cl = {"user_agent": str(client.get("ua", ""))[:300], "viewport": client.get("viewport"), "page_errors": (client.get("errors") or [])[-60:], "page": str(client.get("page", ""))[:80],
          "story_open_in_page": bool(client.get("story_open")), "saved_page_settings": client.get("storage_keys")}
    desc = (description or "").strip()[:6000]
    files: dict[str, str] = {}
    files["story.json"] = json.dumps(sc.data(snap) if snap else {"note": "No story was chosen: this report is about the app itself."}, indent=1, ensure_ascii=False, default=str)
    files["diagnostics.json"] = json.dumps(sc.data(diag), indent=1, ensure_ascii=False, default=str)
    files["settings.json"] = json.dumps(sc.data(env), indent=1, ensure_ascii=False, default=str)
    files["client.json"] = json.dumps(sc.data(cl), indent=1, ensure_ascii=False, default=str)
    if include_logs:
        files["logs.txt"] = "\n\n".join(f"===== {k} (last lines) =====\n{sc.text(v) or '(empty)'}" for k, v in logs.items())
    levels = {"error": "🔴", "warn": "🟠", "info": "ℹ️"}
    lines = [f"# Bug report: {sc.text(story.get('title', '')) if story else 'AI Storyteller'}", "",
             f"- **App version:** {app_version} · **Python:** {env['python']} · **System:** {env['os']} ({env['machine']})",
             f"- **Created:** {env['created']}"]
    if snap:
        c = snap["counts"]
        lines += [f"- **Game progress:** {c['your_moves']} moves, {c['replies']} replies ({c['turns']} turns); memory covers {c['summary_upto']} turns; {c['cast']} characters, {c['places']} places, "
                  f"{c['lore_entries']} lore entries, {c['beats']} story beats",
                  f"- **Models:** " + ", ".join(f"{k}: {(v or {}).get('provider')}/{(v or {}).get('model')}" for k, v in (snap.get("models") or {}).items() if isinstance(v, dict))]
    lines += ["", "## What went wrong (written by the player)", "", sc.text(desc) or "_Nothing written. Please describe what you did, what you expected and what happened._", "",
              "## Problems the app found by itself", ""]
    lines += [f"- {levels.get(i['level'], '')} **{sc.text(i['where'])}**: {sc.text(i['message'])}" for i in issues] or ["- None found. The problem may be about something the app cannot check (the content of a reply, for example)."]
    errs = list(RECENT)[-8:]
    if errs:
        lines += ["", "## Latest errors since the app started", ""] + [f"- `{e['kind']}` {sc.text(e['message'])[:200]}" for e in errs]
    if cl["page_errors"]:
        lines += ["", f"## Errors in the page ({len(cl['page_errors'])})", ""] + [f"- {sc.text(str(e.get('message', '')))[:200]}" for e in cl["page_errors"][-6:]]
    removed = dict(sc.counts)
    lines += ["", "## What was removed to protect privacy", "", ("Removed so far: " + ", ".join(f"{v} {k}" for k, v in removed.items()) + ".") if removed else "Nothing personal was found to remove.", "",
              "API keys and tokens, e-mail addresses, phone numbers, links, IP addresses, file paths and the computer's user and host name are replaced with placeholders"
              + ("; the player's and characters' names are replaced with *Player* and *Character A, B…*" if hide_names else "; names were **kept** (you chose not to replace them)")
              + (". Story text (prompt, memory, replies) is **included**, so read it before sharing if it is private." if include_text else ". Story text (prompt, memory, replies) was **left out**.")]
    files["REPORT.md"] = "\n".join(lines) + "\n"
    files["WHAT-IS-INSIDE.txt"] = ("This file was made by AI Storyteller's bug report. Nothing was uploaded: attach it to your report yourself.\n\n"
                                   "REPORT.md        a readable summary: your description, how far the game got, the problems the app found\n"
                                   "story.json       the game's settings and state, and the last turns" + (" (with their text)" if include_text else " (text left out)") + "\n"
                                   "diagnostics.json problems found, recent errors, failed background jobs, usage by job\n"
                                   "settings.json    app settings (no keys, no folders, no addresses), hardware, which local services answered\n"
                                   "client.json     the browser and errors the page noticed\n" + ("logs.txt        the last lines of the server logs\n" if include_logs else "") +
                                   "\nRemoved before saving: keys and secrets, e-mail addresses, phone numbers, links, IP addresses, file paths, the user and host name"
                                   + (", character and player names (replaced by Player / Character A, B...)" if hide_names else "") + ".\n"
                                   "Pictures, your API keys, your PIN and the rest of your data folder are never included.\n")
    # last line of defence: nothing known as secret, the user name or the home folder may be left in any file
    blocked = []
    for name, body in list(files.items()):
        if not sc.clean_of(body):
            body = sc.text(body)
            if not sc.clean_of(body):
                files[name] = "This file was left out because the app could not make sure it contained no personal data.\n"
                blocked.append(name)
            else:
                files[name] = body
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    data = buf.getvalue()
    manifest = {"files": [{"name": n, "size": len(b.encode("utf-8"))} for n, b in files.items()], "size": len(data), "issues": issues, "removed": removed, "blocked_files": blocked,
                "report_md": files["REPORT.md"][:8000], "include_text": include_text, "hide_names": hide_names}
    return data, manifest
