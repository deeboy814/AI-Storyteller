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

function renderLeft(force = false) {
  const el = $("leftbar"); if (!el) return;
  if (!force && leftOpen && document.activeElement && document.activeElement.id === "lbSearch") return;   // do not pull the search box away while typing
  el.classList.toggle("hidden", !leftOpen);
  document.body.classList.toggle("leftopen", leftOpen);
  const lt = $("leftTab"); if (lt) { lt.textContent = leftOpen ? "‹" : "›"; lt.title = leftOpen ? "Close the stories panel" : "Open your stories"; }
  if (!leftOpen) return;
  const q = leftQuery.trim().toLowerCase();
  let rows = leftList.filter((s) => !q || (s.title + " " + (s.subtitle || "") + " " + (s.player || "")).toLowerCase().includes(q));
  if (leftFilter === "recent" && !q) rows = rows.slice(0, 8);
  const cur = story && story.id;
  el.innerHTML = `<div class="lbhead"><b>Stories</b><span style="flex:1"></span><button class="lbclose" title="Hide this panel">«</button></div>
    <button class="primary lbnew" id="lbNew">＋ New story</button>
    <input id="lbSearch" class="lbsearch" type="search" placeholder="Search stories" value="${esc(leftQuery)}" autocomplete="off">
    <div class="lbtabs"><button class="lbtab${leftFilter === "recent" ? " on" : ""}" data-f="recent">Recent</button><button class="lbtab${leftFilter === "all" ? " on" : ""}" data-f="all">All <small>${leftList.length}</small></button></div>
    <div class="lblist">${rows.map((s) => `<button class="lbitem${s.id === cur ? " on" : ""}" data-id="${esc(s.id)}" title="${esc(s.subtitle || s.title)}">
        <span class="lbthumb" style="${s.cover ? "" : `background:${gradientFor(s.title)}`}">${s.cover ? `<img loading="lazy" src="${thumbUrl(s.cover, 96)}" alt="">` : esc((s.title || "?").trim().charAt(0).toUpperCase())}</span>
        <span class="lbtxt"><b>${esc(s.title)}</b><small>${s.turns} turn${s.turns === 1 ? "" : "s"} · ${leftAgo(s.updated)}</small></span></button>`).join("") || '<div class="side-empty">No stories match.</div>'}</div>
    <div class="lbnav">
      <button data-go="home">🏠 <span>Home</span></button><button data-go="avatars">👤 <span>Avatars</span></button><button data-go="lore">📚 <span>Lore library</span></button>
      <button data-go="art" ${story ? "" : "disabled"}>🖼 <span>Art</span></button><button data-go="usage">💲 <span>Usage</span></button><button data-go="settings">⚙ <span>Settings</span></button>
    </div>`;
  const keep = $("lbSearch");
  keep.oninput = () => { leftQuery = keep.value; const pos = keep.selectionStart; renderLeft(true); const n = $("lbSearch"); n.focus(); n.setSelectionRange(pos, pos); };
  el.querySelector(".lbclose").onclick = () => setLeft(false);
  $("lbNew").onclick = () => { if (innerWidth <= 760) setLeft(false); editStory(null); };
  el.querySelectorAll(".lbtab").forEach((b) => b.onclick = () => { leftFilter = b.dataset.f; try { localStorage.setItem("leftFilter", leftFilter); } catch (e) { /* not saved */ } renderLeft(); });
  el.querySelectorAll(".lbitem").forEach((b) => b.onclick = async () => {
    if (busy) { alert("A reply is still being written. Wait for it to finish before switching stories."); return; }
    if (innerWidth <= 760) setLeft(false);
    if (story && story.id === b.dataset.id && $("home").classList.contains("hidden")) return;
    await openStory(b.dataset.id);
  });
  el.querySelectorAll(".lbnav button").forEach((b) => b.onclick = () => {
    if (innerWidth <= 760) setLeft(false);
    const go = b.dataset.go;
    if (go === "home") showHome(); else if (go === "avatars") showAvatarLibrary(); else if (go === "lore") showLore();
    else if (go === "art" && story) showArtHub(); else if (go === "usage") showUsage(); else if (go === "settings") showSettings();
  });
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
$("btnLeft").onclick = () => { if (isPhone()) showStoriesModal(); else setLeft(!leftOpen); };
$("leftTab").onclick = () => setLeft(!leftOpen);
$("btnAvatars").onclick = () => showAvatarLibrary();
$("btnLore").onclick = () => showLore();
$("btnUsage").onclick = () => showUsage();
setTimeout(() => loadLeftList(true), 400);
