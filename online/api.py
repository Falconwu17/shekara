"""HTTP-API ШЕКАРЫ (раздел 3.5): хаб и узлы банков в одном процессе: вариант «для одного
автора» из раздела 3.2 (банки: отдельные объекты со своими базами, чужие переводы в них не
попадают). Логика: та же, что в protocol/ и trace/, без упрощений.

Время в симуляции задаёт проигрыватель: поле `now` (часы проигрывателя) определяет, что
«уже зачислено». В пилоте его заменят часы сервера.

Запуск: py -m online.api --banks 006,011,01292 [--port 8000]   (базы: data/processed/banks)
Документация эндпоинтов: http://localhost:8000/docs
"""

from __future__ import annotations

import argparse
import hmac
import os
import sys
import time as _time
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Literal, Sequence

import numpy as np
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from online.alerts import AlertQueue, alert_budget
from protocol.accountant import noise_multiplier
from protocol.gateway import CHANNEL, Gateway, Release
from protocol.hub import Hub
from protocol.matching import TransferKey
from protocol.tokens import new_pair_key
from trace.incident import IncidentDesk, Operator


class Network:
    """Банки-участники, хаб и оператор. `rows`: строки каждого банка в порядке исходного файла."""

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
        # обмен открытыми ключами при подключении: дальше ответы и выпуски шифруются адресату
        public = {b: gw.sealer.public_key for b, gw in self.gateways.items()}
        for b, gw in self.gateways.items():
            gw.sealer.add_peers({o: k for o, k in public.items() if o != b})

    def reset(self) -> None:
        """Состояние протокола с нуля: журнал, выпуски, учёт ε, инциденты, очереди."""
        self.hub.log.clear()
        self.operator = Operator()
        for b, gw in self.gateways.items():
            gw.reset_state()
            self.desks[b].reset_state()
            self.queues[b] = AlertQueue(self.queues[b].budget)

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


class ControlIn(BaseModel):
    action: Literal["play", "pause", "reset"] | None = None
    speed: float | None = None
    mode: str | None = None


class BankIncidentIn(BaseModel):
    kind: Literal["soft", "confirmed"] = "confirmed"


class SignatureIn(BaseModel):
    seq: int
    hash: str
    signature: str
    public_key: str


WEB_DIR = Path(__file__).resolve().parents[1] / "web"

# Публичный режим (сервер в интернете): если переменные заданы, управление временем требует
# X-Admin-Token, а роль «оператор»: X-Operator-Token. Локально они не заданы: всё открыто.
ADMIN_TOKEN = os.environ.get("SHEKARA_ADMIN_TOKEN", "")
OPERATOR_TOKEN = os.environ.get("SHEKARA_OPERATOR_TOKEN", "")
ACTION_LIMIT_PER_MIN = int(os.environ.get("SHEKARA_ACTION_LIMIT", "30"))
# Общий потолок запросов к /v1 с одного IP: вкладка стенда делает ~3 запроса в секунду.
REQUEST_LIMIT_PER_MIN = int(os.environ.get("SHEKARA_REQUEST_LIMIT", "600"))
MIN_TOKEN_LEN = 16


def public_mode() -> bool:
    return bool(ADMIN_TOKEN or OPERATOR_TOKEN)


def check_public_config(public: bool) -> None:
    """Сервер в интернете не стартует с пустыми или короткими ключами: иначе стенд открыт всем."""
    if not public:
        return
    for name, token in (("SHEKARA_ADMIN_TOKEN", ADMIN_TOKEN), ("SHEKARA_OPERATOR_TOKEN", OPERATOR_TOKEN)):
        if len(token) < MIN_TOKEN_LEN:
            raise SystemExit(f"{name}: нужен ключ не короче {MIN_TOKEN_LEN} символов (openssl rand -hex 16)")


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def require_admin(token: str) -> None:
    if ADMIN_TOKEN and not _same(token or "", ADMIN_TOKEN):
        raise HTTPException(403, "управление временем стенда: только с ключом администратора")


def admin_only(x_admin_token: str = Header(default="")) -> None:
    """Низкоуровневые эндпоинты протокола (произвольное `now`, выпуски, инциденты от имени любого
    банка): в публичном режиме только с ключом администратора. Сайт ими не пользуется."""
    require_admin(x_admin_token)


PROTOCOL_GUARD = [Depends(admin_only)]


def require_operator(role: str, token: str) -> None:
    if role != "operator":
        raise HTTPException(403, "доступно только роли «оператор»")
    if OPERATOR_TOKEN and not _same(token or "", OPERATOR_TOKEN):
        raise HTTPException(403, "неверный токен оператора")


class RateLimit:
    """Скользящее окно в минуту на IP: для действий банков (запрос выпуска, инцидент)."""

    def __init__(self, per_min: int):
        self.per_min, self.hits = per_min, defaultdict(deque)

    def check(self, ip: str) -> None:
        now, q = _time.monotonic(), self.hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_min:
            raise HTTPException(429, "слишком много действий подряд: подождите минуту")
        q.append(now)


