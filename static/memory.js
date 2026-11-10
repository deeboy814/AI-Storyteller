// Memory editor (summary + pinned facts + rebuild) and backups.
async function showMemory() {
  if (!story) return;
  const s = await api("/stories/" + story.id); story = s;
  const done = Math.floor((s.summary_upto || 0) / 2), total = Math.floor(s.turns.length / 2);
  const rec = (config.default_models || {}).recaps || config.active || {};
  const modelOpts = models.map((m) => `<option value="${esc(m.provider)}|${esc(m.model)}" ${rec.provider === m.provider && rec.model === m.model ? "selected" : ""}>${esc(m.provider_name)}: ${esc(m.model)}</option>`).join("");
  modal(`<h2>Memory</h2>
    <p class="hint">The narrator reads this every turn along with the most recent turns. Older events live only here, so errors in it get repeated. It covers turns up to <b>${done}</b> of ${total} that this app holds.</p>
    <label>Pinned facts: one per line. They override everything else (use them to correct something the narrator keeps getting wrong)</label>
    <textarea class="short" id="mPins" style="min-height:80px" placeholder="e.g. Mara is left-handed.">${esc((s.pinned_facts || []).join("\n"))}</textarea>
    <label>Summary of older events (edit freely)</label>
    <textarea class="short" id="mSum" style="min-height:260px">${esc(s.summary || "")}</textarea>
    <details><summary>Rebuild the summary from the story's turns</summary>
      <div class="grid2"><div><label>Summarize turns up to (player turn number; blank = all but the recent ones)</label><input id="mUpto" type="number" min="1" max="${total}" placeholder="${Math.max(1, total - 5)}"></div>
      <div><label>Which AI</label><select id="mModel">${modelOpts}</select></div><div></div></div>
      <label class="chk"><input type="checkbox" id="mKeep" checked> Start from the current summary (leave ticked for imported stories: their early history exists only in it)</label>
      <div class="actions" style="justify-content:flex-start"><button id="mRebuild">Rebuild</button><span class="hint" id="mProg"></span></div>
      <p class="hint">A backup of the story is saved first.</p></details>
    <div class="actions"><button id="mBackups">Backups…</button><button id="mLore" title="Facts about your world, sent to the narrator only when their trigger words come up">📚 Lore…</button><button id="mCont" title="Read the whole story for contradictions and repair them">🔎 Continuity check…</button><span style="flex:1"></span><button id="mClose">Close</button><button class="primary" id="mSave">Save</button></div>`);
  $("mClose").onclick = closeModal;
  $("mBackups").onclick = () => showBackups(story.id);
  $("mCont").onclick = () => showContinuity();
  $("mLore").onclick = () => showLore(() => showMemory());
  $("mSave").onclick = async () => {
    story = await api(`/stories/${story.id}/memory`, { method: "PUT", body: { summary: $("mSum").value, pinned_facts: $("mPins").value.split("\n") } });
    closeModal(); renderAll();
  };
  $("mRebuild").onclick = async (e) => {
    const [provider, model] = $("mModel").value.split("|");
    e.target.disabled = true;
    try {
      const job = await api(`/stories/${story.id}/memory/rebuild`, { method: "POST", body: { upto_turn: +$("mUpto").value || null, keep_existing: $("mKeep").checked, provider, model } });
      const j = await watchJob(job.id, (x) => { $("mProg").textContent = x.current ? `${x.current}… (${x.done}/${x.total})` : ""; });
      if (j.status !== "done") throw new Error(j.error || "failed");
      showMemory();
    } catch (err) { $("mProg").textContent = err.message; e.target.disabled = false; }
  };
}

async function showBackups(sid) {
  const list = await api(`/stories/${sid}/snapshots`);
  modal(`<h2>Backups</h2>
    <p class="hint">The app saves a copy of this story before every summarize, memory edit, restore or delete, and every 5 turns (the newest 40 are kept). Restoring replaces the story with that copy; the current version is backed up first.</p>
    <div class="snaplist">${list.map((b) => `<div class="storyrow"><div><b>${new Date(b.when * 1000).toLocaleString()}</b><br><small>${esc(b.reason)} · ${b.turns} turns</small></div><button data-r="${esc(b.name)}">Restore</button></div>`).join("") || "<p class='hint'>No backups yet.</p>"}</div>
    <div class="actions"><button id="bNow">Back up now</button><span style="flex:1"></span><button id="bClose">Close</button></div>`);
  $("bClose").onclick = closeModal;
  $("bNow").onclick = async () => { await api(`/stories/${sid}/snapshots`, { method: "POST" }); showBackups(sid); };
  document.querySelectorAll("[data-r]").forEach((b) => b.onclick = async () => {
    if (!confirm("Replace the story with this backup?")) return;
    const s = await api(`/stories/${sid}/snapshots/restore`, { method: "POST", body: { name: b.dataset.r } });
    if (story && story.id === sid) story = s;
    closeModal(); renderAll(); if (typeof showHome === "function" && !$("home").classList.contains("hidden")) showHome();
  });
}


