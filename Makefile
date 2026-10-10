# Цели по README / разделу 6.6 docs/SHEKARA.md. Нужен скачанный AMLworld в data/raw/.
# Порядок: stats (решение split_decision.json) → data (базы банков) → case (демо-паттерн).
# Окружение — .venv на диске D (py -m venv .venv; .venv\Scripts\python -m pip install -r requirements.txt).
PY ?= .venv/Scripts/python.exe

.PHONY: stats data case experiment metrics demo test

stats:
	$(PY) -m data.stats

data:
	$(PY) -m data.split

case:
	$(PY) -m demo.pick_case

experiment:
	$(PY) -m model.train_rounds
	$(PY) -m model.metrics

metrics:
	$(PY) -m model.metrics

demo:
	$(PY) -m demo.replay

test:
	$(PY) -m pytest
