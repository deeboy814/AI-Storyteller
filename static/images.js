"use strict";
// Image features: mood portraits in the story, scene illustrations, the Art tab, the Cast & gallery screen, and the
// image provider settings. Loaded before app.js; it uses app.js's globals (story, api, modal, esc, ...) at call time.

// ---------- portraits & scene pictures in the story ----------
// The narrator writes ![](portrait:Name/mood); show that character's picture for that mood (or their default).
// Small cached copies (w = longest side) keep screens with many big pictures fast; the original opens on click.
const thumbUrl = (u, w) => u ? `/api/thumb?url=${encodeURIComponent(u)}&w=${w}` : "";
function applyPortraitSize() {
  const sz = { small: "130px", medium: "200px", large: "300px" }[((story && story.art) || {}).portrait_size || "medium"];
  document.getElementById("narrative").style.setProperty("--npcpic-h", sz);
}
// Intro / webtoon panels: ![](intro:It1) in the opening scene shows the story's panel named It1.
function introPanelHtml(name) {
  const im = ((story && story.intro_images) || []).find((p) => p.name.toLowerCase() === name.trim().toLowerCase());
  return im ? `<a href="${im.url}" target="_blank"><img class="intro-panel" src="${thumbUrl(im.url, 1100)}" alt=""></a>` : "";
}
// The opening scene sits at the top of the story: open for a new story, folded away once play is underway.
const openingOpen = {};
function openingHtml() {
  if (!story) return "";
  let h = "";
  if ((story.prologue || "").trim()) {
    const open = openingOpen["p" + story.id] ?? (story.turns.length === 0);
    h += `<details class="opening" data-sid="p${story.id}"${open ? " open" : ""}><summary>📜 Prologue</summary><div class="openingbody">${md(story.prologue)}</div></details>`;
  }
  if ((story.opening || "").trim()) {
    const open = openingOpen[story.id] ?? (story.turns.length === 0);
    h += `<details class="opening" data-sid="${story.id}"${open ? " open" : ""}><summary>📖 Opening scene</summary><div class="openingbody">${md(story.opening)}</div></details>`;
  }
  return h;
}
document.getElementById("narrative").addEventListener("toggle", (e) => {
  if (e.target.classList && e.target.classList.contains("opening")) openingOpen[e.target.dataset.sid] = e.target.open;
}, true);
const newMoodSeen = new Set();   // one "Draw new" button per character+mood per render
let drawingNew = null;             // "slug|mood" being drawn right now
function portraitInfo(spec) {
  const i = spec.lastIndexOf("/");
  const name = (i < 0 ? spec : spec.slice(0, i)).trim().toLowerCase();
  const mood = (i < 0 ? "default" : spec.slice(i + 1)).trim().toLowerCase();
  const flat = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");   // "Female Student" = "FemaleStudent"
  const ch = ((story && story.cast) || []).find((c) => c.name.toLowerCase() === name || c.name.toLowerCase().split(" ")[0] === name || flat(c.name) === flat(name));
  if (!ch) return null;
  const m = ch.moods || {};
  // A character's "default" is a picture named default, or else a neutral one (normal 1 / normal), or else any picture.
  const neutral = m["default"] || m["normal 1"] || m["normal"] || Object.values(m)[0] || "";
  return { ch, mood, url: m[mood] || neutral, exact: !!m[mood] || (mood === "default" && !!neutral) };
}
// Existing pictures are always used. A new-picture button only appears when the narrator asks for a mood this character has no picture for.
function portraitHtml(spec) {
  const p = portraitInfo(spec);
  if (!p) return "";
  let h = p.url ? `<img class="npcpic" loading="lazy" src="${thumbUrl(p.url, 640)}" alt="">` : "";
  const key = p.ch.slug + "|" + p.mood;
  if (!p.exact && /^[a-z0-9][a-z0-9 _-]{0,29}$/.test(p.mood) && (p.ch.appearance || "").trim() && !newMoodSeen.has(key)) {
    newMoodSeen.add(key);
    h += drawingNew === key ? '<span class="drawing">🎨 Drawing…</span>'
      : `<button class="newmood" data-slug="${esc(p.ch.slug)}" data-mood="${esc(p.mood)}" title="No picture fits this moment yet. Draw one with the image AI.">＋ Draw “${esc(p.mood)}” for ${esc(p.ch.name)}</button>`;
  }
  return h;
}

let illustratingTurn = null; // index of the reply being drawn right now

function sceneHtml(t, i) {
  let h = (t.images || []).map((im, k) => `<figure class="scenefig"><a href="${im.url}" target="_blank"><img class="scene" loading="lazy" src="${thumbUrl(im.url, 1100)}" alt=""></a>
    <div class="scenebar"><button data-act="redraw" data-turn="${i}" data-img="${k}" title="Draw this scene again (the earlier picture stays)">↻ Redraw</button>
    <button data-act="save" data-turn="${i}" data-img="${k}" title="Keep this picture as a portrait of a character">💾 Save as portrait</button>
    <button class="x" data-act="del" data-turn="${i}" data-img="${k}" title="Delete this picture">×</button></div></figure>`).join("");
  if (illustratingTurn === i) h += '<div class="drawing">🎨 Drawing…</div>';
  return h;
}

async function illustrate(turnIdx = null, scene = null) {
  if (!story || busy || illustratingTurn !== null) return;
  const idx = turnIdx ?? story.turns.map((t) => t.role).lastIndexOf("assistant");
  if (idx < 0) { alert("There is no narrator reply to illustrate yet."); return; }
  illustratingTurn = idx; renderNarrative();
  try { story = await api(`/stories/${story.id}/illustrate`, { method: "POST", body: { turn: idx, scene } }); }
  catch (e) { alert("Illustration failed: " + e.message); }
  illustratingTurn = null; renderAll();
}

document.getElementById("narrative").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || !story || busy) return;
  if (b.classList.contains("newmood")) {
    drawingNew = b.dataset.slug + "|" + b.dataset.mood; renderNarrative();
    try { story = await api(`/stories/${story.id}/cast/${b.dataset.slug}/generate`, { method: "POST", body: { mood: b.dataset.mood } }); }
    catch (err) { alert("Could not draw it: " + err.message); }
    drawingNew = null; renderAll(); return;
  }
  const act = b.dataset.act; if (!act) return;
  const turn = +b.dataset.turn, im = story.turns[turn] && (story.turns[turn].images || [])[+b.dataset.img];
  if (!im) return;
  if (act === "redraw") illustrate(turn, im.scene);
  else if (act === "save") showSaveAsPortrait(im.url);
  else if (act === "del" && confirm("Delete this picture?")) { story = await api(`/stories/${story.id}/images?url=${encodeURIComponent(im.url)}`, { method: "DELETE" }); renderAll(); }
});

function showSaveAsPortrait(url) {
  const cast = story.cast || [];
  if (!cast.length) { alert("Add characters in 🎭 Cast first."); return; }
  modal(`<h2>Save as a portrait</h2>
    <p class="hint">Keep this picture as one of a character's portraits. It can then be shown above their lines, and it also becomes reference material that helps later pictures of them look the same.</p>
    <label>Character</label><select id="spChar">${cast.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("")}</select>
    <label>Picture name (mood), e.g. default, angry, smile</label><input id="spMood" value="default" list="spMoods"><datalist id="spMoods"></datalist>
    <p class="hint">If the character already has a picture with that name, the old one is kept as an earlier version.</p>
    <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="spGo">Save</button></div>`);
  const fillMoods = () => { const c = cast.find((x) => x.slug === document.getElementById("spChar").value); document.getElementById("spMoods").innerHTML = Object.keys((c && c.moods) || {}).map((m) => `<option value="${esc(m)}">`).join(""); };
  document.getElementById("spChar").onchange = fillMoods; fillMoods();
  document.getElementById("mClose").onclick = closeModal;
  document.getElementById("spGo").onclick = async () => {
    try { story = await api(`/stories/${story.id}/cast/${document.getElementById("spChar").value}/from-image`, { method: "POST", body: { url, mood: document.getElementById("spMood").value || "default" } }); closeModal(); renderAll(); }
    catch (err) { alert(err.message); }
  };
}

