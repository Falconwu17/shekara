// Маршруты по адресу после #: «#/bank/B01» → страница bank с параметрами ["B01"].
// Чтобы добавить страницу: создать js/pages/<имя>.js с export function mount(root, params) и вписать её в ROUTES.

import * as audit from "./pages/audit.js";
import * as bank from "./pages/bank.js";
import * as network from "./pages/network.js";
import * as operator from "./pages/operator.js";
import * as results from "./pages/results.js";

const ROUTES = { network, bank, operator, audit, results };
const DEFAULT_ROUTE = "network";

function parseHash() {
  const [name, ...params] = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  return ROUTES[name] ? { name, params: params.map(decodeURIComponent) } : { name: DEFAULT_ROUTE, params: [] };
}

/** Следит за адресом и монтирует нужную страницу в root; ссылки в nav получают класс active. */
export function startRouter(root, nav) {
  let cleanup = () => {};

  function go() {
    const { name, params } = parseHash();
    cleanup();
    root.replaceChildren();
    root.scrollTop = 0;
    cleanup = ROUTES[name].mount(root, params) || (() => {});
    nav.querySelectorAll("a[data-route]").forEach(link => link.classList.toggle("active", link.dataset.route === name));
  }

  addEventListener("hashchange", go);
  go();
}
