// Journal (a readable timeline made from what the app already keeps: key events, finished quests, bookmarks, the memory summary; no AI call)
// and the "Previously…" card shown when a story is reopened after a break (one small helper-AI call, only when the player asks for it).
const PREV_AFTER_HOURS = 6;
function agoText(h) {
  if (h < 24) return `${Math.round(h)} hours ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "yesterday" : d < 14 ? `${d} days ago` : d < 60 ? `${Math.round(d / 7)} weeks ago` : `${Math.round(d / 30)} months ago`;
}
async function showJournal() {
  if (!story) return;
  modal(`<h2>📖 Journal</h2><div id="jrBody"><p class="hint">Loading…</p></div><div class="actions"><span style="flex:1"></span><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  let j;
  try { j = await api(`/stories/${story.id}/journal`); } catch (e) { $("jrBody").innerHTML = `<p class="hint">${esc(e.message)}</p>`; return; }
  const icon = { event: "•", quest: "🏁", bookmark: "★" };
  const who = [...new Set(j.timeline.filter((r) => r.who).map((r) => r.who))].sort();
  const rows = (f) => j.timeline.filter((r) => !f || r.who === f || (f === "__q" && r.kind === "quest") || (f === "__b" && r.kind === "bookmark"));
  const draw = (f) => {
    const list = rows(f);
    $("jrList").innerHTML = list.length ? list.map((r) => `<div class="jrrow jr-${r.kind}"><span class="jrwhen">${esc(r.when || "")}</span><span class="jrtext">${icon[r.kind] || "•"} ${r.who ? `<b>${esc(r.who)}:</b> ` : ""}${esc(r.text)}</span>${r.turn != null && r.turn < story.turns.length ? `<a href="#" class="jrgo" data-i="${r.turn}" title="Show this moment in the story">↪</a>` : ""}</div>`).join("")
      : `<p class="hint">Nothing here yet. Key events are logged as the story is played; finished quests and bookmarked moments (☆ on a reply) appear here too.</p>`;
    $("jrList").querySelectorAll(".jrgo").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); closeModal(); const el = document.querySelector(`#narrative .turn[data-i="${a.dataset.i}"]`); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); });
  };
  $("jrBody").innerHTML = `
    <h3>Previously…</h3>
    <div id="jrRecap" class="jrrecap">${j.recap ? esc(j.recap) : `<span class="hint">A short reminder of where the story stands, written by the helper AI (one small request).</span>`}</div>
    <div class="actions" style="justify-content:flex-start"><button id="jrMake">${j.recap ? "Write it again" : "Write a recap"}</button></div>
    ${j.summary ? `<details><summary><b>The story so far</b> <span class="hint">(the story's memory)</span></summary><div class="jrsum">${esc(j.summary)}${j.events ? "\n\n" + esc(j.events) : ""}</div></details>` : ""}
    <h3>Timeline <span class="hint">${j.timeline.length} entries</span></h3>
    <select id="jrFilter"><option value="">Everything</option><option value="__q">Quests</option><option value="__b">Bookmarks</option>${who.map((n) => `<option value="${esc(n)}">${esc(n)}</option>`).join("")}</select>
    <div id="jrList" class="jrlist"></div>`;
  draw("");
  $("jrFilter").onchange = () => draw($("jrFilter").value);
  $("jrMake").onclick = async () => {
    const b = $("jrMake"); b.disabled = true; b.textContent = "Writing…";
    try { const r = await api(`/stories/${story.id}/recap?force=${j.recap ? "true" : "false"}`, { method: "POST" }); j.recap = r.recap; $("jrRecap").textContent = r.recap; b.textContent = "Write it again"; }
    catch (e) { alert(e.message); b.textContent = j.recap ? "Write it again" : "Write a recap"; }
    b.disabled = false;
  };
}
// When a story is reopened after a break: a small card above the input box offering the recap. Nothing is sent to an AI until the player presses the button.
async function maybePreviously(id) {
  const el = $("prevNote"); if (!el) return;
  el.classList.add("hidden");
  let j;
  try { j = await api(`/stories/${id}/journal`); } catch { return; }
  if (!story || story.id !== id || j.turns < 6 || j.away_hours == null || j.away_hours < PREV_AFTER_HOURS) return;
  const key = `prev:${id}:${j.turns}`;
  try { if (sessionStorage.getItem(key)) return; } catch {}
  const done = () => { el.classList.add("hidden"); try { sessionStorage.setItem(key, "1"); } catch {} };
  el.className = "budgetnote prevnote";
  el.innerHTML = `<div><b>Welcome back.</b> You last played this story ${esc(agoText(j.away_hours))}. <span id="prevText" class="prevtext">${j.recap ? esc(j.recap) : ""}</span></div>
    <div class="prevbtns">${j.recap ? "" : `<button id="prevGo">Remind me what happened</button>`}<button id="prevJr">📖 Journal</button><button id="prevX" title="Hide">×</button></div>`;
  $("prevX").onclick = done;
  $("prevJr").onclick = () => showJournal();
  const go = $("prevGo");
  if (go) go.onclick = async () => {
    go.disabled = true; go.textContent = "Writing…";
    try { const r = await api(`/stories/${id}/recap`, { method: "POST" }); if (story && story.id === id) { $("prevText").textContent = r.recap; go.remove(); } }
    catch (e) { go.disabled = false; go.textContent = "Remind me what happened"; alert(e.message); }
  };
}
