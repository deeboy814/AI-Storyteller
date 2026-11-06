// Appearance: reading-area colours (background + text, with matching panel shades derived from them) and the static scene backdrop on the screen sides.
const THEME_PRESETS = [
  { name: "Default", bg: "", text: "" },
  { name: "Midnight", bg: "#0b0f1a", text: "#dbe4f5" },
  { name: "Charcoal", bg: "#1c1c1e", text: "#e6e6e6" },
  { name: "Forest", bg: "#0f1a14", text: "#d9eadf" },
  { name: "Sepia", bg: "#f0e6d2", text: "#3b2f23" },
  { name: "Paper", bg: "#fafaf7", text: "#1d1d1f" },
  { name: "High contrast", bg: "#000000", text: "#ffffff" },
];
const hex2rgb = (h) => { const m = /^#?([0-9a-f]{6})$/i.exec((h || "").trim()); if (!m) return null; const n = parseInt(m[1], 16); return [n >> 16, (n >> 8) & 255, n & 255]; };
const rgb2hex = (c) => "#" + c.map((v) => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, "0")).join("");
const mix = (a, b, t) => a.map((v, i) => v + (b[i] - v) * t);
const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
const contrastRatio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };

// cfg: {ui_bg, ui_text, ui_backdrop, ui_backdrop_dim}. Empty colours = the built-in theme.
function applyTheme(cfg, st) {
  const root = document.documentElement, props = ["--bg", "--panel", "--panel2", "--line", "--text", "--muted"];
  const bg = hex2rgb(cfg && cfg.ui_bg), tx = hex2rgb(cfg && cfg.ui_text);
  props.forEach((p) => root.style.removeProperty(p));
  if (bg || tx) {
    const b = bg || [18, 17, 28], t = tx || (lum(b) > 0.5 ? [29, 29, 31] : [232, 229, 242]);
    const toward = lum(b) > 0.5 ? [0, 0, 0] : [255, 255, 255];   // lighter shades on dark backgrounds, darker on light ones
    root.style.setProperty("--bg", rgb2hex(b)); root.style.setProperty("--text", rgb2hex(t));
    root.style.setProperty("--panel", rgb2hex(mix(b, toward, 0.06))); root.style.setProperty("--panel2", rgb2hex(mix(b, toward, 0.11)));
    root.style.setProperty("--line", rgb2hex(mix(b, toward, 0.2))); root.style.setProperty("--muted", rgb2hex(mix(t, b, 0.42)));
  }
  const url = st === undefined ? (story && story.backdrop && story.backdrop.url) : (st && st.backdrop && st.backdrop.url);
  const on = !!(cfg && cfg.ui_backdrop && url);
  document.body.classList.toggle("has-backdrop", on);
  root.style.setProperty("--backdrop", on ? `url("${url}")` : "none");
  root.style.setProperty("--dim", ((cfg && cfg.ui_backdrop_dim) ?? 55) / 100);
}

function appearanceHtml(cfg) {
  const t = (cfg.ui_text || "") , b = (cfg.ui_bg || "");
  return `<h3>Colours</h3>
    <div class="hint">Changes preview instantly; Save keeps them. They apply to the whole app.</div>
    <div class="presets">${THEME_PRESETS.map((p, i) => `<button class="preset" data-i="${i}" style="${p.bg ? `background:${p.bg};color:${p.text}` : ""}">${p.name}</button>`).join("")}</div>
    <div class="grid2"><div class="field"><label>Background colour</label><div class="colorrow"><input type="color" id="uiBg" value="${b || "#12111c"}"><input id="uiBgHex" value="${esc(b)}" placeholder="default"></div></div>
      <div class="field"><label>Text colour</label><div class="colorrow"><input type="color" id="uiText" value="${t || "#e8e5f2"}"><input id="uiTextHex" value="${esc(t)}" placeholder="default"></div></div><div></div></div>
    <div class="hint" id="uiContrast"></div>
    <div class="uiprev" id="uiPrev"><b>Preview</b><p>The corridor was quiet, and then it was not. <em>*Her hand closed over his.*</em></p></div>
    <h3>Scene backdrop</h3>
    <label class="chk top"><input type="checkbox" id="uiBackdrop" ${cfg.ui_backdrop ? "checked" : ""}><span><b>Show the story's backdrop on the sides of the screen</b><span class="hint">A picture drawn from the story's setting or its current scene fills the sides of a wide window. The centre keeps your chosen colours. The backdrop is static: it does not scroll. It only shows on windows wider than the story column.</span></span></label>
    <div class="grid2"><div class="field"><label>Darken the backdrop</label><input type="range" id="uiDim" min="0" max="90" value="${cfg.ui_backdrop_dim ?? 55}"><div class="hint"><span id="uiDimV">${cfg.ui_backdrop_dim ?? 55}</span>%</div></div><div></div><div></div></div>
    <div id="uiBackdropStory"></div>`;
}

