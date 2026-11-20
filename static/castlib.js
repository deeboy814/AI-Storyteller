// Cast library: characters made once, with a picture, that any story can use. Opened from the Home screen (make and edit them)
// and from a story's Cast screen ("From the library": adds a copy to that story, picture included).
async function showCastLibrary(opts = {}) { return showLibrary("Characters", opts); }   // the character cards live in the Library window now

async function showCastLibraryEditor(id, opts = {}) {
  let c = id ? (await api("/cast-library")).find((x) => x.id === id) : { id: null, name: "", appearance: "", face: "", personality: "", bio: "", image: "" };
  if (!c) return showCastLibrary(opts);
  const stories = await api("/stories");
  // The Sheet tab exists only where sheets are supported: in a story, when that story has them on; from the Home screen, when some story does.
  const sheetsOk = story && story.id ? !!(story.statsheets || {}).on : stories.some((x) => x.sheets);
  const sh = c.sheet || { stats: [], notes: "", image: "" };
  const statRow = (x) => `<div class="shrow"><input class="shn" value="${esc(x.name)}" placeholder="Stat, e.g. Level" maxlength="30"><input class="shv" value="${esc(x.value)}" placeholder="Value, e.g. 12 or 40/40" maxlength="60"><button type="button" class="x shdel" title="Remove this stat">×</button></div>`;
  modal(`<h2>${c.id ? "Edit character" : "New character"}</h2>
    ${sheetsOk ? `<div class="tabs"><button class="tab cltab" data-tab="Main">Character</button><button class="tab cltab" data-tab="Sheet" ${c.id ? "" : 'disabled title="Save the character first"'}>📋 Sheet</button></div>` : ""}
    <div id="clMain"><div class="avrow"><div id="clPreview">${c.image ? `<img class="avatar" src="${esc(c.image)}" alt="">` : '<div class="avatar noimg">No picture yet</div>'}</div>
      <div><input type="file" id="clFile" accept="image/png,image/jpeg,image/webp,image/gif" style="padding:4px">${c.image ? videoBar(c.image) : ""}
        <div class="actions" style="justify-content:flex-start;margin-top:8px"><button id="clGen">✨ Draw 4 options from the description</button></div>
        <div class="field"><label>Draw it in the style of</label><select id="clStory"><option value="">No particular story (the default picture maker)</option>${stories.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("")}</select></div>
        <div id="clNote" class="hint">Uploads are saved at once. Drawing uses that story's picture maker and art style; pick another story later for another version.</div></div></div>
    <label>Name</label><input id="cl_name" value="${esc(c.name)}" placeholder="Name">
    <label>How they look <small class="hint">(age, hair, eyes, build, clothing, distinctive features: this is what the picture is drawn from)</small></label><textarea class="short" id="cl_appearance" rows="3">${esc(c.appearance || "")}</textarea>
    <label>Face in words <small class="hint">(optional: kept identical in every picture)</small></label><textarea class="short" id="cl_face" rows="2">${esc(c.face || "")}</textarea>
    <label>Personality</label><input id="cl_personality" value="${esc(c.personality || "")}">
    <label>Bio</label><textarea class="short" id="cl_bio" rows="3">${esc(c.bio || "")}</textarea>
    </div>
    ${sheetsOk && c.id ? `<div id="clSheet" class="hidden">
      <p class="hint">${esc(c.name)}'s character sheet, kept in the library. It is copied into a story together with the character, but only into a story that has character sheets switched on.</p>
      <div id="shRows">${(sh.stats || []).map(statRow).join("")}</div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button type="button" id="shAdd">＋ Add a stat</button><select id="shPreset"><option value="">Add a ready-made set…</option><option value="simple">Level, HP</option><option value="rpg">Level, HP, Strength, Agility, Intellect, Charisma</option><option value="dnd">Level, HP, AC, STR, DEX, CON, INT, WIS, CHA</option></select></div>
      <label>Notes <small class="hint">(skills, gear, abilities, anything else)</small></label><textarea class="short" id="shNotes" rows="4" maxlength="1200">${esc(sh.notes || "")}</textarea>
      <label>Your own picture <small class="hint">(a stat card or anything you like; for you only)</small></label>
      <div class="shimg">${sh.image ? `<a href="${esc(sh.image)}" target="_blank"><img src="${esc(sh.image)}" alt="" style="max-width:100%;max-height:360px;border-radius:8px"></a>` : '<span class="hint">No picture yet.</span>'}</div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><label class="btnlike">${sh.image ? "Replace picture…" : "Upload a picture…"}<input type="file" id="shFile" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${sh.image ? '<button type="button" id="shImgDel">Remove picture</button>' : ""}</div>
      <div class="actions"><span class="hint" id="shStatus"></span><span style="flex:1"></span><button type="button" class="primary" id="shSave">Save sheet</button></div></div>` : ""}
    <div class="actions sticky">${c.id ? '<button class="danger" id="clDel">Delete</button>' : ""}<span style="flex:1"></span><button id="clBack">← Library</button><button class="primary" id="clSave">Save</button></div>`);
  const body = () => ({ name: $("cl_name").value, appearance: $("cl_appearance").value, face: $("cl_face").value, personality: $("cl_personality").value, bio: $("cl_bio").value,
    species: c.species || "", maker: c.maker || "", face_lora: c.face_lora || "", face_trigger: c.face_trigger || "" });
  const ensure = async () => { if (!c.id) c = await api("/cast-library", { method: "POST", body: body() }); return c.id; };
  const prev = (url) => { $("clPreview").innerHTML = `<img class="avatar" src="${esc(url)}" alt="">`; };
  $("clBack").onclick = () => showCastLibrary(opts);
  $("clSave").onclick = async () => { if (c.id) await api("/cast-library/" + c.id, { method: "PUT", body: body() }); else await api("/cast-library", { method: "POST", body: body() }); showCastLibrary(opts); };
  if ($("clDel")) $("clDel").onclick = async () => { if (confirm(`Delete “${c.name}” from the library? Stories that already have them keep their own copy.`)) { await api("/cast-library/" + c.id, { method: "DELETE" }); showCastLibrary(opts); } };
  $("clFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return; const lid = await ensure();
    const r = await fetch(`/api/cast-library/${lid}/image`, { method: "POST", headers: { "content-type": f.type }, body: f });
    if (!r.ok) { $("clNote").textContent = "Upload failed."; return; }
    c = { ...c, ...(await r.json()) }; prev(c.image);
  };
  const showOpts = (lid, cands) => {
    $("clNote").innerHTML = `<b>Pick a picture.</b> Click one to see it full size. Nothing is saved until you choose one.
      <div class="gallery">${cands.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary clUse" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button id="clMore">Draw 4 more</button></div>`;
    document.querySelectorAll(".clUse").forEach((b) => b.onclick = async () => { c = { ...c, ...(await api(`/cast-library/${lid}/use`, { method: "POST", body: { url: b.dataset.url } })) }; prev(c.image); $("clNote").textContent = "Saved. Press Save to keep the description too."; });
    $("clMore").onclick = gen;
  };
  const gen = async () => {
    if (!$("cl_appearance").value.trim()) { $("clNote").textContent = "Describe how they look first."; return; }
    $("clGen").disabled = true; $("clNote").textContent = "Drawing 4 options… this can take a few minutes on a local picture maker.";
    try {
      const lid = await ensure();
      const r = await api(`/cast-library/${lid}/candidates`, { method: "POST", body: { ...body(), story_id: $("clStory").value || null, count: 4 } });
      showOpts(lid, r.candidates);
    } catch (err) { $("clNote").textContent = err.message; }
    $("clGen").disabled = false;
  };
  $("clGen").onclick = gen;
  c._opts = opts;
  bindLibSheet(() => c, (v) => { c = v; });
}

