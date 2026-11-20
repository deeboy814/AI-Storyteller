// The Library: avatars, characters and species you keep once and use in any story, in one window (Home screen → Library, and wherever a story offers "From the library").
// Each tab is a grid of cards; selecting a card opens it for editing. Inside a story a card also has a button that adds it to that story.
let libTab = "Characters";
async function showLibrary(tab, opts = {}) {
  if (tab) libTab = tab;
  const inStory = !!(story && story.id);
  const castPick = inStory && opts.pick !== false && !!story.cast;
  let avs = [], chars = [], sps = [], allStories = [];
  try { [avs, chars, sps, allStories] = await Promise.all([api("/avatars"), api("/cast-library"), api("/species-library"), inStory ? Promise.resolve([]) : api("/stories")]); } catch (e) { alert(e.message); return; }
  const sheetsOk = inStory ? !!(story.statsheets || {}).on : allStories.some((x) => x.sheets);      // the sheet badge (like the Sheet tab) only where sheets are supported
  const thumb = (url, name) => url ? `<img loading="lazy" src="${esc(thumbUrl(url.split("?")[0], 360))}" alt="">` : `<span>${esc((name || "?").charAt(0).toUpperCase())}</span>`;
  const card = (kind, id, img, name, sub, extra, btn) => `<div class="charcard avcard libcard" data-kind="${kind}" data-id="${esc(id)}" tabindex="0" title="Open to edit"><div class="avthumb">${thumb(img, name)}</div>
    <div class="meta"><b>${esc(name)}</b><small>${esc(sub)}</small>${extra || ""}${btn ? `<div class="avbtns">${btn}</div>` : ""}</div></div>`;
  const grids = {
    Avatars: avs.map((a) => card("av", a.id, a.image, a.name, oneLine([a.sheet.gender, a.sheet.age, a.sheet.appearance].filter(Boolean).join(", "), 70) || "no details yet",
      story && story.avatar_id === a.id ? '<small class="evn">✓ used in this story</small>' : "", inStory ? `<button data-use="${esc(a.id)}">Use here</button>` : "")),
    Characters: chars.map((c) => card("ch", c.id, c.image, c.name, oneLine(c.appearance || "", 70) || "no description yet", (c.species ? `<small class="evn">${esc(c.species)}</small>` : "") + (sheetsOk && c.sheet && ((c.sheet.stats || []).length || (c.sheet.notes || "").trim()) ? '<small class="evn" title="This character has a character sheet">📋 sheet</small>' : ""),
      castPick ? `<button class="primary" data-use="${esc(c.id)}">Add to story</button>` : "")),
    Species: sps.map((x) => card("sp", x.id, x.image, x.name, oneLine(x.description || "", 70) || "no description yet", x.lora ? `<small class="evn">LoRA @ ${esc(String(x.lora_strength ?? 1))}</small>` : "",
      inStory ? `<button class="primary" data-use="${esc(x.id)}">Add to story</button>` : "")),
  };
  const newLabel = { Avatars: "＋ New avatar", Characters: "＋ New character", Species: "＋ New species" };
  const hint = {
    Avatars: "Player avatars to reuse. Each story uses exactly one and gets its own copy, so editing it in a story doesn't change the library unless you choose Save to library in Player → Avatar.",
    Characters: "Characters made once, with a picture. A story gets its own copy (look, face and picture), so changing it in a story never changes the library. The picture becomes their face reference there.",
    Species: "Species kept from your stories: description, story traits, picture maker, LoRA and reference picture. Use “Save to library” on a species card in a story's Species tab, or make one here. A story gets its own copy.",
  };
  const counts = { Avatars: avs.length, Characters: chars.length, Species: sps.length };
  modal(`<h2>📚 Library</h2>
    <div class="tabs">${Object.keys(grids).map((t) => `<button class="tab libtab ${t === libTab ? "on" : ""}" data-tab="${t}">${t} (${counts[t]})</button>`).join("")}</div>
    <p class="hint">${hint[libTab]}</p>
    <div class="cardgrid">${grids[libTab].join("")}<button class="charcard addcard" id="libNew"><div class="noart">${newLabel[libTab]}</div></button></div>
    <div class="actions"><button id="mClose">${castPick && libTab === "Characters" && typeof showCast === "function" ? "← Cast" : "Close"}</button></div>`);
  $("mClose").onclick = () => { if (castPick && libTab === "Characters" && typeof showCast === "function") showCast("Characters"); else closeModal(); };
  document.querySelectorAll(".libtab").forEach((b) => b.onclick = () => showLibrary(b.dataset.tab, opts));
  const edit = (kind, id) => (kind === "av" ? showAvatarEditor(id) : kind === "ch" ? showCastLibraryEditor(id, opts) : showSpeciesLibraryEditor(id, opts));
  $("libNew").onclick = () => edit({ Avatars: "av", Characters: "ch", Species: "sp" }[libTab], null);
  document.querySelectorAll(".libcard").forEach((el) => {
    const open = (e) => { if (e.target.closest("button")) return; edit(el.dataset.kind, el.dataset.id); };
    el.onclick = open; el.onkeydown = (e) => { if (e.key === "Enter") open(e); };
  });
  document.querySelectorAll("[data-use]").forEach((b) => b.onclick = async (e) => {
    e.stopPropagation(); const id = b.dataset.use, kind = b.closest(".libcard").dataset.kind;
    b.disabled = true; const old = b.textContent; b.textContent = "Adding…";
    try {
      if (kind === "av") { await useLibraryAvatar(id); closeModal(); return; }
      if (kind === "ch") { story = await api(`/stories/${story.id}/cast/from-library`, { method: "POST", body: { id } }); if (typeof renderAll === "function") renderAll(); showCast("Characters"); return; }
      try { story = await api(`/stories/${story.id}/species/from-library`, { method: "POST", body: { id } }); }
      catch (err) { if (!/already has a species/.test(err.message) || !confirm(`${err.message} Replace it with the library's version (description, traits, maker, LoRA and reference picture)?`)) throw err; story = await api(`/stories/${story.id}/species/from-library`, { method: "POST", body: { id, replace: true } }); }
      b.textContent = "✔ Added";
    } catch (err) { b.disabled = false; b.textContent = old; if (err.message && !/already has a species/.test(err.message)) alert(err.message); }
  });
}

