// Per-turn tools (bookmark, edit, branch, retry with another model, continue, read aloud), search, quick instructions, voices.
function turnBar(t, i, lastAi) {
  const b = (act, title, label) => `<button data-act="${act}" title="${title}">${label}</button>`;
  return `<span class="tbar">${t.role === "assistant" ? b("read", "Read aloud", "🔊") : ""}${b("bm", "Bookmark this turn", t.bookmark ? "★" : "☆")}${b("edit", "Edit this message", "✏️")}` +
    `${t.role === "user" ? b("branch", "Branch: start a copy of this story from here to try a different move", "🔀") : ""}` +
    `${i === lastAi && (t.attempts || []).length ? b("prev", "Show the previous attempt", "◀") + `<span class="tcount" title="Attempt ${t.attempt_no || "?"} of ${t.attempts.length + 1}">${t.attempt_no || "?"}/${t.attempts.length + 1}</span>` + b("next", "Show the next attempt", "▶") + b("tries", "All attempts at this reply: compare and pick one", "🗂️") : ""}${i === lastAi ? b("alt", "Retry this reply with a different AI", "🔄▾") + b("cont", "Continue this reply (if it was cut off)", "⏩") : ""}</span>`;
}

async function turnAction(act, i) {
  if (!story || busy) return;
  const t = story.turns[i];
  if (act === "read") return speakText(t.text);
  if (act === "bm") {
    const label = t.bookmark ? "" : prompt("Bookmark label (shown above the turn):", (t.text.replace(/[*_#>`]/g, "").trim().slice(0, 40)));
    if (!t.bookmark && label === null) return;
    story.turns = (await api(`/stories/${story.id}/turns/${i}/bookmark`, { method: "PUT", body: { label: label || "" } })).turns; return renderNarrativeKeepScroll();
  }
  if (act === "edit") {
    modal(`<h2>Edit ${t.role === "user" ? "your move" : "the narrator's reply"}</h2><textarea id="eTxt" style="min-height:300px">${esc(t.text)}</textarea>
      <p class="hint">The change is used from the next turn on. If this turn is already in the memory summary, edit the summary too (🧠).</p>
      ${(t.versions || []).length ? `<details><summary class="hint">Earlier versions (${t.versions.length})</summary>${t.versions.map((v, k) => `<div class="aiBox"><small class="muted">${new Date(v.ts * 1000).toLocaleString()}</small><div class="hint">${esc(v.text.slice(0, 300))}${v.text.length > 300 ? "…" : ""}</div><button data-ver="${k}">Put this version in the box</button></div>`).join("")}</details>` : ""}
      <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="eSave">Save</button></div>`);
    $("mClose").onclick = closeModal;
    document.querySelectorAll("#modalCard [data-ver]").forEach((bt) => bt.onclick = () => { $("eTxt").value = t.versions[+bt.dataset.ver].text; });
    $("eSave").onclick = async () => { story = await api(`/stories/${story.id}/turns/${i}`, { method: "PUT", body: { text: $("eTxt").value } }); closeModal(); renderAll(); };
    return;
  }
  if (act === "branch") {
    const title = prompt("Name for the new branch:", `${story.title} (branch)`); if (!title) return;
    try {
      const r = await api(`/stories/${story.id}/branch`, { method: "POST", body: { upto: i, title } });
      story = r.story; localStorage.setItem("story", story.id); renderAll(); $("input").value = r.text; autosize(); $("input").focus();
    } catch (e) { alert(e.message); }
    return;
  }
  if (act === "alt") {
    const cur = (t.model || "").replace("/", "|");
    modal(`<h2>Retry this reply with a different AI</h2><p class="hint">Your move is sent again; the current reply is discarded. Last written by ${esc(t.model || "?")}.</p>
      <select id="aModel" style="width:100%">${models.map((m) => `<option value="${esc(m.provider)}|${esc(m.model)}" ${cur === m.provider + "|" + m.model ? "selected" : ""}>${esc(m.provider_name)}: ${esc(m.model)}</option>`).join("")}</select>
      <textarea id="aNote" rows="2" style="margin-top:8px" placeholder="Optional: what should change? (sent along with the retry, not saved)"></textarea>
      ${KEEP_HTML}
      <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="aGo">Retry</button></div>`);
    $("mClose").onclick = closeModal;
    restoreKeepTicks();
    $("aGo").onclick = () => retryTurn($("aModel").value, $("aNote").value.trim());
    return;
  }
  if (act === "prev" || act === "next") {
    try { story = await api(`/stories/${story.id}/turns/${i}/attempt-step?dir=${act === "next" ? 1 : -1}`, { method: "POST" }); renderNarrativeKeepScroll(); renderSidebar(); } catch (e) { alert(e.message); }
    return;
  }
  if (act === "tries") {
    const short = (x) => esc((x.text || "").replace(/\[\[[^\]]*\]\]|[*_#>`]/g, " ").replace(/\s+/g, " ").trim().slice(0, 360)) + "…";
    modal(`<h2>Attempts at this reply</h2><p class="hint">All earlier attempts are saved on this computer. Pick one to swap it in; the current reply is kept as an attempt. Side-panel changes, new characters and card events follow the reply you pick.</p>
      <div class="aiBox"><b>Current</b> <small>${esc(t.model || "")}</small><div class="hint">${short(t)}</div></div>
      ${t.attempts.map((x, k) => `<div class="aiBox"><b>Attempt ${k + 1}</b> <small>${esc(x.model || "")}</small><div class="hint">${short(x)}</div><button data-use="${k}">Use this one</button></div>`).join("")}
      <div class="actions"><button id="mClose">Close</button></div>`);
    $("mClose").onclick = closeModal;
    document.querySelectorAll("#modalCard [data-use]").forEach((bt) => bt.onclick = async () => {
      try { story = await api(`/stories/${story.id}/turns/${i}/attempt/${bt.dataset.use}`, { method: "POST" }); closeModal(); renderAll(); } catch (e) { alert(e.message); }
    });
    return;
  }
  if (act === "cont") return send("(OOC: Your last reply was cut off or unfinished. Continue it exactly where it stopped, without repeating anything and without moving the story past that moment.)");
}
function renderNarrativeKeepScroll() { const box = $("narrative"), y = box.scrollTop; renderNarrative(); box.scrollTop = y; }

// ---------- search & bookmarks ----------
function showSearch(initialTab = "Search") {
  if (!story) return;
  modal(`<h2>Search</h2><div class="tabs"><button class="tab stab" data-t="Search">Search</button><button class="tab stab" data-t="Bookmarks">Bookmarks (${story.turns.filter((t) => t.bookmark).length})</button></div>
    <div id="sSearch"><input id="sQ" placeholder="Find text in this story (names, places, words)…" autofocus><div id="sRes"></div></div>
    <div id="sMarks" class="hidden"></div><div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  const go = (i) => { closeModal(); const el = document.querySelector(`.turn[data-i="${i}"]`); if (el) { el.scrollIntoView({ block: "center" }); el.classList.add("flash"); setTimeout(() => el.classList.remove("flash"), 1800); } };
  const row = (t, i, q) => {
    const plain = t.text.replace(/[*_#>`]/g, " ").replace(/\s+/g, " "), k = q ? plain.toLowerCase().indexOf(q.toLowerCase()) : 0;
    const s = Math.max(0, k - 50), snip = esc(plain.slice(s, s + 140)), hl = q ? snip.replace(new RegExp(esc(q).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "ig"), (m) => `<mark>${m}</mark>`) : snip;
    return `<div class="hit" data-i="${i}"><small>${t.bookmark ? "★ " + esc(t.bookmark) + " · " : ""}Turn ${Math.floor(i / 2) + 1} · ${t.role === "user" ? "you" : "narrator"}</small><br>${s ? "…" : ""}${hl}…</div>`;
  };
  const bind = (el) => el.querySelectorAll(".hit").forEach((h) => h.onclick = () => go(+h.dataset.i));
  $("sQ").oninput = (e) => {
    const q = e.target.value.trim(); if (q.length < 2) { $("sRes").innerHTML = ""; return; }
    const hits = story.turns.map((t, i) => [t, i]).filter(([t]) => t.text.toLowerCase().includes(q.toLowerCase()));
    $("sRes").innerHTML = (hits.slice(0, 60).map(([t, i]) => row(t, i, q)).join("") || '<p class="hint">No matches in the turns.</p>') + (story.summary && story.summary.toLowerCase().includes(q.toLowerCase()) ? '<p class="hint">Also found in the memory summary (🧠).</p>' : "");
    bind($("sRes"));
  };
  $("sMarks").innerHTML = story.turns.map((t, i) => [t, i]).filter(([t]) => t.bookmark).map(([t, i]) => row(t, i, "")).join("") || '<p class="hint">No bookmarks yet. Hover a turn and press ☆.</p>';
  bind($("sMarks"));
  const show = (n) => { document.querySelectorAll(".stab").forEach((b) => b.classList.toggle("on", b.dataset.t === n)); $("sSearch").classList.toggle("hidden", n !== "Search"); $("sMarks").classList.toggle("hidden", n !== "Bookmarks"); };
  document.querySelectorAll(".stab").forEach((b) => b.onclick = () => show(b.dataset.t));
  show(initialTab); $("sQ").focus();
}

// ---------- quick instructions ----------
const QUICK = [["Shorter", "keep the next reply shorter"], ["More detail", "describe the scene in richer detail"], ["More dialogue", "use more dialogue and less narration"], ["Slow down", "slow the pacing; stay in this moment"],
  ["Skip ahead", "skip ahead to the next significant moment"], ["Don't decide for me", "do not describe my character's actions, words or feelings"], ["Lighter tone", "lighten the tone a little"], ["Stay in character", "keep every character true to their established personality"]];
function renderQuick() {
  const act = (() => { try { return localStorage.getItem("fightAct") === "1"; } catch (e) { return false; } })();
  const adult = !!(story && story.nsfw);
  $("quickBar").innerHTML = `<div class="qcmds"><div class="qrow">
      <button data-cmd="continue" title="The narrator carries the story on by itself from the prompt, characters, world lore and the last few turns">▶️ Continue</button>
      <button data-cmd="dice" title="Roll a d20 check against one of your stats and send your move with the result (the narrator must narrate the outcome)">🎲 Check…</button>
      <button data-cmd="fight" title="Continue the ongoing battle with dynamic, creative actions: enemies attack, dodge, counter and adapt">⚔️ Continue the fight</button>
      <label class="chk qact" title="Off: your character is left to you. On: the narrator may also choose fitting actions for your character in the fight."><input type="checkbox" id="fightAct" ${act ? "checked" : ""}> narrator may act for me</label></div>
      ${adult ? `<div class="qrow"><button data-cmd="adult" class="${armed ? "armed" : ""}" title="A fuller adult-content instruction for narrator models that fade to black or skip intimate scenes. Press to attach it to your next move (press again to take it off).">🔞 Full adult scene</button>
      <label class="chk qact" title="Keep it attached to every move until you turn it off"><input type="checkbox" id="adultKeep" ${cmdKeep() ? "checked" : ""}> every move</label></div>` : ""}
    </div><div id="qDice" class="qdice hidden"></div><div class="qmoves" id="qMoves"></div><div class="qnotes">` + QUICK.map(([l, v]) => `<button data-v="${esc(v)}" title="Adds an OOC note to your message">${l}</button>`).join("") + "</div>";
  const bar = $("quickBar");
  bar.querySelectorAll("button[data-v]").forEach((b) => b.onclick = () => { const t = $("input"); t.value = (t.value.trim() ? t.value.trim() + "\n\n" : "") + `(OOC: ${b.dataset.v}.)`; autosize(); t.focus(); });
  bar.querySelector("[data-cmd='continue']").onclick = () => { if (!busy) sendCommand("continue"); };
  bar.querySelector("[data-cmd='fight']").onclick = () => { if (!busy) sendCommand("fight", { act_for_me: $("fightAct").checked }); };
  $("fightAct").onchange = () => { try { localStorage.setItem("fightAct", $("fightAct").checked ? "1" : "0"); } catch (e) { /* not saved */ } };
  bar.querySelector("[data-cmd='dice']").onclick = () => diceForm();
  renderMoves();
  const ad = bar.querySelector("[data-cmd='adult']");
  if (ad) {
    ad.onclick = () => setArmed(!armed);
    $("adultKeep").onchange = () => { try { localStorage.setItem("adultKeep", $("adultKeep").checked ? "1" : "0"); } catch (e) { /* not saved */ } };
  }
}


// ---------- read aloud (the browser's own voices, or optional neural voices made by the app: both free and local) ----------
const tts = { on: !!window.speechSynthesis };
function voiceSettings() { try { return JSON.parse(localStorage.getItem("voices:" + (story ? story.id : "")) || "{}"); } catch { return {}; } }
function saveVoices(v) { try { localStorage.setItem("voices:" + story.id, JSON.stringify(v)); } catch {} }
// Which language to read in: the browser's language if it is a Latin-script one, Japanese/Chinese text by its letters, else English.
function ttsLang(text = "") {
  if (/[\u3040-\u30ff]/.test(text)) return "ja";
  if (/[\u4e00-\u9fff]/.test(text)) return "zh";
  const b = (navigator.language || "en").slice(0, 2).toLowerCase();
  return ["en", "fr", "de", "es", "it", "pt", "nl", "sv"].includes(b) ? b : "en";
}
// Voices are ranked, never taken from the top of the list: on Linux the first voice is often Afrikaans, which turns English text into noise.
function rankVoice(v, lang) {
  const l = (v.lang || "").toLowerCase().replace("_", "-");
  let sc = l.startsWith(lang) ? 100 : 0;
  if (lang === "en") sc += l === "en-us" ? 10 : l === "en-gb" ? 8 : 0;
  if (/natural|neural|premium|enhanced|online|google|siri|wavenet/i.test(v.name)) sc += 20;
  if (v.localService) sc += 2;
  if (/\+/.test(v.name)) sc -= 5;        // espeak voice variants (English+Adam…) sound stranger than the plain one
  if (v.default) sc += 1;
  return sc;
}
function bestVoice(lang) {
  const all = speechSynthesis.getVoices();
  return all.slice().sort((x, y) => rankVoice(y, lang) - rankVoice(x, lang))[0] || null;
}
// A chosen voice is either a browser voice name or "piper:<voice>" (a neural voice made on this computer by the app; see Read-aloud voices).
function pickVoice(name, lang = "en") {
  const all = speechSynthesis.getVoices(), vs = voiceSettings(), want = vs[name] || vs.narrator || "";
  const pv = (tts.piper || []);
  if (want.startsWith("piper:") && pv.includes(want.slice(6))) return { piper: want.slice(6) };
  const found = all.find((v) => v.name === want);
  if (found) return found;
  if (pv.length) return { piper: pv[0] };          // neural voices are installed and nothing else was chosen: use one
  return bestVoice(lang);
}
async function loadPiper() { try { const s = await api("/voice/status"); tts.piper = s.voices || []; return s; } catch { return null; } }
// Plain speech text from a reply: no status blocks, pictures, links, hidden tags; then (per line) no markdown marks or emoji.
function speechBlocks(text) {
  return text.replace(/```[\s\S]*?```/g, "").replace(/<(state|scene|newchar|items)>[\s\S]*?(<\/\1>|$)/gi, "").replace(/!\[[^\]]*\]\([^)]*\)/g, "")
    .replace(/^\[\/\/\]:.*$/gm, "").replace(/\[([^\]]*)\]\([^)]*\)/g, "$1").replace(/https?:\/\/\S+/g, "");
}
function speechMarks(t) {
  return t.replace(/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}\u{200D}]/gu, "").replace(/[*_#>`~|]/g, " ").replace(/[ \t]{2,}/g, " ").trim();
}
// Short pieces (a sentence or two): long utterances get cut off or garbled by several browsers and engines.
function chunks(text, max = 200) {
  const parts = text.match(/[^.!?…]+(?:[.!?…]+["')\]]*|$)\s*/g) || [text], out = [];
  let cur = "";
  for (const p of parts) {
    if ((cur + p).length > max && cur) { out.push(cur.trim()); cur = ""; }
    if (p.length > max * 1.5) { for (let i = 0; i < p.length; i += max) out.push(p.slice(i, i + max).trim()); }   // no punctuation at all: cut at a fixed length
    else cur += p;
  }
  if (cur.trim()) out.push(cur.trim());
  return out.filter((x) => /[A-Za-z0-9\u00c0-\uffff]/.test(x));
}
function segments(text) {
  const out = [];
  for (const para of speechBlocks(text).split(/\n+/)) {
    const m = para.match(/^\s*\*\*([^*|]{1,40}?)\*\*\s*\|\s*(.*)$/);   // "**Name** | line" is that character speaking
    const body = speechMarks(m ? m[2] : para);
    if (body) out.push({ who: m ? speechMarks(m[1]) : "narrator", text: body });
  }
  return out;
}
let ttsRun = 0, ttsAudio = null, ttsBusy = false;
function ttsStop() { ttsRun++; ttsBusy = false; try { speechSynthesis.cancel(); } catch {} if (ttsAudio) { try { ttsAudio.pause(); } catch {} ttsAudio = null; } }
function piperFetch(voice, text, rate) {
  return fetch("/api/voice/speak", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ voice, text, speed: rate }) })
    .then((r) => { if (!r.ok) throw new Error("The neural voice could not speak (" + r.status + ")."); return r.blob(); }).then((b) => URL.createObjectURL(b));
}
function playUrl(url, run) {
  return new Promise((ok) => {
    if (run !== ttsRun) return ok();
    const a = ttsAudio = new Audio(url);
    a.onended = a.onerror = () => { URL.revokeObjectURL(url); ok(); };
    a.play().catch(() => ok());
  });
}
function sayBrowser(piece, v, lang, rate, run) {
  return new Promise((ok) => {
    if (run !== ttsRun || !tts.on) return ok();
    const u = new SpeechSynthesisUtterance(piece); u.rate = rate; u.lang = v ? v.lang : lang;
    if (v) u.voice = v;
    u.onend = u.onerror = () => ok();
    speechSynthesis.speak(u);
  });
}
// Reads the pieces one after another. Neural pieces are made a step ahead (the next one is fetched while the current one plays), so there is no gap between sentences.
async function speakText(text) {
  if (!tts.piper) await loadPiper();     // which neural voices are installed (asked once)
  if (!tts.on && !(tts.piper || []).length) return alert("This browser can't read aloud.");
  ttsStop();
  const run = ++ttsRun, rate = +localStorage.getItem("ttsRate") || 1, lang = ttsLang(text), queue = [];
  for (const seg of segments(text)) { const v = pickVoice(seg.who, lang); for (const piece of chunks(seg.text, v && v.piper ? 320 : 200)) queue.push({ piece, v }); }
  ttsBusy = true;
  await new Promise((ok) => setTimeout(ok, 120));   // speaking right after cancel() makes Chrome drop or garble the first words
  const ready = (q) => q && q.v && q.v.piper && !q.url ? (q.url = piperFetch(q.v.piper, q.piece, rate).catch((e) => { q.err = e; return null; })) : null;
  ready(queue[0]);
  for (let i = 0; i < queue.length && run === ttsRun; i++) {
    const q = queue[i]; ready(queue[i + 1]);
    if (q.v && q.v.piper) {
      const url = await q.url;
      if (run !== ttsRun) { if (url) URL.revokeObjectURL(url); break; }
      if (url) await playUrl(url, run); else if (tts.on) await sayBrowser(q.piece, bestVoice(lang), lang, rate, run);   // the neural voice failed: fall back to the browser's
    } else await sayBrowser(q.piece, q.v, lang, rate, run);
  }
  if (run === ttsRun) ttsBusy = false;
}
function readLatest() {
  if (!story) return;
  if (ttsBusy || speechSynthesis.speaking) { ttsStop(); return; }
  const t = [...story.turns].reverse().find((x) => x.role === "assistant"); if (t) speakText(t.text);
}
async function showVoices() {
  const ps = await loadPiper();
  if (!tts.on && !ps) return alert("This browser can't read aloud.");
  const lang0 = ttsLang(), vs = (tts.on ? speechSynthesis.getVoices() : []).slice().sort((x, y) => rankVoice(y, lang0) - rankVoice(x, lang0) || x.name.localeCompare(y.name)), cur = voiceSettings(), names = ["narrator", ...(story.cast || []).filter((c) => !c.generic).map((c) => c.name.split(" ")[0])];
  const pv = tts.piper || [], pname = (id) => ((ps && ps.catalog.find((c) => c.id === id)) || {}).name || id;
  const noLang = !pv.length && !vs.some((v) => (v.lang || "").toLowerCase().startsWith(lang0));
  const opts = (sel) => `<option value="">(same as narrator)</option>` + (pv.length ? `<optgroup label="Neural voices (on this computer)">${pv.map((id) => `<option value="piper:${esc(id)}" ${"piper:" + id === sel ? "selected" : ""}>${esc(pname(id))}</option>`).join("")}</optgroup>` : "")
    + `<optgroup label="Browser voices">${vs.map((v) => `<option value="${esc(v.name)}" ${v.name === sel ? "selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("")}</optgroup>`;
  const narrOpts = (sel) => opts(sel || (pv.length ? "piper:" + pv[0] : "")).replace('<option value="">(same as narrator)</option>', "");
  modal(`<h2>Read-aloud voices</h2><p class="hint">Speech is made on this computer, so nothing is sent anywhere. Voices are saved on this device. Speaker lines (<code>**Name** | "…"</code>) use that character's voice; everything else uses the narrator.</p>
    ${noLang ? `<p class="hint" style="color:var(--danger)">Your computer has no ${lang0.toUpperCase()} voice installed, so speech will sound garbled. Install a neural voice below, or on Linux install a system voice with <code>sudo apt install speech-dispatcher espeak-ng</code>, then restart the browser.</p>` : ""}
    <details class="aiBox" ${pv.length ? "" : "open"}><summary><b>Neural voices</b> <span class="hint">${pv.length ? pv.length + " installed" : "optional download: natural-sounding speech"}</span></summary>
      <p class="hint">Natural-sounding voices from the free Piper project. They run on this computer (no account, no cost). ${ps && ps.engine ? "The voice engine is installed." : "The first voice also installs the voice engine (about 35 MB from PyPI)."} Each voice is one download of about 63 MB from the Piper voices collection on Hugging Face, kept in the app's data/voices folder.</p>
      ${ps ? `<div class="row" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><select id="pvPick">${ps.catalog.filter((c) => !c.installed).map((c) => `<option value="${esc(c.id)}">${esc(c.name)} (${c.mb} MB)</option>`).join("")}<option value="__other">Another Piper voice…</option></select><button id="pvGet">Download</button><span class="hint" id="pvMsg">${esc(ps.busy || "")}</span></div>
      ${pv.length ? `<div class="hint" style="margin-top:6px">Installed: ${pv.map((id) => `${esc(pname(id))} <a href="#" class="pvDel" data-id="${esc(id)}" title="Delete this voice file">remove</a>`).join(" · ")}</div>` : ""}` : `<p class="hint">Could not reach the app to check for neural voices.</p>`}
    </details>
    <label>Speed</label><input id="vRate" type="number" step="0.1" min="0.5" max="2" value="${+localStorage.getItem("ttsRate") || 1}">
    <div class="chkgrid" style="grid-template-columns:1fr 2fr">${names.map((n) => `<label>${esc(n)}</label><select data-v="${esc(n)}">${n === "narrator" ? narrOpts(cur[n]) : opts(cur[n])}</select>`).join("")}</div>
    <div class="actions"><button id="vAuto">Give each character a different voice</button><button id="vTest">Test narrator</button><span style="flex:1"></span><button id="mClose">Close</button><button class="primary" id="vSave">Save</button></div>`);
  $("mClose").onclick = () => { ttsStop(); closeModal(); };
  const keep = () => { const v = {}; document.querySelectorAll("[data-v]").forEach((s) => { if (s.value) v[s.dataset.v] = s.value; }); saveVoices(v); localStorage.setItem("ttsRate", $("vRate").value); };
  $("vTest").onclick = async () => {
    ttsStop(); const sel = document.querySelector('[data-v="narrator"]').value, rate = +$("vRate").value || 1, line = "The corridor was quiet, and then it was not.", run = ++ttsRun;
    if (sel.startsWith("piper:")) { try { await playUrl(await piperFetch(sel.slice(6), line, rate), run); } catch (e) { alert(e.message); } }
    else await sayBrowser(line, vs.find((x) => x.name === sel) || bestVoice(lang0), lang0, rate, run);
  };
  $("vAuto").onclick = () => { const pool = [...pv.map((id) => "piper:" + id), ...vs.filter((v) => (v.lang || "").toLowerCase().startsWith(lang0) && !/\+/.test(v.name)).map((v) => v.name)]; const sels = [...document.querySelectorAll("[data-v]")].filter((s) => s.dataset.v !== "narrator"); sels.forEach((s, k) => { if (pool.length) s.value = pool[(k + 1) % pool.length]; }); };
  $("vSave").onclick = () => { keep(); ttsStop(); closeModal(); };
  if ($("pvGet")) $("pvGet").onclick = async () => {
    let id = $("pvPick").value;
    if (id === "__other") { id = (prompt("Type the Piper voice name (for example en_US-lessac-medium or de_DE-thorsten-medium):") || "").trim(); if (!id) return; }
    const b = $("pvGet"); b.disabled = true; $("pvMsg").textContent = "Downloading… this can take a minute or two.";
    try { await api("/voice/install", { method: "POST", body: { voice: id } }); keep(); showVoices(); }
    catch (e) { $("pvMsg").textContent = e.message; b.disabled = false; }
  };
  document.querySelectorAll(".pvDel").forEach((a) => a.onclick = async (ev) => {
    ev.preventDefault(); if (!confirm("Delete this voice file from the computer? You can download it again later.")) return;
    try { await api("/voice/" + encodeURIComponent(a.dataset.id), { method: "DELETE" }); keep(); showVoices(); } catch (e) { alert(e.message); }
  });
}
if (tts.on) speechSynthesis.getVoices();


// ---------- prompt tester ----------
async function runPromptTest(body, modelChoice) {
  const out = $("fTestOut");
  if (!body.base_prompt.trim()) { out.innerHTML = '<p class="hint">Write the prompt first.</p>'; return; }
  const move = prompt("Sample player move to test with:", "I look around and take stock of what's happening."); if (move === null) return;
  out.innerHTML = '<p class="hint">Playing one sample move, then checking the reply… (can take up to a minute on a local model)</p>';
  try {
    const r = await api("/prompt-test", { method: "POST", body: { ...body, move, provider: modelChoice && modelChoice.provider, model: modelChoice && modelChoice.model } });
    const sev = { high: "var(--danger)", medium: "var(--gold)", low: "var(--muted)" };
    out.innerHTML = `<div class="aiBox"><b>${r.verdict === "good" ? "✓ No problems found" : r.verdict === "issues" ? "⚠ Problems found" : "Check finished"}</b> <small>(reply by ${esc(r.model)})</small>
      ${(r.findings || []).map((f) => `<div class="hint" style="color:${sev[f.severity] || "inherit"}"><b>${esc(f.severity || "")}</b>: ${esc(f.issue)}<br>→ ${esc(f.suggestion || "")}</div>`).join("")}
      <details><summary>Show the sample reply</summary><div style="max-height:260px;overflow:auto">${md(r.reply)}</div></details></div>`;
  } catch (e) { out.innerHTML = `<p class="hint" style="color:var(--danger)">${esc(e.message)}</p>`; }
}

// ---------- model comparison ----------
function showCompare() {
  if (!story) return;
  const opt = (sel) => models.map((m) => `<option value="${esc(m.provider)}|${esc(m.model)}" ${sel === m.provider + "|" + m.model ? "selected" : ""}>${esc(m.provider_name)}: ${esc(m.model)}</option>`).join("");
  const cur = story.models && story.models.story ? story.models.story.provider + "|" + story.models.story.model : $("modelSelect").value;
  modal(`<h2>Compare two AIs on your next move</h2><p class="hint">Both get exactly what a real turn would send. Nothing is saved until you press “Use this reply”. Online models cost real money; each side is logged in 💲 Usage.</p>
    <label>Your move</label><textarea id="cText" style="min-height:70px">${esc($("input").value)}</textarea>
    <div class="grid2"><div><label>A</label><select id="cA">${opt(cur)}</select></div><div><label>B</label><select id="cB">${opt("")}</select></div><div></div></div>
    <div class="actions" style="justify-content:flex-start"><button class="primary" id="cGo">Run both</button></div><div id="cOut"></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  if ($("cB").options.length > 1) $("cB").selectedIndex = Math.min(1, $("cB").options.length - 1);
  $("cGo").onclick = async (e) => {
    const text = $("cText").value.trim(); if (!text) return;
    const spec = (v) => { const [provider, model] = v.split("|"); return { provider, model }; };
    e.target.disabled = true; $("cOut").innerHTML = '<p class="hint">Both models are writing…</p>';
    try {
      const r = await api(`/stories/${story.id}/compare`, { method: "POST", body: { text, a: spec($("cA").value), b: spec($("cB").value) } });
      const col = (x, k) => `<div class="cmpcol"><b>${esc(x.provider)}: ${esc(x.model)}</b><br><small>${x.error ? "" : `${x.seconds}s · ${(x.usage.output || 0)} tokens out · ${x.cost == null ? "cost unknown" : "≈ $" + x.cost.toFixed(4)}`}</small>
        <div class="cmpbody">${x.error ? `<p class="hint" style="color:var(--danger)">${esc(x.error)}</p>` : md(x.reply)}</div>${x.error ? "" : `<button class="primary" data-use="${k}">Use this reply</button>`}</div>`;
      $("cOut").innerHTML = `<div class="cmpgrid">${col(r.a, "a")}${col(r.b, "b")}</div>`;
      $("cOut").querySelectorAll("[data-use]").forEach((b) => b.onclick = async () => {
        const x = r[b.dataset.use];
        story = await api(`/stories/${story.id}/compare/apply`, { method: "POST", body: { text, raw: x.raw, provider: x.provider, model: x.model } });
        $("input").value = ""; closeModal(); renderAll(); refreshArtSuggestions();
      });
    } catch (err) { $("cOut").innerHTML = `<p class="hint" style="color:var(--danger)">${esc(err.message)}</p>`; }
    e.target.disabled = false;
  };
}

// ---------- export & share ----------
function showExport() {
  if (!story) return;
  modal(`<h2>Export &amp; backups: “${esc(story.title)}”</h2>
    <div class="aiBox"><b>Plain text</b><div class="hint">Every turn as a text file.</div><button id="xText">Download .txt</button></div>
    <div class="aiBox"><b>Illustrated page</b><div class="hint">One self-contained web page with the story, pictures and your cover, ready to print or save as PDF from the browser (Ctrl+P). Pictures are shrunk to keep the file small.</div>
      <label class="chk"><input type="checkbox" id="xStatus"> Include status panels</label><button id="xHtml">Build page</button> <span class="hint" id="xProg"></span></div>
    <div class="aiBox"><b>Story package</b><div class="hint">Everything the author made, as one .zip to share or back up: prompt, prologue, opening scene and suggested first moves, cast (looks, personalities, bios, pictures), suggested trackers, endings, cover, intro panels and backdrop. It never contains your turns, memory, notes or API keys, and your avatar sheet only if you tick the box. Import it from the Home screen (📦).</div>
      <select id="xPics"><option value="all">All pictures</option><option value="normal">One picture per character</option><option value="none">No pictures</option></select>
      <label class="chk"><input type="checkbox" id="xAv"> Include my avatar sheet as a starter character (text only)</label>
      <label class="chk"><input type="checkbox" id="xWebp"> Smaller file: save pictures as WebP (high quality, much smaller)</label> <button id="xPack">Download package</button></div>
    <div class="aiBox"><b>Update this game from a package</b><div class="hint">Bring a newer version of this story's package into the game you are playing. Your turns, memory, avatar, notes and existing characters are kept; a backup is made first.</div>
      <label class="chk"><input type="checkbox" class="upPart" value="text" checked> Story text (prompt, prologue, opening, endings)</label>
      <label class="chk"><input type="checkbox" class="upPart" value="trackers" checked> Side-panel trackers (current values kept)</label>
      <label class="chk"><input type="checkbox" class="upPart" value="art"> Art settings</label>
      <label class="chk"><input type="checkbox" class="upPart" value="world"> Places and species (new ones added, missing pictures, cover and backdrop filled in)</label>
      <label class="chk"><input type="checkbox" class="upPart" value="cast"> Characters this game does not have yet, and pictures for characters that have none</label>
      <label class="btnlike">Choose package…<input type="file" id="xUpd" accept=".zip,application/zip" hidden></label> <span class="hint" id="xUpdNote"></span></div>
    <div class="aiBox"><b>Backups</b><div class="hint">This story: the app keeps copies of it automatically (before a summary, an edit or a restore, and every 5 turns) and you can restore one. Everything: one .zip with all stories, pictures, avatars and settings.</div>
      <button id="xSnaps">This story's backups…</button> <button id="xAll">Back up everything…</button></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  $("xSnaps").onclick = () => showBackups(story.id);
  $("xAll").onclick = () => showSettings("Backup");
  $("xText").onclick = () => window.open(`/api/stories/${story.id}/export`, "_blank");
  $("xPack").onclick = () => { window.location = `/api/stories/${story.id}/pack?pictures=${$("xPics").value}&avatar=${$("xAv").checked ? 1 : 0}&webp=${$("xWebp").checked ? 1 : 0}`; };
  $("xUpd").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const parts = [...document.querySelectorAll(".upPart:checked")].map((x) => x.value).join(",");
    if (!parts) { $("xUpdNote").textContent = "Tick at least one part."; e.target.value = ""; return; }
    try {
      const r = await uploadZip(`/api/stories/${story.id}/update-from-pack?parts=${parts}`, f, (pct) => { $("xUpdNote").textContent = `Uploading… ${pct}%`; });
      story = r; renderAll(); refreshTracker && refreshTracker();
      $("xUpdNote").textContent = "✔ Updated: " + (r.updated_parts || []).join(", ") + "." + ((r.import_notes || []).length ? " " + r.import_notes.join(" ") : "");
    } catch (err) { $("xUpdNote").textContent = "Could not update: " + err.message; }
    e.target.value = "";
  };
  $("xHtml").onclick = () => exportHtml($("xStatus").checked, (t) => { $("xProg").textContent = t; });
}
async function shrinkImage(src, max = 720) {
  try {
    const blob = await (await fetch(src)).blob(), bmp = await createImageBitmap(blob), k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas"); c.width = Math.round(bmp.width * k); c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.82);
  } catch { return ""; }
}
async function exportHtml(withStatus, progress) {
  const root = document.createElement("div");
  const parts = [];
  if (story.cover) parts.push(`<img class="cover" src="${story.cover}">`);
  parts.push(`<h1>${esc(story.title)}</h1>${story.description ? `<p class="desc">${esc(story.description)}</p>` : ""}`);
  const op = typeof openingHtml === "function" ? openingHtml() : "";
  if (op) parts.push(op);
  story.turns.forEach((t) => {
    let text = t.text; if (!withStatus) text = text.replace(/```[\s\S]*?```/g, "");
    parts.push(t.role === "user" ? `<div class="player">${md(text)}</div>` : md(text) + (t.images || []).map((im) => `<img class="scene" src="${im.url}">`).join(""));
  });
  root.innerHTML = parts.join("\n");
  const imgs = [...root.querySelectorAll("img")];
  let n = 0;
  for (const im of imgs) {
    progress(`Preparing pictures ${++n}/${imgs.length}…`);
    const src = im.getAttribute("src") || ""; const big = im.classList.contains("cover") || im.classList.contains("scene");
    const d = await shrinkImage(src, big ? 1000 : 520);
    if (d) im.setAttribute("src", d); else im.remove();
    im.removeAttribute("loading");
  }
  const css = "body{max-width:760px;margin:2em auto;padding:0 1em;font:17px/1.6 Georgia,serif;color:#222;background:#fff}h1{font-size:2em}.desc{color:#666;font-style:italic}.player{border-left:3px solid #8b6fd6;padding-left:12px;margin:1em 0;color:#5a4a9a;font-style:italic}img{max-width:100%;border-radius:6px;margin:.6em 0}img.cover{width:100%}pre{background:#f4f2fa;padding:8px;overflow:auto;font-size:12px;white-space:pre-wrap}blockquote{color:#555;border-left:3px solid #ccc;margin-left:0;padding-left:12px}@media print{body{margin:0;max-width:none}pre{break-inside:avoid}}";
  const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${esc(story.title)}</title><style>${css}</style></head><body>${root.innerHTML}</body></html>`;
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([html], { type: "text/html" })); a.download = (story.title.replace(/[^\w]+/g, "-").slice(0, 40) || "story") + ".html"; a.click();
  progress("Done.");
}

function bindTools() {   // called by app.js once $ and the page exist
  $("btnSearch").onclick = () => showSearch();
  $("btnQuick").onclick = () => { renderQuick(); $("quickBar").classList.toggle("hidden"); };
  $("btnCompare").onclick = showCompare;
  $("btnExport").onclick = showExport;
}


// A new story with stats and levels (a game, not real life): ask once how dice should work. The player can have the app roll for risky moves, keep dice to the 🎲
// button, or switch them off (also when the story's own prompt already calls for rolls).
async function maybeAskDice(tries = 0) {
  if (!story || (story.turns || []).length > 2) return;
  let d; try { d = await api(`/stories/${story.id}/dice`); } catch (e) { return; }
  if (!d.offer) return;
  if (!$("modal").classList.contains("hidden")) { if (tries < 6) setTimeout(() => maybeAskDice(tries + 1), 2500); return; }     // another window is open: try again shortly
  const sid = story.id, stats = d.stats.map((x) => x.label).join(", ");
  modal(`<h2>🎲 Dice checks for this story?</h2>
    <p class="hint">This story tracks stats and levels (${esc(stats)}). ${d.prompt_calls_for_checks
      ? "Its prompt already talks about rolls, so the app would roll for risky moves by itself."
      : "Dice make outcomes fair: when your move is risky, the app rolls a d20 plus your stat against a difficulty and the narrator must play out the result, so the story cannot simply let you win."}
      Choose how you want them (you can change this later in ⚡ → 🎲).</p>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap">
      <button class="primary" id="dcAuto">${d.prompt_calls_for_checks ? "Keep automatic dice" : "Turn on automatic dice"}</button>
      <button id="dcMan">Only when I press 🎲</button><button id="dcOff">No dice</button></div>
    <div class="hint" id="dcMsg">${d.prompt_calls_for_checks ? "" : "Automatic also adds a short dice rule to the story prompt (you can edit it in ✏️ Edit story)."}</div>`);
  const answer = (mode) => async () => {
    try { await api(`/stories/${sid}/dice/offer`, { method: "POST", body: { mode } }); story = await api(`/stories/${sid}`); } catch (e) { $("dcMsg").textContent = e.message; return; }
    closeModal(); renderAll(false);
  };
  $("dcAuto").onclick = answer("auto"); $("dcMan").onclick = answer("manual"); $("dcOff").onclick = answer("off");
}

// ---- 🎲 dice: the app rolls d20 + the stat's bonus (stat - 5, easing off above 10) against a difficulty and sends the result with your move
async function diceForm() {
  const box = $("qDice"); if (!box || !story) return;
  if (!box.classList.contains("hidden")) { box.classList.add("hidden"); return; }
  let d; try { d = await api(`/stories/${story.id}/dice`); } catch (e) { alert(e.message); return; }
  box.classList.remove("hidden");
  box.innerHTML = `<div class="trow"><div class="lrow" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
    <label>Roll against <select id="dStat"><option value="">(no stat: a plain d20)</option>${d.stats.map((x) => `<option value="${esc(x.id)}">${esc(x.label)}${x.value != null ? " " + x.value : ""}</option>`).join("")}</select></label>
    <label>Difficulty <select id="dDiff">${Object.entries(d.difficulties).map(([k, v]) => `<option value="${esc(k)}" ${k === d.difficulty ? "selected" : ""}>${esc(k)} (${v})</option>`).join("")}</select></label>
    <label>Dice <select id="dAdv"><option value="">one d20</option><option value="adv">advantage (best of two)</option><option value="dis">disadvantage (worst of two)</option></select></label>
    <button class="primary" id="dGo">🎲 Roll and send</button></div>
    <div class="hint">Your move is whatever is in the box (or “I attempt it.”). Result = d20 + the stat's bonus (stat − 5, easing off above 10 so a high-level stat stays meaningful) against the difficulty; the app rolls, so the narrator cannot fudge it.</div>
    <div class="lrow" style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:6px"><label>Automatic rolls <select id="dMode"><option value="">Let the story prompt decide (${d.prompt_calls_for_checks ? "it calls for rolls: automatic" : "it does not mention rolls: only when I press 🎲"})</option><option value="auto">Automatic: the app rolls for risky actions</option><option value="manual">Only when I press 🎲</option><option value="off">Never</option></select></label></div></div>`;
  $("dMode").value = d.mode || "";
  $("dMode").onchange = () => api(`/stories/${story.id}/dice`, { method: "PUT", body: { mode: $("dMode").value } }).catch((e) => alert(e.message));
  $("dGo").onclick = async () => {
    if (busy) return;
    try {
      const r = await api(`/stories/${story.id}/roll`, { method: "POST", body: { id: $("dStat").value, difficulty: $("dDiff").value, adv: $("dAdv").value } });
      const typed = $("input").value.trim();
      box.classList.add("hidden");
      send(typed || "I attempt it.", undefined, false, "", false, [], {}, r);
    } catch (e) { alert(e.message); }
  };
}

// ---- 🧭 moves the app suggests from the scene (no AI): talk to whoever is here, go to a place, check a quest, spend points
function renderMoves() {
  const box = $("qMoves"); if (!box || !story) return;
  const turns = story.turns || [], lastAi = [...turns].reverse().find((t) => t.role === "assistant"), txt = lastAi ? lastAi.text : "";
  const names = [], places = [];
  for (const m of txt.matchAll(/!\[\]\(portrait:([^/)]+)\/|\[\[([^/\]]+)\//g)) { const n = (m[1] || m[2] || "").trim(); if (n && !names.includes(n)) names.push(n); }
  for (const m of txt.matchAll(/!\[\]\(place:([^)]+)\)/g)) { const n = m[1].trim(); if (n && !places.includes(n)) places.push(n); }
  const me = ((story.avatar || {}).name || story.player_name || "").toLowerCase();
  const items = [];
  names.filter((n) => n.toLowerCase() !== me).slice(0, 4).forEach((n) => items.push([`💬 Talk to ${n}`, `I turn to ${n} and talk to ${n.split(" ")[0]}.`]));
  (story.places || []).filter((p) => p.name && !places.includes(p.name)).slice(0, 3).forEach((p) => items.push([`🚪 Go to ${p.name}`, `I head to ${p.name}.`]));
  const tr = story.tracker || {};
  for (const f of activeFields().filter((x) => x.kind === "quests")) {
    const q = ((tr.me || {})[f.id.split(".")[1]] || []).find((x) => x.status === "active"); if (q) { items.push([`📋 ${q.name}`, `I think about my task: ${q.name}, and decide what to do next.`]); break; }
  }
  items.push(["🔎 Look around", "I look around carefully."]);
  const pf = activeFields().find((f) => (f.spend_on || []).length);
  if (pf && Number((tr.me || {})[pf.id.split(".")[1]]) > 0) items.push([`⭐ Spend ${pf.label.toLowerCase()}`, null]);
  box.innerHTML = items.length ? `<small class="hint">Suggested moves (no AI):</small> ` + items.map(([l, v], i) => `<button data-mv="${i}">${esc(l)}</button>`).join("") : "";
  box.querySelectorAll("button").forEach((b) => b.onclick = () => { const it = items[+b.dataset.mv]; if (!it[1]) return showPoints(); const t = $("input"); t.value = (t.value.trim() ? t.value.trim() + " " : "") + it[1]; autosize(); t.focus(); });
}
