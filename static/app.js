"use strict";
const $ = (id) => document.getElementById(id);
let config = null, models = [], story = null, busy = false, sbTrk = null;

// ---------- helpers ----------
async function api(path, opts = {}) {
  const r = await fetch("/api" + path, {
    headers: { "content-type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!r.ok) { let t = (await r.text()) || r.statusText; try { t = JSON.parse(t).detail || t; } catch (e) { /* plain text */ } try { (window.__bugErr = window.__bugErr || []).push({ kind: "api", t: Date.now(), message: `${opts.method || "GET"} ${path.split("?")[0]} -> ${r.status}: ${String(t).slice(0, 160)}` }); } catch (e) { /* ignore */ } throw new Error(t); }
  return r.json();
}
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
// Images: only local ones (/static/..., /portraits/...) are shown. Remote image links
// (e.g. from prompts written for other sites) are dropped so nothing is fetched from outside.
function inlineMd(text) {
  return esc(text).split(/\n+/).filter((l) => l.trim() && !/^[\s*_`]*\[\/\/\]:/.test(l))  // hide markdown-comment lines, even if the model bolds them
    .map((l) => {
      let imgs = "";
      l = l.replace(/!\[[^\]]*\]\(portrait:([^)]+)\)/g, (_, spec) => { imgs += portraitHtml(spec); return ""; });
      l = l.replace(/\[\[([^\]\/\n|]{1,40})\/([^\]\n|]{1,40})\]\]/g, (_, n, m) => { imgs += portraitHtml(n.trim() + "/" + m.trim()); return ""; });   // the compact tag the narrator writes while a reply is still streaming
      l = l.replace(/!\[[^\]]*\]\(intro:([^)]+)\)/g, (_, name) => { imgs += introPanelHtml(name); return ""; });
      l = l.replace(/!\[[^\]]*\]\(place:([^)]+)\)/g, (_, name) => { imgs += placeHtml(name); return ""; });
      l = l.replace(/^&gt;\s?/, "");   // "> quote" marker: show the text without it
      l = l.replace(/!\[[^\]]*\]\((\/(?:static|portraits)\/[^)\s]*)\)/g, (_, u) => { imgs += `<img class="portrait" src="${u}">`; return ""; })
           .replace(/!\[[^\]]*\]\([^)]*\)/g, "");
      // Asterisks that are literal text (escaped \*, or inside a word like f**k / b*tch) must not
      // be read as bold/italic markers. Park them as \u0001 while formatting, then restore.
      l = l.replace(/\\\*/g, "\u0001").replace(/(\w)(\*{1,2})(?=\w)/g, (m, c, a) => c + "\u0001".repeat(a.length));
      l = l.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\*(.+?)\*/g, "<i>$1</i>").replace(/\u0001/g, "*").trim();
      // Spoken words (in quotes) get a span so the two-tone reading option (Settings > Appearance) can show them brighter than the narration.
      l = l.replace(/(“[^”\n]*”|"[^"\n]*"|«[^»\n]*»)/g, '<span class="dlg">$1</span>');
      const hm = l.match(/^(#{1,3})\s+(.+)$/);
      return imgs + (hm ? `<h3 class="mdh">${hm[2]}</h3>` : l ? "<p>" + l + "</p>" : "");
    }).join("");
}
function md(text) {
  // ```Info ... ``` blocks (status panels) become collapsible boxes
  return text.split("```").map((part, i) => {
    if (i % 2 === 0) return inlineMd(part);
    const nl = part.indexOf("\n"), first = (nl >= 0 ? part.slice(0, nl) : part).trim();
    const isLabel = /^[A-Za-z]{0,12}$/.test(first);   // "", "Info", "json" ... versus real text on the fence line
    const body = isLabel ? (nl >= 0 ? part.slice(nl + 1) : "") : part.replace(/^\n/, "");
    // Status panels (```Info) fold away; any other code block is shown as a note box
    if (first.toLowerCase() === "info" || (isLabel && /🎯/.test(body))) return `<details class="panel"><summary>Info</summary><pre>${esc(body)}</pre></details>`;
    return `<pre class="note">${esc(body.replace(/\n+$/, ""))}</pre>`;
  }).join("");
}
function stripState(t) { const cut = ["<state", "<scene", "<newchar"].map((k) => t.indexOf(k)).filter((i) => i >= 0); return cut.length ? t.slice(0, Math.min(...cut)) : t; }

// Token readout under the latest reply. Claude reports cached vs new prompt tokens. Ollama reports the prompt size and
// how long it took to read: a fast read on a long prompt means its cached copy was reused.
const fmtN = (n) => (n || 0).toLocaleString();
function usageLine(u) {
  if (!u || !Object.keys(u).length) return "";
  const prompt = "cache_read" in u
    ? `prompt: ${fmtN(u.cache_read)} cached · ${fmtN(u.input)} new` + (u.cache_write ? ` · ${fmtN(u.cache_write)} written to cache` : "")
    : `prompt: ${fmtN(u.input)} tokens, read in ${((u.prompt_ms || 0) / 1000).toFixed(1)}s`;
  return `<div class="usage">${prompt} · reply: ${fmtN(u.output)}${u.thinking ? ` (incl. ${fmtN(u.thinking)} thinking)` : ""}${u.tok_s ? ` (${u.tok_s} tok/s)` : ""}</div>`;
}

// Claude cache status line (how long it stays warm; whether keep-warm is running)
const fmtDur = (sec) => sec >= 3600 ? `${Math.floor(sec / 3600)}h ${Math.round((sec % 3600) / 60)}m` : `${Math.max(1, Math.round(sec / 60))} min`;
async function refreshCache() {
  try {
    const c = await api("/cache-status"), el = $("cacheNote");
    if (!story || !c.caching || c.story_id !== story.id) { el.textContent = ""; return; }
    let t = c.warm_seconds > 0 ? `Claude cache warm: ${fmtDur(c.warm_seconds)} left` : "Claude cache cold: your next turn rewrites it";
    if (c.keep_warm && c.warm_seconds > 0) t += ` · keep-warm on (stops in ${fmtDur(c.keep_warm_stops_in)}${c.pings ? `, ${c.pings} ping${c.pings > 1 ? "s" : ""} so far` : ""})`;
    if (c.error) t += ` · ping error: ${c.error}`;
    el.textContent = t;
  } catch (e) { /* status line is optional */ }
}
setInterval(refreshCache, 30000);

// ---------- rendering ----------
function renderHeader() {
  const eb = $("btnEndings"); if (eb) eb.classList.toggle("hidden", !(story && (story.endings || []).length));
  $("title").textContent = story ? story.title : "AI Storyteller";
  const te = $("titleEdit");
  te.classList.toggle("hidden", !story);
  te.onclick = () => editTitleInline($("title"), story.id, story.title, (nt) => { story.title = nt; renderHeader(); });
  $("subtitle").textContent = story ? (story.subtitle || "") + (story.nsfw ? "  ·  🔞 adult content on" : "") : "";
}
function renderNarrative(streamText = null, scroll = "bottom") {   // scroll: "bottom" (open a story), "keep" (stay where the reader is), "anchor" (start of your newest move)
  const box = $("narrative"), keepY = box.scrollTop;
  newMoodSeen.clear(); applyPortraitSize();
  if (!story) { box.innerHTML = '<div class="empty">No story open.<br><button id="eNew">Create a story</button></div>'; $("eNew").onclick = () => editStory(null); return; }
  let html = openingHtml();
  const lastAi = story.turns.map((t) => t.role).lastIndexOf("assistant");
  story.turns.forEach((t, i) => {
    const sw = (story.art || {}).scene_where, sc = t.role === "user" ? "" : sceneHtml(t, i);
    const chk = t.role === "assistant" && i === lastAi && t.check && (t.check.issues || []).length
      ? `<div class="rulechk">⚠ <b>Possible rule slips</b><ul>${t.check.issues.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><button class="rcfix">Fix this reply</button> <button class="rcok">Ignore</button></div>` : "";
    const tidy = t.role === "assistant" && (t.tidied || []).length ? `<div class="tidynote" title="The app tidied this reply. The original wording is kept in the turn's data.">✓ Tidied: ${t.tidied.map(esc).join("; ")}</div>` : "";
    const inner = t.role === "user" ? `<div class="player">${md(t.text)}${t.roll ? `<div class="rolltag" title="Rolled by the app: d20 + (stat − 5) against the difficulty. The narrator must narrate this outcome.">🎲 ${esc(rollText(t.roll))}</div>` : ""}${(t.cmds || []).length && !Object.values(CMD_LABEL).includes(t.text) ? `<div class="cmdtags">${t.cmds.map((c) => esc(CMD_LABEL[c] || c)).join(" · ")}</div>` : ""}</div>` : (sw === "banner_top" ? sc + md(t.text) : md(t.text) + sc) + (i === lastAi ? usageLine(t.usage) : "") + tidy + chk;
    html += `<div class="turn ${t.role}${t.bookmark ? " bm" : ""}" data-i="${i}">${t.bookmark ? `<div class="bmlabel">★ ${esc(t.bookmark)}</div>` : ""}${inner}${turnBar(t, i, lastAi)}</div>`;
  });
  if (streamText !== null) html += `<div class="typing" style="min-height:${Math.max(200, box.clientHeight - 40)}px">${md(stripState(streamText)) || ""}</div>`;   // room below, so your move can sit at the top of the page while the reply grows
  if (!story.turns.length && streamText === null)
    html += '<div class="empty">Your story is ready.<br><button class="primary" id="eBegin">Begin the story</button></div>';
  box.innerHTML = html;
  if ($("eBegin")) $("eBegin").onclick = () => send("Begin the story.");
  box.querySelectorAll(".altpick").forEach((b) => b.onclick = async () => {
    try { story = await api(`/stories/${story.id}/opening/choose`, { method: "POST", body: { index: +b.dataset.i } }); renderAll(false); }
    catch (err) { alert(err.message); }
  });
  box.style.scrollBehavior = "auto";
  if (scroll === "keep") box.scrollTop = keepY;
  else if (scroll === "anchor") { const mine = box.querySelectorAll(".turn.user"), el = mine[mine.length - 1]; if (el) box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 12; }
  else box.scrollTop = box.scrollHeight;
  box.style.scrollBehavior = "";
}
// "Writing…" / "Finished" indicator above the input box (the page does not follow the text while it is written)
let genTimer = null;
function genStatus(state, words = 0) {
  const el = $("genStatus"); clearTimeout(genTimer);
  if (!state) { el.classList.add("hidden"); return; }
  el.classList.remove("hidden"); el.className = "genstatus " + state;
  el.innerHTML = state === "writing" ? `<span class="spin"></span> Writing… <small>${words} words so far. The page stays where your move starts; scroll whenever you like.</small>`
    : `✓ <b>Finished writing</b> <small>${words} words</small> <button id="genJump">Jump to the end</button>`;
  if (state === "done") { $("genJump").onclick = () => { const b = $("narrative"); b.scrollTop = b.scrollHeight; genStatus(null); }; genTimer = setTimeout(() => genStatus(null), 20000); }
}
function renderSidebar() {
  const s = (story && story.state) || {}, el = $("sidebar");
  let h = "";
  const av = (story && story.avatar) || {};
  const panel = panelSidebarHtml();   // the tracked side panel (scene, you, characters), when there is data
  h += rollCardHtml();   // the latest dice roll, only while the most recent move had one
  const now = nowShowAvatar ? "" : nowCardHtml();   // who the player is talking to covers the avatar for now
  if (now) h += now;
  else if (story && story.art && story.art.now_card && nowShowAvatar && nowCardHtml()) h += `<div class="nowflipbar"><button class="nowflip" title="Show who I am talking to">↩ who I'm talking to</button></div>`;
  if (!now && story && !panel && (story.avatar_image || av.name)) {
    h += "<h4>You</h4>" + (story.avatar_image ? `<img class="avatar" src="${story.avatar_image}" alt="">` : "") + (av.name ? `<div><b>${esc(av.name)}</b></div>` : "");
  }
  h += panel;
  if (story && story.user_notes) h += `<h4>User notes</h4><div class="side-empty">Active (sent every turn)</div>`;
  if (!panel) {   // stories whose prompt uses the state block and have no tracked data yet
    if (s.when) h += `<h4>When</h4><div>${esc(s.when)}</div>`;
    if (s.location) h += `<h4>Location</h4><div>${esc(s.location)}</div>`;
    if (s.sheet) h += `<h4>Sheet</h4><div>${esc(s.sheet)}</div>`;
    if (s.hp) h += `<h4>HP ${s.hp[0]}/${s.hp[1]}</h4><div class="bar hp"><i style="width:${Math.max(0, Math.min(100, (s.hp[0] / s.hp[1]) * 100))}%"></i></div>`;
    const aff = Object.entries(s.affinity || {});
    if (aff.length) {
      h += "<h4>Affinity</h4>";
      for (const [n, v] of aff) h += `<div class="row"><span>${esc(n)}</span><span>${v}</span></div><div class="bar"><i style="width:${v}%"></i></div>`;
    }
    if ((s.inventory || []).length) h += "<h4>Inventory</h4><ul>" + s.inventory.map((i) => `<li>${esc(i)}</li>`).join("") + "</ul>";
  }
  if (story && story.summary) h += "<h4>Memory</h4><div class='side-empty'>Older events summarized (" + story.summary.length + " chars)</div>";
  el.innerHTML = h || '<div class="side-empty">The sheet fills in as the story is played.</div>';
  el.querySelectorAll(".pname[data-slug]").forEach((n) => n.onclick = () => showCharacter(n.dataset.slug));
  el.querySelectorAll(".nowscbtn button").forEach((b) => b.onclick = async () => {   // the scene picture's own buttons (same as under a reply)
    const turn = +b.dataset.turn, im = story.turns[turn] && (story.turns[turn].images || [])[+b.dataset.img]; if (!im) return;
    if (b.dataset.act === "redraw") illustrate(turn, im.scene);
    else if (b.dataset.act === "save") showSaveAsPortrait(im.url);
    else if (b.dataset.act === "del" && confirm("Delete this picture?")) { story = await api(`/stories/${story.id}/images?url=${encodeURIComponent(im.url)}`, { method: "DELETE" }); renderAll(); }
  });
  el.querySelectorAll(".nowflip").forEach((b) => b.onclick = () => { nowShowAvatar = !nowShowAvatar; try { localStorage.setItem("nowShowAvatar", nowShowAvatar ? "1" : "0"); } catch (e) { /* not saved */ } renderSidebar(); });
  el.querySelectorAll(".artneed[data-art]").forEach((b) => b.onclick = () => showArtDialog({ only: b.dataset.art, onClose: () => { closeModal(); renderAll(); } }));
  bindPanel(el);
}
function renderChips() {
  const c = $("chips");
  c.innerHTML = "";
  if (busy || !story) return;
  for (const s of story.suggestions || []) {
    const b = document.createElement("button");
    b.className = "chip"; b.textContent = s; b.onclick = () => send(s);
    c.appendChild(b);
  }
}
// ---------- suggested replies: pick one, edit it, send it ----------
const startOptsHidden = {};
function renderReplies(loading = false, err = "") {
  const box = $("replyOpts");
  const start = !!(story && !story.turns.length && (story.opening_options || []).length && !startOptsHidden[story.id]);   // the author's suggested first moves
  const ro = start ? { options: story.opening_options, for: 0 } : story && story.reply_options;
  const fresh = start || (ro && ro.options && ro.options.length && ro.for === (story.turns || []).length);
  if (busy || !story || (!fresh && !loading && !err)) { box.classList.add("hidden"); box.innerHTML = ""; return; }
  box.classList.remove("hidden");
  if (loading) { box.innerHTML = '<div class="rhead">Thinking of replies…</div>'; return; }
  if (err) { box.innerHTML = `<div class="rhead"><span>${esc(err)}</span><button id="rClose">×</button></div>`; $("rClose").onclick = () => renderReplies(); return; }
  box.innerHTML = `<div class="rhead"><span>${start ? "Suggested first moves: pick one to edit it, then send" : "Pick one to edit it, then send"}</span><span>${start ? "" : '<button id="rAgain">↻ New options</button> '}<button id="rClose">×</button></span></div>` +
    ro.options.map((o, i) => `<button class="ropt" data-i="${i}"><b>${i + 1}.</b><span>${inlineMd(o)}</span></button>`).join("");
  box.querySelectorAll(".ropt").forEach((b) => b.onclick = () => {
    const t = $("input"); t.value = ro.options[+b.dataset.i]; autosize(); t.focus(); t.setSelectionRange(t.value.length, t.value.length);
    if (start) { startOptsHidden[story.id] = true; renderReplies(); return; }
    ro.for = -1; renderReplies();   // picked: the options go away; they come back, regenerated, when you next scroll to the bottom
  });
  if ($("rAgain")) $("rAgain").onclick = () => fetchReplies(true);
  $("rClose").onclick = () => { if (start) { startOptsHidden[story.id] = true; renderReplies(); return; } ro.for = -1; setRepliesWanted(false); renderReplies(); };   // closing them also stops them coming back by themselves
}
// Once you have used the 💡 button, suggestions follow you: they reappear (regenerated) each time you scroll to the bottom, until you close them with ×.
const repliesWanted = () => { try { return !!story && localStorage.getItem("repliesOn:" + story.id) === "1"; } catch { return false; } };
function setRepliesWanted(on) { try { if (story) localStorage.setItem("repliesOn:" + story.id, on ? "1" : "0"); } catch {} }
let repliesFetching = false, wasAtBottom = true;
const narrativeAtBottom = () => { const b = $("narrative"); return b.scrollHeight - b.scrollTop - b.clientHeight < 40; };
function maybeShowReplies() {
  const ro = story && story.reply_options, fresh = ro && ro.options && ro.options.length && ro.for === (story.turns || []).length;
  if (repliesWanted() && story && !busy && !repliesFetching && !fresh && story.turns.length) fetchReplies(false);
}
async function fetchReplies(manual = true) {
  if (!story || busy || repliesFetching) return;
  if (manual === true) setRepliesWanted(true);
  repliesFetching = true;
  const id = story.id; renderReplies(true);
  try { const r = await api(`/stories/${id}/replies`, { method: "POST" }); if (story && story.id === id) { story.reply_options = r; renderReplies(); } }
  catch (e) { renderReplies(false, e.message); }
  repliesFetching = false;
}
// The story's own command buttons (a package can ship them: Status, Shop, Assign points...): a row above the input box.
function renderCmdBar() {
  const bar = $("cmdBar"), list = (story && story.commands) || [];
  if (!bar) return;
  if (!list.length) { bar.classList.add("hidden"); bar.innerHTML = ""; return; }
  bar.classList.remove("hidden");
  bar.innerHTML = list.map((c, i) => `<button data-i="${i}" title="${esc(c.kind === "points" ? "Spend your stat points (the app does the arithmetic, no AI call)" : c.kind === "edit" ? "Puts this in the box so you can finish it: " + c.text : "Sends: " + c.text)}">${esc(c.label)}</button>`).join("");
  bar.querySelectorAll("button").forEach((b) => b.onclick = () => {
    const c = list[+b.dataset.i]; if (!c || !story) return;
    if (c.kind === "points") { showPoints(); return; }
    if (c.kind === "edit") { const t = $("input"); t.value = (t.value.trim() ? t.value.trim() + " " : "") + c.text; autosize(); t.focus(); return; }
    if (!busy) send(c.text);
  });
}
function renderAll(keepScroll = true) { renderHeader(); renderNarrative(null, keepScroll ? "keep" : "bottom");   // stays where the reader is; only opening a story jumps to the end
  renderSidebar(); renderChips(); renderReplies(); setBusy(busy);
  renderCmdBar();
  if (typeof renderLeft === "function") { renderLeft(); loadLeftList(); } }
function setBusy(b) {
  busy = b;
  for (const id of ["btnSend", "btnUndo", "btnRetry", "btnIllustrate", "input"]) $(id).disabled = b || !story;
  if ($("btnRedo")) { $("btnRedo").disabled = b || !story || !story.undone; $("btnRedo").classList.toggle("hidden", !(story && story.undone)); }
  $("btnSend").textContent = b ? "…" : "Send";
}

// ---------- playing a turn ----------
const rollText = (r) => `${r.label ? r.label + " " + (r.mod >= 0 ? "+" : "") + r.mod + ": " : ""}d20 ${r.die}${r.rolls && r.rolls.length > 1 ? " (" + r.rolls.join(", ") + ")" : ""} = ${r.total} vs ${r.dc} (${r.difficulty}): ${r.outcome}${r.auto ? " · automatic" : ""}`;
// Story commands (▶ Continue, ⚔ Continue the fight, 🔞 Full adult scene) are sent with a move; the adult one can be "armed" so it goes with the next move(s)
const CMD_LABEL = { continue: "▶ Continue", fight: "⚔ Continue the fight", adult: "🔞 Full adult scene" };
let armed = false, armedFor = "";
const cmdKeep = () => { try { return localStorage.getItem("adultKeep") === "1"; } catch (e) { return false; } };
function setArmed(on) { armed = !!on && !!(story && story.nsfw); armedFor = armed ? story.id : ""; document.querySelectorAll("[data-cmd='adult']").forEach((b) => b.classList.toggle("armed", armed)); const i = $("btnQuick"); if (i) i.classList.toggle("armed", armed); }
function sendCommand(cmd, opts = {}) {
  const typed = $("input").value.trim();
  send(typed || CMD_LABEL[cmd], undefined, false, "", false, [cmd], opts);
}
async function send(text, override, forceBudget = false, note = "", redraw = false, cmds = [], cmdOpts = {}, roll = null) {
  text = (text || "").trim();
  if (!text || busy || !story) return;
  if (armed && story.nsfw && armedFor === story.id && !cmds.includes("adult")) cmds = [...cmds, "adult"];       // the armed adult instruction goes with this move
  if (!cmdKeep()) setArmed(false);
  const own = story.models && story.models.story;   // the story's own choice is used by the server; the header menu only matters for older stories
  const [provider, model] = override ? override.split("|") : own ? [undefined, undefined] : ($("modelSelect").value || "|").split("|");
  $("input").value = ""; autosize();
  if (window.matchMedia("(pointer: coarse)").matches) $("input").blur();   // phone: close the keyboard now so it stays closed while the reply is written
  setBusy(true); renderChips();
  // optimistic display of the player's line + streaming reply
  story.turns.push({ role: "user", text, ...(cmds.length ? { cmds, cmd_opts: cmdOpts } : {}), ...(roll ? { roll } : {}) });
  let acc = "", failed = null, srvNote = "";
  renderNarrative("", "anchor"); genStatus("writing", 0);
  try {
    const r = await fetch(`/api/stories/${story.id}/turn`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ text, provider, model, force_budget: forceBudget, note, cmds, cmd_opts: cmdOpts, roll }),
    });
    if (!r.ok) throw new Error(await r.text());
    const reader = r.body.getReader(), dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
        if (!line.startsWith("data:")) continue;
        const ev = JSON.parse(line.slice(5));
        if (ev.t === "delta") { acc += ev.v; const ty = $("narrative").querySelector(".typing"); if (ty) ty.innerHTML = md(stripState(acc)); genStatus("writing", stripState(acc).split(/\s+/).filter(Boolean).length); }
        else if (ev.t === "done") story = ev.story;
        else if (ev.t === "error") failed = ev.v;
        else if (ev.t === "note") srvNote = ev.v;
      }
    }
  } catch (e) { failed = e.message; }
  if (failed) {
    try { failed = JSON.parse(failed).detail || failed; } catch (e) { /* plain text */ }
    try { story = await api("/stories/" + story.id); }  // drop the unsaved optimistic turn
    catch (e) {                                          // the story is gone from the server: never leave the page stuck on "Writing…"
      story.turns.pop();
      failed = /not found/i.test(e.message) ? "This story no longer exists on this computer (it may have been deleted, or the app is using a different data folder). Go Home and open or create it again." : e.message;
    }
    $("input").value = Object.values(CMD_LABEL).includes(text) ? "" : text; genStatus(null);
  }
  const words = failed ? 0 : stripState(acc).split(/\s+/).filter(Boolean).length;
  setBusy(false); renderAll(failed ? false : true); refreshCache(); refreshArtSuggestions();
  if (failed) {   // after the final redraw, or it would wipe the message
    $("narrative").insertAdjacentHTML("beforeend", `<div class="err">${esc(failed.replace(/^BUDGET: /, ""))}</div>`);
    $("narrative").scrollTop = $("narrative").scrollHeight;
  }
  if (srvNote && !failed) $("narrative").insertAdjacentHTML("beforeend", `<div class="hint">${esc(srvNote)}</div>`);
  if (!failed) { genStatus("done", words); refreshTracker(); watchBackdrop(); watchNow(); wasAtBottom = false; if (narrativeAtBottom() && $("narrative").scrollHeight <= $("narrative").clientHeight + 40) maybeShowReplies(); }
  const lastT = story.turns[story.turns.length - 1];
  if (!failed && lastT && lastT.role === "assistant" && !(lastT.images || []).length && ((story.art && story.art.auto && lastT.scene) || redraw)) illustrate(story.turns.length - 1);
  if (!failed && story.auto_replies) fetchReplies(false);
  if (!failed && story.rule_check) watchRuleCheck(story.turns.length - 1);
  refreshBudget();
  if (failed && failed.startsWith("BUDGET:") && confirm(failed.slice(8) + "\n\nSend anyway, this once?")) { $("input").value = ""; return send(text, override, true, note, redraw); }
  if (window.matchMedia("(pointer: coarse)").matches) { if (document.activeElement === $("input")) $("input").blur(); }
  else $("input").focus();   // on a phone, focusing here would pop the keyboard up over the story the moment it finishes writing
}
// Rule check (story option): fetch the helper's verdict on the newest reply and show it under the reply.
async function watchRuleCheck(i) {
  try {
    const r = await api(`/stories/${story.id}/turns/${i}/check?wait=true`);
    const t = story && story.turns[i];
    if (!t || t.role !== "assistant" || i !== story.turns.length - 1) return;
    t.check = { issues: r.issues || [] };
    if ((r.issues || []).length) renderNarrativeKeepScroll();
  } catch (e) { /* the check is optional */ }
}
$("narrative").addEventListener("click", (e) => {
  const b = e.target.closest(".rulechk button"); if (!b || busy || !story) return;
  const t = story.turns[story.turns.length - 1]; if (!t || !t.check) return;
  if (b.classList.contains("rcok")) { t.check.issues = []; renderNarrativeKeepScroll(); return; }
  retryTurn(undefined, "Fix these slips of the story's rules, and keep everything else about the reply: " + t.check.issues.join(" "));
});
// Spending-limit warning above the input box
async function refreshBudget() {
  const el = $("budgetNote"); if (!el) return;
  try {
    const b = await api("/budget"), hit = ["daily", "monthly"].map((k) => [k, b[k]]).filter(([, v]) => v.limit > 0 && v.pct >= 80).sort((x, y) => y[1].pct - x[1].pct)[0];
    if (!hit) { el.classList.add("hidden"); return; }
    const [k, v] = hit, over = v.pct >= 100;
    el.className = "budgetnote " + (over ? "over" : "warn");
    el.innerHTML = `${over ? "⛔" : "⚠"} ${over ? "You have reached" : "You have used " + v.pct + "% of"} ${k === "daily" ? "today's" : "this month's"} spending limit ($${v.spent.toFixed(2)} of $${v.limit.toFixed(2)}, estimated).${over && b.action === "stop" ? " Paid calls ask before sending." : ""} <button id="bnSet">Change</button>`;
    $("bnSet").onclick = () => showSettings("Memory");
  } catch (e) { el.classList.add("hidden"); }
}
function hadPicture() { const l = [...story.turns].reverse().find((x) => x.role === "assistant"); return !!(l && (l.images || []).length); }
async function undo() {
  if (busy || !story || !story.turns.length) return null;
  const last = [...story.turns].reverse().find((t) => t.role === "user");
  story = await api(`/stories/${story.id}/undo`, { method: "POST" });
  renderAll();
  return last ? last.text : null;
}

