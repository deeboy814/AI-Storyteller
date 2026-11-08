// Avatar library (optional, Settings → Libraries): several player avatars you can reuse across stories; each story uses one.
const AV_FIELDS = [["name", "Name"], ["gender", "Gender"], ["age", "Age"], ["appearance", "Appearance", 3], ["personality", "Personality", 3], ["background", "Background", 3], ["abilities", "Abilities / stats / starting inventory", 3]];

async function showAvatarLibrary() {
  const list = await api("/avatars");
  const pic = (a) => a.image ? `<img src="${esc(a.image)}" alt="">` : `<span>${esc((a.name || "?").charAt(0).toUpperCase())}</span>`;
  modal(`<h2>Avatar library</h2>
    <p class="hint">Create player avatars once and reuse them in any story. Each story uses exactly one; it gets its own copy, so editing it in a story doesn't change the library unless you choose <i>Save to library</i> in Player → Avatar.</p>
    <div class="cardgrid">${list.map((a) => `<div class="charcard avcard" data-id="${esc(a.id)}"><div class="avthumb">${pic(a)}</div>
        <div class="meta"><b>${esc(a.name)}</b><small>${esc(oneLine([a.sheet.gender, a.sheet.age, a.sheet.appearance].filter(Boolean).join(", "), 70)) || "no details yet"}</small>${story && story.avatar_id === a.id ? '<small class="evn">✓ used in this story</small>' : ""}
        <div class="avbtns"><button data-edit="${esc(a.id)}">Edit</button>${story ? `<button data-use="${esc(a.id)}">Use here</button>` : ""}</div></div></div>`).join("")}
      <button class="charcard addcard" id="avNew"><div class="noart">＋ New avatar</div></button></div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  $("avNew").onclick = () => showAvatarEditor(null);
  document.querySelectorAll("[data-edit]").forEach((b) => b.onclick = () => showAvatarEditor(b.dataset.edit));
  document.querySelectorAll("[data-use]").forEach((b) => b.onclick = async () => { await useLibraryAvatar(b.dataset.use); closeModal(); });
}

async function useLibraryAvatar(id) {
  if (story.avatar && Object.keys(story.avatar).length && story.avatar_id !== id && !confirm("Replace this story's current avatar (sheet and picture) with the selected one? A backup of the story is saved first.")) return;
  story = await api(`/stories/${story.id}/avatar/select`, { method: "POST", body: { id } });
  renderAll();
}

async function showAvatarEditor(id) {
  let a = id ? (await api("/avatars")).find((x) => x.id === id) : { id: null, name: "", sheet: {}, image: "" };
  if (!a) return showAvatarLibrary();
  const stories = await api("/stories");
  modal(`<h2>${a.id ? "Edit avatar" : "New avatar"}</h2>
    <div class="avrow"><div id="lvPreview">${a.image ? `<img class="avatar" src="${esc(a.image)}" alt="">` : '<div class="avatar noimg">No image</div>'}</div>
      <div><input type="file" id="lvFile" accept="image/png,image/jpeg,image/webp,image/gif" style="padding:4px">
        <div class="actions" style="justify-content:flex-start;margin-top:8px"><button id="lvGen">✨ Draw 4 options from the info below</button></div>
        <div class="field"><label>Style it for the world of</label><select id="lvStory"><option value="">No particular story (plain)</option>${stories.map((s) => `<option value="${esc(s.id)}" ${story && story.id === s.id ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select></div>
        <div id="lvNote" class="hint">Uploads apply immediately. Generating uses that story's picture maker and setting, so the avatar fits its world; pick another story later to make a different version.</div></div></div>
    <div class="grid2" style="grid-template-columns:1fr 1fr 1fr">${AV_FIELDS.slice(0, 3).map(([k, l]) => `<div><label>${l}</label><input id="lv_${k}" value="${esc(a.sheet[k] || "")}"></div>`).join("")}</div>
    ${AV_FIELDS.slice(3).map(([k, l, r]) => `<div><label>${l}</label><textarea class="short" id="lv_${k}" rows="${r}">${esc(a.sheet[k] || "")}</textarea></div>`).join("")}
    <div class="actions sticky">${a.id ? '<button class="danger" id="lvDel">Delete avatar</button>' : ""}<span style="flex:1"></span><button id="lvBack">← Library</button><button class="primary" id="lvSave">Save</button></div>`);
  const sheet = () => Object.fromEntries(AV_FIELDS.map(([k]) => [k, $("lv_" + k).value]));
  const ensure = async () => { if (!a.id) { a = await api("/avatars", { method: "POST", body: { sheet: sheet(), name: $("lv_name").value } }); } return a.id; };
  const prev = (url) => { $("lvPreview").innerHTML = `<img class="avatar" src="${esc(url)}" alt="">`; };
  $("lvBack").onclick = showAvatarLibrary;
  $("lvSave").onclick = async () => { const body = { sheet: sheet(), name: $("lv_name").value }; if (a.id) await api("/avatars/" + a.id, { method: "PUT", body }); else await api("/avatars", { method: "POST", body }); showAvatarLibrary(); };
  if ($("lvDel")) $("lvDel").onclick = async () => { if (confirm(`Delete “${a.name}” from the library? Stories already using it keep their own copy.`)) { await api("/avatars/" + a.id, { method: "DELETE" }); showAvatarLibrary(); } };
  $("lvFile").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return; const aid = await ensure();
    const r = await fetch(`/api/avatars/${aid}/image`, { method: "POST", headers: { "content-type": f.type }, body: f });
    if (!r.ok) { $("lvNote").textContent = "Upload failed."; return; }
    a = await r.json(); prev(a.image);
  };
  let opts = [], look = "";
  const showOpts = (aid) => {
    $("lvNote").innerHTML = opts.length ? `<b>Pick a picture.</b> Nothing is saved until you choose one.
      <div class="gallery">${opts.map((u) => `<figure><img loading="lazy" src="${thumbUrl(u, 480)}" alt=""><figcaption><button class="primary lvUse" data-url="${esc(u)}">Use this one</button></figcaption></figure>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start"><button id="lvMore">Draw 4 more</button></div><div class="hint">Drawn from: ${esc(look)}</div>` : "";
    document.querySelectorAll(".lvUse").forEach((b) => b.onclick = async () => { a = await api(`/avatars/${aid}/use`, { method: "POST", body: { url: b.dataset.url } }); prev(a.image); opts = []; $("lvNote").textContent = "Saved."; });
    if ($("lvMore")) $("lvMore").onclick = () => gen(false);
  };
  const gen = async (fresh = true) => {
    $("lvGen").disabled = true; $("lvNote").textContent = "Starting…";
    try {
      const aid = await ensure();
      const r = await api(`/avatars/${aid}/candidates`, { method: "POST", body: { sheet: sheet(), story_id: $("lvStory").value || null, count: 4, fresh } });
      const job = await watchJob(r.job, (j) => { $("lvNote").textContent = j.current || "Drawing…"; });
      if (job.status === "error") $("lvNote").textContent = "Could not draw: " + job.error;
      else { if (fresh) opts = []; opts.push(...job.result.candidates); look = job.result.look; showOpts(aid); }
    } catch (err) { $("lvNote").textContent = err.message; }
    $("lvGen").disabled = false;
  };
  $("lvGen").onclick = () => gen(true);
}

// Player → Avatar: the library row (only when the library is on)
function avatarLibraryRow() {
  return `<div class="aiBox" id="avLib"><b>Avatar library</b> <small class="muted">pick which saved avatar this story uses</small>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;margin-top:6px"><select id="avLibSel"><option value="">Loading…</option></select><button id="avLibUse">Use in this story</button><button id="avLibSave" title="Save this story's avatar sheet and picture to the library">Save to library</button><button id="avLibMan">Manage…</button></div><div class="hint" id="avLibNote"></div></div>`;
}
async function avatarLibraryInit() {
  const list = await api("/avatars");
  $("avLibSel").innerHTML = list.length ? list.map((a) => `<option value="${esc(a.id)}" ${story.avatar_id === a.id ? "selected" : ""}>${esc(a.name)}</option>`).join("") : '<option value="">(library is empty)</option>';
  $("avLibUse").onclick = async () => { const id = $("avLibSel").value; if (!id) return; await useLibraryAvatar(id); showPlayer(); };
  $("avLibSave").onclick = async () => { try { const r = await api(`/stories/${story.id}/avatar/save-to-library`, { method: "POST" }); story = r.story; $("avLibNote").textContent = `Saved to the library as “${r.avatar.name}”.`; avatarLibraryInit(); } catch (e) { $("avLibNote").textContent = e.message; } };
  $("avLibMan").onclick = showAvatarLibrary;
}
