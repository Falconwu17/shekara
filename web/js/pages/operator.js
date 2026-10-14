// Оператор (антифрод-центр): список инцидентов и дерево цепочки: только для роли «оператор».

import { incidentTree } from "../charts/incident-tree.js";
import { dataTable } from "../components/table.js";
import { chip, emptyState, note, panel, result, segmented } from "../components/ui.js";
import { api, auth } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { shortTime } from "../core/format.js";
import { every } from "../core/poll.js";
import { store } from "../core/store.js";

const INCIDENT_COLUMNS = [
  { title: "Инцидент", cell: i => h("span", { class: "mono" }, i.id) },
  { title: "Тип", cell: i => (i.kind === "soft" ? chip("мягкий", "alert") : chip("подтверждённый", "risk")) },
  { title: "Банк", cell: i => i.bank },
  { title: "Открыт", cell: i => h("span", { class: "mono" }, shortTime(i.t)) },
  { title: "Шагов", align: "right", cell: i => i.hops },
];

function tokenForm(onLogin) {
  const input = h("input", { id: "operator-token", type: "password", class: "input", value: auth.operatorToken });
  return h("div", { class: "row" },
    h("label", { for: "operator-token", class: "muted" }, "Токен оператора"), input,
    h("button", { type: "button", class: "btn", onClick: () => { auth.operatorToken = input.value; onLogin(); } }, "Войти"));
}

export function mount(root) {
  const listBox = h("div", { class: "scroll" });
  const treeBox = h("section", { class: "panel" }, h("h2", {}, "Дерево цепочки"), emptyState("Выберите инцидент."));

  async function showTree(id) {
    try {
      const tree = await api.incidentTree(id);
      if (tree.kind === "soft") {
        render(treeBox, h("h2", {}, "Инцидент ", h("span", { class: "mono" }, id)),
          note(`Мягкий инцидент: оператору видно только число звеньев: ${tree.links}. Суммы и счета остаются у банков.`));
        return;
      }
      render(treeBox, h("h2", {}, "Инцидент ", h("span", { class: "mono" }, id), ` · ${tree.edges.length} звеньев`), incidentTree(tree.edges));
    } catch (e) {
      render(treeBox, h("h2", {}, "Дерево цепочки"), result(e.message, { error: true }));
    }
  }

  const draw = () => {
    render(root,
      h("div", { class: "page-head" },
        h("h1", {}, "Оператор · антифрод-центр"),
        h("div", { class: "row" }, h("span", { class: "muted" }, "Роль:"),
          segmented([["", "Банк"], ["operator", "Оператор"]], auth.role, role => { auth.role = role; draw(); }, "Роль"))),
      note("Дерево цепочки с суммами доступно только роли «оператор». По мягкому инциденту оператор видит лишь факт и число звеньев."),
      store.config.operator_token_required ? tokenForm(() => { auth.role = "operator"; draw(); }) : null,
      h("div", { class: "grid grid-half" }, panel("Инциденты", { body: listBox }), treeBox));
  };
  draw();

  return every(2500, async () => {
    try {
      const { incidents } = await api.incidents();
      render(listBox, dataTable(INCIDENT_COLUMNS, incidents, { onRowClick: i => showTree(i.id), empty: "Инцидентов пока нет." }));
    } catch (e) {
      render(listBox, result(e.message, { error: true }));
    }
  });
}