function bindAppearance(cfg) {
  const draft = { ui_bg: cfg.ui_bg || "", ui_text: cfg.ui_text || "", ui_backdrop: !!cfg.ui_backdrop, ui_backdrop_dim: cfg.ui_backdrop_dim ?? 55 };
  const refresh = () => {
    applyTheme(draft);
    const b = hex2rgb(draft.ui_bg) || [18, 17, 28], t = hex2rgb(draft.ui_text) || (lum(b) > 0.5 ? [29, 29, 31] : [232, 229, 242]), r = contrastRatio(b, t);
    $("uiContrast").innerHTML = r < 3 ? `<span style="color:var(--danger)">⚠ Low contrast (${r.toFixed(1)}:1): the text may be hard to read.</span>` : `Contrast ${r.toFixed(1)}:1 ${r >= 7 ? "(excellent)" : "(good)"}`;
    $("uiBg").value = rgb2hex(b); $("uiText").value = rgb2hex(t);
    $("uiBgHex").value = draft.ui_bg; $("uiTextHex").value = draft.ui_text;
  };
  const set = (k, v) => { draft[k] = v; refresh(); };
  $("uiBg").oninput = (e) => set("ui_bg", e.target.value); $("uiText").oninput = (e) => set("ui_text", e.target.value);
  $("uiBgHex").onchange = (e) => set("ui_bg", hex2rgb(e.target.value) ? e.target.value.trim() : ""); $("uiTextHex").onchange = (e) => set("ui_text", hex2rgb(e.target.value) ? e.target.value.trim() : "");
  document.querySelectorAll(".preset").forEach((b) => b.onclick = () => { const p = THEME_PRESETS[+b.dataset.i]; draft.ui_bg = p.bg; draft.ui_text = p.text; refresh(); });
  $("uiBackdrop").onchange = (e) => set("ui_backdrop", e.target.checked);
  $("uiDim").oninput = (e) => { $("uiDimV").textContent = e.target.value; set("ui_backdrop_dim", +e.target.value); };
  const box = $("uiBackdropStory");
  const drawStory = () => {
    if (!story) { box.innerHTML = '<div class="hint">Open a story to create its backdrop.</div>'; return; }
    const bd = story.backdrop || {};
    box.innerHTML = `<label>Backdrop for “${esc(story.title)}”</label>${bd.url ? `<img class="bdprev" src="${esc(bd.url)}" alt="">` : '<div class="hint">No backdrop yet.</div>'}
      <div class="actions" style="justify-content:flex-start;flex-wrap:wrap"><button id="bdSetting">✨ From the story's setting</button><button id="bdScene">✨ From the latest scene</button>${bd.url ? '<button id="bdDel">Remove</button>' : ""}</div>
      <div class="hint" id="bdNote">Drawn with this story's picture maker (about 20 seconds locally), environment only. ${bd.source ? `Last made from the ${bd.source}.` : ""}</div>`;
    const gen = (source) => async (e) => {
      e.target.disabled = true; $("bdNote").textContent = "Drawing the backdrop…";
      try { story = await api(`/stories/${story.id}/backdrop/generate`, { method: "POST", body: { source } }); if (!draft.ui_backdrop) { draft.ui_backdrop = true; $("uiBackdrop").checked = true; } refresh(); drawStory(); }
      catch (err) { $("bdNote").textContent = err.message; e.target.disabled = false; }
    };
    $("bdSetting").onclick = gen("setting"); $("bdScene").onclick = gen("scene");
    if ($("bdDel")) $("bdDel").onclick = async () => { story = await api(`/stories/${story.id}/backdrop`, { method: "DELETE" }); refresh(); drawStory(); };
  };
  drawStory(); refresh();
  return draft;
}
