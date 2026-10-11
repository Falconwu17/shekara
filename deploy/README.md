# Стенд ШЕКАРЫ в Oracle Cloud

Итог: сайт по адресу `https://<ваш-IP>.sslip.io` (или своё имя) с настоящим сертификатом. Стенд сам проигрывает тестовые дни по кругу. Управление временем закрыто ключом администратора, вид оператора — токеном. Тариф Always Free: денег не списывают.

## 1. Аккаунт и сервер (делаете вы, ~20 минут)

1. Зарегистрируйтесь на oracle.com/cloud/free. Карта нужна только для проверки. Регион выбирайте поближе (Frankfurt или любой другой с ARM), сменить его потом нельзя.
2. **Compute → Instances → Create instance**:
   - Image: **Canonical Ubuntu 22.04** (или 24.04);
   - Shape: **Ampere → VM.Standard.A1.Flex**, **2 OCPU, 12 GB** — бесплатно (до 4 OCPU / 24 GB);
   - Networking: оставить по умолчанию, **Assign a public IPv4 address** — да;
   - SSH keys: **Generate a key pair for me** → скачайте приватный ключ (например, `C:\keys\oracle.key`).
3. Откройте порты: Instance → Subnet → **Security List → Add Ingress Rules**:
   source `0.0.0.0/0`, TCP, destination port **80**; ещё одно правило — порт **443**.
4. Запишите **Public IP** сервера.

Если A1 пишет «Out of capacity» — повторите позже или выберите другой Availability Domain.

## 2. Настройка сервера (одна команда)

```powershell
ssh -i C:\keys\oracle.key ubuntu@<IP>
```
На сервере:
```bash
curl -fsSL https://raw.githubusercontent.com/Falconwu17/shekara/main/deploy/bootstrap.sh | bash
exit
```
Скрипт ставит Docker, открывает 80/443 во встроенном файрволе, клонирует репозиторий и создаёт `~/shekara/deploy/.env` с адресом и случайными ключами. Ключи он печатает — сохраните их.

## 3. Данные и запуск (с вашего компьютера, из `D:\shekara`)

```powershell
powershell -ExecutionPolicy Bypass -File deploy\push.ps1 -Server <IP> -Key C:\keys\oracle.key
```
Скрипт собирает архив (~200 МБ: базы банков и результаты эксперимента), отправляет его на сервер, собирает образ и поднимает стенд. Первая сборка на ARM — 3–5 минут, старт стенда — около 20 секунд.

Сайт: `https://<IP с дефисами>.sslip.io`, например `https://158-101-12-34.sslip.io`.

## Управление

- **Ключ администратора** (`SHEKARA_ADMIN_TOKEN` в `~/shekara/deploy/.env`): на сайте кнопка «Ключ администратора» — после ввода появляются пуск/пауза, скорость, режим и сброс.
- **Токен оператора** (`SHEKARA_OPERATOR_TOKEN`): вводится на странице «Оператор». Для жюри его можно указать в заявке.
- Обновить код: снова `deploy\push.ps1`.
- Логи: `ssh ... "cd ~/shekara && docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env logs -f app"`.

## Своё имя вместо sslip.io (по желанию, бесплатно)

duckdns.org → войти через GitHub → создать имя `shekara-kz` → вписать IP сервера. Затем на сервере в `deploy/.env` поставить `SHEKARA_DOMAIN=shekara-kz.duckdns.org` и выполнить `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d`. Caddy сам получит новый сертификат.

## Что защищено

HTTPS с HSTS; управление временем — только с ключом администратора (сравнение с постоянным временем); роль оператора — по токену; действия банков ограничены 30 в минуту на IP; запрет встраивания во фрейм и подмены типов; контейнер перезапускается сам и проверяется healthcheck-ом.
