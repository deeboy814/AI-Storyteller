// Home screen: a card for every story (cover art + short description), a blank capsule to start a new story, and one to import a save.
// The Home screen is not "inside" a story: its Settings, Picture makers, Usage and Avatars windows work on the app-wide defaults. The open story is set aside while such a
// window is open and put back when it closes (so "Back to story" still works).
async function asHomeDefaults(fn) {
  const keep = story; story = null;
  const back = () => { if (story === null) story = keep; };
  try { await fn(); } catch (e) { back(); throw e; }
  const m = $("modal");
  if (m.classList.contains("hidden")) { back(); return; }
  const obs = new MutationObserver(() => { if (m.classList.contains("hidden")) { obs.disconnect(); back(); } });
  obs.observe(m, { attributes: true, attributeFilter: ["class"] });
}
function hideHome() { $("home").classList.add("hidden"); }
function gradientFor(title) { let h = 0; for (const ch of title) h = (h * 31 + ch.charCodeAt(0)) % 360; return `linear-gradient(135deg, hsl(${h} 45% 28%), hsl(${(h + 50) % 360} 50% 16%))`; }
// Rename a story in place: replaces `el` with a text box (Enter or clicking away saves, Esc cancels). `done(newTitle)` runs after a save.
function editTitleInline(el, id, current, done) {
  if (el.querySelector("input")) return;
  const old = el.innerHTML, box = document.createElement("input");
  box.className = "titleedit"; box.value = current; box.maxLength = 120;
  el.innerHTML = ""; el.appendChild(box); box.focus(); box.select();
  let over = false;
  const finish = async (save) => {
    if (over) return; over = true;
    const v = box.value.trim();
    if (!save || !v || v === current) { el.innerHTML = old; return; }
    try { const r = await api(`/stories/${id}/home`, { method: "PUT", body: { title: v } }); done(r.title); }
    catch (err) { el.innerHTML = old; alert("Could not rename: " + err.message); }
  };
  box.onkeydown = (e) => { e.stopPropagation(); if (e.key === "Enter") finish(true); else if (e.key === "Escape") finish(false); };
  box.onblur = () => finish(true);
  box.onclick = (e) => e.stopPropagation();
}
function blurb(s) { return s.description || s.subtitle || ""; }

