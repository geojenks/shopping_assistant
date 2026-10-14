"use strict";

const $ = sel => document.querySelector(sel);
const money = n => "£" + Number(n).toFixed(2);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

const CHAIN = {tesco: "Tesco", sainsburys: "Sainsbury's", aldi: "Aldi", waitrose: "Waitrose", ms: "M&S", coop: "Co-op",
  iceland: "Iceland", morrisons: "Morrisons", asda: "Asda"};
let state = {list: [], due: [], recent: [], catalogue: []};
let who = "";
try { who = localStorage.getItem("who") || ""; } catch (e) { /* private mode */ }

async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  if (res.status === 401) { location.reload(); throw new Error("signed out"); }
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

// --- the list -------------------------------------------------------------

const STATUS_LABEL = {want: "to buy", low: "running low", out: "RUN OUT"};
const NEXT_STATUS = {want: "low", low: "out", out: "want"};

function render() {
  renderShop();
  $("#who").textContent = who ? `I'm ${who}` : "Who are you?";
  $("#catalogue").innerHTML = state.catalogue.map(n => `<option value="${esc(n)}">`).join("");

  $("#due-wrap").hidden = !state.due.length;
  $("#due").innerHTML = state.due.map(d =>
    `<button class="chip" data-add="${esc(d.name)}" data-qty="${d.qty}">${esc(d.name)}</button>`).join("");

  const items = [...state.list].sort((a, b) => (a.category || "~").localeCompare(b.category || "~"));
  $("#list-title").textContent = items.length ? `We need (${items.length})` : "We need";
  $("#empty").hidden = items.length > 0;
  $("#list").innerHTML = items.map(e => `
    <li class="row">
      <button class="tick" data-bought="${esc(e.item_id)}" aria-label="Bought ${esc(e.name)}"><span></span></button>
      <div class="name">
        <strong>${esc(e.name)}</strong>
        <span class="meta">
          <button class="status ${e.status}" data-toggle="${esc(e.item_id)}"
            aria-label="Change between to buy, running low and run out">${STATUS_LABEL[e.status] || "to buy"}</button>
          ${e.who ? " · " + esc(e.who) : ""}
        </span>
      </div>
      <div class="controls">
        <button class="step" data-qty-for="${esc(e.item_id)}" data-delta="-1" aria-label="One fewer">−</button>
        <span class="qty">${e.qty}</span>
        <button class="step" data-qty-for="${esc(e.item_id)}" data-delta="1" aria-label="One more">+</button>
      </div>
    </li>`).join("");

  $("#recent-wrap").hidden = !state.recent.length;
  $("#recent").innerHTML = state.recent.map(r => `
    <li class="row"><div class="name"><strong>${esc(r.name)}</strong></div>
      <button class="ghost" data-undo="${r.purchase_id}">Undo</button></li>`).join("");
  $("#compare").disabled = !items.length;
}

async function refresh() {
  try { state = await api("/api/state"); render(); } catch (e) { /* offline: keep what we have */ }
}

async function act(path, body) {
  try { state = await api(path, body); render(); } catch (e) { alert(e.message); }
}

$("#add").addEventListener("submit", e => {
  e.preventDefault();
  const input = $("#add-text");
  const text = input.value.trim();
  if (!text) return;
  const status = (e.submitter && e.submitter.dataset.status) || "low";
  input.value = "";
  act("/api/add", {text, status, who});
});

document.addEventListener("click", e => {
  const t = e.target.closest("button");
  if (!t) return;
  const item = t.dataset;
  if (item.aisle) { aisle = item.aisle; renderShop(); window.scrollTo(0, 0); }
  else if (item.addId) act("/api/add", {item_id: item.addId, text: item.addId, status: "want", who});
  else if (item.add) act("/api/add", {text: item.add, qty: Number(item.qty) || 1, who});
  else if (item.bought) act("/api/bought", {item_id: item.bought, who});
  else if (item.undo) act("/api/undo", {purchase_id: Number(item.undo)});
  else if (item.toggle) {
    const entry = state.list.find(x => x.item_id === item.toggle);
    act("/api/update", {item_id: item.toggle, status: NEXT_STATUS[entry.status] || "low"});
  } else if (item.qtyFor) {
    const entry = state.list.find(x => x.item_id === item.qtyFor);
    const qty = entry.qty + Number(item.delta);
    if (qty <= 0) act("/api/remove", {item_id: item.qtyFor});
    else act("/api/update", {item_id: item.qtyFor, qty});
  }
});

