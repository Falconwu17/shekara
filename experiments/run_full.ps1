# Полный эксперимент одной командой: сетка режимов × 3 seed → проверка при зачислении, сочетание моделей,
# метрики, удержание, демо → каскад инцидентов → чек-лист 6.7 (с переобучением). Около 2 часов на CPU.
# Запуск из корня: powershell -ExecutionPolicy Bypass -File experiments/run_full.ps1
$py = ".venv\Scripts\python.exe"
Remove-Item -ErrorAction SilentlyContinue experiments\logs\grid_done.txt, experiments\logs\after_done.txt, experiments\logs\full_done.txt
& powershell -ExecutionPolicy Bypass -File experiments\run_all.ps1
& powershell -ExecutionPolicy Bypass -File experiments\run_after.ps1
& $py -u -m trace.cascade *> experiments\logs\cascade.log
& $py -u -m experiments.checks --retrain *> experiments\logs\checks.log
"done" | Out-File experiments\logs\full_done.txt
