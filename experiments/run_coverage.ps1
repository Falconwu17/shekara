# Исследовательский прогон охвата (experiments/coverage.py): каждый N: отдельный процесс,
# чтобы память прошлого прогона освобождалась. Запуск из корня:
# powershell -ExecutionPolicy Bypass -File experiments/run_coverage.ps1 [-N "200"]
param([string]$N = "100,200", [string]$Seeds = "1")
$py = ".venv\Scripts\python.exe"
foreach ($n in $N.Split(",")) {
    & $py -u -m experiments.coverage --n $n --seeds $Seeds *> "experiments\logs\coverage_N$n.log"
}
"done" | Out-File experiments\logs\coverage_done.txt