async function showHome() {
  const home = $("home");
  home.classList.remove("hidden");
  const list = (await api("/stories")).sort((a, b) => b.updated - a.updated);
  const card = (s) => `<div class="hcard" data-id="${esc(s.id)}">
    <div class="hcover" style="${s.cover ? "" : `background:${gradientFor(s.title)}`}">${s.cover ? `<img loading="lazy" src="${esc(s.cover)}" alt="">` : `<span>${esc((s.title || "?").trim().charAt(0).toUpperCase())}</span>`}
      <button class="hedit" data-edit="${esc(s.id)}" title="Cover art, description, delete">⋯</button></div>
    <div class="hmeta"><span class="ttl"><b>${esc(s.title)}</b><button class="tedit" data-rename="${esc(s.id)}" title="Rename this story">✎</button></span>
      <p>${esc(blurb(s)) || '<i>No description yet. Use ⋯ to add one.</i>'}</p>
      <small>${s.turns} turn${s.turns === 1 ? "" : "s"}${s.player ? " · " + esc(s.player) : ""}${s.cast ? " · " + s.cast + " characters" : ""}</small></div></div>`;
  home.innerHTML = `<div class="hwrap">
    <div class="hhead"><h1>AI Storyteller</h1><span style="flex:1"></span>${story ? '<button id="hBack">← Back to story</button>' : ""}<label class="btnlike" title="Import a story package (.zip) exported from the story creator">📦 Import a story<input type="file" id="hPack" accept=".zip,application/zip" hidden></label>${config && config.avatar_library ? '<button id="hAv" title="Your saved player avatars">🧑 Avatars</button>' : ""}<button id="hStore" title="Disk use, clean-up, and stories you deleted (restore them)">🗂 Storage</button><button id="hUse" title="Usage and estimated cost">💲 Usage</button><button id="hBug" title="Make a bug report file (personal data removed) to attach to a problem report">🐞 Report a bug</button><button id="hMk" title="Which AI draws pictures, ComfyUI and the picture services">🖼 Picture makers</button><button id="hSet" title="Settings">⚙ Settings</button><button id="hQuit" class="quitbtn" title="Close or restart AI Storyteller">${isThisComputer() ? "🚪 Quit" : "🔄 Restart"}</button></div>
    <div class="hgrid">
      ${list.map(card).join("")}
      <button class="hcard hnew" id="hNew"><div class="hplus">＋</div><b>New story</b><small>Write or paste a prompt and start playing</small></button>
      <button class="hcard hnew" id="hImp"><div class="hplus">📥</div><b>Import a save</b><small>Bring over a story played elsewhere: a summary or the whole text</small></button>
    </div></div>`;
  home.querySelectorAll(".hcard[data-id]").forEach((el) => el.onclick = async (e) => {
    if (e.target.closest(".hedit") || e.target.closest(".tedit") || e.target.closest(".titleedit")) return;
    await openStory(el.dataset.id); hideHome();
  });
  home.querySelectorAll(".tedit[data-rename]").forEach((b) => b.onclick = (e) => {
    e.stopPropagation();
    const s = list.find((x) => x.id === b.dataset.rename), ttl = b.closest(".ttl");
    editTitleInline(ttl, s.id, s.title, async (nt) => { if (story && story.id === s.id) { story.title = nt; renderHeader(); } await showHome(); });
  });
  home.querySelectorAll(".hedit").forEach((b) => b.onclick = () => showCardEditor(list.find((s) => s.id === b.dataset.edit)));
  $("hNew").onclick = () => editStory(null);
  $("hImp").onclick = () => showImport(list);
  $("hMk").onclick = () => asHomeDefaults(() => showArtHub("Makers"));
  $("hSet").onclick = () => asHomeDefaults(() => showSettings());
  $("hQuit").onclick = () => quitApp();
  $("hUse").onclick = () => asHomeDefaults(() => showUsage());
  $("hBug").onclick = () => showBugReport(() => showHome());
  $("hStore").onclick = () => showStorage();
  if ($("hAv")) $("hAv").onclick = () => asHomeDefaults(() => showAvatarLibrary());
  $("hPack").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const lbl = e.target.closest("label"), old = lbl ? lbl.firstChild.textContent : "";
    if (lbl) lbl.firstChild.textContent = `Importing ${(f.size / 1048576).toFixed(0)} MB… `;
    try {
      const s = await uploadZip("/api/stories/import-pack", f, (pct) => { if (lbl) lbl.firstChild.textContent = pct < 100 ? `Uploading ${pct}%… ` : "Unpacking… "; });
      showPackImported(s);
    } catch (err) { alert("Could not import the story: " + err.message); }
    if (lbl) lbl.firstChild.textContent = old;
    e.target.value = "";
  };
  if ($("hBack")) $("hBack").onclick = hideHome;
}

