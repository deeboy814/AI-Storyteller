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
    <div class="actions"><button id="mBackups">Backups…</button><span style="flex:1"></span><button id="mClose">Close</button><button class="primary" id="mSave">Save</button></div>`);
  $("mClose").onclick = closeModal;
  $("mBackups").onclick = () => showBackups(story.id);
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
