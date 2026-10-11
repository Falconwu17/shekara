// Создание DOM без строк HTML: данные с сервера всегда попадают в страницу как текст.

const SVG_NS = "http://www.w3.org/2000/svg";

function applyProps(el, props) {
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") el.setAttribute("class", Array.isArray(value) ? value.filter(Boolean).join(" ") : value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2).toLowerCase(), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
}

function appendChildren(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
}

/** h("div", { class: "panel", onClick: fn }, "текст", h("b", {}, 1)) */
export function h(tag, props, ...children) {
  const el = document.createElement(tag);
  applyProps(el, props);
  appendChildren(el, children);
  return el;
}

/** То же для SVG: svg("circle", { cx: 10, cy: 10, r: 4 }) */
export function svg(tag, props, ...children) {
  const el = document.createElementNS(SVG_NS, tag);
  applyProps(el, props);
  appendChildren(el, children);
  return el;
}

/** Заменить содержимое элемента. */
export function render(target, ...children) {
  target.replaceChildren();
  appendChildren(target, children);
}

export function $(selector, root = document) {
  return root.querySelector(selector);
}

/** Значение CSS-токена темы, например cssVar("--accent"). */
export function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}
