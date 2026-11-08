// AI chooser: which AI does the story, the recaps/helper jobs, and the pictures. Used for a new story and at the top of Settings.
// mountAIChooser(el, {initial}) -> {get(): {story, recaps, images}, ready: Promise}
const AI_ROLES = [
  { key: "story", title: "Story narrator", kind: "text", help: "Writes the story each turn. This is the one that matters most for quality." },
  { key: "recaps", title: "Recaps & helpers", kind: "text", help: "Memory summaries, key events, bios and picture prompts. Small jobs that run often, so a cheaper model is fine." },
  { key: "small", title: "Small helpers (optional)", kind: "text", optional: true, help: "A second, cheaper (or local) model for the small jobs that run after every reply: key events log, new characters, side panel, background place and suggested replies. Leave on “same as Recaps & helpers” to use one helper for everything." },
  { key: "images", title: "Pictures", kind: "image", help: "Draws character portraits and scene illustrations." },
];
const FIT_LABEL = { fits: "fits in video memory", partial: "slower: spills into RAM", cpu: "CPU only: slow", too_big: "too big for this computer" };

function mountAIChooser(el, { initial = {}, onChange = null, roles = null } = {}) {
  const st = { opts: null, choice: {}, keyOpen: {} };
  const ROLES = roles ? AI_ROLES.filter((r) => roles.includes(r.key)) : AI_ROLES;   // roles: show only some of the three choices
  const notify = () => { if (onChange && st.opts) onChange(); };   // lets other parts of the Settings window follow the choices
  const list = (kind) => (kind === "image" ? st.opts.images : st.opts.text);
  const prov = (kind, id) => list(kind).find((p) => p.id === id);
  const label = (p) => p.name + (p.local ? (p.ready ? "" : " (not available)") : p.has_key ? "" : " (needs a key)");

  function fill() { // make sure every role has a valid choice: saved one if usable, else the recommendation
    for (const r of ROLES) {
      const c = st.choice[r.key] || initial[r.key], p = c && prov(r.kind, c.provider);
      if (r.optional && !(c && c.provider && c.model)) { st.choice[r.key] = null; continue; }   // "same as Recaps & helpers"
      if (p && p.models.some((m) => m.id === c.model)) st.choice[r.key] = { provider: c.provider, model: c.model };
      else if (p && c && c.provider === p.id && !p.models.length) st.choice[r.key] = { provider: c.provider, model: c.model };
      else if (st.opts.recommended[r.key]) { const x = st.opts.recommended[r.key]; st.choice[r.key] = { provider: x.provider, model: x.model }; }
      else st.choice[r.key] = null;
    }
  }
  const modelOptions = (p, sel) => {
    const groups = {};
    for (const m of p.models) (groups[m.tier] ||= []).push(m);
    const text = (m) => esc(m.id) + (m.size_gb ? ` · ${m.size_gb} GB` : "") + (m.fit ? ` · ${FIT_LABEL[m.fit]}` : "") + (m.unverified ? " · unverified" : "");
    return Object.keys(st.opts.tier_labels).filter((t) => groups[t]).map((t) =>
      `<optgroup label="${esc(st.opts.tier_labels[t])}">${groups[t].map((m) => `<option value="${esc(m.id)}" ${m.id === sel ? "selected" : ""}>${text(m)}</option>`).join("")}</optgroup>`).join("");
  };
  function roleHtml(r) {
    const c = st.choice[r.key], p = c && prov(r.kind, c.provider), rec = st.opts.recommended[r.key];
    if (r.optional && !c) {   // not chosen: one dropdown to turn it on
      return `<div class="aiRole" data-role="${r.key}"><div class="aiHead"><b>${r.title}</b> <small>${esc(r.help)}</small></div>
        <div class="grid2" style="grid-template-columns:1fr 2fr"><div><select data-sel="prov"><option value="">Same as Recaps &amp; helpers</option>${list(r.kind).map((q) => `<option value="${esc(q.id)}">${esc(label(q))}</option>`).join("")}</select></div><div></div></div></div>`;
    }
    const provs = list(r.kind);
    const isRec = rec && c && rec.provider === c.provider && rec.model === c.model;
    const needsKey = p && !p.local && !p.has_key;
    return `<div class="aiRole" data-role="${r.key}">
      <div class="aiHead"><b>${r.title}</b> <small>${esc(r.help)}</small></div>
      <div class="grid2" style="grid-template-columns:1fr 2fr">
        <div><select data-sel="prov">${r.optional ? '<option value="">Same as Recaps &amp; helpers</option>' : ""}${provs.map((q) => `<option value="${esc(q.id)}" ${c && q.id === c.provider ? "selected" : ""}>${esc(label(q))}</option>`).join("")}</select></div>
        <div><select data-sel="model" ${p && p.models.length ? "" : "disabled"}>${p && p.models.length ? modelOptions(p, c && c.model) : `<option>${needsKey ? "add a key to see models" : "no models found"}</option>`}</select></div>
      </div>
      ${needsKey && p.hint ? `<div class="hint">${esc(p.hint)}</div>` : ""}
      ${p && p.error ? `<div class="hint" style="color:var(--danger,#c0504d)">${esc(p.error)}</div>` : ""}
      ${needsKey ? `<div class="aiKey"><input type="password" data-key placeholder="Paste your ${esc(p.name)} API key"><button data-savekey="${esc(p.id)}">Save key</button>${p.key_help ? `<small>Get one: ${esc(p.key_help)}</small>` : ""}</div>` : ""}
      ${rec ? (isRec ? `<div class="hint">✓ Recommended for this computer. ${esc(rec.why)}</div>` : `<div class="hint">Recommended: <b>${esc((prov(r.kind, rec.provider) || {}).name || rec.provider)} · ${esc(rec.model)}</b> — ${esc(rec.why)} <button data-usedrec="${r.key}">Use it</button></div>`) : ""}
    </div>`;
  }
  function render() {
    const o = st.opts;
    el.innerHTML = `<div class="aiBox">
      <div class="hint">Your computer: <b>${esc(o.hardware.summary)}</b> <button data-rescan>Re-scan</button></div>
      ${o.notes.map((n) => `<div class="hint" style="color:var(--warn,#b8860b)">⚠ ${esc(n)}</div>`).join("")}
      ${ROLES.map(roleHtml).join("")}
      ${o.install.length ? `<details class="hint"><summary>Local models worth installing for this computer</summary>${o.install.map((i) => `<div><code>${esc(i.command)}</code> · ${i.size_gb} GB · ${esc(i.note)}</div>`).join("")}<div>Run the command in a terminal, then Re-scan.</div></details>` : ""}
    </div>`;
    el.querySelectorAll(".aiRole").forEach((box) => {
      const r = AI_ROLES.find((x) => x.key === box.dataset.role);
      box.querySelector('[data-sel="prov"]').onchange = (e) => {
        if (r.optional && !e.target.value) { st.choice[r.key] = null; render(); return; }
        const p = prov(r.kind, e.target.value), m = p.models.find((x) => x.tier === "balanced") || p.models[0];
        st.choice[r.key] = { provider: p.id, model: m ? m.id : (p.default_model || "") }; render();
      };
      const ms = box.querySelector('[data-sel="model"]'); if (ms) ms.onchange = (e) => { st.choice[r.key].model = e.target.value; notify(); };
      const sk = box.querySelector("[data-savekey]");
      if (sk) sk.onclick = async () => {
        const v = box.querySelector("[data-key]").value.trim(); if (!v) return;
        sk.disabled = true; sk.textContent = "Checking…";
        await api("/provider-key", { method: "POST", body: { provider: sk.dataset.savekey, api_key: v } });
        await load(true);
      };
      const ur = box.querySelector("[data-usedrec]");
      if (ur) ur.onclick = () => { const x = st.opts.recommended[ur.dataset.usedrec]; st.choice[ur.dataset.usedrec] = { provider: x.provider, model: x.model }; render(); };
    });
    el.querySelector("[data-rescan]").onclick = () => load(true, true);
    notify();
  }
  async function load(keepChoice, refresh) {
    el.innerHTML = '<div class="hint">Looking at your computer and the AI services you can use…</div>';
    st.opts = await api("/model-options" + (refresh ? "?refresh=true" : ""));
    if (!keepChoice) st.choice = {};
    fill(); render();
  }
  const ready = load(false);
  return {
    ready,
    get: () => ({ ...Object.fromEntries(Object.entries(st.choice).filter(([, v]) => v)), ...(ROLES.some((r) => r.key === "small") && !st.choice.small ? { small: {} } : {}) }),   // small: {} = "same as Recaps & helpers" (clears an earlier choice)
    opts: () => st.opts,
    choice: (role) => st.choice[role] || null,
    // another part of the window changed a model: keep the chooser in step (only if that provider is the chosen one)
    setModel: (role, provider, model) => { const c = st.choice[role]; if (c && c.provider === provider && c.model !== model) { c.model = model; render(); } },
  };
}
