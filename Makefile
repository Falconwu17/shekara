# Цели по README / разделу 6.6 docs/SHEKARA.md. Нужен скачанный AMLworld в data/raw/.
# Порядок: stats (решение split_decision.json) → data (базы банков) → case (демо-паттерн).
# experiment и demo — заглушки до модели. На Windows — `py`, не `python` (см. STATUS.md).

.PHONY: stats data case experiment demo test

stats:
	py -m data.stats

data:
	py -m data.split

case:
	py -m demo.pick_case

experiment:
	py -m experiments.run

demo:
	py -m demo.replay

test:
	py -m pytest
