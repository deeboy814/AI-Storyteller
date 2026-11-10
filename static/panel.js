// Side panel: the tracked data of the story (scene, you, characters), with this turn's changes marked and before/after on hover.
let TRACKER_FIELDS = [];
const STATUS_WORDS = { normal: "Normal", immune: "Immune", zombie: "Zombie", dead: "Dead" };

function activeFields() {
  const sb = (story && story.sidebar) || { mode: "prompt" };
  if (sb.mode === "ai" && sb.schema) return sb.schema;
  if (sb.mode === "player" || (sb.mode === "prompt" && (sb.custom || []).length)) return TRACKER_FIELDS.filter((f) => (sb.fields || []).includes(f.id)).concat(sb.custom || []);
  return TRACKER_FIELDS;
}
const fmtVal = (v) => (v === null || v === undefined ? "(nothing yet)" : Array.isArray(v) ? (v.map((q) => q.name + (q.status && q.status !== "active" ? ` (${q.status})` : "")).join(", ") || "(none)") : STATUS_WORDS[v] || String(v));
let panelTab = (() => { try { return localStorage.getItem("panelTab") || ""; } catch (e) { return ""; } })();
let panelMin = (() => { try { return localStorage.getItem("panelMin") === "1"; } catch (e) { return false; } })();
const openQuests = new Set();   // quest rows the player has opened (kept across updates)