// ---------- import a portrait pack from a folder ----------
function showImportPortraits() {
  let last = ""; try { last = localStorage.getItem("portraitFolder") || ""; } catch (e) { /* optional */ }
  modal(`<h2>Import portraits from a folder</h2>
    <p class="hint">Point to a folder that has one sub-folder per character, each holding pictures named for moods (for example <code>Mara/Angry 1.webp</code>). A folder named after your story can sit inside it.
      Pictures are copied: your originals are never moved or changed. Pictures that were already imported are skipped.</p>
    <label>Folder path on this computer</label><input id="ipPath" value="${esc(last)}" placeholder="/home/you/Pictures/My Story">
    <div class="actions" style="justify-content:flex-start"><button id="ipScan" class="primary">Preview</button></div>
    <div id="ipOut"></div>
    <div class="actions"><button id="mClose">Cancel</button><button id="ipGo" class="primary hidden">Import these pictures</button></div>`);
  const path = () => document.getElementById("ipPath").value.trim();
  document.getElementById("mClose").onclick = () => showCast("Characters");
  document.getElementById("ipScan").onclick = async (e) => {
    const out = document.getElementById("ipOut"); e.target.disabled = true; out.textContent = "Reading the folder…";
    try {
      const r = await api(`/stories/${story.id}/cast/import-folder`, { method: "POST", body: { path: path(), apply: false } });
      const total = r.characters.reduce((n, c) => n + c.pictures, 0), fresh = r.characters.filter((c) => !c.existing).length;
      out.innerHTML = `<p><b>${r.characters.length}</b> characters, <b>${total}</b> pictures found in <code>${esc(r.root)}</code></p>
        <p class="hint">${fresh} new character${fresh === 1 ? "" : "s"} will be added to your cast; ${r.characters.length - fresh} already in your cast get these pictures added.</p>
        <div style="max-height:200px;overflow:auto;font-size:13px">${r.characters.map((c) => `<div>${esc(c.name)}: ${c.pictures} picture${c.pictures === 1 ? "" : "s"}${c.existing ? "" : " (new)"}</div>`).join("")}</div>
        ${r.intro ? `<p class="hint"><b>${r.intro}</b> intro / webtoon panels will be set as this story's intro panels.</p>` : ""}
        ${r.skipped.length ? `<p class="hint">Skipped: ${r.skipped.map((x) => `${esc(x.folder)} (${x.pictures} pictures, ${esc(x.why)})`).join("; ")}</p>` : ""}`;
      document.getElementById("ipGo").classList.toggle("hidden", !r.characters.length);
    } catch (err) { out.textContent = err.message; document.getElementById("ipGo").classList.add("hidden"); }
    e.target.disabled = false;
  };
  document.getElementById("ipGo").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "Importing…";
    try {
      const r = await api(`/stories/${story.id}/cast/import-folder`, { method: "POST", body: { path: path(), apply: true } });
      story = r.story; try { localStorage.setItem("portraitFolder", path()); } catch (err) { /* optional */ }
      alert(`Imported ${r.added} pictures` + (r.duplicates ? ` (${r.duplicates} already imported, skipped)` : "") + (r.failed.length ? `. ${r.failed.length} could not be read: ${r.failed.slice(0, 3).join("; ")}` : "."));
      renderAll(); showCast("Characters");
    } catch (err) { alert(err.message); e.target.disabled = false; e.target.textContent = "Import these pictures"; }
  };
}

// ---------- Art tab (inside the Player panel) ----------
function artTabHtml() {
  const a = (story && story.art) || {};
  return `<div id="tabArt" class="hidden">
    <label>Art direction: added to every image prompt, so it sets the look of your whole story</label>
    <textarea class="short" id="artDir" rows="4" placeholder="e.g. anime light-novel illustration, soft cel shading, muted autumn palette, cinematic lighting">${esc(a.direction || "")}</textarea>
    <label>Setting: the world your characters belong to. Every generated character (portraits, candidates, your avatar) is dressed and styled to fit it</label>
    <textarea class="short" id="artSetting" rows="3" placeholder="Written automatically from your story prompt the first time a character is drawn. Edit freely.">${esc(a.setting || "")}</textarea>
    <div class="actions" style="justify-content:flex-start;margin-top:4px"><button id="artSettingGen">✨ Write it from the story prompt</button><span class="hint" id="artSettingNote"></span></div>
    <label>Negative prompt: things to keep out of pictures</label>
    <textarea class="short" id="artNeg" rows="2">${esc(a.negative || "")}</textarea>
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Scene picture shape</label>
      <select id="artSize"><option value="portrait">Portrait (2:3)</option><option value="landscape">Landscape (3:2)</option><option value="square">Square</option></select></div>
      <div><label>Portrait size in the story</label><select id="artPsize"><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option></select></div></div>
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Shape of newly drawn portraits</label><select id="artPshape"><option value="landscape">Landscape card (3:2)</option><option value="portrait">Portrait (2:3)</option><option value="square">Square</option></select></div><div></div></div>
    <label class="chk"><input type="checkbox" id="artAuto" ${a.auto ? "checked" : ""}> Auto-illustrate: the narrator flags visually striking turns and a picture is drawn for them (uses image credits)</label>
    <label class="chk"><input type="checkbox" id="artSuggest" ${a.suggest !== false ? "checked" : ""}> Offer to create art for named characters that have none (you can always skip or say never)</label>
    <label class="chk"><input type="checkbox" id="artPort" ${a.portraits !== false ? "checked" : ""}> Show character mood portraits above their lines (set them up in 🎭 Cast)</label>
    <p class="hint">Both switches add a short instruction for the narrator. Changing them makes the next Claude turn rewrite the prompt cache once.</p>
  </div>`;
}
function artTabInit() { const a = (story && story.art) || {};
  $("artSettingGen").onclick = async (e) => { e.target.disabled = true; $("artSettingNote").textContent = "Writing…"; try { const r = await api(`/stories/${story.id}/art/setting`, { method: "POST" }); $("artSetting").value = r.art.setting; $("artSettingNote").textContent = "Done. Edit if you like, then Save."; } catch (err) { $("artSettingNote").textContent = err.message; } e.target.disabled = false; }; $("artSize").value = a.size || "portrait"; $("artPsize").value = a.portrait_size || "medium"; $("artPshape").value = a.portrait_shape || "landscape"; }
async function artTabSave() {
  story = await api(`/stories/${story.id}/art`, { method: "PUT", body: {
    direction: $("artDir").value, setting: $("artSetting").value, negative: $("artNeg").value, size: $("artSize").value, portrait_size: $("artPsize").value, portrait_shape: $("artPshape").value, auto: $("artAuto").checked, suggest: $("artSuggest").checked, portraits: $("artPort").checked } });
}

// ---------- Cast: character cards, and a character view with Bio / Key events / Images ----------
const NEUTRAL_MOODS = ["default", "normal 1", "normal"];
// The card picture is the character's neutral one: default, else "normal 1", else "normal", else any picture.
function normalUrl(c) { const m = c.moods || {}; for (const k of NEUTRAL_MOODS) if (m[k]) return m[k]; return Object.values(m)[0] || ""; }
const oneLine = (t, n) => { t = (t || "").replace(/\s+/g, " ").trim(); return t.length > n ? t.slice(0, n - 1) + "…" : t; };

// Characters that share a "group" (e.g. Generic Characters) show as one card whose picture is a mashup of up to four members' normal pictures.
function castCards(card) {
  const groups = {}, out = [];
  for (const c of story.cast || []) {
    if (!c.group || story.group_cast === false) { out.push(card(c)); continue; }
    if (!groups[c.group]) { groups[c.group] = []; out.push({ group: c.group }); }
    groups[c.group].push(c);
  }
  return out.map((o) => {
    if (typeof o === "string") return o;
    const ms = groups[o.group], pics = ms.filter((c) => normalUrl(c)).sort(() => Math.random() - 0.5).slice(0, 4);
    const art = pics.length ? `<div class="mash n${pics.length}">${pics.map((c) => `<img loading="lazy" src="${thumbUrl(normalUrl(c), 300)}" alt="">`).join("")}</div>` : '<div class="noart">No art yet</div>';
    return `<button class="charcard" data-group="${esc(o.group)}">${art}<div class="meta"><b>${esc(o.group)}</b><small>${ms.length} characters: ${esc(ms.map((c) => c.name).join(", "))}</small></div></button>`;
  }).join("");
}
function showCastGroup(name) {
  const ms = (story.cast || []).filter((c) => c.group === name);
  modal(`<h2>${esc(name)}</h2><p class="hint">Click one to see their bio, key events and pictures.</p>
    <div class="cardgrid">${ms.map((c) => `<button class="charcard" data-slug="${esc(c.slug)}">${normalUrl(c) ? `<img loading="lazy" src="${thumbUrl(normalUrl(c), 420)}" alt="">` : '<div class="noart">No art yet</div>'}<div class="meta"><b>${esc(c.name)}</b><small>${esc(oneLine(c.bio || c.appearance, 60) || "no description yet")}</small></div></button>`).join("")}</div>
    <div class="actions"><button id="gBack">← Cast</button><button class="danger" id="gUn">Ungroup all</button></div>`);
  document.querySelectorAll(".charcard[data-slug]").forEach((b) => b.onclick = () => showCharacter(b.dataset.slug));
  $("gBack").onclick = () => showCast("Characters");
  $("gUn").onclick = async () => { story = await api(`/stories/${story.id}/cast-group`, { method: "PUT", body: { slugs: ms.map((c) => c.slug), group: "" } }); showCast("Characters"); };
}
function showGroupEditor() {
  const cast = story.cast || [], existing = [...new Set(cast.map((c) => c.group).filter(Boolean))];
  modal(`<h2>Group characters</h2><p class="hint">Grouped characters show as one card in the Cast. Each keeps their own pictures and bio.</p>
    <label>Group name</label><input id="gName" value="${esc(existing[0] || "Generic Characters")}">
    <div class="actions" style="justify-content:flex-start"><button id="gGuess">Tick likely generic characters</button></div>
    <div class="chkgrid">${cast.map((c) => `<label class="chk"><input type="checkbox" value="${esc(c.slug)}" ${c.group ? "checked" : ""}> ${esc(c.name)}${c.group ? ` <small>(${esc(c.group)})</small>` : ""}</label>`).join("")}</div>
    <div class="actions"><button id="gCancel">Cancel</button><button class="primary" id="gSave">Save</button></div>`);
  $("gCancel").onclick = () => showCast("Characters");
  $("gGuess").onclick = () => { // role words and lettered extras ("Zombie A", "Guard 2") are usually generic
    const re = /\b(dean|student|teacher|professor|researcher|scientist|soldier|guard|officer|police|nurse|doctor|zombie|villager|bandit|civilian|stranger|survivor|extra)s?\b|\s[a-d]$|\d$/i;
    document.querySelectorAll(".chkgrid input").forEach((i) => { const c = cast.find((x) => x.slug === i.value); if (re.test(c.name.replace(/([a-z])([A-Z])/g, "$1 $2"))) i.checked = true; });
  };
  $("gSave").onclick = async () => {
    const on = [...document.querySelectorAll(".chkgrid input:checked")].map((i) => i.value), off = cast.map((c) => c.slug).filter((s) => !on.includes(s));
    await api(`/stories/${story.id}/cast-group`, { method: "PUT", body: { slugs: off, group: "" } });
    story = await api(`/stories/${story.id}/cast-group`, { method: "PUT", body: { slugs: on, group: $("gName").value.trim() || "Generic Characters" } });
    showCast("Characters");
  };
}
function showCast(tab = "Characters") {
  if (!story) return;
  const items = [];
  (story.turns || []).forEach((t) => (t.images || []).forEach((im) => items.push(im)));
  const gallery = items.length
    ? `<div class="gallery">${items.map((im) => `<figure><a href="${im.url}" target="_blank"><img loading="lazy" src="${thumbUrl(im.url, 360)}" alt=""></a><figcaption>${esc((im.scene || "").slice(0, 90))} <button class="x gdel" data-url="${esc(im.url)}" title="Delete">×</button></figcaption></figure>`).join("")}</div>`
    : '<p class="hint">No pictures yet. Use the 🎨 button under the input box to illustrate the latest reply.</p>';
  const card = (c) => `<button class="charcard" data-slug="${esc(c.slug)}">
    ${normalUrl(c) ? `<img loading="lazy" src="${thumbUrl(normalUrl(c), 420)}" alt="">` : '<div class="noart">No art yet</div>'}
    <div class="meta"><b>${esc(c.name)}</b><small>${esc(oneLine(c.bio || c.appearance, 60) || "no description yet")}</small>${(c.events || []).length ? `<small class="evn">📜 ${c.events.length} event${c.events.length > 1 ? "s" : ""}</small>` : ""}</div></button>`;
  modal(`<h2>Cast &amp; gallery</h2>
    <div class="tabs"><button class="tab ctab" data-tab="Characters">Characters (${(story.cast || []).length})</button><button class="tab ctab" data-tab="Relationships">Relationships</button><button class="tab ctab" data-tab="Gallery">Gallery (${items.length})</button><button class="tab ctab" data-tab="Intro">Intro panels (${(story.intro_images || []).length})</button></div>
    <div id="cTabCharacters"><p class="hint">Click a character to see their bio, key events and all their pictures. The card shows their normal picture.</p>
      <div class="cardgrid">${castCards(card)}<button class="charcard addcard" id="cAdd"><div class="noart">＋ Add character</div></button></div>
      <details class="fold"><summary>Cast tools</summary>
        <div class="tooltable">
          <button id="cImport">Import portraits…</button><span class="hint">Load a folder of pictures: one folder per character, one file per mood.</span>
          <button id="cFindArt">Find characters without art…</button><span class="hint">Lists named characters in the story that have no pictures and offers to draw them.</span>
          <button id="cSuggest">Suggest from the prompt</button><span class="hint">Reads the story prompt and proposes cast members with descriptions.</span>
          <button id="cGroup">Group characters…</button><span class="hint">Shows extras (guards, zombies…) as one card.<label class="chk" style="margin:4px 0 0"><input type="checkbox" id="cGroupOn" ${story.group_cast !== false ? "checked" : ""}> Show groups as one card</label></span>
          <button id="cVoices">Voices…</button><span class="hint">Choose read-aloud voices for the narrator and each character.</span>
        </div></details></div>
    <div id="cTabRelationships" class="hidden"><p class="hint">How each character feels about you over time, read from the story\u2019s status panels (or the state block). Click a name to open their page.</p><div id="relBox">Loading…</div></div>
    <div id="cTabGallery" class="hidden">${gallery}</div>
    <div id="cTabIntro" class="hidden"><p class="hint">Intro / webtoon panels. Put <code>![](intro:Name)</code> in the story's opening scene (✎ Edit) to show one; the name is the file name without its extension. The folder import fills these in automatically.</p>
      <div class="gallery">${(story.intro_images || []).map((p) => `<figure><a href="${p.url}" target="_blank"><img loading="lazy" src="${thumbUrl(p.url, 360)}" alt=""></a><figcaption>${esc(p.name)} <button class="x idel" data-url="${esc(p.url)}" title="Remove this panel">×</button></figcaption></figure>`).join("") || '<p class="hint">No panels yet.</p>'}</div>
      <label>Add panels (you can pick several; the file name becomes the panel name)</label><input type="file" id="iAdd" multiple accept="image/png,image/jpeg,image/webp,image/gif"></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  const show = (n) => {
    document.querySelectorAll(".ctab").forEach((b) => b.classList.toggle("on", b.dataset.tab === n));
    for (const t of ["Characters", "Relationships", "Gallery", "Intro"]) $("cTab" + t).classList.toggle("hidden", t !== n);
  };
  let relLoaded = false;
  document.querySelectorAll(".ctab").forEach((b) => b.onclick = async () => {
    show(b.dataset.tab);
    if (b.dataset.tab === "Relationships" && !relLoaded) { relLoaded = true; $("relBox").innerHTML = relationshipsHtml(await api(`/stories/${story.id}/relationships`)); $("relBox").querySelectorAll("[data-slug]").forEach((x) => x.onclick = () => showCharacter(x.dataset.slug)); }
  });
  show(tab);
  $("mClose").onclick = () => { closeModal(); renderAll(); refreshArtSuggestions(); };
  document.querySelectorAll(".charcard[data-slug]").forEach((b) => b.onclick = () => showCharacter(b.dataset.slug));
  document.querySelectorAll(".charcard[data-group]").forEach((b) => b.onclick = () => showCastGroup(b.dataset.group));
  $("cGroup").onclick = () => showGroupEditor();
  $("cVoices").onclick = () => showVoices();
  $("cGroupOn").onchange = async (e) => { story = await api(`/stories/${story.id}/group-cast?on=${e.target.checked}`, { method: "PUT" }); showCast("Characters"); };
  $("cAdd").onclick = () => showCharacter(null);
  $("cImport").onclick = () => showImportPortraits();
  $("cFindArt").onclick = () => showArtDialog({});
  $("cSuggest").onclick = async (e) => {
    const btn = e.target; btn.disabled = true; btn.textContent = "Reading your story prompt…";
    try {
      const r = await api(`/stories/${story.id}/cast/suggest`, { method: "POST" });
      const have = new Set((story.cast || []).map((c) => c.name.toLowerCase()));
      const fresh = r.cast.filter((c) => !have.has(c.name.toLowerCase()));
      if (!fresh.length) alert("Every character the story prompt names is already in your cast.");
      else if (confirm(`Add ${fresh.length} characters from your story prompt?\n\n${fresh.map((c) => c.name).join(", ")}`)) {
        story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: [...story.cast.map((c) => ({ name: c.name, appearance: c.appearance, slug: c.slug })), ...fresh] } });
      }
    } catch (err) { alert(err.message); }
    showCast("Characters");
  };
  $("cTabIntro").addEventListener("click", async (e) => {
    if (!e.target.classList.contains("idel") || !confirm("Remove this panel?")) return;
    story = await api(`/stories/${story.id}/intro?url=${encodeURIComponent(e.target.dataset.url)}`, { method: "DELETE" }); showCast("Intro"); renderAll();
  });
  $("iAdd").onchange = async (e) => {
    for (const f of [...e.target.files]) {
      const res = await fetch(`/api/stories/${story.id}/intro?name=${encodeURIComponent(f.name.replace(/\.[^.]+$/, ""))}`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!res.ok) { alert((await res.json().catch(() => ({}))).detail || "Upload failed"); break; }
      story = await res.json();
    }
    showCast("Intro"); renderAll();
  };
  $("cTabGallery").addEventListener("click", async (e) => {
    if (!e.target.classList.contains("gdel") || !confirm("Delete this picture?")) return;
    story = await api(`/stories/${story.id}/images?url=${encodeURIComponent(e.target.dataset.url)}`, { method: "DELETE" });
    showCast("Gallery"); renderAll();
  });
}

