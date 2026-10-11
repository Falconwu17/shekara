// Точка входа: тема, конфигурация стенда, панель времени, опрос сервера, маршрутизация.

import { mountSimBar, restoreTheme } from "./components/sim-bar.js";
import { api } from "./core/api.js";
import { $ } from "./core/dom.js";
import { every } from "./core/poll.js";
import { refreshEvents, refreshSim, store } from "./core/store.js";
import { startRouter } from "./router.js";

async function start() {
  restoreTheme();
  store.config = await api.config().catch(() => ({}));
  await refreshSim();
  mountSimBar($("#sim-bar"));
  every(1000, async () => { await refreshSim(); await refreshEvents(); });
  startRouter($("#view"), $("#nav"));
}

start();
