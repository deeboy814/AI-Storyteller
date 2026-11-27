// The picture bin: removed or replaced character pictures (and videos) are kept for 30 days.
// api() calls noteBinned() when the server says a request moved something there; that shows the Undo bar.
// showPictureBin() is the window that lists the bin (Images tab and the 🖼 Art menu).
(function () {
  let bar = null, ids = [], timer = 0;

  function hideBar() { if (bar) bar.remove(); bar = null; ids = []; clearTimeout(timer); }

  // Redraw whatever is showing the pictures that came back.
  async function afterRestore(r) {
    if (typeof story !== "undefined" && story && story.id === r.story.id) {
      story = r.story;
      if (window.refreshVideos) await refreshVideos();
      renderAll();
      if (document.getElementById("imGrid") && !document.getElementById("modal").classList.contains("hidden")) {
        const open = document.getElementById("imGrid").dataset.slug || r.slugs[0];
        if ((story.cast || []).some((c) => c.slug === open)) showCharacter(open, "Images");
      }
    }
  }

  window.noteBinned = (header, path) => {
    const got = (header || "").split(",").filter(Boolean);
    const sid = (typeof story !== "undefined" && story && story.id) || "";
    if (!got.length || !sid) return;
    // several removals in a row (for example removing outfit after outfit) can all be undone together while the bar is up
    ids = bar && bar.dataset.sid === sid ? ids.concat(got) : got;
    if (bar) bar.remove();
    clearTimeout(timer);
    bar = document.createElement("div");
    bar.className = "binbar"; bar.dataset.sid = sid; bar.setAttribute("role", "status");
    bar.innerHTML = `<span>🗑 Moved ${ids.length === 1 ? "1 item" : ids.length + " items"} to the picture bin.</span><button type="button" class="primary binundo">Undo</button><button type="button" class="binclose" aria-label="Close">✕</button>`;
    document.body.appendChild(bar);
    bar.querySelector(".binclose").onclick = hideBar;
    bar.querySelector(".binundo").onclick = async (e) => {
      e.target.disabled = true; e.target.textContent = "Putting back…";
      const want = ids.slice();
      try {
        const r = await api(`/stories/${sid}/picture-bin/restore`, { method: "POST", body: { ids: want } });
        hideBar(); await afterRestore(r);
        if (r.failed.length) alert("Some could not be put back:\n" + r.failed.join("\n"));
      } catch (err) { hideBar(); alert(err.message); }
    };
    timer = setTimeout(hideBar, 15000);
  };

  window.showPictureBin = async (back) => {
    const sid = story.id;
    let data;
    try { data = await api(`/stories/${sid}/picture-bin`); } catch (e) { alert(e.message); return; }
    hideBar();
    const why = { removed: "removed", replaced: "replaced by a newer picture" };
    modal(`<h2>🗑 Picture bin</h2>
      <p class="hint">Character pictures and videos you remove or replace in this story are kept here for ${data.keep_days} days, then deleted for good. Restore puts one back where it was.</p>
      <div class="bingrid">${data.items.length ? data.items.map((m) => `<figure class="binitem" data-id="${esc(m.id)}">${m.thumb ? `<img loading="lazy" src="${esc(m.thumb)}" alt="">` : '<div class="binnone">no preview</div>'}
        <figcaption><b>${esc(m.char || "Picture")}</b><span>${m.kind === "video" ? "🎞 " : ""}${esc(m.label)}${m.kind === "picture" && m.has_video ? " (with its video)" : ""}</span>
        <small class="hint">${why[m.reason] || "removed"} ${new Date(m.deleted * 1000).toLocaleDateString()} · ${m.days_left} day${m.days_left === 1 ? "" : "s"} left</small>
        <span class="binbtns"><button type="button" class="binrestore">Restore</button><button type="button" class="x binforever" title="Delete for good">×</button></span></figcaption></figure>`).join("") : '<p class="hint">The bin is empty.</p>'}</div>
      <div class="hint" id="binStatus" role="status"></div>
      <div class="actions"><button type="button" id="binEmpty" class="danger"${data.items.length ? "" : " disabled"}>Empty the bin</button><span style="flex:1"></span><button type="button" id="binBack">${back ? "Back" : "Close"}</button></div>`);
    const card = $("modalCard");
    $("binBack").onclick = () => (back ? back() : closeModal());
    $("binEmpty").onclick = async () => {
      if (!confirm("Delete everything in this story's picture bin for good? This cannot be undone.")) return;
      try { await api(`/stories/${sid}/picture-bin/delete`, { method: "POST", body: { all: true } }); showPictureBin(back); } catch (e) { $("binStatus").textContent = e.message; }
    };
    card.querySelector(".bingrid").onclick = async (e) => {
      const b = e.target.closest("button"); if (!b) return;
      const id = b.closest(".binitem").dataset.id;
      try {
        if (b.classList.contains("binrestore")) {
          b.disabled = true;
          const r = await api(`/stories/${sid}/picture-bin/restore`, { method: "POST", body: { ids: [id] } });
          story = r.story; if (window.refreshVideos) await refreshVideos(); renderAll();
          await showPictureBin(back);
          $("binStatus").textContent = "✔ Put back.";
        } else if (b.classList.contains("binforever")) {
          if (!confirm("Delete this for good? This cannot be undone.")) return;
          await api(`/stories/${sid}/picture-bin/delete`, { method: "POST", body: { ids: [id] } }); showPictureBin(back);
        }
      } catch (err) { b.disabled = false; $("binStatus").textContent = err.message; }
    };
  };
})();