function showCharacter(slug, tab = "Bio") {
  if (!story) return;
  const ch = slug ? (story.cast || []).find((c) => c.slug === slug) : null;
  if (slug && !ch) return showCast();
  const isNew = !ch, c = ch || { name: "", appearance: "", bio: "", events: [], moods: {}, alts: {} };
  const saved = () => (isNew ? null : (story.cast || []).find((x) => x.slug === slug));
  const castPayload = (edit) => (story.cast || []).map((x) => (x.slug === slug && edit ? edit : { name: x.name, appearance: x.appearance, slug: x.slug }));

  // ----- Images tab pieces
  const moodChips = () => {
    const cur = Object.entries(c.moods || {}).map(([m, u]) =>
      `<figure class="mood"><img loading="lazy" src="${thumbUrl(u, 260)}" alt=""><figcaption>${esc(m)} <button class="x mregen" data-mood="${esc(m)}" title="Draw another version. The current one is kept as an earlier version.">↻</button><button class="x mdel" data-mood="${esc(m)}" title="Remove this picture">×</button></figcaption></figure>`).join("");
    const old = Object.entries(c.alts || {}).flatMap(([m, us]) => us.map((u) =>
      `<figure class="mood alt"><img loading="lazy" src="${thumbUrl(u, 200)}" alt=""><figcaption>${esc(m)} (earlier) <button class="x altuse" data-mood="${esc(m)}" data-url="${esc(u)}" title="Make this the active picture">use</button><button class="x altdel" data-url="${esc(u)}" title="Delete">×</button></figcaption></figure>`)).join("");
    return (cur + old) || '<span class="hint">No pictures yet. Use “Draw options…” to pick a face, or upload your own.</span>';
  };
  const eventRow = (e) => `<div class="evrow" data-id="${esc(e.id)}"><span class="evwhen">${esc(e.when || "·")}</span><span class="evtext">${esc(e.text)}</span>${e.auto ? '<span class="evauto" title="Logged automatically">auto</span>' : ""}<button class="x evedit" title="Edit">✎</button><button class="x evdel" title="Delete">×</button></div>`;

  modal(`<div class="charhead"><button id="chBack">← Cast</button><h2>${esc(c.name || "New character")}</h2></div>
    <div class="tabs"><button class="tab chtab" data-tab="Bio">Bio</button><button class="tab chtab" data-tab="Events"${isNew ? " disabled" : ""}>Key events (${(c.events || []).length})</button><button class="tab chtab" data-tab="Images"${isNew ? " disabled" : ""}>Images (${Object.keys(c.moods || {}).length})</button></div>

    <div id="chBio">
      <div class="bioTop">${normalUrl(c) ? `<img class="bioimg" src="${thumbUrl(normalUrl(c), 480)}" alt="">` : ""}<div style="flex:1">
        <label>Name</label><input id="chName" value="${esc(c.name)}" placeholder="Name">
        <label>How they look <small class="hint">(what the artist is told: age, hair, eyes, build, clothing, distinctive features)</small></label><textarea class="short" id="chApp" rows="3">${esc(c.appearance)}</textarea>
        <div class="actions" style="justify-content:flex-start;margin:4px 0 0"><button id="chAppSuggest" ${isNew ? "disabled" : ""}>Suggest from the story</button></div></div></div>
      <label>Personality <small class="hint">(traits, how they speak, what they want)</small></label><input id="chPers" value="${esc(c.personality || "")}">
      <label>Bio <small class="hint">(who they are in your story: role, history, relationships)</small></label><textarea class="short" id="chBioText" rows="5">${esc(c.bio || "")}</textarea>
      <label class="chk top"><input type="checkbox" id="chAuthor" ${c.author ? "checked" : ""}><span><b>Tell the narrator about them</b><span class="hint">Sends this character's looks, personality and bio to the narrator every turn. Off: the bio is only shown here.</span></span></label>
      <div class="actions" style="justify-content:flex-start;margin:4px 0 0"><button id="chBioSuggest" ${isNew ? "disabled" : ""}>Suggest a bio from the story</button></div>
      <div id="chPromptLines"></div><div class="aStatus hint" id="chStatus"></div>
      <div class="actions">${isNew ? "" : '<button class="danger" id="chRemove">Remove character</button><span style="flex:1"></span>'}<button class="primary" id="chSave">${isNew ? "Create character" : "Save"}</button></div></div>

    <div id="chEvents" class="hidden">
      <p class="hint">Important moments for ${esc(c.name || "this character")}. New ones are added automatically after each reply (change this in Settings); you can edit, delete or add your own.</p>
      <div id="evList">${(c.events || []).map(eventRow).join("") || '<p class="hint">Nothing logged yet.</p>'}</div>
      <div class="evadd"><input id="evWhen" placeholder="When (e.g. Turn 42)" style="width:150px"><input id="evText" placeholder="What happened" style="flex:1"><button id="evAdd">Add</button></div>
      <div class="actions" style="justify-content:flex-start"><button id="evRebuild" title="Read the story so far and write this character's event log (your own entries are kept)">Rebuild from the story</button><span class="hint" id="evStatus"></span></div></div>

    <div id="chImages" class="hidden">
      <p class="hint">The card shows the <b>normal</b> picture. Other pictures are moods the narrator can pick above this character's lines. ↻ draws another version and keeps the old one as an earlier version; every picture helps keep their face consistent.</p>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="imOpts">Draw options…</button>
        <select id="imSet"><option value="basic">Basic set</option><option value="extended">Extended set</option><option value="match">Moods my other characters have</option></select><button id="imSetGo">Draw the set</button></div>
      <div class="castact" style="margin:6px 0"><input id="imMood" value="normal 1" size="12" title="Picture name / mood"><button id="imDraw">Draw this one with AI</button><input type="file" id="imFile" accept="image/png,image/jpeg,image/webp,image/gif"></div>
      <div class="aStatus hint" id="imStatus"></div>
      <div class="moods" id="imGrid">${moodChips()}</div>
      ${config.reuse_art && !isNew ? `<h3>References from other stories</h3>
        <div class="hint">Borrow a character's pictures from another story as a face reference. Only their face and hair are kept; each story's own art style and setting apply, so the result looks like this story. Used when this character has no pictures of their own yet, and as extra references afterwards.</div>
        <div id="imExtList" class="moods">${(c.ext_refs || []).map((r) => `<figure class="mood alt"><img loading="lazy" src="${thumbUrl(r.url, 200)}" alt=""><figcaption title="${esc(r.source)}">${esc(oneLine(r.source, 28))} <button class="x extdel" data-url="${esc(r.url)}" title="Remove this reference">×</button></figcaption></figure>`).join("")}</div>
        <div class="actions" style="justify-content:flex-start"><select id="imExtSel"><option value="">Loading other stories…</option></select><button id="imExtAdd">Use as reference</button><span class="hint" id="imExtNote"></span></div>` : ""}</div>

    <div class="actions" style="margin-top:6px"><button id="mClose">Close</button></div>`);

  if ($("imExtSel")) {
    api(`/ref-sources?exclude=${story.id}`).then((src) => {
      $("imExtSel").innerHTML = src.length ? src.map((s) => `<optgroup label="${esc(s.title)}">${s.characters.map((x) => `<option value="${esc(s.story_id)}|${esc(x.slug)}">${esc(x.name)} (${x.count} pictures)</option>`).join("")}</optgroup>`).join("") : '<option value="">No other story has character pictures yet</option>';
    });
    $("imExtAdd").onclick = async () => {
      const v = $("imExtSel").value; if (!v) return; const [from_story, from_slug] = v.split("|");
      try { story = await api(`/stories/${story.id}/cast/${slug}/ext-refs`, { method: "POST", body: { from_story, from_slug } }); refresh("Images"); } catch (err) { $("imExtNote").textContent = err.message; }
    };
    document.querySelectorAll(".extdel").forEach((b) => b.onclick = async () => { story = await api(`/stories/${story.id}/cast/${slug}/ext-refs?url=${encodeURIComponent(b.dataset.url)}`, { method: "DELETE" }); refresh("Images"); });
  }
  const show = (n) => {
    document.querySelectorAll(".chtab").forEach((b) => b.classList.toggle("on", b.dataset.tab === n));
    for (const t of ["Bio", "Events", "Images"]) $("ch" + t).classList.toggle("hidden", t !== n);
  };
  document.querySelectorAll(".chtab").forEach((b) => b.onclick = () => !b.disabled && show(b.dataset.tab));
  show(isNew ? "Bio" : tab);
  $("chBack").onclick = () => showCast("Characters");
  $("mClose").onclick = () => { closeModal(); renderAll(); refreshArtSuggestions(); };
  const status = (id, t) => { const el = $(id); if (el) el.textContent = t; };
  const refresh = (t) => showCharacter(slug, t);

  // ----- Bio
  if (!isNew) api(`/stories/${story.id}/cast/${slug}/prompt-lines`).then((r) => {
    const box = $("chPromptLines"); if (!box || !r.lines.length) return;
    box.innerHTML = `<p class="hint" style="margin-bottom:2px">What your story prompt says about them:</p>` + r.lines.map((l, i) => `<div class="pline"><span>${esc(oneLine(l, 260))}</span><button class="usebio" data-i="${i}">Use as bio</button></div>`).join("");
    box.querySelectorAll(".usebio").forEach((b) => b.onclick = () => { $("chBioText").value = r.lines[+b.dataset.i]; });
  }).catch(() => {});
  $("chSave").onclick = async () => {
    const name = $("chName").value.trim(); if (!name) { status("chStatus", "Give the character a name."); return; }
    const edit = { name, appearance: $("chApp").value, bio: $("chBioText").value, personality: $("chPers").value, author: $("chAuthor").checked, slug: isNew ? null : slug };
    try {
      story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: isNew ? [...castPayload(null), edit] : castPayload(edit) } });
      const made = isNew ? story.cast.find((x) => x.name === name) : null;
      renderAll(); showCharacter(isNew ? (made ? made.slug : null) : slug, "Bio");
      if (isNew && !made) showCast();
    } catch (err) { status("chStatus", err.message); }
  };
  if (!isNew) {
    $("chRemove").onclick = async () => {
      if (!confirm(`Remove ${c.name} and all their pictures from this story?`)) return;
      story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: castPayload(null).filter((x) => x.slug !== slug) } }); renderAll(); showCast();
    };
    $("chAppSuggest").onclick = async (e) => {
      e.target.disabled = true; status("chStatus", "Reading what the story says about them…");
      try { const r = await api(`/stories/${story.id}/cast/describe`, { method: "POST", body: { name: c.name } }); $("chApp").value = r.appearance; status("chStatus", "Edit it if it isn't what you picture, then Save."); }
      catch (err) { status("chStatus", err.message); }
      e.target.disabled = false;
    };
    $("chBioSuggest").onclick = async (e) => {
      e.target.disabled = true; status("chStatus", "Reading what the story says about them…");
      try { const r = await api(`/stories/${story.id}/cast/${slug}/bio-suggest`, { method: "POST" }); $("chBioText").value = r.bio; status("chStatus", "Edit it if it isn't right, then Save."); }
      catch (err) { status("chStatus", err.message); }
      e.target.disabled = false;
    };

    // ----- Key events
    $("chEvents").addEventListener("click", async (e) => {
      const b = e.target.closest("button"), row = e.target.closest(".evrow");
      if (!b || !row) return;
      const ev = (saved().events || []).find((x) => x.id === row.dataset.id); if (!ev) return;
      if (b.classList.contains("evdel")) { story = await api(`/stories/${story.id}/cast/${slug}/events/${ev.id}`, { method: "DELETE" }); refresh("Events"); }
      else if (b.classList.contains("evedit")) {
        row.innerHTML = `<input class="evWhenE" value="${esc(ev.when)}" style="width:130px"><input class="evTextE" value="${esc(ev.text)}" style="flex:1"><button class="evsave primary">Save</button>`;
      } else if (b.classList.contains("evsave")) {
        story = await api(`/stories/${story.id}/cast/${slug}/events/${ev.id}`, { method: "PUT", body: { when: row.querySelector(".evWhenE").value, text: row.querySelector(".evTextE").value } }); refresh("Events");
      }
    });
    $("evAdd").onclick = async () => {
      const text = $("evText").value.trim(); if (!text) return;
      story = await api(`/stories/${story.id}/cast/${slug}/events`, { method: "POST", body: { when: $("evWhen").value, text } }); refresh("Events");
    };
    $("evRebuild").onclick = async (e) => {
      if (!confirm("Rebuild the automatic entries from the story so far? Your own entries are kept. This uses one text-model call.")) return;
      e.target.disabled = true; status("evStatus", "Reading the story…");
      try { story = await api(`/stories/${story.id}/cast/${slug}/events/rebuild`, { method: "POST" }); refresh("Events"); }
      catch (err) { status("evStatus", err.message); e.target.disabled = false; }
    };

    // ----- Images
    const busy = async (btn, label, fn) => { const old = btn.textContent; btn.disabled = true; btn.textContent = label; try { await fn(); } catch (err) { alert(err.message); } refresh("Images"); };
    $("imOpts").onclick = () => showArtDialog({ only: c.name, onClose: () => { renderAll(); showCharacter(slug, "Images"); } });
    $("imDraw").onclick = (e) => busy(e.target, "Drawing…", async () => { story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood: $("imMood").value || "normal 1" } }); });
    $("imFile").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const res = await fetch(`/api/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(($("imMood").value || "normal 1").trim())}`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!res.ok) alert((await res.json().catch(() => ({}))).detail || "Upload failed"); else story = await res.json();
      refresh("Images");
    };
    $("imSetGo").onclick = async (e) => {
      e.target.disabled = true; status("imStatus", "Starting…");
      try {
        const r = await api(`/stories/${story.id}/cast/${slug}/draw-set`, { method: "POST", body: { preset: $("imSet").value } });
        const job = await watchJob(r.job, (j) => { artBusyText = `Drawing ${c.name}'s pictures: ${j.done} of ${j.total}`; status("imStatus", `${j.current} (${j.done} of ${j.total} done)`); refreshArtSuggestions(); });
        artBusyText = ""; story = await api(`/stories/${story.id}`); renderAll();
        if (job.status === "error") alert("The set stopped: " + job.error);
      } catch (err) { artBusyText = ""; alert(err.message); }
      refreshArtSuggestions();
      refresh("Images");
    };
    $("imGrid").addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.classList.contains("mregen")) busy(b, "…", async () => { story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood: b.dataset.mood } }); });
      else if (b.classList.contains("mdel")) { story = await api(`/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(b.dataset.mood)}`, { method: "DELETE" }); refresh("Images"); }
      else if (b.classList.contains("altuse")) { story = await api(`/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(b.dataset.mood)}/use?url=${encodeURIComponent(b.dataset.url)}`, { method: "POST" }); refresh("Images"); }
      else if (b.classList.contains("altdel")) { if (!confirm("Delete this earlier version?")) return; story = await api(`/stories/${story.id}/cast/${slug}/alt?url=${encodeURIComponent(b.dataset.url)}`, { method: "DELETE" }); refresh("Images"); }
    });
  }
}

