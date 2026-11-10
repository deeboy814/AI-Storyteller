// Story beats: the story's own schedule of key events. The app keeps them and sends each to the narrator, hidden from you, when the side panel (or the move count) says it is due.
// Everything is hidden behind a spoiler blur until you choose to look.
const beatShown = new Set();
let beatAll = false, beatPoll = null;
const BEAT_STATUS = { waiting: "Waiting", active: "Active now", done: "Done", off: "Skipped", notrigger: "Needs a trigger" };

async function showBeats() {
  if (!story) return;
  clearInterval(beatPoll);
  const sid = story.id;
  let v; try { v = await api(`/stories/${sid}/beats`); } catch (e) { alert(e.message); return; }
  const showing = (id) => beatAll || beatShown.has(id);
  const card = (it) => `<div class="beat s-${it.status}" data-id="${esc(it.id)}">
      <div class="bhead"><span class="bwhen">${esc(it.when)}</span><span class="bstat">${BEAT_STATUS[it.status] || ""}${it.status === "off" && it.why ? " · " + esc(it.why) : ""}</span><span style="flex:1"></span>
        <button class="mini" data-a="eye" title="Show or hide this event">${showing(it.id) ? "🙈" : "👁"}</button><button class="mini" data-a="edit" title="Edit this event">✏️</button><button class="mini" data-a="del" title="Delete this event">🗑️</button></div>
      <div class="spoil ${showing(it.id) ? "on" : ""}" data-a="eye" title="Tap to show or hide"><b>${esc(it.title)}</b><div>${esc(it.text)}</div></div>
      <div class="bacts">${it.status === "off" ? `<button class="mini" data-a="reset">Switch on</button>` : it.status === "waiting" || it.status === "notrigger" ? `<button class="mini" data-a="skip">Skip it</button>` : `<button class="mini" data-a="reset">Send again when due</button>`}
        ${it.source === "player" ? `<small class="hint">yours</small>` : it.edited ? `<small class="hint">edited</small>` : ""}</div></div>`;
  modal(`<h2>📅 Story beats</h2>
    <p class="hint">The key events this story schedules, read from its prompt (or added by you). When the side panel's value (for example the <b>Day</b>) or your move count reaches an event's trigger, the app sends it to the narrator <b>invisibly with your next move</b>, so the story stays on plan without you typing it. Nothing is added to the story text. They are hidden here as spoilers until you choose to look.</p>
    <div class="grid2"><div><label class="chk"><input type="checkbox" id="bOn" ${v.on ? "checked" : ""}> Use story beats</label>
      <label class="chk" title="Also tell the narrator, hidden, what comes next, so the story leads toward it and does not drift"><input type="checkbox" id="bLook" ${v.lookahead ? "checked" : ""}> Tell the narrator what is coming up (hidden)</label></div>
      <div><label>Keep reminding the narrator for <input id="bHold" type="number" min="1" max="12" value="${v.hold}" style="width:64px"> moves after an event starts</label></div></div>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="bRead" title="Ask the AI to read the story prompt again for scheduled events. New ones are added; ones you edited and ones you made are never touched.">🔍 Read the story prompt</button><button id="bAll">${beatAll ? "🙈 Hide all" : "👁 Show all (spoilers)"}</button><button id="bAdd">＋ Add an event</button><button id="bLore" title="World facts sent to the narrator only when their trigger words come up">📚 Lore…</button><span class="hint" id="bMsg">${v.running ? "Reading the story prompt…" : ""}</span></div>
    ${v.error ? `<p class="hint" style="color:var(--bad,#e66)">Could not read the prompt: ${esc(v.error)}</p>` : ""}
    <div id="bList">${v.items.map(card).join("") || `<p class="hint">${v.running ? "Reading the story prompt for key events…" : v.extracted ? "No scheduled events were found in the story prompt. Add your own with ＋." : "Not read yet. Press 🔍 to read the story prompt."}</p>`}</div>
    <div class="actions"><span style="flex:1"></span><button class="primary" id="bClose">Close</button></div>`);
  $("bClose").onclick = closeModal;
  if (v.running) beatPoll = setInterval(() => { if (!$("bList")) return clearInterval(beatPoll); api(`/stories/${sid}/beats`).then((x) => { if (!x.running) { clearInterval(beatPoll); showBeats(); } }).catch(() => clearInterval(beatPoll)); }, 2500);
  const save = async (body) => { try { await api(`/stories/${sid}/beats`, { method: "PUT", body }); story.beats = undefined; } catch (e) { alert(e.message); } };
  $("bOn").onchange = () => save({ on: $("bOn").checked });
  $("bLook").onchange = () => save({ lookahead: $("bLook").checked });
  $("bHold").onchange = () => save({ hold: +$("bHold").value || 3 });
  $("bAll").onclick = () => { beatAll = !beatAll; showBeats(); };
  $("bRead").onclick = async (e) => {
    e.target.disabled = true; $("bMsg").textContent = "Reading the story prompt…";
    try { const r = await api(`/stories/${sid}/beats/read`, { method: "POST" }); beatAll = beatAll; showBeats(); setTimeout(() => { if ($("bMsg")) $("bMsg").textContent = `${r.added} added, ${r.updated} updated.`; }, 50); }
    catch (err) { $("bMsg").textContent = err.message; e.target.disabled = false; }
  };
  $("bAdd").onclick = () => beatForm(v, null);
  $("bLore").onclick = () => showLore(() => showBeats());
  $("bList").onclick = async (e) => {
    const b = e.target.closest("[data-a]"), c = e.target.closest(".beat"); if (!b || !c) return;
    const id = c.dataset.id, it = v.items.find((x) => x.id === id), a = b.dataset.a;
    if (a === "eye") { beatShown.has(id) ? beatShown.delete(id) : beatShown.add(id); return showBeats(); }
    if (a === "edit") return beatForm(v, it);
    try {
      if (a === "del") { if (!confirm("Delete this event?")) return; await api(`/stories/${sid}/beats/items/${id}`, { method: "DELETE" }); }
      else if (a === "skip") await api(`/stories/${sid}/beats/items/${id}`, { method: "PUT", body: { off: true } });
      else if (a === "reset") await api(`/stories/${sid}/beats/items/${id}/reset`, { method: "POST" });
      showBeats();
    } catch (err) { alert(err.message); }
  };
}