function showCardEditor(s, nav = {}) {   // nav.back: where to go when closed instead of Home (used by the Art hub)
  modal(`<h2>${esc(s.title)}</h2>
    <label>Title</label><input id="cvTitle" maxlength="120" value="${esc(s.title)}">
    <label>Cover art</label>
    <div class="hcover small" id="cvPrev" style="${s.cover ? "" : `background:${gradientFor(s.title)}`}">${s.cover ? `<img src="${esc(s.cover)}" alt="">` : ""}</div>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><label class="btnlike">Upload…<input type="file" id="cvUp" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${s.cover ? '<button id="cvDel">Remove</button>' : ""}</div>
    <label>Picture description (what the artist is told: edit it freely)</label>
    <textarea class="short" id="cvPrompt" rows="3" placeholder="Leave empty and the text model writes one from the story prompt when you draw. Your art style is added automatically."></textarea>
    <label>Picture maker for the cover <small class="hint">(try different ones: each draw uses the one chosen here)</small></label>
    <select id="cvMaker"><option value="">The story's own picture maker</option></select>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="cvWritePrompt" title="Have the text model write the description from the story prompt">✨ Write it from the story</button><button class="primary" id="cvGen">Draw 4 options</button></div>
    <p class="hint" id="cvNote">Draws with the picture maker chosen above (the story's own by default) and uses its recap model for the description. Nothing changes on the card until you pick one.</p>
    <div id="cvOpts"></div>
    <label>Description (shown on the card)</label>
    <textarea class="short" id="cvDesc" style="min-height:70px" maxlength="400">${esc(s.description || "")}</textarea>
    <div class="actions" style="justify-content:flex-start"><button id="cvWrite">✨ Write it for me</button></div>
    <div class="actions"><button id="cvBk">Backups…</button><label class="btnlike" title="Bring a newer version of this story's package into this game: new story text, trackers, beats, lore, command buttons, places, and pictures that are missing. Your turns, memory, avatar and notes are kept; a backup is made first.">⟳ Update from a package…<input type="file" id="cvUpd" accept=".zip,application/zip" hidden></label><button id="cvPack" title="Export this story (prompt, cast with pictures, prologue, opening, trackers, endings, media) as a package. No turns, memory or notes.">Export story…</button><button id="cvRestart" title="Start this story again from the beginning as a separate copy. Your current game stays as it is.">↺ Restart from the beginning…</button><button class="danger" id="cvRm">Delete story</button><span style="flex:1"></span><button id="cvClose">Close</button><button class="primary" id="cvSave">Save</button></div>`);
  const done = async () => { closeModal(); if (nav.back) await nav.back(); else await showHome(); };
  const busyNote = (t) => { $("cvNote").textContent = t; };
  $("cvClose").onclick = done;
  $("cvSave").onclick = async () => {
    const r = await api(`/stories/${s.id}/home`, { method: "PUT", body: { description: $("cvDesc").value, title: $("cvTitle").value.trim() || s.title } });
    if (story && story.id === s.id) { story.title = r.title; renderHeader(); }
    done();
  };
  $("cvWrite").onclick = async (e) => { e.target.disabled = true; busyNote("Writing…"); try { const r = await api(`/stories/${s.id}/description/generate`, { method: "POST" }); $("cvDesc").value = r.description; busyNote("Written. Edit it if you like, then Save."); } catch (err) { busyNote(err.message); } e.target.disabled = false; };
  let lastMaker = "";
  api(`/stories/${s.id}/cover/prompt`).then((r) => { if (!$("cvPrompt").value) $("cvPrompt").value = r.prompt || ""; const m = r.maker || {}; lastMaker = m.provider ? `${m.provider}|${m.model || ""}` : ""; if ($("cvMaker") && lastMaker) $("cvMaker").value = lastMaker; }).catch(() => {});
  api("/model-options").then((o) => {   // every picture maker and model this computer can use (online ones need a key and bill separately)
    const sel = $("cvMaker"); if (!sel) return;
    const groups = (o.images || []).map((p) => {
      const note = p.local ? (p.ready ? " (on this computer, free)" : " (not available)") : p.has_key ? " (online, billed by the service)" : " (needs a key)";
      const usable = p.local ? p.ready : p.has_key;
      const models = p.models.length ? p.models : [{ id: "" }];
      return `<optgroup label="${esc(p.name + note)}">${models.map((m) => `<option value="${esc(p.id + "|" + m.id)}" ${usable ? "" : "disabled"}>${esc(m.id || p.name)}${m.size_gb ? ` · ${m.size_gb} GB` : ""}</option>`).join("")}</optgroup>`;
    }).join("");
    sel.insertAdjacentHTML("beforeend", groups);
    if (lastMaker) sel.value = lastMaker;
  }).catch(() => { /* the story's own maker still works */ });
  $("cvWritePrompt").onclick = async (e) => {
    e.target.disabled = true; busyNote("Writing the description…");
    try { const r = await api(`/stories/${s.id}/cover/describe`, { method: "POST" }); $("cvPrompt").value = r.prompt; busyNote("Written. Edit it if you like, then draw."); } catch (err) { busyNote(err.message); }
    e.target.disabled = false;
  };
  const opts = [];
  const showOpts = () => {
    $("cvOpts").innerHTML = opts.length ? `<div class="gallery">${opts.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary cvUse" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button id="cvMore">Draw 4 more options</button></div>` : "";
    if ($("cvMore")) $("cvMore").onclick = () => drawOpts(false);
  };
  const drawOpts = async (fresh) => {
    $("cvGen").disabled = true; busyNote("Starting…");
    try {
      const r = await api(`/stories/${s.id}/cover/candidates`, { method: "POST", body: { prompt: $("cvPrompt").value, count: 4, fresh, provider: ($("cvMaker").value || "|").split("|")[0], model: ($("cvMaker").value || "|").split("|").slice(1).join("|") } });
      const job = await watchJob(r.job, (j) => busyNote(j.current || "Drawing…"));
      if (job.status === "error") { busyNote("Could not draw: " + job.error); }
      else {
        if (fresh) opts.length = 0;
        opts.push(...job.result.candidates);
        if (!$("cvPrompt").value.trim() && job.result.prompt) $("cvPrompt").value = job.result.prompt;
        busyNote("Pick one, or change the description and draw again.");
        showOpts();
      }
    } catch (err) { busyNote("Could not draw: " + err.message); }
    $("cvGen").disabled = false;
  };
  $("cvGen").onclick = () => drawOpts(true);
  $("cvOpts").addEventListener("click", async (e) => {
    const b = e.target.closest(".cvUse"); if (!b) return;
    try {
      const r = await api(`/stories/${s.id}/cover/choose`, { method: "POST", body: { url: b.dataset.url } });
      s.cover = r.cover; $("cvPrev").style.background = ""; $("cvPrev").innerHTML = `<img src="${esc(r.cover)}" alt="">`;
      opts.length = 0; showOpts(); busyNote("✔ Saved as the cover. Close to see it on the card.");
    } catch (err) { busyNote(err.message); }
  });
  $("cvUp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const lbl = e.target.closest("label"), old = lbl ? lbl.firstChild.textContent : "";
    if (lbl) lbl.firstChild.textContent = `Importing ${(f.size / 1048576).toFixed(0)} MB… `;
    try {
      const r = await fetch(`/api/stories/${s.id}/cover`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!r.ok) throw new Error(await r.text());
      s.cover = (await r.json()).cover; $("cvPrev").style.background = ""; $("cvPrev").innerHTML = `<img src="${esc(s.cover)}" alt="">`; busyNote("Uploaded.");
    } catch (err) { busyNote("Upload failed: " + err.message); }
  };
  if ($("cvDel")) $("cvDel").onclick = async () => { await api(`/stories/${s.id}/cover`, { method: "DELETE" }); done(); };
  $("cvRestart").onclick = async () => {
    const t = prompt(`Start “${s.title}” again from the beginning as a separate copy?\n\nYour current game stays exactly as it is. The copy keeps the story prompt, cast and pictures, places, trackers, art settings and your character, but none of the turns, memory, side-panel values or key events. For an imported save only the setup carries over, not the imported history.\n\nName for the new copy:`, `${s.title} (restarted)`);
    if (t === null) return;
    $("cvRestart").disabled = true;
    try { const r = await api(`/stories/${s.id}/restart`, { method: "POST", body: { title: t.trim() } }); closeModal(); await showHome(); alert(`Created “${r.story.title}”. It is on the Home screen: open it to begin.` + (r.prompt_trimmed ? `\n\nThe story prompt was written from your imported text; its “Where the story stands” part was left out, so the copy does not start from where you ended. Read the rest under ✏️ Edit story.` : "") + (r.removed_characters.length ? `\n\nLeft out because the story introduced them later: ${r.removed_characters.join(", ")}.` : "")); }
    catch (err) { alert(err.message); $("cvRestart").disabled = false; }
  };
  $("cvPack").onclick = () => { window.location = `/api/stories/${s.id}/pack?pictures=all`; };
  $("cvBk").onclick = () => showBackups(s.id);
  $("cvUpd").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    if (!confirm(`Update “${s.title}” from this package?\n\nIts turns, memory, avatar and notes are kept and a backup is made first. It brings in the package's story text, trackers, beats, lore, buttons, places and any missing pictures.`)) { e.target.value = ""; return; }
    try {
      const r = await uploadZip(`/api/stories/${s.id}/update-from-pack?parts=text,trackers,world,cast`, f, () => {});
      alert("Updated: " + ((r.updated_parts || []).join(", ") || "nothing new") + ".");
      closeModal(); await showHome();
    } catch (err) { alert("Could not update: " + err.message); }
    e.target.value = "";
  };
  $("cvRm").onclick = async () => {
    let n = 0;
    try { n = (await api(`/stories/${s.id}/art-count`)).files; } catch (e) { /* count is only informational */ }
    modal(`<h2>Delete “${esc(s.title)}”?</h2>
      <p class="hint">The story, its turns and memory are deleted. A copy is kept in its backups folder.</p>
      ${n ? `<label class="chk top"><input type="checkbox" id="rmArt"><span><b>Also delete its pictures</b> (${n} file${n > 1 ? "s" : ""}: portraits, places, scenes, cover, backdrop)<span class="hint">Leave unticked to keep them on this computer, so they come back if the story is restored from a backup.</span></span></label>` : ""}
      <div class="actions"><button id="rmNo">Cancel</button><button class="danger" id="rmYes">Delete story</button></div>`);
    $("rmNo").onclick = () => showCardEditor(s, nav);
    $("rmYes").onclick = async () => {
      const art = $("rmArt") && $("rmArt").checked ? 1 : 0;
      try { await api(`/stories/${s.id}?art=${art}`, { method: "DELETE" }); } catch (e) { alert(e.message); return; }
      if (story && story.id === s.id) { story = null; localStorage.removeItem("story"); renderAll(false); }
      done();
    };
  };
}