// One species of the library, opened for editing.
async function showSpeciesLibraryEditor(id, opts = {}) {
  let sp = id ? (await api("/species-library")).find((x) => x.id === id) : { id: null, name: "", description: "", traits: "", maker: "", lora: "", lora_strength: 1, lora_trigger: "", image: "" };
  if (!sp) return showLibrary("Species", opts);
  const choices = typeof makerChoices === "function" ? await makerChoices() : [["", "Same as the story", ""]];
  modal(`<h2>${sp.id ? "Edit species" : "New species"}</h2>
    <div class="avrow"><div id="spvPreview">${sp.image ? `<img class="avatar" src="${esc(sp.image)}" alt="">` : '<div class="avatar noimg">No reference picture yet</div>'}</div>
      <div><input type="file" id="spvFile" accept="image/png,image/jpeg,image/webp,image/gif" style="padding:4px">
        <div id="spvNote" class="hint">Uploads are saved at once. The reference picture is what new members of this species are drawn from. You can also keep a drawn one: use “Save to library” on a species card in a story.</div></div></div>
    <label>Name</label><input id="spv_name" value="${esc(sp.name)}" placeholder="e.g. Elf">
    <label>What is true of the whole species <small class="hint">(skin or covering, eyes, crests, build, clothing: added to every member's picture prompt)</small></label><textarea class="short" id="spv_description" rows="3">${esc(sp.description || "")}</textarea>
    <label>Story traits <small class="hint">(abilities, weaknesses, senses, customs: given to the narrator)</small></label><textarea class="short" id="spv_traits" rows="3">${esc(sp.traits || "")}</textarea>
    <label>Colour palette <small class="hint">(the colours this species is drawn in: skin, hair, eyes, clothing)</small></label><textarea class="short" id="spv_palette" rows="2">${esc(sp.palette || "")}</textarea>
    <label>Clothing style <small class="hint">(what this species wears: cut, materials, gear)</small></label><textarea class="short" id="spv_clothing" rows="2">${esc(sp.clothing || "")}</textarea>
    <label>Gender of this species <small class="hint">(every member is drawn this way)</small></label>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px"><select id="spv_gender">${[["", "Any (mixed or as written)"], ["female", "Female only"], ["male", "Male only"], ["hermaphrodite", "Hermaphrodite only"], ["unisex", "Unisex only (no male or female distinction)"], ["other", "Other only (describe below)"]].map(([v, t]) => `<option value="${v}" ${(sp.gender || "") === v ? "selected" : ""}>${t}</option>`).join("")}</select><input id="spv_gnote" value="${esc(sp.gender_note || "")}" placeholder="optional note, or for Other: what it is" style="flex:1;min-width:220px"></div>
    <label>Combat clothing <small class="hint">(what this species wears in fights: used for the Actions pictures and fight scenes)</small></label><textarea class="short" id="spv_combat" rows="2">${esc(sp.combat || "")}</textarea>
    <label>Words to keep out of this species' pictures <small class="hint">(negative prompt)</small></label><textarea class="short" id="spv_negative" rows="2">${esc(sp.negative || "")}</textarea>
    <label>Picture maker for this species</label><select id="spv_maker"></select>
    <label>LoRA for this species <small class="hint">(ComfyUI only)</small></label>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;gap:6px"><select id="spv_lora" style="max-width:320px"><option value="">(none)</option></select><input id="spv_str" type="number" step="0.05" min="-2" max="2" value="${esc(String(sp.lora_strength ?? 1))}" style="width:80px" title="Strength: 0.6 to 1.0 is usual"><input id="spv_trig" value="${esc(sp.lora_trigger || "")}" placeholder="trigger word (if it has one)" style="max-width:220px"></div>
    <div class="actions sticky">${sp.id ? '<button class="danger" id="spvDel">Delete species</button>' : ""}<span class="hint" id="spvMsg"></span><span style="flex:1"></span><button id="spvBack">← Library</button><button class="primary" id="spvSave">Save</button></div>`);
  makerSelect($("spv_maker"), choices, sp.maker || "");
  bindLoraSelect($("spv_lora"), sp.lora || "", () => $("spv_maker").value, $("spv_maker"));
  const body = () => ({ name: $("spv_name").value, description: $("spv_description").value, traits: $("spv_traits").value, maker: $("spv_maker").value, lora: $("spv_lora").value, lora_strength: +$("spv_str").value || 1, lora_trigger: $("spv_trig").value, palette: $("spv_palette").value, clothing: $("spv_clothing").value, combat: $("spv_combat").value, gender: $("spv_gender").value, gender_note: $("spv_gnote").value, negative: $("spv_negative").value });
  const ensure = async () => { if (!sp.id) { if (!$("spv_name").value.trim()) throw new Error("Give the species a name first."); sp = await api("/species-library", { method: "POST", body: body() }); } return sp.id; };
  $("spvBack").onclick = () => showLibrary("Species", opts);
  $("spvSave").onclick = async () => { try { if (sp.id) await api("/species-library/" + sp.id, { method: "PUT", body: body() }); else await api("/species-library", { method: "POST", body: body() }); showLibrary("Species", opts); } catch (e) { $("spvMsg").textContent = e.message; } };
  if ($("spvDel")) $("spvDel").onclick = async () => { if (confirm(`Delete “${sp.name}” from the library? Stories that already have it keep their own copy.`)) { await api("/species-library/" + sp.id, { method: "DELETE" }); showLibrary("Species", opts); } };
  $("spvFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      const lid = await ensure();
      const r = await fetch(`/api/species-library/${lid}/image`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || "Upload failed.");
      sp = await r.json(); $("spvPreview").innerHTML = `<img class="avatar" src="${esc(sp.image)}" alt="">`; $("spvNote").textContent = "Saved. Press Save to keep the other details too.";
    } catch (err) { $("spvNote").textContent = err.message; }
  };
}
