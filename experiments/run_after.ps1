# После сетки: проверка при зачислении, комбинированные режимы, метрики, удержание, демо.
# Запуск из корня: powershell -ExecutionPolicy Bypass -File experiments/run_after.ps1
$py = ".venv\Scripts\python.exe"
while (-not (Test-Path experiments\logs\grid_done.txt)) { Start-Sleep -Seconds 60 }
& $py -u -m model.inbound --modes "isolation,L3,L2_s0.25,L1_s0.25" --seeds "1,2,3" *> experiments\logs\inbound.log
& $py -u -m model.combine *> experiments\logs\combine.log
& $py -u -m model.metrics *> experiments\logs\metrics.log
& $py -u -m model.view_eval *> experiments\logs\views.log
foreach ($s in 1, 2, 3) { & $py -u -m trace.replay_all --seed $s *> "experiments\logs\retention_s$s.log"; Copy-Item experiments\results\retention.csv "experiments\results\retention_s$s.csv" }
& $py -u -m demo.replay --seed 1 *> experiments\logs\demo.log
"done" | Out-File experiments\logs\after_done.txt
