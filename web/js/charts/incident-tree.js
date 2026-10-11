// Дерево цепочки инцидента для оператора: колонка — шаг цепочки, строки — ветви.

import { cssVar, svg } from "../core/dom.js";
import { integer, shortTime } from "../core/format.js";

const WIDTH = 640;
const ROW_HEIGHT = 70;
const BOX = { w: 96, h: 44 };

const keyOf = node => node.join("|");

function layout(edges) {
  const nodes = [...new Map(edges.flatMap(e => [e.from, e.to]).map(n => [keyOf(n), n])).values()];
  const depth = new Map(nodes.map(n => [keyOf(n), 0]));
  for (let pass = 0; pass < nodes.length; pass++) {
    for (const e of edges) depth.set(keyOf(e.to), Math.max(depth.get(keyOf(e.to)), depth.get(keyOf(e.from)) + 1));
  }
  const columns = new Map();
  for (const n of nodes) {
    const d = depth.get(keyOf(n));
    columns.set(d, [...(columns.get(d) || []), n]);
  }
  const rows = Math.max(1, ...[...columns.values()].map(c => c.length));
  const height = 60 + rows * ROW_HEIGHT;
  const step = (WIDTH - 140) / Math.max(1, columns.size - 1);
  const pos = new Map();
  for (const [d, list] of columns) {
    list.forEach((n, i) => pos.set(keyOf(n), { x: 70 + d * step, y: height / 2 + (i - (list.length - 1) / 2) * ROW_HEIGHT }));
  }
  return { pos, height };
}

function edgeShape(edge, pos) {
  const a = pos.get(keyOf(edge.from)), b = pos.get(keyOf(edge.to));
  const mid = (a.x + b.x) / 2;
  return [
    svg("path", { d: `M${a.x + BOX.w / 2},${a.y} C${mid},${a.y} ${mid},${b.y} ${b.x - BOX.w / 2},${b.y}`,
      fill: "none", stroke: cssVar("--accent"), "stroke-width": 2.5 }),
    svg("text", { x: mid, y: (a.y + b.y) / 2 - 8, "text-anchor": "middle", "font-size": 11, class: "mono", fill: cssVar("--ink-2") },
      `${integer(edge.amount)} · ${shortTime(edge.time)}`),
  ];
}

function nodeShape(key, { x, y }) {
  const [bank, account] = key.split("|");
  return svg("g", {},
    svg("rect", { x: x - BOX.w / 2, y: y - BOX.h / 2, width: BOX.w, height: BOX.h, rx: 8, fill: cssVar("--panel"), stroke: cssVar("--ink-3") }),
    svg("text", { x, y: y - 3, "text-anchor": "middle", "font-size": 12, "font-weight": 600, fill: cssVar("--ink") }, `банк ${bank}`),
    svg("text", { x, y: y + 13, "text-anchor": "middle", "font-size": 10, class: "mono", fill: cssVar("--ink-2") },
      account && account !== "null" ? account : "?"));
}

export function incidentTree(edges) {
  const { pos, height } = layout(edges);
  return svg("svg", { class: "chart", viewBox: `0 0 ${WIDTH} ${height}`, role: "img", "aria-label": "Дерево цепочки инцидента" },
    edges.map(edge => edgeShape(edge, pos)),
    [...pos].map(([key, p]) => nodeShape(key, p)));
}
