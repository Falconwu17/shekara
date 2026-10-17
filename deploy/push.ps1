# Заливка данных и запуск стенда на сервере Oracle Cloud. Запуск из корня репозитория на Windows:
#   powershell -ExecutionPolicy Bypass -File deploy\push.ps1 -Server 158.101.12.34 -Key C:\keys\oracle.key
param(
  [Parameter(Mandatory = $true)][string]$Server,
  [Parameter(Mandatory = $true)][string]$Key,
  [string]$User = "ubuntu"
)
$ErrorActionPreference = "Stop"
$bundle = "shekara-data.tgz"
$preds = @("experiments/results/preds/inbound_isolation_s1.parquet", "experiments/results/preds/inbound_C_L3_s1.parquet",
           "experiments/results/preds/isolation_s1_nodes.parquet")
foreach ($p in $preds) { if (-not (Test-Path $p)) { throw "нет ${p}: сначала эксперимент (README)" } }
$results = Get-ChildItem experiments/results -File | Where-Object { $_.Extension -in ".csv", ".json", ".md" } | ForEach-Object { "experiments/results/$($_.Name)" }

Write-Host "== Архив данных"
tar -czf $bundle data/processed @results @preds
Write-Host ("   {0:N0} МБ" -f ((Get-Item $bundle).Length / 1MB))

Write-Host "== Отправка на $Server"
scp -i $Key -o StrictHostKeyChecking=accept-new $bundle "${User}@${Server}:~/"

Write-Host "== Распаковка и запуск"
# Архив кладёт итоги эксперимента в рабочую копию git, поэтому прошлая заливка выглядит для git как
# локальная правка и ломает pull. Эти файлы сначала возвращаются к версии из git: архив тут же их перезапишет.
$remote = "set -e; cd ~/shekara && git checkout -- experiments/results && git pull --ff-only && " +
          "tar -xzf ~/$bundle -C ~/shekara && " +
          "docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build && " +
          "docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env ps"
ssh -i $Key "${User}@${Server}" $remote
$code = $LASTEXITCODE
Remove-Item $bundle
if ($code -ne 0) { throw "на сервере команда завершилась с ошибкой (код $code): стенд не обновлён, см. вывод выше" }
Write-Host "Готово. Адрес сайта: SHEKARA_DOMAIN из ~/shekara/deploy/.env на сервере."
