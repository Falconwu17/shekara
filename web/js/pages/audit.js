// Журнал хаба: голова с подписью Ed25519, проверка хэш-цепочки и подписи, последние записи.

import { dataTable } from "../components/table.js";
import { chip, note, panel, result } from "../components/ui.js";
import { api } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { integer, shortTime } from "../core/format.js";
import { every } from "../core/poll.js";

const KIND = { query: "запрос выпуска", release: "ночной выпуск", incident_soft: "шаг мягкого инцидента",
  incident_confirmed: "шаг подтверждённого инцидента", incident_open: "инцидент открыт" };

const ENTRY_COLUMNS = [
  { title: "№", align: "right", cell: e => e.seq },
  { title: "Время", cell: e => h("span", { class: "mono" }, shortTime(e.ts)) },
  { title: "Тип", cell: e => KIND[e.kind] ?? e.kind },
  { title: "От", cell: e => e.src },
  { title: "Кому", cell: e => e.dst },
  { title: "Хэш ссылки", cell: e => h("span", { class: "mono" }, (e.ref_hash || "").slice(0, 16)) },
  { title: "Итог", cell: e => (e.detail.includes("подтверждён") ? chip(e.detail, "risk") : e.detail) },
];

function headView(head) {
  return h("div", { class: "kv" },
    h("span", {}, "Записей"), h("span", {}, integer(head.seq)),
    h("span", {}, "Хэш головы"), h("span", { class: "mono" }, head.hash),
    h("span", {}, "Подпись"), h("span", { class: "mono" }, `${head.signature.slice(0, 48)}…`),
    h("span", {}, "Открытый ключ"), h("span", { class: "mono" }, head.public_key));
}

export function mount(root) {
  let head = null;
  const headBox = h("div");
  const checkBox = h("div");
  const totalBox = h("span", { class: "muted" });
  const entriesBox = h("div", { class: "scroll" });

  async function verifyChain() {
    const r = await api.auditVerify();
    render(checkBox, result(`${r.ok ? "Цепочка цела" : "Цепочка нарушена"}: ${integer(r.entries)} записей проверено`, { error: !r.ok }));
  }
  async function verifySignature() {
    const r = await api.auditVerifySignature(head);
    render(checkBox, result(`Подпись головы ${r.ok ? "верна" : "не сходится"}`, { error: !r.ok }));
  }

  render(root,
    h("div", { class: "page-head" }, h("h1", {}, "Журнал хаба"), h("span", { class: "muted" }, "хэш-цепочка SHA-256, голова подписана Ed25519")),
    h("div", { class: "grid grid-half" },
      panel("Голова журнала", { body: [headBox,
        h("div", { class: "row" },
          h("button", { type: "button", class: "btn", onClick: verifyChain }, "Проверить хэш-цепочку"),
          h("button", { type: "button", class: "btn", onClick: verifySignature }, "Проверить подпись")),
        checkBox] }),
      panel("Что записывает хаб", { body: note("Кто кого спросил, когда, хэш ссылки на перевод и итог. Суммы, счета и баллы в журнал не попадают. Любое изменение записи ломает цепочку, подпись головы позволяет банкам хранить и сверять её у себя.") })),
    panel("Последние записи", { aside: totalBox, body: entriesBox }));

  return every(3000, async () => {
    try {
      head = await api.auditHead();
      render(headBox, headView(head));
      const { total, entries } = await api.auditEntries();
      totalBox.textContent = `всего ${integer(total)}`;
      render(entriesBox, dataTable(ENTRY_COLUMNS, entries));
    } catch (e) {
      render(checkBox, result(e.message, { error: true }));
    }
  });
}
