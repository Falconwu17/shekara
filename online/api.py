"""HTTP-API ШЕКАРЫ (раздел 3.5): хаб и узлы банков в одном процессе — вариант «для одного
автора» из раздела 3.2 (банки — отдельные объекты со своими базами, чужие переводы в них не
попадают). Логика — та же, что в protocol/ и trace/, без упрощений.

Время в симуляции задаёт проигрыватель: поле `now` (часы проигрывателя) определяет, что
«уже зачислено». В пилоте его заменят часы сервера.

Запуск: py -m online.api --banks 006,011,01292 [--port 8000]   (базы — data/processed/banks)
Документация эндпоинтов: http://localhost:8000/docs
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from typing import Literal, Sequence

import numpy as np
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from online.alerts import AlertQueue, alert_budget
from protocol.accountant import noise_multiplier
from protocol.gateway import CHANNEL, Gateway, Release
from protocol.hub import Hub
from protocol.matching import TransferKey
from protocol.tokens import new_pair_key
from trace.incident import IncidentDesk, Operator


class Network:
    """Банки-участники, хаб и оператор. `rows` — строки каждого банка в порядке исходного файла."""

    def __init__(self, rows: dict[str, Sequence[TransferKey]], incidents_mode: str = "I2"):
        banks = sorted(rows)
        keys = {frozenset((a, b)): new_pair_key() for a in banks for b in banks if a < b}
        self.hub = Hub()
        self.operator = Operator()
        self.gateways: dict[str, Gateway] = {}
        self.desks: dict[str, IncidentDesk] = {}
        self.queues: dict[str, AlertQueue] = {}
        for b in banks:
            gw = Gateway(b, rows[b], {o: keys[frozenset((b, o))] for o in banks if o != b})
            active = len({r.sender_account if r.from_bank == b else r.receiver_account for r in rows[b]})
            self.gateways[b] = gw
            self.desks[b] = IncidentDesk(gw, incidents_mode, active)
            self.queues[b] = AlertQueue(alert_budget(active))
            self.hub.register(gw, self.desks[b])

    def bank(self, bank: str) -> tuple[Gateway, IncidentDesk]:
        if bank not in self.gateways:
            raise HTTPException(404, f"банк {bank} не участник")
        return self.gateways[bank], self.desks[bank]


class QueryIn(BaseModel):
    requester_bank: str
    target_bank: str
    tx_ref_hmac: str
    channel: str = CHANNEL
    now: datetime


class NightlyIn(BaseModel):
    now: datetime
    level: Literal["L1", "L2", "L3"]
    sigma: float = 0.0
    scores: dict[str, float | list[float]]  # свой счёт → p (L1) или h (L2/L3)
    seed: int = 0


class IncidentIn(BaseModel):
    origin_bank: str
    account: str
    kind: Literal["soft", "confirmed"]
    now: datetime
    anchor: datetime | None = None


class ForwardIn(BaseModel):
    bank: str
    account: str
    now: datetime


class AlertIn(BaseModel):
    account: str
    score: float
    day: date


def _release_json(r: Release | None):
    if r is None:
        return None
    v = r.value
    return {"level": r.level, "date": r.date.isoformat(), "value": v.tolist() if isinstance(v, np.ndarray) else v}


def create_app(net: Network) -> FastAPI:
    app = FastAPI(title="ШЕКАРА", description="Протокол межбанковского обмена оценками риска (docs/SHEKARA.md, раздел 3.5)")

    def _hops(sender: str, desk: IncidentDesk, iid: str, account: str, now: datetime) -> list[dict]:
        hops = desk.forward(iid, account, now)
        link = next(l for l in desk.my_segment(iid)["links"] if l["account"] == account)
        out = []
        for hop, sent in zip(hops, link["out"][-len(hops):] if hops else []):
            res = net.hub.send_hop(sender, sent["bank"], hop, now)
            out.append({"target_bank": sent["bank"], "hop_no": hop.hop_no,
                        "result": "ok" if isinstance(res, str) else res["error"]})
        return out

    @app.post("/v1/signals/query")
    def signals_query(q: QueryIn):
        resp = net.hub.query(q.requester_bank, q.target_bank, q.tx_ref_hmac, q.now)
        if "error" in resp:
            return resp
        return {"pseudonym": resp["pseudonym"], "release": _release_json(resp["release"])}

    @app.post("/v1/banks/{bank}/releases/nightly")
    def nightly(bank: str, body: NightlyIn):
        gw, _ = net.bank(bank)
        rng = np.random.default_rng(body.seed)
        msgs = gw.publish_nightly(body.scores, body.level, body.sigma, rng, body.now)
        delivered = net.hub.broadcast(bank, msgs, body.now)
        eps = None
        if body.level != "L3" and body.scores:
            acc = next(iter(body.scores))
            e = gw.ledger.epsilon(acc, body.now.date(), body.level, body.sigma)
            eps = e if np.isfinite(e) else None  # None — без шума, гарантии нет
        return {"releases": len(body.scores), "messages": len(msgs), "delivered": delivered,
                "epsilon_30d_first_account": eps, "noise_multiplier": noise_multiplier(body.level, body.sigma) if body.level != "L3" else None}

    @app.get("/v1/health")
    def health():
        return {"banks": sorted(net.gateways), "incidents_mode": next(iter(net.desks.values())).mode if net.desks else None}

    @app.post("/v1/incidents")
    def open_incident(body: IncidentIn):
        _, desk = net.bank(body.origin_bank)
        iid = desk.open(body.account, body.kind, body.now, body.anchor)
        if isinstance(iid, dict):
            return iid
        return {"incident_id": iid, "hops": _hops(body.origin_bank, desk, iid, body.account, body.now)}

    @app.post("/v1/incidents/{incident_id}/forward")
    def forward(incident_id: str, body: ForwardIn):
        _, desk = net.bank(body.bank)
        if not desk.has(incident_id):
            raise HTTPException(404, "инцидент не найден у этого банка")
        return {"hops": _hops(body.bank, desk, incident_id, body.account, body.now)}

    @app.post("/v1/incidents/{incident_id}/segment")
    def submit_segment(incident_id: str, bank: str):
        _, desk = net.bank(bank)
        if not desk.has(incident_id):
            raise HTTPException(404, "инцидент не найден у этого банка")
        net.operator.submit(desk.my_segment(incident_id))
        return {"submitted": True}

    @app.get("/v1/banks/{bank}/incidents/{incident_id}/my-segment")
    def my_segment(bank: str, incident_id: str):
        _, desk = net.bank(bank)
        if not desk.has(incident_id):
            raise HTTPException(404, "инцидент не найден у этого банка")
        return desk.my_segment(incident_id)

    @app.get("/v1/operator/incidents/{incident_id}/tree")
    def operator_tree(incident_id: str, x_role: str = Header(default="")):
        try:
            return net.operator.tree(incident_id, role=x_role)
        except PermissionError as e:
            raise HTTPException(403, str(e))

    @app.post("/v1/banks/{bank}/alerts")
    def offer_alert(bank: str, body: AlertIn):
        net.bank(bank)
        return {"queued": net.queues[bank].offer(body.account, body.score, body.day)}

    @app.get("/v1/banks/{bank}/alerts")
    def alerts(bank: str, day: date):
        net.bank(bank)
        q = net.queues[bank]
        return {"budget": q.budget, "queue": [{"account": a, "score": s} for a, s in q.queue(day)]}

    @app.get("/v1/audit/head")
    def audit_head():
        seq, h = net.hub.log.head()
        return {"seq": seq, "hash": h}

    @app.get("/v1/audit/verify")
    def audit_verify():
        return {"ok": net.hub.log.verify(), "entries": len(net.hub.log.entries())}

    return app


def main() -> int:
    import uvicorn

    from data.split import load_bank_keys

    parser = argparse.ArgumentParser()
    parser.add_argument("--banks", required=True, help="банки-участники через запятую")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    rows = {b: load_bank_keys(b) for b in args.banks.split(",")}
    uvicorn.run(create_app(Network(rows)), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
