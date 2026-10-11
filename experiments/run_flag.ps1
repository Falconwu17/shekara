# Проверка гипотезы «польза — от факта выпуска»: режим F на N = 30 (основной) и N = 100/200 (исследовательские).
# Запуск из корня: powershell -ExecutionPolicy Bypass -File experiments/run_flag.ps1
$py = ".venv\Scripts\python.exe"
& $py -u -m model.gbt --modes "F" --seeds "1,2,3" *> experiments\logs\flag_N30.log
& $py -u -m experiments.coverage --n 100 --seeds "1" --modes "F" *> experiments\logs\flag_N100_s1.log
& $py -u -m experiments.coverage --n 200 --seeds "1" --modes "F" *> experiments\logs\flag_N200_s1.log
& $py -u -m experiments.coverage --n 200 --seeds "2,3" *> experiments\logs\flag_N200_s23.log
& $py -u -m experiments.coverage --n 100 --seeds "2,3" *> experiments\logs\flag_N100_s23.log
"done" | Out-File experiments\logs\flag_done.txt
