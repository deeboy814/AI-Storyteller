// Click any picture in the app to see it full size in an overlay. Esc, a click outside, or × closes it; ← → move between pictures of the same gallery;
// clicking the picture itself toggles between "fit to screen" and "actual size" (scroll to pan).
(function () {
  const SKIP = ".hcover, .charcard, #lightbox";          // story cards and character cards open their story or character; the overlay itself
  const full = (img) => {                                 // the original behind a thumbnail
    try { const u = new URL(img.currentSrc || img.src, location.href); if (u.pathname === "/api/thumb" && u.searchParams.get("url")) return u.searchParams.get("url"); } catch (e) { /* use src */ }
    return img.currentSrc || img.src;
  };
  const eligible = (img) => img.tagName === "IMG" && !img.closest(SKIP) && !(img.complete && img.naturalWidth > 0 && img.naturalWidth <= 40);   // not icons
  let group = [], at = 0, box = null;
  const describe = (img) => {                             // what the picture is: its caption (picture name, scene or place), else its description text
    const fig = img.closest("figure"), cap = fig && fig.querySelector("figcaption");
    let t = img.dataset.name || "";             // a picture in the story names itself (character · picture name)
    if (!t && cap) { const c = cap.cloneNode(true); c.querySelectorAll("button").forEach((b) => b.remove()); t = c.textContent; }
    return (t || img.title || img.alt || "").replace(/\s+/g, " ").trim();
  };

  function show(i) {
    at = (i + group.length) % group.length;
    const img = box.querySelector("img"), url = full(group[at]);
    box.classList.remove("actual"); box.querySelector(".lbwrap").scrollTo(0, 0);
    img.src = url;
    box.querySelector(".lbopen").href = url;
    const cap = describe(group[at]), capEl = box.querySelector(".lbcap");
    capEl.textContent = cap; capEl.hidden = !cap;
    box.querySelector(".lbcount").textContent = group.length > 1 ? `${at + 1} / ${group.length}` : "";
    box.classList.toggle("single", group.length < 2);
  }
  let idleTimer = 0;
  function wake() {                                       // the buttons fade away while you look at the picture and come back when the mouse or a finger moves
    if (!box) return;
    box.classList.remove("idle"); clearTimeout(idleTimer);
    idleTimer = setTimeout(() => { if (box) box.classList.add("idle"); }, 2500);
  }
  function close() { clearTimeout(idleTimer); if (box) { box.remove(); box = null; document.removeEventListener("keydown", keys, true); } }
  function keys(e) {
    wake();
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowLeft" && group.length > 1) { e.preventDefault(); show(at - 1); }
    else if (e.key === "ArrowRight" && group.length > 1) { e.preventDefault(); show(at + 1); }
  }
  function open(img) {
    const scope = img.closest("#imGrid, .gallery, .moods, .cardgrid, .cfiles") || img.parentElement;     // a character's Images tab: every picture of all its categories, not one category at a time
    group = [...scope.querySelectorAll("img")].filter((x) => eligible(x) && !x.closest(".imcat.min"));          // (minimized categories stay out)
    if (!group.includes(img)) group = [img];
    close();
    box = document.createElement("div");
    box.id = "lightbox";
    box.innerHTML = `<div class="lbbar"><span class="lbcount"></span><a class="lbopen" target="_blank" rel="noopener" title="Open the original in a new tab">Open original ↗</a><button class="lbclose" title="Close (Esc)">×</button></div>
      <button class="lbnav lbprev" title="Previous (←)">‹</button><button class="lbnav lbnext" title="Next (→)">›</button>
      <div class="lbwrap"><img alt="" title="Click to toggle actual size"></div><div class="lbcap" hidden></div>`;
    document.body.appendChild(box);
    box.querySelector(".lbclose").onclick = close;
    box.querySelector(".lbprev").onclick = (e) => { e.stopPropagation(); show(at - 1); };
    box.querySelector(".lbnext").onclick = (e) => { e.stopPropagation(); show(at + 1); };
    box.querySelector(".lbwrap img").onclick = (e) => { e.stopPropagation(); box.classList.toggle("actual"); };
    box.onclick = (e) => { if (e.target === box || e.target.classList.contains("lbwrap")) close(); };
    document.addEventListener("keydown", keys, true);
    box.addEventListener("mousemove", wake); box.addEventListener("touchstart", wake, { passive: true });
    show(group.indexOf(img)); wake();
  }
  document.addEventListener("click", (e) => {
    const img = e.target.closest ? e.target.closest("img") : null;
    if (!img || !eligible(img)) return;
    e.preventDefault(); e.stopPropagation();              // the picture's own link or card would otherwise open a tab or another screen
    open(img);
  }, true);
})();