// (the library editor's Sheet tab: wired after the editor is drawn)
function bindLibSheet(getC, setC) {
  if (!$("clSheet")) return;
  const tabs = (n) => { document.querySelectorAll(".cltab").forEach((b) => b.classList.toggle("on", b.dataset.tab === n)); $("clMain").classList.toggle("hidden", n !== "Main"); $("clSheet").classList.toggle("hidden", n !== "Sheet"); };
  document.querySelectorAll(".cltab").forEach((b) => b.onclick = () => !b.disabled && tabs(b.dataset.tab));
  tabs("Main");
  const st = (t) => { $("shStatus").textContent = t; };
  const rows = () => [...document.querySelectorAll("#shRows .shrow")].map((r) => ({ name: r.querySelector(".shn").value.trim(), value: r.querySelector(".shv").value.trim() })).filter((x) => x.name);
  const row = (x) => `<div class="shrow"><input class="shn" value="${esc(x.name)}" placeholder="Stat, e.g. Level" maxlength="30"><input class="shv" value="${esc(x.value)}" placeholder="Value, e.g. 12 or 40/40" maxlength="60"><button type="button" class="x shdel" title="Remove this stat">×</button></div>`;
  const save = async () => { setC({ ...getC(), ...(await api(`/cast-library/${getC().id}/sheet`, { method: "PUT", body: { stats: rows(), notes: $("shNotes").value } })) }); };
  $("shAdd").onclick = () => { $("shRows").insertAdjacentHTML("beforeend", row({ name: "", value: "" })); $("shRows").lastElementChild.querySelector(".shn").focus(); };
  $("shRows").addEventListener("click", (e) => { if (e.target.classList.contains("shdel")) e.target.closest(".shrow").remove(); });
  $("shPreset").onchange = (e) => {
    const sets = { simple: ["Level", "HP"], rpg: ["Level", "HP", "Strength", "Agility", "Intellect", "Charisma"], dnd: ["Level", "HP", "AC", "STR", "DEX", "CON", "INT", "WIS", "CHA"] };
    const have = new Set(rows().map((x) => x.name.toLowerCase()));
    (sets[e.target.value] || []).filter((n) => !have.has(n.toLowerCase())).forEach((n) => $("shRows").insertAdjacentHTML("beforeend", row({ name: n, value: "" })));
    e.target.value = "";
  };
  $("shSave").onclick = async () => { try { await save(); st("Saved ✔"); } catch (e) { st(e.message); } };
  const again = () => showCastLibraryEditor(getC().id, getC()._opts || {}).then(() => { const b = document.querySelector('.cltab[data-tab="Sheet"]'); if (b) b.click(); });
  $("shFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try {
      await save();
      const r = await fetch(`/api/cast-library/${getC().id}/sheet/image`, { method: "POST", headers: { "content-type": f.type }, body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      await again();
    } catch (err) { st(err.message); }
  };
  if ($("shImgDel")) $("shImgDel").onclick = async () => { try { await save(); await api(`/cast-library/${getC().id}/sheet/image`, { method: "DELETE" }); await again(); } catch (e) { st(e.message); } };
}
