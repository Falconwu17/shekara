// Страница «Сеть»: показатели стенда, кольцо банков, лента протокола, таблица банков.

import { bankRing } from "../charts/bank-ring.js";
import { eventFeed } from "../components/event-feed.js";
import { dataTable } from "../components/table.js";
import { kpiStrip, panel } from "../components/ui.js";
import { api } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { decimal, integer, percent } from "../core/format.js";
import { every } from "../core/poll.js";
import { onEvents, onSim, store } from "../core/store.js";

function kpis(counters = {}) {
  const caught = counters.laundering_in ? counters.laundering_alerted / counters.laundering_in : null;
  return kpiStrip([
    { label: "Переводов обработано", value: integer(counters.transfers || 0) },
    { label: "Алертов при зачислении", value: integer(counters.alerts || 0), note: `${integer(counters.alerts_laundering || 0)} — отмывание по разметке` },
    { label: "Входящее отмывание поймано", value: percent(caught), note: `${integer(counters.laundering_alerted || 0)} из ${integer(counters.laundering_in || 0)} межбанковских` },
    { label: "Инцидентов", value: integer((counters.incidents_soft || 0) + (counters.incidents_confirmed || 0)), note: `шагов по ссылке: ${integer(counters.hops || 0)}` },
    { label: "Ночных выпусков", value: integer(counters.releases_delivered || 0), note: `запросов днём: ${integer(counters.queries || 0)}` },
  ]);
}

const BANK_COLUMNS = [
  { title: "Банк", cell: b => h("b", {}, b.bank) },
  { title: "Бюджет", align: "right", cell: b => b.budget },
  { title: "В очереди сегодня", align: "right", cell: b => b.queue },
  { title: "Инцидентов открыл", align: "right", cell: b => b.incidents },
  { title: "Порог алерта", align: "right", cell: b => (b.threshold == null ? h("span", { class: "muted" }, "нет данных валидации") : h("span", { class: "mono" }, decimal(b.threshold))) },
];

export function mount(root) {
  const openBank = bank => { location.hash = `#/bank/${bank}`; };
  const kpiBox = h("div");
  const feed = eventFeed(store.events, { empty: "Нажмите «Запустить» — переводы пойдут по времени." });
  const ring = bankRing(store.sim?.participants || [], openBank);
  const banksBox = h("div", { class: "scroll" });
  const release = store.sim?.release;

  render(root,
    h("div", { class: "page-head" },
      h("h1", {}, "Сеть банков-участников"),
      h("span", { class: "muted" }, "Пунктир — граница банка. Вспышка — алерт, линия — шаг инцидента по ссылке.")),
    kpiBox,
    h("div", { class: "grid grid-wide" },
      panel(`${store.sim?.participants.length ?? 0} банков-участников`, {
        aside: release ? `выпуски: ${release.level}, σ = ${decimal(release.sigma, 2)}` : null, body: ring.el }),
      panel("Лента протокола", { aside: "хаб видит только метаданные", body: feed.el })),
    panel("Банки", { aside: "бюджет алертов — 0,1% активных счетов в сутки", body: banksBox }));

  const drawKpis = sim => render(kpiBox, kpis(sim?.counters));
  drawKpis(store.sim);
  const stopSim = onSim(drawKpis);
  const stopEvents = onEvents(events => { feed.append(events); ring.show(events); });
  const stopPoll = every(1500, async () => {
    const { banks } = await api.banks().catch(() => ({ banks: [] }));
    render(banksBox, dataTable(BANK_COLUMNS, banks, { onRowClick: b => openBank(b.bank) }));
  });
  return () => { stopSim(); stopEvents(); stopPoll(); };
}
