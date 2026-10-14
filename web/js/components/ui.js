// Мелкие переиспользуемые элементы интерфейса.

import { h } from "../core/dom.js";
import { decimal } from "../core/format.js";

/** Метка-статус: kind = alert | risk | ok | accent | muted */
export function chip(text, kind = "muted") {
  return h("span", { class: `chip chip-${kind}` }, text);
}

/** Шкала балла 0…1; выше порога: подсвечена. */
export function scoreBar(value, threshold) {
  if (value == null) return h("span", { class: "muted" }, "-");
  const high = threshold != null && value >= threshold;
  return h("span", { class: "score" },
    h("i", {}, h("b", { class: high ? "high" : "", style: { width: `${Math.max(3, value * 100)}%` } })),
    h("span", { class: "mono" }, decimal(value)));
}

/** Карточка-панель с заголовком, необязательной подписью справа и телом. */
export function panel(title, { aside, body = [], id } = {}) {
  return h("section", { class: "panel", id },
    h("div", { class: "panel-head" }, h("h2", {}, title), aside ? h("span", { class: "muted" }, aside) : null),
    body);
}

/** Полоса показателей: [{ label, value, note }] */
export function kpiStrip(items) {
  return h("div", { class: "kpis" }, items.map(item =>
    h("div", { class: "kpi" },
      h("span", { class: "kpi-label" }, item.label),
      h("b", { class: "kpi-value" }, item.value),
      item.note ? h("em", { class: "kpi-note" }, item.note) : null)));
}

/** Сегментированный переключатель: options = [[value, label]] */
export function segmented(options, current, onSelect, label) {
  return h("div", { class: "seg", role: "group", "aria-label": label },
    options.map(([value, text]) =>
      h("button", { type: "button", class: value === current ? "on" : "", onClick: () => onSelect(value) }, text)));
}

export function note(...children) {
  return h("div", { class: "note" }, children);
}

export function result(text, { error = false } = {}) {
  return h("div", { class: ["result", error && "result-error"] }, text);
}

export function emptyState(text) {
  return h("div", { class: "empty" }, text);
}

let toastTimer;
/** Короткое уведомление в углу экрана. */
export function toast(text) {
  const el = document.getElementById("toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3500);
}