$("#who").addEventListener("click", () => {
  const name = prompt("Your name, so the other phone can see who added what:", who);
  if (name === null) return;
  who = name.trim();
  try { localStorage.setItem("who", who); } catch (e) { /* fine */ }
  render();
});

// --- finding the best shop --------------------------------------------------

let polling = null;

function showView(name) {
  $("#list-view").hidden = name !== "list";
  $("#shop-view").hidden = name !== "shop";
  $("#plan-view").hidden = name !== "plan";
  $("#bar").hidden = name === "plan";
  document.querySelector(".tabs").hidden = name === "plan";
  $("#tab-list").classList.toggle("on", name === "list");
  $("#tab-shop").classList.toggle("on", name === "shop");
  window.scrollTo(0, 0);
}
const showPlanView = on => showView(on ? "plan" : "list");

// --- browsing the shop --------------------------------------------------------

let catalogue = null;
let aisle = null;

async function loadCatalogue() {
  try { catalogue = await api("/api/catalogue"); } catch (e) { return; }
  if (!aisle && catalogue.aisles.length) aisle = catalogue.aisles[0].name;
  renderShop();
}

function itemCard(it) {
  const entry = state.list.find(e => e.item_id === it.id);
  const price = it.from ? `from ${money(it.from.price)} at ${esc(CHAIN[it.from.chain] || it.from.chain)}` : "";
  const control = entry
    ? `<div class="controls">
         <button class="step" data-qty-for="${esc(it.id)}" data-delta="-1" aria-label="One fewer">−</button>
         <span class="qty">${entry.qty}</span>
         <button class="step" data-qty-for="${esc(it.id)}" data-delta="1" aria-label="One more">+</button></div>`
    : `<button class="add-one" data-add-id="${esc(it.id)}" aria-label="Add ${esc(it.name)}">Add</button>`;
  return `<li class="row shop-row ${entry ? "in" : ""}"><div class="name"><strong>${esc(it.name)}</strong>
    <span class="meta">${price}</span></div>${control}</li>`;
}

function renderShop() {
  if (!catalogue) return;
  const q = $("#shop-search").value.trim().toLowerCase();
  $("#aisles").hidden = !!q;
  $("#aisles").innerHTML = catalogue.aisles.map(a =>
    `<button class="aisle ${a.name === aisle ? "on" : ""}" data-aisle="${esc(a.name)}">${esc(a.name)}</button>`).join("");
  const aisles = q ? catalogue.aisles : catalogue.aisles.filter(a => a.name === aisle);
  const html = aisles.map(a => a.shelves.map(sh => {
    const items = sh.items.filter(it => !q || it.name.toLowerCase().includes(q));
    if (!items.length) return "";
    return `<h2>${esc(q ? `${a.name} · ${sh.name}` : sh.name || a.name)}</h2><ul class="list">${items.map(itemCard).join("")}</ul>`;
  }).join("")).join("");
  $("#shelves").innerHTML = html || (q
    ? `<p class="muted">Nothing called “${esc(q)}”. <button class="ghost" data-add="${esc(q)}">Add “${esc(q)}” to the list</button></p>`
    : "");
}

$("#shop-search").addEventListener("input", renderShop);
$("#tab-list").addEventListener("click", () => { showView("list"); refresh(); });
$("#tab-shop").addEventListener("click", () => { showView("shop"); loadCatalogue(); });

async function compare(refresh) {
  showPlanView(true);
  $("#options").innerHTML = $("#rows").innerHTML = "";
  $("#all-wrap").hidden = true;
  $("#plan-status").textContent = refresh ? "Checking today's prices…" : "Comparing…";
  try { await api("/api/plan", {refresh}); } catch (e) { return planError(e.message); }
  clearInterval(polling);
  polling = setInterval(pollPlan, 1500);
  pollPlan();
}

async function pollPlan() {
  let job;
  try { job = await api("/api/plan"); } catch (e) { return; }
  if (job.state === "running") { $("#plan-status").textContent = job.progress || "Working…"; return; }
  clearInterval(polling);
  if (job.state === "error") return planError(job.error);
  if (job.state === "done") renderPlan(job.result);
}

function planError(message) {
  $("#plan-status").innerHTML = `<span class="error">${esc(message)}</span>`;
}

