// Эксперимент: итоговые показатели, PR-AUC по режимам и срезам, кривая цены приватности.

import { barList, privacyChart } from "../charts/results-charts.js";
import { kpiStrip, note, panel, segmented } from "../components/ui.js";
import { api } from "../core/api.js";
import { cssVar, h, render } from "../core/dom.js";
import { decimal, percent } from "../core/format.js";

const MODES = [
  ["operator", "Центральный оператор (MITS)"], ["oracle", "Общий граф участников"], ["isolation", "Изолированный банк"],
  ["C_L1_s0.25", "ШЕКАРА L1 · один балл"], ["C_L2_s0.25", "ШЕКАРА L2 · вектор с шумом"], ["C_L3", "ШЕКАРА L3 · полный сигнал"],
];
const SLICES = [["all_max", "Все межбанковские"], ["pp_sender", "Решает отправитель"], ["pp_receiver", "Решает получатель"], ["external", "Участник ↔ внешний"]];

let cache = null;

function summary(r) {
  const { bank_vs_operator: bank, operator, isolation } = r.paired;
  return kpiStrip([
    { label: "Банк против центрального оператора", value: `F1 +${decimal(bank.diff)}`,
      note: `без обмена, значимо в ${bank.sig} из ${bank.n} запусков; с обменом +${decimal(operator.diff)}` },
    { label: "Прирост от обмена, PR-AUC", value: `+${percent(r.views.C_L3.all_max.ap / r.views.isolation.all_max.ap - 1)}`, note: `значимо в ${isolation.sig} из ${isolation.n} запусков` },
    { label: "Деньги на счёте, H1", value: percent(r.retention.C_L3.h1), note: `без задержки ${percent(r.retention.C_L3.h0_15)}` },
    { label: "Проверка при зачислении", value: `${decimal(r.latency.inbound_ms_p50, 1)} мс`, note: "медиана, один поток CPU" },
  ]);
}

function pointText(p) {
  return `${p.level}, σ = ${decimal(p.sigma, 2)} · ε за 30 дней ≈ ${Math.round(p.eps).toLocaleString("ru-RU")}, недельный выпуск ≈ ${decimal(p.eps_week, 1)} · PR-AUC ${decimal(p.ap)}`;
}

export function mount(root) {
  let slice = "all_max";
  const barsBox = h("div", { class: "stack" });
  const tip = note("Наведите на точку.");

  function drawBars(r) {
    const rows = MODES.filter(([mode]) => r.views[mode]?.[slice])
      .map(([mode, label]) => ({ label, value: r.views[mode][slice].ap, highlight: mode.startsWith("C_") }));
    render(barsBox,
      segmented(SLICES, slice, value => { slice = value; drawBars(r); }, "Срез"),
      barList(rows),
      h("span", { class: "muted" }, `${r.views.isolation[slice]?.pos ?? "-"} положительных переводов в тесте; среднее по трём запускам.`));
  }

  (async () => {
    cache ??= await api.results();
    const r = cache;
    const chart = privacyChart(r.privacy, [
      { label: "изоляция", value: r.views.isolation.all_max.ap, color: cssVar("--ink-3") },
      { label: "L3 без шума", value: r.views.C_L3.all_max.ap, color: cssVar("--accent") },
    ], p => { tip.textContent = pointText(p); });
    render(root,
      h("div", { class: "page-head" }, h("h1", {}, "Эксперимент"),
        h("span", { class: "muted" }, `5,08 млн переводов · 30 банков-участников · 3 запуска · ${r.checks.passed} из ${r.checks.total} проверок пройдено`)),
      summary(r),
      h("div", { class: "grid grid-half" },
        panel("PR-AUC по режимам", { body: barsBox }),
        panel("Цена приватности", { aside: "ε за 30 дней, логарифм", body: [chart, tip] })));
    drawBars(r);
  })();

  return () => {};
}
