// Cast library: characters made once, with a picture, that any story can use. Opened from the Home screen (make and edit them)
// and from a story's Cast screen ("From the library": adds a copy to that story, picture included).
async function showCastLibrary(opts = {}) {
  const list = await api("/cast-library"), inStory = !!(story && story.id && opts.pick !== false && story.cast);
  const pic = (a) => a.image ? `<img src="${thumbUrl(a.image.split("?")[0], 360)}" alt="">` : `<span>${esc((a.name || "?").charAt(0).toUpperCase())}</span>`;
  modal(`<h2>Character library</h2>
    <p class="hint">Make characters once and use them in any story. A story gets its own copy (their look, face and picture), so changing it in a story never changes the library. The picture becomes their face reference there.</p>
    <div class="cardgrid">${list.map((a) => `<div class="charcard avcard" data-id="${esc(a.id)}"><div class="avthumb">${pic(a)}</div>
        <div class="meta"><b>${esc(a.name)}</b><small>${esc(oneLine(a.appearance || "", 70)) || "no description yet"}</small>
        <div class="avbtns"><button data-edit="${esc(a.id)}">Edit</button>${inStory ? `<button data-use="${esc(a.id)}" class="primary">Add to story</button>` : ""}</div></div></div>`).join("")}
      <button class="charcard addcard" id="clNew"><div class="noart">＋ New character</div></button></div>
    <div class="actions"><button id="mClose">${inStory ? "← Cast" : "Close"}</button></div>`);
  $("mClose").onclick = () => { if (inStory && typeof showCast === "function") showCast("Characters"); else closeModal(); };
  $("clNew").onclick = () => showCastLibraryEditor(null, opts);
  document.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => showCastLibraryEditor(b.dataset.edit, opts));
  document.querySelectorAll("[data-use]").forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = "Adding…";
    try { story = await api(`/stories/${story.id}/cast/from-library`, { method: "POST", body: { id: b.dataset.use } }); if (typeof renderAll === "function") renderAll(); showCast("Characters"); }
    catch (err) { alert(err.message); showCastLibrary(opts); }
  });
}

async function showCastLibraryEditor(id, opts = {}) {
  let c = id ? (await api("/cast-library")).find((x) => x.id === id) : { id: null, name: "", appearance: "", face: "", personality: "", bio: "", image: "" };
  if (!c) return showCastLibrary(opts);
  const stories = await api("/stories");
  modal(`<h2>${c.id ? "Edit character" : "New character"}</h2>
    <div class="avrow"><div id="clPreview">${c.image ? `<img class="avatar" src="${esc(c.image)}" alt="">` : '<div class="avatar noimg">No picture yet</div>'}</div>
      <div><input type="file" id="clFile" accept="image/png,image/jpeg,image/webp,image/gif" style="padding:4px">
        <div class="actions" style="justify-content:flex-start;margin-top:8px"><button id="clGen">✨ Draw 4 options from the description</button></div>
        <div class="field"><label>Draw it in the style of</label><select id="clStory"><option value="">No particular story (the default picture maker)</option>${stories.map((s) => `<option value="${esc(s.id)}">${esc(s.title)}</option>`).join("")}</select></div>
        <div id="clNote" class="hint">Uploads are saved at once. Drawing uses that story's picture maker and art style; pick another story later for another version.</div></div></div>
    <label>Name</label><input id="cl_name" value="${esc(c.name)}" placeholder="Name">
    <label>How they look <small class="hint">(age, hair, eyes, build, clothing, distinctive features: this is what the picture is drawn from)</small></label><textarea class="short" id="cl_appearance" rows="3">${esc(c.appearance || "")}</textarea>
    <label>Face in words <small class="hint">(optional: kept identical in every picture)</small></label><textarea class="short" id="cl_face" rows="2">${esc(c.face || "")}</textarea>
    <label>Personality</label><input id="cl_personality" value="${esc(c.personality || "")}">
    <label>Bio</label><textarea class="short" id="cl_bio" rows="3">${esc(c.bio || "")}</textarea>
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
}
