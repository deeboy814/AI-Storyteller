// Report a bug: the app builds ONE .zip (how far the game got, the problems it can find itself, recent errors, settings) with personal data removed. You read what is in it,
// then attach it to your report together with your own explanation. Nothing is sent anywhere by the app.
async function showBugReport(back) {
  const open = story ? story.id : "";
  let list = [];
  try { list = await api("/stories"); } catch (e) { /* Home list unavailable */ }
  const keys = (() => { try { return Object.keys(localStorage).slice(0, 30); } catch (e) { return []; } })();
  modal(`<h2>🐞 Report a bug</h2>
    <p class="hint">The app builds one <b>.zip file</b> with how far your game got, the problems it can find by itself, the latest errors and your settings. <b>Personal data is removed</b>: API keys, e-mail addresses, phone numbers, links, IP addresses, file paths, your computer's user name, and (by default) the player's and characters' names. You read what is in it first, then attach it to your report together with your own explanation. <b>Nothing is sent anywhere by the app.</b></p>
    <label>Which game went wrong?</label>
    <select id="bgStory"><option value="">None: the problem is with the app itself</option>${list.map((s) => `<option value="${esc(s.id)}" ${s.id === open ? "selected" : ""}>${esc(s.title)}</option>`).join("")}</select>
    <label>What went wrong? <small class="hint">(what you did, what you expected, what happened instead; it is written into the report)</small></label>
    <textarea class="short" id="bgDesc" rows="5" placeholder="e.g. I pressed Retry on the last reply and the side panel kept the old values. I expected them to go back to how they were before that reply."></textarea>
    <label class="chk"><input type="checkbox" id="bgText" checked> Include the story text (the story prompt, memory and the recent replies). <small class="hint">Needed for most bugs. Untick if the story is private and the bug is about the app, and the report keeps only structure and problems.</small></label>
    <div class="grid2" style="grid-template-columns:1fr 1fr"><div><label>How many recent turns</label><select id="bgTurns"><option value="6">6</option><option value="10" selected>10</option><option value="20">20</option><option value="40">40</option></select></div>
      <div><label class="chk" style="margin-top:28px"><input type="checkbox" id="bgNames" checked> Replace names with Player / Character A, B…</label></div></div>
    <label class="chk"><input type="checkbox" id="bgLogs" checked> Include the last lines of the server log (personal data removed)</label>
    <div class="hint" id="bgMsg"></div>
    <div id="bgOut"></div>
    <div class="actions"><button id="bgBack">Close</button><span style="flex:1"></span><button class="primary" id="bgGo">Create the report</button></div>`);
  $("bgBack").onclick = () => (back ? back() : closeModal());
  $("bgGo").onclick = async (e) => {
    e.target.disabled = true; $("bgMsg").textContent = "Collecting and cleaning…"; $("bgOut").innerHTML = "";
    try {
      const client = { ua: navigator.userAgent, viewport: [innerWidth, innerHeight], errors: (window.__bugErr || []).slice(-60), page: location.pathname, story_open: !!story, storage_keys: keys };
      const r = await api("/bug-report/preview", { method: "POST", body: { story_id: $("bgStory").value || null, description: $("bgDesc").value, include_text: $("bgText").checked, last_turns: +$("bgTurns").value, hide_names: $("bgNames").checked, include_logs: $("bgLogs").checked, client } });
      const lv = { error: "🔴", warn: "🟠", info: "ℹ️" }, kb = (n) => n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB";
      $("bgMsg").textContent = "";
      $("bgOut").innerHTML = `<h3>Ready: ${esc(r.filename)} (${kb(r.size)})</h3>
        <div class="trow"><b>Problems the app found by itself</b>${(r.issues || []).length ? `<ul style="margin:6px 0 0 18px">${r.issues.map((i) => `<li>${lv[i.level] || ""} <b>${esc(i.where)}</b>: ${esc(i.message)}</li>`).join("")}</ul>` : '<div class="hint">None. The problem may be about something the app cannot check, such as the content of a reply.</div>'}</div>
        <div class="trow"><b>Personal data removed</b><div class="hint">${Object.keys(r.removed || {}).length ? Object.entries(r.removed).map(([k, v]) => `${v} ${esc(k)}`).join(", ") : "Nothing personal was found."}${(r.blocked_files || []).length ? ` · left out because it could not be made safe: ${r.blocked_files.map(esc).join(", ")}` : ""}</div></div>
        <div class="trow"><b>What is in the file</b><div class="hint">${r.files.map((f) => `${esc(f.name)} (${kb(f.size)})`).join(" · ")}</div>
          <details><summary>Read the summary (REPORT.md)</summary><pre style="white-space:pre-wrap;max-height:260px;overflow:auto;font-size:12px">${esc(r.report_md)}</pre></details></div>
        <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><a class="btnlike primary" id="bgDl" href="/api/bug-report/${esc(r.id)}/download" download="${esc(r.filename)}">⬇ Download the .zip</a>
          <a class="btnlike" href="https://github.com/deeboy814/AI-Storyteller/issues/new" target="_blank" rel="noopener">Open the issue page on GitHub</a></div>
        <p class="hint">On the issue page, describe the problem in your own words and attach the .zip (drag it into the box). Nothing was uploaded by the app.</p>`;
    } catch (err) { $("bgMsg").textContent = err.message; }
    e.target.disabled = false;
  };
}