// ---------- modals ----------
let artBack = null;   // set while a screen opened from the 🖼 Art menu is showing: a "← Art menu" button on top of it reopens the menu
function modal(html) {
  $("modalCard").className = "card"; $("modalCard").innerHTML = html; $("modal").classList.remove("hidden");
  if (artBack) {
    const b = document.createElement("button");
    b.type = "button"; b.className = "backart"; b.textContent = "← Art menu";
    b.onclick = () => { const f = artBack; artBack = null; f(); };
    $("modalCard").prepend(b);
  }
  foldLongHints($("modalCard"));
}
// Long explanations in dialogs show two lines and a "More" link, so the controls stay easy to find.
function foldLongHints(root) {
  root.querySelectorAll("div.hint, p.hint").forEach((h) => {
    if (h.dataset.fold || h.id || (h.textContent || "").trim().length < 240 || h.querySelector("input, select, textarea, button")) return;
    h.dataset.fold = "1"; h.classList.add("folded");
    const m = document.createElement("button"); m.type = "button"; m.className = "foldmore"; m.textContent = "More";
    m.onclick = () => { const f = h.classList.toggle("folded"); m.textContent = f ? "More" : "Less"; };
    h.after(m);
  });
}
let onModalClose = null;
function closeModal() { artBack = null; $("modal").classList.add("hidden"); if (onModalClose) onModalClose(); }
$("modal").addEventListener("mousedown", (e) => { if (e.target === $("modal")) closeModal(); });

