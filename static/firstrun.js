// First run: a short pop-up on the Home screen asking for the few settings that matter before the first story
// (which AI writes it, which AI draws, a spending warning). Everything stays changeable in Settings. Shown once.
async function maybeFirstRun() {
  if (!config || config.setup_done || !$("modal").classList.contains("hidden")) return;
  const done = async (extra) => {   // remember it was answered, so it never asks again
    config.setup_done = true;
    if (extra) Object.assign(config, extra);
    config = await api("/config", { method: "PUT", body: config });
  };
  modal(`<h2>Welcome to AI Storyteller</h2>
    <p class="hint">Three quick choices before your first story. You can change every one of them later in ⚙ Settings, so nothing here is final.</p>
    <h3>1. Who writes your stories, and who draws them?</h3>
    <div class="hint">Pick the AI that narrates, and the one that draws pictures. Online services need an API key (paste it below the choice); a local AI on this computer is free. The recommendation is chosen for this computer. Skip the pictures if you only want text: stories work without them.</div>
    <div id="frPick"></div>
    <h3>2. A spending warning (online services only)</h3>
    <div class="hint">Online AI services charge by use. The app shows an estimate in 💲 Usage and can warn you when a limit is near. Leave it at 0 for no limit.</div>
    <div class="grid2" style="grid-template-columns:1fr 1fr"><div><label>Warn me at this much per month ($)</label><input id="frBud" type="number" min="0" step="1" value="${config.budget_monthly || 0}"></div><div></div></div>
    <h3>3. Where to start</h3>
    <div class="hint">On the Home screen, <b>📦 Import a story</b> adds a ready-made story package (.zip), or press <b>Create a story</b> to write your own. Stories with stats and levels will ask once how you want dice rolls handled.</div>
    <div class="actions"><button id="frSkip">Skip for now</button><button class="primary" id="frSave">Save and start</button></div>`);
  const picker = mountAIChooser($("frPick"), { initial: config.default_models || {}, roles: ["story", "recaps", "images"] });
  $("frSkip").onclick = async () => { closeModal(); try { await done(); } catch (e) { /* asked again next time */ } };
  $("frSave").onclick = async () => {
    $("frSave").disabled = true;
    try {
      await picker.ready;
      const chosen = picker.get();
      config.default_models = await api("/default-models", { method: "PUT", body: { ...(config.default_models || {}), ...chosen } });   // kept in the config copy too, or saving it below would put the old list back
      const extra = { budget_monthly: +$("frBud").value || 0 };
      if (extra.budget_monthly) extra.budget_action = "warn";
      if (chosen.story) extra.active = chosen.story;
      await done(extra);
      closeModal(); await loadModels(); if (!$("home").classList.contains("hidden")) showHome();
    } catch (err) { $("frSave").disabled = false; alert(err.message || err); }
  };
}
