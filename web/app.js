"use strict";
/* Стенд ШЕКАРЫ: одностраничное приложение поверх живого API (online/api.py + online/sim.py). */

const $ = (s, r = document) => r.querySelector(s);
const view = $("#view");
const NS = "http://www.w3.org/2000/svg";
const S = { state: null, seq: 0, events: [], bank: null, account: null, role: "", page: "network", timer: null, results: null, cfg: {} };
const store = { get: k => { try { return sessionStorage.getItem(k) || localStorage.getItem(k) || ""; } catch (e) { return ""; } },
  set: (k, v, persist) => { try { (persist ? localStorage : sessionStorage).setItem(k, v); } catch (e) { } } };

const fmtNum = v => v == null ? "—" : Number(v).toLocaleString("ru-RU");
const fmt3 = v => v == null ? "—" : Number(v).toFixed(3).replace(".", ",");
const pct = (v, d = 0) => v == null || isNaN(v) ? "—" : (v * 100).toFixed(d).replace(".", ",") + "%";
const tm = iso => iso ? iso.slice(5, 16).replace("T", " ").replace("-", ".") : "—";
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const amount = (a, c) => `${Number(a).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ${c ? esc(curShort(c)) : ""}`;
const curShort = c => ({ "US Dollar": "USD", "Euro": "EUR", "Yuan": "CNY", "Rupee": "INR", "UK Pound": "GBP", "Yen": "JPY", "Swiss Franc": "CHF",
  "Australian Dollar": "AUD", "Canadian Dollar": "CAD", "Ruble": "RUB", "Mexican Peso": "MXN", "Brazil Real": "BRL", "Saudi Riyal": "SAR", "Shekel": "ILS", "Bitcoin": "BTC" }[c] || c);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const el = (t, a = {}, p) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); if (p) p.appendChild(e); return e; };

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(S.role ? { "X-Role": S.role } : {}) };
  const adm = store.get("shekara-admin"), op = store.get("shekara-operator");
  if (adm) headers["X-Admin-Token"] = adm;
  if (op) headers["X-Operator-Token"] = op;
  const r = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || `${r.status}`);
  return body;
}
function toast(msg) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => t.hidden = true, 3500); }
function scoreCell(v, thr) {
  if (v == null) return '<span class="muted">—</span>';
  const hi = thr != null && v >= thr;
  return `<span class="score"><i><b class="${hi ? "hi" : ""}" style="width:${Math.max(3, v * 100)}%"></b></i><span class="mono">${fmt3(v)}</span></span>`;
}