// Import a story played elsewhere: memory summary + latest status panel (+ optional last few turns word for word).
function showImport(list, opts = {}) {
  let mode = "summary";
  const rec = (config.default_models || {}).recaps || config.active || {};
  const modelOpts = models.map((m) => `<option value="${esc(m.provider)}|${esc(m.model)}" ${rec.provider === m.provider && rec.model === m.model ? "selected" : ""}>${esc(m.provider_name)}: ${esc(m.model)}</option>`).join("");
  modal(`<h2>Import a save</h2>
    ${opts.into ? `<p class="hint">The save continues <b>${esc(opts.intoTitle || "this story")}</b>: its prompt, cast and media stay as they are.</p>` : ""}
    <div id="iStoryBox"${opts.into ? ' class="hidden"' : ""}><h3>Story</h3>
    <label>Title</label><input id="iTitle" placeholder="e.g. My Story (turn 40)">
    <label>Story prompt: copy it from one of your stories, or paste it below</label>
    <select id="iSource" style="width:100%"><option value="">Paste a prompt below (or leave it empty: the AI writes one from your text)</option>${list.map((x) => `<option value="${x.id}" ${story && story.id === x.id ? "selected" : ""}>${esc(x.title)}</option>`).join("")}</select>
    <textarea class="short" id="iPrompt" style="min-height:70px" placeholder="Only needed when you don't copy it from a story. Left empty, the AI writes a prompt from your summary or text, which you can edit afterwards."></textarea>
    </div>
    <h3>Your character</h3>
    <label>Name</label><input id="iName">
    <h3>What has happened so far</h3>
    <div class="impmode"><button id="mSum" class="on">I have a summary</button><button id="mFull">I have the whole story</button></div>
    <div id="pSum">
      <label>Memory: everything that has happened so far (paste your summaries)</label>
      <textarea class="short" id="iSummary" style="min-height:160px"></textarea>
    </div>
    <div id="pFull" class="hidden">
      <label>The story so far: paste it, or load a text file. Text labelled <code>PLAYER:</code> / <code>NARRATOR:</code> (or You / Assistant, Human / AI, Me / GM…) keeps the last few turns word for word; plain prose keeps its last few paragraphs as the scene in progress; JSON chat exports (ChatGPT, Claude, SillyTavern…) are read directly; everything before that is summarized.</label>
      <textarea class="short" id="iFull" style="min-height:160px"></textarea>
      <div class="actions" style="justify-content:flex-start"><label class="btnlike">Load a .txt / .md / .json file…<input type="file" id="iFile" accept=".txt,.md,.json,.jsonl,text/plain,text/markdown,application/json" hidden></label><span class="hint" id="iCount"></span></div>
      <label>Which AI writes the summary (and the story prompt, when you give none)?</label><select id="iModel" style="width:100%">${modelOpts}</select>
      <p class="hint">The text is cut into parts of about 14,000 characters and summarized one part at a time, each building on the last. A cheaper model is fine; a stronger one keeps more detail. You can edit the result afterwards (👤 Player → Memory).</p>
    </div>
    <label>Latest status panel (optional; if you leave it empty, the last status panel found in the text is used)</label>
    <textarea class="short" id="iPanel" style="min-height:90px"></textarea>
    <div id="pTurns"><label>Optional: last few turns, word for word</label>
    <textarea class="short" id="iTurns" style="min-height:80px" placeholder="PLAYER: what you did&#10;NARRATOR: what the narrator wrote"></textarea>
    <p class="hint">Recent turns keep the narrator's voice and the scene in progress. Use PLAYER: and NARRATOR: labels (or separate blocks with a line of ---).</p></div>
    <div id="iProg" class="hint"></div>
    <div class="actions sticky"><button id="mClose">Cancel</button><button class="primary" id="mGo">Import &amp; open</button></div>`);
  const setMode = (m) => {
    mode = m;
    $("mSum").classList.toggle("on", m === "summary"); $("mFull").classList.toggle("on", m === "full");
    $("pSum").classList.toggle("hidden", m !== "summary"); $("pTurns").classList.toggle("hidden", m !== "summary"); $("pFull").classList.toggle("hidden", m !== "full");
  };
  $("mSum").onclick = () => setMode("summary"); $("mFull").onclick = () => setMode("full");
  const count = () => { const n = $("iFull").value.length; $("iCount").textContent = n ? `${n.toLocaleString()} characters ≈ ${Math.max(1, Math.ceil(n / 14000))} part(s) to summarize` : ""; };
  $("iFull").oninput = count;
  $("iFile").onchange = async (e) => { const f = e.target.files[0]; if (f) { $("iFull").value = await f.text(); count(); } };
  $("mClose").onclick = () => { closeModal(); };
  $("mGo").onclick = async () => {
    const base = { title: $("iTitle").value, source_story_id: $("iSource").value || null, base_prompt: $("iPrompt").value, avatar: { name: $("iName").value }, status_panel: $("iPanel").value, into_story_id: opts.into || null };
    const btn = $("mGo"); btn.disabled = true;
    try {
      let r;
      if (mode === "summary") {
        const [sp, sm] = ($("iModel") ? $("iModel").value : "|").split("|");
        $("iProg").textContent = !base.base_prompt.trim() && !base.source_story_id && !base.into_story_id ? "Writing a story prompt from your summary…" : "";
        r = await api("/stories/import", { method: "POST", body: { ...base, summary: $("iSummary").value, recent_turns: $("iTurns").value, provider: sp || null, model: sm || null } });
        story = r.story;
      } else {
        const [provider, model] = $("iModel").value.split("|");
        const job = await api("/stories/import-text", { method: "POST", body: { ...base, text: $("iFull").value, provider, model } });
        const j = await watchJob(job.id, (x) => { $("iProg").textContent = x.current ? `${x.current}… (${x.done}/${x.total} done)` : ""; });
        if (j.status !== "done") throw new Error(j.error || "Import failed");
        story = await api("/stories/" + j.result.story_id);
        r = { warnings: j.result.warnings };
      }
      localStorage.setItem("story", story.id);
      closeModal(); hideHome(); renderAll(false); syncModelSelect();
      if (r.warnings && r.warnings.length) alert(r.warnings.join("\n\n"));
    } catch (e) { btn.disabled = false; $("iProg").textContent = ""; alert("Import failed: " + e.message); }
  };
}

