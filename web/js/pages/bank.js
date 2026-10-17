// Кабинет банка: очередь алертов, лента своих переводов, карточка счёта, запрос выпуска, инцидент.

import { dataTable } from "../components/table.js";
import { chip, note, panel, result, scoreBar, toast } from "../components/ui.js";
import { api } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { amount, decimal, shortTime } from "../core/format.js";
import { every } from "../core/poll.js";
import { store } from "../core/store.js";

function queueTable(queue, threshold, openAccount) {
  return dataTable([
    { title: "Счёт", cell: a => [h("span", { class: "mono" }, a.account), a.incident_flags.length ? [" ", chip("инцидент", "risk")] : null] },
    { title: "Балл", cell: a => scoreBar(a.score, threshold) },
    { title: "Пришло из", cell: a => a.from_bank ?? "-" },
    { title: "Сумма", align: "right", cell: a => (a.amount ? amount(a.amount, a.currency) : "-") },
    { title: "Разметка", cell: a => (a.laundering ? chip("отмывание", "risk") : chip("нет")) },
  ], queue, { onRowClick: a => openAccount(a.account), empty: "Сегодня алертов пока нет." });
}

function feedTable(feed, { bank, threshold, participants, openAccount, ask }) {
  const isPartner = row => participants.includes(row.cp_bank) && row.cp_bank !== bank;
  return dataTable([
    { title: "Время", cell: r => h("span", { class: "mono" }, shortTime(r.t)) },
    { title: "", cell: r => (r.side === "in" ? chip("вход", "accent") : chip("выход")) },
    { title: "Свой счёт", cell: r => h("a", { href: "#", class: "mono", onClick: e => { e.preventDefault(); openAccount(r.account); } }, r.account) },
    { title: "Банк контрагента", cell: r => [r.cp_bank, isPartner(r) ? [" ", chip("участник", "ok")] : null] },
    { title: "Сумма", align: "right", cell: r => amount(r.amount, r.currency) },
    { title: "Балл при зачислении", cell: r => (r.side === "in" ? scoreBar(r.score, threshold) : h("span", { class: "muted" }, "-")) },
    { title: "", cell: r => (isPartner(r) ? h("button", { type: "button", class: "btn", onClick: () => ask(r.row_no) }, "Запросить выпуск") : null) },
  ], feed);
}

function releaseText(release) {
  if (!release) return "ещё не рассылался";
  const value = Array.isArray(release.value) ? release.value.map(v => decimal(v)).join(", ") : decimal(release.value);
  return `${release.level} от ${release.date}: балл ${value}`;
}

function accountCard(card, bank, onIncident) {
  return [
    h("div", { class: "panel-head" },
      h("h2", {}, "Счёт ", h("span", { class: "mono" }, card.account)),
      h("button", { type: "button", class: "btn btn-warn", onClick: onIncident }, "Открыть подтверждённый инцидент")),
    h("div", { class: "kv" },
      h("span", {}, "Переводов к этому моменту"), h("span", {}, card.total),
      h("span", {}, "Флаги инцидентов"), h("span", {}, card.incident_flags.length ? card.incident_flags.map(id => chip(id, "risk")) : "нет")),
    h("div", { class: "scroll scroll-short" }, dataTable([
      { title: "Время", cell: t => h("span", { class: "mono" }, shortTime(t.t)) },
      { title: "", cell: t => (t.side === "in" ? "вход" : "выход") },
      { title: "Контрагент", cell: t => [h("span", { class: "mono" }, t.cp_bank), " · ", h("span", { class: "mono" }, t.cp_account)] },
      { title: "Сумма", align: "right", cell: t => amount(t.amount) },
      { title: "Выпуск о контрагенте", cell: t => (t.release ? chip(`${t.release.level} ${decimal(Array.isArray(t.release.value) ? t.release.value[0] : t.release.value)}`, "accent")
        : h("span", { class: "muted" }, t.partner ? "нет выпуска" : "банк вне ШЕКАРЫ")) },
    ], card.transfers)),
  ];
}

export function mount(root, [bankParam]) {
  const participants = store.sim?.participants || [];
  const bank = participants.includes(bankParam) ? bankParam : participants[0];
  let account = null;

  const queueMeta = h("span", { class: "muted" });
  const queueBox = h("div", { class: "scroll" });
  const cardBox = h("section", { class: "panel" }, h("div", { class: "panel-head" }, h("h2", {}, "Карточка счёта")),
    h("div", { class: "empty" }, "Выберите счёт в очереди или в ленте."));
  const answerBox = h("div");
  const feedBox = h("div", { class: "scroll" });

  async function openAccount(id) {
    account = id;
    try {
      const card = await api.account(bank, id);
      render(cardBox, accountCard(card, bank, async () => {
        const r = await api.openIncident(bank, id);
        toast(r.incident_id ? `Инцидент ${r.incident_id} открыт; шаги уйдут по исходящим переводам счёта` : `Отказ: ${r.error}`);
      }));
    } catch (e) {
      toast(e.message);
    }
  }

  async function ask(rowNo) {
    try {
      const r = await api.queryCounterparty(bank, rowNo);
      if (r.error) {
        render(answerBox, result(`Отказ: ${r.text || r.error}. Ответ одинаков для неверной, чужой и ещё не зачисленной ссылки.`, { error: true }));
        return;
      }
      const sealed = r.sealed_bytes ? ` · через хаб прошло ${r.sealed_bytes} байт шифротекста, открыть его может только ваш банк` : "";
      render(answerBox, result(`Банк ${r.to_bank} ответил: выпуск ${releaseText(r.release)} · псевдоним счёта ${r.pseudonym.slice(0, 16)}… · дневная квота на пару: ${r.quota}${sealed}`));
      toast(`Запрос к банку ${r.to_bank} записан в журнал хаба`);
    } catch (e) {
      toast(e.message);
    }
  }

  const select = h("select", { id: "bank-select", onChange: e => { location.hash = `#/bank/${e.target.value}`; } },
    participants.map(b => h("option", { value: b, selected: b === bank }, b)));

  render(root,
    h("div", { class: "page-head" },
      h("h1", {}, `Кабинет банка ${bank}`),
      h("div", { class: "row" }, h("label", { for: "bank-select", class: "muted" }, "Войти как банк"), select)),
    note(`Вы видите только переводы клиентов банка ${bank} и баллы, которые банк считает сам. О счёте в другом банке: только выпуск, присланный по ссылке на реальный перевод. Столбец «разметка»: истина AMLworld для проверки стенда; банку она неизвестна.`),
    h("div", { class: "grid grid-half" },
      panel("Очередь алертов на сегодня", { aside: queueMeta, body: queueBox }),
      cardBox),
    panel("Лента переводов банка", { aside: "«Запросить выпуск»: спросить банк-контрагента о счёте на другом конце этого перевода", body: [answerBox, feedBox] }));

  return every(2000, async () => {
    try {
      const [{ queue, overview }, { feed }] = await Promise.all([api.bankQueue(bank), api.bankFeed(bank)]);
      queueMeta.textContent = `${queue.length} из ${overview.budget} · порог ${decimal(overview.threshold)}`;
      render(queueBox, queueTable(queue, overview.threshold, openAccount));
      render(feedBox, feedTable(feed, { bank, threshold: overview.threshold, participants, openAccount, ask }));
      if (account) await openAccount(account);
    } catch (e) {
      render(answerBox, result(e.message, { error: true }));
    }
  });
}