function panelSidebarHtml() {
  const tr = story && story.tracker; if (!tr) return "";
  const F = activeFields(), byId = Object.fromEntries(F.map((f) => [f.id, f])), changes = tr.changes || [];
  const find = (s, n, k) => changes.find((c) => c.s === s && (c.n || null) === (n || null) && c.k === k);
  // stat points the player spends with buttons (a number tracker with a "spend on" list)
  const pf = F.find((f) => f.kind === "number" && (f.spend_on || []).length && /^(me|scene)\./.test(f.id));
  const spendIds = new Set(pf ? pf.spend_on : []), pointsNow = pf ? Number((tr[pf.id.split(".")[0]] || {})[pf.id.split(".")[1]]) || 0 : 0;
  const pendingOf = (id) => ((tr._spent || []).find((r) => r.id === id) || {}).n || 0;
  const tipOf = (c) => `Before: ${fmtVal(c.b)}  →  After: ${fmtVal(c.a)}`;
  const mark = (c) => (c ? ` chg" title="${esc(tipOf(c))}` : "");
  const chip = (v, c) => `<span class="stchip st-${STATUS_WORDS[v] ? v : "other"}${mark(c)}">${esc(STATUS_WORDS[v] || v)}</span>`;
  const bar = (f, v, lo, hi, ch) => {
    const span = hi - lo || 1, zero = Math.max(0, Math.min(100, (-lo / span) * 100)), pos = Math.max(0, Math.min(100, ((v - lo) / span) * 100));
    const fill = v >= 0 && lo < 0 ? `left:${zero}%;width:${pos - zero}%` : v < 0 ? `left:${pos}%;width:${zero - pos}%` : `left:0;width:${pos}%`;
    return `<div class="aff${mark(ch)}"><i class="${v < 0 ? "neg" : ""}" style="${fill}"></i>${lo < 0 ? `<b style="left:${zero}%"></b>` : ""}</div>`;
  };
  const kv = (sec) => F.filter((f) => f.id.startsWith(sec + ".") && f.id !== "me.status" && f.kind !== "quests" && (tr[sec] || {})[f.id.split(".")[1]] != null && tr[sec][f.id.split(".")[1]] !== "")
    .map((f) => {
      const k = f.id.split(".")[1], c = find(sec, null, k), v = tr[sec][k];
      if (f.kind === "slider" && typeof v === "number") {
        const d = c && typeof c.b === "number" && typeof c.a === "number" ? c.a - c.b : 0;
        return `<div class="kv"><small>${esc(f.label)} ${v}${d ? ` <span class="${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>` : ""}</small></div>${bar(f, v, f.min ?? 0, f.max ?? 100, c)}`;
      }
      if (f.custom && f.kind === "text" && String(v).includes(" | ")) return `<div class="kv${mark(c)}"><small>${esc(f.label)}</small>${String(v).split(" | ").map((x) => `<div>• ${esc(x)}</div>`).join("")}</div>`;
      const nxt = f.levels && f.levels.length ? nextLevelAt(f.levels, v, f.max_level) : null;
      const spendBtns = spendIds.has(f.id) ? ` <span class="spendbtns">${pendingOf(f.id) > 0 ? `<button class="spend" data-id="${esc(f.id)}" data-n="-1" title="Take back a point you just spent">−</button>` : ""}${pointsNow > 0 && !(pf.spend_cap && Number(v) >= pf.spend_cap) ? `<button class="spend" data-id="${esc(f.id)}" data-n="1" title="Spend a point on ${esc(f.label)}">+</button>` : ""}</span>` : "";
      return `<div class="kv${mark(c)}"><small>${esc(f.label)}</small> ${esc(String(v))}${nxt ? ` <span class="muted">/ ${nxt}</span>` : ""}${f.kind === "number" && f.unit ? " " + esc(f.unit) : ""}${spendBtns}</div>`;
    }).join("");
  let info = "", chars = "", qh = "", qn = 0, artN = 0;
  const clk = tr._clock && Number.isFinite(tr._clock.min) ? tr._clock.min : null;
  const hhmm = (m) => `${String(Math.floor(m / 60) % 24).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  const clockPill = clk == null ? "" : `<div class="kv clockpill" title="The in-world time, kept by the app. Click to set it."><small>Clock</small> <b>${hhmm(clk)}</b> <span class="muted">${["night", "dawn", "morning", "midday", "afternoon", "evening"][(() => { const h = clk / 60; return h < 5 || h >= 21 ? 0 : h < 7 ? 1 : h < 11 ? 2 : h < 14 ? 3 : h < 17 ? 4 : 5; })()]}</span></div>`;
  const scene = clockPill + kv("scene");
  if (scene) info += `<h4>Scene${tr.turn ? ` · turn ${tr.turn}` : ""}</h4>${scene}`;
  const pointsBanner = pf && pointsNow > 0 ? `<div class="ptsbanner">⭐ <b>${pointsNow}</b> ${esc(pf.label.toLowerCase())} to spend <button class="ptsopen">Assign…</button></div>` : "";
  const meStatus = byId["me.status"] && tr.me && tr.me.status ? `<div>${chip(tr.me.status, find("me", null, "status"))}</div>` : "";
  const me = pointsBanner + meStatus + kv("me");
  if (me) info += `<h4>${esc((tr.me && tr.me.name) || "You")}</h4>${me}`;
  const cf = F.filter((f) => f.id.startsWith("chars."));
  const names = Object.keys(tr.chars || {}).filter((n) => cf.some((f) => tr.chars[n][f.id.split(".")[1]] != null));
  if (cf.length && names.length) {
    const sl = cf.filter((f) => f.kind === "slider"), main = sl[0];
    const sortKey = main ? main.id.split(".")[1] : null;
    names.sort((a, b) => (sortKey ? (tr.chars[b][sortKey] ?? -1e9) - (tr.chars[a][sortKey] ?? -1e9) : a.localeCompare(b)));
    chars += names.map((n) => {
      const c = tr.chars[n], cast = (story.cast || []).find((x) => x.name.toLowerCase().split(" ")[0] === n.toLowerCase().split(" ")[0]);
      const needArt = cast && !Object.keys(cast.moods || {}).length && !(story.art_dismissed || []).some((d) => d.toLowerCase() === cast.name.toLowerCase()) && (story.art || {}).suggest !== false;
      if (needArt) artN++;
      const isNew = find("chars", n, "*"), st = cf.find((f) => f.kind === "status"), stv = st && c[st.id.split(".")[1]];
      const sliders = sl.map((f) => {
        const k = f.id.split(".")[1], v = c[k]; if (v == null) return "";
        const vals = names.map((x) => tr.chars[x][k]).filter((x) => typeof x === "number"), lo = Math.min(f.min ?? 0, ...vals, 0), hi = Math.max(f.max ?? 100, ...vals), span = hi - lo || 1;
        const zero = (-lo / span) * 100, pos = ((v - lo) / span) * 100, fill = v >= 0 ? `left:${zero}%;width:${pos - zero}%` : `left:${pos}%;width:${zero - pos}%`, ch = find("chars", n, k);
        const d = ch && typeof ch.b === "number" && typeof ch.a === "number" ? ch.a - ch.b : 0;
        return `<div class="aff${mark(ch)}"><i class="${v < 0 ? "neg" : ""}" style="${fill}"></i><b style="left:${zero}%"></b></div><div class="row"><small>${esc(f.label)} ${v}${d ? ` <span class="${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>` : ""}</small></div>`;
      }).join("");
      const lines = cf.filter((f) => f.kind === "text" || f.kind === "number" || (f.kind === "status" && f !== st)).map((f) => { const k = f.id.split(".")[1]; if (c[k] == null || c[k] === "") return ""; const ch = find("chars", n, k); return `<div class="kv${mark(ch)}"><small>${esc(f.label)}</small> ${esc(String(c[k]))}${f.kind === "number" && f.unit ? " " + esc(f.unit) : ""}</div>`; }).join("");
      return `<div class="pchar ${stv === "dead" ? "dead" : ""}"><div class="row"><span class="pname" ${cast ? `data-slug="${esc(cast.slug)}"` : ""}>${esc(n)}${isNew ? ` <span class="newtag" title="First appeared this turn">new</span>` : ""}</span>${needArt ? `<button class="artneed" data-art="${esc(cast.name)}" title="${esc(cast.name)} has no pictures yet. Click to create some.">🎨</button>` : ""}${stv ? chip(stv, find("chars", n, st.id.split(".")[1])) : ""}</div>${sliders}${lines}</div>`;
    }).join("");
  }
  for (const f of F.filter((x) => x.kind === "quests")) {
    const k = f.id.split(".")[1], list = (tr.me || {})[k]; if (!Array.isArray(list)) continue;
    const ch = find("me", null, k), before = Array.isArray(ch && ch.b) ? ch.b : [], same = (q) => before.find((x) => x.name === q.name);
    const act = list.filter((q) => q.status === "active").length; qn += act;
    qh += `<div class="qhead"><b>${esc(f.label)}</b> <small>${act} active</small></div>` + (list.length ? list.map((q) => {
      const old = same(q), isNew = ch && !old, changed = ch && old && JSON.stringify(old) !== JSON.stringify(q), done = q.status === "completed", fail = q.status === "failed";
      const ob = q.objectives || [], dn = ob.filter((o) => o.done).length, open = openQuests.has(q.name) || done;
      const qk = (x) => String(x).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim(), dlr = (tr._deadlines || []).find((d) => qk(d.name) === qk(q.name) || qk(q.name).includes(qk(d.name)) || qk(d.name).includes(qk(q.name)));
      const dayF = F.find((x) => /^day$/i.test(x.label) && /^(scene|me)\./.test(x.id)), dayNow = dayF ? Number((tr[dayF.id.split(".")[0]] || {})[dayF.id.split(".")[1]]) : NaN;
      const dlBadge = dlr && q.status === "active" ? ` <span class="dlbadge${dayNow > dlr.day ? " over" : ""}" title="Deadline: Day ${dlr.day}">⏰ ${dayNow > dlr.day ? "overdue" : "Day " + dlr.day}</span>` : "";
      const tip = isNew ? "New this turn" : changed ? `Before: ${old.status}${old.objectives && old.objectives.length ? ", " + old.objectives.filter((o) => o.done).length + "/" + old.objectives.length + " objectives" : ""}  →  After: ${q.status}${ob.length ? ", " + dn + "/" + ob.length + " objectives" : ""}` : "";
      return `<details class="quest ${q.status}${isNew || changed ? " chg" : ""}" data-q="${esc(q.name)}"${open ? " open" : ""}${tip ? ` title="${esc(tip)}"` : ""}><summary><span class="qn">${esc(q.name)}${dlBadge}</span>${isNew ? ` <span class="newtag">new</span>` : ""}
        <span class="stchip ${done ? "st-immune" : fail ? "st-dead" : "st-other"}">${done ? "✓ Completed" : fail ? "Failed" : ob.length ? `${dn}/${ob.length}` : "Active"}</span></summary>
        <div class="qbody">${ob.length ? `<ul class="qobj">${ob.map((o) => `<li class="${o.done ? "done" : ""}">${o.done ? "✓" : "○"} ${esc(o.text)}</li>`).join("")}</ul>` : ""}
        ${q.reward ? `<div class="qrew"><small>${done ? "Reward granted" : "Reward"}</small> ${esc(q.reward)}</div>` : ""}<div class="qstat"><small>Status</small> ${esc(q.status)}</div></div></details>`;
    }).join("") : `<div class="side-empty">No quests right now.</div>`);
  }
  const jr = (tr.journal || []).slice().reverse();
  if (F.some((x) => x.kind === "quests") && jr.length) {
    qh = qh || `<div class="qhead"><b>Quests</b> <small>0 active</small></div><div class="side-empty">No quests right now.</div>`;
    qh += `<details class="qjournal"><summary>Journal · ${jr.length} finished</summary>${jr.map((q) => `<div class="qj ${q.status}"><span class="stchip ${q.status === "completed" ? "st-immune" : "st-dead"}">${q.status === "completed" ? "✓" : "✗"}</span> <b>${esc(q.name)}</b>${q.reward ? ` <small class="muted">${esc(q.reward)}</small>` : ""}${q.turn ? ` <small class="muted">· turn ${q.turn}</small>` : ""}</div>`).join("")}</details>`;
  }
  if (!info && !chars && !qh) return "";
  const list = changes.map((c) => { const f = byId[`${c.s}.${c.k}`]; return `${c.n ? c.n + " · " : ""}${c.k === "*" ? "new character" : (f ? f.label : c.k) + ": " + fmtVal(c.b) + " → " + fmtVal(c.a)}`; });
  const panes = [["info", "Info", info], ["chars", artN ? "Characters 🎨" : "Characters", chars], ["quests", qn ? `Quests · ${qn}` : "Quests", qh]].filter((x) => x[2]);
  if (!panes.some((x) => x[0] === panelTab)) panelTab = panes[0][0];
  const chgbar = `<div class="chgbar${changes.length ? "" : " none"}" title="${esc(list.join("\n") || "Nothing changed in the last reply")}">${changes.length ? `● ${changes.length} change${changes.length > 1 ? "s" : ""} this turn` : "No changes this turn"}</div>`;
  const tabs = `<div class="ptabs">${panes.length > 1 ? panes.map((x) => `<button class="ptab${x[0] === panelTab ? " on" : ""}" data-tab="${x[0]}">${esc(x[1])}</button>`).join("") : `<span class="ptitle">${esc(panes[0][1])}</span>`}<button class="pmin" title="${panelMin ? "Show the panel" : "Minimize the panel"}">${panelMin ? "▸" : "▾"}</button></div>`;
  return chgbar + tabs + (panelMin ? "" : panes.filter((x) => x[0] === panelTab).map((x) => `<div class="ppane">${x[2]}</div>`).join(""));
}

function bindPanel(el) {   // tab clicks, minimize, quest rows (called after the sidebar is drawn)
  const keep = (k, v) => { try { localStorage.setItem(k, v); } catch (e) { /* not saved */ } };
  el.querySelectorAll(".ptab").forEach((b) => b.onclick = () => { panelTab = b.dataset.tab; panelMin = false; keep("panelTab", panelTab); keep("panelMin", "0"); renderSidebar(); });
  const m = el.querySelector(".pmin"); if (m) m.onclick = () => { panelMin = !panelMin; keep("panelMin", panelMin ? "1" : "0"); renderSidebar(); };
  el.querySelectorAll(".spend").forEach((b) => b.onclick = () => spendPoint(b.dataset.id, +b.dataset.n));
  const cp = el.querySelector(".clockpill"); if (cp) cp.onclick = async () => { const t = prompt("Set the in-world time (for example 14:30 or 2:30 pm):"); if (!t) return; try { const r = await api(`/stories/${story.id}/clock/set`, { method: "POST", body: { time: t } }); story = r.story; renderSidebar(); } catch (e) { alert(e.message); } };
  const po = el.querySelector(".ptsopen"); if (po) po.onclick = () => showPoints();
  el.querySelectorAll("details.quest").forEach((d) => d.addEventListener("toggle", () => { if (d.open) openQuests.add(d.dataset.q); else openQuests.delete(d.dataset.q); }));
}

async function refreshTracker() {   // after a reply: let a background AI update finish, then show it
  if (!story) return;
  const id = story.id;
  try { const r = await api(`/stories/${id}/tracker?wait=true`); if (story && story.id === id) { story.tracker = r.tracker; story.sidebar = r.sidebar; renderSidebar(); } } catch (e) { /* keep what is shown */ }
}


function nextLevelAt(levels, v, cap) {   // same rule as the server's tracker.level_for (null at the highest level)
  const n = parseFloat(String(v).replace(",", ".").match(/-?\d+(\.\d+)?/)?.[0]); if (isNaN(n)) return null;
  const lv = levels.slice(), max = cap ? cap - 1 : 500;
  while (lv[lv.length - 1] <= n && lv.length < max) {
    const L = lv.length, gap = lv[L - 1] - (L > 1 ? lv[L - 2] : 0), step = L > 2 ? gap - (lv[L - 2] - lv[L - 3]) : 0;
    lv.push(lv[L - 1] + gap + Math.max(step, 0));
  }
  return lv.slice(0, max).find((t) => t > n) ?? null;
}

// ---- the player's own trackers: type a name, say where it lives and how it looks (text / number / slider / status)
const TRK_SCOPES = [["me", "You"], ["scene", "Scene"], ["chars", "Each character"]];
const TRK_KINDS = [["slider", "Slider (gauge)"], ["number", "Number"], ["status", "Status (labels)"], ["text", "Text"], ["quests", "Quest log (tab)"]];
function trackerEditor(el, initial, ctx) {
  // ctx: { storyId?, title?, base_prompt?, models? } for the "Suggest" helper. Returns { get() } giving the cleaned list.
  let list = JSON.parse(JSON.stringify(initial || []));
  const sync = () => {
    el.querySelectorAll(".trow").forEach((r, i) => {
      const f = list[i]; if (!f) return; const g = (k) => { const x = r.querySelector(`[data-k="${k}"]`); return x ? x.value : undefined; };
      f.label = g("label"); f.scope = g("scope"); f.kind = g("kind"); f.note = g("note");
      if (f.kind === "slider") { f.min = g("min"); f.max = g("max"); }
      if (f.kind === "status") f.options = g("options");
      if (f.kind === "number") { f.unit = g("unit"); f.levels = g("levels"); f.max_level = g("max_level"); f.counts = g("counts"); f.spend_on = g("spend_on"); f.spend_cap = g("spend_cap"); }
    });
  };
  const draw = () => {
    el.innerHTML = `<div class="hint" style="margin:6px 0">Your own trackers. Name it, choose where it lives and how it looks. The AI reads each reply and keeps it up to date; nothing else to set up.</div>` +
      list.map((f, i) => {
        const sc = f.scope || (f.id || "").split(".")[0] || "me", k = f.kind || "slider";
        const opt = (arr, v) => arr.map(([a, b]) => `<option value="${a}" ${a === v ? "selected" : ""}>${b}</option>`).join("");
        return `<div class="trow"><div class="trow1"><input data-k="label" maxlength="30" placeholder="Name, e.g. Hunger" value="${esc(f.label || "")}">
          <select data-k="scope" title="Where it lives">${opt(TRK_SCOPES, sc)}</select><select data-k="kind" title="How it looks">${opt(TRK_KINDS, k)}</select>
          <button class="aibtn tsug" title="Let the helper AI suggest the type, range and meaning from the name">✨</button><button class="x tdel" title="Remove">×</button></div>
          <div class="trow2">${k === "slider" ? `<label>from <input data-k="min" type="number" value="${f.min ?? 0}"></label><label>to <input data-k="max" type="number" value="${f.max ?? 100}"></label>` : ""}
          ${k === "status" ? `<input data-k="options" placeholder="Labels, separated by commas (e.g. calm, tense, panicked)" value="${esc(Array.isArray(f.options) ? f.options.join(", ") : f.options || "")}">` : ""}
          ${k === "number" ? `<label>unit <input data-k="unit" maxlength="12" placeholder="optional, e.g. gold" value="${esc(f.unit || "")}"></label>
            <label title="Optional, for an XP tracker: the XP totals where each level begins (level 2 at the first number, level 3 at the second…). The app then keeps a tracker named “Level” in step with the XP, shows the next goal (XP 160 / 300) and tells the narrator the level.">levels at <input data-k="levels" placeholder="optional, e.g. 50, 150, 300" value="${esc(Array.isArray(f.levels) ? f.levels.join(", ") : f.levels || "")}"></label>
            <label title="Optional: the highest level there is. At this level the XP shows no next goal and the narrator is told there are no more level-ups.">max level <input data-k="max_level" type="number" min="2" style="width:80px" placeholder="none" value="${esc(String(f.max_level ?? ""))}"></label>
            <label title="Optional: the app counts the narrator's replies since another tracker (for example Day) last changed, so you get a turn-of-the-day counter that never depends on the narrator counting. The narrator is told the number each turn.">counts replies since <select data-k="counts" style="max-width:150px"><option value="">(not a counter)</option>${list.filter((g, n) => n !== i && (g.label || "").trim() && ["number", "text", "status"].includes(g.kind || "")).map((g) => { const cur = (list.find((h) => h.id && h.id === f.counts) || {}).label || f.counts || ""; return `<option value="${esc(g.label)}" ${String(g.label).toLowerCase() === String(cur).toLowerCase() ? "selected" : ""}>${esc(g.label)}</option>`; }).join("")}</select> changes</label>
            <label title="Optional, for a points tracker (unspent stat or skill points): the stats and skills the player may spend them on, separated by commas. The side panel then shows + buttons, the app does the arithmetic, and the narrator is told the final numbers and never assigns points itself.">spend points on <input data-k="spend_on" style="min-width:170px" placeholder="optional, e.g. Strength, Stamina" value="${esc(Array.isArray(f.spend_on) ? f.spend_on.map((x) => (list.find((h) => h.id === x) || {}).label || x).join(", ") : f.spend_on || "")}"></label>
            <label title="Optional: the most a stat can reach by spending points">max <input data-k="spend_cap" type="number" min="1" style="width:70px" placeholder="none" value="${esc(String(f.spend_cap ?? ""))}"></label>` : ""}
          <input data-k="note" class="tnote" maxlength="200" placeholder="What it means / what changes it (helps the AI)" value="${esc(f.note || "")}"></div><div class="hint tmsg"></div></div>`;
      }).join("") + `<div class="actions" style="justify-content:flex-start"><button class="tadd">＋ Add a tracker</button></div>`;
    el.querySelectorAll(".trow").forEach((r, i) => {
      r.querySelector('[data-k="kind"]').onchange = () => { sync(); draw(); };
      r.querySelector(".tdel").onclick = () => { sync(); list.splice(i, 1); draw(); };
      r.querySelector(".tsug").onclick = async (e) => {
        sync(); const f = list[i], msg = r.querySelector(".tmsg");
        if (!(f.label || "").trim()) { msg.textContent = "Type a name first."; return; }
        e.target.disabled = true; msg.textContent = "Asking the helper AI…";
        try {
          const c = ctx() || {}, s = await api("/tracker-assist", { method: "POST", body: { label: f.label, scope: f.scope, story_id: c.storyId || null, title: c.title || "", base_prompt: c.base_prompt || "", ...(c.models || {}) } });
          Object.assign(f, { scope: s.scope, kind: s.kind, min: s.min, max: s.max, options: s.options, unit: s.unit, note: s.note || f.note }); draw();
          el.querySelectorAll(".trow")[i].querySelector(".tmsg").textContent = "Suggested. Adjust anything you like.";
        } catch (err) { msg.textContent = err.message; e.target.disabled = false; }
      };
    });
    el.querySelector(".tadd").onclick = () => { sync(); if (list.length < 24) list.push({ label: "", scope: "me", kind: "slider", min: 0, max: 100 }); draw(); const ins = el.querySelectorAll('.trow [data-k="label"]'); if (ins.length) ins[ins.length - 1].focus(); };
  };
  draw();
  return { get: () => { sync(); return list.filter((f) => (f.label || "").trim()); } };
}


// "Right now" card: who the player is talking to (picture for their mood, mood, clothing). It takes the place of the player's avatar at the top of the
// side panel; a small button flips back to the avatar. Needs art.now_card (Art tab); the server fills story.now after each reply.
let nowShowAvatar = (() => { try { return localStorage.getItem("nowShowAvatar") === "1"; } catch (e) { return false; } })();
function latestScenePicture() {   // the newest scene picture of the story (while a new one is being drawn, the previous one stays up)
  if (!(story && story.art && story.art.scene_where === "panel")) return null;
  for (let i = (story.turns || []).length - 1; i >= 0; i--) {
    const t = story.turns[i];
    if (t.role === "assistant" && (t.images || []).length) return { turn: i, k: t.images.length - 1, im: t.images[t.images.length - 1] };
  }
  return null;
}
function nowCardHtml() {
  const n = story && story.now, on = story && story.art && story.art.now_card, sc = latestScenePicture();
  const p = on && n && n.name ? portraitInfo(`${n.name}/${n.mood || "default"}`) : null;
  const drawing = typeof illustratingTurn !== "undefined" && illustratingTurn !== null;
  if (!p && !sc && !(drawing && story.art && story.art.scene_where === "panel")) return "";
  const scene = sc ? `<figure class="nowscene"><a href="${esc(sc.im.url)}" target="_blank"><img src="${thumbUrl(sc.im.url, 640)}" alt=""></a>
      <figcaption>${esc(oneLine(sc.im.scene || "", 160))}</figcaption>
      <span class="nowscbtn"><button data-act="redraw" data-turn="${sc.turn}" data-img="${sc.k}" title="Draw this scene again (the earlier picture stays)">↻</button><button data-act="save" data-turn="${sc.turn}" data-img="${sc.k}" title="Keep this picture as a portrait of a character">💾</button><button data-act="del" data-turn="${sc.turn}" data-img="${sc.k}" title="Delete this picture">×</button></span></figure>` : "";
  const drawingNote = drawing && story.art.scene_where === "panel" ? '<div class="drawing">🎨 Drawing the scene…</div>' : "";
  const live = story.art.now_pic === "live" && n && n.image, picUrl = live ? n.image : p && p.url;
  const liveNote = story.art.now_pic === "live" && n && n.drawing ? '<div class="drawing">🎨 Drawing what they are doing…</div>' : "";
  const who = p ? `${liveNote}<div class="nowbody${live ? " live" : ""}">${picUrl ? `<a href="${esc(picUrl)}" target="_blank"><img class="nowpic${live ? " live" : ""}${!live && isActionMood(n.mood || "") ? " upper" : ""}" src="${live ? esc(picUrl) : thumbUrl(picUrl, 360)}" alt="" title="${live ? esc(`${p.ch.name}: ${n.doing || ""}${n.wearing ? " · wearing " + n.wearing : ""}`) : ""}"></a>` : ""}<div class="nowtext"><b class="pname" data-slug="${esc(p.ch.slug)}">${esc(p.ch.name)}</b>
    ${n.mood ? `<div class="nowmood">${esc(n.mood)}</div>` : ""}${n.doing ? `<div class="nowdoing">${esc(n.doing)}</div>` : ""}${n.wearing ? `<div class="nowwear"><small>Wearing</small> ${esc(n.wearing)}</div>` : ""}</div></div>` : "";
  return `<div class="nowcard"><div class="nowhead"><small>Right now</small><button class="nowflip" title="Show my own avatar instead">me</button></div>${scene}${drawingNote}${who}</div>`;
}
async function watchNow() {
  if (!story || !(story.art && story.art.now_card)) return;
  const id = story.id;
  try {
    let r = await api(`/stories/${id}/now?wait=true`);
    if (!story || story.id !== id) return;
    story.now = r.now; renderSidebar();
    for (let i = 0; i < 60 && r.now && r.now.drawing; i++) {     // a new "right now" picture is being drawn: show it when it is ready
      await new Promise((ok) => setTimeout(ok, 4000));
      if (!story || story.id !== id) return;
      r = await api(`/stories/${id}/now`); story.now = r.now; renderSidebar();
    }
  } catch (e) { /* keep what is shown */ }
}


// ---- stat points: spend with buttons (the app does the arithmetic and tells the narrator the final numbers with your next move)
async function spendPoint(id, n) {
  if (!story || busy) return;
  try { const r = await api(`/stories/${story.id}/spend`, { method: "POST", body: { id, n } }); story = r.story; renderSidebar(); if ($("ptsList")) fillPoints(r.points); }
  catch (e) { alert(e.message); }
}
function fillPoints(p) {
  const box = $("ptsList"); if (!box) return;
  $("ptsLeft").textContent = `${p.points} ${p.label.toLowerCase()} left`;
  box.innerHTML = p.targets.map((t) => `<div class="ptsrow"><span class="ptsname">${esc(t.label)}</span><button data-id="${esc(t.id)}" data-n="-1" ${t.pending > 0 ? "" : "disabled"} title="Take back a point you just spent">−</button><b class="ptsval">${t.value}</b>
    <button data-id="${esc(t.id)}" data-n="1" ${p.points > 0 && !(p.cap && t.value >= p.cap) ? "" : "disabled"} title="Spend a point on ${esc(t.label)}">+</button>${t.pending ? `<small class="hint"> +${t.pending} now</small>` : ""}</div>`).join("");
  box.querySelectorAll("button").forEach((b) => b.onclick = () => spendPoint(b.dataset.id, +b.dataset.n));
}
async function showPoints() {
  if (!story) return;
  let p; try { p = await api(`/stories/${story.id}/points`); story = await api("/stories/" + story.id); renderSidebar(); } catch (e) { alert(e.message); return; }
  if (!p.on) { alert("This story has no points to spend. Set up a Points tracker with a “spend points on” list under Settings → Story & narration → Side panel."); return; }
  modal(`<h2>⭐ Spend your ${esc(p.label.toLowerCase())}</h2>
    <p class="hint">Spend them on the stats and skills below. The narrator is told the final numbers with your next move and cannot assign points itself. You can take back points you spent since your last move.${p.cap ? ` A stat cannot go above ${p.cap} this way.` : ""}</p>
    <div class="ptsleft" id="ptsLeft"></div><div id="ptsList"></div>
    <div class="trow"><b>＋ A skill</b> <small class="hint">(for example one your class gives)</small><div class="actions" style="justify-content:flex-start"><input id="ptsSkill" maxlength="30" placeholder="Skill name, e.g. Brace"><button id="ptsAddSkill">Add the skill</button></div><div class="hint" id="ptsMsg"></div></div>
    <div class="actions"><span style="flex:1"></span><button class="primary" id="ptsDone">Done</button></div>`);
  fillPoints(p);
  $("ptsDone").onclick = () => { try { localStorage.setItem("pointsAsked:" + story.id, "1"); } catch (e) { /* not saved */ } closeModal(); };
  $("ptsAddSkill").onclick = async () => {
    const name = $("ptsSkill").value.trim(); if (!name) return;
    try { const r = await api(`/stories/${story.id}/points/skill`, { method: "POST", body: { name } }); story = r.story; renderSidebar(); fillPoints(r.points); $("ptsSkill").value = ""; $("ptsMsg").textContent = `Added ${name}.`; }
    catch (e) { $("ptsMsg").textContent = e.message; }
  };
}
// when a story with points to spend is opened at its start, offer the dialog once
async function maybeAskPoints() {
  if (!story || (story.turns || []).length > 2) return;
  try { if (localStorage.getItem("pointsAsked:" + story.id)) return; } catch (e) { /* ask */ }
  if (!activeFields().some((f) => (f.spend_on || []).length)) return;
  try { const p = await api(`/stories/${story.id}/points`); if (p.on && p.points > 0) showPoints(); } catch (e) { /* optional */ }
}