// Player panel: Avatar (your character's sheet + picture) and User Notes (standing instructions).
// Both are sent to the model every turn, along with the story prompt and memory.
function showPlayer() {
  if (!story) return;
  const av = story.avatar || {};
  const field = (key, label, rows) => rows
    ? `<div><label>${label}</label><textarea class="short" id="av_${key}" rows="${rows}">${esc(av[key] || "")}</textarea></div>`
    : `<div><label>${label}</label><input id="av_${key}" value="${esc(av[key] || "")}"></div>`;
  const preview = () => story.avatar_image ? `<img class="avatar" src="${story.avatar_image}" alt="">` : '<div class="avatar noimg">No image</div>';
  modal(`<h2>Player</h2>
    <div class="tabs"><button class="tab on" data-tab="Avatar">Avatar</button><button class="tab" data-tab="Notes">User notes</button></div>
    <div id="tabAvatar">
      ${config.avatar_library ? avatarLibraryRow() : ""}
      <div class="avrow"><div id="avPreview">${preview()}</div>
        <div><input type="file" id="avFile" accept="image/png,image/jpeg,image/webp,image/gif" style="padding:4px">
        <div class="actions" style="justify-content:flex-start;margin-top:8px"><button id="avGen" title="Draws four pictures from the Avatar fields below with this story's picture maker, and you pick one">✨ Draw 4 options from the info below</button><button class="danger" id="avRemove">Remove image</button></div>
        <div class="actions" style="justify-content:flex-start;margin-top:4px"><button id="avBuild" title="Only have a name? The text model fills the empty boxes below from the story prompt, and you review them before saving">🪄 Build from story</button><span class="hint" id="avBuildNote"></span></div>
        <div id="avCand" class="hint">Upload your own, or generate one from the name, age, gender and appearance below (a safe-for-work description is written from them first). Uploads and removals apply immediately (PNG/JPEG/WebP/GIF, up to 8 MB). The picture also joins the Cast as you, so scenes can use it as a face reference; whether it is shown in scenes is up to your story prompt.</div></div></div>
      ${field("name", "Name (replaces {user} in the story prompt)")}
      <div class="grid2" style="grid-template-columns:1fr 1fr">${field("gender", "Gender")}${field("age", "Age")}</div>
      ${field("appearance", "Appearance", 3)}${field("personality", "Personality", 3)}${field("background", "Background", 3)}
      ${field("abilities", "Abilities / stats / starting inventory", 3)}
    </div>
    <div id="tabNotes" class="hidden">
      <label>Standing instructions for the narrator</label>
      <textarea id="uNotes" placeholder="e.g. Keep combat gritty and consequences permanent. Slow-burn romance. Never skip time without asking. Avoid repeating a character's catchphrase…">${esc(story.user_notes || "")}</textarea>
      <p class="hint">Sent every turn, right after the story's memory. Edit any time; changes apply from the next turn.</p>
    </div>
    <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="mSave">Save</button></div>`);
  if (config.avatar_library) avatarLibraryInit();
  document.querySelectorAll(".tab").forEach((b) => b.onclick = () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
    for (const n of ["Avatar", "Notes"]) $("tab" + n).classList.toggle("hidden", n !== b.dataset.tab);
  });
  const refresh = () => { $("avPreview").innerHTML = preview(); renderSidebar(); };
  $("avFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const r = await fetch(`/api/stories/${story.id}/avatar-image`, { method: "POST", headers: { "content-type": f.type }, body: f });
    if (!r.ok) { alert((await r.json().catch(() => ({}))).detail || "Upload failed"); return; }
    story = await r.json(); refresh();
  };
  let avOpts = [], avLook = "";
  const showAvOpts = () => {
    $("avCand").innerHTML = avOpts.length ? `<b>Pick a picture.</b> Nothing is saved until you choose one.
      <div class="gallery">${avOpts.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary avUse" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button id="avMore">Draw 4 more</button><button id="avDiscard">Discard</button></div><div class="hint">Drawn from: ${esc(avLook)}</div>` : "";
    document.querySelectorAll(".avUse").forEach((b) => b.onclick = async () => {
      try { story = await api(`/stories/${story.id}/avatar-image/use`, { method: "POST", body: { look: avLook, url: b.dataset.url } }); avOpts = []; refresh(); $("avCand").textContent = "Saved as your avatar (and added to the Cast as you)."; }
      catch (err) { $("avCand").textContent = err.message; }
    });
    if ($("avMore")) $("avMore").onclick = () => genAvatar(false);
    if ($("avDiscard")) $("avDiscard").onclick = () => { avOpts = []; $("avCand").textContent = ""; };
  };
  const genAvatar = async (fresh = true) => {
    const avatar = {}; for (const k of ["name", "gender", "age", "appearance", "background"]) avatar[k] = $("av_" + k).value;
    $("avGen").disabled = true; $("avCand").textContent = "Starting…";
    try {
      const r = await api(`/stories/${story.id}/avatar-image/candidates`, { method: "POST", body: { avatar, count: 4, fresh } });
      const job = await watchJob(r.job, (j) => { if ($("avCand")) $("avCand").textContent = j.current || "Drawing…"; });
      if (job.status === "error") { $("avCand").textContent = "Could not draw: " + job.error; }
      else { if (fresh) avOpts = []; avOpts.push(...job.result.candidates); avLook = job.result.look; showAvOpts(); }
    } catch (err) { $("avCand").textContent = err.message; }
    $("avGen").disabled = false;
  };
  $("avBuild").onclick = async () => {
    const avatar = {}; for (const k of ["name", "gender", "age", "appearance", "personality", "background", "abilities"]) avatar[k] = $("av_" + k).value;
    const note = $("avBuildNote"); $("avBuild").disabled = true; note.textContent = "Reading the story prompt…";
    try {
      const r = await api(`/stories/${story.id}/avatar/suggest`, { method: "POST", body: { avatar } });
      let n = 0;
      for (const [k, v] of Object.entries(r.fields)) { const el = $("av_" + k); if (el && v && !el.value.trim()) { el.value = v; n++; } }   // only empty boxes are filled; what you wrote stays
      note.textContent = n ? `Filled ${n} empty box${n > 1 ? "es" : ""}. Read them, change what you like, then press Save.` : "Every box already has text, so nothing was changed.";
    } catch (err) { note.textContent = err.message; }
    $("avBuild").disabled = false;
  };
  $("avGen").onclick = () => genAvatar(true);
  $("avRemove").onclick = async () => { story = await api(`/stories/${story.id}/avatar-image`, { method: "DELETE" }); refresh(); };
  $("mClose").onclick = closeModal;
  $("mSave").onclick = async () => {
    const avatar = {};
    for (const k of ["name", "gender", "age", "appearance", "personality", "background", "abilities"]) avatar[k] = $("av_" + k).value;
    story = await api(`/stories/${story.id}/player`, { method: "PUT", body: { avatar, user_notes: $("uNotes").value } });
    closeModal(); renderAll();
  };
}
function currentChoices() { // what this story uses right now, including older stories that predate per-story choices
  const m = (story && story.models) || {}, d = config.default_models || {}, out = {};
  out.story = m.story || (story ? config.active : d.story);
  const ev = (config.events_model || "").split("|");
  out.recaps = m.recaps || (story && ev.length === 2 && ev[0] ? { provider: ev[0], model: ev[1] } : d.recaps);
  out.small = m.small || (story ? null : d.small);
  out.images = m.images || (story && config.image_active ? { provider: config.image_active, model: "" } : d.images);
  if (out.images && !out.images.model) out.images = null;
  return Object.fromEntries(Object.entries(out).filter(([, v]) => v));
}
async function showSettings(tab = "AI") {
  config = await api("/config");
  const provHtml = (p, i) => `<div class="prov" data-i="${i}"><label>Provider name</label><input data-f="name" value="${esc(p.name)}">
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Type</label><select data-f="type"><option value="ollama">ollama (local)</option><option value="anthropic">anthropic (Claude)</option><option value="openai">openai-compatible</option></select></div>
    <div><label>Base URL</label><input data-f="base_url" value="${esc(p.base_url)}"></div></div>
    <label>API key ${p.has_key ? "(saved; leave blank to keep it)" : ""}</label><input data-f="api_key" type="password" placeholder="${p.has_key ? "••••••••" : p.type === "ollama" ? "not needed for local models" : "paste key"}">
    <label>Models (comma-separated${p.type === "ollama" ? "; installed ones are found automatically" : "; found automatically once a key is saved"})</label><input data-f="models" value="${esc((p.models || []).join(", "))}">
    <div class="actions" style="margin-top:8px"><button class="danger" data-rm="${i}">Remove provider</button></div></div>`;
  const field = (label, control, help = "") => `<div class="field"><label>${label}</label>${control}${help ? `<div class="hint">${help}</div>` : ""}</div>`;
  modal(`<h2>Settings</h2>
    <div class="tabs"><button class="tab stt" data-t="AI">AI models</button><button class="tab stt" data-t="Story">Story &amp; narration</button><button class="tab stt" data-t="Memory">Memory &amp; cost</button><button class="tab stt" data-t="Appearance">Appearance</button><button class="tab stt" data-t="Phone">Phone &amp; tablet</button><button class="tab stt" data-t="Local">Local models</button><button class="tab stt" data-t="Backup">Libraries &amp; backup</button></div>

    <div class="stpane" data-p="AI">
      <h3>Which AI does what${story ? ` in “${esc(story.title)}”` : ""}</h3>
      <div id="aiPick"></div><div id="aiPickImg" class="hidden"></div>
      <div id="artStyleBox"></div>
      <div class="hint">${story ? "Saved for this story, and used as the starting choice for new stories." : "Used as the starting choice for new stories (open a story to change it for that story)."}</div>
      <h3>Claude's thinking</h3>
      <div class="grid2">${field("Thinking", '<select id="sThink"><option value="off">Lowest available</option><option value="on">On (more tokens)</option></select>', "Extra private reasoning before replying. Costs output tokens.")}
        ${field("Effort", '<select id="sEffort"><option value="low">Low (recommended)</option><option value="medium">Medium</option><option value="high">High</option></select>', "Low keeps hidden thinking short.")}
        ${field("Room for hidden thinking", `<input id="sHead" type="number" min="0" step="512" value="${config.thinking_headroom ?? 4096}">`, "tokens added to the reply limit so thinking can't cut a reply short; only used tokens are billed.")}</div>
      <h3>Providers</h3>
      <div class="hint">Connections to AI services. API keys can also be pasted in the AI chooser. Add any OpenAI-compatible service (OpenRouter, LM Studio, DeepSeek…).</div>
      <details class="fold"><summary>Show provider settings (${config.providers.length})</summary><div id="provs">${config.providers.map(provHtml).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button id="mAdd">+ Add provider</button></div></details>
    </div>

    <div class="stpane hidden" data-p="Story">
      ${story ? `<h3>Content</h3><label class="chk top"><input type="checkbox" id="sNsfw" ${story.nsfw ? "checked" : ""}><span><b>Adult (NSFW) content</b><span class="hint">Allows explicit content between adult characters in the narration. Off by default and set per story. It also lets scene pictures and portraits show adult content between adults (covers and backdrops stay safe for work). Online picture services (Gemini, OpenAI, Grok) usually refuse explicit images; a local model in ComfyUI is what allows them. Your AI provider's own rules still apply.</span></span></label>` : ""}
      <h3>Writing</h3>
      <div class="grid2">${field("Creativity (temperature)", `<input id="sTemp" type="number" step="0.05" min="0" max="2" value="${config.temperature}">`, "How adventurous the word choice is. Lower (0.5) = predictable and focused, higher (1.1) = more varied and surprising. Matters for local models; Claude's newer models and ChatGPT's reasoning models ignore it.")}
        ${field("Longest reply", `<input id="sMax" type="number" step="128" min="256" value="${config.max_tokens}">`, "tokens (about ¾ of a word each). Replies stop here.")}
        ${field("Context size (local models)", `<input id="sCtx" type="number" step="1024" min="2048" value="${config.num_ctx}">`, "How much text a local model reads at once. Bigger remembers more but needs more video memory; 8192 is safe on 12 GB.")}</div>
      <h3>Side panel</h3>
      ${story ? `<div class="field"><label>What the side panel shows in “${esc(story.title)}”</label><select id="sbMode"><option value="prompt">Leave it to the prompt author</option><option value="player">I choose the fields</option><option value="ai">Let the AI decide</option></select><div class="hint" id="sbHint"></div></div>
        <div id="sbFields"></div><div id="sbCustom"></div><div id="sbAi"></div>
        <div class="actions" style="justify-content:flex-start;margin-top:4px"><button id="sbBuild" title="Forget the panel's values and read them again from the whole story (corrects a panel that went wrong)">Re-read the story and rebuild</button><span class="hint" id="sbNote"></span></div>` : '<div class="hint">Open a story to set its side panel.</div>'}
      <div class="field"><label>Default for new stories</label><select id="sbDefault"><option value="prompt">Leave it to the prompt author</option><option value="player">I choose the fields</option><option value="ai">Let the AI decide</option></select></div>
      <h3>Key events</h3>
      ${field("Key events log", '<select id="sEvents"><option value="every">Update after every reply</option><option value="off">Off (only what you write)</option></select>', "Writes each character's important moments (Cast → character → Key events) with one small background call per reply, using the recaps &amp; helpers AI above.")}
    </div>

    <div class="stpane hidden" data-p="Memory">
      <h3>Spending limit</h3>
      <div class="actions" style="justify-content:flex-start;margin:0 0 6px"><button id="sUsage">💲 Usage &amp; cost…</button></div>
      <div class="grid2">${field("Per day", `<input id="sBudD" type="number" min="0" step="0.5" value="${config.budget_daily || ""}" placeholder="no limit">`, "dollars")}
        ${field("Per month", `<input id="sBudM" type="number" min="0" step="1" value="${config.budget_monthly || ""}" placeholder="no limit">`, "dollars")}
        ${field("When a limit is reached", '<select id="sBudAct"><option value="warn">Just warn me</option><option value="stop">Ask before every paid call</option></select>', "Local models are free and never stopped.")}</div>
      <div class="hint" id="sBudNow"></div>
      <div class="hint">Amounts are estimates from the usage log (see 💲 Usage). A warning appears above the input box from 80%. Calls with no price set count as zero, so add prices for ChatGPT, Gemini and Grok in 💲 Usage → Prices.</div>
      <h3>Memory</h3>
      <div class="grid2">${field("Summarize when history exceeds", `<input id="sWhen" type="number" min="6" step="2" value="${config.summarize_when || 24}">`, "messages")}
        ${field("…keeping this many recent", `<input id="sRecent" type="number" min="2" step="2" value="${config.keep_recent || 10}">`, "messages, word for word")}<div></div></div>
      <div class="hint">Older turns are folded into a short summary the narrator always reads. A longer window keeps more detail verbatim but costs more tokens per turn and causes fewer cache rewrites. Edit the summary any time under 🧠 Memory.</div>
      <h3>Character memory</h3>
      <label class="chk top"><input type="checkbox" id="sCharMem" ${config.char_memory !== false ? "checked" : ""}><span><b>Keep each character's key events on their card, and send them only when they are in the scene</b><span class="hint">The narrator is reminded of the key past events of the characters named in the latest exchange (a short note, about 400 to 600 tokens, added to the newest message), so the shared memory summary above can stay short (about 1,200 words) and cover plot, places, items and open threads. Long event logs are condensed automatically by the helper AI (older events folded into a short history; your own entries are never touched). Needs <b>Key events log</b> on AI &amp; story to be on. Saves the most with models that do not cache the prompt; with Claude's caching the saving is smaller, since the summary is cached.</span></span></label>
      <div class="grid2">${field("How much of each character's history the narrator sees", '<select id="sCharMemSize"><option value="small">Small (cheapest)</option><option value="medium">Medium</option><option value="large">Large (most detail)</option></select>', "Applies to the characters named in the latest exchange. A smaller note costs less per turn (it is not cached) but older events of that character are left out.")}<div></div><div></div></div>
      <h3>Claude prompt caching</h3>
      <label class="chk top"><input type="checkbox" id="sCache" ${config.prompt_cache !== false ? "checked" : ""}><span><b>Cache the story prompt</b><span class="hint">Cached prompt text costs about 10% of normal. Strongly recommended for Claude; other providers cache automatically.</span></span></label>
      <div class="grid2">${field("Cache lifetime", '<select id="sTtl"><option value="5m">5 minutes</option><option value="1h">1 hour</option></select>', "Saves money only if your next turn comes within this time. 5 minutes is cheapest to write; 1 hour suits slower play.")}<div></div><div></div></div>
      <label class="chk top"><input type="checkbox" id="sWarm" ${config.keep_warm ? "checked" : ""}><span><b>Keep warm while you're away</b><span class="hint">Sends a tiny request just before the cache would expire so it stays cached (costs only the cheap cache-read price). Best with the 1-hour lifetime.</span></span></label>
      <div class="grid2">${field("Stop keep-warm after", `<input id="sWarmH" type="number" min="0.5" step="0.5" value="${config.keep_warm_hours ?? 2}">`, "hours without a turn")}<div></div><div></div></div>
    </div>


    <div class="stpane hidden" data-p="Appearance">
      ${appearanceHtml(config)}
    </div>

    <div class="stpane hidden" data-p="Local">
      <h3>What is running now</h3>
      <div class="aiBox" id="rtStatus"><span class="hint">Checking…</span></div>
      <div class="actions" style="justify-content:flex-start;margin-top:6px"><button id="rtRefresh">↻ Refresh</button><button id="rtFree" title="Unloads local text models and stops (or frees) ComfyUI right now">Free memory now</button></div>
      <h3>Text models (Ollama)</h3>
      <label class="chk top"><input type="checkbox" id="sLocal" ${config.local_mode ? "checked" : ""}><span><b>Local-model mode (steadier writing)</b><span class="hint">One switch for the app's own writing helpers, which cost no AI calls. With any model, online or local: removes the unfinished last sentence of a reply cut off by the length limit, closes unclosed italics, bold and picture tags, and tells the narrator which phrases it keeps repeating and when replies drift from the story's usual length. With Ollama models only (an online model gains nothing from them): keeps the prompt inside the model's window (dropping the oldest history if needed, and folding history into the summary sooner), forces clean JSON from the helper jobs, uses steadier sampling, runs helper jobs one at a time after the reply is written, fills the right-now card and scene-picture prompts without the AI (mood read from the words around a name when there is no portrait tag), and logs key events once every third reply. Turn it off to go back to normal.</span></span></label>
      <label class="chk top"><input type="checkbox" id="sRtText" ${config.rt_text ? "checked" : ""}><span><b>Manage local text models automatically</b><span class="hint">When you open a story (or change its AI), its local model is loaded and ready, and local models no story is using are unloaded to free video memory. Turn off to leave Ollama alone.</span></span></label>
      <div class="grid2">${field("Unload an idle model after", `<input id="sKeep" value="${esc(config.keep_alive || "30m")}">`, "e.g. 10m, 30m, 2h. Ollama frees the model itself after this much time without a turn; it reloads automatically at your next turn.")}<div></div><div></div></div>
      <h3>Picture generator (ComfyUI)</h3>
      <div class="hint">Its address and which checkpoint to use are under <b>🖼 Art → Picture makers</b> inside a story; this section only controls starting and stopping it.</div>
      <label class="chk top"><input type="checkbox" id="sRtComfy" ${config.rt_comfy ? "checked" : ""}><span><b>Start and stop ComfyUI automatically</b><span class="hint">ComfyUI starts when you open a story that draws pictures locally (or when a picture is asked for), and shuts down after it has been idle for the time below. Starting takes about 15 to 30 seconds.</span></span></label>
      <div class="grid2">${field("Idle time before it stops", `<input id="sRtIdle" type="number" min="1" step="1" value="${config.rt_comfy_idle_min ?? 15}">`, "minutes without drawing. Pictures are drawn in bursts, so a longer time than for text suits it.")}
        ${field("When idle", `<select id="sRtMode"><option value="stop">Stop ComfyUI completely</option><option value="free">Keep it running, free its video memory</option></select>`, "Stopping frees the most; keeping it running restarts faster.")}<div></div></div>
      ${field("How to start ComfyUI", `<input id="sRtCmd" value="${esc(config.rt_comfy_cmd || "")}" placeholder="~/ComfyUI/start.sh, or start.bat on Windows (default)">`, "A command or script path on this computer. Leave empty for the default.")}
      <label class="chk top"><input type="checkbox" id="sRtSwap" ${config.rt_swap !== false ? "checked" : ""}><span><b>Make room for pictures</b><span class="hint">If video memory is short when a local picture is about to be drawn, the local text model is unloaded first (it reloads at your next turn). Helpful on 12 GB cards that can't hold both.</span></span></label>
    </div>

    <div class="stpane hidden" data-p="Phone">
      <h3>Play from your phone or tablet</h3>
      <label class="chk top"><input type="checkbox" id="sLan" ${config.lan_access ? "checked" : ""}><span><b>Let devices on my home network open AI Storyteller</b><span class="hint">Your phone or tablet opens the app in its browser over your home Wi-Fi. Everything still runs on this computer: the phone only shows the pages and sends your moves here; it never connects to anything on the internet itself. Other devices must enter a PIN once (this computer never does), and anything that is not on your home network is refused. Takes effect after a restart.</span></span></label>
      <div id="lanBox" class="aiBox"><span class="hint">Loading…</span></div>
    </div>
    <div class="stpane hidden" data-p="Backup">
      <h3>Something went wrong?</h3>
      <div class="hint">Make a bug report file (how far your game got, the problems the app can find, recent errors) with personal data removed, to attach to a problem report.</div>
      <div class="actions" style="justify-content:flex-start"><button id="sBugReport">🐞 Report a bug…</button></div>
      <h3>Reuse across stories</h3>
      <div class="hint">Both are off by default and work independently.</div>
      <label class="chk top"><input type="checkbox" id="sAvLib" ${config.avatar_library ? "checked" : ""}><span><b>Avatar library</b><span class="hint">Create several player avatars (a sheet and a picture each) and reuse them in any story. Each story uses exactly one, as its own copy. Adds a 🧑 Avatars button on the Home screen and a library row in Player → Avatar.</span></span></label>
      <label class="chk top"><input type="checkbox" id="sReuse" ${config.reuse_art ? "checked" : ""}><span><b>Reuse character art across stories</b><span class="hint">Borrow a character's pictures from another story as a face reference (Cast → character → Images). Only identity carries over: each story's own art style and setting still apply, so the character looks like they belong to the new story.</span></span></label>
      <h3>Back up everything</h3>
      <div class="hint">One .zip with all your stories, character pictures, scene pictures, avatars, usage log and settings. Your per-story backups (🧠 → Backups) protect against bad edits; this protects against a lost or broken disk, so keep a copy on another drive.</div>
      <label class="chk top"><input type="checkbox" id="bkKeys"><span><b>Include API keys</b><span class="hint">Off by default. If you include them, keep the file private.</span></span></label>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button class="primary" id="bkDownload">Download a backup</button><button id="bkNow">Save one to the backup folder</button><span class="hint" id="bkNote"></span></div>
      <h3>Automatic backups</h3>
      <div class="grid2">${field("Back up every", `<input id="bkDays" type="number" min="0" step="1" value="${config.backup_auto_days || 0}">`, "days. 0 = off. Checked while the app is running.")}
        ${field("Keep the newest", `<input id="bkKeep" type="number" min="1" step="1" value="${config.backup_keep || 5}">`, "backups")}<div></div></div>
      ${field("Backup folder", `<input id="bkDir" value="${esc(config.backup_dir || "")}" placeholder="(default: the app's data/backups/full folder)">`, "A folder on this computer. Use another drive for real protection.")}
      <div id="bkList" class="hint">Looking…</div>
      <h3>Restore</h3>
      <div class="hint">Puts a backup's files back. A safety copy of your current data is saved first, files with the same names are replaced, nothing else is deleted, and your saved API keys are kept.</div>
      <div class="actions" style="justify-content:flex-start"><label class="btnlike">Restore from a backup file…<input type="file" id="bkFile" accept=".zip,application/zip" hidden></label><span class="hint" id="bkRestoreNote"></span></div>
    </div>

    <div class="actions sticky"><button id="mClose">Cancel</button><button class="primary" id="mSave">Save</button></div>`);
  $("modalCard").classList.add("tall");   // same window size on every tab
  const showTab = (t) => { document.querySelectorAll(".stt").forEach((b) => b.classList.toggle("on", b.dataset.t === t)); document.querySelectorAll(".stpane").forEach((p) => p.classList.toggle("hidden", p.dataset.p !== t)); };
  document.querySelectorAll(".stt").forEach((b) => b.onclick = () => showTab(b.dataset.t));
  showTab(tab);
  document.querySelectorAll(".prov").forEach((el, i) => el.querySelector('[data-f="type"]').value = config.providers[i].type);
  $("sTtl").value = config.cache_ttl || "5m";
  document.querySelectorAll(".stpane").forEach((p) => collapsify(p));   // each section can be minimised; the first of each tab starts open
  $("sUsage").onclick = () => showUsage();
  $("sbDefault").value = config.sidebar_default_mode || "prompt";
  if (story) {
    const SBH = { prompt: "Shows whatever the story prompt's own status panel or state block provides, and nothing extra.", player: "Shows only the fields you tick. Read from the story's status panel when it writes one; otherwise the AI reads each reply (one small background call).", ai: "The AI reads your story prompt, decides what is worth tracking, and updates it after each reply (one small background call)." };
    const sb = story.sidebar || { mode: "prompt", fields: [] }, groups = [["scene", "Scene"], ["me", "You"], ["chars", "Characters"]];
    $("sbMode").value = sb.mode;
    const draw = () => {
      const m = $("sbMode").value; $("sbHint").textContent = SBH[m];
      const own = m === "player" || (m === "prompt" && sbTrk && sbTrk.get().length);   // a story that has its own trackers keeps showing them in "prompt" mode
      if (m === "prompt" && own) $("sbHint").textContent += " This story has its own trackers, so they still show and the AI keeps them updated.";
      $("sbFields").classList.toggle("hidden", !own); $("sbCustom").classList.toggle("hidden", !own); $("sbAi").classList.toggle("hidden", m !== "ai");
    };
    $("sbFields").innerHTML = groups.map(([g, t]) => `<div class="sbgroup"><b>${t}</b>${TRACKER_FIELDS.filter((f) => f.id.startsWith(g + ".")).map((f) => `<label class="chk"><input type="checkbox" data-f="${f.id}" ${(sb.fields || []).includes(f.id) ? "checked" : ""}> ${esc(f.label)}</label>`).join("")}</div>`).join("");
    const aiList = () => `${(sb.schema || []).length ? '<div class="hint">The AI decided to track:</div><ul class="sbl">' + sb.schema.map((f) => `<li><b>${esc(f.label)}</b> <small>${esc(f.id)} · ${esc(f.kind)}${f.options ? " · " + esc(f.options.join("/")) : ""}${f.kind === "slider" ? ` · ${f.min}–${f.max}` : ""}</small></li>`).join("") + "</ul>" : '<div class="hint">The AI will decide when you save.</div>'}<div class="actions" style="justify-content:flex-start"><button id="sbRedo">Let the AI decide again</button></div>`;
    $("sbAi").innerHTML = aiList();
    if ($("sbRedo")) $("sbRedo").onclick = async (e) => { e.target.disabled = true; $("sbNote").textContent = "Reading your story prompt…"; try { story = await api(`/stories/${story.id}/sidebar/schema`, { method: "POST" }); sb.schema = story.sidebar.schema; $("sbMode").value = "ai"; $("sbAi").innerHTML = aiList(); $("sbRedo").onclick = null; $("sbNote").textContent = "Decided."; renderSidebar(); } catch (err) { $("sbNote").textContent = err.message; } e.target.disabled = false; };
    sbTrk = trackerEditor($("sbCustom"), sb.custom || [], () => ({ storyId: story.id }));
    $("sbMode").onchange = draw; draw();
    $("sbBuild").onclick = async (e) => { e.target.disabled = true; $("sbNote").textContent = "Building…"; try { story = await api(`/stories/${story.id}/sidebar/build?fresh=true`, { method: "POST" }); renderSidebar(); $("sbNote").textContent = "Done."; } catch (err) { $("sbNote").textContent = err.message; } e.target.disabled = false; };
  }
  const themeDraft = bindAppearance(config);
  const lanFill = async () => {
    const box = $("lanBox"); if (!box) return;
    let l; try { l = await api("/lan"); } catch (e) { box.innerHTML = `<span class="hint">${esc(e.message)}</span>`; return; }
    const want = $("sLan").checked;
    box.innerHTML = (l.active ? `<b>✔ Open on your phone:</b> ${l.urls.map((u) => `<code>${esc(u)}</code>`).join(" or ") || "(this computer's network address could not be read)"}<br><b>PIN:</b> <code>${esc(l.pin)}</code> <button id="lanPin">New PIN</button>`
        : want ? `<b>Restart the app to turn it on.</b> <button id="lanRestart">Save and restart now</button>` : `<span class="hint">Off: only this computer can open the app.</span>`)
      + (l.active && !want ? `<div class="hint">Turned off: save and restart to stop phones from connecting. <button id="lanRestart">Save and restart now</button></div>` : "")
      + `<div class="hint" style="margin-top:6px">If the phone cannot connect, this computer's firewall may be blocking it. Allow only your home network, once, in a terminal: <code>sudo ufw allow from ${esc(((l.urls[0] || "http://192.168.0.1").split("//")[1] || "").split(":")[0].replace(/\.\d+$/, ".0"))}/24 to any port ${l.port} proto tcp</code></div>`;
    if (l.active || l.pin) {      // devices that entered the PIN are remembered, also after a restart
      let devs = []; try { devs = await api("/lan/devices"); } catch (e) {}
      box.insertAdjacentHTML("beforeend", `<div class="hint" style="margin-top:8px"><b>Remembered devices</b> (they enter the PIN once, never again, not even after a restart):</div>` + (devs.length
        ? devs.map((d) => `<div class="optrow"><span style="flex:1">${esc(d.name)} <small class="hint">· ${esc(d.ip)} · last used ${new Date(d.seen * 1000).toLocaleDateString()}</small></span><button data-forget="${esc(d.id)}">Forget</button></div>`).join("")
        : `<div class="hint">None yet.</div>`));
      box.querySelectorAll("[data-forget]").forEach((b) => b.onclick = async () => { await api("/lan/devices/" + b.dataset.forget, { method: "DELETE" }); lanFill(); });
    }
    if ($("lanPin")) $("lanPin").onclick = async () => { if (!confirm("Make a new PIN? Every remembered device is forgotten and has to enter the new PIN.")) return; await api("/lan/new-pin", { method: "POST" }); lanFill(); };
    if ($("lanRestart")) $("lanRestart").onclick = async () => { $("mSave").click(); setTimeout(() => restartApp(), 1500); };
  };
  $("sLan").onchange = lanFill; lanFill();
  $("sBugReport").onclick = () => showBugReport();
  onModalClose = () => { applyTheme(config); onModalClose = null; };   // Cancel / click-away undoes the colour preview
  $("sBudAct").value = config.budget_action || "warn"; $("sCharMemSize").value = config.char_memory_size || "medium";
  api("/budget").then((b) => { $("sBudNow").textContent = `Spent so far: $${b.daily.spent.toFixed(2)} today` + (b.monthly.limit > 0 ? ` · $${b.monthly.spent.toFixed(2)} this month` : "") + " (estimated)."; }).catch(() => {});
  const fmtSize = (n) => (n > 1e9 ? (n / 1e9).toFixed(2) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  const bkRefresh = () => api("/backup/status").then((r) => { $("bkList").innerHTML = r.backups.length ? `Saved in <code>${esc(r.dir)}</code>:<br>` + r.backups.slice(0, 6).map((b) => `${new Date(b.when * 1000).toLocaleString()} · ${fmtSize(b.bytes)} · ${esc(b.name)}`).join("<br>") : `No backups in <code>${esc(r.dir)}</code> yet.`; }).catch(() => {});
  bkRefresh();
  $("bkDownload").onclick = () => { $("bkNote").textContent = "Preparing the file… large collections can take a minute; the download starts when it is ready."; window.location = `/api/backup/download?keys=${$("bkKeys").checked}`; };
  $("bkNow").onclick = async (e) => { e.target.disabled = true; $("bkNote").textContent = "Saving…"; try { const r = await api(`/backup/now?keys=${$("bkKeys").checked}`, { method: "POST" }); $("bkNote").textContent = `Saved ${r.files} files (${fmtSize(r.bytes)}).`; bkRefresh(); } catch (err) { $("bkNote").textContent = err.message; } e.target.disabled = false; };
  $("bkFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (!confirm(`Restore from “${f.name}”? A safety copy of your current data is saved first.`)) { e.target.value = ""; return; }
    $("bkRestoreNote").textContent = "Restoring… (a safety copy is saved first)";
    try {
      const r = await fetch("/api/backup/restore", { method: "POST", headers: { "content-type": "application/zip" }, body: f });
      const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || "Restore failed");
      $("bkRestoreNote").textContent = `Restored ${j.files} files (${j.stories} stories). Reloading…`; setTimeout(() => location.reload(), 1500);
    } catch (err) { $("bkRestoreNote").textContent = err.message; }
  };
  $("sRtMode").value = config.rt_comfy_mode || "stop";
  const rtRefresh = async () => {
    try {
      const r = await api("/runtime"), loaded = r.ollama.loaded.map((m) => `${esc(m.name)} (${m.size_gb} GB)`).join(", ") || "nothing loaded";
      $("rtStatus").innerHTML = `<div>Text (Ollama): <b>${loaded}</b></div><div>Pictures (ComfyUI): <b>${r.comfy.up ? "running" : "not running"}</b>${r.comfy.stops_in_s != null ? ` <small>(stops in about ${Math.ceil(r.comfy.stops_in_s / 60)} min if unused)</small>` : ""}</div>${r.vram.total_gb ? `<div>Video memory: <b>${r.vram.used_gb} / ${r.vram.total_gb} GB</b> used</div>` : ""}`;
    } catch (e) { $("rtStatus").textContent = "Could not read the status."; }
  };
  $("rtRefresh").onclick = rtRefresh; rtRefresh();
  $("rtFree").onclick = async (e) => { e.target.disabled = true; await api("/runtime/free", { method: "POST" }); await rtRefresh(); e.target.disabled = false; };
  let styleBox = null;
  const picker = mountAIChooser($("aiPick"), { initial: currentChoices(), roles: ["story", "recaps", "small"] });
  const imgPicker = mountAIChooser($("aiPickImg"), { initial: currentChoices(), roles: ["images"] });   // hidden: only tells the art-style list which picture maker is chosen (changed in 🖼 Art → Picture makers)
  styleBox = mountArtStyle($("artStyleBox"), imgPicker, story ? (story.art || {}).direction || "" : config.default_art_direction || "");
  imgPicker.ready.then(() => styleBox.render());
  $("sThink").value = config.claude_thinking ? "on" : "off";
  $("sEffort").value = config.claude_effort || "low";
  $("sEvents").value = config.events_mode || "every";
  const collect = () => { // read the form back into config.providers
    document.querySelectorAll(".prov").forEach((el) => {
      const p = config.providers[+el.dataset.i], g = (f) => el.querySelector(`[data-f="${f}"]`).value;
      p.name = g("name"); p.type = g("type"); p.base_url = g("base_url"); p.api_key = g("api_key");
      p.models = g("models").split(",").map((x) => x.trim()).filter(Boolean);
    });
  };
  $("mAdd").onclick = () => { collect(); config.providers.push({ id: "p" + Date.now().toString(36), type: "openai", name: "New provider", base_url: "https://openrouter.ai/api/v1", api_key: "", models: [] }); showSettingsRender(); };
  function showSettingsRender() { /* re-render without refetching so unsaved edits stay */
    $("provs").innerHTML = config.providers.map(provHtml).join("");
    document.querySelectorAll(".prov").forEach((el, i) => { el.querySelector('[data-f="type"]').value = config.providers[i].type; });
    bindRemove();
  }
  const bindRemove = () => document.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => { collect(); config.providers.splice(+b.dataset.rm, 1); showSettingsRender(); });
  bindRemove();
  $("mClose").onclick = closeModal;
  $("mSave").onclick = async () => {
    collect();
    config.temperature = +$("sTemp").value; config.max_tokens = +$("sMax").value; config.num_ctx = +$("sCtx").value;
    config.char_memory = $("sCharMem").checked; config.char_memory_size = $("sCharMemSize").value; config.prompt_cache = $("sCache").checked; config.cache_ttl = $("sTtl").value; config.keep_alive = $("sKeep").value.trim() || "30m"; config.claude_thinking = $("sThink").value === "on"; config.claude_effort = $("sEffort").value; config.events_mode = $("sEvents").value; config.thinking_headroom = +$("sHead").value || 0;
    Object.assign(config, themeDraft);
    config.budget_daily = +$("sBudD").value || 0; config.budget_monthly = +$("sBudM").value || 0; config.budget_action = $("sBudAct").value;
    config.backup_auto_days = +$("bkDays").value || 0; config.backup_keep = +$("bkKeep").value || 5; config.backup_dir = $("bkDir").value.trim();
    config.sidebar_default_mode = $("sbDefault").value;
    const styleDir = styleBox.get(), styleChanged = styleDir !== (story ? (story.art || {}).direction || "" : config.default_art_direction || "");
    if (styleChanged || styleDir) config.default_art_direction = styleDir;   // new stories start with it
    config.local_mode = $("sLocal").checked; config.rt_text = $("sRtText").checked; config.rt_comfy = $("sRtComfy").checked; config.rt_comfy_idle_min = +$("sRtIdle").value || 15; config.rt_comfy_mode = $("sRtMode").value; config.rt_comfy_cmd = $("sRtCmd").value.trim(); config.rt_swap = $("sRtSwap").checked;
    config.avatar_library = $("sAvLib").checked; config.reuse_art = $("sReuse").checked;
    config.lan_access = $("sLan").checked;
    config.keep_warm = $("sWarm").checked; config.keep_warm_hours = +$("sWarmH").value || 2;
    config.summarize_when = +$("sWhen").value || 24; config.keep_recent = +$("sRecent").value || 10;
    await picker.ready;
    const chosen = picker.get();
    if (chosen.recaps) config.events_model = `${chosen.recaps.provider}|${chosen.recaps.model}`;
    config = await api("/config", { method: "PUT", body: config });
    await api("/default-models", { method: "PUT", body: { ...(config.default_models || {}), ...chosen } });
    if (story) story.models = await api(`/stories/${story.id}/models`, { method: "PUT", body: chosen });
    if (story && $("sbMode")) {
      const mode = $("sbMode").value, fields = [...document.querySelectorAll("#sbFields input:checked")].map((i) => i.dataset.f), old = story.sidebar || {};
      const custom = sbTrk ? sbTrk.get() : (old.custom || []), strip = (l) => JSON.stringify((l || []).map((f) => [f.label, f.scope || f.id.split(".")[0], f.kind, f.min ?? null, f.max ?? null, String(Array.isArray(f.options) ? f.options.join(",") : f.options || ""), f.unit || "", f.note || "", String(Array.isArray(f.levels) ? f.levels.join(",") : f.levels || "").replace(/\s+/g, ""), String(f.max_level ?? ""), String(((l || []).find((h) => h.id && h.id === f.counts) || {}).label || f.counts || "").toLowerCase(), (Array.isArray(f.spend_on) ? f.spend_on.map((x) => ((l || []).find((h) => h.id === x) || {}).label || x).join(",") : String(f.spend_on || "")).toLowerCase().replace(/\s+/g, ""), String(f.spend_cap ?? "")]));
      if (mode !== old.mode || JSON.stringify(fields) !== JSON.stringify(old.fields || []) || strip(custom) !== strip(old.custom)) story = await api(`/stories/${story.id}/sidebar`, { method: "PUT", body: { mode, fields, custom } });
    }
    if (story && styleChanged) story = await api(`/stories/${story.id}/art`, { method: "PUT", body: { ...story.art, direction: styleDir, setting: (story.art || {}).setting ?? null } });
    if (story && $("sNsfw")) { const on = $("sNsfw").checked; if (on !== !!story.nsfw) story = await api(`/stories/${story.id}/nsfw?on=${on}`, { method: "PUT" }); }
    closeModal(); await loadModels(); renderAll(); runtimeSelect(); refreshBudget();
    if (!$("home").classList.contains("hidden")) showHome();
  };
}

