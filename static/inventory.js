// Inventory kept by the app: item cards (quantity, effect, equipped). The window to switch it on and edit the list; the side panel's Items tab shows the cards.
function invOf(s) { return (s && s.inventory) || { on: false, items: [] }; }
async function saveInventory(body) {
  const r = await api(`/stories/${story.id}/inventory`, { method: "PUT", body });
  story.inventory = r.inventory; renderSidebar(); return r.inventory;
}
function invCardsHtml() {   // for the side panel's Items tab
  const inv = invOf(story); if (!inv.on) return "";
  const last = inv.last && inv.last.turn === story.turns.length ? inv.last.changes || [] : [];
  const chg = (n) => last.filter((c) => c.name === n);
  const gone = last.filter((c) => c.d < 0 && !inv.items.some((i) => i.name === c.name));
  return `<div class="invhead"><b>Inventory</b> <small>${inv.items.length} item${inv.items.length === 1 ? "" : "s"}</small> <a href="#" class="invopen" title="Add, edit or remove items">✎ Manage</a></div>` +
    (inv.items.length ? inv.items.map((it) => {
      const c = chg(it.name), d = c.reduce((a, x) => a + (x.d || 0), 0);
      return `<div class="itemcard${c.length ? " chg" : ""}${it.equipped ? " eq" : ""}" data-id="${esc(it.id)}"><div class="ithead"><b>${esc(it.name)}</b>${it.qty !== 1 ? ` <span class="itqty">×${it.qty}</span>` : ""}${d ? ` <span class="${d > 0 ? "up" : "down"}">${d > 0 ? "+" : ""}${d}</span>` : ""}${it.equipped ? ` <span class="iteq">equipped</span>` : ""}</div>
        ${it.effect ? `<div class="iteffect">${esc(it.effect)}</div>` : ""}
        <div class="itbtns"><button class="itUse" title="Write a move that uses this item (you can change it before sending)">Use</button><button class="itEq" title="The app marks it; the narrator is told from your next move">${it.equipped ? "Unequip" : "Equip"}</button></div></div>`;
    }).join("") : `<div class="side-empty">You are carrying nothing.</div>`) +
    (gone.length ? `<div class="side-empty">Used up or lost this turn: ${gone.map((c) => esc(c.name)).join(", ")}</div>` : "");
}
function invBind(el) {
  const inv = invOf(story);
  el.querySelectorAll(".invopen").forEach((a) => a.onclick = (ev) => { ev.preventDefault(); showInventory(); });
  el.querySelectorAll(".itemcard").forEach((card) => {
    const it = inv.items.find((x) => x.id === card.dataset.id); if (!it) return;
    card.querySelector(".itUse").onclick = () => { const t = $("input"); t.value = (t.value.trim() ? t.value.trim() + " " : "") + `I use the ${it.name}.`; if (typeof autosize === "function") autosize(); t.focus(); t.setSelectionRange(t.value.length, t.value.length); };
    card.querySelector(".itEq").onclick = async () => { try { await saveInventory({ items: inv.items.map((x) => x.id === it.id ? { ...x, equipped: !x.equipped } : x) }); } catch (e) { alert(e.message); } };
  });
}
function showInventory() {
  if (!story) return;
  const inv = invOf(story);
  let rows = inv.items.map((x) => ({ ...x }));
  modal(`<h2>🎒 Inventory</h2>
    <label class="chk top"><input type="checkbox" id="invOn" ${inv.on ? "checked" : ""}><span><b>Let the app keep the inventory for this story</b><span class="hint">Items become cards with a quantity and an effect. The narrator reports what you gain, use up or lose in a hidden line, and the app does the counting. The narrator is given the true list every move, so using something you do not have fails in the story. Off by default; turning it on takes over any item list the story already kept.</span></span></label>
    <div id="invRows"></div>
    <div class="actions" style="justify-content:flex-start"><button id="invAdd">＋ Add item</button><button id="invScan" title="One small helper-AI request">Read my items from the story</button><span class="hint" id="invMsg"></span></div>
    <div class="actions"><button id="mClose">Cancel</button><button class="primary" id="mSave">Save</button></div>`);
  const read = () => { rows = [...document.querySelectorAll("#invRows .invrow:not(.invhdr)")].map((r) => ({ id: r.dataset.id || undefined, name: r.querySelector(".iN").value, qty: +r.querySelector(".iQ").value || 0, effect: r.querySelector(".iE").value, equipped: r.querySelector(".iW").checked })); };
  const draw = () => {
    $("invRows").innerHTML = rows.length ? `<div class="invrow invhdr"><span>Item</span><span>How many</span><span>Effect or use</span><span>Equipped</span><span></span></div>` + rows.map((x, i) => `<div class="invrow" data-id="${esc(x.id || "")}"><input class="iN" value="${esc(x.name || "")}" placeholder="Item name"><input class="iQ" type="number" min="0" value="${x.qty ?? 1}"><input class="iE" value="${esc(x.effect || "")}" placeholder="e.g. restores 20 HP"><input class="iW" type="checkbox" ${x.equipped ? "checked" : ""}><button class="iX" data-i="${i}" title="Remove">×</button></div>`).join("")
      : `<p class="hint">No items yet. Add them by hand, or let the helper AI read them from the story.</p>`;
    document.querySelectorAll("#invRows .iX").forEach((b) => b.onclick = () => { read(); rows.splice(+b.dataset.i, 1); draw(); });
  };
  draw();
  $("mClose").onclick = closeModal;
  $("invAdd").onclick = () => { read(); rows.push({ name: "", qty: 1, effect: "", equipped: false }); draw(); const ins = document.querySelectorAll("#invRows .iN"); ins[ins.length - 1].focus(); };
  $("invScan").onclick = async () => {
    const b = $("invScan"); b.disabled = true; $("invMsg").textContent = "Reading the story…";
    try {
      const r = await api(`/stories/${story.id}/inventory/scan`, { method: "POST" }); read();
      const key = (n) => String(n).toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().replace(/s$/, "");
      let added = 0;
      for (const it of r.items) { if (!rows.some((x) => key(x.name) === key(it.name))) { rows.push({ ...it, id: undefined }); added++; } }
      draw(); $("invMsg").textContent = added ? `Added ${added} item${added === 1 ? "" : "s"} found in the story. Check them, then Save.` : "Nothing new was found.";
      if (added) $("invOn").checked = true;
    } catch (e) { $("invMsg").textContent = e.message; }
    b.disabled = false;
  };
  $("mSave").onclick = async () => {
    read();
    try { await saveInventory({ on: $("invOn").checked }); await saveInventory({ items: rows.filter((x) => x.name.trim() && x.qty > 0) }); closeModal(); }
    catch (e) { alert(e.message); }
  };
}
