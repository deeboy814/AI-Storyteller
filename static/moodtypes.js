// Image categories window, and the Prompt window of a single picture: its exact prompt, editable, with a button to draw again from the change. No outside AI or tinkering needed.
// Image categories: the groups of pictures (Expressions, Actions, Positions, Finishes, Other adult by default; every one renamable, changeable and deletable, and you can add your own) and the
// pictures in them. Each category is also a batch-generation set ("Draw all of …"). Every picture has a look, a frame, tag words, a note for the narrator and an adult flag,
// and shows the exact prompt that is sent for it.
async function showMoodTypes(startName, back) {
  if (!story) return;
  let data;
  try { data = await api(`/moods?sid=${story.id}`); } catch (e) { return alert(e.message); }
  const adultOk = !!data.story_nsfw;
  let list = data.moods, cats = data.categories, cur = null, previewTimer = 0, previewSeq = 0;
  const chars = (story.cast || []).filter((c) => !c.generic && Object.keys(c.moods || {}).length);
  const catOf = (id) => cats.find((c) => c.id === id) || cats[0];
  const inCat = (id) => list.filter((m) => m.category === id && (adultOk || !m.nsfw)).sort((a, b) => a.name.localeCompare(b.name));
  modal(`<h2>🗂 Image categories</h2>
    <div class="hint">Every picture the app can draw is in a category. The defaults are Expressions, Actions, and three adult ones, Positions, Finishes and Other adult: rename them, change them, add your own. Each category is also a batch set: wherever you pick what to draw for a character, “All: &lt;category&gt;” draws every picture in it. For each picture you can change what it draws, how it is framed, when the narrator should use it, and flag it adult (NSFW). You always see the exact prompt that is sent.</div>
    <div class="mtwrap"><div class="mtlist"><input id="mtFilter" placeholder="Search…"><div id="mtItems"></div><button id="mtNew" class="primary" style="width:100%;margin-top:6px">＋ New picture</button><button id="mtCats" style="width:100%;margin-top:4px">⚙ Edit categories</button></div>
    <div class="mted" id="mtEd"><p class="hint">Pick a picture on the left, make a new one, or edit the categories.</p></div></div>
    <div class="actions"><span class="hint">${adultOk ? "" : "Adult pictures are hidden because this story has adult content off."}</span><span style="flex:1"></span><button id="mClose">Close</button></div>`);
  const close = () => { if (back) back(); else closeModal(); };
  $("mClose").onclick = close;
  const reload = async () => { data = await api(`/moods?sid=${story.id}`); list = data.moods; cats = data.categories; config = await api("/config"); };
  const drawList = () => {
    const q = $("mtFilter").value.trim().toLowerCase();
    $("mtItems").innerHTML = cats.filter((c) => adultOk || !c.nsfw).map((c) => {
      const items = inCat(c.id).filter((m) => !q || m.name.includes(q));
      return (items.length || !q) ? `<div class="mtgroup">${esc(c.name)}${c.nsfw ? ' <span class="mtbadge">NSFW</span>' : ""} <span class="muted">${items.length}</span></div>` + items.map((m) => `<button class="mtitem${cur && cur.name === m.name ? " on" : ""}" data-n="${esc(m.name)}">${esc(m.name)}${m.nsfw && !c.nsfw ? ' <span class="mtbadge">NSFW</span>' : ""}${m.edited ? ' <span class="mtedit" title="Changed by you">✎</span>' : ""}</button>`).join("") : "";
    }).join("");
    $("mtItems").querySelectorAll(".mtitem").forEach((b) => b.onclick = () => open(list.find((m) => m.name === b.dataset.n)));
  };
  const field = (id, label, val, rows, hint) => `<label>${label}</label>${hint ? `<div class="hint">${hint}</div>` : ""}<textarea class="short" id="${id}" rows="${rows}">${esc(val || "")}</textarea>`;

  // ---- the categories themselves ----
  const editCats = () => {
    cur = null; drawList(); clearTimeout(previewTimer); previewSeq++;
    let rows = cats.map((c) => ({ ...c }));
    const draw = () => {
      $("mtEd").innerHTML = `<h3>Categories</h3><div class="hint">Names are yours to change. Each category is a batch set (“All: name”). “Adult” makes everything in it adult: drawn with the adult wording, hidden from the narrator and from batch drawing in stories without adult content. “Shot” decides how its pictures are framed: a portrait of the face, or a three-quarter action shot. The note is sent to the narrator (when to use these pictures); leave it empty if the narrator needs no special rule.</div>
        ${rows.map((c, i) => `<div class="catrow" data-i="${i}"><div class="cathead"><input class="cN" value="${esc(c.name)}" placeholder="Category name"><label class="chk"><input type="checkbox" class="cA" ${c.nsfw ? "checked" : ""}> Adult</label>
          <select class="cS"><option value="face" ${c.shot === "face" ? "selected" : ""}>Portrait</option><option value="body" ${c.shot === "body" ? "selected" : ""}>Action shot</option></select>
          <button class="cUp" title="Move up" ${i ? "" : "disabled"}>↑</button><button class="cDn" title="Move down" ${i < rows.length - 1 ? "" : "disabled"}>↓</button><button class="cDel" title="Delete this category (its pictures move to the first one)" ${rows.length > 1 ? "" : "disabled"}>×</button></div>
          <textarea class="short cNote" rows="2" placeholder="Note for the narrator: when to use the pictures in this category (optional)">${esc(c.note || "")}</textarea></div>`).join("")}
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="catAdd">＋ New category</button><button id="catReset" title="Put the five default categories back (Expressions, Actions, Positions, Finishes, Other adult)">Back to the defaults</button><span style="flex:1"></span><button class="primary" id="catSave">Save categories</button><span class="hint" id="catMsg"></span></div>`;
      const read = () => document.querySelectorAll(".catrow").forEach((r) => { const c = rows[+r.dataset.i]; c.name = r.querySelector(".cN").value; c.nsfw = r.querySelector(".cA").checked; c.shot = r.querySelector(".cS").value; c.note = r.querySelector(".cNote").value; });
      document.querySelectorAll(".catrow").forEach((r) => {
        const i = +r.dataset.i;
        r.querySelector(".cUp").onclick = () => { read(); [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]]; draw(); };
        r.querySelector(".cDn").onclick = () => { read(); [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]]; draw(); };
        r.querySelector(".cDel").onclick = () => { read(); const n = (cats.find((x) => x.id === rows[i].id) || {}).count || 0; if (n && !confirm(`Delete “${rows[i].name}”? Its ${n} picture type${n === 1 ? "" : "s"} move to “${rows.find((_, j) => j !== i).name}”.`)) return; rows.splice(i, 1); draw(); };
      });
      $("catAdd").onclick = () => { read(); rows.push({ id: null, name: "", nsfw: false, shot: "face", note: "" }); draw(); const ns = document.querySelectorAll(".cN"); ns[ns.length - 1].focus(); };
      $("catReset").onclick = async () => { if (!confirm("Put the five default categories back? Names, adult flags and notes you changed are replaced; pictures stay where they are.")) return; try { rows = (await api("/image-categories/defaults")).categories; draw(); } catch (e) { $("catMsg").textContent = e.message; } };
      $("catSave").onclick = async () => {
        read(); if (rows.some((c) => !c.name.trim())) { $("catMsg").textContent = "Every category needs a name."; return; }
        try {
          const body = { categories: rows.map((c) => ({ id: c.id, name: c.name, nsfw: c.nsfw, shot: c.shot, note: c.note })) };
          await api("/image-categories", { method: "PUT", body }); await reload(); rows = cats.map((c) => ({ ...c })); $("catMsg").textContent = "✔ Saved. Batch drawing and the narrator use the new categories from now on."; drawList(); draw(); $("catMsg").textContent = "✔ Saved. Batch drawing and the narrator use the new categories from now on.";
        } catch (e) { $("catMsg").textContent = e.message; }
      };
    };
    draw();
  };

  // ---- one picture ----
  const open = (m, isNew) => {
    cur = m; drawList();
    const hasStory = !!m.story && (m.story.look || m.story.framing || m.story.framing_tags);
    $("mtEd").innerHTML = `
      <div class="mthead"><input id="mtName" value="${esc(m.name)}" ${isNew ? "" : "disabled"} placeholder="Name, for example blushing or spooning"><span class="hint">${isNew ? "This is the name the narrator uses in a portrait tag." : m.builtin ? (m.edited ? "Built in, changed by you." : "Built in.") : "Made by you."}</span></div>
      <div class="grid2" style="grid-template-columns:1fr 1fr">
        <div><label>Category</label><select id="mtCat">${cats.map((c) => `<option value="${esc(c.id)}" ${m.category === c.id ? "selected" : ""}>${esc(c.name)}${c.nsfw ? " (adult)" : ""}</option>`).join("")}</select></div>
        <div><label>Also in the sets</label><div class="mtsets"><label class="chk"><input type="checkbox" id="mtBasic" ${(m.sets || []).includes("basic") ? "checked" : ""}> Basic</label> <label class="chk"><input type="checkbox" id="mtExt" ${(m.sets || []).includes("extended") ? "checked" : ""}> Extended</label></div></div></div>
      <label class="chk top"><input type="checkbox" id="mtNsfw" ${m.nsfw ? "checked" : ""}><span><b>Adult (NSFW)</b><span class="hint" id="mtNsfwHint">Drawn with the adult wording. Hidden from the narrator and from batch drawing unless the story has adult content on.</span></span></label>
      <div class="mtscope"><label class="chk"><input type="radio" name="mtScope" value="all" checked> All my stories</label> <label class="chk"><input type="radio" name="mtScope" value="story"> Only this story (look, frame and tags only${hasStory ? "; this story already has its own wording" : ""})</label></div>
      ${field("mtLook", "Look: what to draw", m.look, 3, "The face AND the body language, in plain words.")}
      ${field("mtFraming", "Frame: how it is shot", m.framing, 2, "Empty = the category's usual frame (a chest-up portrait, or an action shot).")}
      <details class="sub"><summary>For tag-style picture makers (anime models)</summary>
        ${field("mtTags", "Tags", m.tags, 2, "Comma-separated tags. Empty = the helper model writes them from the look (one small call).")}
        ${field("mtFTags", "Frame tags", m.framing_tags, 1, "")}</details>
      ${field("mtNote", "Note for the narrator: when to use it", m.note, 2, "Sent to the narrator with the picture's name, so it knows when to pick it. Optional for built-in ones.")}
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button class="primary" id="mtSave">Save</button>${m.builtin ? `<button id="mtReset" ${m.edited || hasStory ? "" : "disabled"}>Reset to the built-in</button>` : isNew ? "" : `<button id="mtDel">Delete</button>`}<span class="hint" id="mtMsg"></span></div>
      <h3>The prompt sent with it</h3>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><label style="margin:0">Shown for</label><select id="mtChar">${chars.length ? chars.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join("") : '<option value="">(no character has pictures yet)</option>'}</select>
        <button id="mtShow">Show the prompt</button>${chars.length ? '<button id="mtDraw">Save and draw this for them</button>' : ""}</div>
      <div class="hint" id="mtInfo"></div>
      <label>Prompt</label><textarea class="short mono" id="mtPrompt" rows="9" readonly placeholder="Press “Show the prompt”."></textarea>
      <label>Negative prompt</label><textarea class="short mono" id="mtNeg" rows="2" readonly></textarea>`;
    const nsfwSync = () => { const c = catOf($("mtCat").value); if (c.nsfw) { $("mtNsfw").checked = true; $("mtNsfw").disabled = true; $("mtNsfwHint").textContent = `Adult because the category “${c.name}” is adult (change that under Edit categories).`; } else { $("mtNsfw").disabled = false; $("mtNsfwHint").textContent = "Drawn with the adult wording. Hidden from the narrator and from batch drawing unless the story has adult content on."; } };
    nsfwSync();
    const draft = () => ({ look: $("mtLook").value, framing: $("mtFraming").value, tags: $("mtTags").value, framing_tags: $("mtFTags").value, category: $("mtCat").value, nsfw: $("mtNsfw").checked, note: $("mtNote").value });
    const scope = () => document.querySelector('input[name="mtScope"]:checked').value;
    const show = async () => {
      const slug = $("mtChar").value, seq = ++previewSeq; if (!slug) return;
      $("mtInfo").textContent = "Building…";
      try {
        const nm = ($("mtName").value || m.name).trim().toLowerCase();
        const r = await api(`/stories/${story.id}/cast/${slug}/prompt-preview`, { method: "POST", body: { mood: nm, draft: draft() } });
        if (seq !== previewSeq) return;
        $("mtPrompt").value = r.prompt; $("mtNeg").value = r.negative || "";
        $("mtInfo").textContent = `Drawn by ${r.maker || "the picture maker"} · ${r.style === "tags" ? "tag-style prompt" : "sentence prompt"} · ${r.with_references ? r.refs + " reference picture" + (r.refs === 1 ? "" : "s") + " of the character are sent along" : "no reference pictures (this maker cannot take them)"}${r.adult ? " · adult picture" : ""}. Shows your unsaved changes.`;
      } catch (e) { if (seq === previewSeq) $("mtInfo").textContent = "Could not build it: " + e.message; }
    };
    const later = () => { clearTimeout(previewTimer); previewTimer = setTimeout(show, 700); };
    ["mtLook", "mtFraming", "mtTags", "mtFTags", "mtName"].forEach((id) => $(id).addEventListener("input", later));
    ["mtCat", "mtNsfw"].forEach((id) => $(id).addEventListener("change", () => { nsfwSync(); later(); }));
    $("mtChar").onchange = show; $("mtShow").onclick = show;
    const save = async () => {
      const name = ($("mtName").value || "").trim(); if (!name) { $("mtMsg").textContent = "Give it a name."; return false; }
      const d = draft(), sc = scope();
      const sets = [$("mtBasic").checked ? "basic" : null, $("mtExt").checked ? "extended" : null].filter(Boolean);
      const body = sc === "story" ? { name, scope: "story", sid: story.id, look: d.look, framing: d.framing, framing_tags: d.framing_tags } : { name, ...d, nsfw: catOf(d.category).nsfw && !d.nsfw ? undefined : d.nsfw, sets };
      try {
        const r = await api("/moods", { method: "PUT", body });
        if (r.story) story = r.story;
        await reload();
        const saved = list.find((x) => x.name === r.moods[0].name);
        $("mtMsg").textContent = "✔ Saved. The narrator and the picture maker use it from now on."; cur = saved; drawList(); return saved;
      } catch (e) { $("mtMsg").textContent = e.message; return false; }
    };
    $("mtSave").onclick = async () => { const s = await save(); if (s) { $("mtName").disabled = true; } };
    if ($("mtReset")) $("mtReset").onclick = async () => {
      if (!confirm(`Put “${m.name}” back to the built-in wording?`)) return;
      try { await api("/moods", { method: "PUT", body: { name: m.name, reset: true, scope: scope(), sid: story.id } }); await reload(); open(list.find((x) => x.name === m.name)); $("mtMsg").textContent = "Back to the built-in."; } catch (e) { $("mtMsg").textContent = e.message; }
    };
    if ($("mtDel")) $("mtDel").onclick = async () => {
      if (!confirm(`Delete the picture “${m.name}”? Pictures already drawn keep their name.`)) return;
      try { await api("/moods", { method: "PUT", body: { name: m.name, reset: true } }); await reload(); cur = null; $("mtEd").innerHTML = '<p class="hint">Deleted.</p>'; drawList(); } catch (e) { $("mtMsg").textContent = e.message; }
    };
    if ($("mtDraw")) $("mtDraw").onclick = async () => {
      const s = await save(); if (!s) return; const slug = $("mtChar").value, b = $("mtDraw"); b.disabled = true; b.textContent = "Drawing…";
      try { story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood: s.name } }); $("mtMsg").textContent = `✔ Drawn for ${chars.find((c) => c.slug === slug).name}: see their Images tab.`; renderAll(); } catch (e) { $("mtMsg").textContent = e.message; }
      b.disabled = false; b.textContent = "Save and draw this for them";
    };
    if (chars.length) show();
  };
  $("mtFilter").oninput = drawList;
  $("mtNew").onclick = () => open({ name: "", builtin: false, edited: false, category: cats[0].id, nsfw: false, look: "", framing: "", tags: "", framing_tags: "", note: "", sets: [], story: null }, true);
  $("mtCats").onclick = editCats;
  drawList();
  const first = startName && list.find((m) => m.name === startName);
  if (first) open(first);
}

