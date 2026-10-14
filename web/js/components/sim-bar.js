// Верхняя панель: часы стенда, пуск/пауза, скорость, режим банков, сброс, тема.

import { api, auth } from "../core/api.js";
import { h, render } from "../core/dom.js";
import { clockTime } from "../core/format.js";
import { onSim, refreshSim, resetEvents, store } from "../core/store.js";
import { segmented } from "./ui.js";

const SPEEDS = [[60, "×60"], [600, "×600"], [3600, "×3600"]];

async function control(command) {
  await api.simControl(command);
  await refreshSim();
}

function toggleTheme() {
  const root = document.documentElement;
  const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  root.dataset.theme = current === "dark" ? "light" : "dark";
  try { localStorage.setItem("shekara-theme", root.dataset.theme); } catch { /* приватный режим */ }
}

function askAdminKey() {
  const key = prompt("Ключ администратора стенда");
  if (key) {
    auth.adminToken = key;
    location.reload();
  }
}

function controls(sim) {
  const finished = sim.progress >= 1;
  return [
    h("button", { type: "button", class: "btn btn-primary", disabled: finished,
      onClick: () => control({ action: sim.running ? "pause" : "play" }) },
      finished ? "Готово" : sim.running ? "Пауза" : "Запустить"),
    segmented(SPEEDS, sim.speed, speed => control({ speed }), "Скорость"),
  ];
}

function modeAndReset(sim) {
  return [
    segmented(Object.entries(sim.modes), sim.mode, mode => control({ mode }), "Режим банков"),
    h("button", { type: "button", class: "btn", onClick: async () => { await control({ action: "reset" }); resetEvents(); } }, "Сначала"),
  ];
}

export function mountSimBar(root) {
  const draw = sim => {
    if (!sim) {
      render(root, h("div", { class: "clock" }, store.connected ? "-" : "нет связи с API"));
      return;
    }
    const locked = store.config.admin_required && !auth.adminToken;
    render(root,
      h("div", { class: "clock" }, clockTime(sim.clock), h("small", {}, sim.running ? "идёт" : "пауза")),
      locked ? h("span", { class: "chip chip-ok" }, `стенд идёт сам · ×${sim.speed}`) : controls(sim),
      h("div", { class: "progress", title: "Прогресс тестовых дней" }, h("i", { style: { width: `${(sim.progress * 100).toFixed(1)}%` } })),
      locked ? h("button", { type: "button", class: "btn", onClick: askAdminKey }, "Ключ администратора") : modeAndReset(sim),
      h("button", { type: "button", class: "btn", "aria-label": "Сменить тему", onClick: toggleTheme }, "◐"));
  };
  onSim(draw);
  draw(store.sim);
}

export function restoreTheme() {
  try {
    const saved = localStorage.getItem("shekara-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch { /* приватный режим */ }
}
