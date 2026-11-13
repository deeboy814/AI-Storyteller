"""Inventory kept by the app (off until the player turns it on for a story): items are cards with a quantity, an effect and an "equipped" mark.
The narrator reports what changed in a hidden <items> line; the app does the adding and removing itself, never lets a count go below zero,
and gives the narrator the true list every turn so "use the potion" fails when there is no potion."""
import re
import uuid

ITEMS_RE = re.compile(r"<items>(.*?)(?:</items>|\Z)", re.DOTALL | re.IGNORECASE)
_OP = re.compile(r"^\s*(?:(?P<sign>[+\-−–])\s*(?P<n>\d+)?\s*[x×]?\s*(?P<name>.+?)|(?P<verb>equip|unequip|wear|remove|wield|unwield)s?\s+(?P<vname>.+?))\s*$", re.IGNORECASE)
_EFFECT = re.compile(r"\s*[\(\[]\s*(?:effect\s*:\s*)?(.+?)\s*[\)\]]\s*$")
_QTY_LEAD = re.compile(r"^\s*(\d+)\s*[x×]?\s+(.+)$")
_QTY_TAIL = re.compile(r"^(.+?)\s*[x×]\s*(\d+)\s*$|^(.+?)\s*\((\d+)\)\s*$")

RULE = ("INVENTORY (kept by the app): the app keeps the player's items. The INVENTORY note each turn is the truth: the player has exactly those things, in those amounts, "
        "and nothing else. When the player tries to use, give, sell, show or equip an item that is not on the list, or more of it than they have, it fails in the story "
        "(they reach for it and it is not there); never let it succeed, and never quietly hand them the item. Equipped items are worn or in hand; their effects apply. "
        "When the player gains, buys, finds, is given, loses, uses up, gives away, sells or breaks an item IN THIS REPLY, add one hidden line near the end of the reply "
        "(before the state block, if this story has one):\n"
        "<items>+2 Healing Potion (restores 20 HP); -1 Rope; equip Iron Sword</items>\n"
        "'+N Name' for something gained (the first time an item appears, add its effect or use in brackets, a few words); '-N Name' for something lost or used up; "
        "'equip Name' / 'unequip Name' when the player puts gear on or away. Use the exact names from the INVENTORY note for things the player already has. "
        "Report only what actually happened in this reply (nothing offered, promised, seen in a shop or asked about), and write no <items> line when nothing changed. "
        "The line is hidden from the player: never mention it. Money, points and other numbers the story tracks are not items.")


def blank() -> dict:
    return {"on": False, "items": []}


def get(story: dict) -> dict:
    inv = story.get("inventory")
    if not isinstance(inv, dict):
        inv = story["inventory"] = blank()
    inv.setdefault("on", False)
    inv.setdefault("items", [])
    return inv


def is_on(story: dict) -> bool:
    return bool((story.get("inventory") or {}).get("on"))


def _key(name: str) -> str:
    k = re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()
    k = re.sub(r"^(a|an|the|some|my|his|her|their)\s+", "", k)
    return re.sub(r"(?<=[a-z]{3})(es|s)$", "", k) if not k.endswith("ss") else k


def clean_item(d: dict) -> dict | None:
    name = re.sub(r"\s+", " ", str((d or {}).get("name") or "")).strip(" .;,")[:60]
    if not name:
        return None
    try:
        qty = int(float(d.get("qty", 1)))
    except Exception:
        qty = 1
    return {"id": str(d.get("id") or uuid.uuid4().hex[:8]), "name": name, "qty": max(0, min(qty, 999999)), "effect": re.sub(r"\s+", " ", str(d.get("effect") or "")).strip()[:160],
            "equipped": bool(d.get("equipped"))}


def clean_items(lst) -> list[dict]:
    out, seen = [], {}
    for d in lst if isinstance(lst, list) else []:
        it = clean_item(d) if isinstance(d, dict) else None
        if not it or it["qty"] <= 0:
            continue
        k = _key(it["name"])
        if k in seen:                       # the same thing twice: one card
            seen[k]["qty"] += it["qty"]
            seen[k]["effect"] = seen[k]["effect"] or it["effect"]
            seen[k]["equipped"] = seen[k]["equipped"] or it["equipped"]
        else:
            seen[k] = it
            out.append(it)
    return out[:200]


