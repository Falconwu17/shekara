# Фронтенд стенда

Без сборки и зависимостей: обычные ES-модули, браузер загружает их как есть. FastAPI (`online/api.py`) отдаёт эту папку по адресу `/`, данные берутся из `/v1/...` того же сервера.

Запуск: `.venv\Scripts\python -m online.api`, затем открыть http://127.0.0.1:8000. После правки файла достаточно обновить страницу.

## Устройство

```
web/
  index.html            каркас: навигация, панель времени, <main id="view">
  css/
    tokens.css          цвета и шрифты, светлая и тёмная тема (только переменные)
    base.css            сброс, заголовки, .mono / .muted
    layout.css          каркас страницы, сетки .grid-half / .grid-wide, .row, .stack, .scroll
    components.css      кнопки, панели, таблицы, метки, лента, графики
  js/
    main.js             точка входа: тема → config → панель времени → опрос → роутер
    router.js           #/имя/параметры → pages/имя.js; список страниц: ROUTES
    core/               без DOM-разметки страниц
      api.js            все запросы к серверу (объект api) и ключи доступа (auth)
      store.js          общее состояние: часы стенда, лента событий; onSim / onEvents
      dom.js            h() и svg(): создание элементов, render(): заменить содержимое
      format.js         числа, проценты, суммы, время по-русски
      poll.js           every(ms, fn): периодический опрос с остановкой
    components/         переиспользуемые куски интерфейса
      ui.js             chip, scoreBar, panel, kpiStrip, segmented, note, result, toast
      table.js          dataTable(columns, rows) по описанию колонок
      event-feed.js     лента событий протокола
      sim-bar.js        верхняя панель времени
    charts/             SVG-графики
      bank-ring.js      кольцо банков вокруг хаба
      incident-tree.js  дерево цепочки инцидента
      results-charts.js полосы PR-AUC и кривая цены приватности
    pages/              по файлу на раздел, у каждого mount(root, params)
```

Зависимости идут сверху вниз: `pages → charts/components → core`. `core` не знает о страницах.

## Правила

- **Разметка только через `h()`**. Никакого `innerHTML`: данные с сервера всегда попадают в страницу как текст. Тест `tests/test_web.py` это проверяет.
- **Запросы только через `api`** из `core/api.js`. Новый эндпоинт: новый метод там.
- **Цвета только через переменные** из `tokens.css` (`var(--accent)`, в SVG: `cssVar("--accent")`). Тогда обе темы работают сами.
- **Страница сама убирает за собой**: `mount` возвращает функцию очистки, которая останавливает опросы и отписывается от `store`. Роутер вызывает её при уходе со страницы.

## Как добавить страницу

1. Создать `js/pages/alerts.js`:

   ```js
   import { panel } from "../components/ui.js";
   import { api } from "../core/api.js";
   import { h, render } from "../core/dom.js";
   import { every } from "../core/poll.js";

   export function mount(root, params) {
     const body = h("div");
     render(root, h("div", { class: "page-head" }, h("h1", {}, "Алерты")), panel("Сегодня", { body }));
     return every(2000, async () => {
       const { banks } = await api.banks();
       render(body, `${banks.length} банков`);
     });
   }
   ```

2. Подключить в `js/router.js`: `import * as alerts from "./pages/alerts.js";` и добавить `alerts` в `ROUTES`.
3. Добавить ссылку в `index.html`: `<a href="#/alerts" data-route="alerts">Алерты</a>`.
4. `pytest tests/test_web.py`: проверит, что импорты и маршрут на месте.

## Таблица

```js
dataTable([
  { title: "Банк", cell: row => row.bank },
  { title: "Порог", align: "right", cell: row => decimal(row.threshold) },
], rows, { onRowClick: row => openBank(row.bank), empty: "Нет банков." });
```
