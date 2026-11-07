// Home screen: a card for every story (cover art + short description), a blank capsule to start a new story, and one to import a save.
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
    <div class="hhead"><h1>AI Storyteller</h1><span style="flex:1"></span>${story ? '<button id="hBack">← Back to story</button>' : ""}<label class="btnlike" title="Import a story package (.zip) exported from the story creator">📦 Import a story<input type="file" id="hPack" accept=".zip,application/zip" hidden></label>${config && config.avatar_library ? '<button id="hAv" title="Your saved player avatars">🧑 Avatars</button>' : ""}<button id="hUse" title="Usage and estimated cost">💲 Usage</button><button id="hSet" title="Settings">⚙ Settings</button><button id="hQuit" class="quitbtn" title="Close AI Storyteller">⏻ Quit</button></div>
    <div class="hgrid">
      ${list.map(card).join("")}
      <button class="hcard hnew" id="hNew"><div class="hplus">＋</div><b>New story</b><small>Write or paste a prompt and start playing</small></button>
      <button class="hcard hnew" id="hImp"><div class="hplus">⭳</div><b>Import a save</b><small>Bring over a story played elsewhere: a summary or the whole text</small></button>
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
  $("hSet").onclick = () => showSettings();
  $("hQuit").onclick = () => quitApp();
  $("hUse").onclick = () => showUsage();
  if ($("hAv")) $("hAv").onclick = () => showAvatarLibrary();
  $("hPack").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await fetch("/api/stories/import-pack", { method: "POST", headers: { "content-type": "application/zip" }, body: f });
      if (!r.ok) throw new Error(JSON.parse(await r.text()).detail);
      const s = await r.json(); showPackImported(s);
    } catch (err) { alert("Could not import the story: " + err.message); }
  };
  if ($("hBack")) $("hBack").onclick = hideHome;
}

function showCardEditor(s) {
  modal(`<h2>${esc(s.title)}</h2>
    <label>Title</label><input id="cvTitle" maxlength="120" value="${esc(s.title)}">
    <label>Cover art</label>
    <div class="hcover small" id="cvPrev" style="${s.cover ? "" : `background:${gradientFor(s.title)}`}">${s.cover ? `<img src="${esc(s.cover)}" alt="">` : ""}</div>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="cvGen">✨ Generate from the story prompt</button><label class="btnlike">Upload…<input type="file" id="cvUp" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${s.cover ? '<button id="cvDel">Remove</button>' : ""}</div>
    <p class="hint" id="cvNote">Generating uses this story's picture maker (Settings → AI & story) and its recap model to write the picture description.</p>
    <label>Description (shown on the card)</label>
    <textarea class="short" id="cvDesc" style="min-height:70px" maxlength="400">${esc(s.description || "")}</textarea>
    <div class="actions" style="justify-content:flex-start"><button id="cvWrite">✨ Write it for me</button></div>
    <div class="actions"><button id="cvBk">Backups…</button><button id="cvPack" title="Export this story (prompt, cast with pictures, prologue, opening, trackers, endings, media) as a package. No turns, memory or notes.">Export story…</button><button class="danger" id="cvRm">Delete story</button><span style="flex:1"></span><button id="cvClose">Close</button><button class="primary" id="cvSave">Save</button></div>`);
  const done = async () => { closeModal(); await showHome(); };
  const busyNote = (t) => { $("cvNote").textContent = t; };
  $("cvClose").onclick = done;
  $("cvSave").onclick = async () => {
    const r = await api(`/stories/${s.id}/home`, { method: "PUT", body: { description: $("cvDesc").value, title: $("cvTitle").value.trim() || s.title } });
    if (story && story.id === s.id) { story.title = r.title; renderHeader(); }
    done();
  };
  $("cvWrite").onclick = async (e) => { e.target.disabled = true; busyNote("Writing…"); try { const r = await api(`/stories/${s.id}/description/generate`, { method: "POST" }); $("cvDesc").value = r.description; busyNote("Written. Edit it if you like, then Save."); } catch (err) { busyNote(err.message); } e.target.disabled = false; };
  $("cvGen").onclick = async (e) => {
    e.target.disabled = true; busyNote("Drawing the cover… (about 20 seconds on a local graphics card)");
    try { const r = await api(`/stories/${s.id}/cover/generate`, { method: "POST" }); s.cover = r.cover; $("cvPrev").style.background = ""; $("cvPrev").innerHTML = `<img src="${esc(r.cover)}" alt="">`; busyNote("Done. Close to see it on the card."); }
    catch (err) { busyNote(err.message); }
    e.target.disabled = false;
  };
  $("cvUp").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const r = await fetch(`/api/stories/${s.id}/cover`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!r.ok) throw new Error(await r.text());
      s.cover = (await r.json()).cover; $("cvPrev").style.background = ""; $("cvPrev").innerHTML = `<img src="${esc(s.cover)}" alt="">`; busyNote("Uploaded.");
    } catch (err) { busyNote("Upload failed: " + err.message); }
  };
  if ($("cvDel")) $("cvDel").onclick = async () => { await api(`/stories/${s.id}/cover`, { method: "DELETE" }); done(); };
  $("cvPack").onclick = () => { window.location = `/api/stories/${s.id}/pack?pictures=all`; };
  $("cvBk").onclick = () => showBackups(s.id);
  $("cvRm").onclick = async () => {
    if (!confirm(`Delete “${s.title}” permanently?`)) return;
    await api("/stories/" + s.id, { method: "DELETE" });
    if (story && story.id === s.id) { story = null; localStorage.removeItem("story"); renderAll(); }
    done();
  };
}

// After importing a story package: start fresh, or bring over a save made elsewhere
async function showPackImported(s) {
  const list = await api("/stories");
  modal(`<h2>“${esc(s.title)}” imported</h2>
    <p class="hint">${(s.cast || []).length} characters${s.prologue ? ", a prologue" : ""}${s.opening ? ", an opening scene" : ""}${(s.endings || []).length ? ", " + s.endings.length + " endings" : ""}. How do you want to begin?</p>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button class="primary" id="pkScratch">Start from scratch</button><button id="pkSave">Import a save…</button></div>
    <p class="hint">A save is a game you already played elsewhere (a summary, or the whole story). It continues this story from where you left off.</p>`);
  $("pkScratch").onclick = async () => { closeModal(); await openStory(s.id); hideHome(); };
  $("pkSave").onclick = () => showImport(list, { into: s.id, intoTitle: s.title });
}


// Close the app from its own screen (no terminal needed). Stories are saved as they go.
function quitApp() {
  const writing = typeof busy !== "undefined" && busy;
  modal(`<h2>Close AI Storyteller?</h2>
    <div class="hint">${writing ? "<b>A reply is still being written.</b> Closing now stops it. " : ""}Your stories are saved as you play, so nothing is lost. To open the app again, double-click <b>AI Storyteller</b> on your Desktop.</div>
    <div class="actions"><button id="mCancel">Cancel</button><button class="primary" id="mQuit">Close the app</button></div>`);
  $("mCancel").onclick = closeModal;
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
