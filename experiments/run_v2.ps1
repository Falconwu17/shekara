# Вторая версия эксперимента (17.10.2026): попарные признаки во всех режимах (model/gbt.py, PAIR_FEATURES).
# Полная цепочка: сетка → проверка при зачислении, сочетание, метрики, удержание → каскад → чек-лист 6.7.
# Запуск из корня: powershell -ExecutionPolicy Bypass -File experiments/run_v2.ps1
$py = ".venv\Scripts\python.exe"
Remove-Item -ErrorAction SilentlyContinue experiments\logs\grid_done.txt, experiments\logs\after_done.txt
& powershell -ExecutionPolicy Bypass -File experiments\run_all.ps1
& powershell -ExecutionPolicy Bypass -File experiments\run_after.ps1
& $py -u -m trace.cascade *> experiments\logs\cascade.log
& $py -u -m experiments.checks --retrain *> experiments\logs\checks.log
"done" | Out-File experiments\logs\v2_done.txt