// ---------- boot ----------
async function loadModels() {
  config = await api("/config");
  if (!TRACKER_FIELDS.length) TRACKER_FIELDS = await api("/tracker-fields");
  applyTheme(config);
  models = await api("/models");
  const sel = $("modelSelect"), saved = localStorage.getItem("model") || `${config.active.provider}|${config.active.model}`;
  sel.innerHTML = "";
  const groups = {};
  for (const m of models) (groups[m.provider_name] ||= []).push(m);
  for (const [name, ms] of Object.entries(groups)) {
    const g = document.createElement("optgroup"); g.label = name;
    for (const m of ms) { const o = document.createElement("option"); o.value = `${m.provider}|${m.model}`; o.textContent = m.model; g.appendChild(o); }
    sel.appendChild(g);
  }
  if ([...sel.options].some((o) => o.value === saved)) sel.value = saved;
  syncModelSelect();
  if (!sel.options.length) sel.innerHTML = '<option value="|">(no models — open Settings)</option>';
}
function syncModelSelect() { // the header menu shows this story's narrator model
  const c = story && story.models && story.models.story, sel = $("modelSelect");
  if (!c) return;
  const v = `${c.provider}|${c.model}`;
  if (![...sel.options].some((o) => o.value === v)) { const o = document.createElement("option"); o.value = v; o.textContent = c.model; sel.appendChild(o); }
  sel.value = v;
}
async function openStory(id) {
  story = await api("/stories/" + id);
  localStorage.setItem("story", id);
  syncModelSelect(); hideHome(); renderAll(false); applyTheme(config); refreshCache(); refreshArtSuggestions(); runtimeSelect(); refreshBudget();
  api(`/stories/${id}/beats/ensure`, { method: "POST" }).catch(() => {});
  startupAsks(id);         // a new game-like story (stats and levels): ask once how dice should work      // the first time: read the story prompt for scheduled events (background)
}
async function startupAsks(id) {   // what a story asks when it is opened, in this order: set-up first (who you are, art, adult content), then stat points, then dice
  const same = () => story && story.id === id;
  if (typeof maybeStorySetup === "function") { await new Promise((ok) => setTimeout(ok, 300)); if (!same()) return; await maybeStorySetup(); await new Promise((ok) => setTimeout(ok, 300)); if (!same()) return; }
  await maybeAskPoints(); if (!same()) return;
  setTimeout(() => { if (same()) maybeAskDice(); }, 800);
}
function runtimeSelect() {   // local-model auto-management (Settings → Local models): load what this story needs, release the rest
  if (story && config && (config.rt_text || config.rt_comfy)) api("/runtime/select", { method: "POST", body: { story_id: story.id } }).catch(() => {});
}
function autosize() { const t = $("input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, 160) + "px"; }

$("narrative").addEventListener("click", (e) => { const b = e.target.closest(".tbar button"); if (b) turnAction(b.dataset.act, +b.closest(".turn").dataset.i); });
$("btnSend").onclick = () => send($("input").value);
$("input").addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send($("input").value); } });
$("input").addEventListener("input", autosize);
$("btnUndo").onclick = async () => { const t = await undo(); if (t) { $("input").value = t; autosize(); } };
$("btnRedo").onclick = async () => {
  if (busy || !story || !story.undone) return;
  try { story = await api(`/stories/${story.id}/redo`, { method: "POST" }); $("input").value = ""; autosize(); renderAll(); } catch (e) { alert(e.message); }
};
const KEEP_HTML = `<details style="margin-top:6px"><summary class="hint">Keep something from the discarded reply? (by default the retry starts from the previous turn only)</summary>
    <label class="chk"><input type="checkbox" id="kpChars"> New characters it introduced</label>
    <label class="chk"><input type="checkbox" id="kpEvents"> What it logged on character cards</label>
    <label class="chk"><input type="checkbox" id="kpState"> Its side-panel and status changes</label></details>`;
