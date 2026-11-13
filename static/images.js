"use strict";
// Image features: mood portraits in the story, scene illustrations, the Art tab, the Cast & gallery screen, and the
// image provider settings. Loaded before app.js; it uses app.js's globals (story, api, modal, esc, ...) at call time.

// ---------- portraits & scene pictures in the story ----------
// The narrator writes ![](portrait:Name/mood); show that character's picture for that mood (or their default).
// Small cached copies (w = longest side) keep screens with many big pictures fast; the original opens on click.
const thumbUrl = (u, w) => u ? `/api/thumb?url=${encodeURIComponent(u)}&w=${w}` : "";
function applyPortraitSize() {
  const sz = { small: "130px", medium: "200px", large: "300px", xl: "420px" }[((story && story.art) || {}).portrait_size || "medium"];
  document.getElementById("narrative").style.setProperty("--npcpic-h", sz);
}
// Intro / webtoon panels: ![](intro:It1) in the opening scene shows the story's panel named It1.
function introPanelHtml(name) {
  const im = ((story && story.intro_images) || []).find((p) => p.name.toLowerCase() === name.trim().toLowerCase());
  return im ? `<a href="${im.url}" target="_blank"><img class="intro-panel" src="${thumbUrl(im.url, 1100)}" alt=""></a>` : "";
}
// A place picture (![](place:Old library)): a fixed picture of a room or location the story returns to often.
function placeHtml(name) {
  const pl = ((story && story.places) || []).find((p) => p.url && p.name.toLowerCase() === name.trim().toLowerCase());
  return pl ? `<figure class="placepic"><img loading="lazy" src="${thumbUrl(pl.url, 1100)}" alt=""><figcaption>📍 ${esc(pl.name)}</figcaption></figure>` : "";
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
  const alts = story.turns.length ? [] : (story.alt_openings || []);
  if (alts.length) {   // the author gave other ways to begin: pick one before the first move (the two trade places, so you can switch back)
    h += `<div class="altopen"><b>How would you like to begin?</b><div class="altbtns"><button class="primary" disabled>${esc(story.opening_name || "Main opening")}</button>${alts.map((a, i) => `<button class="altpick" data-i="${i}">${esc(a.name)}</button>`).join("")}</div><div class="hint">Only before your first move. The opening scene below changes to match.</div></div>`;
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
// Same list as the server's EXPRESSION_MOODS: any other picture type is an action picture (drawn three-quarter, shown cropped to the upper body).
const EXPRESSION_MOODS = new Set(["default", "normal", "smile", "happy", "laugh", "laughing", "shy", "surprise", "surprised", "sulky", "fear", "scared", "sad", "angry", "scorn",
  "despair", "cute", "stern", "flustered", "unimpressed", "trusting", "determined", "worried", "confused", "bored", "disgusted", "embarrassed", "blush", "blushing",
  "cry", "crying", "smug", "curious", "proud", "nervous", "tired", "excited", "love", "flirty", "calm", "serious", "thinking", "pout", "annoyed", "suspicious",
  "relieved", "hopeful", "guilty", "jealous", "lonely", "grim", "cold", "zombie"]);
// Batch generation: every image category is a set to draw ("Draw all of Moods / Actions / Positions…"); adult categories only in adult stories.
function categorySetOptions(withPrefix) {
  return ((typeof config !== "undefined" && config && config.image_categories) || []).filter((c) => !c.nsfw || (story && story.nsfw)).map((c) => `<option value="cat:${esc(c.id)}">${withPrefix || ""}${esc(c.name)}${c.nsfw ? " (adult)" : ""}</option>`).join("");
}
function isActionMood(m) {
  if (!m) return false;
  const b = String(m).toLowerCase().replace(/\s*\d+$/, "").trim(), d = ((typeof config !== "undefined" && config && config.mood_defs) || {})[b];
  const cid = (d && d.category) || (EXPRESSION_MOODS.has(b) ? "expressions" : "actions");           // the category it is in (Image categories)
  const c = ((typeof config !== "undefined" && config && config.image_categories) || []).find((x) => x.id === cid);
  return c ? c.shot === "body" : !EXPRESSION_MOODS.has(b);
}
function portraitInfo(spec) {
  const i = spec.lastIndexOf("/");
  const name = (i < 0 ? spec : spec.slice(0, i)).trim().toLowerCase();
  const mood = (i < 0 ? "default" : spec.slice(i + 1)).trim().toLowerCase();
  const flat = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, "");   // "Tall Guard" = "TallGuard"
  const ch = ((story && story.cast) || []).find((c) => c.name.toLowerCase() === name || c.name.toLowerCase().split(" ")[0] === name || flat(c.name) === flat(name))
    || ((story && story.cast) || []).find((c) => (c.aka || []).some((a) => a.toLowerCase() === name || flat(a) === flat(name)));   // a nickname finds the character
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
  const crop = isActionMood(p.mood) && p.exact;     // action pictures (combat, dead, showering...): show the upper body here; a click opens the whole picture
  let h = p.url ? `<img class="npcpic${crop ? " upper" : ""}" loading="lazy" src="${thumbUrl(p.url, 640)}" alt="">` : "";
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
  const where = (story && story.art && story.art.scene_where) || "reply";
  const inPanel = where === "panel", banner = where === "banner_top" || where === "banner_bottom";   // panel: shown in the "Right now" card instead; banner: a wide strip
  let h = inPanel ? "" : (t.images || []).map((im, k) => `<figure class="scenefig${banner ? " banner" : ""} disp-${esc((story.art || {}).scene_display || "normal")}"><a href="${im.url}" target="_blank"><img class="scene" loading="lazy" src="${thumbUrl(im.url, 1400)}" alt="${esc(oneLine(im.scene || "", 120))}" title="${esc(oneLine(im.scene || "", 220))}"></a>
    <div class="scenebar"><button data-act="redraw" data-turn="${i}" data-img="${k}" title="Draw this scene again (the earlier picture stays)">↻ Redraw</button>
    <select class="reframe" data-turn="${i}" data-img="${k}" title="Draw this scene again in another shape (the earlier picture stays)"><option value="">⤢ Reframe…</option><option value="portrait">Tall (2:3)</option><option value="landscape">Wide (3:2)</option><option value="wide">Cinematic (16:9)</option><option value="square">Square</option></select>
    <button data-act="save" data-turn="${i}" data-img="${k}" title="Keep this picture as a portrait of a character">💾 Save as portrait</button>
    <button class="x" data-act="del" data-turn="${i}" data-img="${k}" title="Delete this picture">×</button></div></figure>`).join("");
  if (illustratingTurn === i) h += '<div class="drawing">🎨 Drawing…</div>';
  return h;
}

// After a new "normal 1": offer to delete the character's other pictures and redraw them all from the new face. Returns true if it ran.
async function offerRedrawAll(ch, onProgress) {
  const others = Object.keys(ch.moods || {}).filter((m) => m !== "normal 1");
  if (!others.length) return false;
  if (!confirm(`${ch.name} has ${others.length} other picture${others.length > 1 ? "s" : ""} (${others.slice(0, 6).join(", ")}${others.length > 6 ? "…" : ""}).\n\nRedraw them all to match the new Normal 1?\n\nOK deletes every other picture of ${ch.name} first (including earlier versions), then redraws them. Cancel keeps them as they are.`)) return false;
  const r = await api(`/stories/${story.id}/cast/${ch.slug}/redraw-all`, { method: "POST" });
  story = await api(`/stories/${story.id}`); renderAll();
  const job = await watchJob(r.job, (j) => { artBusyText = `Redrawing ${ch.name}'s pictures: ${j.done} of ${j.total}`; if (onProgress) onProgress(`${j.current} (${j.done} of ${j.total} done)`); refreshArtSuggestions(); });
  artBusyText = ""; story = await api(`/stories/${story.id}`); renderAll();
  if (job.status === "error") alert("The redraw stopped: " + job.error);
  return true;
}

async function illustrate(turnIdx = null, scene = null, shape = null) {
  if (!story || busy || illustratingTurn !== null) return;
  const idx = turnIdx ?? story.turns.map((t) => t.role).lastIndexOf("assistant");
  if (idx < 0) { alert("There is no narrator reply to illustrate yet."); return; }
  illustratingTurn = idx; renderNarrative(null, "keep"); renderSidebar();
  try { story = await api(`/stories/${story.id}/illustrate`, { method: "POST", body: { turn: idx, scene, ...(shape ? { shape } : {}) } }); }
  catch (e) { alert("Illustration failed: " + e.message); }
  illustratingTurn = null; renderAll();
}

document.getElementById("narrative").addEventListener("change", (e) => {
  const sel = e.target.closest("select.reframe"); if (!sel || !sel.value || !story || busy) return;
  const turn = +sel.dataset.turn, im = (story.turns[turn] || {}).images && story.turns[turn].images[+sel.dataset.img]; if (!im) return;
  illustrate(turn, im.scene, sel.value);
});

document.getElementById("narrative").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b || !story || busy) return;
  if (b.classList.contains("newmood")) {
    drawingNew = b.dataset.slug + "|" + b.dataset.mood; renderNarrative(null, "keep");
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

// ---------- art style dropdown on AI & story (under Pictures) ----------
// Offers the styles that make sense for what the app found: your local checkpoint(s) and any online picture service with a key.
function mountArtStyle(el, picker, current) {
  let value = current || "";
  const render = () => {
    const o = picker.opts(); if (!o) { el.innerHTML = ""; return; }
    const ch = picker.choice("images"), imgs = o.images || [];
    const online = imgs.some((p) => !p.local && p.has_key), local = imgs.filter((p) => p.local && p.models.length);
    const cur = ch && imgs.find((p) => p.id === ch.provider), fam = cur && cur.local ? modelFamily(ch.model) : "";
    let styles = ART_STYLES;
    if (!online && local.length) {   // only local checkpoints: show styles those can do
      const fams = new Set(local.flatMap((p) => p.models.map((m) => modelFamily(m.id) || "any")));
      styles = fams.has("any") ? ART_STYLES : ART_STYLES.filter((s) => s.family === "any" || fams.has(s.family));
    }
    const match = ART_STYLES.find((s) => s.direction === value);
    const star = (s) => (fam && s.family === fam ? " ★" : "");
    const none = !online && !local.length;
    el.innerHTML = `<div class="field"><label>Art style <small class="muted">for ${story ? "this story's" : "new stories'"} pictures</small></label>
      <select id="sArtStyle" ${none ? "disabled" : ""}><option value="">${value && !match ? "Keep my own style" : "No style chosen"}</option>${styles.map((s) => `<option value="${esc(s.id)}" ${match && match.id === s.id ? "selected" : ""}>${esc(s.label)}${star(s)}</option>`).join("")}</select>
      <div class="hint">${none ? "Add an API key for Gemini, Grok or OpenAI, or start ComfyUI with a checkpoint, and styles that suit them appear here."
        : `Styles for ${[online ? "your online picture services" : "", local.length ? "your local checkpoint" + (local[0].models.length > 1 ? "s" : "") : ""].filter(Boolean).join(" and ")}${fam ? ". ★ = suits the selected checkpoint" : ""}. You can still write your own in the story's Art tab.`}</div></div>`;
    const sel = el.querySelector("#sArtStyle");
    sel.onchange = () => { const st = ART_STYLES.find((x) => x.id === sel.value); if (st) value = st.direction; else if (!sel.value && !match) { /* keep own */ } else value = ""; };
  };
  return { render, get: () => value };
}

// ---------- art style presets ----------
let ART_STYLES = [];
fetch("/api/art-styles").then((r) => r.json()).then((l) => { ART_STYLES = l; }).catch(() => {});
// A dropdown that fills the art direction box (id `target`) with a ready-made style. The box stays editable.
function styleSelectHtml(target, hint = "") {
  return `<div class="field"><label>Art style${hint ? ` <small class="muted">${hint}</small>` : ""}</label>
    <select class="stylepick" data-target="${target}"><option value="">Choose a style…</option>${ART_STYLES.map((s) => `<option value="${esc(s.id)}">${esc(s.label)}</option>`).join("")}<option value="custom">My own (type below)</option></select></div>`;
}
document.addEventListener("change", (e) => {
  const sel = e.target.closest && e.target.closest("select.stylepick");
  if (!sel) return;
  const box = document.getElementById(sel.dataset.target), st = ART_STYLES.find((s) => s.id === sel.value);
  if (box && st) { box.value = st.direction; box.dispatchEvent(new Event("input", { bubbles: true })); }
  else if (box && sel.value === "custom") { box.focus(); }
});

// ---------- Art tab (inside the Player panel) ----------
function artTabHtml() {
  const a = (story && story.art) || {};
  return `<div id="tabArt" class="hidden">
    <details class="sub" open><summary>Look of the pictures</summary>
    ${styleSelectHtml("artDir", (a.direction || "").trim() ? "" : "pick one before drawing any characters, so every picture matches")}
    <label>Art direction: added to every image prompt, so it sets the look of your whole story</label>
    <textarea class="short" id="artDir" rows="4" placeholder="e.g. anime light-novel illustration, soft cel shading, muted autumn palette, cinematic lighting">${esc(a.direction || "")}</textarea>
    <label>Setting: the world your characters belong to. Every generated character (portraits, candidates, your avatar) is dressed and styled to fit it</label>
    <textarea class="short" id="artSetting" rows="3" placeholder="Written automatically from your story prompt the first time a character is drawn. Edit freely.">${esc(a.setting || "")}</textarea>
    <div class="actions" style="justify-content:flex-start;margin-top:4px"><button id="artSettingGen">✨ Write it from the story prompt</button><span class="hint" id="artSettingNote"></span></div>
    <label>Negative prompt: things to keep out of pictures</label>
    <textarea class="short" id="artNeg" rows="2">${esc(a.negative || "")}</textarea>
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Scene picture shape</label>
      <select id="artSize"><option value="portrait">Portrait (2:3)</option><option value="landscape">Landscape (3:2)</option><option value="square">Square</option><option value="wide">Wide (16:9)</option></select></div>
      <div><label>Portrait size in the story</label><select id="artPsize"><option value="small">Small</option><option value="medium">Medium</option><option value="large">Large</option><option value="xl">Extra large</option></select></div></div>
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Shape of newly drawn portraits</label><select id="artPshape"><option value="landscape">Landscape card (3:2)</option><option value="portrait">Portrait (2:3)</option><option value="square">Square</option></select></div><div></div></div>
    </details>
    <details class="sub"><summary>What gets drawn</summary>
    <label class="chk"><input type="checkbox" id="artAuto" ${a.auto ? "checked" : ""}> Auto-illustrate: the narrator flags visually striking turns and a picture is drawn for them (uses image credits)</label>
    <label class="chk"><input type="checkbox" id="artSuggest" ${a.suggest !== false ? "checked" : ""}> Offer to create art for named characters that have none (you can always skip or say never)</label>
    <label class="chk"><input type="checkbox" id="artNewchar" ${a.newchar ? "checked" : ""}> Let the narrator flag new named characters as they appear, so the app can offer to draw them (off by default; works with the offer above)</label>
    <label class="chk"><input type="checkbox" id="artPort" ${a.portraits !== false ? "checked" : ""}> Show character mood portraits above their lines (set them up in 🎭 Cast)</label>
    <label class="chk"><input type="checkbox" id="artExtMoods" ${a.extended_moods ? "checked" : ""}> Use the extended set of mood pictures: new characters get the extended set by default (not just the basic one), and from the second turn on the narrator is reminded each turn to use the whole range of a character's moods instead of mostly normal and smile (off by default).</label>
    <label class="chk"><input type="checkbox" id="artSceneTpl" ${a.scene_template ? "checked" : ""}> Build scene picture prompts in the app (free): from the reply's portrait tags, the place and the most active sentence, instead of asking the AI. Simpler than the AI's, but costs no model call per picture (a scene frame the narrator wrote itself is always used first).</label>
    <label class="chk"><input type="checkbox" id="artAction" ${a.action_moods !== false ? "checked" : ""}> Action moods: in fights, chases and danger${story.nsfw ? " (and, because this story allows adult content, intimate scenes)" : ""} the narrator prefers the action pictures (strike, guard, dodge, charge, cast, wounded, victory… draw them from a character's page → Draw the set → Action set) over calm expressions. On by default; untick to keep the narrator to ordinary moods.</label>
    <label class="chk"><input type="checkbox" id="artExact" ${a.exact_moods ? "checked" : ""}> Ask for exact moods: the narrator names the real feeling (mischievous, worried, teasing, shocked…) and only uses a listed mood when it is an exact match, so you are offered a new picture for each new mood (more drawing offers; off by default)</label>
    </details>
    <details class="sub"><summary>Scene pictures</summary>
    <label class="chk"><input type="checkbox" id="artOften" ${a.scene_often ? "checked" : ""}> Scene picture most turns: the narrator adds a scene frame (who is where, what they are doing) to most replies. Needs Auto-illustrate (in “What gets drawn”). Routine, repeated settings can use the fixed Places pictures instead.</label>
    <div class="field"><label>Turns between scene pictures</label><input id="artSceneGap" type="number" min="1" max="20" value="${Math.max(1, +a.scene_gap || 1)}" style="width:90px"><div class="hint">1 = a picture whenever the narrator adds one. 3 = at most one every third reply (the narrator is told to skip the frame on the replies in between, and the app drops one if it is added anyway).</div></div>
    <div class="grid2" style="grid-template-columns:1fr 1fr"><div><label>Scene picture size in the story</label><select id="artSceneDisp"><option value="normal">Normal</option><option value="large">Large</option><option value="full">Full width</option></select></div>
      <div><label>Drawn size (local ComfyUI)</label><select id="artSceneScale"><option value="normal">Standard</option><option value="large">Larger (1.25×)</option><option value="xl">Largest (1.5×)</option></select></div></div>
    <div class="hint">Every scene picture also has a Reframe menu in the story to redraw it tall, wide, or square. The drawn size only applies to your own computer's ComfyUI (online services have fixed sizes) and uses more video memory and time; if the picture duplicates people, go back to Standard.</div>
    <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Show scene pictures</label><select id="artSceneWhere"><option value="reply">Under the reply</option><option value="banner_top">A banner above the narration</option><option value="banner_bottom">A banner below the narration</option><option value="panel">In the side panel (the “Right now” card)</option></select></div><div></div></div>
    <div class="actions" style="justify-content:flex-start;margin:2px 0 6px"><button id="artPlaces" type="button">📍 Places… (fixed pictures of rooms you return to often)</button></div>
    </details>
    <details class="sub"><summary>Side panel card</summary>
    <label class="chk"><input type="checkbox" id="artNow" ${a.now_card ? "checked" : ""}> Show who you are talking to in the side panel: their picture, mood and what they are wearing, in place of your avatar (the helper model updates it after each reply; off by default).</label>
    <div class="field" style="margin-left:24px"><label>Its picture</label><select id="artNowPic"><option value="portrait" ${a.now_pic !== "live" ? "selected" : ""}>Their saved picture for the mood (no extra drawing)</option><option value="live" ${a.now_pic === "live" ? "selected" : ""}>A new picture each turn: what they are doing and wearing right now</option></select>
      <label class="chk"><input type="checkbox" id="artNowFree" ${a.now_free ? "checked" : ""}> Fill the card from the narrator's portrait tags only (free): who and their mood, with no AI call. It then shows no "doing" or "wearing" (and cannot draw a new picture each turn).</label>
      <div class="hint">A new picture each turn uses their saved pictures for the face and is drawn in the background after the reply, only when what they are doing or wearing changes. It uses your picture maker once per change (on a local ComfyUI, a few seconds of GPU each time).</div></div>
    </details>
    <p class="hint">Both switches add a short instruction for the narrator. Changing them makes the next Claude turn rewrite the prompt cache once.</p>
  </div>`;
}
function artTabInit() { const a = (story && story.art) || {}; if ($("artSceneWhere")) $("artSceneWhere").value = a.scene_where || "reply"; if ($("artPlaces")) $("artPlaces").onclick = () => { artBack = () => showArtHub("Style"); showPlaces({ back: () => showArtHub("Style") }); };
  $("artSettingGen").onclick = async (e) => { e.target.disabled = true; $("artSettingNote").textContent = "Writing…"; try { const r = await api(`/stories/${story.id}/art/setting`, { method: "POST" }); $("artSetting").value = r.art.setting; $("artSettingNote").textContent = "Done. Edit if you like, then Save."; } catch (err) { $("artSettingNote").textContent = err.message; } e.target.disabled = false; }; $("artSize").value = a.size || "portrait"; $("artSceneDisp").value = a.scene_display || "normal"; $("artSceneScale").value = a.scene_scale || "normal"; $("artPsize").value = a.portrait_size || "medium"; $("artPshape").value = a.portrait_shape || "landscape"; }
async function artTabSave() {
  story = await api(`/stories/${story.id}/art`, { method: "PUT", body: {
    direction: $("artDir").value, setting: $("artSetting").value, negative: $("artNeg").value, size: $("artSize").value, portrait_size: $("artPsize").value, portrait_shape: $("artPshape").value, auto: $("artAuto").checked, suggest: $("artSuggest").checked, newchar: $("artNewchar").checked, exact_moods: $("artExact").checked, action_moods: $("artAction").checked, scene_template: $("artSceneTpl").checked, extended_moods: $("artExtMoods").checked, scene_often: $("artOften").checked, scene_gap: Math.max(1, Math.min(20, +$("artSceneGap").value || 1)), scene_where: $("artSceneWhere").value, scene_display: $("artSceneDisp").value, scene_scale: $("artSceneScale").value, now_card: $("artNow").checked, now_free: $("artNowFree").checked, now_pic: $("artNowPic").value, portraits: $("artPort").checked } });
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
async function showGenericEditor() {
  const presets = await api("/generic-presets"), have = (story.cast || []).filter((c) => c.generic);
  const taken = new Set((story.cast || []).map((c) => c.name.toLowerCase()));
  modal(`<h2>Generic characters</h2>
    <p class="hint">Throwaway characters for people with no name: the waiter, a guard, someone in the crowd. When the narrator brings in an unnamed person it uses one of these (by role, never a made-up name) and shows their picture. They appear as one "Generic Characters" card in the Cast.</p>
    ${have.length ? `<p class="hint">Already in this story: ${esc(have.map((c) => c.name).join(", "))}.</p>` : ""}
    <label>Pick from the list</label>
    <div class="chkgrid" id="gnList">${presets.map((p) => `<label class="chk" title="${esc(p.appearance)}"><input type="checkbox" value="${esc(p.name)}" ${taken.has(p.name.toLowerCase()) ? "disabled" : ""}> ${esc(p.name)}</label>`).join("")}</div>
    <div class="field"><label>Or let the app suggest roles that suit this story's world</label><div style="display:flex;gap:8px;align-items:center"><input id="gnSuggest" type="number" min="0" max="12" value="0" style="width:70px"><span class="hint">how many (0 = none)</span></div></div>
    <div class="field"><label>Add your own</label><div style="display:flex;gap:8px"><input id="gnName" placeholder="Role, e.g. Ticket Inspector" style="flex:1"></div><textarea id="gnLook" rows="2" placeholder="What they look like (optional)"></textarea></div>
    <label class="chk"><input type="checkbox" id="gnDraw" checked> Draw a normal picture for each (about half a minute each on a local picture maker; online makers bill per picture)</label>
    <div class="hint" id="gnMsg"></div>
    <div class="actions"><button id="gnCancel">Close</button><button class="primary" id="gnAdd">Add</button></div>`);
  $("gnCancel").onclick = () => showCast("Characters");
  $("gnAdd").onclick = async (e) => {
    const items = [...document.querySelectorAll("#gnList input:checked")].map((i) => presets.find((p) => p.name === i.value));
    if ($("gnName").value.trim()) items.push({ name: $("gnName").value.trim(), appearance: $("gnLook").value.trim() });
    const suggest = Math.max(0, Math.min(12, +$("gnSuggest").value || 0));
    if (!items.length && !suggest) { $("gnMsg").textContent = "Pick at least one, type a role, or ask for suggestions."; return; }
    e.target.disabled = true; $("gnMsg").textContent = suggest ? "Asking the helper AI…" : "Adding…";
    try {
      const r = await api(`/stories/${story.id}/generic-characters`, { method: "POST", body: { items, suggest } });
      story = r.story;
      if ($("gnDraw").checked) {
        for (let n = 0; n < r.added.length; n++) {
          const sl = r.added[n]; $("gnMsg").textContent = `Drawing ${n + 1} of ${r.added.length}…`;
          try { story = await api(`/stories/${story.id}/cast/${sl}/generate`, { method: "POST", body: { mood: "normal 1" } }); } catch (err) { $("gnMsg").textContent = "A picture failed: " + err.message; }
        }
      }
    } catch (err) { $("gnMsg").textContent = err.message; e.target.disabled = false; return; }
    showCast("Characters");
  };
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
function makerLabel(m) { return !m ? "" : m === "flux" ? "FLUX" : m.startsWith("model:") ? m.split(":").slice(2).join(":").replace(/\.safetensors$/, "").replace(/[_-]/g, " ").slice(0, 18) : m.startsWith("ckpt:") ? m.slice(5).replace(/\.safetensors$/, "").replace(/[_-]/g, " ").slice(0, 18) : m; }
function charBadges(c) {   // small species / picture-maker tags on a cast card
  const sp = (c.species || "").trim(), own = makerLabel(c.maker || "");
  const spMaker = sp ? makerLabel(((story.species || {})[sp.toLowerCase()] || {}).maker || "") : "";
  return (sp && sp.toLowerCase() !== "human" ? `<span class="cbadge" title="Species">${esc(sp)}</span>` : "") +
    (own ? `<span class="cbadge mk" title="This character's own picture maker">🖌 ${esc(own)}</span>` : spMaker ? `<span class="cbadge mk" title="Picture maker of their species">🖌 ${esc(spMaker)}</span>` : "");
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
    <div class="meta"><b>${esc(c.name)}</b>${charBadges(c)}<small>${esc(oneLine(c.bio || c.appearance, 60) || "no description yet")}</small>${(c.events || []).length ? `<small class="evn">📜 ${c.events.length} event${c.events.length > 1 ? "s" : ""}</small>` : ""}</div></button>`;
  modal(`<h2>Cast &amp; gallery</h2>
    <div class="tabs"><button class="tab ctab" data-tab="Characters">Characters (${(story.cast || []).length})</button><button class="tab ctab" data-tab="Relationships">Relationships</button><button class="tab ctab" data-tab="Gallery">Gallery (${items.length})</button><button class="tab ctab" data-tab="Intro">Intro panels (${(story.intro_images || []).length})</button></div>
    <div id="cTabCharacters"><p class="hint">Click a character to see their bio, key events and all their pictures. The card shows their normal picture.</p>
      <div class="cardgrid">${castCards(card)}<button class="charcard addcard" id="cAdd"><div class="noart">＋ Add character</div></button><button class="charcard addcard" id="cLib"><div class="noart">👥 From the library</div></button></div>
      <details class="fold"><summary>Cast tools</summary>
        <div class="tooltable">
          <button id="cImport">Import portraits…</button><span class="hint">Load a folder of pictures: one folder per character, one file per mood.</span>
          <button id="cFindArt">Find characters without art…</button><span class="hint">Lists named characters in the story that have no pictures and offers to draw them.</span>
          <button id="cSuggest">Suggest from the prompt</button><span class="hint">Reads the story prompt and proposes cast members with descriptions.</span>
          <button id="cMoods">Image categories…</button><span class="hint">Rename the categories (Expressions, Actions, Positions, Finishes, Other adult), add your own, edit what each picture draws and see the exact prompt. The narrator learns the changes automatically.</span>
          <button id="cGeneric">Generic characters…</button><span class="hint">Throwaway characters (a waiter, a guard, a passer-by) the narrator uses whenever someone with no name appears.</span>
          <button id="cGroup">Group characters…</button><span class="hint">Shows extras (guards, zombies…) as one card.<label class="chk" style="margin:4px 0 0"><input type="checkbox" id="cGroupOn" ${story.group_cast !== false ? "checked" : ""}> Show groups as one card</label></span>
          <button id="cVoices">Voices…</button><span class="hint">Choose read-aloud voices for the narrator and each character.</span>
        </div></details></div>
    <div id="cTabRelationships" class="hidden"><p class="hint">How each character feels about you over time, read from the story\u2019s status panels (or the state block). Click a name to open their page.</p><div id="relBox">Loading…</div></div>
    <div id="cTabGallery" class="hidden">${gallery}</div>
    <div id="cTabIntro" class="hidden"><p class="hint">Intro / webtoon panels. Put <code>![](intro:Name)</code> in the story's opening scene (✎ Edit) to show one; the name is the file name without its extension. The folder import fills these in automatically.</p>
      <div class="gallery">${(story.intro_images || []).map((p) => `<figure><a href="${p.url}" target="_blank"><img loading="lazy" src="${thumbUrl(p.url, 360)}" alt=""></a><figcaption>${esc(p.name)} <button class="x idel" data-url="${esc(p.url)}" title="Remove this panel">×</button></figcaption></figure>`).join("") || '<p class="hint">No panels yet.</p>'}</div>
      <label>Add panels (you can pick several; the file name becomes the panel name)</label><input type="file" id="iAdd" multiple accept="image/png,image/jpeg,image/webp,image/gif"></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  api(`/stories/${story.id}/cast/duplicates`).then((dups) => {       // characters that are probably one person (free, no AI)
    const host = $("cTabCharacters"); if (!host || !dups.length) return;
    host.insertAdjacentHTML("afterbegin", `<div class="dupbox"><b>Possibly the same person</b>${dups.map((d, i) => `<div class="lrow" style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0"><span>${esc(d.names[0])} and ${esc(d.names[1])} <small class="hint">(${esc(d.reason)})</small></span><button class="dupmerge" data-i="${i}">Merge ${esc(d.names[0])} into ${esc(d.names[1])}</button></div>`).join("")}</div>`);
    host.querySelectorAll(".dupmerge").forEach((b) => b.onclick = async () => { const d = dups[+b.dataset.i]; if (!confirm(`Merge ${d.names[0]} into ${d.names[1]}? ${d.names[0]} stops being a separate character; the name is kept as an “also called” name.`)) return;
      try { story = await api(`/stories/${story.id}/cast/${d.fold}/merge`, { method: "POST", body: { into: d.into } }); renderAll(); showCast("Characters"); } catch (e) { alert(e.message); } });
  }).catch(() => {});
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
  $("cGeneric").onclick = () => showGenericEditor();
  $("cMoods").onclick = () => showMoodTypes(null, () => showCast("Characters"));
  $("cVoices").onclick = () => showVoices();
  $("cGroupOn").onchange = async (e) => { story = await api(`/stories/${story.id}/group-cast?on=${e.target.checked}`, { method: "PUT" }); showCast("Characters"); };
  $("cAdd").onclick = () => showCharacter(null);
  $("cLib").onclick = () => showCastLibrary();
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

// The Images tab groups a character's pictures by image category (Expressions, Actions, Positions...), and each group can be minimized (remembered across characters).
const moodCatCache = {};          // picture name (without its number) -> category id, filled from /api/moods
function moodCategoryId(m) {
  const b = String(m).toLowerCase().replace(/\s*\d+$/, "").trim(), d = ((typeof config !== "undefined" && config && config.mood_defs) || {})[b];
  return moodCatCache[b] || (d && d.category) || (EXPRESSION_MOODS.has(b) ? "expressions" : "actions");
}
function minimizedCats() { try { return new Set(JSON.parse(localStorage.getItem("imMinCats") || "[]")); } catch (e) { return new Set(); } }
function saveMinimizedCats(set) { try { localStorage.setItem("imMinCats", JSON.stringify([...set])); } catch (e) { /* it is a convenience */ } }
function groupByCategory(items) {       // items: [mood name, figure html]
  if (!items.length) return "";
  const cats = ((typeof config !== "undefined" && config && config.image_categories) || []), groups = new Map(), min = minimizedCats();
  for (const c of cats) groups.set(c.id, []);
  for (const [m, h] of items) { const id = m === "__earlier" ? m : moodCategoryId(m); if (!groups.has(id)) groups.set(id, []); groups.get(id).push(h); }
  const later = groups.get("__earlier"); groups.delete("__earlier"); if (later) groups.set("__earlier", later);       // the replaced pictures are a group of their own, last
  return [...groups].filter(([, hs]) => hs.length).map(([id, hs]) => {
    const c = cats.find((x) => x.id === id);
    return `<div class="imcat${min.has(id) ? " min" : ""}" data-cat="${esc(id)}"><button type="button" class="imcatHead" aria-expanded="${!min.has(id)}"><span class="imcatArrow">▾</span> ${esc(c ? c.name : id === "__earlier" ? "Earlier versions (replaced pictures)" : id)} <span class="hint">(${hs.length})</span></button><div class="imcatGrid">${hs.join("")}</div></div>`;
  }).join("");
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
    const cur = Object.entries(c.moods || {}).map(([m, u]) => [m,
      `<figure class="mood"><img loading="lazy" src="${thumbUrl(u, 260)}" alt=""><figcaption>${esc(m)} <button class="x mregen" data-mood="${esc(m)}" title="Draw another version. The current one is kept as an earlier version.">↻</button><button class="x mprompt" data-mood="${esc(m)}" data-url="${esc(u)}" title="Show and edit the exact prompt this picture was drawn from, and draw again from your change">📝</button><button class="x mren" data-mood="${esc(m)}" title="Rename this picture (the narrator picks pictures by name)">✎</button><button class="x mdel" data-mood="${esc(m)}" title="Remove this picture">×</button></figcaption></figure>`]);
    const old = Object.entries(c.alts || {}).flatMap(([m, us]) => us.map((u) => ["__earlier",
      `<figure class="mood alt"><img loading="lazy" src="${thumbUrl(u, 200)}" alt=""><figcaption>${esc(m)} (earlier) <button class="x mprompt" data-mood="${esc(m)}" data-url="${esc(u)}" title="Show the prompt this earlier picture was drawn from">📝</button><button class="x altuse" data-mood="${esc(m)}" data-url="${esc(u)}" title="Make this the active picture">use</button><button class="x altdel" data-url="${esc(u)}" title="Delete">×</button></figcaption></figure>`]));
    return groupByCategory([...cur, ...old]) || '<span class="hint">No pictures yet. Use “Draw options…” to pick a face, or upload your own.</span>';
  };
  const eventRow = (e) => `<div class="evrow" data-id="${esc(e.id)}"><span class="evwhen">${esc(e.when || "·")}</span><span class="evtext">${esc(e.text)}</span>${e.digest ? '<span class="evauto" title="Several older events condensed by the helper AI">history</span>' : e.auto ? '<span class="evauto" title="Logged automatically">auto</span>' : ""}<button class="x evedit" title="Edit">✎</button><button class="x evdel" title="Delete">×</button></div>`;

  modal(`<div class="charhead"><button id="chBack">← Cast</button><h2>${esc(c.name || "New character")}</h2></div>
    <div class="tabs"><button class="tab chtab" data-tab="Bio">Bio</button><button class="tab chtab" data-tab="Events"${isNew ? " disabled" : ""}>Key events (${(c.events || []).length})</button><button class="tab chtab" data-tab="Images"${isNew ? " disabled" : ""}>Images (${Object.keys(c.moods || {}).length})</button></div>

    <div id="chBio">
      <div class="bioTop">${normalUrl(c) ? `<img class="bioimg" src="${thumbUrl(normalUrl(c), 480)}" alt="">` : ""}<div style="flex:1">
        <label>Name</label><input id="chName" value="${esc(c.name)}" placeholder="Name">
        <label>Also called <small class="hint">(other names the story uses for them: first name, nickname, title; separate with commas)</small></label><input id="chAka" value="${esc((c.aka || []).join(", "))}" placeholder="e.g. a nickname or title">
        <div class="grid2" style="grid-template-columns:1fr 2fr"><div><label>Species <small class="hint">(empty = human)</small></label><input id="chSpecies" list="speciesList" value="${esc(c.species || "")}" placeholder="human"><datalist id="speciesList">${Object.values(story.species || {}).map((x) => `<option value="${esc(x.name)}">`).join("")}</datalist></div>
          <div><label>Picture maker <small class="hint">(for this character; empty = their species' or the story's)</small></label><select id="chMaker"></select></div></div>
        ${(() => { const sp = c.species && (story.species || {})[c.species.toLowerCase()]; return sp && sp.reference_url
          ? `<div class="spref"><img src="${thumbUrl(sp.reference_url, 200)}" alt=""><div class="hint"><b>${esc(sp.name)} reference picture.</b> New characters of this species are drawn from it, so they share its look but have their own face. Change it under 🖼 Art → Characters → Species.</div></div>`
          : `<div class="hint">The first picture drawn for a new species becomes its reference: later characters of that species keep its look (skin, crests, build) but are drawn as their own person. Species traits are written under 🖼 Art → Characters → Species.</div>`; })()}
        <label>How they look <small class="hint">(what the artist is told: age, hair, eyes, build, clothing, distinctive features)</small></label><textarea class="short" id="chApp" rows="3">${esc(c.appearance)}</textarea>
        <label>Face in words <small class="hint">(optional. Picture makers that cannot take reference pictures, like UnCanny, only know a face from words: this is put first in every picture prompt, so the face stays the same. Hair colour belongs here too)</small></label><textarea class="short" id="chFace" rows="3" placeholder="e.g. oval face, soft jawline, dark brown almond eyes, straight thin brows, small nose, full lips, fair skin, shoulder-length dark brown bob with straight bangs">${esc(c.face || "")}</textarea>
        ${isNew ? "" : '<div class="actions" style="justify-content:flex-start;margin:2px 0 6px"><button id="chFaceWrite" type="button" title="A helper model that can see pictures describes their face from their normal picture. Nothing is saved until you press Save.">👁 Describe the face from their picture</button><span class="hint" id="chFaceNote"></span></div>'}
        <label>Face LoRA <small class="hint">(optional. A LoRA trained on this character's face, from ComfyUI's loras folder. It is added whenever ComfyUI draws them: pictures, scenes and the Now card. It only works with the kind of model it was trained for)</small></label>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px;margin:2px 0 6px"><select id="chFaceLora" style="max-width:320px"><option value="">(none)</option></select><input id="chFaceLoraSt" type="number" step="0.05" min="-2" max="2" value="${esc(String(c.face_lora_strength ?? 1))}" style="width:80px" title="Strength: 0.7 to 1.0 is usual"><input id="chFaceTrig" value="${esc(c.face_trigger || "")}" placeholder="trigger word (if it has one)" style="max-width:220px"></div>
        <div class="actions" style="justify-content:flex-start;margin:4px 0 0"><button id="chAppSuggest" ${isNew ? "disabled" : ""}>Suggest from the story</button></div></div></div>
      <label>Names and forms of address <small class="hint">(optional: which names or titles each person uses for this character, and who is allowed to. Sent to the narrator every turn, so it can keep names consistent. For example: a given name only close friends use, a nickname, a title or rank others use, or a name that should stay secret.)</small></label><textarea class="short" id="chAddress" rows="3" maxlength="600">${esc(c.address || "")}</textarea>
      <label>Personality <small class="hint">(traits, how they speak, what they want)</small></label><input id="chPers" value="${esc(c.personality || "")}">
      <label>Bio <small class="hint">(who they are in your story: role, history, relationships)</small></label><textarea class="short" id="chBioText" rows="5">${esc(c.bio || "")}</textarea>
      <label class="chk top"><input type="checkbox" id="chAuthor" ${c.author ? "checked" : ""}><span><b>Tell the narrator about them</b><span class="hint">Sends this character's looks, personality and bio to the narrator every turn. Off: the bio is only shown here.</span></span></label>
      <div class="actions" style="justify-content:flex-start;margin:4px 0 0"><button id="chBioSuggest" ${isNew ? "disabled" : ""}>Suggest a bio from the story</button><button id="chSheetFill" ${isNew ? "disabled" : ""} title="Read the story so far and suggest every box of this character's sheet. Not for early in a story.">✨ Fill the whole sheet from the story…</button></div>
      <div id="chPromptLines"></div><div class="aStatus hint" id="chStatus"></div>
      ${isNew || (story.cast || []).length < 2 ? "" : `<div class="aiBox"><b>Same person as another character?</b> <small class="muted">If the story ended up with this character twice (for example under a nickname and under their full name), fold this entry into the right one: its name becomes an “also called” name there, and its pictures, key events and side-panel row move over.</small>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px"><select id="chMergeSel">${(story.cast || []).filter((x) => x.slug !== slug).map((x) => `<option value="${esc(x.slug)}">${esc(x.name)}</option>`).join("")}</select><button id="chMerge">Merge ${esc(c.name)} into them</button></div></div>`}
      <div class="actions">${isNew ? "" : '<button id="chToLib" title="Keep a copy of this character (description and normal picture) in the Character library, so any story can use them">👥 Save to library</button><button class="danger" id="chRemove">Remove character</button><span style="flex:1"></span>'}<button class="primary" id="chSave">${isNew ? "Create character" : "Save"}</button></div></div>

    <div id="chEvents" class="hidden">
      <p class="hint">Important moments for ${esc(c.name || "this character")}. New ones are added automatically after each reply (change this in Settings); you can edit, delete or add your own.</p>
      <div id="evList">${(c.events || []).map(eventRow).join("") || '<p class="hint">Nothing logged yet.</p>'}</div>
      <div class="evadd"><input id="evWhen" placeholder="When (e.g. Turn 42)" style="width:150px"><input id="evText" placeholder="What happened" style="flex:1"><button id="evAdd">Add</button></div>
      <div class="actions" style="justify-content:flex-start"><button id="evRebuild" title="Read the story so far and write this character's event log (your own entries are kept)">Rebuild from the story</button><button id="evCondense" title="Fold the older automatic events into a short history (one helper-AI call). This also happens by itself when the log gets long.">Condense older events</button><span class="hint" id="evStatus"></span></div></div>

    <div id="chImages" class="hidden">
      <p class="hint">The card shows the <b>normal</b> picture. Other pictures are moods the narrator can pick above this character's lines. ↻ draws another version and keeps the old one as an earlier version; every picture helps keep their face consistent.</p>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="imOpts">Draw options…</button>
        <select id="imSet"><option value="basic">Basic set</option><option value="extended">Extended set</option>${categorySetOptions("All: ")}<option value="match">Images my other characters have</option></select><button id="imSetGo">Draw the set</button>${Object.keys(c.moods || {}).length ? '<button id="imDelAll" class="danger" title="Delete every picture of this character in this story, including earlier versions">Delete all their pictures</button>' : ""}</div>
      <div class="castact" style="margin:6px 0"><label for="imMood" class="hint" style="margin:0 4px 0 0">Picture name:</label><input id="imMood" value="normal 1" size="12" title="What this picture is called (its mood): used when drawing and when you upload your own"><button id="imDraw">Draw this one with AI</button><input type="file" id="imFile" accept="image/png,image/jpeg,image/webp,image/gif"></div>
      <div class="aStatus hint" id="imStatus"></div>
      <div class="actions imcatBar" style="justify-content:flex-start;margin:0 0 4px"><button type="button" id="imMinAll" class="x">Minimize all</button><button type="button" id="imMaxAll" class="x">Expand all</button></div>
      <div id="imGrid">${moodChips()}</div>
      ${isNew ? "" : `<details class="sub" id="imTagsBox"><summary>Picture tags (for tag-style picture models)</summary>
        <div class="hint">With a tag-style model such as Animagine, these tags describe this character's looks (hair, eyes, build, clothing) in every picture, so they stay the same. The helper model writes them once from the description; edit them here, or press Rebuild to have them written again. Leave your own tags empty to use the helper's.</div>
        <textarea class="short" id="imTags" rows="3" placeholder="Written automatically the first time a picture is drawn with a tag-style model."></textarea>
        ${story.nsfw ? `<label style="margin-top:8px">Adult picture tags</label><div class="hint">A separate tag set used ONLY for this character's adult pictures (nude, the positions, the finishes...), so nudity never leaks into everyday pictures and everyday clothes never leak into adult ones. Write who they are (subject count, hair, eyes, skin, build) plus any adult words you want. Empty = the everyday tags above, without their clothing.</div>
        <textarea class="short" id="imTagsAdult" rows="2" placeholder="e.g. 1girl, shoulder-length hair, black hair, brown eyes, dark skin, slim, nude"></textarea>` : ""}
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="imTagsSave">Save my tags</button><button id="imTagsRebuild">Rebuild from the description</button><span class="hint" id="imTagsNote"></span></div></details>`}
      ${config.reuse_art && !isNew ? `<h3>References from other stories</h3>
        <div class="hint">Borrow a character's pictures from another story as a face reference. Only their face and hair are kept; each story's own art style and setting apply, so the result looks like this story. Used when this character has no pictures of their own yet, and as extra references afterwards.</div>
        <div id="imExtList" class="moods">${(c.ext_refs || []).map((r) => `<figure class="mood alt"><img loading="lazy" src="${thumbUrl(r.url, 200)}" alt=""><figcaption title="${esc(r.source)}">${esc(oneLine(r.source, 28))} <button class="x extdel" data-url="${esc(r.url)}" title="Remove this reference">×</button></figcaption></figure>`).join("")}</div>
        <div class="actions" style="justify-content:flex-start"><select id="imExtSel"><option value="">Loading other stories…</option></select><button id="imExtAdd">Use as reference</button><span class="hint" id="imExtNote"></span></div>` : ""}</div>

    <div class="actions" style="margin-top:6px"><button id="mClose">Close</button></div>`);

  if ($("imTags")) {
    const fill = (r) => { $("imTags").value = r.tags || ""; if ($("imTagsAdult")) $("imTagsAdult").value = r.adult || ""; $("imTagsNote").textContent = r.custom ? "Your own tags are in use." : r.saved ? "Written by the helper model." : "Nothing yet: made the first time a picture is drawn with a tag-style model."; };
    api(`/stories/${story.id}/cast/${slug}/tags`).then(fill).catch(() => {});
    $("imTagsSave").onclick = async () => { try { fill(await api(`/stories/${story.id}/cast/${slug}/tags`, { method: "PUT", body: { tags: $("imTags").value, adult: $("imTagsAdult") ? $("imTagsAdult").value : null } })); $("imTagsNote").textContent = "✔ Saved. " + $("imTagsNote").textContent; } catch (err) { $("imTagsNote").textContent = err.message; } };
    $("imTagsRebuild").onclick = async (e) => {
      e.target.disabled = true; $("imTagsNote").textContent = "Writing the tags…";
      try { fill(await api(`/stories/${story.id}/cast/${slug}/tags/rebuild`, { method: "POST" })); } catch (err) { $("imTagsNote").textContent = err.message; }
      e.target.disabled = false;
    };
  }
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
  if ($("chFaceLora")) { const cur = c.face_lora || "", fillLoras = (list) => { const sel = $("chFaceLora"); if (!sel) return; const names = [...new Set([...(list || []), ...(cur ? [cur] : [])])]; sel.innerHTML = '<option value="">(none)</option>' + names.map((n) => `<option value="${esc(n)}">${esc(n)}${list && list.includes(n) ? "" : " (not found in ComfyUI)"}</option>`).join(""); sel.value = cur; };
    fillLoras(null); api("/model-options").then((o) => { const p = (o.images || []).find((x) => (x.loras || []).length); fillLoras(p ? p.loras : []); }).catch(() => {}); }
  if ($("chMaker")) { makerSelect($("chMaker"), [["", "Same as the story"]], c.maker || ""); makerChoices().then((ch) => { if ($("chMaker")) makerSelect($("chMaker"), ch, c.maker || ""); }); }
  $("chSave").onclick = async () => {
    const name = $("chName").value.trim(); if (!name) { status("chStatus", "Give the character a name."); return; }
    const edit = { name, appearance: $("chApp").value, face: $("chFace").value, face_lora: $("chFaceLora").value, face_lora_strength: parseFloat($("chFaceLoraSt").value) || 1, face_trigger: $("chFaceTrig").value, bio: $("chBioText").value, personality: $("chPers").value, author: $("chAuthor").checked, aka: $("chAka").value, address: $("chAddress").value, species: $("chSpecies").value, maker: $("chMaker").value, slug: isNew ? null : slug };
    try {
      story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: isNew ? [...castPayload(null), edit] : castPayload(edit) } });
      const made = isNew ? story.cast.find((x) => x.name === name) : null;
      renderAll(); showCharacter(isNew ? (made ? made.slug : null) : slug, "Bio");
      if (isNew && !made) showCast();
    } catch (err) { status("chStatus", err.message); }
  };
  if (!isNew) {
    if ($("chMerge")) $("chMerge").onclick = async () => {
      const into = $("chMergeSel").value, target = (story.cast || []).find((x) => x.slug === into); if (!target) return;
      if (!confirm(`Merge ${c.name} into ${target.name}? ${c.name} stops being a separate character; the name is kept as an “also called” name.`)) return;
      try { story = await api(`/stories/${story.id}/cast/${slug}/merge`, { method: "POST", body: { into } }); renderAll(); showCharacter(into, "Bio"); }
      catch (err) { status("chStatus", err.message); }
    };
    $("chToLib").onclick = async (e) => {
      e.target.disabled = true;
      try { await api(`/stories/${story.id}/cast/${encodeURIComponent(slug)}/to-library`, { method: "POST" }); status("chStatus", "Saved to the Character library (Home → Characters)."); }
      catch (err) { status("chStatus", err.message); }
      e.target.disabled = false;
    };
    $("chRemove").onclick = async () => {
      if (!confirm(`Remove ${c.name} and all their pictures from this story?`)) return;
      story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: castPayload(null).filter((x) => x.slug !== slug) } }); renderAll(); showCast();
    };
    if ($("chFaceWrite")) $("chFaceWrite").onclick = async (e) => {
      e.target.disabled = true; $("chFaceNote").textContent = "Looking…";
      try { const r = await api(`/stories/${story.id}/cast/${encodeURIComponent(slug)}/describe-face`, { method: "POST" }); $("chFace").value = r.face; $("chFaceNote").textContent = "Described. Edit it if you like, then Save."; }
      catch (err) { $("chFaceNote").textContent = err.message; }
      e.target.disabled = false;
    };
    $("chAppSuggest").onclick = async (e) => {
      e.target.disabled = true; status("chStatus", "Reading what the story says about them…");

      try { const r = await api(`/stories/${story.id}/cast/describe`, { method: "POST", body: { name: c.name } }); $("chApp").value = r.appearance; status("chStatus", "Edit it if it isn't what you picture, then Save."); }
      catch (err) { status("chStatus", err.message); }
      e.target.disabled = false;
    };
    $("chSheetFill").onclick = () => sheetFill(slug, () => showCharacter(slug, "Bio"));
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
    $("evCondense").onclick = async (e) => {
      e.target.disabled = true; status("evStatus", "Condensing…");
      try { story = await api(`/stories/${story.id}/cast/${slug}/events/condense`, { method: "POST" }); refresh("Events"); }
      catch (err) { status("evStatus", err.message); e.target.disabled = false; }
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
    $("imDraw").onclick = (e) => busy(e.target, "Drawing…", async () => { const mood = ($("imMood").value || "normal 1").trim().toLowerCase(); story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood } }); if (mood === "normal 1") await offerRedrawAll(story.cast.find((x) => x.slug === slug), (m) => status("imStatus", m)); });
    $("imFile").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return;
      const nm = prompt("Name for this picture. The narrator picks pictures by name, e.g. normal 1, smile, angry 2.\nUsing a name this character already has keeps the old picture as an earlier version.", ($("imMood").value || "normal 1").trim());
      if (nm === null) { e.target.value = ""; return; }
      $("imMood").value = nm.trim() || "normal 1";
      const res = await fetch(`/api/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(($("imMood").value || "normal 1").trim())}`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!res.ok) alert((await res.json().catch(() => ({}))).detail || "Upload failed"); else story = await res.json();
      refresh("Images");
    };
    if ((story.art || {}).extended_moods && $("imSet")) $("imSet").value = "extended";   // the story's default set (Art → Style & options)
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
    if ($("imDelAll")) $("imDelAll").onclick = (e) => busy(e.target, "Deleting…", async () => {
      const n = Object.keys(c.moods || {}).length + Object.values(c.alts || {}).reduce((t, a) => t + a.length, 0);
      if (!confirm(`Delete all ${n} pictures of ${c.name} in this story, including earlier versions? This cannot be undone.`)) return;
      story = await api(`/stories/${story.id}/cast/${slug}/pictures`, { method: "DELETE" }); renderAll();
    });
    const redrawGrid = () => { $("imGrid").innerHTML = moodChips(); };
    $("imGrid").addEventListener("click", (e) => {
      const h = e.target.closest(".imcatHead"); if (!h) return;
      const box = h.parentElement, set = minimizedCats(), id = box.dataset.cat;
      box.classList.toggle("min"); h.setAttribute("aria-expanded", !box.classList.contains("min"));
      if (box.classList.contains("min")) set.add(id); else set.delete(id);
      saveMinimizedCats(set);
    });
    $("imMinAll").onclick = () => { saveMinimizedCats(new Set([...document.querySelectorAll("#imGrid .imcat")].map((x) => x.dataset.cat))); redrawGrid(); };
    $("imMaxAll").onclick = () => { saveMinimizedCats(new Set()); redrawGrid(); };
    api("/moods").then((r) => { for (const m of r.moods || []) moodCatCache[m.name] = m.category; if ($("imGrid")) redrawGrid(); }).catch(() => {});
    $("imGrid").addEventListener("click", async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      if (b.classList.contains("mregen")) busy(b, "…", async () => { story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood: b.dataset.mood } }); if (b.dataset.mood === "normal 1") await offerRedrawAll(story.cast.find((x) => x.slug === slug), (m) => status("imStatus", m)); });
      else if (b.classList.contains("mprompt")) showPicturePrompt(slug, b.dataset.mood, b.dataset.url, () => showCharacter(slug, "Images"));
      else if (b.classList.contains("mren")) { const to = prompt(`Name for this picture (now “${b.dataset.mood}”).\nThe narrator picks pictures by name, e.g. smile, angry 2, normal 1:`, b.dataset.mood); if (to && to.trim() && to.trim().toLowerCase() !== b.dataset.mood) busy(b, "…", async () => { story = await api(`/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(b.dataset.mood)}/rename`, { method: "POST", body: { to: to.trim() } }); }); }
      else if (b.classList.contains("mdel")) { story = await api(`/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(b.dataset.mood)}`, { method: "DELETE" }); refresh("Images"); }
      else if (b.classList.contains("altuse")) { story = await api(`/stories/${story.id}/cast/${slug}/mood/${encodeURIComponent(b.dataset.mood)}/use?url=${encodeURIComponent(b.dataset.url)}`, { method: "POST" }); refresh("Images"); }
      else if (b.classList.contains("altdel")) { story = await api(`/stories/${story.id}/cast/${slug}/alt?url=${encodeURIComponent(b.dataset.url)}`, { method: "DELETE" }); refresh("Images"); }
    });
  }
}

// ---------- image provider settings (inside Settings) ----------
function imgProvCard(p, i) {
  const comfy = p.type === "comfyui", oa = p.type === "openai";
  const nice = { gemini: "Gemini", xai: "Grok (xAI)", openai: "OpenAI", comfyui: "Local (ComfyUI)" }[p.id] || p.name;
  return `<details class="fold iprov" data-i="${i}" data-pid="${esc(p.id)}"><summary><b>${esc(nice)}</b> <small class="ipstat">${comfy ? "checking…" : p.has_key ? "key saved" : "needs a key"}</small></summary>
    <div class="grid2" style="grid-template-columns:2fr 1fr"><div><label>Address</label><input data-f="base_url" value="${esc(p.base_url || "")}"></div>
      <div class="imodel"><label>${comfy ? "Checkpoint file" : "Model"}</label><input data-f="model" value="${esc(p.model || "")}" placeholder="${comfy ? "e.g. illustriousXL.safetensors" : ""}"></div></div>
    <div class="hint imnote"></div>
    <label>Prompt style</label><select data-f="prompt_style">
      <option value="natural" ${(p.prompt_style || (comfy ? "tags" : "natural")) === "natural" ? "selected" : ""}>Sentences (Gemini, OpenAI, xAI, most new models)</option>
      <option value="tags" ${(p.prompt_style || (comfy ? "tags" : "natural")) === "tags" ? "selected" : ""}>Tags (anime SDXL models: Animagine, Illustrious, NoobAI)</option></select>
    <div class="hint psnote"></div>
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
      <button class="starterface" type="button">Insert face-matching (IPAdapter) workflow</button>
      <details class="fold mtweak"><summary><b>Settings for this checkpoint only</b></summary>
        <div class="hint">Applies to the checkpoint chosen above and nothing else; leave a box empty to keep the workflow's own value. Each checkpoint keeps its own set, so switching the checkpoint shows that one's settings.</div>
        <div class="grid2" style="grid-template-columns:repeat(4,1fr)">
          <div><label>Reference strength</label><input data-t="ref_weight" type="number" step="0.05" min="0" max="2" placeholder="workflow"></div>
          <div><label>Reference stops at</label><input data-t="ref_end" type="number" step="0.05" min="0" max="1" placeholder="workflow"></div>
          <div><label>Refine denoise</label><input data-t="hires_denoise" type="number" step="0.05" min="0" max="1" placeholder="workflow"></div>
          <div><label>Steps</label><input data-t="steps" type="number" step="1" min="1" max="150" placeholder="workflow"></div></div>
        <div class="grid2" style="grid-template-columns:1fr 2fr">
          <div><label>References used</label><input data-t="ref_count" type="number" step="1" min="1" max="3" placeholder="all"></div>
          <div><label>Crop references to the face</label><select data-t="ref_crop"><option value="">No (whole picture)</option><option value="face">Yes (head and shoulders only: a closer face match)</option></select></div></div>
        <div class="hint">1 reference = only the picture you picked (normal 1), so later pictures that drifted can't pull the face further away.</div>
        <label>Refine pass (second, enlarging pass)</label><select data-t="refine"><option value="">Use the workflow's</option><option value="off">Off (some anime checkpoints look sharper without it)</option></select>
        <label>LoRAs <small class="hint">(small add-on files from ComfyUI's loras folder that change the style or details of this checkpoint; one per line: name @ strength)</small></label>
        <textarea class="short" data-t="loras" rows="2" placeholder="none"></textarea>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin:2px 0 6px"><select class="lorapick" style="max-width:320px"><option value="">(no LoRAs found in ComfyUI)</option></select><input class="lorast" type="number" step="0.05" min="-2" max="2" value="0.8" style="width:80px" title="Strength: 0.6 to 1.0 is usual"><button class="loraadd" type="button">Add</button></div>
        <div class="hint">Use a LoRA made for the same kind of model as this checkpoint (an SDXL LoRA with an SDXL checkpoint, a FLUX.2 LoRA with FLUX.2); a mismatched one does nothing or changes the picture oddly. Online picture makers (Gemini, OpenAI, Grok) do not take LoRAs.</div>
        <label>Extra words added to every picture</label><textarea class="short" data-t="extra_positive" rows="2" placeholder="${esc(p.extra_positive || "empty = none")}"></textarea>
        <label>Extra words to avoid</label><textarea class="short" data-t="extra_negative" rows="2" placeholder="${esc(p.extra_negative || "empty = none")}"></textarea>
        <div class="hint">Empty extra-word boxes use the shared ones (shown faded). Anything typed here replaces them for this checkpoint.</div>
      </details>` : ""}
    <div class="actions" style="margin-top:8px"><button class="itest" type="button">Test: draw a small picture</button></div><div class="itestout"></div></details>`;
}
// Per-checkpoint settings (ComfyUI): the boxes show the settings of the checkpoint named in card.dataset.tm.
function flushTweaks(card, p) {
  const tm = card.dataset.tm;
  if (tm === undefined || !card.querySelector(".mtweak")) return;
  const t = {};
  card.querySelectorAll("[data-t]").forEach((el) => { const v = el.value.trim(); if (v !== "") t[el.dataset.t] = v; });
  const ps = card.querySelector('[data-f="prompt_style"]');
  if (ps && ps.dataset.mine === tm) t.prompt_style = ps.value;          // the style you chose for this model is remembered with it
  p.model_tweaks = p.model_tweaks || {};
  if (Object.keys(t).length) p.model_tweaks[tm] = t; else delete p.model_tweaks[tm];
}
function loadTweaks(card, p, model) {
  if (!card.querySelector(".mtweak") || card.dataset.tm === model) return;
  flushTweaks(card, p);
  const firstLoad = card.dataset.tm === undefined;
  card.dataset.tm = model;
  const t = (p.model_tweaks || {})[model] || {};
  card.querySelectorAll("[data-t]").forEach((el) => { el.value = t[el.dataset.t] ?? ""; });
  const ps = card.querySelector('[data-f="prompt_style"]'), note = card.querySelector(".psnote");
  if (ps) {       // this model's remembered prompt style, or the usual one for its kind (anime checkpoints: tags; others: sentences)
    const fam = modelFamily(model);
    if (t.prompt_style) { ps.value = t.prompt_style; ps.dataset.mine = model; }
    else if (firstLoad) {       // the saved service-wide style stays; if it is not the usual one for this kind of model, it was a deliberate choice: remember it for the model
      const usual = fam === "anime" ? "tags" : fam ? "natural" : ps.value;
      ps.dataset.mine = ps.value !== usual ? model : "";
    } else { if (fam) ps.value = fam === "anime" ? "tags" : "natural"; ps.dataset.mine = ""; }
    if (note) note.textContent = (t.prompt_style || ps.dataset.mine) ? `Remembered for ${model}: it switches back to this whenever you pick this model.` : "Chosen for this kind of model. Change it and it is remembered for this model.";
  }
  const s = card.querySelector(".mtweak summary b");
  if (s) s.textContent = model ? `Settings for ${model} only` : "Settings for this checkpoint only";
}
// Which picture maker a character or species can use: the story's own, FLUX.2 klein (takes reference pictures: best for non-human characters), or another ComfyUI checkpoint.
async function makerChoices() {
  const out = [["", "Same as the story", ""], ["flux", "FLUX.2 klein 4B (takes reference pictures: best for non-human characters)", ""]];
  try {
    const o = await api("/model-options");
    for (const p of o.images || []) {
      const grp = p.name + (p.ready ? "" : p.local ? " (not running)" : " (needs an API key)");
      for (const m of p.models || []) {
        const kind = m.kind === "flux" ? " (FLUX: reference pictures)" : m.kind === "chroma" ? " (Chroma)" : m.kind === "qwen" ? " (Qwen-Image)" : p.local ? " (text only)" : "";
        out.push(["model:" + p.id + ":" + m.id, m.id.replace(/\.safetensors$/, "") + kind, grp]);
      }
    }
  } catch (e) { /* the list is a convenience */ }
  return out;
}
function makerSelect(sel, choices, cur) {
  const have = new Set(choices.map((c) => c[0]));
  if (cur && !have.has(cur)) choices = [...choices, [cur, cur, "Other"]];
  const groups = [], byG = {};
  for (const c of choices) { const g = c[2] || ""; if (!(g in byG)) { byG[g] = []; groups.push(g); } byG[g].push(c); }
  const opt = ([v, tx]) => `<option value="${esc(v)}">${esc(tx)}</option>`;
  sel.innerHTML = groups.map((g) => g ? `<optgroup label="${esc(g)}">${byG[g].map(opt).join("")}</optgroup>` : byG[g].map(opt).join("")).join("");
  sel.value = cur || "";
}

function imageSettingsHtml(cfg) {
  const provs = cfg.image_providers || [];
  return `<h3>Picture settings</h3>
    <div class="hint">Which AI draws pictures is chosen under <b>AI &amp; story</b>. Online services need their own API key and bill separately from Claude; ComfyUI runs on your own computer for free.</div>
    <div class="grid2"><div class="field"><label>Reference pictures per scene</label><input id="imgRefs" type="number" min="0" max="8" value="${cfg.image_max_refs ?? 3}"><div class="hint">Saved portraits sent along so faces stay consistent.</div></div><div></div><div></div></div>
    <h3>Image categories</h3>
    <div class="actions" style="justify-content:flex-start"><button id="imMoodTypes" type="button">🗂 Image categories…</button><span class="hint">Rename the categories (Expressions, Actions, Positions, Finishes, Other adult), add your own, and edit what each picture draws. Every category is also a batch set. Shows the exact prompt for each.</span></div>
    <h3>Pictures drawn for a new character</h3>
    <div class="hint">After you pick a face for a new character, the app can draw more pictures of them from these lists, one picture type per line. Add your own anywhere: type a name (<code>blushing</code>) or a name with what to draw (<code>blushing: embarrassed, red cheeks, hand over mouth</code>). The narrator is told to use the pictures in these sets before asking for anything new; if it does ask for a new one and you draw it, it is added to the end of the Extended set automatically. Built-in types: normal, smile, shy, surprise, sulky, fear, sad, angry, scorn, despair, combat, cute, dead, zombie. Numbers (<code>smile 2</code>) make a second version.</div>
    <div class="grid2" style="grid-template-columns:1fr 1fr"><div class="field"><label>Basic set</label><textarea class="short" id="msBasic" rows="6">${esc(((cfg.mood_sets || {}).basic || []).map((m) => (cfg.mood_looks || {})[m.replace(/\s*\d+$/, "")] ? m + ": " + cfg.mood_looks[m.replace(/\s*\d+$/, "")] : m).join("\n"))}</textarea></div>
      <div class="field"><label>Extended set</label><textarea class="short" id="msExt" rows="6">${esc(((cfg.mood_sets || {}).extended || []).map((m) => (cfg.mood_looks || {})[m.replace(/\s*\d+$/, "")] ? m + ": " + cfg.mood_looks[m.replace(/\s*\d+$/, "")] : m).join("\n"))}</textarea></div>
    </div>
    <div class="hint">Every image category (Expressions, Actions, Positions, Finishes…) is a batch set of its own, listed as “All: …” wherever you pick what to draw. Rename, add or change categories under <b>Image categories</b> above.</div>
    <h3>Picture services</h3>
    <div class="hint">Click a service to open its settings. The model you pick here is the same one chosen under <b>AI &amp; story</b> (changing either changes both). Model names change often: if one stops working, pick another from the list, then use Test.</div>
    <div id="iprovs">${[...provs.keys()].sort((x, y) => { const o = ["gemini", "xai", "openai", "comfyui"], f = (k) => { const n = o.indexOf(provs[k].id); return n < 0 ? 9 : n; }; return f(x) - f(y); }).map((i) => imgProvCard(provs[i], i)).join("")}</div>
    <h3>Backgrounds and places</h3>
    <label class="chk top"><input type="checkbox" id="imgBgOn" ${(cfg.image_bg || {}).on ? "checked" : ""}><span><b>Use a separate picture maker for backgrounds and places</b><span class="hint">Scene backdrops and the fixed Places pictures are drawn by a different local model than your characters, for example FLUX.2 klein, which draws scenery well. It runs on the same ComfyUI as the local picture maker below. Characters, scene pictures and covers keep using the picture maker you chose. Off by default.</span></span></label>
    <div id="imgBgBox" class="${(cfg.image_bg || {}).on ? "" : "hidden"}">
      <label>Diffusion model <small class="hint">(a file in ComfyUI's diffusion_models folder)</small></label>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><select id="imgBgModel" style="min-width:260px"><option value="${esc((cfg.image_bg || {}).model || "flux-2-klein-4b-fp8.safetensors")}">${esc((cfg.image_bg || {}).model || "flux-2-klein-4b-fp8.safetensors")}</option></select><span class="hint" id="imgBgNote"></span></div>
      <label>Workflow <small class="hint">(ComfyUI "Save (API Format)" JSON with {{prompt}} {{seed}} {{width}} {{height}} {{model}}. filled in for you when you pick a model: FLUX.2, Chroma, Qwen-Image or a checkpoint. Empty = the built-in one for the chosen model)</small></label>
      <textarea class="short" id="imgBgFlow" rows="4" placeholder="Empty = the built-in workflow for the chosen model">${esc((cfg.image_bg || {}).workflow || "")}</textarea>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="imgBgStarter" type="button">Reset to the built-in workflow for this model</button><button id="imgBgTest" type="button">Test: draw a small backdrop</button></div>
      <div id="imgBgOut"></div>
    </div>
    ${typeof story !== "undefined" && story && story.nsfw ? `<h3>Adult (NSFW) pictures</h3>
    <label class="chk top"><input type="checkbox" id="imgNsOn" ${(cfg.image_nsfw || {}).on ? "checked" : ""}><span><b>Use a different picture maker for explicit pictures</b><span class="hint">Shown because this story has adult content on (Settings → AI &amp; story). Used only in stories with adult content on, and only for pictures that are explicit (a scene or picture type that mentions nudity or sex). Everything else keeps using your normal picture makers. Off by default.</span></span></label>
    <div id="imgNsBox" class="${(cfg.image_nsfw || {}).on ? "" : "hidden"}">
      <label>Picture maker for explicit pictures</label>
      <select id="imgNsMaker" style="min-width:260px">${(cfg.image_nsfw || {}).maker ? `<option value="${esc(cfg.image_nsfw.maker)}">${esc(cfg.image_nsfw.maker.replace("|", ": "))}</option>` : '<option value="">Loading…</option>'}</select>
      <details class="fold" id="imgNsTune" open><summary><b>Settings for this model (explicit pictures only)</b></summary>
        <div class="hint"><b>These settings are used only when this model draws an explicit picture.</b> They do not change the model when it draws anything else (its ordinary settings are under Picture services above), and each model keeps its own set.</div>
        ${[["ref_weight", "Face match strength (how closely the face follows Normal 1)", 0, 1.6, 0.05, 0.85],
           ["ref_end", "Face match stops at (later = stronger hold on the face)", 0.3, 1, 0.05, 0.95],
           ["ref_count", "Reference pictures used (Normal 1 first)", 1, 3, 1, 1],
           ["cfg", "Guidance (how literally it follows the prompt)", 1, 15, 0.5, 6],
           ["steps", "Steps (quality vs. speed)", 10, 60, 1, 28],
           ["clip_skip", "CLIP skip (Pony models need 2)", 1, 3, 1, 1],
           ["hires_denoise", "Refine strength (second pass)", 0.1, 0.8, 0.05, 0.45]].map(([k, l, mn, mx, st, dv]) =>
          `<div class="field nsslider"><label>${l}: <b data-v="${k}"></b></label><input type="range" data-n="${k}" min="${mn}" max="${mx}" step="${st}" data-def="${dv}"></div>`).join("")}
        <div class="grid2">
          <div class="field"><label>Face match scaling</label><select data-n="ref_scaling"><option value="">Workflow's</option><option>V only</option><option>K+V</option><option>K+V w/ C penalty</option><option>K+mean(V) w/ C penalty</option></select></div>
          <div class="field"><label>Face match blend</label><select data-n="ref_type"><option value="">Workflow's</option><option>linear</option><option>ease in-out</option><option>strong middle</option><option>weak input</option><option>style transfer</option></select></div>
          <div class="field"><label>Crop the reference to the face</label><select data-n="ref_crop"><option value="face">Yes</option><option value="">No</option></select></div>
          <div class="field"><label>Refine pass</label><select data-n="refine"><option value="">On</option><option value="off">Off</option></select></div>
          <div class="field"><label>Picture shape</label><select data-n="shape"><option value="">Story's</option><option value="portrait">Portrait (tall)</option><option value="square">Square</option><option value="landscape">Landscape (wide)</option></select></div>
        </div>
        <label>Art style <small class="hint">(empty = match the story's art style; type a style to draw this model's explicit pictures in it instead; the face still follows Normal 1)</small></label><input data-n="style_override" placeholder="e.g. illustration, digital art, clean lines, soft shading">
        <label>LoRAs <small class="hint">(from ComfyUI's loras folder, for this model's explicit pictures only: name @ strength; a second one after a semicolon. Empty = none)</small></label><input data-n="loras" placeholder="e.g. Realistic_People_V1.safetensors @ 0.35">
        <label>Extra words (style)</label><input data-n="extra_positive" placeholder="e.g. semi-realistic, painterly, soft shading">
        <label>Extra negative words</label><input data-n="extra_negative" placeholder="e.g. chibi, flat color, 3d">
        <div class="actions" style="justify-content:flex-start"><button type="button" id="imgNsTuneReset">Reset this model's explicit settings</button></div>
      </details>

      <label>Extra words added to its prompts <small class="hint">(empty = the app's choice for that model, shown faded)</small></label>
      <input id="imgNsWords" value="${esc((cfg.image_nsfw || {}).words || "")}" placeholder="nsfw, explicit, uncensored, nude">
      <label>Picture types and words that count as explicit <small class="hint">(comma separated, whole words or phrases; a short core list such as nude, naked and sex always counts)</small></label>
      <textarea class="short" id="imgNsTypes" rows="4" placeholder="Loading the built-in list…">${esc((cfg.image_nsfw || {}).types || "")}</textarea>
      <div class="actions" style="justify-content:flex-start"><button type="button" id="imgNsTypesReset">Restore the built-in list</button></div>
      <div class="hint" id="imgNsNote"></div>
    </div>` : ""}`;
}
// Family of a checkpoint / model by its name: "anime", "real" or "" (unknown). Used to suggest art styles and the prompt style.
function modelFamily(id) {
  const n = (id || "").toLowerCase();
  if (/anim|illustri|noob|pony|waifu|anything|counterfeit|meina|hassaku|danbooru|cartoon|toon/.test(n)) return "anime";
  if (/real|photo|juggernaut|epic|cyber|lifelike|raw|dreamshaper|sdxl_base|flux/.test(n)) return "real";
  return "";
}
function bindImageSettings(cfg, picker) {
  const cardOf = (pid) => document.querySelector(`.iprov[data-pid="${pid}"]`);
  if ($("imMoodTypes")) $("imMoodTypes").onclick = () => showMoodTypes(null);
  if ($("imgBgOn")) {   // separate backdrop maker (FLUX): the box only shows when the player wants it
    $("imgBgOn").onchange = () => $("imgBgBox").classList.toggle("hidden", !$("imgBgOn").checked);
    // The workflow box follows the model: picking a model fills in the built-in workflow that suits it (FLUX.2, Chroma, Qwen-Image, or an ordinary checkpoint).
    // A workflow you edited or pasted yourself is never replaced.
    const flow = $("imgBgFlow"), norm = (t) => { try { return JSON.stringify(JSON.parse(t)); } catch (e) { return t.trim(); } };
    let stock = [];
    const isStock = () => !flow.value.trim() || stock.map(norm).includes(norm(flow.value));
    const loadFlow = async (force) => {
      const m = $("imgBgModel").value; if (!m || !(force || isStock())) return;
      try { flow.value = JSON.stringify(await api("/images/model-workflow?model=" + encodeURIComponent(m)), null, 2); } catch (e) { /* keep what is there */ }
    };
    api("/images/bg-models").then((r) => {
      const sel = $("imgBgModel"), cur = sel.value, names = r.models.includes(cur) || !cur ? r.models : [cur, ...r.models];
      stock = r.stock || [];
      const tag = (n) => ({ chroma: " (Chroma)", qwen: " (Qwen-Image)", flux2: " (FLUX.2)", checkpoint: " (checkpoint)" })[(r.kinds || {})[n]] || "";
      if (r.models.length) { sel.innerHTML = names.map((n) => `<option value="${esc(n)}">${esc(n)}${tag(n)}</option>`).join(""); sel.value = cur && names.includes(cur) ? cur : names[0]; }
      $("imgBgNote").textContent = r.up ? (r.models.length ? "" : "ComfyUI is running but has no diffusion models yet.") : "ComfyUI isn't running, so the list can't be read; start it and reopen Settings.";
      loadFlow(false);
    }).catch(() => {});
    $("imgBgModel").onchange = () => loadFlow(false);
    $("imgBgStarter").onclick = () => loadFlow(true);
    $("imgBgTest").onclick = async (e) => {
      const out = $("imgBgOut"), btn = e.target; btn.disabled = true; out.textContent = "Drawing… (the first picture loads the model and can take a minute)";
      try { const r = await api("/image-test-bg", { method: "POST", body: { settings: { model: $("imgBgModel").value, workflow: $("imgBgFlow").value } } }); out.innerHTML = `<img class="mood" style="height:160px;border-radius:8px" src="${r.image}" alt="">`; }
      catch (err) { out.textContent = "Failed: " + err.message; }
      btn.disabled = false;
    };
  }
  if ($("imgNsOn")) {   // adult picture maker: every model a picture service offers, by "provider|model"
    $("imgNsOn").onchange = () => $("imgNsBox").classList.toggle("hidden", !$("imgNsOn").checked);
    const hint = () => {
      const v = $("imgNsMaker").value, m = v.split("|")[1] || "", f = modelFamily(m);
      $("imgNsWords").placeholder = /flux[._ -]?2/i.test(m) || !v.startsWith("comfyui") ? "Explicit, uncensored adult image: nudity and sexual content shown openly, adults only." : /pony/i.test(m) ? "rating_explicit, nsfw, explicit, uncensored, nude" : f === "anime" ? "nsfw, explicit, uncensored, nude" : "Explicit, uncensored adult image: nudity and sexual content shown openly, adults only.";
      const opts = [...$("imgNsMaker").options].map((o) => o.value), pony = opts.find((x) => /pony/i.test(x));
      $("imgNsNote").innerHTML = /flux[._ -]?2/i.test(m) ? `<span style="color:var(--danger)">⚠ FLUX.2 klein is trained to avoid nudity: it softens or covers explicit content whatever the prompt says.</span> ${pony ? `Choose <b>${esc(pony.split("|")[1])}</b> (recommended) instead.` : "Choose an SDXL checkpoint trained on adult content instead (a Pony or Illustrious model)."}`
        : !v.startsWith("comfyui") && v ? "Online picture services usually refuse explicit pictures; a local checkpoint trained on adult content works best."
        : /pony/i.test(m) ? "Good choice: Pony models are trained on adult content and get their own score and rating tags automatically." : v ? "Tip: a checkpoint trained on adult content (Pony, Illustrious) follows explicit prompts best." : "";
    };
    api("/model-options").then((o) => {
      const cur = (cfg.image_nsfw || {}).maker || "", opts = [];
      for (const p of o.images || []) for (const m of p.models || []) opts.push([`${p.id}|${m.id}`, `${p.name || p.id}: ${m.id}${m.kind === "qwen" ? " (Qwen-Image: text only)" : m.kind === "chroma" ? " (Chroma: uncensored, text only)" : m.kind === "flux" ? " (FLUX.2: avoids nudity)" : p.local && /pony/i.test(m.id) ? " (recommended for explicit pictures)" : ""}`]);
      opts.sort((a, b) => (/pony/i.test(b[0]) ? 1 : 0) - (/pony/i.test(a[0]) ? 1 : 0));      // adult-trained models first
      if (cur && !opts.some((x) => x[0] === cur)) opts.unshift([cur, cur.replace("|", ": ")]);
      $("imgNsMaker").innerHTML = opts.length ? opts.map(([v, t]) => `<option value="${esc(v)}">${esc(t)}</option>`).join("") : '<option value="">No picture models found (start ComfyUI or add a key)</option>';
      if (cur) $("imgNsMaker").value = cur;
      hint();
      if (window.__nsfwFill) window.__nsfwFill();
    }).catch(() => {});
    $("imgNsMaker").onchange = hint;
    // per-model explicit-picture settings: own values in cfg.image_nsfw.tweaks[model]; empty boxes show the tuned value faded
    const nsT = JSON.parse(JSON.stringify((cfg.image_nsfw || {}).tweaks || {}));
    let nsModel = "";
    const tuneBoxes = () => [...document.querySelectorAll("#imgNsTune [data-n]")];
    const keepTune = () => { if (!nsModel) return; const o = {}; tuneBoxes().forEach((b) => { if (b.type === "range" ? b.dataset.set === "1" : b.value.trim()) o[b.dataset.n] = b.value.trim(); }); nsT[nsModel] = o; };
    const showV = (b) => { const v = document.querySelector(`#imgNsTune [data-v="${b.dataset.n}"]`); if (v) v.textContent = b.dataset.set === "1" ? b.value : `${b.value} (default)`; };
    const fillTune = async () => {
      keepTune();
      const v = $("imgNsMaker").value; nsModel = v.split("|")[1] || "";
      if (!nsModel) return;
      let start = {}; try { start = (await api(`/images/nsfw-tuned?model=${encodeURIComponent(nsModel)}`)).tweaks || {}; } catch (e) { /* none */ }
      const mine = nsT[nsModel] || (Object.keys(nsT).includes(nsModel) ? {} : { ...start });
      tuneBoxes().forEach((b) => {
        const has = mine[b.dataset.n] !== undefined && mine[b.dataset.n] !== "";
        if (b.type === "range") { b.value = has ? mine[b.dataset.n] : b.dataset.def; b.dataset.set = has ? "1" : ""; showV(b); b.oninput = () => { b.dataset.set = "1"; showV(b); }; }
        else b.value = has ? mine[b.dataset.n] : (b.dataset.n === "ref_crop" ? "face" : "");
      });
      $("imgNsTune").querySelector("summary b").textContent = `Settings for ${nsModel} (explicit pictures only)`;
    };
    $("imgNsTuneReset").onclick = () => { if (!nsModel) return; delete nsT[nsModel]; nsModel = ""; fillTune(); };
    const oldMakerChange = $("imgNsMaker").onchange;
    $("imgNsMaker").onchange = () => { if (oldMakerChange) oldMakerChange(); fillTune(); };
    window.__nsfwFill = fillTune;
    $("imgNsOn").dataset.collect = "1";
    window.__nsfwTweaks = () => { keepTune(); return nsT; };
    api("/images/nsfw-types").then((r) => {     // an empty box means the built-in list: show it so it can be read and edited
      if (!$("imgNsTypes").value.trim()) $("imgNsTypes").value = r.types;
      $("imgNsTypesReset").onclick = () => { $("imgNsTypes").value = r.types; };
    }).catch(() => {});
  }
  // Fill each card's model box with the models that service really offers, and keep it equal to the AI & story choice.
  const sync = () => {
    const o = picker && picker.opts(); if (!o) return;
    const ch = picker.choice("images");
    for (const q of o.images) {
      const card = cardOf(q.id); if (!card) continue;
      const p = cfg.image_providers[+card.dataset.i];
      const box = card.querySelector(".imodel"), cur = box.querySelector("[data-f=model]");
      let want = ch && ch.provider === q.id && ch.model ? ch.model : (cur ? cur.value : p.model || "");
      if (q.models.length) {
        if (!q.models.some((m) => m.id === want)) want = p.model && q.models.some((m) => m.id === p.model) ? p.model : q.models[0].id;
        const ids = q.models.map((m) => m.id);
        if (cur && cur.tagName !== "SELECT") {
          const sel = document.createElement("select"); sel.dataset.f = "model";
          cur.replaceWith(sel);
          sel.onchange = () => {
            picker.setModel("images", q.id, sel.value); card.querySelector(".imnote").textContent = noteFor(q, sel.value);
            loadTweaks(card, p, sel.value);       // also switches the prompt style to the one remembered for this model (or the usual one for its kind)
            const f = q.local ? modelFamily(sel.value) : "", ps = card.querySelector('[data-f="prompt_style"]');
            if (ps && f && !card.querySelector(".mtweak")) ps.value = f === "anime" ? "tags" : "natural";
          };
        }
        const sel = box.querySelector("[data-f=model]");
        if (sel.dataset.filled !== ids.join("|")) { sel.innerHTML = q.models.map((m) => `<option value="${esc(m.id)}">${esc(m.id)}${m.kind === "qwen" ? " (Qwen-Image: sentence prompts, no references)" : m.kind === "chroma" ? " (Chroma: sentence prompts, no references)" : m.kind === "flux" ? " (FLUX.2: reference pictures)" : ""}</option>`).join(""); sel.dataset.filled = ids.join("|"); }
        sel.value = want;
      } else if (cur && ch && ch.provider === q.id && ch.model) cur.value = ch.model;
      card.querySelector(".ipstat").textContent = q.local ? (q.models.length ? (q.ready ? `running · ${q.models.length} model${q.models.length > 1 ? "s" : ""}` : "found, but " + (q.error || "not usable")) : "not running") : q.has_key ? (q.models.length ? "key saved" : "key saved, no models found") : "needs a key";
      card.querySelector(".imnote").textContent = noteFor(q, box.querySelector("[data-f=model]").value);
      const lp = card.querySelector(".lorapick");
      if (lp && lp.dataset.filled !== (q.loras || []).join("|")) { lp.innerHTML = (q.loras || []).map((l) => `<option value="${esc(l)}">${esc(l)}</option>`).join("") || '<option value="">(no LoRAs found in ComfyUI)</option>'; lp.dataset.filled = (q.loras || []).join("|"); }
      loadTweaks(card, p, box.querySelector("[data-f=model]").value);
      const pst = card.querySelector('[data-f="prompt_style"]');
      if (pst && !pst.dataset.bound) { pst.dataset.bound = "1"; pst.addEventListener("change", () => { pst.dataset.mine = card.dataset.tm || ""; const n = card.querySelector(".psnote"); if (n && card.dataset.tm) n.textContent = `Remembered for ${card.dataset.tm}: it switches back to this whenever you pick this model.`; }); }
      const key = card.querySelector('[data-f="api_key"]');
      if (key && q.has_key) key.placeholder = "••••••••";
    }
  };
  const noteFor = (q, model) => {
    if (!q.local) return "";
    if ((q.models.find((m) => m.id === model) || {}).kind === "qwen") return "Qwen-Image diffusion model: drawn with the app's Qwen-Image workflow (Qwen2.5-VL text encoder, a negative prompt, sentence-style prompts, no reference pictures). Per-checkpoint settings below don't apply to it.";
    if ((q.models.find((m) => m.id === model) || {}).kind === "chroma") return "Chroma diffusion model: drawn with the app's Chroma workflow (T5 text encoder, a negative prompt, sentence-style prompts, no reference pictures). Per-checkpoint settings below don't apply to it.";
    if ((q.models.find((m) => m.id === model) || {}).kind === "flux") return "FLUX.2 diffusion model: drawn with the app's FLUX workflow, using saved pictures as references (sentence-style prompts). Per-checkpoint settings below don't apply to it.";
    const f = modelFamily(model);
    return f === "anime" ? "Anime-style checkpoint: tag-style prompts work best." : f === "real" ? "Realistic checkpoint: sentence-style prompts work best." : "";
  };
  if (picker) picker.ready.then(sync);
  document.querySelectorAll(".iprov").forEach((card) => {
    const p = cfg.image_providers[+card.dataset.i];
    card.addEventListener("change", (e) => { if (e.target.matches && e.target.matches("[data-f=model]")) loadTweaks(card, p, e.target.value); });
    const la = card.querySelector(".loraadd");
    if (la) la.onclick = () => { const pick = card.querySelector(".lorapick").value, box = card.querySelector('[data-t="loras"]'); if (!pick) return; const line = `${pick} @ ${card.querySelector(".lorast").value || 1}`; if (!box.value.split("\n").some((l) => l.split("@")[0].trim() === pick)) box.value = (box.value.trim() ? box.value.trim() + "\n" : "") + line; };
    const st = card.querySelector(".starter");
    const sf = card.querySelector(".starterface");
    if (sf) sf.onclick = async () => { card.querySelector('[data-f="workflow_ref"]').value = JSON.stringify(await api("/images/starter-workflow?kind=face"), null, 2); };
    if (st) st.onclick = async () => { card.querySelector('[data-f="workflow"]').value = JSON.stringify(await api("/images/starter-workflow"), null, 2); };
    card.querySelector(".itest").onclick = async (e) => {
      const out = card.querySelector(".itestout"), btn = e.target; btn.disabled = true; out.textContent = "Drawing…";
      const settings = {};
      card.querySelectorAll("[data-f]").forEach((inp) => settings[inp.dataset.f] = inp.type === "checkbox" ? inp.checked : inp.value);
      flushTweaks(card, p); if (p.model_tweaks) settings.model_tweaks = p.model_tweaks;   // test with the unsaved per-checkpoint settings too
      try { const r = await api("/image-test", { method: "POST", body: { provider: p.id, settings } }); out.innerHTML = `<img class="mood" style="height:140px;border-radius:8px" src="${r.image}" alt="">`; }
      catch (err) { out.textContent = "Failed: " + err.message; }
      btn.disabled = false;
    };
  });
  return sync;
}
function collectImageSettings(cfg) {
  const looks = {};
  const lines = (id) => $(id).value.split("\n").map((x) => x.trim()).filter(Boolean).map((x) => {
    const i = x.indexOf(":");   // "name: what to draw"
    const name = (i < 0 ? x : x.slice(0, i)).trim().toLowerCase();
    if (i >= 0 && x.slice(i + 1).trim()) looks[name.replace(/\s*\d+$/, "")] = x.slice(i + 1).trim();
    return name;
  }).filter(Boolean);
  cfg.mood_sets = { ...(cfg.mood_sets || {}), basic: lines("msBasic"), extended: lines("msExt") };
  cfg.mood_looks = looks;
  cfg.image_max_refs = +$("imgRefs").value || 0;
  if ($("imgBgOn")) cfg.image_bg = { on: $("imgBgOn").checked, model: $("imgBgModel").value, workflow: $("imgBgFlow").value.trim() };
  if ($("imgNsOn")) cfg.image_nsfw = { on: $("imgNsOn").checked, maker: $("imgNsMaker").value, words: $("imgNsWords").value.trim(), types: $("imgNsTypes").value.trim(),
    style_mode: (cfg.image_nsfw || {}).style_mode || "match", style: (cfg.image_nsfw || {}).style || "", tweaks: window.__nsfwTweaks ? window.__nsfwTweaks() : ((cfg.image_nsfw || {}).tweaks || {}) };
  document.querySelectorAll(".iprov").forEach((card) => {
    const p = cfg.image_providers[+card.dataset.i];
    card.querySelectorAll("[data-f]").forEach((inp) => { p[inp.dataset.f] = inp.type === "checkbox" ? inp.checked : inp.value; });
    p.prompt_style_model = p.model || "";      // the service-wide style above was set for this model
    flushTweaks(card, p);   // (does nothing if the boxes were never filled, so saved settings are kept)
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
    ${(story.art || {}).direction ? "" : styleSelectHtml("artDlgDir", "no art style is set for this story yet: pick one so every character matches") + '<textarea class="short" id="artDlgDir" rows="2" placeholder="Art direction (filled in when you pick a style)"></textarea><div class="actions" style="justify-content:flex-start"><button id="artDlgSave">Use this style</button><span class="hint" id="artDlgNote"></span></div>'}
    <div id="artCards">${items.map(card).join("") || '<p class="hint">Every named character already has art.</p>'}</div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  const sv = document.getElementById("artDlgSave");
  if (sv) sv.onclick = async () => {
    const v = document.getElementById("artDlgDir").value.trim();
    if (!v) { document.getElementById("artDlgNote").textContent = "Pick a style or type one first."; return; }
    story = await api(`/stories/${story.id}/art`, { method: "PUT", body: { ...story.art, direction: v, setting: story.art.setting || null } });
    document.getElementById("artDlgNote").textContent = "✔ Saved. Every picture of this story now follows it.";
  };
  document.getElementById("mClose").onclick = () => {
    for (const [n, urls] of Object.entries(cands)) {   // options that were drawn but not picked are not kept
      const ch = (story.cast || []).find((c) => c.name.toLowerCase() === n.toLowerCase());
      if (ch && urls.length) api(`/stories/${story.id}/cast/${ch.slug}/candidates`, { method: "DELETE" }).catch(() => {});
    }
    refreshArtSuggestions(); if (opts.onClose) opts.onClose(); else closeModal();
  };
  const cands = {};   // name -> candidate urls drawn so far

  const showCandidates = (el, name) => {
    const urls = cands[name] || [];
    el.querySelector(".aResult").innerHTML = urls.length ? `<div class="gallery">${urls.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="aUse primary" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <label>After you pick one, also draw</label><select class="aSet"><option value="none">No more pictures (just this one)</option><option value="basic" ${(story.art || {}).extended_moods ? "" : "selected"}>The basic set</option><option value="extended" ${(story.art || {}).extended_moods ? "selected" : ""}>The extended set</option>${categorySetOptions("All: ")}<option value="match">The images my other characters have</option></select>
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
      const hadOthers = Object.keys(ch.moods || {}).some((m) => m !== "normal 1");
      story = await api(`/stories/${story.id}/cast/${ch.slug}/choose`, { method: "POST", body: { url, mood: "normal 1" } });
      el.querySelector(".aResult").innerHTML = ""; el.querySelectorAll("button, textarea, input").forEach((x) => x.disabled = true);
      status.textContent = `✔ ${name} now has a picture.`;
      renderAll();
      if (hadOthers && await offerRedrawAll(story.cast.find((c) => c.slug === ch.slug), (m) => { if (document.body.contains(status)) status.textContent = "✔ Chosen. " + m; })) {
        if (document.body.contains(status)) status.textContent = `✔ ${name} is done: all pictures redrawn from the new Normal 1.`;
      } else if (set !== "none") {
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


// ---------- Places: fixed pictures of rooms and locations the story returns to often ----------
function showPlaces(nav = {}) {   // nav.back: where to go when closed (the Art hub)
  if (!story) return;
  let places = (story.places || []).map((p) => ({ ...p }));
  const opts = {};   // place name -> option urls drawn so far
  const card = (p, i) => `<div class="artcard plcard" data-i="${i}">
    <div class="plhead"><input class="plName" value="${esc(p.name)}" placeholder="Name (e.g. Old library)" maxlength="60"><button class="x plDel" title="Remove this place">×</button></div>
    ${p.url ? `<img class="plthumb" src="${thumbUrl(p.url, 480)}" alt="">` : ""}
    <label>What it looks like <small class="hint">(what the artist is told; the narrator also sees this)</small></label>
    <textarea class="short plDesc" rows="2" maxlength="500" placeholder="e.g. A cold white pod room with a reclined link chair, ceiling arrays and a glass observation window.">${esc(p.description || "")}</textarea>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button class="primary plDraw">Draw 4 options</button><label class="btnlike">Upload…<input type="file" class="plUp" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label></div>
    <div class="aStatus hint plStatus"></div><div class="plOpts"></div></div>`;
  const draw = () => {
    $("plList").innerHTML = places.map(card).join("") || '<p class="hint">No places yet. Add the rooms the story keeps returning to.</p>';
  };
  modal(`<h2>Places</h2>
    <p class="hint">Fixed pictures of rooms and locations the story returns to often (a lab, an office, a cafeteria, bunks). Once a place has a picture, the narrator is told about it and shows it with a tag when a scene starts there, instead of drawing a new picture each time. Pictures show the place only, with nobody in it, in your story's art style. Places without a picture are not mentioned to the narrator.</p>
    <div id="plList"></div>
    <div class="actions" style="justify-content:flex-start"><button id="plAdd">＋ Add a place</button></div>
    <div class="aStatus hint" id="plNote"></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  draw();
  const gather = () => { document.querySelectorAll(".plcard").forEach((el) => { const p = places[+el.dataset.i]; if (p) { p.name = el.querySelector(".plName").value.trim(); p.description = el.querySelector(".plDesc").value.trim(); } }); };
  const save = async () => {
    gather();
    story = await api(`/stories/${story.id}/places`, { method: "PUT", body: { places: places.filter((p) => p.name).map((p) => ({ name: p.name, description: p.description })) } });
    places = story.places.map((p) => ({ ...p }));
  };
  const rerender = () => { draw(); Object.keys(opts).forEach((n) => showOpts(n)); };
  const showOpts = (name) => {
    const i = places.findIndex((p) => p.name.toLowerCase() === name.toLowerCase()); if (i < 0) return;
    const el = document.querySelector(`.plcard[data-i="${i}"] .plOpts`); if (!el) return;
    const urls = opts[name] || [];
    el.innerHTML = urls.length ? `<div class="gallery">${urls.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary plUse" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button class="plMore">Draw 4 more</button></div>` : "";
  };
  const drawOpts = async (el, fresh) => {
    const status = el.querySelector(".plStatus"); gather();
    const p = places[+el.dataset.i];
    if (!p.name || !p.description) { status.textContent = "Give the place a name and describe what it looks like first."; return; }
    status.textContent = "Saving…"; el.querySelectorAll("button").forEach((b) => b.disabled = true);
    let msg = "";
    try {
      await save();
      const r = await api(`/stories/${story.id}/places/candidates`, { method: "POST", body: { name: p.name, count: 4, fresh } });
      const job = await watchJob(r.job, (j) => { if (document.body.contains(status)) status.textContent = j.current || "Drawing…"; });
      if (job.status === "error") msg = "Could not draw: " + job.error;
      else { opts[p.name] = fresh ? job.result.candidates : (opts[p.name] || []).concat(job.result.candidates); msg = "Pick one, or change the description and draw again."; }
    } catch (e) { msg = "Could not draw: " + e.message; }
    rerender();
    const again = [...document.querySelectorAll(".plcard")].find((c) => c.querySelector(".plName").value.trim().toLowerCase() === p.name.toLowerCase());
    if (again) again.querySelector(".plStatus").textContent = msg;   // redrawing the cards cleared the line
  };
  $("plAdd").onclick = () => { gather(); places.push({ name: "", description: "", url: "" }); draw(); Object.keys(opts).forEach((n) => showOpts(n)); };
  $("mClose").onclick = async () => { try { await save(); } catch (e) { /* keep what was saved */ } closeModal(); renderAll(); if (nav.back) nav.back(); };
  $("plList").addEventListener("click", async (e) => {
    const b = e.target.closest("button"), el = e.target.closest(".plcard"); if (!b || !el) return;
    const i = +el.dataset.i;
    if (b.classList.contains("plDraw")) drawOpts(el, true);
    else if (b.classList.contains("plMore")) drawOpts(el, false);
    else if (b.classList.contains("plDel")) {
      gather(); const p = places[i];
      if (p.name && !confirm(`Remove “${p.name}” and its picture?`)) return;
      places.splice(i, 1); try { await save(); } catch (err) { $("plNote").textContent = err.message; } rerender();
    } else if (b.classList.contains("plUse")) {
      gather(); const p = places[i];
      try { await save(); story = await api(`/stories/${story.id}/places/choose`, { method: "POST", body: { name: p.name, url: b.dataset.url } }); places = story.places.map((x) => ({ ...x })); delete opts[p.name]; renderAll(); } catch (err) { $("plNote").textContent = err.message; }
      rerender();
    }
  });
  $("plList").addEventListener("change", async (e) => {
    if (!e.target.classList.contains("plUp")) return;
    const el = e.target.closest(".plcard"), f = e.target.files[0]; if (!f) return; gather(); const p = places[+el.dataset.i];
    if (!p.name) { el.querySelector(".plStatus").textContent = "Name the place first."; return; }
    try {
      await save();
      const r = await fetch(`/api/stories/${story.id}/places/${encodeURIComponent(p.name)}/picture`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Upload failed");
      story = await r.json(); places = story.places.map((x) => ({ ...x })); renderAll(); rerender();
    } catch (err) { el.querySelector(".plStatus").textContent = err.message; }
  });
}


// Fill a character's whole sheet from the story: a warning first (not for early in a story), then the AI's suggestion for each box, which you review before anything is saved.
const SHEET_FIELDS = [["appearance", "How they look", 3], ["personality", "Personality", 1], ["bio", "Bio", 4], ["address", "Names and forms of address", 2], ["aka", "Also called (separated by commas)", 1], ["species", "Species (empty = human)", 1]];
async function sheetFill(slug, back) {
  const c = (story.cast || []).find((x) => x.slug === slug); if (!c) return;
  let info; try { info = await api(`/stories/${story.id}/cast/${slug}/sheet-info`); } catch (e) { alert(e.message); return; }
  modal(`<h2>✨ Fill ${esc(c.name)}'s whole sheet from the story</h2>
    <div class="trow" style="border-color:#e8b04a"><b>⚠ Do not use this early in a story.</b>
      <p class="hint" style="margin:4px 0 0">The AI can only use what the story has shown so far. Early on there is too little, so it guesses, and a guess you save becomes a fact the narrator repeats every turn. Use it once the character has appeared in many scenes. Nothing is saved until you have read and approved each box.</p></div>
    <p><b>${info.replies}</b> narrator replies so far; ${esc(c.name)} appears in <b>${info.mentions}</b> of them.</p>
    ${info.too_early ? `<p style="color:#e8b04a"><b>Too early:</b> ${info.reasons.map(esc).join("; ")}.</p><label class="chk"><input type="checkbox" id="sfSure"> I understand the risk, do it anyway</label>` : `<p class="hint">That is enough for a reasonable first draft.</p>`}
    <label>Which boxes?</label><select id="sfMode"><option value="empty">Only the empty boxes (keep what I wrote)</option><option value="all">Every box (suggest a change for everything)</option></select>
    <label class="chk"><input type="checkbox" id="sfEvents"> Also rebuild ${esc(c.name)}'s key-event log (one more call; your own entries are kept)</label>
    <div class="hint" id="sfMsg"></div>
    <div class="actions"><button id="sfBack">Cancel</button><span style="flex:1"></span><button class="primary" id="sfGo">Read the story and suggest</button></div>`);
  $("sfBack").onclick = back;
  if (info.too_early) $("sfGo").disabled = true;
  if ($("sfSure")) $("sfSure").onchange = () => { $("sfGo").disabled = !$("sfSure").checked; };
  $("sfGo").onclick = async (e) => {
    e.target.disabled = true; $("sfMsg").textContent = "Reading what the story says about them…";
    let r; try { r = await api(`/stories/${story.id}/cast/${slug}/sheet-suggest`, { method: "POST" }); } catch (err) { $("sfMsg").textContent = err.message; e.target.disabled = false; return; }
    const rebuild = $("sfEvents").checked, all = $("sfMode").value === "all";
    modal(`<h2>${esc(c.name)}: suggested sheet</h2><p class="hint">Edit anything, untick what you do not want. Nothing is saved until you press Apply.</p>
      ${SHEET_FIELDS.map(([k, label, rows]) => {
        const now = r.current[k] || "", sug = r.fields[k] || "", on = sug && (all || !now);
        return `<div class="trow"><label class="chk"><input type="checkbox" data-sf="${k}" ${on ? "checked" : ""} ${sug ? "" : "disabled"}> <b>${label}</b></label>
          ${rows > 1 ? `<textarea class="short" data-sv="${k}" rows="${rows}" ${sug ? "" : "disabled"}>${esc(sug)}</textarea>` : `<input data-sv="${k}" value="${esc(sug)}" ${sug ? "" : "disabled"}>`}
          <div class="hint">${now ? "Now: " + esc(oneLine(now, 240)) : "Now: empty"}${sug ? "" : " · the story does not say enough"}</div></div>`;
      }).join("")}
      <div class="hint" id="sfMsg2"></div><div class="actions"><button id="sfBack2">Cancel</button><span style="flex:1"></span><button class="primary" id="sfApply">Apply the ticked boxes</button></div>`);
    $("sfBack2").onclick = back;
    $("sfApply").onclick = async (e2) => {
      const edit = { name: c.name, appearance: c.appearance || "", slug };
      document.querySelectorAll("[data-sf]").forEach((cb) => { if (cb.checked) edit[cb.dataset.sf] = document.querySelector(`[data-sv="${cb.dataset.sf}"]`).value; });
      if (Object.keys(edit).length <= 3) { $("sfMsg2").textContent = "Nothing is ticked."; return; }
      e2.target.disabled = true;
      try {
        story = await api(`/stories/${story.id}/cast`, { method: "PUT", body: { cast: (story.cast || []).map((x) => x.slug === slug ? edit : { name: x.name, appearance: x.appearance, slug: x.slug }) } });
        if (rebuild) { $("sfMsg2").textContent = "Rebuilding the key events…"; story = await api(`/stories/${story.id}/cast/${slug}/events/rebuild`, { method: "POST" }); }
        renderAll(); back();
      } catch (err) { $("sfMsg2").textContent = err.message; e2.target.disabled = false; }
    };
  };
}
