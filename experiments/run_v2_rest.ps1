# Досчёт второй версии (17.10.2026): остальная сетка и контроль F на 3 seed, затем цепочка после сетки.
# Запуск из корня: powershell -ExecutionPolicy Bypass -File experiments/run_v2_rest.ps1
$py = ".venv\Scripts\python.exe"
Remove-Item -ErrorAction SilentlyContinue experiments\logs\after_done.txt, experiments\logs\v2_done.txt
& $py -u -m model.gbt --modes "F,L1_s4,L2_s0.1,L2_s0.25,L2_s0.5,L1_s0.1,L1_s0.25,L1_s0.5,L1_s2,L3_r2" --seeds "1,2,3" *> experiments\logs\grid_rest.log
& $py -u -m model.metrics *> experiments\logs\metrics.log
& $py -u -m model.view_eval *> experiments\logs\views.log
"done" | Out-File experiments\logs\grid_done.txt
& powershell -ExecutionPolicy Bypass -File experiments\run_after.ps1
& $py -u -m trace.cascade *> experiments\logs\cascade.log
& $py -u -m experiments.checks --retrain *> experiments\logs\checks.log
"done" | Out-File experiments\logs\v2_done.txt
