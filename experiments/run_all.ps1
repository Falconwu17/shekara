# Полная сетка эксперимента (раздел 4.6): сначала ключевые режимы на 3 seed, затем остальная сетка.
# Запуск из корня репозитория: powershell -File experiments/run_all.ps1
$py = ".venv\Scripts\python.exe"
New-Item -ItemType Directory -Force experiments\logs | Out-Null
& $py -u -m model.gbt --modes "isolation,L3,L3_nocf,operator,oracle" --seeds "1,2,3" *> experiments\logs\grid_key.log
& $py -u -m model.gbt --modes "F,L2_s0.1,L2_s0.25,L2_s0.5,L1_s0.1,L1_s0.25,L1_s0.5,L1_s2,L1_s4,L3_r2" --seeds "1,2,3" *> experiments\logs\grid_rest.log
& $py -u -m model.metrics *> experiments\logs\metrics.log
& $py -u -m model.view_eval *> experiments\logs\views.log
"done" | Out-File experiments\logs\grid_done.txt
