// Проверка защиты: настоящие атаки на протокол стенда (online/attacks.py), каждая на копии, стенд не меняется.

import { chip, note, panel, result } from "../components/ui.js";
import { api } from "../core/api.js";
import { h, render } from "../core/dom.js";

// Две группы: санитарные проверки (базовая криптография и журнал) и атаки на устройство протокола.
// open: атака ожидаемо не закрыта (online/attacks.py, OPEN_ATTACKS): показываем как открытую угрозу, не как ошибку.
const GROUPS = [
  ["Санитарные проверки", "Базовые свойства криптографии и журнала: без них протокол не имеет смысла, но и устройство протокола они не проверяют.", [
    ["brute", "Перебор ссылок", "1 000 случайных ссылок на переводы"],
    ["signature", "Подделка подписи", "подменённая голова журнала"],
    ["journal", "Подделка журнала", "правка одной записи журнала хаба без пересчёта хэшей"],
    ["eavesdrop", "Хаб читает ответ", "попытка расшифровать ответ банка"],
  ]],
  ["Атаки на устройство протокола", "Нечестный хаб (оператор) и злонамеренный банк-участник пользуются логикой протокола, а не слабой криптографией.", [
    ["foreign", "Чужой перевод", "ссылка на перевод между другими банками против случайной ссылки"],
    ["quota", "Выкачивание по квоте", "запросы без остановки с настоящей ссылкой"],
    ["swap", "Хаб переставил ответы", "ответ на один запрос вместо другого"],
    ["suppress", "Хаб подавил ответ", "отказ «не найдено» вместо настоящего ответа"],
    ["replay", "Хаб повторил старый выпуск", "выпуск трёхдневной давности поверх свежего"],
    ["withhold", "Хаб удержал свежий выпуск, отдал старый", "свежего выпуска нет, отдан старый; ночная квитанция ответчика"],
    ["redirect", "Хаб перенаправил выпуск", "выпуск одного счёта на ссылку другого"],
    ["length", "Балл по длине шифротекста", "длина ответа при разных баллах"],
    ["rewrite", "Хаб переписал историю", "правка журнала с пересчётом хэшей и новой подписью"],
    ["attribute", "Хаб приписал банку запрос", "запись в журнале от имени банка без его подписи"],
    ["split", "Хаб показал банкам разные журналы", "две версии журнала, каждая с настоящей подписью"],
    ["cover", "Банк покрывает дропов", "балл 0 своим известным дропам"],
    ["cover_quiet", "Банк прикрывает дропов незаметно", "медианный балл выпуска своим известным дропам", true],
  ]],
];

function verdict(r) {
  if (r.ok) return chip("защита сработала", "ok");
  if (r.open) return chip("защита не сработала: открытая угроза", "alert");
  return chip("защита не сработала", "risk");
}

function outcome(r) {
  return [
    h("div", { class: "row" }, verdict(r)),
    h("p", { class: "attack-text" }, r.attack),
    r.rows.length ? h("div", { class: "kv" }, r.rows.flatMap(([k, v]) => [h("span", {}, k), h("span", { class: "mono" }, String(v))])) : null,
    r.defense ? note(r.defense) : null,
  ];
}

function card([id, title, hint, open], onRun) {
  const body = h("div", { class: "stack" }, h("span", { class: "muted" }, hint),
    open ? h("div", { class: "row" }, chip("ожидаемо открытая угроза", "alert")) : null);
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

  const groups = GROUPS.map(([title, about, checks]) => ({ title, about, cards: checks.map(c => card(c, runOne)) }));
  const cards = groups.flatMap(g => g.cards);
  const runAll = async () => { for (const c of cards) await runOne(c.id, c.body, c.button); };

  render(root,
    h("div", { class: "page-head" },
      h("h1", {}, "Проверка защиты"),
      h("button", { type: "button", class: "btn btn-primary", onClick: runAll }, "Запустить все атаки")),
    note("Атаки идут на настоящий протокол стенда: шлюзы банков, квоты, журнал хаба, подписи и шифрование. Каждая работает на копии, поэтому квоты и журнал живого стенда не меняются. Открытая угроза: атака, которую протокол ожидаемо не ловит; стенд показывает это честно и пишет, что закрывает её вместо протокола."),
    groups.map(g => h("section", { class: "stack check-group" },
      h("h2", {}, g.title, " ", h("span", { class: "muted" }, `(${g.cards.length})`)),
      h("span", { class: "muted" }, g.about),
      h("div", { class: "grid grid-half" }, g.cards.map(c => c.el)))));
  if (autorun === "all") runAll();  // #/security/all: сразу все атаки (для показа на защите)
  return () => {};
}
