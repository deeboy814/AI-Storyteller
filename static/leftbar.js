// Left panel: your stories (switch with one click, search, recent / all), a New story button and shortcuts to the app-wide tools.
// It is a column beside the story on a wide screen and a slide-over from the left edge on a phone. Open or closed is remembered.
let leftList = [], leftLoaded = 0, leftFilter = (() => { try { return localStorage.getItem("leftFilter") || "recent"; } catch (e) { return "recent"; } })(), leftQuery = "";
let leftOpen = (() => { try { const v = localStorage.getItem("leftOpen"); return v === null ? innerWidth >= 1250 : v === "1"; } catch (e) { return false; } })();

const leftAgo = (t) => {
  const m = Math.max(0, (Date.now() / 1000 - t) / 60);
  return m < 2 ? "just now" : m < 60 ? Math.round(m) + " min ago" : m < 60 * 36 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago";
};

async function loadLeftList(force = false) {
  if (!force && Date.now() - leftLoaded < 20000 && leftList.length) return;
  try { leftList = (await api("/stories")).sort((a, b) => b.updated - a.updated); leftLoaded = Date.now(); } catch (e) { /* keep the old list */ }
  renderLeft();
}

// The story tools: what used to be separate buttons in the top bar. Only the open story has a Cast, an Avatar, an Inventory, a Journal and a Search.
const toolList = () => [
  ["cast", "🎭", "Cast", story ? `${(story.cast || []).length}` : "", !story], ["lore", "📚", "Lore library", "", false], ["avatar", "🧑", "Avatar", "", !story],
  ["items", "🎒", "Inventory", "", !story], ["journal", "📖", "Journal", "", !story], ["search", "🔍", "Search", "", !story],
];
function runTool(go) {
  if (go === "home") showHome(); else if (go === "settings") showSettings();
  else if (go === "cast" && story) showCast(); else if (go === "lore") showLore(); else if (go === "avatar" && story) showPlayer();
  else if (go === "items" && story) showInventory(); else if (go === "journal" && story) showJournal(); else if (go === "search" && story) showSearch();
}
function toolButtons() { return toolList().map(([go, icon, label, n, off]) => `<button data-go="${go}" ${off ? "disabled" : ""}>${icon} <span>${label}</span>${n ? `<small>${n}</small>` : ""}</button>`).join(""); }

function renderLeft(force = false) {
  const el = $("leftbar"); if (!el) return;
  el.classList.toggle("hidden", !leftOpen);
  document.body.classList.toggle("leftopen", leftOpen);
  const lt = $("leftTab"); if (lt) { lt.textContent = leftOpen ? "‹" : "›"; lt.title = leftOpen ? "Close the tools panel" : "Open the story tools"; }
  if (!leftOpen) return;
  el.innerHTML = `<div class="lbhead"><b>${story ? esc(story.title) : "Story tools"}</b><span style="flex:1"></span><button class="lbclose" title="Hide this panel">«</button></div>
    <div class="lbnav lbtools">${toolButtons()}</div>
    <div class="lbspacer"></div>
    <div class="lbnav bottom"><button data-go="home">🏠 <span>Home</span></button><button data-go="settings">⚙ <span>Settings</span></button></div>`;
  el.querySelector(".lbclose").onclick = () => setLeft(false);
  el.querySelectorAll(".lbnav button").forEach((b) => b.onclick = () => { if (innerWidth <= 760) setLeft(false); runTool(b.dataset.go); });
}

function setLeft(on) {
  leftOpen = on;
  try { localStorage.setItem("leftOpen", on ? "1" : "0"); } catch (e) { /* not saved */ }
  renderLeft(true); if (on) loadLeftList(true);
}

const isPhone = () => innerWidth <= 760;
// On a phone there is no side column: 📚 in the menu opens the same stories list in a window, and Avatars, Lore and Usage are menu buttons of their own
function showStoriesModal(query = "") {
  loadLeftList(true);
  const q = query.trim().toLowerCase(), cur = story && story.id;
  const rows = leftList.filter((s) => !q || (s.title + " " + (s.subtitle || "") + " " + (s.player || "")).toLowerCase().includes(q));
  modal(`<h2>📚 Your stories</h2>
    <div class="actions" style="justify-content:flex-start"><button class="primary" id="smNew">＋ New story</button></div>
    <input id="smSearch" type="search" placeholder="Search stories" value="${esc(query)}" autocomplete="off">
    <div class="lblist" style="max-height:60vh;margin-top:8px">${rows.map((s) => `<button class="lbitem${s.id === cur ? " on" : ""}" data-id="${esc(s.id)}">
      <span class="lbthumb" style="${s.cover ? "" : `background:${gradientFor(s.title)}`}">${s.cover ? `<img loading="lazy" src="${thumbUrl(s.cover, 96)}" alt="">` : esc((s.title || "?").trim().charAt(0).toUpperCase())}</span>
      <span class="lbtxt"><b>${esc(s.title)}</b><small>${s.turns} turn${s.turns === 1 ? "" : "s"} · ${leftAgo(s.updated)}</small></span></button>`).join("") || '<div class="side-empty">No stories match.</div>'}</div>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  $("smNew").onclick = () => { closeModal(); editStory(null); };
  const sr = $("smSearch"); sr.oninput = () => { const pos = sr.selectionStart; showStoriesModal(sr.value); const n = $("smSearch"); n.focus(); n.setSelectionRange(pos, pos); };
  document.querySelectorAll("#modalCard .lbitem").forEach((b) => b.onclick = async () => {
    if (busy) { alert("A reply is still being written. Wait for it to finish before switching stories."); return; }
    closeModal(); if (!(story && story.id === b.dataset.id)) await openStory(b.dataset.id);
  });
}
// 📚 always opens the stories window (switch story, search, New story); 🧰 (or the little tab on the left edge) shows or hides the tools panel. On a phone the tools open in a window.
function showToolsModal() {
  modal(`<h2>${story ? esc(story.title) : "Story tools"}</h2><div class="lbnav lbtools">${toolButtons()}</div><div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  document.querySelectorAll("#modalCard .lbnav button").forEach((b) => b.onclick = () => { closeModal(); runTool(b.dataset.go); });
}
$("btnLeft").onclick = () => showStoriesModal();
$("btnTools").onclick = () => { if (isPhone()) showToolsModal(); else setLeft(!leftOpen); };
$("leftTab").onclick = () => setLeft(!leftOpen);
setTimeout(() => loadLeftList(true), 400);
