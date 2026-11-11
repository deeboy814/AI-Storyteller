// First start of a story: one pop-up for the choices that shape it (art style, which AI draws, adult content, who you play). It opens before anything else
// that a new story asks (the stat points of a game-like story, the dice question), because those depend on who the player is. Shown once per story; every
// answer can be changed later (🖼 Art, ⚙ Settings, 👤 Player).
const SETUP_AV_FIELDS = [["name", "Name", 1], ["gender", "Gender", 1], ["age", "Age", 1], ["appearance", "Appearance", 2], ["personality", "Personality", 2], ["background", "Background", 3], ["abilities", "Abilities, stats and starting items", 3]];

const storyNeedsSetup = () => !!story && !story.setup_done && !(story.turns || []).length;

async function maybeStorySetup() {
  if (!storyNeedsSetup() || !$("modal").classList.contains("hidden")) return;
  const sid = story.id, st = story.art || {}, own = (st.direction || "").trim();
  let styles = [], lib = [];
  try { styles = await (await fetch("/api/art-styles")).json(); } catch (e) { /* the list is a convenience */ }
  try { lib = await api("/avatars"); } catch (e) { /* the library is optional */ }
  const hasAv = !!(story.avatar && story.avatar.name);
  const modeDefault = hasAv ? "story" : "blank";
  const curModels = { ...(config.default_models || {}), ...(story.models || {}) };
  const styleCards = [
    ...(own ? [{ id: "__own", label: "This story's own style", direction: own, note: oneLine(own, 90) }] : []),
    ...styles.map((x) => ({ id: x.id, label: x.label, direction: x.direction, note: "" })),
    { id: "__custom", label: "Write my own…", direction: "", note: "" },
  ];
  const startStyle = own ? "__own" : (styles.find((x) => x.direction === (config.default_art_direction || "").trim()) || {}).id || (styles[0] || {}).id || "__custom";
  modal(`<h2>Set up “${esc(story.title)}”</h2>
    <p class="hint">Four quick choices before you begin. Every one can be changed later (🖼 Art, ⚙ Settings, 👤 Player), so nothing here is final. Your character comes first because the story's stats and starting items are built around who you are.</p>
    <h3>1. Who are you?</h3>
    <div class="suopts">
      ${hasAv ? `<label class="suopt"><input type="radio" name="suAv" value="story" ${modeDefault === "story" ? "checked" : ""}><span><b>${esc(story.avatar.name)}</b> <small class="hint">the character this story already has</small></span></label>` : ""}
      ${lib.length ? `<label class="suopt"><input type="radio" name="suAv" value="lib"><span><b>One of my saved avatars</b> <select id="suLib" style="margin-left:6px">${lib.map((a) => `<option value="${esc(a.id)}">${esc(a.name || "Unnamed")}</option>`).join("")}</select></span></label>` : ""}
      <label class="suopt"><input type="radio" name="suAv" value="blank" ${modeDefault === "blank" ? "checked" : ""}><span><b>A blank character</b> <small class="hint">write your own; clues from the story show what fits its world</small></span></label>
      <label class="suopt"><input type="radio" name="suAv" value="skip"><span><b>Decide later</b> <small class="hint">uses the plain “you” until you fill in 👤 Player</small></span></label>
    </div>
    <div id="suBlank" class="${modeDefault === "blank" ? "" : "hidden"}">
      <div class="suclue" id="suFit"><span class="hint">Reading the story for clues…</span></div>
      <div class="actions" style="justify-content:flex-start"><button type="button" id="suBuild" title="The helper AI writes the whole sheet from the story prompt, using what you have typed so far. You can edit it.">✨ Fill it in for me</button><span class="hint" id="suBuildNote"></span></div>
      ${SETUP_AV_FIELDS.map(([k, l, r]) => `<div><label>${l}</label>${r === 1 ? `<input id="suav_${k}" placeholder="">` : `<textarea class="short" id="suav_${k}" rows="${r}"></textarea>`}</div>`).join("")}
    </div>
    <h3>2. Art style</h3>
    <div class="hint">How pictures of this story look. Your picture maker (below) follows it as well as it can.</div>
    <div class="sustyles" id="suStyles">${styleCards.map((c) => `<label class="sustyle"><input type="radio" name="suStyle" value="${esc(c.id)}" ${c.id === startStyle ? "checked" : ""}><span><b>${esc(c.label)}</b>${c.note ? `<small class="hint"> ${esc(c.note)}</small>` : ""}</span></label>`).join("")}</div>
    <textarea class="short ${startStyle === "__custom" ? "" : "hidden"}" id="suStyleOwn" rows="2" placeholder="e.g. moody watercolor, muted colors, soft light"></textarea>
    <h3>3. Which AI draws the pictures?</h3>
    <div id="suPick"></div>
    <h3>4. Adult content</h3>
    <label class="chk top"><input type="checkbox" id="suNsfw" ${story.nsfw ? "checked" : ""}><span><b>Allow adult (18+) content</b><span class="hint">Lets the story and its pictures show explicit content between adult characters. Off keeps everything safe for work. Characters are always adults.</span></span></label>
    <div class="actions sticky"><span class="hint" id="suNote"></span><span style="flex:1"></span><button id="suSkip">Skip setup</button><button class="primary" id="suGo">Start the story</button></div>`);

  const picker = mountAIChooser($("suPick"), { initial: curModels, roles: ["images"] });
  const radio = (n) => (document.querySelector(`input[name="${n}"]:checked`) || {}).value;
  const sheetNow = () => Object.fromEntries(SETUP_AV_FIELDS.map(([k]) => [k, $("suav_" + k).value.trim()]));
  const swap = () => { $("suBlank").classList.toggle("hidden", radio("suAv") !== "blank"); };
  document.querySelectorAll('input[name="suAv"]').forEach((r) => r.onchange = swap);
  document.querySelectorAll('input[name="suStyle"]').forEach((r) => r.onchange = () => $("suStyleOwn").classList.toggle("hidden", radio("suStyle") !== "__custom"));

  // clues for a blank sheet: short hints (placeholders) about what fits this world, read from the story prompt
  api(`/stories/${sid}/avatar/clues`, { method: "POST" }).then((r) => {
    if (!$("suFit")) return;
    $("suFit").innerHTML = r.fit ? `<b>What fits this world:</b> ${esc(r.fit)}` : "";
    for (const [k] of SETUP_AV_FIELDS) { const el = $("suav_" + k); if (el && r.hints[k]) el.placeholder = r.hints[k]; }
  }).catch(() => { if ($("suFit")) $("suFit").innerHTML = '<span class="hint">No clues could be read right now. Write what you like, or use “Fill it in for me”.</span>'; });

  $("suBuild").onclick = async (e) => {
    e.target.disabled = true; $("suBuildNote").textContent = "Writing…";
    try {
      const r = await api(`/stories/${sid}/avatar/suggest`, { method: "POST", body: { avatar: sheetNow() } });
      for (const [k] of SETUP_AV_FIELDS) if (k !== "name" && r.fields[k] !== undefined && !$("suav_" + k).value.trim()) $("suav_" + k).value = r.fields[k];
      $("suBuildNote").textContent = "Done. Edit anything you like.";
    } catch (err) { $("suBuildNote").textContent = err.message; }
    e.target.disabled = false;
  };

  const finish = async (skip) => {
    $("suGo").disabled = $("suSkip").disabled = true; $("suNote").textContent = "Saving…";
    try {
      if (skip) { story = await api(`/stories/${sid}/setup`, { method: "POST", body: { done: true } }); }
      else {
        const mode = radio("suAv"), sid2 = radio("suStyle"), card = styleCards.find((c) => c.id === sid2);
        const direction = sid2 === "__custom" ? $("suStyleOwn").value.trim() : card ? card.direction : own;
        if (mode === "lib" && $("suLib") && $("suLib").value) story = await api(`/stories/${sid}/avatar/select`, { method: "POST", body: { id: $("suLib").value } });
        await picker.ready;
        const chosen = picker.get();
        if (chosen.images) story.models = await api(`/stories/${sid}/models`, { method: "PUT", body: { images: chosen.images } });
        const sheet = mode === "blank" ? sheetNow() : null, filled = !!(sheet && Object.values(sheet).some(Boolean));
        const clear = mode === "skip" || (mode === "blank" && !filled);      // "decide later", or a blank sheet left blank: no starter character, the plain "you"
        story = await api(`/stories/${sid}/setup`, { method: "POST", body: { nsfw: $("suNsfw").checked, art_direction: direction, avatar: filled ? sheet : null, clear_avatar: clear, done: true } });
      }
      closeModal(); renderAll(false); applyTheme(config); refreshArtSuggestions();
      return true;
    } catch (err) { $("suNote").textContent = err.message; $("suGo").disabled = $("suSkip").disabled = false; return false; }
  };
  return new Promise((resolve) => {
    const watch = setInterval(() => { if ($("modal").classList.contains("hidden")) { clearInterval(watch); resolve(false); } }, 400);   // closed without answering: it asks again next time
    $("suGo").onclick = async () => { if (await finish(false)) resolve(true); };
    $("suSkip").onclick = async () => { if (await finish(true)) resolve(true); };
  });
}
