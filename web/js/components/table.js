// Таблица по описанию колонок: [{ title, cell: row => узел|текст, align: "right" }].

import { h } from "../core/dom.js";
import { emptyState } from "./ui.js";

export function dataTable(columns, rows, { onRowClick, empty = "Нет данных." } = {}) {
  if (!rows.length) return emptyState(empty);
  const alignClass = column => (column.align === "right" ? "align-right" : "");
  return h("table", { class: "table" },
    h("thead", {}, h("tr", {}, columns.map(column => h("th", { class: alignClass(column) }, column.title)))),
    h("tbody", {}, rows.map(row =>
      h("tr", { class: onRowClick ? "row-link" : "", onClick: onRowClick ? () => onRowClick(row) : null },
        columns.map(column => h("td", { class: alignClass(column) }, column.cell(row)))))));
}