// add or edit one event
function beatForm(v, it) {
  const sid = story.id, f = it ? it.field : (v.fields.length ? v.fields[0].id : "next"), kind = !f ? "none" : f === "next" ? "next" : f === "turn" ? "turn" : "field";
  const first = (v.fields[0] || {}).id || "";
  modal(`<h2>${it ? "Edit event" : "Add an event"}</h2>
    <label>Title (short)</label><input id="eT" value="${esc(it ? it.title : "")}" maxlength="90" placeholder="e.g. The bridge is raised">
    <label>What happens (what the narrator should make happen, and what it changes)</label><textarea id="eX" rows="4" placeholder="e.g. A storm cuts the village off. Nobody can leave until the pass is cleared.">${esc(it ? it.text : "")}</textarea>
    <label>When should it happen?</label>
    <select id="eK"><option value="field" ${kind === "field" ? "selected" : ""}>When a side-panel value reaches…</option><option value="turn" ${kind === "turn" ? "selected" : ""}>At a move number…</option><option value="next" ${kind === "next" ? "selected" : ""}>On my next move</option><option value="none" ${kind === "none" ? "selected" : ""}>No trigger yet (I will decide later)</option></select>
    <div id="eField" class="grid2"><div><label>Side-panel value</label><select id="eF">${v.fields.map((x) => `<option value="${esc(x.id)}" ${x.id === (kind === "field" ? f : first) ? "selected" : ""}>${esc(x.label)}${x.now != null && x.now !== "" ? " (now: " + esc(x.now) + ")" : ""}</option>`).join("") || '<option value="">(this story has no usable side-panel values)</option>'}</select></div>
      <div><label>Comparison</label><select id="eO"><option value=">=" ${it && it.op !== "=" || !it ? "selected" : ""}>reaches or passes (number)</option><option value="=" ${it && it.op === "=" ? "selected" : ""}>is / contains (text)</option></select></div>
      <div><label>Value</label><input id="eV" value="${esc(it && kind !== "next" ? it.value : "")}" placeholder="e.g. 7"></div></div>
    <div id="eTurn" class="hidden"><label>Move number (the Nth move you send)</label><input id="eN" type="number" min="1" value="${esc(it && kind === "turn" ? it.value : v.next_move + 1)}"></div>
    <div class="actions"><button id="eCancel">Cancel</button><span style="flex:1"></span><button class="primary" id="eSave">Save</button></div><div class="hint" id="eMsg"></div>`);
  const sync = () => { const k = $("eK").value; $("eField").classList.toggle("hidden", k !== "field"); $("eTurn").classList.toggle("hidden", k !== "turn"); };
  $("eK").onchange = sync; sync();
  $("eCancel").onclick = () => showBeats();
  $("eSave").onclick = async () => {
    const k = $("eK").value, body = { title: $("eT").value, text: $("eX").value };
    if (k === "field") Object.assign(body, { field: $("eF").value, op: $("eO").value, value: $("eV").value });
    else if (k === "turn") Object.assign(body, { field: "turn", value: $("eN").value });
    else Object.assign(body, { field: k === "next" ? "next" : "", value: "" });
    if (k === "field" && (!body.field || !String(body.value).trim())) { $("eMsg").textContent = "Pick a side-panel value and say where it triggers."; return; }
    try { await api(`/stories/${sid}/beats/items${it ? "/" + it.id : ""}`, { method: it ? "PUT" : "POST", body }); showBeats(); } catch (e) { $("eMsg").textContent = e.message; }
  };
}
