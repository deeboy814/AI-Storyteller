"""World clock: the app keeps the in-world time of day, so it stops drifting. Optional per story: "on" or "off", or "auto" (on when the story prompt talks about a clock or a
Time value). Each reply moves time on by a set number of minutes; a new Day (the Day tracker changing) starts the day again at the start time; a time the narrator prints
itself (a Time tracker) is adopted. The narrator is told the time each turn. State lives in the tracker (tracker["_clock"]) so undo and retry restore it."""
import re

DEFAULTS = {"mode": "auto", "start": "06:00", "minutes": 30, "weekdays": "auto", "start_day": ""}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_WD = re.compile(r"\b(mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?s?\b", re.IGNORECASE)
_WD_FULL = re.compile(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b", re.IGNORECASE)
_WD_PROMPT = re.compile(r"\b(day of the week|days of the week|weekdays?|weekends?|weekly)\b", re.IGNORECASE)
_WD_LABELS = ("weekday", "day of week", "day of the week", "weekday name")
_PROMPT = re.compile(r"\b(clock|time of day|o'clock|\d{1,2}:\d{2}\s*(?:am|pm|a\.m\.|p\.m\.)?|(?:hours?|minutes?) (?:per|each|a) (?:turn|reply)|time (?:passes|advances|of night))\b", re.IGNORECASE)
_TIME = re.compile(r"^\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", re.IGNORECASE)


def settings(story: dict) -> dict:
    c = {**DEFAULTS, **(story.get("clock") or {})}
    if c["mode"] not in ("auto", "on", "off"):
        c["mode"] = "auto"
    c["minutes"] = max(1, min(int(c.get("minutes") or 30), 720))
    if parse_time(c["start"]) is None:
        c["start"] = DEFAULTS["start"]
    if c["weekdays"] not in ("auto", "on", "off"):
        c["weekdays"] = "auto"
    return c


def clean(c: dict) -> dict:
    """A clock setting from outside (the editor, a package) made safe."""
    c = c or {}
    i = weekday_index(c.get("start_day"))
    return {"mode": c.get("mode") if c.get("mode") in ("auto", "on", "off") else "auto",
            "start": fmt(parse_time(c.get("start"))) if parse_time(c.get("start")) is not None else "06:00",
            "minutes": max(1, min(int(c.get("minutes") or 30), 720)),
            "weekdays": c.get("weekdays") if c.get("weekdays") in ("auto", "on", "off") else "auto",
            "start_day": WEEKDAYS[i] if i is not None else ""}


def weekday_index(text) -> int | None:
    """0 (Monday) to 6 (Sunday) from "Tuesday", "tue", "Tues"; None when it is not a weekday name."""
    m = _WD.fullmatch(str(text or "").strip().rstrip(".")) or _WD_FULL.search(str(text or ""))
    if not m:
        return None
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(m.group(1).lower()[:3])


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
        return _advance_weekday(story, turn, False, _day(story))
    cfg, out = settings(story), []
    start, mpt, day = parse_time(cfg["start"]), cfg["minutes"], _day(story)
    tf = _field(story, ("time", "clock", "time of day"))
    seen = None
    if tf:
        sec, k = tf["id"].split(".", 1)
        seen = (tr.get(sec) or {}).get(k)
    st = tr.get("_clock")
    prev = dict(st) if st else None
    before = st["min"] if st else None
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
    wrapped = bool(prev and prev["t"] != turn and not (day and day != prev["day"]) and (st["min"] < before - (120 if narrated is not None else 0)))   # past midnight (a time the narrator prints counts as a jump back only past 2 hours)
    st["day"] = day or st["day"]
    tr["_clock"] = st
    out += _advance_weekday(story, turn, wrapped, day)
    if tf:
        sec, k = tf["id"].split(".", 1)
        new = fmt(st["min"])
        if (tr.setdefault(sec, {})).get(k) != new:
            out.append({"s": sec, "n": None, "k": k, "b": tr[sec].get(k), "a": new})
            tr[sec][k] = new
        st["shown"] = new
    return out


def note(story: dict) -> str:
    """The hidden note telling the narrator the time and the day of the week (empty when both are off)."""
    wd = current_weekday(story)
    wname = WEEKDAYS[wd] if wd is not None else ""
    day = _day(story)
    wf = _wd_field(story)
    if not is_on(story):
        if not wname:
            return ""
        return (f"[CALENDAR, kept by the app: today is {wname}" + (f" (Day {day})" if day else "") + ". When a new day begins, the next weekday follows; keep schedules, classes, work and events consistent with it"
                + (" and print it in the status line as Weekday: Name" if wf else "") + ". Do not print the weekday otherwise.]")
    cfg, m = settings(story), current(story)
    tf = _field(story, ("time", "clock", "time of day"))
    return (f"[CLOCK, kept by the app: it is now {fmt(m)} ({part_of_day(m)})" + (f" on {wname}" if wname else "") + (f"{', ' if wname else ' on '}Day {day}" if day else "") + f". Each reply moves time on by about {cfg['minutes']} minutes unless the story skips time; "
            "keep light, meals, curfews and schedules consistent with it, and when the story jumps ahead (a night's sleep, a long march) say so plainly"
            + (" and print the new time in the status line as Time: HH:MM" if tf else "") + (" and the new day as Weekday: Name" if wf and wname else "") + ". Do not print the clock otherwise.]")


# ---------------- days of the week (Monday, Tuesday, ...) ----------------
def _wd_field(story: dict) -> dict | None:
    return _field(story, _WD_LABELS)


def prompt_uses_weekdays(story: dict) -> bool:
    """Does the story deal in days of the week? Its prompt names two or more of them (or talks about "the day of the week" / a weekly schedule), or it set up a Weekday tracker."""
    text = story.get("base_prompt") or ""
    if len({m.group(1).lower() for m in _WD_FULL.finditer(text)}) >= 2 or _WD_PROMPT.search(text):
        return True
    from . import rules
    if any(l.strip().lower() in _WD_LABELS for l in rules.expected_labels(story)):
        return True
    return any(f["label"].strip().lower() in _WD_LABELS for f in _configured_fields(story))


def weekdays_on(story: dict) -> bool:
    m = settings(story)["weekdays"]
    return True if m == "on" else False if m == "off" else prompt_uses_weekdays(story)


def start_weekday(story: dict) -> int:
    """The weekday of Day 1: the story's setting, else the one the prompt names for its first day ("Day 1 is a Monday", "begins on a Wednesday"), else Monday."""
    i = weekday_index(settings(story)["start_day"])
    if i is not None:
        return i
    m = re.search(r"(?:day\s*(?:1|one)|first day|begins?|starts?|opens?|arriv\w+)[^.\n]{0,40}?\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", story.get("base_prompt") or "", re.IGNORECASE)
    return weekday_index(m.group(1)) if m else 0


def current_weekday(story: dict) -> int | None:
    if not weekdays_on(story):
        return None
    st = (story.get("tracker") or {}).get("_wd")
    return st["i"] if st else start_weekday(story)


def _advance_weekday(story: dict, turn: int, wrapped: bool, day: str) -> list[dict]:
    """Moves the weekday on: from the numeric Day tracker when there is one (Day 3 is two days after Day 1), else a day at a time when the clock passes midnight or a
    (text) Day value changes. A Weekday the narrator prints itself is adopted. Returns tracker changes (the Weekday field, when the story has one)."""
    tr, out = story.setdefault("tracker", {}), []
    if not weekdays_on(story):
        tr.pop("_wd", None)
        return out
    st = tr.get("_wd")
    n = re.search(r"\d+", day or "")
    if st is None:
        st = {"i": start_weekday(story), "day": day, "t": turn, "shown": None}
        if n:
            st["i"] = (st["i"] + int(n.group()) - 1) % 7
    elif st["t"] != turn:
        if n and re.search(r"\d+", st.get("day") or ""):
            st["i"] = (st["i"] + int(n.group()) - int(re.search(r"\d+", st["day"]).group())) % 7      # Day 3 -> Day 5 is two weekdays on (or back, after an undo)
        elif (day and day != st.get("day")) or wrapped:
            st["i"] = (st["i"] + 1) % 7
        st["t"] = turn
    st["day"] = day or st.get("day")
    f = _wd_field(story)
    if f:
        sec, k = f["id"].split(".", 1)
        seen = (tr.get(sec) or {}).get(k)
        said = weekday_index(seen) if seen not in (None, "") and str(seen) != st.get("shown") else None
        if said is not None:
            st["i"] = said
        new = WEEKDAYS[st["i"]]
        if (tr.setdefault(sec, {})).get(k) != new:
            out.append({"s": sec, "n": None, "k": k, "b": tr[sec].get(k), "a": new})
            tr[sec][k] = new
        st["shown"] = new
    tr["_wd"] = st
    return out
