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
      return `<div class="kv${mark(c)}"><small>${esc(f.label)}</small> ${esc(String(v))}${f.kind === "number" && f.unit ? " " + esc(f.unit) : ""}</div>`;
    }).join("");
  let info = "", chars = "", qh = "", qn = 0, artN = 0;
  const scene = kv("scene");
  if (scene) info += `<h4>Scene${tr.turn ? ` · turn ${tr.turn}` : ""}</h4>${scene}`;
  const meStatus = byId["me.status"] && tr.me && tr.me.status ? `<div>${chip(tr.me.status, find("me", null, "status"))}</div>` : "";
  const me = meStatus + kv("me");
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
      const tip = isNew ? "New this turn" : changed ? `Before: ${old.status}${old.objectives && old.objectives.length ? ", " + old.objectives.filter((o) => o.done).length + "/" + old.objectives.length + " objectives" : ""}  →  After: ${q.status}${ob.length ? ", " + dn + "/" + ob.length + " objectives" : ""}` : "";
      return `<details class="quest ${q.status}${isNew || changed ? " chg" : ""}" data-q="${esc(q.name)}"${open ? " open" : ""}${tip ? ` title="${esc(tip)}"` : ""}><summary><span class="qn">${esc(q.name)}</span>${isNew ? ` <span class="newtag">new</span>` : ""}
        <span class="stchip ${done ? "st-immune" : fail ? "st-dead" : "st-other"}">${done ? "✓ Completed" : fail ? "Failed" : ob.length ? `${dn}/${ob.length}` : "Active"}</span></summary>
        <div class="qbody">${ob.length ? `<ul class="qobj">${ob.map((o) => `<li class="${o.done ? "done" : ""}">${o.done ? "✓" : "○"} ${esc(o.text)}</li>`).join("")}</ul>` : ""}
        ${q.reward ? `<div class="qrew"><small>${done ? "Reward granted" : "Reward"}</small> ${esc(q.reward)}</div>` : ""}<div class="qstat"><small>Status</small> ${esc(q.status)}</div></div></details>`;
    }).join("") : `<div class="side-empty">No quests right now.</div>`);
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
  el.querySelectorAll("details.quest").forEach((d) => d.addEventListener("toggle", () => { if (d.open) openQuests.add(d.dataset.q); else openQuests.delete(d.dataset.q); }));
}

async function refreshTracker() {   // after a reply: let a background AI update finish, then show it
  if (!story) return;
  const id = story.id;
  try { const r = await api(`/stories/${id}/tracker?wait=true`); if (story && story.id === id) { story.tracker = r.tracker; story.sidebar = r.sidebar; renderSidebar(); } } catch (e) { /* keep what is shown */ }
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
      if (f.kind === "number") f.unit = g("unit");
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
          ${k === "number" ? `<label>unit <input data-k="unit" maxlength="12" placeholder="optional, e.g. gold" value="${esc(f.unit || "")}"></label>` : ""}
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
    el.querySelector(".tadd").onclick = () => { sync(); if (list.length < 16) list.push({ label: "", scope: "me", kind: "slider", min: 0, max: 100 }); draw(); const ins = el.querySelectorAll('.trow [data-k="label"]'); if (ins.length) ins[ins.length - 1].focus(); };
  };
  draw();
  return { get: () => { sync(); return list.filter((f) => (f.label || "").trim()); } };
}
