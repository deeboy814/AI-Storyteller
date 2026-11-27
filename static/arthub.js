// 🖼 Art: every picture-making tool of the open story in one place.
//   Overview     - where each kind of picture is made (characters, your avatar, places, backgrounds, cover, intro panels, scene pictures) with its status
//   Style        - art direction, the story's setting, negative prompt and every picture option (the old "Art style" tab of the Player sheet)
//   Backgrounds  - the story's backdrop: draw it from the setting or the latest scene, upload one, remove it
//   Picture makers - which AI draws pictures, picture services, ComfyUI per-checkpoint settings, mood sets, the separate backgrounds maker
// The tools that already have their own screens (Cast, Places, the cover editor, the avatar sheet, picture settings) open from here and, where it makes
// sense, come back here when closed.
function showArtHub(tab = "Overview") {
  artBack = null;
  if (!story) return showMakersOnly();
  const a = story.art || {}, cast = (story.cast || []).filter((c) => !c.player);
  const withArt = cast.filter((c) => Object.keys(c.moods || {}).length).length;
  const need = cast.filter((c) => !Object.keys(c.moods || {}).length && (c.appearance || "").trim()).length;
  const places = story.places || [], placesArt = places.filter((p) => p.url).length, bd = story.backdrop || {};
  const bg = (config && config.image_bg) || {}, onoff = (v) => (v ? "on" : "off");
  const intro = (story.intro_images || []).length;
  const card = (icon, title, status, buttons) => `<div class="hubcard"><div class="hubhead"><span>${icon}</span><b>${title}</b></div><div class="hubstatus">${status}</div><div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px">${buttons}</div></div>`;
  modal(`<h2>🖼 Art</h2>
    <p class="hint">Everything that makes pictures for “${esc(story.title)}”. Pick what you want to make or change.</p>
    <div class="tabs"><button class="tab hubtab" data-tab="Overview">Overview</button><button class="tab hubtab" data-tab="Style">Style &amp; options</button><button class="tab hubtab" data-tab="Backgrounds">Backgrounds</button><button class="tab hubtab" data-tab="Makers">Picture makers</button></div>
    <div id="hubOverview" class="hubgrid">
      ${card("🎭", "Characters", `${cast.length} character${cast.length === 1 ? "" : "s"} · ${withArt} with pictures${need ? ` · <b>${need} need art</b>` : ""}`,
        `<button id="hCast">Cast &amp; pictures…</button><button id="aNew">Draw art for new characters…</button><button id="hGal">Gallery</button><button id="hSpecies">Species &amp; makers…</button><button id="hBin" title="Pictures and videos you removed or replaced, kept for 30 days">🗑 Picture bin</button>`)}
      ${(() => { const sb = story.statsheets || {}; if (!sb.on) return "";       // character sheets: shown only in stories that have them on
        const has = (c) => { const h = (sb.items || {})[c.slug]; return !!(h && ((h.stats || []).length || (h.notes || "").trim())); }, n = cast.filter(has).length, shown = cast.slice(0, 12);
        return card("📋", "Character sheets", `${n} of ${cast.length} character${cast.length === 1 ? "" : "s"} ${n === 1 ? "has" : "have"} a sheet · the narrator updates them: ${onoff(sb.auto !== false)}`,
          shown.map((c) => `<button class="hSheet" data-slug="${esc(c.slug)}" title="Open ${esc(c.name)}'s sheet">${has(c) ? "✓ " : ""}${esc(c.name)}</button>`).join("") + (cast.length > shown.length ? `<button id="hSheetMore">All characters…</button>` : "")); })()}
      ${card("🧑", "Your avatar", story.avatar_image ? "Has a picture" : "No picture yet", `<button id="hAvatar">Avatar picture…</button>`)}
      ${card("📍", "Places", `${places.length} place${places.length === 1 ? "" : "s"} · ${placesArt} with pictures`, `<button id="hPlaces">Manage places…</button>`)}
      ${card("🌄", "Backgrounds", `Showing: ${esc(bd.name || (bd.url ? "a picture you chose" : "nothing yet"))}<br>Changes automatically: ${onoff(a.auto_backdrop)}${a.dynamic_backdrop ? " (also for dramatic moments)" : ""} · screen display: ${onoff(config && config.ui_backdrop)}`, `<button id="hBg">Backgrounds…</button>`)}
      ${card("📖", "Cover", story.cover ? "Has a cover on the Home card" : "No cover yet", `<button id="hCover">Cover art…</button>`)}
      ${card("🖼", "Intro panels", `${intro} picture${intro === 1 ? "" : "s"} shown at the start of the story`, `<button id="hIntro">Intro panels…</button>`)}
      ${card("🎬", "Scene pictures", `Auto-illustrate: ${onoff(a.auto)} · a picture most turns: ${onoff(a.scene_often)}${(a.scene_gap || 1) > 1 ? " · one every " + a.scene_gap + " turns" : ""} · shown ${({ panel: "in the side panel", banner_top: "as a banner above the narration", banner_bottom: "as a banner below the narration" })[a.scene_where] || "under the reply"}`, `<button id="hIllus" ${busy ? "disabled" : ""}>Illustrate the latest reply</button><button id="hStyle1">Options…</button>`)}
      ${(() => { const cp = comfyProv(config || {});
        return card("🎞", "Videos", cp ? `Animate every new picture: <b>${onoff(cp.video_auto)}</b> (all stories) · <span id="hVidCount">counting…</span>` : "Videos are made with a local ComfyUI picture maker (Picture makers → Picture services → Local).",
          cp ? `<button id="hAnimAll" title="Make a short video of every picture in this story (characters, outfits, places, scene pictures, the cover), one after another. Pictures that already have a video are skipped.">🎬 Animate all pictures in this story</button><button id="hVidSet">Video settings…</button><span class="hint" id="hAnimNote" role="status"></span>` : `<button id="hVidSet">Picture makers…</button>`); })()}
    </div>
    <div id="hubStyle" class="hidden"><div id="hubArtStyle"></div><div id="hubArtStylePick" class="hidden"></div>${artTabHtml().replace('id="tabArt" class="hidden"', 'id="tabArt"').replace(/<div class="field"><label>Art style[\s\S]*?class="stylepick"[\s\S]*?<\/select><\/div>/, "")}
      <div class="actions sticky"><span class="hint" id="hubStyleNote"></span><span style="flex:1"></span><button class="primary" id="hubStyleSave">Save</button></div></div>
    <div id="hubBackgrounds" class="hidden"></div>
    <div id="hubMakers" class="hidden"></div>
    <div class="actions" style="margin-top:8px"><button id="mClose">Close</button></div>`);

  const show = (n) => {
    document.querySelectorAll(".hubtab").forEach((b) => b.classList.toggle("on", b.dataset.tab === n));
    for (const t of ["Overview", "Style", "Backgrounds", "Makers"]) $("hub" + t).classList.toggle("hidden", t !== n);
    if (n === "Backgrounds") drawBackgrounds();
    if (n === "Makers") mountMakers($("hubMakers"));
  };
  document.querySelectorAll(".hubtab").forEach((b) => b.onclick = () => show(b.dataset.tab));
  { // the art style list (it used to be in Settings): choosing one applies it to this story at once
    const ip = mountAIChooser($("hubArtStylePick"), { initial: currentChoices(), roles: ["images"] });     // hidden: only tells the list which picture maker is chosen
    const sbx = mountArtStyle($("hubArtStyle"), ip, (story.art || {}).direction || "");
    ip.ready.then(() => sbx.render());
    $("hubArtStyle").addEventListener("change", async (e) => {
      if (e.target.id !== "sArtStyle") return;
      const id = sbx.id(); if (!id) return;
      if (!confirm("Give this story that art style? It replaces the art direction" + ((ART_STYLES.find((x) => x.id === id) || {}).user ? ", the picture maker and the other settings saved in the style." : "."))) { sbx.render(); return; }
      try {
        story = await api(`/stories/${story.id}/art-styles/${encodeURIComponent(id)}/apply`, { method: "POST" }); config = await api("/config");
        config.default_art_direction = (story.art || {}).direction || ""; config = await api("/config", { method: "PUT", body: config });      // new stories start with it
        showArtHub("Style");
      } catch (err) { alert(err.message); }
    });
  }
  $("mClose").onclick = () => { closeModal(); renderAll(); applyTheme(config); refreshArtSuggestions(); };

  // ----- Overview: open the existing tools; most return here when closed
  const back = () => showArtHub("Overview");
  const via = (f) => () => { artBack = back; f(); };    // the screen that opens gets a "← Art menu" button (see modal() in app.js)
  $("hCast").onclick = via(() => showCast("Characters"));
  document.querySelectorAll(".hSheet").forEach((b) => b.onclick = via(() => showCharacter(b.dataset.slug, "Sheet")));
  if ($("hSheetMore")) $("hSheetMore").onclick = via(() => showCast("Characters"));
  $("hGal").onclick = via(() => showCast("Gallery"));
  $("hBin").onclick = () => showPictureBin(back);
  $("hSpecies").onclick = via(() => showSpecies({ back }));
  $("hIntro").onclick = via(() => showCast("Intro"));
  $("aNew").onclick = via(() => showArtDialog({ onClose: () => { renderAll(); back(); } }));
  $("hAvatar").onclick = via(() => showPlayer());
  $("hPlaces").onclick = via(() => showPlaces({ back }));
  $("hBg").onclick = () => show("Backgrounds");
  $("hCover").onclick = via(() => showCardEditor({ id: story.id, title: story.title, cover: story.cover || "", description: story.description || "" }, { back: async () => { try { story = await api(`/stories/${story.id}`); } catch (e) { /* keep */ } renderAll(); back(); } }));
  $("hIllus").onclick = () => { closeModal(); illustrate(); };
  if ($("hVidCount")) api(`/stories/${story.id}/animate-all`).then((c) => { if ($("hVidCount")) $("hVidCount").textContent = `${c.with_video} of ${c.total} picture${c.total === 1 ? "" : "s"} have a video`; }).catch(() => { if ($("hVidCount")) $("hVidCount").textContent = ""; });
  if ($("hAnimAll")) $("hAnimAll").onclick = (e) => animateAll("", e.target, $("hAnimNote"));
  $("hVidSet").onclick = async () => {      // open Picture makers at the Video generation section
    show("Makers");
    for (let i = 0; i < 50 && !$("vidSecEvery"); i++) await new Promise((r) => setTimeout(r, 100));
    const d = $("vidSecEvery") && $("vidSecEvery").closest("details");
    if (d) { d.open = true; d.scrollIntoView({ block: "start", behavior: "smooth" }); }
  };
  $("hStyle1").onclick = () => show("Style");

  // ----- Style & options: the story's art direction and every picture switch
  artTabInit();
  $("hubStyleSave").onclick = async () => {
    $("hubStyleNote").textContent = "Saving…";
    try { await artTabSave(); renderAll(); $("hubStyleNote").textContent = "✔ Saved."; }
    catch (err) { $("hubStyleNote").textContent = err.message; }
  };

  // ----- Backgrounds
  function drawBackgrounds() {
    const box = $("hubBackgrounds"), b = story.backdrop || {}, st = story.art || {};
    box.innerHTML = `<p class="hint">The picture behind the story on the sides of a wide window (shown while “Show the story's backdrop” is on in Settings → Appearance; it is ${config && config.ui_backdrop ? "on" : "off"} now). It can follow the scene automatically: after each reply the helper model says where the scene is, and a Places picture, an earlier background or a newly drawn one is used. ${bg.on ? `New ones are drawn with ${esc(bg.model || "the separate backdrop maker")}.` : "New ones are drawn with this story's picture maker."}</p>
      ${b.url ? `<img class="bdprev" src="${esc(b.url)}" alt=""><div class="hint">Now: ${esc(b.name || "chosen by you")}${b.source ? ` · ${esc(b.source)}` : ""}</div>` : '<div class="hint">No backdrop yet.</div>'}
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="bdSetting">✨ From the story's setting</button><button id="bdScene">✨ From the latest scene</button>
        <label class="btnlike">Upload…<input type="file" id="bdUp" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${b.url ? '<button id="bdDel">Remove</button>' : ""}</div>
      <div class="hint" id="bdNote">Drawing takes a few seconds with a local model. Environment only: nobody is in the picture.</div>
      <details class="sub" open><summary>Automatic background</summary>
        <label class="chk"><input type="checkbox" id="bdAuto" ${st.auto_backdrop ? "checked" : ""}> Change the background automatically: after each reply the helper model says where the scene is. A Places picture or an earlier background for that location is reused; a new location gets a new one from the backdrop maker. Needs “Show the story's backdrop” in Settings → Appearance (off by default).</label>
        <label class="chk"><input type="checkbox" id="bdDyn" ${st.dynamic_backdrop ? "checked" : ""} ${st.auto_backdrop ? "" : "disabled"}> Dramatic moments get their own background: when the helper sees an alarm, fire, power failure, launch or battle, it draws a variant of the current place (drawn once and reused), and the plain background returns afterwards. Draws more pictures (off by default).</label>
        <div class="hint" id="bdTogNote">These save as soon as you tick them. They add a short instruction for the helper only, not the narrator.</div></details>`;
    const note = (t) => { $("bdNote").textContent = t; };
    const gen = (source) => async (e) => {
      e.target.disabled = true; note("Drawing the backdrop…");
      try { story = await api(`/stories/${story.id}/backdrop/generate`, { method: "POST", body: { source } }); applyTheme(config); drawBackgrounds(); }
      catch (err) { note(err.message); e.target.disabled = false; }
    };
    $("bdSetting").onclick = gen("setting"); $("bdScene").onclick = gen("scene");
    $("bdUp").onchange = async (e) => {
      const f = e.target.files[0]; if (!f) return; note("Uploading…");
      try {
        const r = await fetch(`/api/stories/${story.id}/backdrop-image`, { method: "POST", headers: { "content-type": f.type }, body: f });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Upload failed");
        story = await r.json(); applyTheme(config); drawBackgrounds();
      } catch (err) { note(err.message); }
    };
    if ($("bdDel")) $("bdDel").onclick = async () => { story = await api(`/stories/${story.id}/backdrop`, { method: "DELETE" }); applyTheme(config); drawBackgrounds(); };
    const saveTog = async (k, v) => {
      try { story = await api(`/stories/${story.id}/art`, { method: "PUT", body: { [k]: v } }); $("bdTogNote").textContent = "✔ Saved."; }
      catch (err) { $("bdTogNote").textContent = err.message; }
    };
    $("bdAuto").onchange = async (e) => { $("bdDyn").disabled = !e.target.checked; await saveTog("auto_backdrop", e.target.checked); };
    $("bdDyn").onchange = (e) => saveTog("dynamic_backdrop", e.target.checked);
  }
  show(tab);
}