def create_app(net: Network | None = None, sim=None) -> FastAPI:
    """`sim`: стенд online.sim.Simulator (его сеть и часы); без него: только протокол."""
    if sim is not None:
        net = sim.net
    public = public_mode()
    # в публичном режиме схема API и Swagger не раздаются
    app = FastAPI(title="ШЕКАРА", description="Протокол межбанковского обмена оценками риска (docs/SHEKARA.md, раздел 3.5)",
                  docs_url=None if public else "/docs", redoc_url=None if public else "/redoc",
                  openapi_url=None if public else "/openapi.json")
    requests_limit = RateLimit(REQUEST_LIMIT_PER_MIN)

    @app.middleware("http")
    async def limit_requests(request: Request, call_next):
        if request.url.path.startswith("/v1/"):
            try:
                requests_limit.check(request.client.host if request.client else "?")
            except HTTPException as e:
                return JSONResponse({"detail": e.detail}, status_code=e.status_code)
        return await call_next(request)

    def _hops(sender: str, desk: IncidentDesk, iid: str, account: str, now: datetime) -> list[dict]:
        hops = desk.forward(iid, account, now)
        link = next(l for l in desk.my_segment(iid)["links"] if l["account"] == account)
        out = []
        for hop, sent in zip(hops, link["out"][-len(hops):] if hops else []):
            res = net.hub.send_hop(sender, sent["bank"], hop, now)
            out.append({"target_bank": sent["bank"], "hop_no": hop.hop_no,
                        "result": "ok" if isinstance(res, str) else res["error"]})
        return out

    @app.post("/v1/signals/query", dependencies=PROTOCOL_GUARD)
    def signals_query(q: QueryIn):
        resp = net.hub.query(q.requester_bank, q.target_bank, q.tx_ref_hmac, q.now)
        if "error" in resp:
            return resp
        # хаб передал шифротекст; расшифровывает узел запрашивающего банка (в MVP: тот же процесс)
        sealed = len(resp.get("sealed", b""))
        answer = net.gateways[q.requester_bank].open_answer(q.target_bank, resp) if q.requester_bank in net.gateways else resp
        return {"pseudonym": answer["pseudonym"], "release": _release_json(answer["release"]), "sealed_bytes": sealed}

    @app.post("/v1/banks/{bank}/releases/nightly", dependencies=PROTOCOL_GUARD)
    def nightly(bank: str, body: NightlyIn):
        gw, _ = net.bank(bank)
        rng = np.random.default_rng(body.seed)
        msgs = gw.publish_nightly(body.scores, body.level, body.sigma, rng, body.now)
        delivered = net.hub.broadcast(bank, msgs, body.now)
        eps = None
        if body.level != "L3" and body.scores:
            acc = next(iter(body.scores))
            e = gw.ledger.epsilon(acc, body.now.date(), body.level, body.sigma)
            eps = e if np.isfinite(e) else None  # None: без шума, гарантии нет
        return {"releases": len(body.scores), "messages": len(msgs), "delivered": delivered,
                "epsilon_30d_first_account": eps, "noise_multiplier": noise_multiplier(body.level, body.sigma) if body.level != "L3" else None}

    @app.get("/v1/health")
    def health():
        return {"banks": sorted(net.gateways), "incidents_mode": next(iter(net.desks.values())).mode if net.desks else None}

    @app.post("/v1/incidents", dependencies=PROTOCOL_GUARD)
    def open_incident(body: IncidentIn):
        _, desk = net.bank(body.origin_bank)
        iid = desk.open(body.account, body.kind, body.now, body.anchor)
        if isinstance(iid, dict):
            return iid
        return {"incident_id": iid, "hops": _hops(body.origin_bank, desk, iid, body.account, body.now)}

    @app.post("/v1/incidents/{incident_id}/forward", dependencies=PROTOCOL_GUARD)
    def forward(incident_id: str, body: ForwardIn):
        _, desk = net.bank(body.bank)
        if not desk.has(incident_id):
            raise HTTPException(404, "инцидент не найден у этого банка")
        return {"hops": _hops(body.bank, desk, incident_id, body.account, body.now)}

    @app.post("/v1/incidents/{incident_id}/segment", dependencies=PROTOCOL_GUARD)
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
    def operator_tree(incident_id: str, x_role: str = Header(default=""), x_operator_token: str = Header(default="")):
        if OPERATOR_TOKEN:
            require_operator(x_role, x_operator_token)
        try:
            return net.operator.tree(incident_id, role=x_role)
        except PermissionError as e:
            raise HTTPException(403, str(e))

    @app.post("/v1/banks/{bank}/alerts", dependencies=PROTOCOL_GUARD)
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
        return net.hub.signed_head()

    @app.get("/v1/audit/verify")
    def audit_verify():
        return {"ok": net.hub.log.verify(), "entries": net.hub.log.count()}

    @app.get("/v1/audit/entries")
    def audit_entries(offset: int = 0, limit: int = 50):
        return {"total": net.hub.log.count(), "entries": net.hub.log.page(offset, min(limit, 200))}

    @app.post("/v1/audit/verify-signature")
    def audit_verify_signature(body: SignatureIn):
        from protocol.hub import verify_head

        return {"ok": verify_head(body.public_key, body.seq, body.hash, body.signature)}

    @app.get("/v1/results")
    def results():
        from online.results import results as collect

        return collect()

    limit = RateLimit(ACTION_LIMIT_PER_MIN)

    @app.get("/v1/config")
    def config():
        return {"admin_required": bool(ADMIN_TOKEN), "operator_token_required": bool(OPERATOR_TOKEN),
                "public": bool(ADMIN_TOKEN or OPERATOR_TOKEN)}

    if sim is not None:
        def need_bank(bank: str):
            if bank not in sim.feeds:
                raise HTTPException(404, f"банк {bank} не участник")

        @app.get("/v1/sim/state")
        def sim_state():
            return sim.state()

        @app.post("/v1/sim/control")
        def sim_control(body: ControlIn, x_admin_token: str = Header(default="")):
            require_admin(x_admin_token)
            sim.control(body.action, body.speed, body.mode)
            return sim.state()

        @app.get("/v1/sim/events")
        def sim_events(since: int = 0):
            return {"events": sim.events_since(since)}

        @app.get("/v1/banks")
        def banks():
            return {"banks": [sim.bank_overview(b) for b in sim.participants]}

        @app.get("/v1/banks/{bank}/feed")
        def bank_feed(bank: str):
            need_bank(bank)
            return {"bank": bank, "feed": sim.bank_feed(bank), "overview": sim.bank_overview(bank)}

        @app.get("/v1/banks/{bank}/queue")
        def bank_queue(bank: str):
            need_bank(bank)
            return {"bank": bank, "queue": sim.bank_queue(bank), "overview": sim.bank_overview(bank)}

        @app.get("/v1/banks/{bank}/accounts/{account}")
        def account(bank: str, account: str):
            need_bank(bank)
            return sim.account_card(bank, account)

        @app.post("/v1/banks/{bank}/transfers/{row_no}/query")
        def query_counterparty(bank: str, row_no: int, request: Request):
            need_bank(bank)
            limit.check(request.client.host if request.client else "?")
            return sim.query_counterparty(bank, row_no)

        @app.post("/v1/banks/{bank}/accounts/{account}/incident")
        def bank_incident(bank: str, account: str, body: BankIncidentIn, request: Request):
            need_bank(bank)
            limit.check(request.client.host if request.client else "?")
            with sim.lock:
                return sim.open_incident(bank, account, body.kind)

        @app.get("/v1/operator/incidents")
        def operator_incidents(x_role: str = Header(default=""), x_operator_token: str = Header(default="")):
            require_operator(x_role, x_operator_token)
            with sim.lock:
                return {"incidents": sorted(sim.incidents.values(), key=lambda i: i["t"], reverse=True)[:200]}

        @app.get("/v1/operator/incidents/{incident_id}/full-tree")
        def operator_full_tree(incident_id: str, x_role: str = Header(default=""), x_operator_token: str = Header(default="")):
            require_operator(x_role, x_operator_token)
            return sim.incident_tree(incident_id)

    if WEB_DIR.exists():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


