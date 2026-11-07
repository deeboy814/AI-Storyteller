// Story creator / editor: premake everything a story can start with (prompt, cast with pictures, prologue, opening scene + suggested first moves,
// suggested trackers, endings, media). New stories collect it all in memory and create it in one go; existing stories edit the same parts
// (their cast and media are managed in the Cast screen / Home card).
function editStory(s) { showStoryForm(s); }

async function showStoryForm(s) {
  const isNew = !s;
  if (!TRACKER_FIELDS.length) TRACKER_FIELDS = await api("/tracker-fields");
  const D = {   // the draft
    title: s ? s.title : "", subtitle: s ? s.subtitle || "" : "", description: s ? s.description || "" : "", base_prompt: s ? s.base_prompt : "",
    prologue: s ? s.prologue || "" : "", opening: s ? s.opening || "" : "", opening_in_context: !s || s.opening_in_context !== false,
    opening_options: s ? [...(s.opening_options || [])] : [], endings: s ? (s.endings || []).map((e) => ({ ...e })) : [],
    sidebar: s ? JSON.parse(JSON.stringify(s.sidebar || {})) : { mode: (config && config.sidebar_default_mode) || "prompt", fields: TRACKER_FIELDS.map((f) => f.id), schema: null, custom: [] },
    art: { direction: s && s.art ? s.art.direction || "" : (config.default_art_direction || ""), setting: s && s.art ? s.art.setting || "" : "" },
    use_state_block: !s || s.use_state_block !== false, auto_replies: !!(s && s.auto_replies), nsfw: false,
    cast: [], cover: null, intro: [], backdrop: null, models: null,
  };
  const tabs = isNew ? ["Basics", "Cast", "Story start", "Trackers & endings", "Media", "Options"] : ["Basics", "Story start", "Trackers & endings", "Options"];
  let tab = "Basics", picker = null, trk = null;
  const aiHelp = (kind) => api("/suggest", { method: "POST", body: { kind, title: D.title, base_prompt: D.base_prompt, opening: D.opening, prologue: D.prologue, cast: D.cast.map((c) => ({ name: c.name, appearance: c.appearance, personality: c.personality })), ...modelOf() } });
  const modelOf = () => { const r = D.models && D.models.recaps; return r ? { provider: r.provider, model: r.model } : {}; };
  const val = (id) => { const e = $(id); return e ? e.value : undefined; };
  const chk = (id, on, title, help) => `<label class="chk top"><input type="checkbox" id="${id}" ${on ? "checked" : ""}><span><b>${title}</b><span class="hint">${help}</span></span></label>`;
  const aiBtn = (id, label) => `<button id="${id}" class="aibtn">✨ ${label}</button>`;

  // ---- read the visible pane back into the draft
  const collect = () => {
    if (tab === "Basics") { D.title = val("fTitle"); D.subtitle = val("fSub"); D.description = val("fDesc"); D.base_prompt = val("fPrompt"); }
    if (tab === "Cast") document.querySelectorAll(".ccard").forEach((el) => { const c = D.cast[+el.dataset.i]; for (const k of ["name", "appearance", "personality", "bio"]) c[k] = el.querySelector(`[data-k="${k}"]`).value; });
    if (tab === "Story start") { D.prologue = val("fPro"); D.opening = val("fOpening"); D.opening_in_context = $("fOpenCtx").checked; D.opening_options = [...document.querySelectorAll(".optrow input")].map((i) => i.value).filter((x) => x.trim()); }
    if (tab === "Trackers & endings") {
      D.sidebar.mode = val("sfMode"); if (D.sidebar.mode === "player") { D.sidebar.fields = [...document.querySelectorAll("#sfFields input:checked")].map((i) => i.dataset.f); if (trk) D.sidebar.custom = trk.get(); }
      D.endings = [...document.querySelectorAll(".endrow")].map((r) => ({ title: r.querySelector("input").value, text: r.querySelector("textarea").value })).filter((e) => e.title.trim() || e.text.trim());
      D.art.direction = val("sfArtDir"); D.art.setting = val("sfArtSet");
    }
    if (tab === "Options") { D.use_state_block = $("fState").checked; D.auto_replies = $("fAutoRep").checked; if ($("fNsfw")) D.nsfw = $("fNsfw").checked; if (picker) D.models = picker.get(); }
  };

  // ---- panes
  const panes = {
    "Basics": () => `<h3>Story</h3>
      <div class="grid2" style="grid-template-columns:2fr 1fr"><div class="field"><label>Title</label><input id="fTitle" value="${esc(D.title)}"></div><div class="field"><label>Genre / subtitle</label><input id="fSub" value="${esc(D.subtitle)}"></div></div>
      <div class="field"><label>Short description <span class="muted">(shown on the Home card)</span></label><textarea class="short" id="fDesc" rows="2" maxlength="400">${esc(D.description)}</textarea>${aiBtn("aiDesc", "Write it from the prompt")}</div>
      <h3>Story prompt</h3><div class="hint">Worldbuilding, characters, stats, prose style and rules. It is sent to the AI every turn; changes apply from the next turn.</div>
      <textarea id="fPrompt" style="min-height:300px" placeholder="Paste or write your story prompt here: the world, characters, rules and writing style…">${esc(D.base_prompt)}</textarea>
      <div class="actions" style="justify-content:flex-start"><button id="fTest" title="Plays one sample move against this prompt and checks the reply for problems (two small model calls)">🧪 Test this prompt</button></div><div id="fTestOut"></div>`,
    "Cast": () => `<h3>Premade cast</h3><div class="hint">Characters players will meet. The narrator is told each one's looks, personality and background; the bio is also shown in the Cast screen. Add pictures now (one file per picture; the file name becomes the mood, e.g. <code>smile 1.webp</code>), or draw them after the story is created.</div>
      <div id="castList">${D.cast.map((c, i) => `<div class="ccard" data-i="${i}">
          <div class="grid2" style="grid-template-columns:1fr 2fr"><div class="field"><label>Name</label><input data-k="name" value="${esc(c.name)}"></div><div class="field"><label>How they look <small class="muted">(for the artist)</small></label><input data-k="appearance" value="${esc(c.appearance)}"></div></div>
          <div class="field"><label>Personality</label><input data-k="personality" value="${esc(c.personality)}" placeholder="traits, how they speak, what they want"></div>
          <div class="field"><label>Bio / background</label><textarea class="short" data-k="bio" rows="2">${esc(c.bio)}</textarea></div>
          <div class="cfiles">${c.files.map((f, j) => `<figure class="mood"><img src="${f.url}" alt=""><figcaption>${esc(f.mood)} <button class="x cfdel" data-i="${i}" data-j="${j}" title="Remove">×</button></figcaption></figure>`).join("")}
            <label class="btnlike">＋ Pictures<input type="file" class="cfile" data-i="${i}" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden></label></div>
          <div class="actions" style="justify-content:flex-start"><button class="danger crm" data-i="${i}">Remove character</button></div></div>`).join("") || '<p class="hint">No characters yet.</p>'}</div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="castAdd">＋ Add a character</button>${aiBtn("aiCast", "Suggest the cast from the prompt")}<span class="hint" id="castNote"></span></div>`,
    "Story start": () => `<h3>Prologue <small class="muted">(optional)</small></h3><div class="hint">The events leading up to the story. Shown at the start (collapsible) and known to the narrator as background.</div>
      <textarea id="fPro" style="min-height:140px">${esc(D.prologue)}</textarea>${aiBtn("aiPro", "Write a prologue from the prompt")}
      <h3>Opening scene <small class="muted">(optional)</small></h3><div class="hint">The story's first message, shown before the first move. <code>![](intro:It1)</code> places an intro panel.</div>
      <textarea id="fOpening" style="min-height:160px">${esc(D.opening)}</textarea>${aiBtn("aiOpen", "Write an opening scene")}
      ${chk("fOpenCtx", D.opening_in_context, "The narrator sees the opening", "Includes the opening scene as the story's first message so the narrator continues from it.")}
      <h3>Suggested first moves</h3><div class="hint">Up to 4 options shown under the opening scene. The player picks one, edits it and sends it.</div>
      <div id="optList">${D.opening_options.map((o) => `<div class="optrow"><input value="${esc(o)}"><button class="x optdel" title="Remove">×</button></div>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="optAdd">＋ Add an option</button>${aiBtn("aiOpts", "Suggest first moves")}<span class="hint" id="startNote"></span></div>`,
    "Trackers & endings": () => `<h3>Suggested side panel</h3><div class="hint">What the side panel tracks for this story. Players can change it in Settings.</div>
      <div class="field"><select id="sfMode"><option value="prompt">Whatever the story prompt's own status panel provides</option><option value="player">These fields</option><option value="ai">Fields the AI decides</option></select></div>
      <div id="sfFields" class="hidden">${[["scene", "Scene"], ["me", "You"], ["chars", "Characters"]].map(([g, t]) => `<div class="sbgroup"><b>${t}</b>${TRACKER_FIELDS.filter((f) => f.id.startsWith(g + ".")).map((f) => `<label class="chk"><input type="checkbox" data-f="${f.id}" ${(D.sidebar.fields || []).includes(f.id) ? "checked" : ""}> ${esc(f.label)}</label>`).join("")}</div>`).join("")}</div>
      <div id="sfCustom" class="hidden"></div>
      <div id="sfAi" class="hidden">${D.sidebar.schema && D.sidebar.schema.length ? `<ul class="sbl">${D.sidebar.schema.map((f) => `<li><b>${esc(f.label)}</b> <small>${esc(f.id)} · ${esc(f.kind)}</small></li>`).join("")}</ul>` : '<div class="hint">Nothing chosen yet.</div>'}${aiBtn("aiTrack", "Let the AI suggest trackers from the prompt")}<span class="hint" id="trackNote"></span></div>
      <h3>Art style <small class="muted">(optional)</small></h3>
      ${styleSelectHtml("sfArtDir", "players of a story with no pre-made characters pick the look here; if your characters already have pictures, describe their style instead")}
      <div class="field"><label>Art direction</label><textarea class="short" id="sfArtDir" rows="2" placeholder="e.g. anime light-novel illustration, muted palette">${esc(D.art.direction)}</textarea></div>
      <div class="field"><label>Setting <small class="muted">(the world characters belong to; written automatically if left empty)</small></label><textarea class="short" id="sfArtSet" rows="2">${esc(D.art.setting)}</textarea></div>
      <h3>Possible endings <small class="muted">(optional, spoilers)</small></h3><div class="hint">Hidden notes for the narrator: it may steer toward one when the player's choices lead there, and never reveals them. Players can also ask the story to work toward one (🏁 Endings).</div>
      <div id="endList">${D.endings.map((e) => `<div class="endrow"><input value="${esc(e.title)}" placeholder="Ending title"><textarea class="short" rows="2" placeholder="How it ends">${esc(e.text)}</textarea><button class="x enddel" title="Remove">×</button></div>`).join("")}</div>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="endAdd">＋ Add an ending</button>${aiBtn("aiEnds", "Suggest endings")}<span class="hint" id="endNote"></span></div>`,
    "Media": () => `<h3>Cover</h3><div class="hint">Shown on the Home card. You can generate one later from the Home card.</div>
      <div class="actions" style="justify-content:flex-start;align-items:center"><label class="btnlike">${D.cover ? "Change cover…" : "Choose a cover…"}<input type="file" id="mCover" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${D.cover ? `<img class="bdprev" style="max-width:200px;margin:0" src="${D.cover.url}" alt=""><button class="x" id="mCoverDel">×</button>` : ""}</div>
      <h3>Intro / webtoon panels</h3><div class="hint">Shown in order where the opening scene says <code>![](intro:Name)</code> (the file name without its extension is the name).</div>
      <div class="cfiles">${D.intro.map((f, j) => `<figure class="mood"><img src="${f.url}" alt=""><figcaption>${esc(f.name)} <button class="x indel" data-j="${j}">×</button></figcaption></figure>`).join("")}<label class="btnlike">＋ Panels<input type="file" id="mIntro" accept="image/png,image/jpeg,image/webp,image/gif" multiple hidden></label></div>
      <h3>Backdrop</h3><div class="hint">A picture for the sides of the screen (Settings → Appearance). Or generate one later from the story's setting.</div>
      <div class="actions" style="justify-content:flex-start;align-items:center"><label class="btnlike">${D.backdrop ? "Change backdrop…" : "Choose a backdrop…"}<input type="file" id="mBack" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>${D.backdrop ? `<img class="bdprev" style="max-width:200px;margin:0" src="${D.backdrop.url}" alt=""><button class="x" id="mBackDel">×</button>` : ""}</div>`,
    "Options": () => `<h3>Options</h3>
      ${chk("fState", D.use_state_block, "Let the app track the story state", "Fills the sidebar from a hidden block. Turn off if your prompt prints its own status panel.")}
      ${chk("fAutoRep", D.auto_replies, "Suggest three replies after every narrator reply", "Otherwise use the 💡 button. Costs one small call each time.")}
      ${isNew ? chk("fNsfw", D.nsfw, "Adult (NSFW) content", "Allows explicit content between adult characters, in the text and in scene pictures. Off by default; change it later in Settings.") : ""}
      ${isNew ? '<h3>Which AI should run this story?</h3><div id="aiPick"></div><div class="hint">A one-time choice for this story. You can change it later in Settings.</div>' : ""}`,
  };

  const pick = (inp) => [...inp.files].map((f) => ({ file: f, url: URL.createObjectURL(f), name: f.name.replace(/\.[^.]+$/, ""), mood: f.name.replace(/\.[^.]+$/, "").toLowerCase().replace(/[_]+/g, " ").trim() || "normal 1" }));

  const bind = () => {
    const note = (id, t) => { const e = $(id); if (e) e.textContent = t; };
    const ai = (btnId, kind, noteId, apply) => { const b = $(btnId); if (!b) return; b.onclick = async () => {
      collect(); if (!D.base_prompt.trim()) { alert("Write the story prompt first (Basics tab)."); return; }
      b.disabled = true; const old = b.textContent; b.textContent = "✨ Working…";
      try { const r = await aiHelp(kind); apply(r.result); } catch (e) { if (noteId) note(noteId, e.message); else alert(e.message); }
      b.disabled = false; b.textContent = old; }; };
    if (tab === "Basics") {
      $("fTest").onclick = () => runPromptTest({ base_prompt: val("fPrompt"), opening: D.opening, use_state_block: D.use_state_block, avatar: (s && s.avatar) || {} }, (picker && picker.get().story) || (s && s.models && s.models.story) || null);
      ai("aiDesc", "description", null, (t) => { $("fDesc").value = t; });
    }
    if (tab === "Cast") {
      $("castAdd").onclick = () => { collect(); D.cast.push({ name: "", appearance: "", personality: "", bio: "", files: [] }); render(); };
      document.querySelectorAll(".crm").forEach((b) => b.onclick = () => { collect(); D.cast.splice(+b.dataset.i, 1); render(); });
      document.querySelectorAll(".cfile").forEach((inp) => inp.onchange = () => { collect(); D.cast[+inp.dataset.i].files.push(...pick(inp)); render(); });
      document.querySelectorAll(".cfdel").forEach((b) => b.onclick = () => { collect(); D.cast[+b.dataset.i].files.splice(+b.dataset.j, 1); render(); });
      ai("aiCast", "cast", "castNote", (list) => { collect(); const have = new Set(D.cast.map((c) => c.name.trim().toLowerCase())); let n = 0; for (const c of list) { if (!c.name || have.has(String(c.name).toLowerCase())) continue; D.cast.push({ name: c.name, appearance: c.appearance || "", personality: c.personality || "", bio: c.bio || "", files: [] }); n++; } render(); note("castNote", `Added ${n} character${n === 1 ? "" : "s"}. Edit them as you like.`); });
    }
    if (tab === "Story start") {
      ai("aiPro", "prologue", "startNote", (t) => { $("fPro").value = t; });
      ai("aiOpen", "opening", "startNote", (t) => { $("fOpening").value = t; });
      $("optAdd").onclick = () => { collect(); if (D.opening_options.length < 4) D.opening_options.push(""); render(); const r = document.querySelectorAll(".optrow input"); if (r.length) r[r.length - 1].focus(); };
      document.querySelectorAll(".optdel").forEach((b, i) => b.onclick = () => { collect(); D.opening_options.splice(i, 1); render(); });
      ai("aiOpts", "opening_options", "startNote", (list) => { D.opening_options = list.map(String).slice(0, 4); render(); });
    }
    if (tab === "Trackers & endings") {
      $("sfMode").value = D.sidebar.mode;
      trk = trackerEditor($("sfCustom"), D.sidebar.custom || [], () => ({ title: D.title, base_prompt: D.base_prompt, models: modelOf() }));
      const vis = () => { $("sfFields").classList.toggle("hidden", $("sfMode").value !== "player"); $("sfCustom").classList.toggle("hidden", $("sfMode").value !== "player"); $("sfAi").classList.toggle("hidden", $("sfMode").value !== "ai"); };
      $("sfMode").onchange = vis; vis();
      ai("aiTrack", "sidebar", "trackNote", (schema) => { collect(); D.sidebar.schema = schema; D.sidebar.mode = "ai"; render(); });
      $("endAdd").onclick = () => { collect(); D.endings.push({ title: "", text: "" }); render(); };
      document.querySelectorAll(".enddel").forEach((b, i) => b.onclick = () => { collect(); D.endings.splice(i, 1); render(); });
      ai("aiEnds", "endings", "endNote", (list) => { collect(); for (const e of list) if (e && (e.title || e.text)) D.endings.push({ title: String(e.title || ""), text: String(e.text || "") }); render(); });
    }
    if (tab === "Media") {
      $("mCover").onchange = (e) => { const f = pick(e.target)[0]; if (f) { D.cover = f; render(); } };
      if ($("mCoverDel")) $("mCoverDel").onclick = () => { D.cover = null; render(); };
      $("mIntro").onchange = (e) => { D.intro.push(...pick(e.target).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))); render(); };
      document.querySelectorAll(".indel").forEach((b) => b.onclick = () => { D.intro.splice(+b.dataset.j, 1); render(); });
      $("mBack").onchange = (e) => { const f = pick(e.target)[0]; if (f) { D.backdrop = f; render(); } };
      if ($("mBackDel")) $("mBackDel").onclick = () => { D.backdrop = null; render(); };
    }
    if (tab === "Options" && isNew) {
      api("/config").then((cf) => { picker = mountAIChooser($("aiPick"), { initial: D.models || cf.default_models || {} }); });
    }
  };

  const render = () => {
    modal(`<h2>${isNew ? "New story" : "Edit story"}</h2>
      <div class="tabs">${tabs.map((t) => `<button class="tab sft ${t === tab ? "on" : ""}" data-t="${t}">${t}</button>`).join("")}</div>
      <div class="stpane" id="sfPane">${panes[tab]()}</div>
      <div class="actions sticky"><button id="mClose">Cancel</button><button class="primary" id="mSave">${isNew ? "Create & play" : "Save"}</button><span class="hint" id="sfProg"></span></div>`);
    $("modalCard").classList.add("tall");
    document.querySelectorAll(".sft").forEach((b) => b.onclick = () => { collect(); tab = b.dataset.t; render(); });
    $("mClose").onclick = closeModal;
    $("mSave").onclick = save;
    bind();
  };

  async function save() {
    collect();
    if (!D.title.trim()) { tab = "Basics"; render(); alert("Give the story a title."); return; }
    const prog = (t) => { const e = $("sfProg"); if (e) e.textContent = t; };
    $("mSave").disabled = true;
    const body = { title: D.title, subtitle: D.subtitle, base_prompt: D.base_prompt, description: D.description, prologue: D.prologue, opening: D.opening, opening_in_context: D.opening_in_context,
      opening_options: D.opening_options, endings: D.endings, auto_replies: D.auto_replies, use_state_block: D.use_state_block, art: D.art,
      sidebar: D.sidebar.mode === "player" ? { mode: "player", fields: D.sidebar.fields, custom: D.sidebar.custom || [] } : D.sidebar.mode === "ai" ? { mode: "ai", schema: D.sidebar.schema } : { mode: "prompt" } };
    try {
      if (!isNew) { story = await api("/stories/" + s.id, { method: "PUT", body }); localStorage.setItem("story", story.id); closeModal(); renderAll(); applyTheme(config); return; }
      if (D.models && Object.keys(D.models).length) { body.models = D.models; await api("/default-models", { method: "PUT", body: body.models }); }
      body.nsfw = D.nsfw;
      body.cast = D.cast.filter((c) => c.name.trim()).map((c) => ({ name: c.name, appearance: c.appearance, bio: c.bio, personality: c.personality }));
      prog("Creating the story…");
      story = await api("/stories", { method: "POST", body });
      const up = async (path, f) => { const r = await fetch(`/api${path}`, { method: "POST", headers: { "content-type": f.file.type }, body: f.file }); if (r.ok) story = await r.json(); };
      let n = 0; const total = D.cast.reduce((a, c) => a + c.files.length, 0) + D.intro.length + (D.cover ? 1 : 0) + (D.backdrop ? 1 : 0);
      for (const c of D.cast) {
        const ch = story.cast.find((x) => x.name.toLowerCase() === c.name.trim().toLowerCase()); if (!ch) continue;
        for (const f of c.files) { prog(`Uploading pictures… ${++n}/${total}`); await up(`/stories/${story.id}/cast/${ch.slug}/mood/${encodeURIComponent(f.mood)}`, f); }
      }
      if (D.cover) { prog(`Uploading pictures… ${++n}/${total}`); await up(`/stories/${story.id}/cover`, D.cover); }
      for (const f of D.intro) { prog(`Uploading pictures… ${++n}/${total}`); await up(`/stories/${story.id}/intro?name=${encodeURIComponent(f.name)}`, f); }
      if (D.backdrop) { prog(`Uploading pictures… ${++n}/${total}`); await up(`/stories/${story.id}/backdrop-image`, D.backdrop); }
      localStorage.setItem("story", story.id);
      closeModal(); hideHome(); renderAll(); applyTheme(config); runtimeSelect();
    } catch (e) { $("mSave").disabled = false; prog(""); alert("Could not save: " + e.message); }
  }
  render();
}
