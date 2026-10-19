"""Telegram-бот аналитика банка: алерты стенда с объяснением и решение аналитика одной кнопкой.

Работает в процессе стенда (online/api.py), если задан TELEGRAM_BOT_TOKEN. Long polling getUpdates,
без вебхука и без сторонних библиотек. Аналитик подписывается на свой банк (/bank 0116); каждый алерт
этого банка приходит сообщением с причинами модели (model/explain.py) и кнопками:
«Открыть инцидент»: подтверждённый инцидент на стенде, шаги по исходящим переводам, запись в журнале хаба;
«Ложная тревога»: событие в ленте протокола стенда.

Переменные окружения: TELEGRAM_BOT_TOKEN; TELEGRAM_ALLOWED_CHATS (через запятую, необязательно: иначе
бот отвечает всем); SHEKARA_PUBLIC_URL (необязательно: кнопка «Открыть на стенде»).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import date, datetime
from pathlib import Path

API = "https://api.telegram.org/bot{token}/{method}"
MAX_ALERTS_PER_MIN = 6  # на один чат: при быстрой симуляции алерты не превращаются в спам


def load_env_file(path: Path = Path("deploy/.env.local")) -> None:
    """Локальный запуск: переменные из deploy/.env.local (файл в .gitignore), если они не заданы."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key, value.strip())


def _amount(value, currency: str | None) -> str:
    try:
        text = f"{float(value):,.2f}".replace(",", " ").replace(".", ",")
    except (TypeError, ValueError):
        text = str(value)
    return f"{text} {currency or ''}".strip()


