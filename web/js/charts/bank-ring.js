// Кольцо банков-участников вокруг хаба: вспышки алертов и дуги шагов инцидентов.

import { cssVar, svg } from "../core/dom.js";

const WIDTH = 680;
const HEIGHT = 560;
const RADIUS = 236;
const NODE_RADIUS = 21;

function positions(banks) {
  const cx = WIDTH / 2, cy = HEIGHT / 2;
  return new Map(banks.map((bank, i) => {
    const angle = -Math.PI / 2 + (i / banks.length) * Math.PI * 2;
    return [bank, { x: cx + RADIUS * Math.cos(angle), y: cy + RADIUS * Math.sin(angle) }];
  }));
}

function hub(cx, cy) {
  return svg("g", {},
    svg("rect", { x: cx - 58, y: cy - 30, width: 116, height: 60, rx: 12, fill: cssVar("--side"), stroke: cssVar("--accent") }),
    svg("text", { x: cx, y: cy - 4, "text-anchor": "middle", fill: "#fff", "font-size": 14, "font-weight": 700, class: "display" }, "ХАБ"),
    svg("text", { x: cx, y: cy + 15, "text-anchor": "middle", fill: "#9FB2B8", "font-size": 10.5 }, "журнал · подпись"));
}

function bankNode(bank, { x, y }, onSelect) {
  return svg("g", { class: "ring-node", tabindex: 0, role: "link", "aria-label": `Банк ${bank}`, onClick: () => onSelect(bank),
    onKeydown: e => e.key === "Enter" && onSelect(bank) },
    svg("circle", { cx: x, cy: y, r: NODE_RADIUS, fill: cssVar("--panel"), stroke: cssVar("--ink-3"), "stroke-width": 1.5 }),
    svg("text", { x, y: y + 4, "text-anchor": "middle", "font-size": 10.5, class: "mono", fill: cssVar("--ink") }, bank));
}

/** Рисует кольцо; возвращает { el, show(events) } для анимации новых событий. */
export function bankRing(banks, onSelect) {
  const pos = positions(banks);
  const cx = WIDTH / 2, cy = HEIGHT / 2;
  const hops = svg("g", {});
  const el = svg("svg", { class: "chart", viewBox: `0 0 ${WIDTH} ${HEIGHT}`, role: "img", "aria-label": "Банки-участники и хаб" },
    svg("circle", { cx, cy, r: RADIUS + 34, fill: "none", stroke: cssVar("--line"), "stroke-dasharray": "4 6" }),
    hops,
    [...pos].map(([bank, p]) => bankNode(bank, p, onSelect)),
    hub(cx, cy));

  function flash({ bank, laundering }) {
    const p = pos.get(bank);
    if (!p) return;
    const ring = svg("circle", { cx: p.x, cy: p.y, r: NODE_RADIUS, fill: "none", "stroke-width": 3,
      stroke: laundering ? cssVar("--risk") : cssVar("--amber") });
    el.append(ring);
    ring.animate?.([{ r: NODE_RADIUS, opacity: 1 }, { r: NODE_RADIUS * 2, opacity: 0 }], { duration: 1400 });
    setTimeout(() => ring.remove(), 1400);
  }

  function arc({ bank, to_bank: toBank }) {
    const a = pos.get(bank), b = pos.get(toBank);
    if (!a || !b) return;
    const path = svg("path", { d: `M${a.x},${a.y} Q${cx},${cy} ${b.x},${b.y}`, fill: "none", stroke: cssVar("--accent"), "stroke-width": 3 });
    hops.append(path);
    setTimeout(() => path.remove(), 6000);
  }

  return {
    el,
    show(events) {
      for (const event of events.slice(-30)) {
        if (event.kind === "alert") flash(event);
        if (event.kind === "hop") arc(event);
      }
    },
  };
}