def from_text(s: str) -> dict | None:
    """'3 Healing Potions', 'Rope x2', 'Iron Sword (worn)' → an item."""
    s = re.sub(r"\s+", " ", str(s)).strip(" .;,-•*")
    if not s:
        return None
    qty, effect, equipped = 1, "", False
    m = _QTY_TAIL.match(s)
    if m:
        s, qty = (m.group(1), int(m.group(2))) if m.group(1) else (m.group(3), int(m.group(4)))
    m = _EFFECT.search(s)
    if m:
        note = m.group(1)
        s = s[:m.start()]
        if re.fullmatch(r"(?i)(equipped|worn|wielded|in hand|wearing)", note.strip()):
            equipped = True
        else:
            effect = note
    m = _QTY_LEAD.match(s)
    if m and qty == 1:
        qty, s = int(m.group(1)), m.group(2)
    return clean_item({"name": s, "qty": qty, "effect": effect, "equipped": equipped})


def find(items: list[dict], name: str) -> dict | None:
    k = _key(name)
    if not k:
        return None
    for it in items:
        if _key(it["name"]) == k:
            return it
    near = [it for it in items if k in _key(it["name"]) or _key(it["name"]) in k]
    return near[0] if len(near) == 1 else None


def parse_ops(text: str) -> list[dict]:
    ops = []
    for block in ITEMS_RE.findall(text or ""):
        for part in re.split(r"[;\n]+", block):
            m = _OP.match(part.strip(" .,*`"))
            if not m:
                continue
            if m.group("verb"):
                ops.append({"op": "equip" if m.group("verb").lower() in ("equip", "wear", "wield") else "unequip", "name": m.group("vname").strip(" .")})
                continue
            name, effect = m.group("name").strip(), ""
            e = _EFFECT.search(name)
            if e:
                effect, name = e.group(1), name[:e.start()].strip()
            n = int(m.group("n") or 1)
            if name and n > 0:
                ops.append({"op": "add" if m.group("sign") == "+" else "remove", "n": n, "name": name[:60], "effect": effect[:160]})
    return ops[:40]


def strip(text: str) -> str:
    return ITEMS_RE.sub("", text or "")


def apply(story: dict, ops: list[dict], turn: int | None = None) -> None:
    """Do the arithmetic. What changed (and what could not be done) is kept for the side panel and for the narrator's next note."""
    inv = get(story)
    if not inv["on"]:
        return
    items, changes, problems = inv["items"], [], []
    for op in ops:
        it = find(items, op["name"])
        if op["op"] == "add":
            if it:
                it["qty"] += op["n"]
                it["effect"] = it["effect"] or op.get("effect", "")
            else:
                it = clean_item({"name": op["name"], "qty": op["n"], "effect": op.get("effect", "")})
                if not it:
                    continue
                items.append(it)
            changes.append({"name": it["name"], "d": op["n"]})
        elif op["op"] == "remove":
            if not it:
                problems.append(f"the reply took away '{op['name']}', but the player had none (nothing was removed)")
                continue
            took = min(op["n"], it["qty"])
            if took < op["n"]:
                problems.append(f"the reply used {op['n']} × {it['name']}, but the player had only {it['qty']} (they now have 0)")
            it["qty"] -= took
            changes.append({"name": it["name"], "d": -took})
            if it["qty"] <= 0:
                items.remove(it)
        elif it:
            want = op["op"] == "equip"
            if it["equipped"] != want:
                it["equipped"] = want
                changes.append({"name": it["name"], "eq": want})
        elif op["op"] == "equip":
            problems.append(f"the reply equipped '{op['name']}', but the player does not have it")
    del items[200:]
    inv["last"] = {"turn": turn, "changes": changes}
    inv["problems"] = problems[:6]


def line(it: dict) -> str:
    return f"{it['name']} ×{it['qty']}" + (f" ({it['effect']})" if it.get("effect") else "") + (" [equipped]" if it.get("equipped") else "")


def tail_note(story: dict) -> str:
    if not is_on(story):
        return ""
    inv = get(story)
    lst = "; ".join(line(it) for it in inv["items"]) or "nothing at all"
    out = (f"[INVENTORY, kept by the app (the player has exactly this and nothing else): {lst}. "
           "If the player gains, buys, finds, uses up, loses or gives away any item in the reply you are about to write, that reply MUST include the hidden line "
           "<items>+N Name (effect); -N Name</items> so the app can count it; without it the item does not exist next turn.]")
    if inv.get("problems"):
        out += "\n[INVENTORY CORRECTION for your last reply: " + "; ".join(inv["problems"]) + ". Keep the story consistent with the list above.]"
    return out


def seed_from_state(story: dict) -> int:
    """First switch-on: take over the list the story's state block already has."""
    inv = get(story)
    if inv["items"]:
        return 0
    got = [x for x in (from_text(s) for s in (story.get("state") or {}).get("inventory") or []) if x]
    inv["items"] = clean_items(got)
    return len(inv["items"])