// After importing a story package: start fresh, or bring over a save made elsewhere
async function showPackImported(s) {
  const list = await api("/stories");
  modal(`<h2>“${esc(s.title)}” imported</h2>
    <p class="hint">${(s.cast || []).length} characters${s.prologue ? ", a prologue" : ""}${s.opening ? ", an opening scene" : ""}${(s.endings || []).length ? ", " + s.endings.length + " endings" : ""}${(s.places || []).length ? ", " + s.places.length + " places" : ""}${Object.keys(s.species || {}).length ? ", " + Object.keys(s.species).length + " species" : ""}${((s.sidebar || {}).custom || []).length ? ", " + s.sidebar.custom.length + " trackers" : ""}${s.avatar && s.avatar.name ? ", a starter avatar (" + esc(s.avatar.name) + ")" : ""}. How do you want to begin?</p>${(s.import_notes || []).map((n) => `<p class="budgetnote warn">⚠ ${esc(n)}</p>`).join("")}
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button class="primary" id="pkScratch">Start from scratch</button><button id="pkSave">Import a save…</button></div>
    <p class="hint">A save is a game you already played elsewhere (a summary, or the whole story). It continues this story from where you left off.</p>`);
  $("pkScratch").onclick = async () => { closeModal(); await openStory(s.id); hideHome(); };
  $("pkSave").onclick = () => showImport(list, { into: s.id, intoTitle: s.title });
}