class AnalystBot:
    def __init__(self, sim, token: str, allowed: set[int] | None = None, public_url: str | None = None, log=print):
        self.sim, self.token, self.allowed, self.public_url, self.log = sim, token, allowed, public_url, log
        self.subs: dict[int, str] = {}  # чат → банк
        self.sent: dict[int, deque] = {}  # чат → время последних отправок
        self.offset = 0
        self.last_seq = sim.seq
        self.stop = threading.Event()

    # ---------- Telegram API ----------
    def call(self, method: str, **params) -> dict:
        req = urllib.request.Request(API.format(token=self.token, method=method), data=json.dumps(params).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            self.log(f"telegram {method}: {e}")
            return {"ok": False}

    def send(self, chat: int, text: str, buttons: list[list[dict]] | None = None) -> None:
        params = {"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        self.call("sendMessage", **params)

    # ---------- запуск ----------
    def start(self) -> None:
        me = self.call("getMe")
        if not me.get("ok"):
            self.log("telegram: токен не принят, бот не запущен")
            return
        self.log(f"telegram: бот @{me['result']['username']} запущен")
        self.call("setMyCommands", commands=[
            {"command": "start", "description": "Выбрать банк"},
            {"command": "last", "description": "Последний алерт моего банка"},
            {"command": "bank", "description": "Сменить банк: /bank 0116"},
            {"command": "stop", "description": "Не присылать алерты"},
        ])
        threading.Thread(target=self.poll_updates, daemon=True, name="tg-updates").start()
        threading.Thread(target=self.push_alerts, daemon=True, name="tg-alerts").start()

    def poll_updates(self) -> None:
        while not self.stop.is_set():
            r = self.call("getUpdates", offset=self.offset, timeout=25, allowed_updates=["message", "callback_query"])
            for u in r.get("result", []):
                self.offset = u["update_id"] + 1
                try:
                    if "message" in u:
                        self.on_message(u["message"])
                    elif "callback_query" in u:
                        self.on_callback(u["callback_query"])
                except Exception as e:  # одно плохое сообщение не роняет бота
                    self.log(f"telegram: ошибка обработки {e!r}")
            if not r.get("ok"):
                time.sleep(5)

    def push_alerts(self) -> None:
        while not self.stop.is_set():
            time.sleep(1)
            if self.sim.seq < self.last_seq:  # стенд начал проигрывание заново
                self.last_seq = 0
            events = self.sim.events_since(self.last_seq, limit=500)
            if events:
                self.last_seq = events[-1]["seq"]
            for e in events:
                if e["kind"] != "alert":
                    continue
                for chat, bank in list(self.subs.items()):
                    if bank == e["bank"] and self._may_send(chat):
                        self.send_alert(chat, e)

    def _may_send(self, chat: int) -> bool:
        q = self.sent.setdefault(chat, deque())
        now = time.time()
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= MAX_ALERTS_PER_MIN:
            return False
        q.append(now)
        return True

    # ---------- сообщения ----------
    def allowed_chat(self, chat: int) -> bool:
        return not self.allowed or chat in self.allowed

    def on_message(self, m: dict) -> None:
        chat, text = m["chat"]["id"], (m.get("text") or "").strip()
        if not self.allowed_chat(chat):
            self.send(chat, "Это демонстрационный бот стенда ШЕКАРА; доступ выдаёт автор проекта.")
            return
        cmd, _, arg = text.partition(" ")
        if cmd in ("/start", "/help"):
            banks = self.busiest_banks(12)
            rows = [[{"text": b, "callback_data": f"sub|{b}"} for b in banks[i:i + 4]] for i in range(0, len(banks), 4)]
            self.send(chat, "<b>ШЕКАРА · кабинет аналитика</b>\nВыберите банк, алерты которого присылать, "
                            "или отправьте <code>/bank 0116</code>. Отписаться: /stop.", rows)
        elif cmd == "/bank" and arg.strip():
            self.subscribe(chat, arg.strip())
        elif cmd == "/last":
            self.send_last(chat)
        elif cmd == "/stop":
            self.subs.pop(chat, None)
            self.send(chat, "Алерты больше не присылаются.")
        else:
            self.send(chat, "Команды: /start, /bank &lt;код банка&gt;, /last (последний алерт), /stop.")

    def subscribe(self, chat: int, bank: str) -> None:
        if bank not in self.sim.participants:
            self.send(chat, f"Банка {bank} нет среди участников стенда.")
            return
        self.subs[chat] = bank
        self.log(f"telegram: чат {chat} подписан на банк {bank}")
        self.send(chat, f"Подписка на алерты банка <b>{bank}</b>. Новые алерты придут сами; последний: ниже.")
        self.send_last(chat)

    def busiest_banks(self, n: int) -> list[str]:
        """Банки с наибольшим числом алертов за сегодня: на них демонстрация идёт быстрее."""
        day = self.sim.clock.date()
        counts = {b: 0 for b in self.sim.participants}
        for (bank, _, d) in list(self.sim.alert_info):
            if d == day and bank in counts:
                counts[bank] += 1
        return sorted(counts, key=lambda b: -counts[b])[:n]

    def send_last(self, chat: int) -> None:
        bank = self.subs.get(chat)
        if not bank:
            self.send(chat, "Сначала выберите банк: /start.")
            return
        last = [e for e in self.sim.events_since(0, limit=5000) if e["kind"] == "alert" and e["bank"] == bank]
        if last:
            self.send_alert(chat, last[-1])
        else:
            self.send(chat, f"У банка {bank} алертов пока не было. Выберите другой банк: /start.")

    def send_alert(self, chat: int, e: dict) -> None:
        t = datetime.fromisoformat(e["t"])
        info = self.sim.alert_info.get((e["bank"], e["account"], t.date()), {})
        thr = self.sim.thr[self.sim.mode].get(e["bank"], (None, None))[0]
        why = info.get("why") or []
        lines = [f"🚨 <b>Алерт · банк {e['bank']}</b>",
                 f"Счёт <code>{e['account']}</code> · балл <b>{e['score']:.2f}</b>" + (f" при пороге {thr:.2f}" if thr else ""),
                 f"Зачисление из банка {e['from_bank']}: {_amount(info.get('amount'), info.get('currency'))} · {t:%d.%m %H:%M}"]
        if why:
            lines.append("\n<b>Почему</b>")
            lines += [f"{'▲' if w['contrib'] >= 0 else '▼'} {w['text']}" for w in why]
        buttons = [[{"text": "Открыть инцидент", "callback_data": f"inc|{e['bank']}|{e['account']}"},
                    {"text": "Ложная тревога", "callback_data": f"fp|{e['bank']}|{e['account']}"}]]
        if self.public_url:
            buttons.append([{"text": "Открыть на стенде", "url": f"{self.public_url.rstrip('/')}/#/bank/{e['bank']}"}])
        self.send(chat, "\n".join(lines), buttons)

    def on_callback(self, q: dict) -> None:
        chat = q["message"]["chat"]["id"]
        kind, *rest = q.get("data", "").split("|")
        if not self.allowed_chat(chat):
            self.call("answerCallbackQuery", callback_query_id=q["id"], text="Нет доступа")
            return
        if kind == "sub":
            self.call("answerCallbackQuery", callback_query_id=q["id"])
            self.subscribe(chat, rest[0])
            return
        bank, account = rest
        who = q["from"].get("first_name", "аналитик")
        with self.sim.lock:
            if kind == "inc":
                r = self.sim.open_incident(bank, account, "confirmed")
                if "incident_id" in r:
                    hops = self.sim.incidents[r["incident_id"]]["hops"]
                    step = (f"шагов по ссылке к следующим банкам: {hops}" if hops else
                            "исходящих переводов пока нет, шаг уйдёт, как только деньги двинутся дальше")
                    done = f"✅ {who} открыл инцидент {r['incident_id']}: запись в журнале хаба; {step}"
                else:
                    done = f"Инцидент не открыт: {r.get('error')}"
            else:
                self.sim._event("dismiss", bank=bank, account=account, by=who)
                done = f"☑️ {who} закрыл алерт как ложный; событие в ленте стенда"
        self.log(f"telegram: {done}")
        self.call("answerCallbackQuery", callback_query_id=q["id"], text=done[:190])
        old = q["message"].get("text", "")
        self.call("editMessageText", chat_id=chat, message_id=q["message"]["message_id"], text=f"{old}\n\n{done}")


def start_bot(sim, log=print) -> AnalystBot | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    allowed = {int(c) for c in os.environ.get("TELEGRAM_ALLOWED_CHATS", "").split(",") if c.strip()}
    bot = AnalystBot(sim, token, allowed or None, os.environ.get("SHEKARA_PUBLIC_URL"), log)
    bot.start()
    return bot
