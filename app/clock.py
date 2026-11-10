"""World clock: the app keeps the in-world time of day, so it stops drifting. Optional per story: "on" or "off", or "auto" (on when the story prompt talks about a clock or a
Time value). Each reply moves time on by a set number of minutes; a new Day (the Day tracker changing) starts the day again at the start time; a time the narrator prints
itself (a Time tracker) is adopted. The narrator is told the time each turn. State lives in the tracker (tracker["_clock"]) so undo and retry restore it."""
import re

DEFAULTS = {"mode": "auto", "start": "06:00", "minutes": 30}
_PROMPT = re.compile(r"\b(clock|time of day|o'clock|\d{1,2}:\d{2}\s*(?:am|pm|a\.m\.|p\.m\.)?|(?:hours?|minutes?) (?:per|each|a) (?:turn|reply)|time (?:passes|advances|of night))\b", re.IGNORECASE)
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", re.IGNORECASE)


def settings(story: dict) -> dict:
    c = {**DEFAULTS, **(story.get("clock") or {})}
    if c["mode"] not in ("auto", "on", "off"):
        c["mode"] = "auto"
    c["minutes"] = max(1, min(int(c.get("minutes") or 30), 720))
    if parse_time(c["start"]) is None:
        c["start"] = DEFAULTS["start"]
    return c


def parse_time(v) -> int | None:
    """Minutes since midnight from "14:30", "2:30 pm", "2pm"; None when it is not a time."""
    m = _TIME.match(str(v or ""))
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60) or (m.group(2) is None and not ap):
        return None
    return h * 60 + mi


def fmt(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def part_of_day(minutes: int) -> str:
    h = (minutes % 1440) / 60
    return ("night" if h < 5 or h >= 21 else "dawn" if h < 7 else "morning" if h < 11 else "midday" if h < 14 else "afternoon" if h < 17 else "evening")


def _field(story: dict, labels: tuple) -> dict | None:
    from . import tracker
    return next((f for f in tracker.active_fields(story) if f["label"].strip().lower() in labels and f["id"].split(".")[0] in ("scene", "me") and f.get("kind") in ("text", "number", "status")), None)


def _configured_fields(story: dict) -> list[dict]:
    """The trackers this story set up on purpose (the player's own, the ticked ones, or the AI's design): not the built-in list every story starts with."""
    from . import tracker
    sb = story.get("sidebar") or {}
    custom = list(sb.get("custom") or [])
    if sb.get("mode") == "ai" and sb.get("schema"):
        return list(sb["schema"])
    if sb.get("mode") == "player" or custom:
        return [f for f in tracker.DEFAULT_FIELDS if f["id"] in (sb.get("fields") or [])] + custom
    return []


def prompt_uses_clock(story: dict) -> bool:
    """Does the story itself deal in the time of day? Its prompt talks about a clock or times, a Time value is in its status line, or it set up a Time tracker."""
    from . import rules
    if _PROMPT.search(story.get("base_prompt") or ""):
        return True
    if any(l.strip().lower() in ("time", "clock", "time of day") for l in rules.expected_labels(story)):
        return True
    return any(f["label"].strip().lower() in ("time", "clock", "time of day") for f in _configured_fields(story))


def is_on(story: dict) -> bool:
    m = settings(story)["mode"]
    return True if m == "on" else False if m == "off" else prompt_uses_clock(story)


def _day(story: dict) -> str:
    f = _field(story, ("day",))
    if not f:
        return ""
    sec, k = f["id"].split(".", 1)
    return str((story.get("tracker") or {}).get(sec, {}).get(k) or "")


def current(story: dict) -> int | None:
    st = (story.get("tracker") or {}).get("_clock")
    if st is not None:
        return st["min"]
    return parse_time(settings(story)["start"]) if is_on(story) else None


def advance(story: dict, turn: int) -> list[dict]:
    """After a reply's numbers are read: move the clock on (or reset it for a new day, or adopt the narrator's own Time). Returns tracker changes."""
    tr = story.setdefault("tracker", {})
    if not is_on(story):
        tr.pop("_clock", None)
        return []
    cfg, out = settings(story), []
    start, mpt, day = parse_time(cfg["start"]), cfg["minutes"], _day(story)
    tf = _field(story, ("time", "clock", "time of day"))
    seen = None
    if tf:
        sec, k = tf["id"].split(".", 1)
        seen = (tr.get(sec) or {}).get(k)
    st = tr.get("_clock")
    narrated = parse_time(seen) if seen not in (None, "") and (st is None or str(seen) != st.get("shown")) else None
    if st is None:
        st = {"min": (start + mpt) % 1440, "day": day, "t": turn, "shown": None}
    elif st["t"] == turn:                      # a second pass over the same reply: only a new day or the narrator's own time can change it
        if day and day != st["day"]:
            st.update(min=start, day=day)
    elif day and day != st["day"]:
        st.update(min=start, day=day, t=turn)
    else:
        st.update(min=(st["min"] + mpt) % 1440, t=turn)
    if narrated is not None:
        st["min"] = narrated
    st["day"] = day or st["day"]
    tr["_clock"] = st
    if tf:
        sec, k = tf["id"].split(".", 1)
        new = fmt(st["min"])
        if (tr.setdefault(sec, {})).get(k) != new:
            out.append({"s": sec, "n": None, "k": k, "b": tr[sec].get(k), "a": new})
            tr[sec][k] = new
        st["shown"] = new
    return out


def note(story: dict) -> str:
    """The hidden note telling the narrator the time (empty when the clock is off)."""
    if not is_on(story):
        return ""
    cfg, m = settings(story), current(story)
    day = _day(story)
    tf = _field(story, ("time", "clock", "time of day"))
    return (f"[CLOCK, kept by the app: it is now {fmt(m)} ({part_of_day(m)})" + (f" on Day {day}" if day else "") + f". Each reply moves time on by about {cfg['minutes']} minutes unless the story skips time; "
            "keep light, meals, curfews and schedules consistent with it, and when the story jumps ahead (a night's sleep, a long march) say so plainly"
            + (" and print the new time in the status line as Time: HH:MM" if tf else "") + ". Do not print the clock otherwise.]")