// ---------- image provider settings (inside Settings) ----------
function imgProvCard(p, i) {
  const comfy = p.type === "comfyui", oa = p.type === "openai";
  return `<div class="iprov" data-i="${i}"><label><b>${esc(p.name)}</b> <small>(${p.type})</small></label>
    <div class="grid2" style="grid-template-columns:2fr 1fr"><div><label>Address</label><input data-f="base_url" value="${esc(p.base_url || "")}"></div>
      <div><label>${comfy ? "Checkpoint file" : "Model"}</label><input data-f="model" value="${esc(p.model || "")}" placeholder="${comfy ? "e.g. illustriousXL.safetensors" : ""}"></div></div>
    <label>Prompt style</label><select data-f="prompt_style">
      <option value="natural" ${(p.prompt_style || (comfy ? "tags" : "natural")) === "natural" ? "selected" : ""}>Sentences (Gemini, OpenAI, xAI, most new models)</option>
      <option value="tags" ${(p.prompt_style || (comfy ? "tags" : "natural")) === "tags" ? "selected" : ""}>Tags (anime SDXL models: Animagine, Illustrious, NoobAI)</option></select>
    <label>Reference pictures</label><select data-f="ref_mode">
      <option value="characters" ${(p.ref_mode || (comfy ? "one_character" : "characters")) === "characters" ? "selected" : ""}>One saved picture of each character in the scene (online services)</option>
      <option value="one_character" ${(p.ref_mode || (comfy ? "one_character" : "characters")) === "one_character" ? "selected" : ""}>Several saved pictures of the main character (face-matching workflows)</option></select>
    ${comfy ? "" : `<label>API key ${p.has_key ? "(saved; leave blank to keep)" : ""}</label><input data-f="api_key" type="password" placeholder="${p.has_key ? "••••••••" : "paste key"}">`}
    ${oa ? `<label class="chk"><input type="checkbox" data-f="send_size" ${p.send_size !== false ? "checked" : ""}> Send the picture size</label>
            <label class="chk"><input type="checkbox" data-f="allow_refs" ${p.allow_refs !== false ? "checked" : ""}> Accepts reference pictures (keeps faces consistent)</label>` : ""}
    ${comfy ? `<label>Workflow: ComfyUI "Save (API Format)" JSON. Placeholders: {{prompt}} {{negative}} {{seed}} {{width}} {{height}} {{model}}</label>
      <textarea class="short" data-f="workflow" rows="5" placeholder="Empty = the built-in starter (SDXL text-to-image)">${esc(p.workflow || "")}</textarea>
      <button class="starter" type="button">Insert starter workflow</button>
      <label>Workflow used when reference pictures are available: matches faces to a character's portrait (needs {{ref1}} in a LoadImage node)</label>
      <textarea class="short" data-f="workflow_ref" rows="5">${esc(p.workflow_ref || "")}</textarea>
      <button class="starterface" type="button">Insert face-matching (IPAdapter) workflow</button>` : ""}
    <div class="actions" style="margin-top:8px"><button class="itest" type="button">Test: draw a small picture</button></div><div class="itestout"></div></div>`;
}
function imageSettingsHtml(cfg) {
  const provs = cfg.image_providers || [];
  return `<h3>Picture settings</h3>
    <div class="hint">Which AI draws pictures is chosen under <b>AI &amp; story</b>. Online services need their own API key and bill separately from Claude; ComfyUI runs on your own computer for free.</div>
    <div class="grid2"><div class="field"><label>Reference pictures per scene</label><input id="imgRefs" type="number" min="0" max="8" value="${cfg.image_max_refs ?? 3}"><div class="hint">Saved portraits sent along so faces stay consistent.</div></div><div></div><div></div></div>
    <h3>Pictures drawn for a new character</h3>
    <div class="hint">After you pick a face for a new character, the app can draw more pictures of them from these lists (one mood per line). Only moods that work for one person are used: normal, smile, shy, surprise, sulky, fear, sad, angry, scorn, despair, combat, cute, dead, zombie.</div>
    <div class="grid2" style="grid-template-columns:1fr 1fr"><div class="field"><label>Basic set</label><textarea class="short" id="msBasic" rows="6">${esc(((cfg.mood_sets || {}).basic || []).join("\n"))}</textarea></div>
      <div class="field"><label>Extended set</label><textarea class="short" id="msExt" rows="6">${esc(((cfg.mood_sets || {}).extended || []).join("\n"))}</textarea></div></div>
    <h3>Picture services</h3>
    <div class="hint">Model names change often: if one stops working, check the service's docs and edit its Model field, then use Test.</div>
    <details class="fold"><summary>Show settings for each picture service (address, model, ComfyUI workflow…)</summary><div id="iprovs">${provs.map(imgProvCard).join("")}</div></details>`;
}
function bindImageSettings(cfg) {
  document.querySelectorAll(".iprov").forEach((card) => {
    const p = cfg.image_providers[+card.dataset.i];
    const st = card.querySelector(".starter");
    const sf = card.querySelector(".starterface");
    if (sf) sf.onclick = async () => { card.querySelector('[data-f="workflow_ref"]').value = JSON.stringify(await api("/images/starter-workflow?kind=face"), null, 2); };
    if (st) st.onclick = async () => { card.querySelector('[data-f="workflow"]').value = JSON.stringify(await api("/images/starter-workflow"), null, 2); };
    card.querySelector(".itest").onclick = async (e) => {
      const out = card.querySelector(".itestout"), btn = e.target; btn.disabled = true; out.textContent = "Drawing…";
      const settings = {};
      card.querySelectorAll("[data-f]").forEach((inp) => settings[inp.dataset.f] = inp.type === "checkbox" ? inp.checked : inp.value);
      try { const r = await api("/image-test", { method: "POST", body: { provider: p.id, settings } }); out.innerHTML = `<img class="mood" style="height:140px;border-radius:8px" src="${r.image}" alt="">`; }
      catch (err) { out.textContent = "Failed: " + err.message; }
      btn.disabled = false;
    };
  });
}
function collectImageSettings(cfg) {
  const lines = (id) => $(id).value.split("\n").map((x) => x.trim().toLowerCase()).filter(Boolean);
  cfg.mood_sets = { basic: lines("msBasic"), extended: lines("msExt") };
  cfg.image_max_refs = +$("imgRefs").value || 0;
  document.querySelectorAll(".iprov").forEach((card) => {
    const p = cfg.image_providers[+card.dataset.i];
    card.querySelectorAll("[data-f]").forEach((inp) => { p[inp.dataset.f] = inp.type === "checkbox" ? inp.checked : inp.value; });
  });
}

