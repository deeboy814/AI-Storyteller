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
        `<button id="hCast">Cast &amp; pictures…</button><button id="aNew">Draw art for new characters…</button><button id="hGal">Gallery</button><button id="hSpecies">Species &amp; makers…</button>`)}
      ${card("🧑", "Your avatar", story.avatar_image ? "Has a picture" : "No picture yet", `<button id="hAvatar">Avatar picture…</button>`)}
      ${card("📍", "Places", `${places.length} place${places.length === 1 ? "" : "s"} · ${placesArt} with pictures`, `<button id="hPlaces">Manage places…</button>`)}
      ${card("🌄", "Backgrounds", `Showing: ${esc(bd.name || (bd.url ? "a picture you chose" : "nothing yet"))}<br>Changes automatically: ${onoff(a.auto_backdrop)}${a.dynamic_backdrop ? " (also for dramatic moments)" : ""} · screen display: ${onoff(config && config.ui_backdrop)}`, `<button id="hBg">Backgrounds…</button>`)}
      ${card("📖", "Cover", story.cover ? "Has a cover on the Home card" : "No cover yet", `<button id="hCover">Cover art…</button>`)}
      ${card("🖼", "Intro panels", `${intro} picture${intro === 1 ? "" : "s"} shown at the start of the story`, `<button id="hIntro">Intro panels…</button>`)}
      ${card("🎬", "Scene pictures", `Auto-illustrate: ${onoff(a.auto)} · a picture most turns: ${onoff(a.scene_often)}${(a.scene_gap || 1) > 1 ? " · one every " + a.scene_gap + " turns" : ""} · shown ${({ panel: "in the side panel", banner_top: "as a banner above the narration", banner_bottom: "as a banner below the narration" })[a.scene_where] || "under the reply"}`, `<button id="hIllus" ${busy ? "disabled" : ""}>Illustrate the latest reply</button><button id="hStyle1">Options…</button>`)}
    </div>
    <div id="hubStyle" class="hidden">${artTabHtml().replace('id="tabArt" class="hidden"', 'id="tabArt"')}
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
  $("mClose").onclick = () => { closeModal(); renderAll(); applyTheme(config); refreshArtSuggestions(); };

  // ----- Overview: open the existing tools; most return here when closed
  const back = () => showArtHub("Overview");
  const via = (f) => () => { artBack = back; f(); };    // the screen that opens gets a "← Art menu" button (see modal() in app.js)
  $("hCast").onclick = via(() => showCast("Characters"));
  $("hGal").onclick = via(() => showCast("Gallery"));
  $("hSpecies").onclick = via(() => showSpecies({ back }));
  $("hIntro").onclick = via(() => showCast("Intro"));
  $("aNew").onclick = via(() => showArtDialog({ onClose: () => { renderAll(); back(); } }));
  $("hAvatar").onclick = via(() => showPlayer());
  $("hPlaces").onclick = via(() => showPlaces({ back }));
  $("hBg").onclick = () => show("Backgrounds");
  $("hCover").onclick = via(() => showCardEditor({ id: story.id, title: story.title, cover: story.cover || "", description: story.description || "" }, { back: async () => { try { story = await api(`/stories/${story.id}`); } catch (e) { /* keep */ } renderAll(); back(); } }));
  $("hIllus").onclick = () => { closeModal(); illustrate(); };
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
// separate backgrounds maker. This used to be Settings → Pictures. Without an open story it only changes the app-wide choices (Home screen).
async function mountMakers(box) {
  if (box.dataset.ready) return;
  box.dataset.ready = "1";
  box.innerHTML = '<div class="hint">Loading…</div>';
  config = await api("/config");
  box.innerHTML = `<h3>Which AI draws pictures${story ? ` in “${esc(story.title)}”` : ""}</h3><div id="mkPick"></div>
    ${imageSettingsHtml(config)}
    <div class="actions sticky"><span class="hint" id="mkNote"></span><span style="flex:1"></span><button class="primary" id="mkSave">Save</button></div>`;
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

// Species: what is true of a whole species (written into every member's picture prompt), the picture maker it uses by default, and its reference picture. The first
// picture drawn of a new species becomes its reference by itself; later characters of the species keep its look but are drawn as their own person.
async function showSpecies(nav = {}) {
  const list = Object.entries(story.species || {}), cast = story.cast || [], choices = await makerChoices();
  const members = (name) => cast.filter((c) => ((c.species || "human").trim().toLowerCase() || "human") === name.toLowerCase());
  const card = ([key, sp]) => {
    const m = members(sp.name), withPic = m.filter((c) => Object.keys(c.moods || {}).length);
    return `<div class="aiBox spcard" data-key="${esc(key)}"><b>${esc(sp.name)}</b> <small class="muted">${m.length} character${m.length === 1 ? "" : "s"}: ${esc(m.map((c) => c.name).join(", ") || "none yet")}</small>
      <div class="bioTop spTop">${sp.reference_url ? `<img class="bioimg" src="${thumbUrl(sp.reference_url, 360)}" alt="reference">` : '<div class="noart" style="width:110px;height:140px">No reference yet</div>'}
      <div style="flex:1"><label>What is true of the whole species <small class="hint">(skin or covering, eyes, crests, build, clothing: added to every member's picture prompt)</small></label><textarea class="short spDesc" rows="3">${esc(sp.description || "")}</textarea>
        <label>Story traits <small class="hint">(abilities, weaknesses, senses, customs: given to the narrator so it stays consistent)</small></label><textarea class="short spTraits" rows="3">${esc(sp.traits || "")}</textarea>
        <div class="actions" style="justify-content:flex-start;margin:2px 0 6px"><button class="spWrite" title="The helper model drafts both boxes from the story prompt and this species' members. Nothing is saved until you press Save.">✨ Write both from the story</button></div>
        <label>Picture maker for this species <small class="hint">(a character can still choose their own)</small></label><select class="spMaker"></select>
        <label>LoRA for this species <small class="hint">(used for the reference picture and every character of this species; ComfyUI only)</small></label>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px;margin:2px 0 6px"><select class="spLora" style="max-width:320px"><option value="">(none)</option></select><input class="spLoraSt" type="number" step="0.05" min="-2" max="2" value="${esc(String(sp.lora_strength ?? 1))}" style="width:80px" title="Strength: 0.6 to 1.0 is usual"><input class="spLoraTrig" value="${esc(sp.lora_trigger || "")}" placeholder="trigger word (if it has one)" style="max-width:220px"></div>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px"><select class="spPick">${withPic.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("") || "<option value=''>(no pictures yet)</option>"}</select>
          <button class="spUse" ${withPic.length ? "" : "disabled"}>Use their picture as the reference</button></div>
          <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px"><button class="spRedraw" title="Draws 4 new reference pictures of a typical member from the description above, then you pick one, with the picture maker chosen above (even if not saved yet).">🎨 Redraw the reference (pick from 4)</button>${sp.reference_url ? '<button class="spClear">Clear the reference</button>' : ""}</div><div class="spCands"></div></div></div></div>`;
  };
  modal(`<h2>🧬 Species</h2><p class="hint">Every character has a species (empty means human). The <b>first picture drawn</b> for a species becomes its reference: the next character of that species is drawn from a tiny version of it, so they share its look but have their own face. After a character has pictures of their own, those are used instead.</p>
    <div id="spList">${list.map(card).join("") || '<p class="hint">No species yet. Give a character a species (character page → Species) or add one below.</p>'}</div>
    <div class="actions" style="justify-content:flex-start"><input id="spNew" placeholder="Add a species, e.g. Elf" style="max-width:220px"><button id="spAdd">Add</button><span style="flex:1"></span><span class="hint" id="spNote"></span><button class="primary" id="spSave">Save</button></div>
    <div class="actions" style="margin-top:8px"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = () => { closeModal(); renderAll(); };
  const body = () => ({ species: [...document.querySelectorAll(".spcard")].map((el) => ({ name: (story.species[el.dataset.key] || {}).name || el.dataset.key, description: el.querySelector(".spDesc").value, traits: el.querySelector(".spTraits").value, maker: el.querySelector(".spMaker").value, lora: el.querySelector(".spLora").value, lora_strength: parseFloat(el.querySelector(".spLoraSt").value) || 1, lora_trigger: el.querySelector(".spLoraTrig").value })) });
  document.querySelectorAll(".spcard").forEach((el) => {
    const key = el.dataset.key; makerSelect(el.querySelector(".spMaker"), choices, (story.species[key] || {}).maker || "");
    { const cur = (story.species[key] || {}).lora || "", sel = el.querySelector(".spLora"), fill = (list) => { const names = [...new Set([...(list || []), ...(cur ? [cur] : [])])]; sel.innerHTML = '<option value="">(none)</option>' + names.map((n) => `<option value="${esc(n)}">${esc(n)}${list && list.includes(n) ? "" : " (not found in ComfyUI)"}</option>`).join(""); sel.value = cur; };
      fill(null); api("/model-options").then((o) => { const p = (o.images || []).find((x) => (x.loras || []).length); fill(p ? p.loras : []); }).catch(() => {}); }
    el.querySelector(".spUse").onclick = async () => { try { story = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/reference`, { method: "POST", body: { slug: el.querySelector(".spPick").value } }); showSpecies(nav); } catch (err) { $("spNote").textContent = err.message; } };
    el.querySelector(".spWrite").onclick = async (e) => { e.target.disabled = true; $("spNote").textContent = "Writing…"; try { const r = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/write`, { method: "POST" }); el.querySelector(".spDesc").value = r.description; el.querySelector(".spTraits").value = r.traits; $("spNote").textContent = "Drafted. Edit if you like, then Save."; } catch (err) { $("spNote").textContent = err.message; } e.target.disabled = false; };
    el.querySelector(".spRedraw").onclick = async (e) => {
      const b = e.target; b.disabled = true; $("spNote").textContent = "Drawing 4 pictures… this can take a few minutes.";
      try {
        const r = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/redraw`, { method: "POST", body: { maker: el.querySelector(".spMaker").value, description: el.querySelector(".spDesc").value, count: 4, lora: el.querySelector(".spLora").value, lora_strength: parseFloat(el.querySelector(".spLoraSt").value) || 1, lora_trigger: el.querySelector(".spLoraTrig").value } });
        const box = el.querySelector(".spCands");
        box.innerHTML = `<div class="hint">Click a picture to see it full size, then choose the one you want.</div><div class="gallery">${r.candidates.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary spUseThis" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div><button class="spKeep">Keep the current one</button>`;
        $("spNote").textContent = "";
        box.querySelectorAll(".spUseThis").forEach((im) => im.onclick = async () => { try { story = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/pick`, { method: "POST", body: { url: im.dataset.url } }); showSpecies(nav); } catch (err) { $("spNote").textContent = err.message; } });
        box.querySelector(".spKeep").onclick = async () => { await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/pick`, { method: "POST", body: { url: "" } }); box.innerHTML = ""; };
        box.scrollIntoView({ block: "nearest" });
      } catch (err) { $("spNote").textContent = err.message; }
      b.disabled = false;
    };
    const clr = el.querySelector(".spClear"); if (clr) clr.onclick = async () => { story = await api(`/stories/${story.id}/species/${encodeURIComponent(key)}/reference`, { method: "DELETE" }); showSpecies(nav); };
  });
  $("spAdd").onclick = async () => {
    const n = $("spNew").value.trim(); if (!n) return;
    const b = body(); b.species.push({ name: n, description: "", traits: "", maker: "", lora: "", lora_strength: 1, lora_trigger: "" });
    try { story = await api(`/stories/${story.id}/species`, { method: "PUT", body: b }); showSpecies(nav); } catch (err) { $("spNote").textContent = err.message; }
  };
  $("spSave").onclick = async () => { try { story = await api(`/stories/${story.id}/species`, { method: "PUT", body: body() }); $("spNote").textContent = "✔ Saved."; } catch (err) { $("spNote").textContent = err.message; } };
}
