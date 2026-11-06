// Usage & cost: tokens and ESTIMATED dollars by story, job, model and day; editable prices.
async function showUsage(opts = {}) {
  const days = opts.days ?? 30, scope = opts.scope || "all";
  const q = `?days=${days}` + (scope === "story" && story ? `&story=${story.id}` : "");
  const r = await api("/usage" + q);
  const money = (v) => "$" + (v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2));
  const num = (n) => n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? (n / 1e3).toFixed(1) + "k" : String(n);
  const t = r.total, allIn = t.input + t.cache_read + t.cache_write;
  const table = (title, rows) => `<h3>${title}</h3><table class="utab"><tr><th></th><th>Calls</th><th>Tokens</th><th>Est. cost</th></tr>${rows.map((x) =>
    `<tr><td>${esc(x.name)}</td><td>${x.calls}${x.images ? ` (${x.images} pic${x.images > 1 ? "s" : ""})` : ""}</td><td>${num(x.tokens)}</td><td>${money(x.cost)}${x.unpriced ? ` <small title="${x.unpriced} call(s) have no price set">+?</small>` : ""}</td></tr>`).join("") || '<tr><td colspan="4"><i>Nothing yet.</i></td></tr>'}</table>`;
  const maxDay = Math.max(0.0001, ...r.day.map((d) => d.cost));
  const priceRow = (p = {}) => `<tr class="prow"><td><input data-k="match" value="${esc(p.match || "")}" placeholder="model name contains…"></td>
    <td><select data-k="kind"><option value="text" ${p.kind !== "image" ? "selected" : ""}>text</option><option value="image" ${p.kind === "image" ? "selected" : ""}>picture</option></select></td>
    <td><input data-k="input" type="number" step="any" value="${p.input ?? ""}" placeholder="in"></td><td><input data-k="output" type="number" step="any" value="${p.output ?? ""}" placeholder="out"></td>
    <td><input data-k="cache_read" type="number" step="any" value="${p.cache_read ?? ""}" placeholder="cached in"></td><td><input data-k="per_image" type="number" step="any" value="${p.per_image ?? ""}" placeholder="per pic"></td></tr>`;
  modal(`<h2>Usage &amp; cost</h2>
    <div class="grid2"><div><label>Period</label><select id="uDays">${[[1, "Last 24 hours"], [7, "Last 7 days"], [30, "Last 30 days"], [0, "All time"]].map(([d, n]) => `<option value="${d}" ${d === days ? "selected" : ""}>${n}</option>`).join("")}</select></div>
    <div><label>Show</label><select id="uScope"><option value="all">All stories</option>${story ? `<option value="story" ${scope === "story" ? "selected" : ""}>Only “${esc(story.title)}”</option>` : ""}</select></div><div></div></div>
    <div class="hint" id="uBudget"></div>
    <div class="ubig"><b>${money(t.cost)}</b> estimated${t.unpriced ? ` <small>(+ ${t.unpriced} call${t.unpriced > 1 ? "s" : ""} with no price set)</small>` : ""}<br>
      <small>${t.calls} calls · ${num(allIn)} tokens in (${allIn ? Math.round(100 * t.cache_read / allIn) : 0}% read from cache) · ${num(t.output)} out · ${t.images} picture${t.images === 1 ? "" : "s"}</small></div>
    <div class="ubars">${r.day.map((d) => `<div title="${d.name}: ${money(d.cost)}"><i style="height:${Math.max(2, 100 * d.cost / maxDay)}%"></i><small>${d.name.slice(5)}</small></div>`).join("")}</div>
    ${table("By story", r.story)}${table("By job", r.purpose)}${table("By model", r.model)}
    <p class="hint">Costs are estimates from the prices below, not your bill. Local models and ComfyUI cost nothing. Usage is logged from now on; calls made before this feature existed aren't included.</p>
    <details><summary>Prices ($ per million tokens; per picture for pictures)</summary>
      <p class="hint">Built in (Claude): ${r.default_prices.map((p) => `${esc(p.match)} ${p.input}/${p.output}`).join(" · ")}. Add rows for ChatGPT, Gemini, Grok and picture makers, or to override a built-in price. The first matching row wins.</p>
      <table class="utab" id="uPrices"><tr><th>Model contains</th><th>Kind</th><th>In</th><th>Out</th><th>Cached in</th><th>Per pic</th></tr>${(r.prices || []).map(priceRow).join("")}</table>
      <div class="actions" style="justify-content:flex-start"><button id="uAdd">+ Add price</button><button class="primary" id="uSave">Save prices</button></div></details>
    <div class="actions"><button id="mClose">Close</button></div>`);
  $("mClose").onclick = closeModal;
  api("/budget").then((b) => { const p = (k, t) => (b[k].limit > 0 ? `${t}: $${b[k].spent.toFixed(2)} of $${b[k].limit.toFixed(2)} (${b[k].pct}%)` : `${t}: $${b[k].spent.toFixed(2)}, no limit`); $("uBudget").innerHTML = `${p("daily", "Today")} · ${p("monthly", "This month")} <button id="uBudSet">Spending limits…</button>`; $("uBudSet").onclick = () => showSettings("Memory"); }).catch(() => {});
  $("uDays").onchange = $("uScope").onchange = () => showUsage({ days: +$("uDays").value, scope: $("uScope").value });
  $("uAdd").onclick = () => $("uPrices").insertAdjacentHTML("beforeend", priceRow());
  $("uSave").onclick = async () => {
    const prices = [...document.querySelectorAll(".prow")].map((tr) => Object.fromEntries([...tr.querySelectorAll("[data-k]")].map((i) => [i.dataset.k, i.value])));
    await api("/prices", { method: "PUT", body: { prices } });
    showUsage({ days, scope });
  };
}
