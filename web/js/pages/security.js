// Проверка защиты: настоящие атаки на протокол стенда (online/attacks.py), каждая на копии, стенд не меняется.

import { chip, note, panel, result } from "../components/ui.js";
import { api } from "../core/api.js";
import { h, render } from "../core/dom.js";

const CHECKS = [
  ["brute", "Перебор ссылок", "1 000 случайных ссылок на переводы"],
  ["foreign", "Чужой перевод", "ссылка на перевод между другими банками"],
  ["quota", "Выкачивание по квоте", "запросы без остановки с настоящей ссылкой"],
  ["journal", "Подделка журнала", "правка одной записи журнала хаба"],
  ["signature", "Подделка подписи", "подменённая голова журнала"],
  ["eavesdrop", "Хаб читает ответ", "попытка расшифровать ответ банка"],
];

function outcome(r) {
  return [
    h("div", { class: "row" }, r.ok ? chip("защита сработала", "ok") : chip("защита не сработала", "risk")),
    h("p", { class: "attack-text" }, r.attack),
    r.rows.length ? h("div", { class: "kv" }, r.rows.flatMap(([k, v]) => [h("span", {}, k), h("span", { class: "mono" }, String(v))])) : null,
    r.defense ? note(r.defense) : null,
  ];
}

function card([id, title, hint], onRun) {
  const body = h("div", { class: "stack" }, h("span", { class: "muted" }, hint));
  const button = h("button", { type: "button", class: "btn btn-primary", onClick: () => onRun(id, body, button) }, "Атаковать");
  return { el: panel(title, { aside: button, body }), body, button, id };
}

export function mount(root, [autorun]) {
  async function runOne(id, body, button) {
    button.disabled = true;
    render(body, h("span", { class: "muted" }, "Атака идёт…"));
    try {
      render(body, outcome(await api.securityCheck(id)));
    } catch (e) {
      render(body, result(e.message, { error: true }));
    } finally {
      button.disabled = false;
    }
  }

  const cards = CHECKS.map(c => card(c, runOne));
  const runAll = async () => { for (const c of cards) await runOne(c.id, c.body, c.button); };

  render(root,
    h("div", { class: "page-head" },
      h("h1", {}, "Проверка защиты"),
      h("button", { type: "button", class: "btn btn-primary", onClick: runAll }, "Запустить все атаки")),
    note("Атаки идут на настоящий протокол стенда: шлюзы банков, квоты, журнал хаба и шифрование. Каждая работает на копии, поэтому квоты и журнал живого стенда не меняются."),
    h("div", { class: "grid grid-half" }, cards.map(c => c.el)));
  if (autorun === "all") runAll();  // #/security/all: сразу все атаки (для показа на защите)
  return () => {};
}