// Upload a .zip with a progress callback (percent), resolving to the JSON reply. fetch() cannot report upload progress, so this uses XMLHttpRequest.
function uploadZip(url, file, onPct) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url); x.setRequestHeader("content-type", "application/zip");
    x.upload.onprogress = (ev) => { if (ev.lengthComputable && onPct) onPct(Math.round((ev.loaded / ev.total) * 100)); };
    x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (e) { /* not JSON */ }
      if (x.status >= 200 && x.status < 300) resolve(j); else reject(new Error(j.detail || `HTTP ${x.status}`)); };
    x.onerror = () => reject(new Error("The upload failed (is the app still running?)"));
    x.send(file);
  });
}

function isThisComputer() { return ["127.0.0.1", "localhost", "[::1]", "::1"].includes(location.hostname); }

// Close the app from its own screen (no terminal needed). Stories are saved as they go.
function quitApp() {
  const writing = typeof busy !== "undefined" && busy;
  if (!isThisComputer()) {     // a phone or tablet: it can only restart the app, and only when that was allowed at the computer
    if (!(config && config.lan_restart)) { modal(`<h2>Close or restart the app</h2><div class="hint">This can only be done on the computer the app runs on. To let a phone or tablet restart it, turn on <b>Let phones and tablets restart the app</b> in Settings → Phone &amp; tablet on that computer.</div><div class="actions"><button id="mCancel">OK</button></div>`); $("mCancel").onclick = closeModal; return; }
    modal(`<h2>Restart AI Storyteller?</h2><div class="hint">${writing ? "<b>A reply is still being written.</b> Restarting now stops it. " : ""}The app closes and starts again by itself, which takes about 10 seconds; this page then reloads. Your stories are saved as you play, so nothing is lost. If it does not come back, it has to be started again at the computer.</div>
      <div class="actions"><button id="mCancel">Cancel</button><button class="primary" id="mRestart">Restart</button></div>`);
    $("mCancel").onclick = closeModal;
    $("mRestart").onclick = async () => { $("mRestart").disabled = true; if (!(await restartApp())) $("mRestart").disabled = false; };
    return;
  }
  modal(`<h2>Close AI Storyteller?</h2>
    <div class="hint">${writing ? "<b>A reply is still being written.</b> Closing now stops it. " : ""}Your stories are saved as you play, so nothing is lost. To open the app again, double-click <b>AI Storyteller</b> on your Desktop.</div>
    <div class="actions"><button id="mCancel">Cancel</button><button id="mRestart" title="Close and start again, then reload this page (use after an update or when something is stuck)">Restart</button><button class="primary" id="mQuit">Close the app</button></div>`);
  $("mCancel").onclick = closeModal;
  $("mRestart").onclick = async () => {
    $("mRestart").disabled = $("mQuit").disabled = true;
    if (!(await restartApp())) { $("mRestart").disabled = $("mQuit").disabled = false; }
  };
  $("mQuit").onclick = async () => {
    $("mQuit").disabled = true;
    try { await api("/quit", { method: "POST" }); } catch (e) { $("mQuit").disabled = false; alert(e.message); return; }
    closeModal();
    document.body.innerHTML = `<div style="max-width:520px;margin:18vh auto;padding:0 20px;text-align:center;font:16px/1.6 system-ui,sans-serif;color:#e8e5f2">
      <h1 style="font-size:24px">AI Storyteller has closed</h1>
      <p>Your stories are saved. You can close this tab.</p><p style="color:#9a97b3">To open it again, double-click <b>AI Storyteller</b> on your Desktop or in your applications menu.</p></div>`;
    document.body.style.background = "#13121c";
  };
}
// Close and start the app again, then reload the page. Returns false if the app refused.
async function restartApp() {
    try { await api("/quit?restart=1", { method: "POST" }); } catch (e) { alert(e.message); return false; }
    closeModal();
    document.body.innerHTML = `<div style="max-width:520px;margin:18vh auto;padding:0 20px;text-align:center;font:16px/1.6 system-ui,sans-serif;color:#e8e5f2"><h1 style="font-size:24px">Restarting AI Storyteller…</h1><p id="rsNote" style="color:#9a97b3">This takes a few seconds. The page reloads by itself.</p></div>`;
    document.body.style.background = "#13121c";
    await new Promise((r) => setTimeout(r, 3500));
    for (let i = 0; i < 60; i++) {
      try { const r = await fetch("/api/stories", { cache: "no-store" }); if (r.ok) { location.reload(); return; } } catch (e) {}
      await new Promise((r) => setTimeout(r, 1000));
    }
    $("rsNote") && ($("rsNote").textContent = "It did not come back. Double-click AI Storyteller on your Desktop to start it.");
    return true;
}