// ---------- offer art for named characters that have none ----------
// Free detection on the server: cast entries with a description but no pictures, and new names the narrator introduced.
// For each one you can edit the description, add notes, draw options, pick one (and optionally the rest of a set), skip, or say never.
const artSnoozed = {};        // storyId -> Set of lower-case names skipped for this session
let artBusyText = "";         // shown in the banner while a background drawing job runs

async function refreshArtSuggestions() {
  const el = document.getElementById("artBanner");
  if (!el) return;
  if (!story) { el.classList.add("hidden"); return; }
  if (artBusyText) { el.innerHTML = `<span>🎨 ${esc(artBusyText)}</span>`; el.classList.remove("hidden"); return; }
  let s;
  try { s = await api(`/stories/${story.id}/art-suggestions`); } catch (e) { return; }
  const snoozed = artSnoozed[story.id] || (artSnoozed[story.id] = new Set());
  const items = [...s.missing_art, ...s.new].filter((c) => !snoozed.has(c.name.toLowerCase()));
  if (!s.enabled || !items.length) { el.classList.add("hidden"); return; }
  const names = items.slice(0, 4).map((c) => c.name).join(", ") + (items.length > 4 ? ` and ${items.length - 4} more` : "");
  el.innerHTML = `<span>🎨 ${items.length} named character${items.length > 1 ? "s" : ""} without art: <b>${esc(names)}</b></span><button id="abReview" class="primary">Review…</button><button id="abLater">Not now</button>`;
  el.classList.remove("hidden");
  document.getElementById("abReview").onclick = () => showArtDialog({});
  document.getElementById("abLater").onclick = () => { items.forEach((c) => snoozed.add(c.name.toLowerCase())); refreshArtSuggestions(); };
}