// The Prompt window of one picture: the exact words it was drawn from (or, for older pictures, what would be sent now), editable, with "Draw again from this".
async function showPicturePrompt(slug, mood, url, back) {
  modal(`<h2>📝 Prompt: ${esc(mood)}</h2><div id="ppBody"><p class="hint">Loading…</p></div>`);
  const ch = story.cast.find((c) => c.slug === slug);
  let r;
  try { r = await api(`/stories/${story.id}/cast/${slug}/picture-prompt?url=${encodeURIComponent(url)}`); } catch (e) { $("ppBody").innerHTML = `<p class="hint">${esc(e.message)}</p><div class="actions"><button id="mClose">Close</button></div>`; $("mClose").onclick = () => back ? back() : closeModal(); return; }
  $("ppBody").innerHTML = `
    <div class="mtpic"><img src="${thumbUrl(url, 260)}" alt=""><div class="hint">${r.saved ? `The exact prompt this picture was drawn from${r.edited ? " (you edited it before)" : ""}.` : "This picture was drawn before the app kept its prompt. This is what would be sent for it now."}<br>Maker: ${esc(r.maker || "?")} · ${r.style === "tags" ? "tag-style prompt" : "sentence prompt"}${r.refs != null ? " · " + r.refs + " reference picture(s)" : ""}</div></div>
    <label>Prompt</label><textarea class="short mono" id="ppPrompt" rows="12">${esc(r.prompt)}</textarea>
    <label>Negative prompt</label><textarea class="short mono" id="ppNeg" rows="2">${esc(r.negative || "")}</textarea>
    <div class="hint">Change any words, then draw again: a new picture is made from exactly this text (the character's saved pictures are still sent as references, if the maker takes them). The current picture is kept as an earlier version. Some makers ignore the negative prompt.</div>
    <div class="actions"><span class="hint" id="ppMsg"></span><span style="flex:1"></span><button id="ppReset">Undo my edits</button><button id="mClose">Close</button><button class="primary" id="ppGo">Draw again from this</button></div>`;
  const orig = r.prompt, origNeg = r.negative || "";
  $("mClose").onclick = () => back ? back() : closeModal();
  $("ppReset").onclick = () => { $("ppPrompt").value = orig; $("ppNeg").value = origNeg; };
  $("ppGo").onclick = async () => {
    const b = $("ppGo"); b.disabled = true; b.textContent = "Drawing…"; $("ppMsg").textContent = "";
    try { story = await api(`/stories/${story.id}/cast/${slug}/generate`, { method: "POST", body: { mood: r.mood || mood, prompt: $("ppPrompt").value, negative: $("ppNeg").value } }); renderAll(); if (back) back(); else closeModal(); }
    catch (e) { $("ppMsg").textContent = e.message; b.disabled = false; b.textContent = "Draw again from this"; }
  };
}