def main() -> int:
    import uvicorn

    from data.split import load_bank_keys

    parser = argparse.ArgumentParser()
    parser.add_argument("--banks", default="", help="банки-участники через запятую (по умолчанию: все 30)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--protocol-only", action="store_true", help="только протокол, без стенда-симуляции")
    args = parser.parse_args()
    check_public_config(public_mode() or bool(os.environ.get("SHEKARA_PUBLIC")))
    banks =[b for b in args.banks.split(",") if b] or None
    if args.protocol_only:
        rows = {b: load_bank_keys(b) for b in banks}
        app = create_app(Network(rows))
    else:
        from online.sim import Simulator

        t0 = datetime.now()
        print("Загружаю банки, баллы и строю шлюзы...", flush=True)
        sim = Simulator(lambda rows: Network(rows), seed=args.seed, banks=banks)
        print(f"Готово за {(datetime.now() - t0).seconds} с: {len(sim.participants)} банков, "
              f"{len(sim.timeline)} переводов тестовых дней. Откройте http://{args.host}:{args.port}", flush=True)
        app = create_app(sim=sim)
        if os.environ.get("SHEKARA_AUTOPLAY"):
            sim.loop_forever = True
            sim.control("play", speed=float(os.environ.get("SHEKARA_SPEED", "600")))
        from online.telegram import load_env_file, start_bot

        load_env_file()
        start_bot(sim, log=lambda m: print(m, flush=True))
    # IP клиента из X-Forwarded-For берём только от доверенного прокси (в прод-сборке: Caddy в сети Docker),
    # иначе любой подставит себе чужой адрес и обойдёт лимиты
    trusted = os.environ.get("SHEKARA_TRUSTED_PROXIES", "127.0.0.1")
    uvicorn.run(app, host=args.host, port=args.port, proxy_headers=True, forwarded_allow_ips=trusted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