async function watchJob(id, onUpdate) {
  for (;;) {
    const j = await api(`/jobs/${id}`);
    if (onUpdate) onUpdate(j);
    if (j.status !== "running") return j;
    await new Promise((r) => setTimeout(r, 1500));
  }
}

async function showArtDialog(opts = {}) {
  if (!story) return;
  const snoozed = artSnoozed[story.id] || (artSnoozed[story.id] = new Set());
  let items;
  if (opts.only) {
    const c = (story.cast || []).find((x) => x.name.toLowerCase() === opts.only.toLowerCase());
    items = [{ name: opts.only, appearance: c ? c.appearance : "", mentions: 0, sample: "" }];
  } else {
    const s = await api(`/stories/${story.id}/art-suggestions`);
    items = [...s.missing_art, ...s.new].filter((c) => !snoozed.has(c.name.toLowerCase()));
  }
  const card = (c, i) => `<div class="artcard" data-i="${i}" data-name="${esc(c.name)}">
    <div class="arthead"><b>${esc(c.name)}</b> <small>${c.mentions ? `speaks or appears ${c.mentions} time${c.mentions > 1 ? "s" : ""} in the story, no art yet` : ((story.cast || []).some((x) => x.name === c.name && Object.keys(x.moods || {}).length) ? "new look" : "no art yet")}</small>
      ${c.sample ? `<div class="hint">e.g. ${esc(c.sample)}</div>` : ""}</div>
    <label>How they look (edit freely; this is what the artist is told)</label>
    <textarea class="short aDesc" rows="3" placeholder="age, hair, eyes, build, clothing, distinctive features…">${esc(c.appearance || "")}</textarea>
    <div class="actions" style="justify-content:flex-start;margin:4px 0 0"><button class="aDescribe" title="Have the text model write this from what the story says about them">Suggest from the story</button></div>
    <label>Notes for this drawing (optional, e.g. “glasses, scar over the left eye, darker hair”)</label><input class="aNotes">
    <div class="actions"><button class="aSkip">Not now</button><button class="aNever danger" title="Never ask about this character again">Never</button><button class="primary aDraw">Draw 4 options</button></div>
    <div class="aStatus hint"></div><div class="aResult"></div></div>`;
  modal(`<h2>${opts.only ? `Pictures for ${esc(opts.only)}` : "Art for new characters"}</h2>
    <p class="hint">Edit the description to match what you have in mind, add notes, and the app draws four options to choose from. Not what you wanted? Change the text and draw again. Nothing is saved to the character until you pick one.</p>
    <div id="artCards">${items.map(card).join("") || '<p class="hint">Every named character already has art.</p>'}</div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  document.getElementById("mClose").onclick = () => { refreshArtSuggestions(); if (opts.onClose) opts.onClose(); else closeModal(); };
  const cands = {};   // name -> candidate urls drawn so far

  const showCandidates = (el, name) => {
    const urls = cands[name] || [];
    el.querySelector(".aResult").innerHTML = urls.length ? `<div class="gallery">${urls.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="aUse primary" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <label>After you pick one, also draw</label><select class="aSet"><option value="none">No more pictures (just this one)</option><option value="basic" selected>The basic set</option><option value="extended">The extended set</option><option value="match">The moods my other characters have</option></select>
      <div class="actions" style="justify-content:flex-start"><button class="aMore">Draw 4 more options</button></div>` : "";
  };

  const draw = async (el) => {
    const name = el.dataset.name, desc = el.querySelector(".aDesc").value.trim(), notes = el.querySelector(".aNotes").value.trim();
    const status = el.querySelector(".aStatus"), btn = el.querySelector(".aDraw");
    if (!desc) { status.textContent = "Describe how they look first, or press “Suggest from the story”."; return; }
    btn.disabled = true; status.textContent = "Starting…";
    try {
      const r = await api(`/stories/${story.id}/cast/candidates`, { method: "POST", body: { name, appearance: desc, notes, count: 4 } });
      const job = await watchJob(r.job, (j) => { if (document.body.contains(status)) status.textContent = j.current || "Drawing…"; });
      if (job.status === "error") { status.textContent = "Could not draw: " + job.error; btn.disabled = false; return; }
      cands[name] = (cands[name] || []).concat(job.result.candidates);
      story = await api(`/stories/${story.id}`);
      status.textContent = "Pick one, or change the description or notes and draw again.";
      showCandidates(el, name);
    } catch (e) { status.textContent = "Could not draw: " + e.message; }
    btn.disabled = false;
  };

  const use = async (el, url) => {
    const name = el.dataset.name, status = el.querySelector(".aStatus"), set = el.querySelector(".aSet").value;
    const ch = story.cast.find((c) => c.name.toLowerCase() === name.toLowerCase());
    try {
      story = await api(`/stories/${story.id}/cast/${ch.slug}/choose`, { method: "POST", body: { url, mood: "normal 1" } });
      el.querySelector(".aResult").innerHTML = ""; el.querySelectorAll("button, textarea, input").forEach((x) => x.disabled = true);
      status.textContent = `✔ ${name} now has a picture.`;
      renderAll();
      if (set !== "none") {
        const r = await api(`/stories/${story.id}/cast/${ch.slug}/draw-set`, { method: "POST", body: { preset: set } });
        const job = await watchJob(r.job, (j) => {
          artBusyText = `Drawing ${name}'s pictures: ${j.done} of ${j.total}`;
          if (document.body.contains(status)) status.textContent = `✔ Chosen. ${j.current} (${j.done} of ${j.total} done)`;
          refreshArtSuggestions();
        });
        artBusyText = "";
        story = await api(`/stories/${story.id}`); renderAll();
        if (document.body.contains(status)) status.textContent = job.status === "error" ? `✔ Chosen. The extra pictures stopped: ${job.error}` : `✔ ${name} is done: ${job.result.drawn.length} extra pictures drawn.`;
      }
    } catch (e) { artBusyText = ""; status.textContent = "Could not finish: " + e.message; }
    refreshArtSuggestions();
  };

  document.getElementById("artCards").addEventListener("click", async (e) => {
    const b = e.target.closest("button"), el = e.target.closest(".artcard");
    if (!b || !el) return;
    const name = el.dataset.name;
    if (b.classList.contains("aDraw") || b.classList.contains("aMore")) draw(el);
    else if (b.classList.contains("aUse")) use(el, b.dataset.url);
    else if (b.classList.contains("aSkip")) { snoozed.add(name.toLowerCase()); el.remove(); refreshArtSuggestions(); }
    else if (b.classList.contains("aNever")) {
      if (!confirm(`Never offer art for ${name} again in this story?`)) return;
      await api(`/stories/${story.id}/art-suggestions/dismiss`, { method: "POST", body: { names: [name] } }); el.remove(); refreshArtSuggestions();
    } else if (b.classList.contains("aDescribe")) {
      const status = el.querySelector(".aStatus"); b.disabled = true; status.textContent = "Reading what the story says about them…";
      try { const r = await api(`/stories/${story.id}/cast/describe`, { method: "POST", body: { name } }); el.querySelector(".aDesc").value = r.appearance; status.textContent = "Edit it if it isn't what you picture."; }
      catch (err) { status.textContent = err.message; }
      b.disabled = false;
    }
  });
}

