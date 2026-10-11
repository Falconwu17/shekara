// Графики результатов: полосы PR-AUC по режимам и кривая «раскрытие ↔ качество ↔ ε».

import { cssVar, h, svg } from "../core/dom.js";
import { decimal } from "../core/format.js";

/** rows = [{ label, value, highlight }] */
export function barList(rows) {
  const max = Math.max(...rows.map(r => r.value)) * 1.08;
  return h("div", { class: "bars" }, rows.map(row =>
    h("div", { class: "bar-row" },
      h("span", {}, row.label),
      h("div", { class: "bar-track" },
        h("div", { class: "bar-fill", style: { width: `${(row.value / max) * 100}%`, background: row.highlight ? cssVar("--accent") : cssVar("--ink-3") } })),
      h("span", { class: "bar-value" }, decimal(row.value)))));
}

const SIZE = { w: 540, h: 280 };
const MARGIN = { left: 48, right: 34, top: 14, bottom: 40 };

function scales(points, baselines) {
  const logs = points.map(p => Math.log10(p.eps));
  const x0 = Math.floor(Math.min(...logs)), x1 = Math.ceil(Math.max(...logs));
  const values = [...points.map(p => p.ap), ...baselines.map(b => b.value)];
  const y0 = Math.floor(Math.min(...values) * 100 - 1) / 100, y1 = Math.ceil(Math.max(...values) * 100 + 1) / 100;
  const x = eps => MARGIN.left + ((Math.log10(eps) - x0) / (x1 - x0)) * (SIZE.w - MARGIN.left - MARGIN.right);
  const y = ap => MARGIN.top + (1 - (ap - y0) / (y1 - y0)) * (SIZE.h - MARGIN.top - MARGIN.bottom);
  return { x, y, x0, x1, y0, y1 };
}

function grid({ x, y, x0, x1, y0, y1 }) {
  const parts = [];
  for (let e = x0; e <= x1; e++) {
    parts.push(svg("line", { x1: x(10 ** e), y1: MARGIN.top, x2: x(10 ** e), y2: SIZE.h - MARGIN.bottom, stroke: cssVar("--line") }),
      svg("text", { x: x(10 ** e), y: SIZE.h - MARGIN.bottom + 16, "text-anchor": "middle", "font-size": 11, class: "mono", fill: cssVar("--ink-3") },
        (10 ** e).toLocaleString("ru-RU")));
  }
  for (let v = y0; v <= y1 + 1e-9; v += 0.01) {
    parts.push(svg("line", { x1: MARGIN.left, y1: y(v), x2: SIZE.w - MARGIN.right, y2: y(v), stroke: cssVar("--line") }),
      svg("text", { x: MARGIN.left - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, class: "mono", fill: cssVar("--ink-3") }, decimal(v, 2)));
  }
  return parts;
}

/** points = [{ level, sigma, eps, eps_week, ap }], baselines = [{ label, value, color }], onHover(point) */
export function privacyChart(points, baselines, onHover) {
  const s = scales(points, baselines);
  const lines = baselines.map(b => [
    svg("line", { x1: MARGIN.left, y1: s.y(b.value), x2: SIZE.w - MARGIN.right, y2: s.y(b.value), stroke: b.color, "stroke-dasharray": "5 4" }),
    svg("text", { x: SIZE.w - MARGIN.right - 4, y: s.y(b.value) - 5, "text-anchor": "end", "font-size": 11, fill: b.color }, b.label),
  ]);
  const series = ["L1", "L2"].map(level => {
    const color = level === "L1" ? cssVar("--amber") : cssVar("--accent");
    const levelPoints = points.filter(p => p.level === level).sort((a, b) => a.eps - b.eps);
    return [
      svg("polyline", { points: levelPoints.map(p => `${s.x(p.eps)},${s.y(p.ap)}`).join(" "), fill: "none", stroke: color, "stroke-width": 2 }),
      levelPoints.map(p => svg("circle", { cx: s.x(p.eps), cy: s.y(p.ap), r: 6, fill: color, stroke: cssVar("--panel"), "stroke-width": 2,
        tabindex: 0, class: "point", onMouseenter: () => onHover(p), onFocus: () => onHover(p) })),
    ];
  });
  return svg("svg", { class: "chart", viewBox: `0 0 ${SIZE.w} ${SIZE.h}`, role: "img", "aria-label": "Качество в зависимости от ε" }, grid(s), lines, series);
}
