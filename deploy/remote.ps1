# Выполнить команду на сервере стенда или просто зайти на него по SSH.
#
# Запуск из корня репозитория:
#   .\deploy\remote.ps1                                  # интерактивно: зайти на сервер (выход: exit)
#   .\deploy\remote.ps1 "hostname; free -h"              # выполнить одну команду и вернуться
#   .\deploy\remote.ps1 "docker compose -f ~/shekara/deploy/docker-compose.prod.yml ps"
#
# Ключ лежит локально (в git не попадает). Другой сервер/ключ: -Server / -Key.
param(
    [Parameter(Position = 0)][string]$Cmd = "",
    [string]$Server = "89.168.115.156",
    [string]$Key = "C:\keys\oracle.key"
)

if (-not (Test-Path $Key)) { throw "нет SSH-ключа: $Key (укажи путь через -Key)" }

$sshArgs = @("-i", $Key, "-o", "StrictHostKeyChecking=accept-new", "ubuntu@$Server")
if ($Cmd) {
    & ssh @sshArgs $Cmd
} else {
    Write-Host "Заходим на сервер $Server. Выход: exit или Ctrl+D." -ForegroundColor Cyan
    & ssh @sshArgs
}