// "Update waiting" notice: the server's files changed since it started (restart to load), or the page's files changed since it loaded (reload).
let pageFp = null;
async function checkCodeStatus() {
  let st; try { st = await api("/code-status"); } catch (e) { return; }
  if (pageFp === null) pageFp = st.page;
  const el = $("updateNote"); if (!el) return;
  const page = st.page !== pageFp;
  if (!st.restart && !page) { el.classList.add("hidden"); return; }
  el.className = "budgetnote warn";
  el.innerHTML = st.restart ? `⟳ An update to the app is waiting. <button id="unRestart">Restart now</button> <small>(stories are saved)</small>`
    : `⟳ The page was updated. <button id="unReload">Reload</button>`;
  if ($("unRestart")) $("unRestart").onclick = () => { if (!(typeof busy !== "undefined" && busy) || confirm("A reply is still being written. Restart anyway?")) restartApp(); };
  if ($("unReload")) $("unReload").onclick = () => location.reload();
}
window.addEventListener("load", () => { checkCodeStatus(); setInterval(checkCodeStatus, 60000); window.addEventListener("focus", checkCodeStatus); });   // after app.js (which defines api) has loaded

// ---------- storage & recently deleted stories ----------
function fmtBytes(n) { return n > 1e9 ? (n / 1e9).toFixed(1) + " GB" : n > 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.round(n / 1e3) + " KB"; }
async function showStorage() {
  let rep, del;
  try { [rep, del] = await Promise.all([api("/storage"), api("/deleted-stories")]); } catch (e) { alert(e.message); return; }
  const keptTotal = rep.kept_pictures.reduce((a, k) => a + k.bytes, 0);
  modal(`<h2>Storage</h2>
    <h3>Recently deleted stories</h3>
    ${del.length ? del.map((d) => `<div class="aiBox"><b>${esc(d.title)}</b> <small class="muted">${d.turns} turn${d.turns === 1 ? "" : "s"} · deleted ${new Date(d.when * 1000).toLocaleString()} · ${d.pictures_kept ? "pictures kept" : "pictures deleted"}</small>
      <div class="actions" style="justify-content:flex-start;margin-top:6px"><button data-restore="${esc(d.id)}">Restore</button><button class="danger" data-forget="${esc(d.id)}">Delete forever</button></div></div>`).join("")
      : '<p class="hint">None. When you delete a story, a copy is kept here so you can bring it back.</p>'}
    <h3>Disk use</h3>
    <table class="stotab"><tr><th>Story</th><th>Pictures</th><th>Backups</th><th>Text</th></tr>
      ${rep.stories.map((s) => `<tr><td>${esc(s.title)}</td><td>${fmtBytes(s.pictures)}</td><td>${fmtBytes(s.backups)}</td><td>${fmtBytes(s.story)}</td></tr>`).join("")}</table>
    <h3>Clean up</h3>
    <div class="aiBox"><b>Unchosen picture options</b> ${fmtBytes(rep.options)} <button data-clean="options" ${rep.options ? "" : "disabled"}>Delete</button><div class="hint">Options you drew but did not pick. They are also removed automatically after a day.</div></div>
    <div class="aiBox"><b>Small-picture cache</b> ${fmtBytes(rep.thumbs)} <button data-clean="thumbs" ${rep.thumbs ? "" : "disabled"}>Clear</button><div class="hint">Shrunk copies that make screens load fast; they are made again when needed.</div></div>
    <div class="aiBox"><b>Pictures kept from deleted stories</b> ${fmtBytes(keptTotal)} <button class="danger" data-clean="kept" ${keptTotal ? "" : "disabled"}>Delete</button><div class="hint">Kept when you deleted a story without its pictures. A restored story finds them again; once deleted they are gone.</div></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  const card = $("modalCard");
  card.querySelectorAll("[data-restore]").forEach((b) => b.onclick = async () => {
    try { await api(`/deleted-stories/${b.dataset.restore}/restore`, { method: "POST" }); closeModal(); await showHome(); } catch (e) { alert(e.message); }
  });
  card.querySelectorAll("[data-forget]").forEach((b) => b.onclick = async () => {
    if (!confirm("Delete this story's backups and any kept pictures forever? This cannot be undone.")) return;
    try { await api(`/deleted-stories/${b.dataset.forget}`, { method: "DELETE" }); showStorage(); } catch (e) { alert(e.message); }
  });
  card.querySelectorAll("[data-clean]").forEach((b) => b.onclick = async () => {
    if (b.dataset.clean === "kept" && !confirm("Delete the pictures kept from deleted stories? This cannot be undone.")) return;
    try { await api("/storage/clean", { method: "POST", body: { what: b.dataset.clean } }); showStorage(); } catch (e) { alert(e.message); }
  });
}