let lastPlan = null;

function renderPlan(result) {
  lastPlan = result;
  if (!result.options.length) {
    return planError("No way to buy this list with the shops and prices we have. Try “Find the best shop” to fetch prices.");
  }
  const checked = Object.entries(result.checked || {}).map(([c, d]) => `${c} ${String(d).slice(5, 10)}`).join(", ");
  $("#plan-status").innerHTML = (checked ? `Prices checked: ${esc(checked)}` : "") + ((result.failed || []).length
    ? `<br><span class="error">Couldn't check ${esc(result.failed.join(", "))} this time; using their older prices.</span>` : "");
  $("#options").innerHTML = result.options.map((o, i) => `
    <article class="option ${i === 0 ? "best" : ""}">
      <div class="head"><span class="title"><span class="letter">${o.letter}</span>${esc(o.title)}</span>
        <span class="price">${money(o.cash)}</span></div>
      <div class="where">${o.route.length ? esc(o.route.join(" → ")) + ` <span class="muted">(${o.miles} mi)</span>` : ""}
        ${o.deliveries.length ? (o.route.length ? " + " : "") + esc(o.deliveries.join(" + ")) : ""}</div>
      <div class="sum">${esc(o.summary)}</div>
    </article>`).join("") + (result.unavailable.length
      ? `<p class="muted">Not found anywhere: ${esc(result.unavailable.join(", "))}</p>` : "");
  const draw = () => {
    const all = $("#show-all").checked;
    $("#rows").innerHTML = result.rows.filter(r => all || r.differs).map(r => {
      const first = r.cells[0] && r.cells[0].product;
      const row = result.rows.indexOf(r);
      return `<div class="item"><h3>${esc(r.name)}</h3>${r.cells.map((c, i) => c ? `
        <button class="choice ${i > 0 && c.product !== first ? "changed" : ""}" data-row="${row}" data-cell="${i}">
          <span class="letter">${result.options[i].letter}</span>
          <span><span class="store">${esc(c.store)}</span>${c.packs > 1 ? c.packs + "× " : ""}${esc(c.product)}</span>
          <span class="cost">${money(c.cost)}</span></button>` : "").join("")}</div>`;
    }).join("") || `<p class="muted">Every option buys the same things.</p>`;
  };
  $("#all-wrap").hidden = false;
  $("#show-all").onchange = draw;
  draw();
}

// Tap a product in the comparison to say what you think of it.

$("#rows").addEventListener("click", e => {
  const b = e.target.closest(".choice");
  if (!b || !lastPlan) return;
  const row = lastPlan.rows[Number(b.dataset.row)];
  const c = row.cells[Number(b.dataset.cell)];
  const shop = CHAIN[c.chain] || c.chain;
  const actions = [
    ["never-product", `Never buy this one again`],
    ["not-from-chain", `Don't buy ${row.name.toLowerCase()} at ${shop}`],
  ];
  if (c.own_label) actions.push(["no-own-label", `${shop}'s own brand isn't good enough for this`]);
  actions.push(["reset", `Forget my rules for ${row.name.toLowerCase()}`]);
  $("#sheet .sheet-title").innerHTML = `<strong>${esc(c.product)}</strong><br><span class="muted">${esc(c.store)} · ${money(c.cost)}</span>`
    + (c.url ? ` · <a href="${esc(c.url)}" target="_blank" rel="noopener">see it</a>` : "");
  $("#sheet .sheet-actions").innerHTML = actions.map(([a, label]) =>
    `<button value="${a}" class="sheet-action">${esc(label)}</button>`).join("");
  $("#sheet").returnValue = "";
  $("#sheet").onclose = async () => {
    const action = $("#sheet").returnValue;
    if (!action || action === "cancel") return;
    try {
      await api("/api/pref", {action, item_id: row.item_id, key: c.key, chain: c.chain});
      compare(false);  // same prices, new rules
    } catch (err) { alert(err.message); }
  };
  $("#sheet").showModal();
});

$("#compare").addEventListener("click", () => compare(true));
$("#compare-saved").addEventListener("click", () => compare(false));
$("#back").addEventListener("click", () => { showPlanView(false); refresh(); });

// --- keep both phones in step -------------------------------------------------

refresh();
setInterval(() => { if (!document.hidden && $("#plan-view").hidden) refresh(); }, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
