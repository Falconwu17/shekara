// Общее состояние приложения: часы стенда, лента событий, конфигурация. Страницы подписываются на изменения.

import { api } from "./api.js";

const MAX_EVENTS = 400;
const listeners = { sim: new Set(), events: new Set() };

export const store = {
  config: {},
  sim: null,
  events: [],
  lastSeq: 0,
  connected: true,
};

/** Подписка на обновление часов стенда; возвращает функцию отписки. */
export function onSim(callback) {
  listeners.sim.add(callback);
  return () => listeners.sim.delete(callback);
}

/** Подписка на новые события протокола (приходят пачкой); возвращает функцию отписки. */
export function onEvents(callback) {
  listeners.events.add(callback);
  return () => listeners.events.delete(callback);
}

export async function refreshSim() {
  try {
    const sim = await api.simState();
    if (sim.seq < store.lastSeq) {
      // стенд начал проигрывание заново: лента тоже начинается заново
      store.lastSeq = 0;
      store.events = [];
    }
    store.sim = sim;
    store.connected = true;
  } catch {
    store.connected = false;
  }
  listeners.sim.forEach(callback => callback(store.sim));
}

export async function refreshEvents() {
  try {
    const { events } = await api.simEvents(store.lastSeq);
    if (!events.length) return;
    store.lastSeq = events[events.length - 1].seq;
    store.events = [...store.events, ...events].slice(-MAX_EVENTS);
    listeners.events.forEach(callback => callback(events));
  } catch {
    // следующий опрос попробует снова
  }
}

export function resetEvents() {
  store.lastSeq = 0;
  store.events = [];
}
