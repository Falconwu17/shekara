// Лента событий протокола: как показывать каждый тип события.

import { h } from "../core/dom.js";
import { decimal, shortTime } from "../core/format.js";
import { chip } from "./ui.js";

const DESCRIBE = {
  alert: e => [chip("алерт", "alert"), [
    "банк ", h("b", {}, e.bank), ": счёт ", h("span", { class: "mono" }, e.account),
    `, балл ${decimal(e.score)} · входящий из ${e.from_bank}`,
    e.laundering ? [" ", chip("в разметке — отмывание", "risk")] : null]],
  incident: e => [chip("инцидент", "risk"), [
    "банк ", h("b", {}, e.bank), ` открыл ${e.incident_kind === "soft" ? "мягкий" : "подтверждённый"} инцидент `,
    h("span", { class: "mono" }, e.incident)]],
  hop: e => [chip("шаг", "accent"), [
    `${e.bank} → ${e.to_bank}: ссылка на перевод по инциденту `, h("span", { class: "mono" }, e.incident),
    ` · ${e.result === "ok" ? "перевод найден" : e.result}`]],
  night: e => [chip("ночь", "ok"), e.text],
  query: e => [chip("запрос", "accent"), `${e.bank} спросил ${e.to_bank} о контрагенте перевода · ${e.result === "ok" ? "выпуск получен" : e.result}`],
  mode: e => [chip("режим"), e.text],
};

export function eventRow(event, { fresh = false } = {}) {
  const [badge, text] = (DESCRIBE[event.kind] || (e => [chip(e.kind), ""]))(event);
  return h("div", { class: ["event", fresh && "event-new"] },
    h("span", { class: "event-time" }, shortTime(event.t)), h("span", {}, badge), h("span", {}, text));
}

/** Лента с последними событиями сверху; append(events) добавляет новые. */
export function eventFeed(initial, { limit = 200, empty } = {}) {
  const el = h("div", { class: "feed" });
  const rows = initial.slice().reverse().map(event => eventRow(event));
  if (rows.length) el.append(...rows);
  else if (empty) el.append(h("div", { class: "empty" }, empty));
  return {
    el,
    append(events) {
      el.querySelector(".empty")?.remove();
      el.prepend(...events.slice().reverse().map(event => eventRow(event, { fresh: true })));
      while (el.children.length > limit) el.lastChild.remove();
    },
  };
}