function relationshipsHtml(r) {
  if (!r.characters.length) return '<p class="hint">No relationship data yet. It appears once replies contain a status panel with ❤️ values, or the state block tracks affinity.</p>';
  const spark = (ser) => { if (ser.length < 2) return ""; const w = 120, h = 26, xs = ser.map((p) => p[0]), x0 = Math.min(...xs), x1 = Math.max(...xs) || 1, ys = ser.map((p) => p[1]), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const pts = ser.map((p) => `${((p[0] - x0) / Math.max(1, x1 - x0) * w).toFixed(1)},${(h - (p[1] - y0) / Math.max(1, y1 - y0) * (h - 4) - 2).toFixed(1)}`).join(" ");
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polyline points="${pts}" fill="none" stroke="var(--accent2)" stroke-width="1.5"/></svg>`; };
  return r.characters.map((c) => `<div class="relrow"><div class="relname">${c.slug ? `<a href="#" data-slug="${esc(c.slug)}">${esc(c.name)}</a>` : esc(c.name)}<small>${esc(c.location || "")}</small></div>
    <div class="relbar"><i style="width:${Math.min(100, 100 * c.value / r.max)}%"></i></div><div class="relval">${c.value}${c.delta ? `<small class="${c.delta > 0 ? "up" : "down"}">${c.delta > 0 ? "+" : ""}${c.delta}</small>` : ""}</div>${spark(c.series)}
    ${c.thought ? `<div class="relq">“${esc(c.thought)}”</div>` : ""}</div>`).join("");
}

// 🏁 the author's possible endings (hidden from the narrator's output; spoilers for the player)
function showEndings() {
  if (!story) return;
  const ends = story.endings || [], t = story.target_ending ?? -1;
  modal(`<h2>Possible endings</h2><p class="hint">⚠ Spoilers. These are the author's notes for how the story could end. The narrator may drift toward one when your choices lead there, and never reveals them. You can also ask the story to work toward one.</p>
    ${ends.map((e, i) => `<details class="fold"><summary>${i === t ? "🏁 " : ""}${esc(e.title || "Ending " + (i + 1))}${i === t ? " <small class=\"muted\">(the story is working toward this)</small>" : ""}</summary><div class="hint">${esc(e.text)}</div>
      <div class="actions" style="justify-content:flex-start">${i === t ? `<button data-end="-1">Stop steering toward it</button>` : `<button class="primary" data-end="${i}">Work toward this ending</button>`}</div></details>`).join("") || '<p class="hint">This story has no listed endings.</p>'}
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  document.querySelectorAll("[data-end]").forEach((b) => b.onclick = async () => { story = await api(`/stories/${story.id}/target-ending`, { method: "PUT", body: { index: +b.dataset.end } }); showEndings(); });
}