function restoreKeepTicks() {   // the Retry dialog remembers your last "keep" choices
  let k = {}; try { k = JSON.parse(localStorage.getItem("retryKeep") || "{}"); } catch (e) { /* none saved */ }
  for (const [id, key] of [["kpChars", "keep_chars"], ["kpEvents", "keep_events"], ["kpState", "keep_state"]]) if ($(id)) $(id).checked = !!k[key];
  if (Object.values(k).some(Boolean) && $("kpChars")) $("kpChars").closest("details").open = true;
}
async function retryTurn(model, note) {   // steps back, keeping the discarded reply as an attempt, and asks again
  const keep = { keep_chars: !!($("kpChars") && $("kpChars").checked), keep_events: !!($("kpEvents") && $("kpEvents").checked), keep_state: !!($("kpState") && $("kpState").checked) };
  if ($("kpChars")) { try { localStorage.setItem("retryKeep", JSON.stringify(keep)); } catch (e) { /* not saved */ } }
  const had = hadPicture(), text = [...story.turns].reverse().find((x) => x.role === "user");
  if (!text) return;
  closeModal();
  story = await api(`/stories/${story.id}/retry`, { method: "POST", body: keep }); renderAll();
  send(text.text, model, false, note, !!note || had, text.cmds || [], text.cmd_opts || {}, text.roll || null);
}
$("btnRetry").onclick = () => {
  if (busy || !story || !story.turns.length) return;
  modal(`<h2>Regenerate this reply</h2><p class="hint">Your move is sent again and the current reply is discarded (it is kept as an earlier attempt you can swap back to). Optionally say what should change; it is sent along with the request and is not saved in the story. If you describe a change, or the reply had a picture, the scene is drawn again from the new reply.</p>
    <textarea id="rgNote" rows="3" placeholder="e.g. Shorter, more tension, keep the guard calmer… (leave empty to just try again)" autofocus></textarea>${KEEP_HTML}
    <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="rgGo">Regenerate</button></div>`);
  $("mClose").onclick = closeModal;
  restoreKeepTicks();
  $("rgGo").onclick = () => retryTurn(undefined, $("rgNote").value.trim());
};
$("btnHome").onclick = showHome;
bindTools();
// side panel on a phone: a small tab on the right edge (or a swipe from the edge) slides it over the story; tap the tab, the story or swipe back to close
{ const sb = $("sidebar"), tab = $("sideTab");
  const setSide = on => { sb.classList.toggle("open", on); document.body.classList.toggle("sideopen", on); tab.textContent = on ? "›" : "‹"; tab.title = on ? "Close the status panel" : "Open the status panel"; };
  $("btnSide").onclick = () => setSide(!sb.classList.contains("open"));
  tab.onclick = () => setSide(!sb.classList.contains("open"));
  document.addEventListener("click", e => { if (sb.classList.contains("open") && e.target.isConnected && !e.target.closest("#sidebar, #sideTab, #btnSide, .modal, #modal")) setSide(false); });   // a tap on a panel tab redraws the panel, so its target is already detached: that is not a tap outside
  let sx = 0, sy = 0, st = 0;
  document.addEventListener("touchstart", e => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; st = Date.now(); }, { passive: true });
  document.addEventListener("touchend", e => {
    if (innerWidth > 760 || Date.now() - st > 700) return;
    const t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.6) return;
    const open = sb.classList.contains("open");
    if (!open && dx < 0 && sx > innerWidth - 40) setSide(true);        // swipe left from the right edge
    else if (open && dx > 0) setSide(false);                            // swipe right to put it away
  }, { passive: true }); }
