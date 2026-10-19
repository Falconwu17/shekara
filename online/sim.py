"""Стенд ШЕКАРЫ: проигрывание тестовых дней AMLworld через настоящий протокол в ускоренном времени.

Фоновый поток двигает часы симуляции и подаёт переводы банков-участников по времени:
- зачисление: балл банка-получателя (model/inbound.py, те же баллы, что в метриках; режим
  «изоляция» или «ШЕКАРА»: сочетание моделей) → очередь алертов с бюджетом 0,1% активных
  счетов; балл выше строгого порога → мягкий инцидент (квота 0,02%);
- исходящий перевод со счёта с открытым инцидентом в окне 60 мин → шаг соседу по ссылке через
  хаб; сосед помечает счёт и пересылает дальше, только если его балл перевода ≥ его порога;
- полночь: каждый банк рассылает выпуски L1 о своих счетах (балл модели счёта за прошлый день)
  банкам, с клиентами которых у счёта были переводы за 30 дней; учитывается ε.
Пороги банков подобраны на дне валидации (trace/cascade.thresholds). Всё: в журнал хаба.
"""

from __future__ import annotations

import json
import math
import threading
import time as _time
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from data.split import BANKS_DIR, FULL_DAYS_FILE, load_bank
from data.amlworld import transfer_keys
from data.stats import DECISION_FILE
from model.explain import merge
from online.alerts import AlertQueue
from trace.cascade import SOFT_SHARE, thresholds
from trace.incident import TRACE_WINDOW
from trace.replay_all import ALERT_SHARE, PREDS_DIR, active_accounts

MODES = {"isolation": "Изоляция", "C_L3": "ШЕКАРА"}
RELEASE_LEVEL, RELEASE_SIGMA = "L1", 0.25
TICK_SEC = 0.25


def load_explanations(preds_dir: Path, seed: int) -> dict[str, dict[tuple[int, str], list[dict]]]:
    """Объяснения баллов проверки при зачислении (model/explain.py): изоляция как есть, ШЕКАРА:
    сочетание изоляции и L3 с весом 1/2. Нет файлов: объяснений нет, стенд работает без них."""
    def read(mode: str) -> dict[tuple[int, str], list[dict]]:
        f = preds_dir / f"inbound_{mode}_s{seed}_explain.parquet"
        if not f.exists():
            return {}
        e = pl.read_parquet(f)
        return {(r, v): json.loads(i) for r, v, i in zip(e["row_no"].to_list(), e["view"].to_list(), e["items"].to_list())}
    iso, l3 = read("isolation"), read("L3")
    return {"isolation": iso, "C_L3": {k: merge(iso.get(k, []), l3.get(k, [])) for k in iso.keys() | l3.keys()}}


