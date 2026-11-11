// Lore: facts about the world that you (or the story's author) keep in the app. An entry is sent to the narrator, hidden, only when one of its trigger words appears in your move or
// the last couple of turns (or always, if marked). Nothing is added to the story text, and no AI is involved. Entries travel in story packages.
async function showLore(back) {
  if (!story) return;
  const sid = story.id; let lb;
  try { lb = await api(`/stories/${sid}/lore`); } catch (e) { alert(e.message); return; }
  let items = JSON.parse(JSON.stringify(lb.items || [])), on = lb.on !== false, filter = "";
  const editing = new Set();          // entries shown as the editing form; the rest are read-only cards
  const sync = () => {
    if ($("lrOn")) on = $("lrOn").checked;
    document.querySelectorAll(".loreitem.editing").forEach((c) => {
      const it = items[+c.dataset.i]; if (!it) return; const g = (k) => c.querySelector(`[data-k="${k}"]`);
      it.title = g("title").value; it.keys = g("keys").value; it.text = g("text").value; it.always = g("always").checked; it.on = g("on").checked; it.priority = +g("priority").value;
    });
  };
  const keysOf = (it) => (Array.isArray(it.keys) ? it.keys : String(it.keys || "").split(/[,;\n]/)).map((k) => k.trim()).filter(Boolean);
  const viewCard = (it, i) => `<div class="loreitem lorecard${it.on === false ? " off" : ""}" data-i="${i}">
      <div class="lrow"><b class="lrtitle">${esc(it.title || "(untitled)")}</b>${it.always ? '<span class="lrbadge">always sent</span>' : ""}${it.on === false ? '<span class="lrbadge off">off</span>' : ""}<span class="lrbadge pri" title="Importance when several entries compete for room">importance ${it.priority ?? 5}</span><span style="flex:1"></span><button class="lrEdit" title="Edit this entry">✎ Edit</button><button class="x lrDel" title="Delete this entry">🗑️</button></div>
      ${it.always ? "" : `<div class="lrkeys">${keysOf(it).map((k) => `<span class="lrkey">${esc(k)}</span>`).join("") || '<span class="hint">no trigger words</span>'}</div>`}
      <div class="lrtext">${esc(it.text || "")}</div></div>`;
  const editCard = (it, i) => `<div class="loreitem editing${it.on === false ? " off" : ""}" data-i="${i}">
        <div class="lrow"><input data-k="title" maxlength="90" placeholder="Title, e.g. The Line" value="${esc(it.title || "")}" style="flex:1;min-width:160px"><button class="lrDone" title="Back to the card (press Save to keep your changes)">Done</button><button class="x lrDel" title="Delete this entry">🗑️</button></div>
        <label>Trigger words <small class="hint">(separated by commas; whole words, any capitalisation)</small></label><input data-k="keys" value="${esc(Array.isArray(it.keys) ? it.keys.join(", ") : it.keys || "")}" placeholder="e.g. the Line, boundary wall">
        <label>What the narrator should know</label><textarea class="short" data-k="text" rows="5" maxlength="2000">${esc(it.text || "")}</textarea>
        <div class="lrow"><label class="chk"><input type="checkbox" data-k="always" ${it.always ? "checked" : ""}> Always send</label><label class="chk"><input type="checkbox" data-k="on" ${it.on !== false ? "checked" : ""}> On</label>
          <label>Importance <select data-k="priority">${[9, 8, 7, 6, 5, 4, 3, 2, 1, 0].map((n) => `<option value="${n}" ${(it.priority ?? 5) === n ? "selected" : ""}>${n}${n === 9 ? " (first)" : n === 5 ? " (normal)" : n === 0 ? " (last)" : ""}</option>`).join("")}</select></label></div></div>`;
  const draw = () => {
    modal(`<h2>📚 Lore</h2>
      <p class="hint">Facts about your world: a place, a faction, a rule, a character's history. An entry is sent to the narrator, <b>hidden</b>, only when one of its <b>trigger words</b> appears in your move or the last couple of turns (or always, if you tick <i>Always send</i>), so the narrator stays consistent without you retyping it and without filling the prompt. Nothing is added to the story text. They are saved with the story and included when you export a story package.</p>
      <label class="chk"><input type="checkbox" id="lrOn" ${on ? "checked" : ""}> Use lore entries</label>
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="lrAdd">＋ Add an entry</button><input id="lrTest" placeholder="Test a sentence: which entries would it trigger?" style="flex:1;min-width:200px"><button id="lrPrev">Test</button></div>
      <div class="hint" id="lrOut"></div>
      <input id="lrFind" placeholder="Search entries (title, trigger words or text)" value="${esc(filter)}" style="width:100%;margin-top:6px">
      <div id="lrList">${items.map((it, i) => ((it.title || "") + " " + (Array.isArray(it.keys) ? it.keys.join(" ") : it.keys || "") + " " + (it.text || "")).toLowerCase().includes(filter.toLowerCase()) || editing.has(i) ? (editing.has(i) ? editCard(it, i) : viewCard(it, i)) : "").join("") || '<p class="hint">No entries yet. Press ＋ to add one.</p>'}</div>
      <div class="actions"><button id="lrClose">Close</button><span style="flex:1"></span><button class="primary" id="lrSave">Save</button></div><div class="hint" id="lrMsg"></div>`);
    $("lrOn").onchange = () => { on = $("lrOn").checked; };
    $("lrFind").oninput = () => { sync(); filter = $("lrFind").value; const pos = $("lrFind").selectionStart; draw(); const f = $("lrFind"); f.focus(); f.setSelectionRange(pos, pos); };
    document.querySelectorAll(".lrEdit").forEach((b) => b.onclick = () => { sync(); editing.add(+b.closest(".loreitem").dataset.i); draw(); });
    document.querySelectorAll(".lrDone").forEach((b) => b.onclick = () => { sync(); editing.delete(+b.closest(".loreitem").dataset.i); draw(); });
    $("lrAdd").onclick = () => { sync(); if (items.length >= 200) return; items.push({ title: "", keys: "", text: "", always: false, on: true, priority: 5 }); editing.add(items.length - 1); filter = ""; draw(); const t = document.querySelectorAll('.loreitem [data-k="title"]'); if (t.length) t[t.length - 1].focus(); };
    document.querySelectorAll(".lrDel").forEach((b) => b.onclick = () => { sync(); const i = +b.closest(".loreitem").dataset.i; if (!items[i].title && !items[i].text || confirm(`Delete “${items[i].title || "this entry"}”?`)) { items.splice(i, 1); const keep = [...editing].filter((x) => x !== i).map((x) => (x > i ? x - 1 : x)); editing.clear(); keep.forEach((x) => editing.add(x)); draw(); } });
    const save = async () => { sync(); const r = await api(`/stories/${sid}/lore`, { method: "PUT", body: { on, items } }); items = r.items; on = r.on; story.lore = r; editing.clear(); return r; };
    $("lrSave").onclick = async () => { try { await save(); $("lrMsg").textContent = "✔ Saved."; draw(); $("lrMsg").textContent = "✔ Saved."; } catch (e) { $("lrMsg").textContent = e.message; } };
    $("lrPrev").onclick = async () => {
      try { await save(); const r = await api(`/stories/${sid}/lore/preview`, { method: "POST", body: { text: $("lrTest").value } }); draw(); $("lrOut").textContent = r.matched.length ? "Would send: " + r.matched.join(", ") : "No entry would be sent for that."; }
      catch (e) { $("lrOut").textContent = e.message; }
    };
    $("lrClose").onclick = () => { if (back) back(); else closeModal(); };
  };
  draw();
}
