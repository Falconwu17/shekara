# ШЕКАРА (SHEKARA)

Межбанковский поиск сетей дропов без обмена клиентскими данными. Проект на конкурс АБРК
2026 (трек AI for Finance / Digital Banking). Полное описание идеи, архитектуры, плана
и материалов для подачи — в [`docs/SHEKARA.md`](docs/SHEKARA.md) (скопировано из решения
арены, раздел за разделом).

## Быстрый старт

```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash; на macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

make stats        # подневная статистика, полные дни, выбор N участников → data/split_decision.json
make data         # базы банков-участников по этому решению (data/processed/banks/*.duckdb)
make case         # выбор демо-паттерна по трём уровням правил
make experiment   # обучение и метрики по всем режимам (ещё не написано)
make demo         # проигрывание тестового паттерна в дашборде (ещё не написано)
make test         # тесты
```

Если `make` не установлен (обычно на Windows), те же команды запускаются напрямую:
`py -m data.stats`, `py -m data.split`, `py -m demo.pick_case`, `py -m pytest`.
`data/split_decision.json` после первого `make stats` нужно закоммитить до обучения модели
и больше не менять (раздел 3.3); повторный запуск его не перезапишет без `--force`.

## Датасет

Нужен **IBM Transactions for Anti Money Laundering (AMLworld)**, вариант **HI-Small**,
с Kaggle: https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml

У модели нет доступа к Kaggle-аккаунту, поэтому скачать датасет может только человек:

1. Зарегистрироваться на kaggle.com (если ещё нет аккаунта).
2. Settings → API → Create New Token — скачается `kaggle.json`.
3. Положить его в `~/.kaggle/kaggle.json` (Windows: `C:\Users\<имя>\.kaggle\kaggle.json`).
4. `pip install kaggle`
5. `kaggle datasets download -d ealtman2019/ibm-transactions-for-anti-money-laundering-aml -p data/raw --unzip`
6. Положить файл `HI-Small_Trans.csv` (и `HI-Small_Patterns.txt`, если есть отдельно) в `data/raw/`.

После этого `make stats` и `make data` заработают.

## Структура репозитория

См. раздел 6.6 в `docs/SHEKARA.md`. Статус модулей — в `STATUS.md`.

## Статус

Смотри [`STATUS.md`](STATUS.md) — что уже написано и работает, что ждёт датасет,
что в плане дальше.
