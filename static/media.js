// Videos in place of pictures.
// A picture can have a short clip beside it (made with the "Animate" button or uploaded). Wherever that picture is shown
// (cards, cast, avatar, the Home cover, the full-screen view) this file swaps the still for the clip: muted, looping, and only
// playing while it is on screen. The still stays underneath as the poster and as the face reference for later drawings.
(function () {
  window.VIDEOS = {};                       // picture url (no ?query) -> video url
  const IMG_RULE = /(^|[\s>+~,(])img(?![\w-])/g;

  // Copy every stylesheet rule that mentions the img tag so a clip looks exactly like the picture it replaces.
  function mirrorImgStyles() {
    let css = "";
    const walk = (rules, wrap) => {
      for (const r of rules) {
        try {
          if (r.cssRules && r.conditionText !== undefined) { const inner = []; walk(r.cssRules, (t) => inner.push(t)); if (inner.length) css += `@media ${r.conditionText}{${inner.join("")}}`; }
          else if (r.selectorText && IMG_RULE.test(r.selectorText)) { IMG_RULE.lastIndex = 0; const t = r.cssText.replace(IMG_RULE, "$1video"); wrap ? wrap(t) : (css += t); }
          IMG_RULE.lastIndex = 0;
        } catch (e) {}
      }
    };
    for (const sh of document.styleSheets) { try { walk(sh.cssRules, null); } catch (e) {} }
    const st = document.createElement("style"); st.textContent = css + "video.vidswap{background:transparent}"; document.head.appendChild(st);
  }

  const keyOf = (img) => {
    let src = img.getAttribute("src") || "";
    if (src.startsWith("/api/thumb?")) { try { src = new URL(src, location.origin).searchParams.get("url") || ""; } catch (e) { return ""; } }
    return src.split("?")[0];
  };

  const io = "IntersectionObserver" in window ? new IntersectionObserver((entries) => {
    for (const e of entries) {
      const v = e.target;
      if (e.isIntersecting) { if (!v.getAttribute("src")) v.src = v.dataset.vurl; v.play().catch(() => {}); }
      else v.pause();
    }
  }, { rootMargin: "100px" }) : null;

  function sync(img) {
    if (!(img instanceof HTMLImageElement)) return;
    const vurl = window.VIDEOS[keyOf(img)];
    let v = img._vid;
    if (!vurl) {
      if (v) { io && io.unobserve(v); v.remove(); img._vid = null; img.style.display = img._disp || ""; }
      return;
    }
    if (!v) {
      v = document.createElement("video");
      v.className = img.className + " vidswap";
      v.muted = true; v.loop = true; v.playsInline = true; v.autoplay = true; v.preload = "metadata"; v.setAttribute("muted", "");
      v.style.cssText = img.style.cssText;
      v.poster = img.currentSrc || img.src;
      v.addEventListener("click", () => img.click());
      img._disp = img.style.display;
      img.after(v); img._vid = v;
      img.style.display = "none";
      if (io) io.observe(v);
    }
    if (v.dataset.vurl !== vurl) { v.dataset.vurl = vurl; if (!io || v.getAttribute("src")) v.src = vurl; if (!io) v.play().catch(() => {}); }
  }

  const scan = (root) => { (root.querySelectorAll ? root.querySelectorAll("img") : []).forEach(sync); if (root instanceof HTMLImageElement) sync(root); };

  window.VIDEO_EXTRA = { hidden: {}, earlier: [] };
  window.refreshVideos = async function () {
    try { window.VIDEOS = await fetch("/api/videos").then((r) => r.json()); } catch (e) { window.VIDEOS = {}; }
    try { window.VIDEO_EXTRA = await fetch("/api/videos/extra").then((r) => r.json()); } catch (e) { window.VIDEO_EXTRA = { hidden: {}, earlier: [] }; }
    // a picture whose clip was removed or replaced: drop stale players, then swap again
    document.querySelectorAll("img").forEach((i) => { if (i._vid && window.VIDEOS[keyOf(i)] !== i._vid.dataset.vurl) { io && io.unobserve(i._vid); i._vid.remove(); i._vid = null; i.style.display = i._disp || ""; } });
    scan(document.body);
  };

  // The controls shown next to a picture: add your own clip, make one with ComfyUI, or remove it.
  window.videoBar = (url, animate = true) => {
    const u = (url || "").split("?")[0];
    if (!u) return "";
    const has = !!window.VIDEOS[u], off = !!(window.VIDEO_EXTRA.hidden || {})[u], earlier = (window.VIDEO_EXTRA.earlier || []).includes(u);
    return `<div class="vidbar" data-url="${u.replace(/"/g, "&quot;")}"><label class="btnlike" title="Use a short looping clip (MP4 or WebM, up to 80 MB) wherever this picture is shown">🎞 ${has || off ? "Replace video…" : "Add video…"}<input type="file" class="vidfile" accept="video/mp4,video/webm" hidden></label>`
      + (animate ? `<button type="button" class="vidanimate" title="Make a short clip from this picture with your ComfyUI video workflow (🖼 Art → Picture makers → Video generation)">🎬 Animate</button>` : "")
      + (has ? `<button type="button" class="vidshow" data-show="0" title="Show the still picture instead. The video stays saved.">🖼 Show the picture</button>` : "")
      + (off ? `<button type="button" class="vidshow" data-show="1" title="Show the video again in place of the picture">▶ Show the video</button>` : "")
      + (earlier ? `<button type="button" class="vidswap2" title="See the videos that were replaced and use one again">↺ Earlier videos</button>` : "")
      + (has || off || earlier ? `<button type="button" class="vidremove" title="Removes the video (and earlier ones). The picture stays.">Remove video</button>` : "") + `<span class="hint vidnote"></span></div>`;
  };

  document.addEventListener("change", async (e) => {
    const inp = e.target.closest && e.target.closest("input.vidfile");
    if (!inp || !inp.files[0]) return;
    const bar = inp.closest(".vidbar"), note = bar.querySelector(".vidnote"), f = inp.files[0];
    note.textContent = "Uploading…";
    try {
      const r = await fetch(`/api/videos?url=${encodeURIComponent(bar.dataset.url)}`, { method: "POST", headers: { "Content-Type": f.type || "video/mp4" }, body: f });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      await refreshVideos(); note.textContent = "✔ Video added. It plays wherever this picture shows.";
    } catch (err) { note.textContent = "Could not add it: " + err.message; }
    inp.value = "";
  });

  document.addEventListener("click", async (e) => {
    const rm = e.target.closest && e.target.closest(".vidremove"), an = e.target.closest && e.target.closest(".vidanimate");
    const sh = e.target.closest && e.target.closest(".vidshow"), sw = e.target.closest && e.target.closest(".vidswap2");
    if (sh || sw) {      // switch between the still, the video and the earlier videos; nothing is deleted
      const b = (sh || sw).closest(".vidbar"), u = b.dataset.url;
      try {
        let pick = "";
        if (sw) { pick = await earlierPicker(u); if (pick === null) return; }
        await fetch(sw ? `/api/videos/swap-earlier?url=${encodeURIComponent(u)}&id=${encodeURIComponent(pick)}` : `/api/videos/show?url=${encodeURIComponent(u)}&show=${sh.dataset.show === "1"}`, { method: "POST" });
        await refreshVideos();
        b.outerHTML = videoBar(u);
      } catch (err) { b.querySelector(".vidnote").textContent = err.message; }
      return;
    }
    if (!rm && !an) return;
    const bar = (rm || an).closest(".vidbar"), note = bar.querySelector(".vidnote");
    if (rm) {
      try { const r = await fetch(`/api/videos?url=${encodeURIComponent(bar.dataset.url)}`, { method: "DELETE" }); await refreshVideos(); bar.outerHTML = videoBar(bar.dataset.url); if (window.noteBinned) noteBinned(r.headers.get("x-picture-bin")); } catch (err) { note.textContent = err.message; }
      return;
    }
    const ask = await animateDialog();
    if (!ask) return;
    an.disabled = true;
    try {
      const r = await api("/videos/animate", { method: "POST", body: { url: bar.dataset.url, motion: ask.motion, motion_strength: ask.strength, adult: ask.adult, story: (typeof story !== "undefined" && story) ? story.id : "" } });
      const job = await watchJob(r.job, (j) => { note.textContent = j.current || "Working…"; });
      if (job.status === "error") throw new Error(job.error);
      await refreshVideos(); const url = bar.dataset.url; bar.outerHTML = videoBar(url); const nb = document.querySelector(`.vidbar[data-url="${url}"] .vidnote`); if (nb) nb.textContent = "✔ Video made. The picture is kept: use 🖼 Show the picture to swap back.";
    } catch (err) { note.textContent = "Could not make it: " + err.message; }
    an.disabled = false;
  });

  // The earlier videos of a picture (every clip that was replaced is kept): preview each and pick one to use; the one now shown goes in its place.
  window.earlierPicker = (url) => new Promise(async (resolve) => {
    let list = [];
    try { list = await fetch(`/api/videos/earlier?url=${encodeURIComponent(url)}`).then((r) => r.json()); } catch (e) {}
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px";
    ov.innerHTML = `<div class="card" style="max-width:520px;width:100%;max-height:90vh;overflow:auto"><h3 style="margin-top:0">↺ Earlier videos</h3>
      <div class="hint">Every video that was replaced is kept. Pick one to use; the video now shown is kept in its place.</div>
      ${list.length ? list.map((c) => `<div style="margin:10px 0"><video src="${c.url}" controls muted loop playsinline preload="metadata" style="width:100%;max-height:260px;border-radius:8px;background:#000"></video>
        <div class="actions" style="margin-top:4px;justify-content:space-between"><small class="hint">${new Date(c.when * 1000).toLocaleString()} · ${(c.size / 1048576).toFixed(1)} MB</small><button type="button" class="primary eruse" data-id="${c.id.replace(/"/g, "&quot;")}">Use this one</button></div></div>`).join("") : '<p class="hint">No earlier videos.</p>'}
      <div class="actions"><button type="button" id="erCancel">Close</button></div></div>`;
    document.body.appendChild(ov);
    const done = (v) => { ov.remove(); resolve(v); };
    ov.querySelector("#erCancel").onclick = () => done(null);
    ov.querySelectorAll(".eruse").forEach((bt) => bt.onclick = () => done(bt.dataset.id));
  });

  // The × on a picture that has a video: ask what to remove, so removing an animation never takes the picture with it by accident.
  // Resolves true when the picture itself should now be removed, false when nothing more is to be done (cancelled, or only the video was removed).
  // `plain` is an extra question for a picture without a video (empty = remove at once, as before); `after` redraws the page when only the video went.
  window.removePicture = async (url, after, plain = "") => {
    const u = (url || "").split("?")[0];
    await refreshVideos();
    const has = !!window.VIDEOS[u] || !!(window.VIDEO_EXTRA.hidden || {})[u] || (window.VIDEO_EXTRA.earlier || []).includes(u);
    if (!u || !has) return !plain || confirm(plain);
    const pick = await new Promise((resolve) => {
      const ov = document.createElement("div");
      ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px";
      ov.innerHTML = `<div class="card" style="max-width:460px;width:100%"><h3 style="margin-top:0">This picture has a video</h3>
        <p><b>Remove video only</b> keeps the picture and removes its video, including any earlier ones. Whatever you remove from a story goes to its 🗑 Picture bin for 30 days.</p>
        <div class="actions" style="flex-wrap:wrap"><button type="button" data-p="">Cancel</button><button type="button" data-p="all">Remove picture and video</button><button type="button" data-p="video" class="primary">Remove video only</button></div></div>`;
      document.body.appendChild(ov);
      ov.querySelectorAll("button").forEach((b) => b.onclick = () => { ov.remove(); resolve(b.dataset.p); });
      ov.querySelector('[data-p="video"]').focus();
    });
    if (pick !== "video") return pick === "all";
    const r = await fetch(`/api/videos?url=${encodeURIComponent(u)}`, { method: "DELETE" });
    if (!r.ok) throw new Error("Could not remove the video.");
    await refreshVideos();
    if (after) after();
    if (window.noteBinned) noteBinned(r.headers.get("x-picture-bin"));
    return false;
  };

  // The Animate question: what should move, and (in a story with adult content on) whether this is an adult clip, which uses the adult video model and LoRAs.
  window.animateDialog = () => new Promise((resolve) => {
    const adultOk = typeof story !== "undefined" && story && story.nsfw;
    const ov = document.createElement("div");
    ov.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;display:flex;align-items:center;justify-content:center;padding:16px";
    ov.innerHTML = `<div class="card" style="max-width:460px;width:100%"><h3 style="margin-top:0">🎬 Animate</h3>
      <label>What should move?</label><textarea id="anMotion" rows="3" placeholder="Empty = gentle breathing and a little hair and clothing motion"></textarea>
      ${adultOk ? `<label>Kind of video</label><select id="anAdult"><option value="">Automatic (adult if the picture or your words are explicit)</option><option value="1">Adult: use the adult video model and LoRAs</option><option value="0">Everyday: use the normal video model and LoRAs</option></select>` : ""}
      <label>Motion strength</label><select id="anStr"><option value="">Automatic (gentler for close-ups)</option><option value="gentle">Gentle</option><option value="normal">Normal</option><option value="strong">Strong (full motion LoRAs)</option></select>
      <div class="actions" style="margin-top:10px"><button type="button" id="anCancel">Cancel</button><button type="button" id="anGo" class="primary">Make the video</button></div></div>`;
    document.body.appendChild(ov);
    const done = (v) => { ov.remove(); resolve(v); };
    ov.querySelector("#anCancel").onclick = () => done(null);
    ov.querySelector("#anGo").onclick = () => { const a = ov.querySelector("#anAdult"); done({ motion: ov.querySelector("#anMotion").value.trim(), strength: ov.querySelector("#anStr").value, adult: a && a.value !== "" ? a.value === "1" : null }); };
    ov.querySelector("#anMotion").focus();
  });

  document.addEventListener("DOMContentLoaded", () => {
    mirrorImgStyles();
    new MutationObserver((muts) => {
      for (const m of muts) {
        if (m.type === "attributes") sync(m.target);
        else m.addedNodes.forEach((n) => { if (n.nodeType === 1) scan(n); });
      }
    }).observe(document.body, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
    refreshVideos();
  });
})();

// 🎬 Animate all: make a clip for every picture of one character (slug) or of the whole story (slug ""), one after another, in a single background job.
// note: an element that shows progress. Pressing the button opens a small chooser under it: tick which groups of pictures to animate (each picture category,
// outfit pictures, backgrounds, scene pictures…; all ticked = everything). Pictures that already have a video are skipped unless every chosen one has, then the player can choose to remake them.
async function animateAll(slug, btn, note) {
  if (!story) return;
  const old = note.parentNode.querySelector(".animpick");
  if (old) { old.remove(); return; }
  const who = slug ? "of this character" : "in this story";
  const count = async (kinds) => api(`/stories/${story.id}/animate-all?slug=${encodeURIComponent(slug || "")}&kinds=${encodeURIComponent((kinds || []).join(","))}`);
  let c;
  try { c = await count([]); } catch (e) { note.textContent = e.message; return; }
  if (!c.total) { note.textContent = `There are no pictures ${who} to animate yet.`; return; }
  const box = document.createElement("div");
  box.className = "animpick";
  box.style.cssText = "margin:8px 0;padding:8px 10px;border:1px solid var(--line, #8884);border-radius:8px";
  box.innerHTML = `<div><b>Which pictures should get a video?</b></div>
    <div class="animgroups" style="display:flex;flex-wrap:wrap;gap:4px 16px;margin:6px 0">${c.groups.map((g) => `<label class="chk"><input type="checkbox" data-k="${esc(g.id)}" checked> ${esc(g.label)} <small class="muted">(${g.total}${g.with_video ? `, ${g.with_video} done` : ""})</small></label>`).join("")}</div>
    <div class="actions" style="justify-content:flex-start;flex-wrap:wrap;align-items:center;gap:8px">
      <button type="button" class="aAll">All</button><button type="button" class="aNone">None</button>
      <span class="hint aSum"></span><button type="button" class="primary aGo">🎬 Animate</button><button type="button" class="aCancel">Cancel</button></div>
    <div class="hint">Each video takes a few minutes on your computer, so this can run a long time. You can keep playing; new pictures are drawn first. ⏹ Stop pictures cancels it. Pictures that already have a video are skipped.</div>`;
  note.parentNode.insertBefore(box, note);
  const boxes = () => [...box.querySelectorAll("input[data-k]")];
  const chosen = () => boxes().filter((i) => i.checked).map((i) => i.dataset.k);
  let cur = c, tick = 0;
  const refresh = async () => {
    const my = ++tick, kinds = chosen();
    box.querySelector(".aGo").disabled = !kinds.length;
    if (!kinds.length) { box.querySelector(".aSum").textContent = "Tick at least one group."; return; }
    try { const r = await count(boxes().every((i) => i.checked) ? [] : kinds); if (my !== tick) return; cur = r;
      box.querySelector(".aSum").textContent = r.without_video ? `${r.without_video} picture${r.without_video === 1 ? "" : "s"} to animate${r.with_video ? ` (${r.with_video} already have one)` : ""}` : `All ${r.total} already have a video`;
    } catch (e) { box.querySelector(".aSum").textContent = e.message; }
  };
  boxes().forEach((i) => i.onchange = refresh);
  box.querySelector(".aAll").onclick = () => { boxes().forEach((i) => i.checked = true); refresh(); };
  box.querySelector(".aNone").onclick = () => { boxes().forEach((i) => i.checked = false); refresh(); };
  box.querySelector(".aCancel").onclick = () => box.remove();
  box.querySelector(".aGo").onclick = async () => {
    const kinds = boxes().every((i) => i.checked) ? [] : chosen();
    let redo = false;
    if (!cur.without_video) {
      if (!confirm(`All ${cur.total} chosen pictures ${who} already have a video. Make new videos for all of them? (The current videos are kept under ↺ Earlier videos.)`)) return;
      redo = true;
    }
    box.remove();
    btn.disabled = true;
    let shown = 0;
    try {
      const r = await api(`/stories/${story.id}/animate-all`, { method: "POST", body: { slug, redo, kinds } });
      if (!r.job) { note.textContent = "Nothing to animate."; return; }
      const j = await watchJob(r.job, async (j) => {
        note.textContent = `${j.current || "Waiting for the picture maker…"} ${j.done ? `(${j.done} done)` : ""}`;
        if (j.done > shown) { shown = j.done; await refreshVideos(); }
      });
      await refreshVideos();
      const made = (j.result || {}).made || 0, failed = ((j.result || {}).failed || []);
      if (j.status === "error") note.textContent = `${made ? `✔ ${made} video${made === 1 ? "" : "s"} made, then it stopped: ` : ""}${j.error}`;
      else note.textContent = `✔ ${made} video${made === 1 ? "" : "s"} made.` + (failed.length ? ` ${failed.length} could not be made: ${failed.join("; ")}` : "");
    } catch (e) { note.textContent = "Could not start: " + e.message; }
    finally { btn.disabled = false; }
  };
  refresh();
}

// Auto-animate (Picture makers → Video generation → "Animate every new picture"): the server makes the clips in the background.
// This checks now and then, so each new clip appears without reloading, and shows the Stop button while videos are being made.
(function () {
  let lastMade = -1, autoShown = false;
  async function poll() {
    let next = 20000;
    try {
      const st = await fetch("/api/videos/auto-status").then((r) => r.json());
      if (lastMade >= 0 && st.made !== lastMade && window.refreshVideos) refreshVideos();
      lastMade = st.made;
      const working = st.busy || st.waiting > 0;
      if (working && !autoShown && typeof stopPicsButton === "function") { _watching++; stopPicsButton(true); autoShown = true; }
      if (!working && autoShown) { autoShown = false; if (--_watching <= 0) { _watching = 0; stopPicsButton(false); } }
      next = working ? 8000 : st.on ? 15000 : 30000;
    } catch (e) {}
    setTimeout(poll, next);
  }
  document.addEventListener("DOMContentLoaded", () => setTimeout(poll, 3000));
})();

// 🎞 on a cast picture opens the video controls right under it
document.addEventListener("click", (e) => {
  const b = e.target.closest && e.target.closest(".mvid");
  if (!b) return;
  const fig = b.closest("figure"), old = fig.querySelector(".vidbar");
  if (old) { old.remove(); return; }
  fig.insertAdjacentHTML("beforeend", videoBar(b.dataset.url));
});