// Picture makers: which AI draws the pictures, the picture services (Gemini, Grok, OpenAI, ComfyUI), per-checkpoint settings, the mood sets and the
// separate backgrounds maker. (Formerly Settings → Pictures.) Without an open story it only changes the app-wide choices (Home screen).
async function mountMakers(box) {
  if (box.dataset.ready) return;
  box.dataset.ready = "1";
  box.innerHTML = '<div class="hint">Loading…</div>';
  config = await api("/config");
const fld = (label, control, help = "") => `<div class="field"><label>${label}</label>${control}${help ? `<div class="hint">${help}</div>` : ""}</div>`;
  box.innerHTML = `<h3>Which AI draws pictures${story ? ` in “${esc(story.title)}”` : ""}</h3><div id="mkPick"></div>
    ${imageSettingsHtml(config)}
    ${videoSettingsHtml(config)}
    <h3>Local picture generator (ComfyUI)</h3>
    <div class="hint">Its address and which checkpoint to use are in the picture makers above; this section controls when it starts and stops, and whether characters can borrow pictures from other stories. These are app-wide.</div>
    <label class="chk top"><input type="checkbox" id="sRtComfy" ${config.rt_comfy ? "checked" : ""}><span><b>Start and stop ComfyUI automatically</b><span class="hint">ComfyUI starts when you open a story that draws pictures locally (or when a picture is asked for), and shuts down after it has been idle for the time below. Starting takes about 15 to 30 seconds.</span></span></label>
    <div class="grid2">${fld("Idle time before it stops", `<input id="sRtIdle" type="number" min="1" step="1" value="${config.rt_comfy_idle_min ?? 15}">`, "minutes without drawing. Pictures are drawn in bursts, so a longer time than for text suits it.")}
      ${fld("When idle", `<select id="sRtMode"><option value="stop">Stop ComfyUI completely</option><option value="free">Keep it running, free its video memory</option></select>`, "Stopping frees the most; keeping it running restarts faster.")}<div></div></div>
    ${fld("How to start ComfyUI", `<input id="sRtCmd" value="${esc(config.rt_comfy_cmd || "")}" placeholder="~/ComfyUI/start.sh, or start.bat on Windows (default)">`, "A command or script path on this computer. Leave empty for the default.")}
    <label class="chk top"><input type="checkbox" id="sRtSwap" ${config.rt_swap !== false ? "checked" : ""}><span><b>Make room for pictures</b><span class="hint">If video memory is short when a local picture is about to be drawn, the local text model is unloaded first (it reloads at your next turn). Helpful on 12 GB cards that can't hold both.</span></span></label>
    <h3>Character art across stories</h3>
    <label class="chk top"><input type="checkbox" id="sReuse" ${config.reuse_art ? "checked" : ""}><span><b>Reuse character art across stories</b><span class="hint">Borrow a character's pictures from another story as a face reference (Cast → character → Images). Only identity carries over: each story's own art style and setting still apply, so the character looks like they belong to the new story.</span></span></label>
    <div class="actions sticky"><span class="hint" id="mkNote"></span><span style="flex:1"></span><button class="primary" id="mkSave">Save</button></div>`;
  $("sRtMode").value = config.rt_comfy_mode || "stop";
  collapsify(box);
  let sync = null;
  const picker = mountAIChooser($("mkPick"), { initial: currentChoices(), roles: ["images"], onChange: () => { if (sync) sync(); } });
  sync = bindImageSettings(config, picker);
  const dirty = () => { if ($("mkNote") && $("mkNote").textContent !== "Saving…") $("mkNote").textContent = "● Unsaved changes: press Save."; };
  box.addEventListener("input", dirty); box.addEventListener("change", dirty);
  $("mkSave").onclick = async (e) => {
    e.target.disabled = true; $("mkNote").textContent = "Saving…";
    try {
      collectImageSettings(config);
      config.rt_comfy = $("sRtComfy").checked; config.rt_comfy_idle_min = +$("sRtIdle").value || 15; config.rt_comfy_mode = $("sRtMode").value; config.rt_comfy_cmd = $("sRtCmd").value.trim(); config.rt_swap = $("sRtSwap").checked; config.reuse_art = $("sReuse").checked;
      await picker.ready;
      const chosen = picker.get();
      if (chosen.images) config.image_active = chosen.images.provider;
      config = await api("/config", { method: "PUT", body: config });
      if (chosen.images) {
        await api("/default-models", { method: "PUT", body: { ...(config.default_models || {}), images: chosen.images } });
        if (story) story.models = await api(`/stories/${story.id}/models`, { method: "PUT", body: { images: chosen.images } });
      }
      $("mkNote").textContent = "✔ Saved.";
    } catch (err) { $("mkNote").textContent = err.message; }
    e.target.disabled = false;
  };
}
function showMakersOnly() {
  modal(`<h2>🖼 Picture makers</h2><p class="hint">Which AI draws pictures, and its settings. Open a story and press 🖼 to reach the rest of the art tools.</p><div id="hubMakers"></div><div class="actions" style="margin-top:8px"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  mountMakers($("hubMakers"));
}

// Fold each "<h3>" section of a settings pane into a collapsible block (the first one open), so long panes can be skimmed and minimised.
function collapsify(pane, openFirst = true) {
  if (!pane) return;
  let cur = null, first = true;
  for (const k of [...pane.children]) {
    if (k.classList.contains("actions") && k === pane.lastElementChild) break;   // a closing Save row stays outside the folded sections, so it is always visible
    if (k.tagName === "H3") {
      cur = document.createElement("details"); cur.className = "sub"; cur.open = first && openFirst; first = false;
      const sm = document.createElement("summary"); sm.innerHTML = k.innerHTML; cur.appendChild(sm); pane.replaceChild(cur, k);
    } else if (cur) cur.appendChild(k);
  }
}

// The reference pictures a species card offers, by the species' gender: a species of one sex has just that one (its main picture); a mixed one has a general look plus a
// male and a female picture; every species can also have a daily-clothing and a combat-clothing picture. (The main picture is `reference_url`, the rest are `refs`.)
function speciesSlots(gender) {
  const sex = gender === "female" ? [["main", "Female", "Every member of this species is female."]]
    : gender === "male" ? [["main", "Male", "Every member of this species is male."]]
    : gender ? [["main", "Typical member", "What a member of this species looks like."]]
    : [["main", "General look", "Either sex: used when a character's sex is not known."], ["male", "Male", "Used for male characters."], ["female", "Female", "Used for female characters."]];
  return [...sex, ["daily", "Daily clothing", "What they wear every day."], ["combat", "Combat clothing", "Fighting gear: used for fight and action pictures."]];
}
const speciesRefUrl = (sp, slot) => (slot === "main" ? sp.reference_url : (sp.refs || {})[slot]) || "";

// Species: what is true of a whole species (written into every member's picture prompt), the picture maker it uses by default, and its reference pictures. The first
// picture drawn of a new species becomes its main reference by itself; later characters of the species keep its look but are drawn as their own person.
async function showSpecies(nav = {}) {
  const list = Object.entries(story.species || {}), cast = story.cast || [], choices = await makerChoices();
  const members = (name) => cast.filter((c) => ((c.species || "human").trim().toLowerCase() || "human") === name.toLowerCase());
  const card = ([key, sp]) => {
    const m = members(sp.name), withPic = m.filter((c) => Object.keys(c.moods || {}).length);
    return `<div class="aiBox spcard" data-key="${esc(key)}"><b>${esc(sp.name)}</b> <small class="muted">${m.length} character${m.length === 1 ? "" : "s"}: ${esc(m.map((c) => c.name).join(", ") || "none yet")}</small>
      <div class="spRefs"></div>
      <div class="bioTop spTop">
      <div style="flex:1"><label>What is true of the whole species <small class="hint">(skin or covering, eyes, crests, build, clothing: added to every member's picture prompt)</small></label><textarea class="short spDesc" rows="3">${esc(sp.description || "")}</textarea>
        <label>Story traits <small class="hint">(abilities, weaknesses, senses, customs: given to the narrator so it stays consistent)</small></label><textarea class="short spTraits" rows="3">${esc(sp.traits || "")}</textarea>
        <label>Colour palette <small class="hint">(the colours this species is drawn in: skin, hair, eyes, clothing. Added to every member's picture prompt)</small></label><textarea class="short spPalette" rows="2" placeholder="e.g. muted greys and deep teal, bronze trim, pale ash skin">${esc(sp.palette || "")}</textarea>
        <label>Clothing style <small class="hint">(what this species wears: cut, materials, gear. Added to every member's picture prompt)</small></label><textarea class="short spClothing" rows="2" placeholder="e.g. layered quilted robes, high collars, no bare arms">${esc(sp.clothing || "")}</textarea>
        <label>Gender of this species <small class="hint">(every member is drawn this way: the picture prompts get the matching words and tags, and the narrator is told)</small></label>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px;margin:2px 0 6px"><select class="spGender">${[["", "Any (mixed or as written)"], ["female", "Female only"], ["male", "Male only"], ["hermaphrodite", "Hermaphrodite only"], ["unisex", "Unisex only (no male or female distinction)"], ["other", "Other only (describe below)"]].map(([v, t]) => `<option value="${v}" ${(sp.gender || "") === v ? "selected" : ""}>${t}</option>`).join("")}</select><input class="spGenderNote" value="${esc(sp.gender_note || "")}" placeholder="optional note, or for Other: what it is (e.g. neuter, no visible sex)" style="flex:1;min-width:220px"></div>
        <label>Combat clothing <small class="hint">(what this species wears in fights: used for the Actions pictures and fight scenes instead of the clothing style above)</small></label><textarea class="short spCombat" rows="2" placeholder="e.g. grey combat fatigues with plated shoulder guards, field harness, rifle sling">${esc(sp.combat || "")}</textarea>
        <label>Words to keep out of this species' pictures <small class="hint">(negative prompt: added to the negative words of their portraits, reference redraws and pictures where only they appear)</small></label><textarea class="short spNeg" rows="2" placeholder="e.g. human ears, tail, fur">${esc(sp.negative || "")}</textarea>
        <div class="actions" style="justify-content:flex-start;margin:2px 0 6px"><button class="spWrite" title="The helper model fills the description, story traits, palette, clothing, combat clothing, negative words and gender from the story prompt and this species' members. Boxes it has nothing for are left as they are. Nothing is saved until you press Save.">✨ Fill everything from the story</button></div>
        <label>Picture maker for this species <small class="hint">(a character can still choose their own)</small></label><select class="spMaker"></select>
        <label>LoRA for this species <small class="hint">(used for the reference picture and every character of this species; ComfyUI only)</small></label>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px;margin:2px 0 6px"><select class="spLora" style="max-width:320px"><option value="">(none)</option></select><input class="spLoraSt" type="number" step="0.05" min="-2" max="2" value="${esc(String(sp.lora_strength ?? 1))}" style="width:80px" title="Strength: 0.6 to 1.0 is usual"><input class="spLoraTrig" value="${esc(sp.lora_trigger || "")}" placeholder="trigger words (filled in from the LoRA)" style="max-width:220px"></div>
          <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px"><button class="spLib" title="Keeps this species, with its reference pictures, in your library, so other stories can add it.">📚 Save to library</button><span class="hint spLibMsg" role="status"></span></div></div></div></div>`;
  };
  modal(`<h2>🧬 Species</h2><p class="hint">Every character has a species (empty means human). Each species can have several <b>reference pictures</b>: a general look, a male and a female one (only the one sex for a species of one sex), and its daily and combat clothing. The <b>first picture drawn</b> for a species becomes its general look by itself. A new character of the species is drawn from tiny versions of the picture of their sex and of the clothing, so they share the look but have their own face. After a character has pictures of their own, those are used instead (a fight picture also gets the combat clothing picture).</p>
    <div id="spList">${list.map(card).join("") || '<p class="hint">No species yet. Give a character a species (character page → Species) or add one below.</p>'}</div>
    <div class="actions" style="justify-content:flex-start"><input id="spNew" placeholder="Add a species, e.g. Elf" style="max-width:220px"><button id="spAdd">Add</button><button id="spFromLib" title="Add a species you kept in the library, with its reference picture">📚 Add from library…</button><span style="flex:1"></span><span class="hint" id="spNote"></span><button class="primary" id="spSave">Save</button></div>
    <div class="actions" style="margin-top:8px"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = () => { closeModal(); renderAll(); };
  // The reference pictures of one card: a tile per picture, each with Upload, "use a character's picture", Draw (pick from 4) and Clear. Only this block is redrawn, so
  // text typed in the other boxes is kept.
  const renderRefs = (el) => {
    const key = el.dataset.key, sp = story.species[key] || {}, box = el.querySelector(".spRefs"), slots = speciesSlots(el.querySelector(".spGender").value);
    const withPic = members(sp.name).filter((c) => Object.keys(c.moods || {}).length);
    box.innerHTML = `<div class="sprefhead"><b>Reference pictures</b> <small class="muted">for the ${esc(sp.name)}</small>
        <span class="spchar"><small class="hint">Characters' pictures:</small> <select class="spPick">${withPic.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("") || "<option value=''>(no pictures yet)</option>"}</select></span></div>
      <div class="spslots">${slots.map(([slot, label, hint]) => { const u = speciesRefUrl(sp, slot);
        return `<figure class="spslot" data-slot="${slot}"><div class="spslotimg">${u ? `<img src="${thumbUrl(u, 300)}" alt="${esc(label)}" loading="lazy">` : '<div class="noart">Empty</div>'}</div>
          <figcaption><b>${esc(label)}</b><div class="hint">${esc(hint)}</div>
            <div class="spsbtns"><label class="btnlike" title="Use a picture file from your computer">⬆ Upload<input type="file" class="spsFile" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>
              <button type="button" class="spsChar" ${withPic.length ? "" : "disabled"} title="Copy the picture of the character chosen above">Use a character</button>
              <button type="button" class="spsDraw" title="Draws 4 pictures from the description above, then you pick one (with the picture maker chosen above, even if not saved yet)">🎨 Draw</button>
              ${u ? '<button type="button" class="spsClear">✕</button>' : ""}</div></figcaption></figure>`; }).join("")}</div>
      <div class="spCands"></div>`;
    const done = (r) => { story = r; renderRefs(el); $("spNote").textContent = ""; };
    box.querySelectorAll(".spslot").forEach((tile) => {
      const slot = tile.dataset.slot, q = (c) => tile.querySelector(c);
      q(".spsFile").onchange = async (e) => {
        const f = e.target.files[0]; if (!f) return;
        try {
          $("spNote").textContent = "Uploading…";
          const r = await fetch(`/api/stories/${story.id}/species/${encodeURIComponent(key)}/image?slot=${slot}`, { method: "POST", headers: { "content-type": f.type }, body: f });
          if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Upload failed.");
          done(await r.json());
        } catch (err) { $("spNote").textContent = err.message; }
      };
      q(".spsChar").onclick = async () => { try { done(await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/reference`, { method: "POST", body: { slug: box.querySelector(".spPick").value, slot } })); } catch (err) { $("spNote").textContent = err.message; } };
      const clr = q(".spsClear"); if (clr) clr.onclick = async () => { try { done(await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/reference?slot=${slot}`, { method: "DELETE" })); } catch (err) { $("spNote").textContent = err.message; } };
      q(".spsDraw").onclick = async (e) => {
        const b = e.target; b.disabled = true; $("spNote").textContent = "Drawing 4 pictures… this can take a few minutes.";
        try {
          const r = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/redraw`, { method: "POST", body: { slot, maker: el.querySelector(".spMaker").value, description: el.querySelector(".spDesc").value, count: 4, lora: el.querySelector(".spLora").value, lora_strength: parseFloat(el.querySelector(".spLoraSt").value) || 1, lora_trigger: el.querySelector(".spLoraTrig").value, palette: el.querySelector(".spPalette").value, clothing: el.querySelector(".spClothing").value, combat: el.querySelector(".spCombat").value, negative: el.querySelector(".spNeg").value, gender: el.querySelector(".spGender").value, gender_note: el.querySelector(".spGenderNote").value } });
          const cands = box.querySelector(".spCands");
          cands.innerHTML = `<div class="hint">Pictures for “${esc(slot === "main" ? "main" : slot)}”. Click a picture to see it full size, then choose the one you want.</div><div class="gallery">${r.candidates.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary spUseThis" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div><button class="spKeep">Keep the current one</button>`;
          $("spNote").textContent = "";
          cands.querySelectorAll(".spUseThis").forEach((im) => im.onclick = async () => { try { done(await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/pick`, { method: "POST", body: { url: im.dataset.url, slot } })); } catch (err) { $("spNote").textContent = err.message; } });
          cands.querySelector(".spKeep").onclick = async () => { await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/pick`, { method: "POST", body: { url: "", slot } }); cands.innerHTML = ""; };
          cands.scrollIntoView({ block: "nearest" });
        } catch (err) { $("spNote").textContent = err.message; }
        b.disabled = false;
      };
    });
  };
  const body = () => ({ species: [...document.querySelectorAll(".spcard")].map((el) => ({ name: (story.species[el.dataset.key] || {}).name || el.dataset.key, description: el.querySelector(".spDesc").value, traits: el.querySelector(".spTraits").value, maker: el.querySelector(".spMaker").value, lora: el.querySelector(".spLora").value, lora_strength: parseFloat(el.querySelector(".spLoraSt").value) || 1, lora_trigger: el.querySelector(".spLoraTrig").value, palette: el.querySelector(".spPalette").value, clothing: el.querySelector(".spClothing").value, combat: el.querySelector(".spCombat").value, negative: el.querySelector(".spNeg").value, gender: el.querySelector(".spGender").value, gender_note: el.querySelector(".spGenderNote").value })) });
  document.querySelectorAll(".spcard").forEach((el) => {
    const key = el.dataset.key; makerSelect(el.querySelector(".spMaker"), choices, (story.species[key] || {}).maker || "");
    bindLoraSelect(el.querySelector(".spLora"), (story.species[key] || {}).lora || "", () => el.querySelector(".spMaker").value, el.querySelector(".spMaker"), el.querySelector(".spLoraTrig"));
    el.querySelector(".spWrite").onclick = async (e) => { e.target.disabled = true; $("spNote").textContent = "Writing…"; try { const r = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/write`, { method: "POST" }); el.querySelector(".spDesc").value = r.description; el.querySelector(".spTraits").value = r.traits; if (r.palette) el.querySelector(".spPalette").value = r.palette; if (r.clothing) el.querySelector(".spClothing").value = r.clothing; if (r.combat) el.querySelector(".spCombat").value = r.combat; if (r.negative) el.querySelector(".spNeg").value = r.negative; if (r.gender) { el.querySelector(".spGender").value = r.gender; el.querySelector(".spGenderNote").value = r.gender_note || ""; renderRefs(el); } $("spNote").textContent = "Drafted. Edit if you like, then Save."; } catch (err) { $("spNote").textContent = err.message; } e.target.disabled = false; };
    el.querySelector(".spGender").onchange = () => renderRefs(el);
    renderRefs(el);
  });
  document.querySelectorAll(".spcard").forEach((el) => {
    const b = el.querySelector(".spLib"); if (!b) return;
    const msg = el.querySelector(".spLibMsg");      // the answer appears next to the button that was pressed, so it is clear which species it was
    b.onclick = async () => {
      msg.textContent = "Saving…"; msg.style.color = "";
      try {
        story = await api(`/stories/${story.id}/species`, { method: "PUT", body: body() });        // what is in the boxes goes to the library, not the last saved version
        const r = await api(`/stories/${story.id}/species/${encodeURIComponent(el.dataset.key)}/to-library`, { method: "POST" });
        msg.textContent = r.updated ? "✔ Updated in the library" : "✔ Added to the library";
      } catch (err) { msg.textContent = err.message; msg.style.color = "var(--danger)"; }
    };
  });
  $("spFromLib").onclick = async () => {
    let lib = []; try { lib = await api("/species-library"); } catch (err) { $("spNote").textContent = err.message; return; }
    const draw = () => modal(`<h2>📚 Species library</h2><p class="hint">Species you kept from your stories. Adding one brings its description, story traits, picture maker, LoRA and reference picture into this story.</p>
      ${lib.map((e) => `<div class="aiBox" data-id="${esc(e.id)}"><div class="bioTop">${e.image ? `<img class="bioimg" src="${thumbUrl(e.image, 200)}" alt="">` : '<div class="noart" style="width:80px;height:100px">No picture</div>'}
        <div style="flex:1"><b>${esc(e.name)}</b>${e.lora ? ` <small class="muted">LoRA ${esc(e.lora)} @ ${esc(String(e.lora_strength ?? 1))}</small>` : ""}<div class="hint">${esc(oneLine(e.description || "", 220))}</div>
        <div class="actions" style="justify-content:flex-start;margin-top:6px"><button class="primary slAdd">Add to this story</button><button class="danger slDel">Remove from library</button></div></div></div></div>`).join("") || '<p class="hint">Nothing here yet. Use “Save to library” on a species card.</p>'}
      <div class="actions"><span class="hint" id="slNote"></span><span style="flex:1"></span><button id="slBack">← Species</button></div>`);
    const bind = () => {
    $("slBack").onclick = () => showSpecies(nav);
    document.querySelectorAll(".slAdd").forEach((b) => b.onclick = async () => {
      const id = b.closest(".aiBox").dataset.id, e = lib.find((x) => x.id === id);
      try {
        try { story = await api(`/stories/${story.id}/species/from-library`, { method: "POST", body: { id } }); }
        catch (err) { if (!/already has a species/.test(err.message)) throw err; if (!confirm(`${err.message} Replace it with the library's version (description, traits, maker, LoRA and reference picture)?`)) return; story = await api(`/stories/${story.id}/species/from-library`, { method: "POST", body: { id, replace: true } }); }
        showSpecies(nav);
      } catch (err) { $("slNote").textContent = err.message; }
    });
    document.querySelectorAll(".slDel").forEach((b) => b.onclick = async () => {
      const id = b.closest(".aiBox").dataset.id, e = lib.find((x) => x.id === id);
      if (!confirm(`Remove “${e.name}” from the library? Stories that already use it keep it.`)) return;
      try { await api(`/species-library/${id}`, { method: "DELETE" }); lib = lib.filter((x) => x.id !== id); draw(); bind(); } catch (err) { $("slNote").textContent = err.message; }
    });
    };
    draw(); bind();
  };
  $("spAdd").onclick = async () => {
    const n = $("spNew").value.trim(); if (!n) return;
    const b = body(); b.species.push({ name: n, description: "", traits: "", palette: "", clothing: "", combat: "", negative: "", gender: "", gender_note: "", maker: "", lora: "", lora_strength: 1, lora_trigger: "" });
    try { story = await api(`/stories/${story.id}/species`, { method: "PUT", body: b }); showSpecies(nav); } catch (err) { $("spNote").textContent = err.message; }
  };
  $("spSave").onclick = async () => { try { story = await api(`/stories/${story.id}/species`, { method: "PUT", body: body() }); $("spNote").textContent = "✔ Saved."; } catch (err) { $("spNote").textContent = err.message; } };
}
