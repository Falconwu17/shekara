// «Почему алерт»: признаки с наибольшим вкладом в балл модели (model/explain.py), человеческим языком.

import { h } from "../core/dom.js";

/** Список причин с полосой вклада: вправо (риск выше), влево (риск ниже). */
export function whyList(items) {
  if (!items?.length) return h("div", { class: "muted" }, "Объяснение для этого перевода не сохранено.");
  const max = Math.max(...items.map(i => Math.abs(i.contrib)));
  return h("ul", { class: "why" }, items.map(item =>
    h("li", { class: "why-item" },
      h("span", { class: "why-bar" },
        h("i", { class: item.contrib >= 0 ? "up" : "down", style: { width: `${Math.max(6, (Math.abs(item.contrib) / max) * 100)}%` } })),
      h("span", {}, item.text))));
}

/** Главная причина одной строкой (для таблицы очереди). */
export function whyShort(items) {
  const top = items?.find(i => i.contrib > 0);
  return top ? h("span", { class: "why-short" }, top.text) : h("span", { class: "muted" }, "-");
}
