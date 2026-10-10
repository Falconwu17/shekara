# Исследовательский прогон охвата (experiments/coverage.py). Запуск из корня:
# powershell -ExecutionPolicy Bypass -File experiments/run_coverage.ps1
$py = ".venv\Scripts\python.exe"
& $py -u -m experiments.coverage --n "100,200" --seeds "1" *> experiments\logs\coverage.log
"done" | Out-File experiments\logs\coverage_done.txt