// phone: the header buttons fold into a ☰ drop-down so they stay out of the way while reading
{ const tools = $("tools"), menu = $("btnMenu");
  const setMenu = on => { tools.classList.toggle("open", on); menu.setAttribute("aria-expanded", on ? "true" : "false"); menu.textContent = on ? "✕" : "☰"; };
  menu.onclick = () => setMenu(!tools.classList.contains("open"));
  tools.addEventListener("click", e => { if (e.target.closest("button")) setMenu(false); });
  document.addEventListener("click", e => { if (tools.classList.contains("open") && !e.target.closest("#tools, #btnMenu")) setMenu(false); }); }
$("btnEdit").onclick = () => story ? editStory(story) : editStory(null);
$("btnSettings").onclick = () => showSettings();
$("btnPlayer").onclick = showPlayer;
$("btnMemory").onclick = showMemory;
$("btnEndings").onclick = showEndings;
$("btnCast").onclick = () => showCast();
$("btnArt").onclick = () => showArtHub();
$("btnIllustrate").onclick = () => illustrate();
$("btnReplies").onclick = () => fetchReplies(true);
$("btnBeats").onclick = () => showBeats();
$("btnContinue").onclick = () => { if (busy || !story || !story.turns.length) return; sendCommand("continue"); };
$("narrative").addEventListener("scroll", () => { const at = narrativeAtBottom(); if (at && !wasAtBottom) maybeShowReplies(); wasAtBottom = at; });
$("modelSelect").onchange = async (e) => {
  localStorage.setItem("model", e.target.value);
  if (!story) return;
  const [provider, model] = e.target.value.split("|");
  story.models = await api(`/stories/${story.id}/models`, { method: "PUT", body: { story: { provider, model } } });
  runtimeSelect();
};

(async function init() {
  try {
    await loadModels();
    const list = await api("/stories");
    const last = localStorage.getItem("story");
    const id = list.find((s) => s.id === last) ? last : list[0] && list[0].id;
    if (id) await openStory(id); else renderAll(false);
    await showHome();   // start on the Home screen; the last story is already loaded behind it
  } finally { document.body.classList.remove("booting"); }   // the story screen stays hidden until Home is up, so it never flashes first
  setTimeout(() => { if (typeof maybeFirstRun === "function") maybeFirstRun(); }, 300);
})();