class Simulator:
    def __init__(self, net_factory, seed: int = 1, banks: list[str] | None = None,
                 decision_path: Path = DECISION_FILE, banks_dir: Path = BANKS_DIR, preds_dir: Path = PREDS_DIR,
                 full_days_file: Path = FULL_DAYS_FILE):
        d = json.loads(Path(decision_path).read_text(encoding="utf-8"))
        self.participants: list[str] = banks or d["participants"]
        self.test_days = [date.fromisoformat(x) for x in d["test_days"]]
        val_day = date.fromisoformat(d["val_days"][0])
        frames = {b: load_bank(b, banks_dir) for b in self.participants}
        self.row_pos: dict[str, dict[int, int]] = {b: {r: i for i, r in enumerate(f["row_no"].to_list())} for b, f in frames.items()}
        self.net = net_factory({b: transfer_keys(f) for b, f in frames.items()})

        part = self.participants
        trans = pl.read_parquet(full_days_file)
        self.active = active_accounts(trans.filter(pl.col("day").is_in([val_day, *self.test_days])), part)
        timeline = (trans.filter(pl.col("day").is_in(self.test_days) & (pl.col("from_bank").is_in(part) | pl.col("to_bank").is_in(part)))
                    .sort("time", "row_no"))
        self.timeline = timeline.select("row_no", "time", "from_bank", "sender_account", "to_bank", "receiver_account",
                                        "amount_paid", "payment_currency", "payment_format", "is_laundering").rows()
        self.scores: dict[str, dict[tuple[int, str], float]] = {}
        self.thr: dict[str, dict[str, tuple[float, float]]] = {}
        for m in MODES:
            p = pl.read_parquet(Path(preds_dir) / f"inbound_{m}_s{seed}.parquet").filter(pl.col("view_is_receiver"))
            p = p.filter(pl.col("view").is_in(part))
            self.scores[m] = dict(zip(zip(p["row_no"].to_list(), p["view"].to_list()), p["score"].to_list()))
            inc = p.select("row_no", "score", pl.col("view").alias("bank")).join(trans.select("row_no", "day"), on="row_no")
            self.thr[m] = thresholds(inc, self.active, val_day)
        self.why = load_explanations(Path(preds_dir), seed)
        nodes = pl.read_parquet(Path(preds_dir) / f"isolation_s{seed}_nodes.parquet").filter(pl.col("bank").is_in(part))
        self.node_p: dict[tuple[str, date], dict[str, float]] = defaultdict(dict)
        for b, acc, end, sc in nodes.select("bank", "account", "end_day", "score").iter_rows():
            self.node_p[(b, end)][acc] = sc
        self.budget = {(b, d): max(1, math.ceil(ALERT_SHARE * a)) for b, d, a in self.active.rows()}
        self.soft_quota = {(b, d): max(1, math.ceil(SOFT_SHARE * a)) for b, d, a in self.active.rows()}
        self.lock = threading.RLock()
        self.mode = "C_L3"
        self.speed = 600.0
        self.loop_forever = False  # публичный стенд: после трёх дней начинать заново
        self._thread: threading.Thread | None = None
        self.reset()

    # ---------- управление ----------
    def reset(self) -> None:
        with self.lock:
            self.net.reset()  # новый круг тестовых дней: журнал и учёт ε не копятся между кругами
            self.running = False
            self.clock = datetime.combine(self.test_days[0], datetime.min.time())
            self.end = datetime.combine(self.test_days[-1], datetime.min.time()) + timedelta(days=1)
            self.pos = 0
            self.seq = 0
            self.events: deque = deque(maxlen=4000)
            self.feeds: dict[str, deque] = {b: deque(maxlen=300) for b in self.participants}
            self.queues: dict[tuple[str, date], AlertQueue] = {}
            self.alert_info: dict[tuple[str, str, date], dict] = {}
            self.soft_used: Counter = Counter()
            self.active_inc: dict[tuple[str, str], list[tuple[str, datetime]]] = defaultdict(list)
            self.incidents: dict[str, dict] = {}
            self.counters: Counter = Counter()
            self.nights_done: set[date] = set()

    def control(self, action: str | None = None, speed: float | None = None, mode: str | None = None) -> None:
        with self.lock:
            if speed:
                self.speed = float(max(1, min(speed, 86400)))
            if mode in MODES and mode != self.mode:
                self.mode = mode
                self._event("mode", text=f"Режим банков: {MODES[mode]}")
            if action == "reset":
                self.reset()
            elif action == "play":
                self.running = True
                if not (self._thread and self._thread.is_alive()):
                    self._thread = threading.Thread(target=self._loop, daemon=True)
                    self._thread.start()
            elif action == "pause":
                self.running = False

    def _loop(self) -> None:
        while True:
            _time.sleep(TICK_SEC)
            with self.lock:
                if not self.running:
                    continue
                self.advance(self.clock + timedelta(seconds=self.speed * TICK_SEC))

    # ---------- проигрывание ----------
    def advance(self, until: datetime) -> None:
        until = min(until, self.end)
        while self.pos < len(self.timeline) and self.timeline[self.pos][1] <= until:
            r = self.timeline[self.pos]
            self._night(r[1].date())
            self.clock = r[1]
            self._transfer(r)
            self.pos += 1
        self._night(until.date())
        self.clock = until
        if self.clock >= self.end:
            if self.loop_forever:
                self.reset()
                self.running = True
                self._event("mode", text="Тестовые дни пройдены: проигрывание начато заново")
            else:
                self.running = False

    def _event(self, kind: str, **kw) -> None:
        self.seq += 1
        self.events.append({"seq": self.seq, "t": self.clock.isoformat(), "kind": kind, **kw})

    def _transfer(self, r) -> None:
        row_no, t, fb, fa, tb, ta, amount, cur, fmt, laund = r
        day = t.date()
        self.counters["transfers"] += 1
        interbank = fb != tb
        for bank, acc, side in ((fb, fa, "out"), (tb, ta, "in")):
            if bank not in self.feeds:
                continue
            score = self.scores[self.mode].get((row_no, bank)) if side == "in" else None
            self.feeds[bank].append({"row_no": row_no, "t": t.isoformat(), "side": side, "account": acc,
                                     "cp_bank": tb if side == "out" else fb, "amount": amount, "currency": cur,
                                     "format": fmt, "score": score})
        if fb in self.feeds and interbank:  # исходящий со счёта с открытым инцидентом → шаг соседу
            for iid, anchor in list(self.active_inc.get((fb, fa), [])):
                if anchor <= t <= anchor + TRACE_WINDOW:
                    self._forward(iid, fb, fa, t)
        if tb not in self.feeds:
            return
        score = self.scores[self.mode].get((row_no, tb))
        if score is None:
            return
        a_thr, s_thr = self.thr[self.mode].get(tb, (1.1, 1.1))
        if score >= a_thr:
            q = self.queues.setdefault((tb, day), AlertQueue(self.budget.get((tb, day), 1)))
            if q.offer(ta, score, day):
                self.alert_info[(tb, ta, day)] = {"row_no": row_no, "t": t.isoformat(), "from_bank": fb, "amount": amount,
                                                  "currency": cur, "score": score, "laundering": bool(laund),
                                                  "why": self.why.get(self.mode, {}).get((row_no, tb), [])}
                self.counters["alerts"] += 1
                self.counters["alerts_laundering"] += bool(laund)
                self._event("alert", bank=tb, account=ta, score=round(score, 3), from_bank=fb, laundering=bool(laund))
        if laund and interbank:
            self.counters["laundering_in"] += 1
            self.counters["laundering_alerted"] += score >= a_thr
        if score >= s_thr and self.soft_used[(tb, day)] < self.soft_quota.get((tb, day), 1):
            self.soft_used[(tb, day)] += 1
            self.open_incident(tb, ta, "soft", anchor=t)

    def open_incident(self, bank: str, account: str, kind: str, anchor: datetime | None = None) -> dict:
        desk = self.net.desks[bank]
        iid = desk.open(account, kind, now=self.clock, anchor=anchor or self.clock)
        if isinstance(iid, dict):
            return iid
        self.incidents[iid] = {"id": iid, "bank": bank, "account": account, "kind": kind, "t": self.clock.isoformat(), "hops": 0}
        self.active_inc[(bank, account)].append((iid, anchor or self.clock))
        self.counters[f"incidents_{kind}"] += 1
        self._event("incident", bank=bank, account=account, incident=iid, incident_kind=kind)
        self._forward(iid, bank, account, self.clock)
        return {"incident_id": iid}

    def _forward(self, iid: str, bank: str, account: str, now: datetime) -> None:
        desk = self.net.desks[bank]
        hops = desk.forward(iid, account, now)
        if not hops:
            return
        link = next(l for l in desk.my_segment(iid)["links"] if l["account"] == account)
        for hop, sent in zip(hops, link["out"][-len(hops):]):
            res = self.net.hub.send_hop(bank, sent["bank"], hop, now)
            ok = isinstance(res, str)
            self.counters["hops"] += 1
            self.incidents[iid]["hops"] += 1
            self._event("hop", bank=bank, to_bank=sent["bank"], incident=iid, result="ok" if ok else res["error"])
            if ok:
                tgt = sent["bank"]
                t = self.net.gateways[tgt].resolve(bank, hop.tx_ref_hmac, now)
                score = None
                if t is not None:
                    score = self._score_of(tgt, t)
                thr = self.thr[self.mode].get(tgt, (1.1, 1.1))[0]
                if t is not None and score is not None and score >= thr:  # сосед пересылает по своему баллу
                    self.active_inc[(tgt, res)].append((iid, t.time))

    def _score_of(self, bank: str, t) -> float | None:
        for r in reversed(self.feeds[bank]):
            if r["side"] == "in" and r["account"] == t.own_account and r["t"] == t.time.isoformat():
                return r["score"]
        return None

    def _night(self, day: date) -> None:
        """В полночь каждого тестового дня банки рассылают выпуски за прошлый день."""
        if day in self.nights_done or day not in self.test_days:
            return
        self.nights_done.add(day)
        prev = day - timedelta(days=1)
        stamp = datetime.combine(day, datetime.min.time())
        sent = delivered = 0
        rng = np.random.default_rng(int(stamp.timestamp()))
        for b in self.participants:
            raw = self.node_p.get((b, prev), {})
            if not raw:
                continue
            msgs = self.net.gateways[b].publish_nightly(raw, RELEASE_LEVEL, RELEASE_SIGMA, rng, stamp)
            sent += len(msgs)
            delivered += self.net.hub.broadcast(b, msgs, stamp)
        self.counters["releases_sent"] += sent
        self.counters["releases_delivered"] += delivered
        self._event("night", text=f"Ночная рассылка {day.strftime('%d.%m')}: {delivered} выпусков доставлено", sent=sent, delivered=delivered)

    # ---------- чтение ----------
    def state(self) -> dict:
        with self.lock:
            total = (self.end - datetime.combine(self.test_days[0], datetime.min.time())).total_seconds()
            done = (self.clock - datetime.combine(self.test_days[0], datetime.min.time())).total_seconds()
            return {"clock": self.clock.isoformat(), "running": self.running, "speed": self.speed, "mode": self.mode,
                    "modes": MODES, "progress": done / total, "processed": self.pos, "total": len(self.timeline),
                    "counters": dict(self.counters), "seq": self.seq, "participants": self.participants,
                    "release": {"level": RELEASE_LEVEL, "sigma": RELEASE_SIGMA}}

    def events_since(self, since: int, limit: int = 200) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["seq"] > since][-limit:]

    def bank_overview(self, bank: str) -> dict:
        with self.lock:
            day = self.clock.date()
            q = self.queues.get((bank, day))
            return {"bank": bank, "day": day.isoformat(), "budget": self.budget.get((bank, day), 1),
                    "queue": len(q.queue(day)) if q else 0, "incidents": sum(1 for i in self.incidents.values() if i["bank"] == bank),
                    "threshold": self.thr[self.mode].get(bank, (None, None))[0]}

    def bank_queue(self, bank: str) -> list[dict]:
        with self.lock:
            day = self.clock.date()
            q = self.queues.get((bank, day))
            if not q:
                return []
            return [{"account": a, "score": s, **self.alert_info.get((bank, a, day), {}),
                     "incident_flags": sorted(self.net.desks[bank].flags.get(a, []))} for a, s in q.queue(day)]

    def bank_feed(self, bank: str, limit: int = 80) -> list[dict]:
        with self.lock:
            return list(self.feeds[bank])[-limit:][::-1]

    def account_card(self, bank: str, account: str) -> dict:
        with self.lock:
            gw = self.net.gateways[bank]
            own = [t for t in gw.transfers if t.own_account == account and t.time <= self.clock]
            rows = []
            for t in own[-40:][::-1]:
                rel = gw.external_release(t.counterparty_bank, t.counterparty_account) if gw.is_partner(t.counterparty_bank) else None
                rows.append({"t": t.time.isoformat(), "side": "out" if t.outgoing else "in", "cp_bank": t.counterparty_bank,
                             "cp_account": t.counterparty_account, "amount": t.amount, "partner": gw.is_partner(t.counterparty_bank),
                             "release": None if rel is None else {"value": rel.value if not hasattr(rel.value, "tolist") else rel.value.tolist(),
                                                                   "date": rel.date.isoformat(), "level": rel.level}})
            alert = self.alert_info.get((bank, account, self.clock.date()))
            return {"bank": bank, "account": account, "transfers": rows, "total": len(own),
                    "incident_flags": sorted(self.net.desks[bank].flags.get(account, [])),
                    "alert": None if alert is None else {k: alert[k] for k in ("t", "score", "from_bank", "why")}}

    def query_counterparty(self, bank: str, row_no: int) -> dict:
        """Банк спрашивает банк-контрагента о счёте на другом конце СВОЕГО перевода."""
        with self.lock:
            pos = self.row_pos[bank].get(row_no)
            if pos is None:
                return {"error": "not_found"}
            gw = self.net.gateways[bank]
            t = gw.transfers[pos]
            if not gw.is_partner(t.counterparty_bank):
                return {"error": "not_participant", "text": f"банк {t.counterparty_bank} не участник ШЕКАРЫ"}
            resp = self.net.hub.query(bank, t.counterparty_bank, gw.ref_hmac(t), self.clock)
            self.counters["queries"] += 1
            self._event("query", bank=bank, to_bank=t.counterparty_bank, result=resp.get("error", "ok"))
            if "error" in resp:
                return resp
            sealed = len(resp.get("sealed", b""))  # столько байт шифротекста прошло через хаб
            resp = gw.open_answer(t.counterparty_bank, resp)
            rel = resp["release"]
            return {"to_bank": t.counterparty_bank, "pseudonym": resp["pseudonym"], "sealed_bytes": sealed,
                    "release": None if rel is None else {"value": rel.value if not hasattr(rel.value, "tolist") else rel.value.tolist(),
                                                         "level": rel.level, "date": rel.date.isoformat()},
                    "quota": self.net.gateways[t.counterparty_bank].quota(bank, self.clock)}

    def incident_tree(self, iid: str) -> dict:
        """Оператор: банки передают свои сегменты, оператор собирает дерево (роль проверяет API)."""
        with self.lock:
            from trace.incident import Operator

            op = Operator()
            for b, desk in self.net.desks.items():
                if desk.has(iid):
                    op.submit(desk.my_segment(iid))
            return op.tree(iid, role="operator")