/* ---------- панель времени ---------- */
async function pollState() {
  try {
    const s = await api("/v1/sim/state");
    if (s.seq < S.seq) { S.seq = 0; S.events = []; }  // стенд начал проигрывание заново
    S.state = s;
    $("#clock").innerHTML = `${s.clock.slice(8, 10)}.${s.clock.slice(5, 7)} ${s.clock.slice(11, 16)}<small>${s.running ? "идёт" : "пауза"}</small>`;
    $("#prog").style.width = `${(s.progress * 100).toFixed(1)}%`;
    $("#play").textContent = s.running ? "Пауза" : (s.progress >= 1 ? "Готово" : "Запустить");
    $("#play").disabled = s.progress >= 1;
    document.querySelectorAll("#speed button").forEach(b => b.classList.toggle("on", +b.dataset.s === s.speed));
    if (!$("#mode").children.length) {
      $("#mode").innerHTML = Object.entries(s.modes).map(([k, v]) => `<button type="button" data-m="${k}">${v}</button>`).join("");
      $("#mode").querySelectorAll("button").forEach(b => b.onclick = () => control({ mode: b.dataset.m }));
    }
    document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.m === s.mode));
  } catch (e) { $("#clock").textContent = "нет связи с API"; }
}
async function pollEvents() {
  try {
    const r = await api(`/v1/sim/events?since=${S.seq}`);
    if (r.events.length) { S.seq = r.events[r.events.length - 1].seq; S.events = [...S.events, ...r.events].slice(-400); onEvents(r.events); }
  } catch (e) { /* связь восстановится на следующем опросе */ }
}
async function control(body) { S.state = await api("/v1/sim/control", { method: "POST", body: JSON.stringify(body) }); pollState(); }
$("#play").onclick = () => control({ action: S.state?.running ? "pause" : "play" });
$("#reset").onclick = async () => { await control({ action: "reset" }); S.seq = 0; S.events = []; render(); };
document.querySelectorAll("#speed button").forEach(b => b.onclick = () => control({ speed: +b.dataset.s }));
$("#theme").onclick = () => {
  const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = cur === "dark" ? "light" : "dark";
  try { localStorage.setItem("shekara-theme", document.documentElement.dataset.theme); } catch (e) { }
  render();
};
try { const t = localStorage.getItem("shekara-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) { }

/* ---------- события ---------- */
const EV = {
  alert: e => [`<span class="chip c-alert">алерт</span>`, `банк <b>${e.bank}</b>: счёт <span class="mono">${e.account}</span>, балл ${fmt3(e.score)} · входящий из ${e.from_bank}${e.laundering ? ' · <span class="chip c-risk">в разметке — отмывание</span>' : ""}`],
  incident: e => [`<span class="chip c-risk">инцидент</span>`, `банк <b>${e.bank}</b> открыл ${e.incident_kind === "soft" ? "мягкий" : "подтверждённый"} инцидент <span class="mono">${e.incident}</span>`],
  hop: e => [`<span class="chip c-acc">шаг</span>`, `${e.bank} → ${e.to_bank}: ссылка на перевод по инциденту <span class="mono">${e.incident}</span> · ${e.result === "ok" ? "перевод найден" : esc(e.result)}`],
  night: e => [`<span class="chip c-ok">ночь</span>`, esc(e.text)],
  query: e => [`<span class="chip c-acc">запрос</span>`, `${e.bank} спросил ${e.to_bank} о контрагенте перевода · ${e.result === "ok" ? "выпуск получен" : esc(e.result)}`],
  mode: e => [`<span class="chip c-mute">режим</span>`, esc(e.text)],
};
function evRow(e, fresh) { const [chip, text] = (EV[e.kind] || (() => [e.kind, ""]))(e); return `<div class="ev${fresh ? " new" : ""}"><span class="tm">${tm(e.t)}</span><span>${chip}</span><span>${text}</span></div>`; }
function onEvents(evs) {
  const f = $("#feed");
  if (f) { f.insertAdjacentHTML("afterbegin", evs.slice().reverse().map(e => evRow(e, true)).join("")); while (f.children.length > 200) f.lastChild.remove(); }
  if (S.page === "network") pulse(evs);
}

/* ---------- маршруты ---------- */
function route() {
  const [, page = "network", arg] = location.hash.split("/");
  S.page = page;
  if (page === "bank" && arg) S.bank = decodeURIComponent(arg);
  document.querySelectorAll(".side a").forEach(a => a.classList.toggle("on", a.dataset.r === page));
  render();
}
window.addEventListener("hashchange", route);
function render() {
  clearInterval(S.timer);
  const pages = { network: pageNetwork, bank: pageBank, operator: pageOperator, audit: pageAudit, results: pageResults };
  (pages[S.page] || pageNetwork)();
}

/* ---------- Сеть ---------- */
function kpis(c) {
  const caught = c.laundering_in ? c.laundering_alerted / c.laundering_in : null;
  return `<div class="kpis">
    <div class="kpi"><span>Переводов обработано</span><b>${fmtNum(c.transfers || 0)}</b></div>
    <div class="kpi"><span>Алертов при зачислении</span><b>${fmtNum(c.alerts || 0)}</b><em>${fmtNum(c.alerts_laundering || 0)} — отмывание по разметке</em></div>
    <div class="kpi"><span>Входящее отмывание поймано</span><b>${pct(caught)}</b><em>${fmtNum(c.laundering_alerted || 0)} из ${fmtNum(c.laundering_in || 0)} межбанковских</em></div>
    <div class="kpi"><span>Инцидентов</span><b>${fmtNum((c.incidents_soft || 0) + (c.incidents_confirmed || 0))}</b><em>шагов по ссылке: ${fmtNum(c.hops || 0)}</em></div>
    <div class="kpi"><span>Ночных выпусков</span><b>${fmtNum(c.releases_delivered || 0)}</b><em>запросов днём: ${fmtNum(c.queries || 0)}</em></div>
  </div>`;
}
let netPos = new Map();
async function pageNetwork() {
  view.innerHTML = `<div class="head"><h1>Сеть банков-участников</h1><span class="muted">Пунктир — граница банка. Вспышка — алерт, линия — шаг инцидента по ссылке.</span></div>
    <div id="kpis"></div>
    <div class="grid g2"><div class="panel net"><div class="ph"><h2>30 банков-участников</h2><span class="muted" id="netnote"></span></div><div id="net"></div></div>
    <div class="panel"><div class="ph"><h2>Лента протокола</h2><span class="muted">хаб видит только метаданные</span></div><div class="feed" id="feed">${S.events.slice().reverse().map(e => evRow(e)).join("") || '<div class="empty">Нажмите «Запустить» — переводы пойдут по времени.</div>'}</div></div></div>
    <div class="panel"><div class="ph"><h2>Банки</h2><span class="muted">бюджет алертов — 0,1% активных счетов в сутки</span></div><div class="scroll"><table class="t" id="banks"></table></div></div>`;
  drawNet();
  const tick = async () => {
    if (S.state) $("#kpis").innerHTML = kpis(S.state.counters);
    try {
      const r = await api("/v1/banks");
      $("#banks").innerHTML = `<tr><th>Банк</th><th class="r">Бюджет</th><th class="r">В очереди сегодня</th><th class="r">Инцидентов открыл</th><th class="r">Порог алерта</th></tr>` +
        r.banks.map(b => `<tr class="click" data-b="${b.bank}"><td><b>${b.bank}</b></td><td class="r num">${b.budget}</td><td class="r num">${b.queue}</td><td class="r num">${b.incidents}</td><td class="r mono">${b.threshold == null ? '<span class="muted">нет данных валидации</span>' : fmt3(b.threshold)}</td></tr>`).join("");
      $("#banks").querySelectorAll("tr.click").forEach(tr => tr.onclick = () => location.hash = `#/bank/${tr.dataset.b}`);
    } catch (e) { }
  };
  tick(); S.timer = setInterval(tick, 1500);
}
function drawNet() {
  const host = $("#net"); if (!host || !S.state) return;
  const banks = S.state.participants, w = 680, h = 560, cx = w / 2, cy = h / 2, R = 236;
  const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": "Банки-участники" });
  el("circle", { cx, cy, r: R + 34, fill: "none", stroke: css("--line"), "stroke-dasharray": "4 6" }, svg);
  el("g", { id: "hops" }, svg);
  netPos = new Map();
  banks.forEach((b, i) => {
    const a = -Math.PI / 2 + i / banks.length * Math.PI * 2, x = cx + R * Math.cos(a), y = cy + R * Math.sin(a);
    netPos.set(b, { x, y });
    const g = el("g", { class: "node", "data-b": b, tabindex: 0, role: "link" }, svg); g.style.cursor = "pointer";
    el("circle", { cx: x, cy: y, r: 21, fill: css("--panel"), stroke: css("--ink-3"), "stroke-width": 1.5, class: "ring" }, g);
    const t = el("text", { x, y: y + 4, "text-anchor": "middle", "font-size": 10.5, "font-family": "JetBrains Mono, monospace", fill: css("--ink") }, g); t.textContent = b;
    g.onclick = () => location.hash = `#/bank/${b}`;
  });
  const hub = el("g", {}, svg);
  el("rect", { x: cx - 58, y: cy - 30, width: 116, height: 60, rx: 12, fill: css("--side"), stroke: css("--accent") }, hub);
  const t1 = el("text", { x: cx, y: cy - 4, "text-anchor": "middle", fill: "#fff", "font-size": 14, "font-weight": 700, "font-family": "Unbounded, sans-serif" }, hub); t1.textContent = "ХАБ";
  const t2 = el("text", { x: cx, y: cy + 15, "text-anchor": "middle", fill: "#9FB2B8", "font-size": 10.5, "font-family": "Golos Text, sans-serif" }, hub); t2.textContent = "журнал · подпись";
  host.innerHTML = ""; host.appendChild(svg);
  $("#netnote").textContent = `выпуски: ${S.state.release.level}, σ = ${String(S.state.release.sigma).replace(".", ",")}`;
}
function pulse(evs) {
  const svg = $("#net svg"); if (!svg) return;
  for (const e of evs.slice(-30)) {
    if (e.kind === "alert" && netPos.has(e.bank)) {
      const p = netPos.get(e.bank), c = el("circle", { cx: p.x, cy: p.y, r: 22, fill: "none", stroke: e.laundering ? css("--risk") : css("--amber"), "stroke-width": 3 }, svg);
      c.animate?.([{ r: 22, opacity: 1 }, { r: 44, opacity: 0 }], { duration: 1400 }); setTimeout(() => c.remove(), 1400);
    }
    if (e.kind === "hop" && netPos.has(e.bank) && netPos.has(e.to_bank)) {
      const a = netPos.get(e.bank), b = netPos.get(e.to_bank), w = svg.viewBox.baseVal.width / 2, h = svg.viewBox.baseVal.height / 2;
      const p = el("path", { d: `M${a.x},${a.y} Q${w},${h} ${b.x},${b.y}`, fill: "none", stroke: css("--accent"), "stroke-width": 3 }, $("#hops", svg) || svg);
      setTimeout(() => p.remove(), 6000);
    }
  }
}

/* ---------- Кабинет банка ---------- */
async function pageBank() {
  const banks = S.state?.participants || [];
  if (!S.bank || !banks.includes(S.bank)) S.bank = banks[0];
  view.innerHTML = `<div class="head"><h1>Кабинет банка ${esc(S.bank)}</h1>
      <div class="selector"><label for="bsel" class="muted">Войти как банк</label><select id="bsel">${banks.map(b => `<option ${b === S.bank ? "selected" : ""}>${b}</option>`).join("")}</select></div></div>
    <div class="note">Вы видите только переводы клиентов банка ${esc(S.bank)} и баллы, которые банк считает сам. О счёте в другом банке — только выпуск, присланный по ссылке на реальный перевод. Столбец «разметка» — истина AMLworld для проверки стенда; банку она неизвестна.</div>
    <div class="grid g2e">
      <div class="panel"><div class="ph"><h2>Очередь алертов на сегодня</h2><span class="muted" id="qmeta"></span></div><div class="scroll"><table class="t" id="queue"></table></div></div>
      <div class="panel" id="card"><div class="ph"><h2>Карточка счёта</h2></div><div class="empty">Выберите счёт в очереди или в ленте.</div></div>
    </div>
    <div class="panel"><div class="ph"><h2>Лента переводов банка</h2><span class="muted">«Запросить выпуск» — спросить банк-контрагента о счёте на другом конце этого перевода</span></div>
      <div id="qres"></div><div class="scroll"><table class="t" id="bfeed"></table></div></div>`;
  $("#bsel").onchange = e => location.hash = `#/bank/${e.target.value}`;
  const tick = async () => {
    try {
      const [q, f] = await Promise.all([api(`/v1/banks/${S.bank}/queue`), api(`/v1/banks/${S.bank}/feed`)]);
      const thr = q.overview.threshold;
      $("#qmeta").textContent = `${q.queue.length} из ${q.overview.budget} · порог ${fmt3(thr)}`;
      $("#queue").innerHTML = q.queue.length ? `<tr><th>Счёт</th><th>Балл</th><th>Пришло из</th><th class="r">Сумма</th><th>Разметка</th></tr>` +
        q.queue.map(a => `<tr class="click" data-a="${a.account}"><td class="mono">${a.account}${a.incident_flags.length ? ' <span class="chip c-risk">инцидент</span>' : ""}</td><td>${scoreCell(a.score, thr)}</td><td>${a.from_bank ?? "—"}</td><td class="r num">${a.amount ? amount(a.amount, a.currency) : "—"}</td><td>${a.laundering ? '<span class="chip c-risk">отмывание</span>' : '<span class="chip c-mute">нет</span>'}</td></tr>`).join("")
        : `<tr><td class="empty">Сегодня алертов пока нет.</td></tr>`;
      $("#queue").querySelectorAll("tr.click").forEach(tr => tr.onclick = () => openCard(tr.dataset.a));
      $("#bfeed").innerHTML = `<tr><th>Время</th><th></th><th>Свой счёт</th><th>Банк контрагента</th><th class="r">Сумма</th><th>Балл при зачислении</th><th></th></tr>` +
        f.feed.map(r => {
          const partner = (S.state?.participants || []).includes(r.cp_bank) && r.cp_bank !== S.bank;
          return `<tr><td class="mono">${tm(r.t)}</td><td>${r.side === "in" ? '<span class="chip c-acc">вход</span>' : '<span class="chip c-mute">выход</span>'}</td>
            <td class="mono"><a href="#" data-a="${r.account}" class="acc">${r.account}</a></td><td>${r.cp_bank}${partner ? ' <span class="chip c-ok">участник</span>' : ""}</td>
            <td class="r num">${amount(r.amount, r.currency)}</td><td>${r.side === "in" ? scoreCell(r.score, thr) : '<span class="muted">—</span>'}</td>
            <td>${partner ? `<button class="btn" data-q="${r.row_no}" type="button">Запросить выпуск</button>` : ""}</td></tr>`;
        }).join("");
      $("#bfeed").querySelectorAll("a.acc").forEach(a => a.onclick = ev => { ev.preventDefault(); openCard(a.dataset.a); });
      $("#bfeed").querySelectorAll("button[data-q]").forEach(b => b.onclick = () => queryRelease(+b.dataset.q));
      if (S.account) openCard(S.account, true);
    } catch (e) { view.insertAdjacentHTML("afterbegin", `<div class="result err">${esc(e.message)}</div>`); }
  };
  tick(); S.timer = setInterval(tick, 2000);
}
async function queryRelease(row) {
  try {
    const r = await api(`/v1/banks/${S.bank}/transfers/${row}/query`, { method: "POST" });
    if (r.error) { $("#qres").innerHTML = `<div class="result err">Отказ: ${esc(r.text || r.error)}. Ответ одинаков для неверной, чужой и ещё не зачисленной ссылки.</div>`; return; }
    const v = r.release?.value;
    $("#qres").innerHTML = `<div class="result">Банк ${r.to_bank} ответил: выпуск ${r.release ? `${r.release.level} от ${r.release.date} — балл <b class="mono">${Array.isArray(v) ? v.map(fmt3).join(", ") : fmt3(v)}</b>` : "ещё не рассылался"} · псевдоним счёта <span class="mono">${r.pseudonym.slice(0, 16)}…</span> · дневная квота на пару: ${r.quota}</div>`;
    toast(`Запрос к банку ${r.to_bank} записан в журнал хаба`);
  } catch (e) { toast(e.message); }
}
async function openCard(acc, silent) {
  S.account = acc;
  try {
    const c = await api(`/v1/banks/${S.bank}/accounts/${encodeURIComponent(acc)}`);
    $("#card").innerHTML = `<div class="ph"><h2>Счёт <span class="mono">${esc(acc)}</span></h2>
        <button class="btn warn" id="incbtn" type="button">Открыть подтверждённый инцидент</button></div>
      <div class="kv"><span>Переводов к этому моменту</span><span class="num">${c.total}</span><span>Флаги инцидентов</span><span>${c.incident_flags.length ? c.incident_flags.map(i => `<span class="chip c-risk">${esc(i)}</span>`).join(" ") : "нет"}</span></div>
      <div class="scroll" style="max-height:300px"><table class="t"><tr><th>Время</th><th></th><th>Контрагент</th><th class="r">Сумма</th><th>Выпуск о контрагенте</th></tr>
      ${c.transfers.map(t => `<tr><td class="mono">${tm(t.t)}</td><td>${t.side === "in" ? "вход" : "выход"}</td><td><span class="mono">${t.cp_bank}</span> · <span class="mono">${t.cp_account}</span></td><td class="r num">${amount(t.amount)}</td>
        <td>${t.release ? `<span class="chip c-acc">${t.release.level} ${fmt3(Array.isArray(t.release.value) ? t.release.value[0] : t.release.value)}</span>` : t.partner ? '<span class="muted">нет выпуска</span>' : '<span class="muted">банк вне ШЕКАРЫ</span>'}</td></tr>`).join("")}</table></div>`;
    $("#incbtn").onclick = async () => {
      const r = await api(`/v1/banks/${S.bank}/accounts/${encodeURIComponent(acc)}/incident`, { method: "POST", body: JSON.stringify({ kind: "confirmed" }) });
      toast(r.incident_id ? `Инцидент ${r.incident_id} открыт; шаги уйдут по исходящим переводам счёта` : `Отказ: ${r.error}`);
    };
  } catch (e) { if (!silent) toast(e.message); }
}

/* ---------- Оператор ---------- */
async function pageOperator() {
  view.innerHTML = `<div class="head"><h1>Оператор · антифрод-центр</h1>
      <div class="selector"><span class="muted">Роль:</span><div class="seg"><button type="button" id="r0" class="${S.role ? "" : "on"}">Банк</button><button type="button" id="r1" class="${S.role ? "on" : ""}">Оператор</button></div></div></div>
    <div class="note">Дерево цепочки с суммами доступно только роли «оператор». По мягкому инциденту оператор видит лишь факт и число звеньев.</div>
    ${S.cfg.operator_token_required ? `<div class="selector"><label for="optok" class="muted">Токен оператора</label><input id="optok" type="password" value="${esc(store.get("shekara-operator"))}" style="border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:6px 10px"><button class="btn" id="opsave" type="button">Войти</button></div>` : ""}
    <div class="grid g2e"><div class="panel"><h2>Инциденты</h2><div class="scroll"><table class="t" id="incs"></table></div></div>
    <div class="panel" id="treep"><h2>Дерево цепочки</h2><div class="empty">Выберите инцидент.</div></div></div>`;
  if ($("#opsave")) $("#opsave").onclick = () => { store.set("shekara-operator", $("#optok").value); S.role = "operator"; render(); };
  $("#r0").onclick = () => { S.role = ""; render(); };
  $("#r1").onclick = () => { S.role = "operator"; render(); };
  const tick = async () => {
    try {
      const r = await api("/v1/operator/incidents");
      $("#incs").innerHTML = r.incidents.length ? `<tr><th>Инцидент</th><th>Тип</th><th>Банк</th><th>Открыт</th><th class="r">Шагов</th></tr>` +
        r.incidents.map(i => `<tr class="click" data-i="${i.id}"><td class="mono">${i.id}</td><td>${i.kind === "soft" ? '<span class="chip c-alert">мягкий</span>' : '<span class="chip c-risk">подтверждённый</span>'}</td><td>${i.bank}</td><td class="mono">${tm(i.t)}</td><td class="r num">${i.hops}</td></tr>`).join("")
        : `<tr><td class="empty">Инцидентов пока нет.</td></tr>`;
      $("#incs").querySelectorAll("tr.click").forEach(tr => tr.onclick = () => showTree(tr.dataset.i));
    } catch (e) { $("#incs").innerHTML = `<tr><td><div class="result err">${esc(e.message)}</div></td></tr>`; }
  };
  tick(); S.timer = setInterval(tick, 2500);
}
async function showTree(id) {
  try {
    const t = await api(`/v1/operator/incidents/${id}/full-tree`);
    if (t.kind === "soft") { $("#treep").innerHTML = `<h2>Инцидент <span class="mono">${id}</span></h2><div class="note">Мягкий инцидент: оператору видно только число звеньев — ${t.links}. Суммы и счета остаются у банков.</div>`; return; }
    const edges = t.edges || [];
    const key = n => n.join("|"), nodes = [...new Map(edges.flatMap(e => [e.from, e.to]).map(n => [key(n), n])).values()];
    const depth = new Map(nodes.map(n => [key(n), 0]));
    for (let k = 0; k < nodes.length; k++) for (const e of edges) depth.set(key(e.to), Math.max(depth.get(key(e.to)), depth.get(key(e.from)) + 1));
    const cols = new Map(); nodes.forEach(n => { const d = depth.get(key(n)); cols.set(d, [...(cols.get(d) || []), n]); });
    const rows = Math.max(1, ...[...cols.values()].map(c => c.length)), w = 640, h = 60 + rows * 70;
    const svg = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": "Дерево цепочки" }), pos = new Map();
    for (const [d, list] of cols) list.forEach((n, i) => pos.set(key(n), { x: 70 + d * ((w - 140) / Math.max(1, cols.size - 1)), y: h / 2 + (i - (list.length - 1) / 2) * 70 }));
    for (const e of edges) {
      const a = pos.get(key(e.from)), b = pos.get(key(e.to));
      el("path", { d: `M${a.x + 48},${a.y} C${(a.x + b.x) / 2},${a.y} ${(a.x + b.x) / 2},${b.y} ${b.x - 48},${b.y}`, fill: "none", stroke: css("--accent"), "stroke-width": 2.5 }, svg);
      const tx = el("text", { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 8, "text-anchor": "middle", "font-size": 11, "font-family": "JetBrains Mono, monospace", fill: css("--ink-2") }, svg);
      tx.textContent = `${Number(e.amount).toLocaleString("ru-RU")} · ${tm(e.time)}`;
    }
    for (const [k, p] of pos) {
      const [b, acc] = k.split("|");
      el("rect", { x: p.x - 48, y: p.y - 22, width: 96, height: 44, rx: 8, fill: css("--panel"), stroke: css("--ink-3") }, svg);
      const a1 = el("text", { x: p.x, y: p.y - 3, "text-anchor": "middle", "font-size": 12, "font-weight": 600, fill: css("--ink") }, svg); a1.textContent = `банк ${b}`;
      const a2 = el("text", { x: p.x, y: p.y + 13, "text-anchor": "middle", "font-size": 10, "font-family": "JetBrains Mono, monospace", fill: css("--ink-2") }, svg); a2.textContent = acc && acc !== "null" ? acc : "?";
    }
    $("#treep").innerHTML = `<h2>Инцидент <span class="mono">${id}</span> · ${edges.length} звеньев</h2>`;
    $("#treep").appendChild(svg);
  } catch (e) { $("#treep").innerHTML = `<h2>Дерево цепочки</h2><div class="result err">${esc(e.message)}</div>`; }
}

/* ---------- Журнал ---------- */
async function pageAudit() {
  view.innerHTML = `<div class="head"><h1>Журнал хаба</h1><span class="muted">хэш-цепочка SHA-256, голова подписана Ed25519</span></div>
    <div class="grid g2e"><div class="panel"><h2>Голова журнала</h2><div class="kv" id="head"></div>
      <div class="selector"><button class="btn" id="vchain" type="button">Проверить хэш-цепочку</button><button class="btn" id="vsig" type="button">Проверить подпись</button></div><div id="vres"></div></div>
    <div class="panel"><h2>Что записывает хаб</h2><div class="note">Кто кого спросил, когда, хэш ссылки на перевод и итог. Суммы, счета и баллы в журнал не попадают. Любое изменение записи ломает цепочку, подпись головы позволяет банкам хранить и сверять её у себя.</div></div></div>
    <div class="panel"><div class="ph"><h2>Последние записи</h2><span class="muted" id="atotal"></span></div><div class="scroll"><table class="t" id="alog"></table></div></div>`;
  let head = null;
  $("#vchain").onclick = async () => { const r = await api("/v1/audit/verify"); $("#vres").innerHTML = `<div class="result ${r.ok ? "" : "err"}">${r.ok ? "Цепочка цела" : "Цепочка нарушена"}: ${fmtNum(r.entries)} записей проверено</div>`; };
  $("#vsig").onclick = async () => { const r = await api("/v1/audit/verify-signature", { method: "POST", body: JSON.stringify(head) }); $("#vres").innerHTML = `<div class="result ${r.ok ? "" : "err"}">Подпись головы ${r.ok ? "верна" : "не сходится"}</div>`; };
  const tick = async () => {
    try {
      head = await api("/v1/audit/head");
      $("#head").innerHTML = `<span>Записей</span><span class="num">${fmtNum(head.seq)}</span><span>Хэш головы</span><span class="mono">${head.hash}</span><span>Подпись</span><span class="mono">${head.signature.slice(0, 48)}…</span><span>Открытый ключ</span><span class="mono">${head.public_key}</span>`;
      const r = await api("/v1/audit/entries?limit=60");
      $("#atotal").textContent = `всего ${fmtNum(r.total)}`;
      $("#alog").innerHTML = `<tr><th class="r">№</th><th>Время</th><th>Тип</th><th>От</th><th>Кому</th><th>Хэш ссылки</th><th>Итог</th></tr>` +
        r.entries.map(e => `<tr><td class="r num">${e.seq}</td><td class="mono">${tm(e.ts)}</td><td>${esc(e.kind)}</td><td>${esc(e.src)}</td><td>${esc(e.dst)}</td><td class="mono">${esc((e.ref_hash || "").slice(0, 16))}</td><td>${esc(e.detail)}</td></tr>`).join("");
    } catch (e) { }
  };
  tick(); S.timer = setInterval(tick, 3000);
}

/* ---------- Эксперимент ---------- */
const MODES = [["operator", "Центральный оператор (MITS)"], ["oracle", "Общий граф участников"], ["isolation", "Изолированный банк"],
  ["C_L1_s0.25", "ШЕКАРА L1 · один балл"], ["C_L2_s0.25", "ШЕКАРА L2 · вектор с шумом"], ["C_L3", "ШЕКАРА L3 · полный сигнал"]];
const SLICES = [["all_max", "Все межбанковские"], ["pp_sender", "Решает отправитель"], ["pp_receiver", "Решает получатель"], ["external", "Участник ↔ внешний"]];
let slice = "all_max";
async function pageResults() {
  if (!S.results) S.results = await api("/v1/results");
  const R = S.results, op = R.paired.operator, iso = R.paired.isolation;
  view.innerHTML = `<div class="head"><h1>Эксперимент</h1><span class="muted">5,08 млн переводов · 30 банков-участников · 3 запуска · ${R.checks.passed} из ${R.checks.total} проверок пройдено</span></div>
    <div class="kpis">
      <div class="kpi"><span>Банк против центрального оператора</span><b>F1 +${fmt3(op.diff)}</b><em>значимо в ${op.sig} из ${op.n} запусков</em></div>
      <div class="kpi"><span>Прирост от обмена, PR-AUC</span><b>+${pct(R.views.C_L3.all_max.ap / R.views.isolation.all_max.ap - 1)}</b><em>значимо в ${iso.sig} из ${iso.n} запусков</em></div>
      <div class="kpi"><span>Деньги на счёте, H1</span><b>${pct(R.retention.C_L3.h1)}</b><em>без задержки ${pct(R.retention.C_L3.h0_15)}</em></div>
      <div class="kpi"><span>Проверка при зачислении</span><b>${R.latency.inbound_ms_p50.toFixed(1).replace(".", ",")} мс</b><em>медиана, один поток CPU</em></div>
    </div>
    <div class="grid g2e"><div class="panel"><div class="ph"><h2>PR-AUC по режимам</h2></div><div class="seg" id="slices"></div><div class="bars" id="bars"></div><span class="muted" id="bnote"></span></div>
    <div class="panel"><div class="ph"><h2>Цена приватности</h2><span class="muted">ε за 30 дней, логарифм</span></div><div id="priv"></div><div class="note" id="ptip">Наведите на точку.</div></div></div>`;
  drawBars(); drawPrivacy();
}
function drawBars() {
  const R = S.results;
  $("#slices").innerHTML = SLICES.map(([k, l]) => `<button type="button" class="${k === slice ? "on" : ""}" data-k="${k}">${l}</button>`).join("");
  $("#slices").querySelectorAll("button").forEach(b => b.onclick = () => { slice = b.dataset.k; drawBars(); });
  const rows = MODES.map(([m, l]) => ({ m, l, x: R.views[m]?.[slice] })).filter(r => r.x);
  const max = Math.max(...rows.map(r => r.x.ap + r.x.sd)) * 1.08;
  $("#bars").innerHTML = rows.map(r => `<div class="barrow"><span>${r.l}</span><div class="track"><div class="b" style="width:${r.x.ap / max * 100}%;background:${r.m.startsWith("C_") ? css("--accent") : css("--ink-3")}"></div></div><span class="v">${fmt3(r.x.ap)}</span></div>`).join("");
  $("#bnote").textContent = `${R.views.isolation[slice]?.pos ?? "—"} положительных переводов в тесте; среднее по трём запускам.`;
}
function drawPrivacy() {
  const R = S.results, pts = R.privacy, iso = R.views.isolation.all_max.ap, l3 = R.views.C_L3.all_max.ap;
  const w = 520, h = 280, m = { l: 48, r: 14, t: 14, b: 40 };
  const lx = pts.map(p => Math.log10(p.eps)), x0 = Math.floor(Math.min(...lx)), x1 = Math.ceil(Math.max(...lx));
  const ys = [...pts.map(p => p.ap), iso, l3], y0 = Math.floor(Math.min(...ys) * 100 - 1) / 100, y1 = Math.ceil(Math.max(...ys) * 100 + 1) / 100;
  const X = v => m.l + (Math.log10(v) - x0) / (x1 - x0) * (w - m.l - m.r), Y = v => m.t + (1 - (v - y0) / (y1 - y0)) * (h - m.t - m.b);
  const s = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img", "aria-label": "Качество и ε" });
  for (let e = x0; e <= x1; e++) { el("line", { x1: X(10 ** e), y1: m.t, x2: X(10 ** e), y2: h - m.b, stroke: css("--line") }, s); const t = el("text", { x: X(10 ** e), y: h - m.b + 16, "text-anchor": "middle", "font-size": 11, fill: css("--ink-3"), "font-family": "JetBrains Mono, monospace" }, s); t.textContent = (10 ** e).toLocaleString("ru-RU"); }
  for (let v = y0; v <= y1 + 1e-9; v += 0.01) { el("line", { x1: m.l, y1: Y(v), x2: w - m.r, y2: Y(v), stroke: css("--line") }, s); const t = el("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", "font-size": 11, fill: css("--ink-3"), "font-family": "JetBrains Mono, monospace" }, s); t.textContent = v.toFixed(2).replace(".", ","); }
  for (const [v, l, c] of [[iso, "изоляция", css("--ink-3")], [l3, "L3 без шума", css("--accent")]]) { el("line", { x1: m.l, y1: Y(v), x2: w - m.r, y2: Y(v), stroke: c, "stroke-dasharray": "5 4" }, s); const t = el("text", { x: w - m.r - 4, y: Y(v) - 5, "text-anchor": "end", "font-size": 11, fill: c }, s); t.textContent = l; }
  for (const lvl of ["L1", "L2"]) {
    const p = pts.filter(q => q.level === lvl).sort((a, b) => a.eps - b.eps), col = lvl === "L1" ? css("--amber") : css("--accent");
    el("polyline", { points: p.map(q => `${X(q.eps)},${Y(q.ap)}`).join(" "), fill: "none", stroke: col, "stroke-width": 2 }, s);
    p.forEach(q => { const c = el("circle", { cx: X(q.eps), cy: Y(q.ap), r: 6, fill: col, stroke: css("--panel"), "stroke-width": 2, tabindex: 0 }, s); c.style.cursor = "pointer";
      const f = () => $("#ptip").innerHTML = `<b>${q.level}, σ = ${String(q.sigma).replace(".", ",")}</b> · ε за 30 дней ≈ <span class="mono">${Math.round(q.eps).toLocaleString("ru-RU")}</span>, недельный выпуск ≈ <span class="mono">${q.eps_week.toFixed(1).replace(".", ",")}</span> · PR-AUC <span class="mono">${fmt3(q.ap)}</span>`;
      c.onmouseenter = f; c.onfocus = f; });
  }
  $("#priv").innerHTML = ""; $("#priv").appendChild(s);
}

/* ---------- публичный режим ---------- */
function applyConfig() {
  const locked = S.cfg.admin_required && !store.get("shekara-admin");
  for (const id of ["#play", "#speed", "#mode", "#reset"]) $(id).hidden = locked;
  let badge = $("#demobadge");
  if (locked && !badge) {
    $(".bar").insertAdjacentHTML("beforeend", `<span id="demobadge" class="chip c-ok">стенд идёт сам · ×${S.state?.speed ?? ""}</span><button class="btn" id="adminbtn" type="button">Ключ администратора</button>`);
    $("#adminbtn").onclick = () => { const k = prompt("Ключ администратора стенда"); if (k) { store.set("shekara-admin", k, true); location.reload(); } };
  }
}

/* ---------- старт ---------- */
(async function start() {
  try { S.cfg = await api("/v1/config"); } catch (e) { }
  await pollState();
  applyConfig();
  if (!location.hash) location.hash = "#/network"; else route();
  setInterval(pollState, 1000); setInterval(pollEvents, 1000);
})();