// Continuity check: the AI reads every turn, lists real contradictions with a small repair for each; you tick which repairs to make, then it checks again.
async function showContinuity(note = "") {
  if (!story) return;
  const sid = story.id, turnNo = (i) => Math.floor(i / 2) + 1;
  let est; try { est = await api(`/stories/${sid}/continuity/estimate`); } catch (e) { alert(e.message); return; }
  modal(`<h2>🔎 Continuity check</h2>
    <p class="hint">The AI reads <b>every turn</b> of this story (plus its rules, character cards, pinned facts and memory) and lists real contradictions: names, looks, ages, places, the timeline, items, injuries, deaths, who knows what, and broken rules. Deliberate changes are kept. Each problem comes with the <b>smallest repair</b>; you choose which to make. Nothing changes until you press the repair button, a backup is saved first, and every edited turn keeps its old wording in its edit history (✏️).</p>
    <div class="grid2"><div><label>Which AI</label><select id="cRole"><option value="story">The narrator's model</option><option value="recaps">The recaps & helpers model (cheaper)</option></select></div><div></div></div>
    <p class="hint" id="cEst">${est.turns} turns, about ${est.tokens.toLocaleString()} tokens to read${est.parts > 1 ? ` in ${est.parts} parts` : ""} (${esc(est.model)}). A long story costs real money on a paid service.${est.parts > 1 ? " Because it is read in parts, a contradiction between far-apart parts can be missed." : ""}</p>
    <div id="cOut">${note ? `<p class="hint">${esc(note)}</p>` : ""}</div>
    <div class="actions"><button id="cClose">Close</button><span style="flex:1"></span><button class="primary" id="cRun">Run the check</button></div>`);
  $("cClose").onclick = closeModal;
  $("cRole").onchange = async () => { try { const e = await api(`/stories/${sid}/continuity/estimate?role=${$("cRole").value}`); $("cEst").textContent = `${e.turns} turns, about ${e.tokens.toLocaleString()} tokens (${e.model}).`; } catch (x) {} };
  const run = async (again) => {
    $("cRun").disabled = true; const out = $("cOut");
    out.innerHTML = `<p class="hint" id="cProg">Starting…</p>`;
    try {
      const job = await api(`/stories/${sid}/continuity/audit`, { method: "POST", body: { role: $("cRole").value } });
      const j = await watchJob(job.id, (x) => { if ($("cProg")) $("cProg").textContent = x.current ? `${x.current}…` : "Working…"; });
      if (j.status !== "done") throw new Error(j.error || "failed");
      show(j.result, again);
    } catch (e) { out.innerHTML = `<p class="hint" style="color:var(--bad,#e66)">${esc(e.message)}</p>`; }
    $("cRun").disabled = false; $("cRun").textContent = "Run the check again";
  };
  const show = (r, again) => {
    const out = $("cOut"), list = r.issues || [];
    if (!list.length) { out.innerHTML = `<p><b>✔ No contradictions found.</b>${again ? " The repairs held." : ""}</p>${r.notes ? `<p class="hint">${esc(r.notes)}</p>` : ""}`; return; }
    const fixable = list.filter((x) => x.fix).length;
    out.innerHTML = `<p><b>${list.length} problem${list.length > 1 ? "s" : ""} found</b>${again ? " (after the repairs)" : ""}: ${fixable} with a ready repair, ${list.length - fixable} for you to decide.</p>`
      + list.map((x) => `<div class="trow" style="margin:6px 0"><label class="chk" style="align-items:flex-start"><input type="checkbox" data-ci="${x.id}" ${x.fix ? "checked" : "disabled"}> <span><b>${esc(x.kind)}</b> · ${x.severity === "major" ? "major" : "minor"} · ${x.turns.length ? "turn " + x.turns.map(turnNo).join(", ") : "memory"}<br>${esc(x.problem)}</span></label>`
        + (x.fix ? `<div class="hint" style="margin:4px 0 0 24px">${x.fix.target === "summary" ? "In the memory summary" : "Turn " + turnNo(x.fix.turn)}: <s>${esc(x.fix.find)}</s> → <b>${esc(x.fix.replace)}</b></div>` : `<div class="hint" style="margin:4px 0 0 24px">No safe automatic repair: change it yourself (✏️ on the turn, a pinned fact, or the character card).</div>`)
        + (x.canon ? `<div style="margin:4px 0 0 24px"><label class="chk"><input type="checkbox" data-cc="${x.id}" checked> Pin as a fact: <input data-ct2="${x.id}" value="${esc(x.canon)}" style="width:60%"></label></div>` : "") + `</div>`).join("")
      + `<div class="actions" style="justify-content:flex-start"><button class="primary" id="cFix" ${fixable ? "" : "disabled"}>Make the ticked repairs and check again</button><button id="cFixOnly" ${fixable ? "" : "disabled"}>Repair only</button></div><div class="hint" id="cMsg"></div>`
      + (r.notes ? `<p class="hint">${esc(r.notes)}</p>` : "");
    const apply = async (recheck) => {
      const fixes = [], canon = [];
      list.forEach((x) => {
        const on = document.querySelector(`[data-ci="${x.id}"]`); if (on && on.checked && x.fix) fixes.push(x.fix);
        const c = document.querySelector(`[data-cc="${x.id}"]`); if (c && c.checked) canon.push(document.querySelector(`[data-ct2="${x.id}"]`).value);
      });
      if (!fixes.length && !canon.length) { $("cMsg").textContent = "Nothing is ticked."; return; }
      $("cFix").disabled = $("cFixOnly").disabled = true;
      try {
        const res = await api(`/stories/${sid}/continuity/apply`, { method: "POST", body: { fixes, canon } });
        story = res.story; renderAll();
        const msg = `${res.applied} repair${res.applied === 1 ? "" : "s"} made` + (res.skipped.length ? `, ${res.skipped.length} skipped (the text had changed)` : "") + ".";
        if (recheck) { $("cOut").innerHTML = `<p class="hint">${esc(msg)} Checking again…</p><p class="hint" id="cProg"></p>`; await run(true); } else $("cMsg").textContent = msg + " Run the check again to confirm.";
      } catch (e) { $("cMsg").textContent = e.message; $("cFix").disabled = $("cFixOnly").disabled = false; }
    };
    $("cFix").onclick = () => apply(true); $("cFixOnly").onclick = () => apply(false);
  };
  $("cRun").onclick = () => run(false);
}
