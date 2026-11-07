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

  function show(i) {
    at = (i + group.length) % group.length;
    const img = box.querySelector("img"), url = full(group[at]);
    box.classList.remove("actual"); box.querySelector(".lbwrap").scrollTo(0, 0);
    img.src = url;
    box.querySelector(".lbopen").href = url;
    box.querySelector(".lbcount").textContent = group.length > 1 ? `${at + 1} / ${group.length}` : "";
    box.classList.toggle("single", group.length < 2);
  }
  function close() { if (box) { box.remove(); box = null; document.removeEventListener("keydown", keys, true); } }
  function keys(e) {
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === "ArrowLeft" && group.length > 1) { e.preventDefault(); show(at - 1); }
    else if (e.key === "ArrowRight" && group.length > 1) { e.preventDefault(); show(at + 1); }
  }
  function open(img) {
    const scope = img.closest(".gallery, .moods, .cardgrid, .cfiles") || img.parentElement;
    group = [...scope.querySelectorAll("img")].filter(eligible);
    if (!group.includes(img)) group = [img];
    close();
    box = document.createElement("div");
    box.id = "lightbox";
    box.innerHTML = `<div class="lbbar"><span class="lbcount"></span><a class="lbopen" target="_blank" rel="noopener" title="Open the original in a new tab">Open original ↗</a><button class="lbclose" title="Close (Esc)">×</button></div>
      <button class="lbnav lbprev" title="Previous (←)">‹</button><button class="lbnav lbnext" title="Next (→)">›</button>
      <div class="lbwrap"><img alt="" title="Click to toggle actual size"></div>`;
    document.body.appendChild(box);
    box.querySelector(".lbclose").onclick = close;
    box.querySelector(".lbprev").onclick = (e) => { e.stopPropagation(); show(at - 1); };
    box.querySelector(".lbnext").onclick = (e) => { e.stopPropagation(); show(at + 1); };
    box.querySelector(".lbwrap img").onclick = (e) => { e.stopPropagation(); box.classList.toggle("actual"); };
    box.onclick = (e) => { if (e.target === box || e.target.classList.contains("lbwrap")) close(); };
    document.addEventListener("keydown", keys, true);
    show(group.indexOf(img));
  }
  document.addEventListener("click", (e) => {
    const img = e.target.closest ? e.target.closest("img") : null;
    if (!img || !eligible(img)) return;
    e.preventDefault(); e.stopPropagation();              // the picture's own link or card would otherwise open a tab or another screen
    open(img);
  }, true);
})();
