// Per-turn tools (bookmark, edit, branch, retry with another model, continue, read aloud), search, quick instructions, voices.
function turnBar(t, i, lastAi) {
  const b = (act, title, label) => `<button data-act="${act}" title="${title}">${label}</button>`;
  return `<span class="tbar">${t.role === "assistant" ? b("read", "Read aloud", "🔊") : ""}${b("bm", "Bookmark this turn", t.bookmark ? "★" : "☆")}${b("edit", "Edit this message", "✎")}` +
    `${t.role === "user" ? b("branch", "Branch: start a copy of this story from here to try a different move", "⑂") : ""}` +
    `${i === lastAi ? b("alt", "Retry this reply with a different AI", "↻▾") + b("cont", "Continue this reply (if it was cut off)", "…") : ""}</span>`;
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
      <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="eSave">Save</button></div>`);
    $("mClose").onclick = closeModal;
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
      <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="aGo">Retry</button></div>`);
    $("mClose").onclick = closeModal;
    $("aGo").onclick = async () => { const v = $("aModel").value; closeModal(); const text = await undo(); if (text) send(text, v); };
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
  $("quickBar").innerHTML = QUICK.map(([l, v]) => `<button data-v="${esc(v)}" title="Adds an OOC note to your message">${l}</button>`).join("");
  $("quickBar").querySelectorAll("button").forEach((b) => b.onclick = () => { const t = $("input"); t.value = (t.value.trim() ? t.value.trim() + "\n\n" : "") + `(OOC: ${b.dataset.v}.)`; autosize(); t.focus(); });
}

// ---------- read aloud (the browser's own voices: free, local) ----------
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
function pickVoice(name, lang = "en") {
  const all = speechSynthesis.getVoices(), vs = voiceSettings(), want = vs[name] || vs.narrator;
  return all.find((v) => v.name === want) || bestVoice(lang);
}
// Plain speech text from a reply: no status blocks, pictures, links, hidden tags; then (per line) no markdown marks or emoji.
function speechBlocks(text) {
  return text.replace(/```[\s\S]*?```/g, "").replace(/<(state|scene|newchar)>[\s\S]*?(<\/\1>|$)/gi, "").replace(/!\[[^\]]*\]\([^)]*\)/g, "")
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
let ttsRun = 0;
function speakText(text) {
  if (!tts.on) return alert("This browser can't read aloud.");
  speechSynthesis.cancel();
  const run = ++ttsRun, rate = +localStorage.getItem("ttsRate") || 1, lang = ttsLang(text);
  setTimeout(() => {   // speaking right after cancel() makes Chrome drop or garble the first words
    if (run !== ttsRun) return;
    for (const seg of segments(text)) {
      const v = pickVoice(seg.who, lang);
      for (const piece of chunks(seg.text)) {
        const u = new SpeechSynthesisUtterance(piece); u.rate = rate; u.lang = v ? v.lang : lang;
        if (v) u.voice = v;
        speechSynthesis.speak(u);
      }
    }
  }, 120);
}
function readLatest() {
  if (!story) return;
  if (speechSynthesis.speaking) { speechSynthesis.cancel(); return; }
  const t = [...story.turns].reverse().find((x) => x.role === "assistant"); if (t) speakText(t.text);
}
function showVoices() {
  if (!tts.on) return alert("This browser can't read aloud.");
  const lang0 = ttsLang(), vs = speechSynthesis.getVoices().slice().sort((x, y) => rankVoice(y, lang0) - rankVoice(x, lang0) || x.name.localeCompare(y.name)), cur = voiceSettings(), names = ["narrator", ...(story.cast || []).map((c) => c.name.split(" ")[0])];
  const noLang = !vs.some((v) => (v.lang || "").toLowerCase().startsWith(lang0));
  const opts = (sel) => `<option value="">(same as narrator)</option>` + vs.map((v) => `<option value="${esc(v.name)}" ${v.name === sel ? "selected" : ""}>${esc(v.name)} (${esc(v.lang)})</option>`).join("");
  modal(`<h2>Read-aloud voices</h2><p class="hint">Uses the voices built into your browser and computer, so nothing is sent anywhere. Voices are saved on this device. Speaker lines (<code>**Name** | "…"</code>) use that character's voice; everything else uses the narrator.</p>
    ${noLang ? `<p class="hint" style="color:var(--danger)">Your computer has no ${lang0.toUpperCase()} voice installed, so speech will sound garbled. On Linux install one with <code>sudo apt install speech-dispatcher espeak-ng</code>, then restart the browser.</p>` : ""}
    <label>Speed</label><input id="vRate" type="number" step="0.1" min="0.5" max="2" value="${+localStorage.getItem("ttsRate") || 1}">
    <div class="chkgrid" style="grid-template-columns:1fr 2fr">${names.map((n) => `<label>${esc(n)}</label><select data-v="${esc(n)}">${n === "narrator" ? opts(cur[n]).replace('<option value="">(same as narrator)</option>', "") : opts(cur[n])}</select>`).join("")}</div>
    <div class="actions"><button id="vAuto">Give each character a different voice</button><button id="vTest">Test narrator</button><span style="flex:1"></span><button id="mClose">Close</button><button class="primary" id="vSave">Save</button></div>`);
  $("mClose").onclick = () => { speechSynthesis.cancel(); closeModal(); };
  $("vTest").onclick = () => { const v = vs.find((x) => x.name === document.querySelector('[data-v="narrator"]').value) || bestVoice(lang0); speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance("The corridor was quiet, and then it was not."); if (v) { u.voice = v; u.lang = v.lang; } u.rate = +$("vRate").value || 1; speechSynthesis.speak(u); };
  $("vAuto").onclick = () => { const pool = vs.filter((v) => (v.lang || "").toLowerCase().startsWith(lang0) && !/\+/.test(v.name)); const sels = [...document.querySelectorAll("[data-v]")].filter((s) => s.dataset.v !== "narrator"); sels.forEach((s, k) => { if (pool.length) s.value = pool[(k + 1) % pool.length].name; }); };
  $("vSave").onclick = () => { const v = {}; document.querySelectorAll("[data-v]").forEach((s) => { if (s.value) v[s.dataset.v] = s.value; }); saveVoices(v); localStorage.setItem("ttsRate", $("vRate").value); speechSynthesis.cancel(); closeModal(); };
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
  modal(`<h2>Export “${esc(story.title)}”</h2>
    <div class="aiBox"><b>Plain text</b><div class="hint">Every turn as a text file.</div><button id="xText">Download .txt</button></div>
    <div class="aiBox"><b>Illustrated page</b><div class="hint">One self-contained web page with the story, pictures and your cover, ready to print or save as PDF from the browser (Ctrl+P). Pictures are shrunk to keep the file small.</div>
      <label class="chk"><input type="checkbox" id="xStatus"> Include status panels</label><button id="xHtml">Build page</button> <span class="hint" id="xProg"></span></div>
    <div class="aiBox"><b>Story package</b><div class="hint">Everything the author made, as one .zip to share or back up: prompt, prologue, opening scene and suggested first moves, cast (looks, personalities, bios, pictures), suggested trackers, endings, cover, intro panels and backdrop. It never contains your turns, memory, character sheet, notes or API keys. Import it from the Home screen (📦).</div>
      <select id="xPics"><option value="all">All pictures</option><option value="normal">One picture per character</option><option value="none">No pictures</option></select> <button id="xPack">Download package</button></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  $("xText").onclick = () => window.open(`/api/stories/${story.id}/export`, "_blank");
  $("xPack").onclick = () => { window.location = `/api/stories/${story.id}/pack?pictures=${$("xPics").value}`; };
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
