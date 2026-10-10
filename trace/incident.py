"""Инциденты и трассировка цепочки (раздел 3.4, «Трассировка инцидента и кто что видит»).

Между банками уходит только `Hop`: ссылка на перевод, тип и номер шага — без суммы,
времени и балла. Получатель сам находит перевод у себя, помечает свой счёт флагом
«входящий по инциденту» и решает, пересылать ли дальше, по своему баллу.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from protocol.gateway import Gateway, Transfer

MODES = {
    "I0": frozenset(),
    "I1": frozenset({"confirmed"}),
    "I2": frozenset({"confirmed", "soft"}),
}
SOFT_QUOTA_SHARE = 0.0002  # 0,02% активных счетов в сутки
TRACE_WINDOW = timedelta(minutes=60)


@dataclass(frozen=True)
class Hop:
    incident_id: str
    origin_bank: str
    kind: str
    hop_no: int
    tx_ref_hmac: str


@dataclass
class Link:
    """Звено цепочки в своём банке: свой счёт, откуда пришло и куда ушло."""

    account: str
    hop_no: int
    anchor: datetime
    in_bank: str | None = None
    in_ref: str | None = None
    out: list[dict] = field(default_factory=list)


@dataclass
class Incident:
    incident_id: str
    origin_bank: str
    kind: str
    links: dict[str, Link] = field(default_factory=dict)


class IncidentDesk:
    def __init__(self, gateway: Gateway, mode: str, active_accounts: int):
        if mode not in MODES:
            raise ValueError(f"неизвестный режим инцидентов: {mode}")
        self.gw = gateway
        self.mode = mode
        self.soft_quota = math.ceil(SOFT_QUOTA_SHARE * active_accounts)
        self.flags: dict[str, set[str]] = defaultdict(set)
        self._soft_opened: Counter = Counter()
        self._incidents: dict[str, Incident] = {}
        self._forwarded: set[tuple[str, str]] = set()
        self._seq = 0

    @property
    def bank_id(self) -> str:
        return self.gw.bank_id

    def open(self, account: str, kind: str, now: datetime, anchor: datetime | None = None) -> str | dict:
        """Открывает инцидент по своему счёту. `anchor` — время подозрительного зачисления."""
        if kind not in MODES[self.mode]:
            return {"error": "mode_off"}
        if kind == "soft":
            if self._soft_opened[now.date()] >= self.soft_quota:
                return {"error": "quota"}
            self._soft_opened[now.date()] += 1
        self._seq += 1
        iid = f"{self.bank_id}-{self._seq}"
        inc = Incident(iid, self.bank_id, kind)
        inc.links[account] = Link(account, 0, anchor or now)
        self._incidents[iid] = inc
        return iid

    def receive(self, sender: str, hop: Hop, now: datetime) -> str | dict:
        """Входящий шаг: возвращает свой счёт, к которому ведёт ссылка, или отказ."""
        if hop.kind not in MODES[self.mode]:
            return {"error": "mode_off"}
        t = self.gw.resolve(sender, hop.tx_ref_hmac, now)
        if t is None:
            return {"error": "not_found"}
        inc = self._incidents.setdefault(
            hop.incident_id, Incident(hop.incident_id, hop.origin_bank, hop.kind)
        )
        inc.links.setdefault(
            t.own_account, Link(t.own_account, hop.hop_no, t.time, sender, hop.tx_ref_hmac)
        )
        self.flags[t.own_account].add(hop.incident_id)
        return t.own_account

    def forward(
        self,
        incident_id: str,
        account: str,
        now: datetime,
        score: Callable[[Transfer], float] | None = None,
    ) -> list[Hop]:
        """Шаги дальше по уже зачисленным исходящим счёта в окне Δt от якоря звена.
        Можно вызывать повторно по мере зачисления: каждый перевод уходит один раз."""
        inc = self._incidents[incident_id]
        link = inc.links[account]
        end = min(link.anchor + TRACE_WINDOW, now)
        transfers = [
            t
            for t in self.gw.outgoing_transfers(account, link.anchor, end)
            if (incident_id, t.tx_ref) not in self._forwarded
        ]
        if score is not None:
            transfers.sort(key=score, reverse=True)
        hops = []
        for t in transfers:
            self._forwarded.add((incident_id, t.tx_ref))
            ref = self.gw.ref_hmac(t)
            link.out.append(
                {"bank": t.counterparty_bank, "tx_ref_hmac": ref, "amount": t.amount, "time": t.time.isoformat()}
            )
            hops.append(Hop(incident_id, inc.origin_bank, inc.kind, link.hop_no + 1, ref))
        return hops

    def has(self, incident_id: str) -> bool:
        return incident_id in self._incidents

    def my_segment(self, incident_id: str) -> dict:
        """Свой сегмент: только свои счета и свои звенья."""
        inc = self._incidents[incident_id]
        return {
            "incident_id": incident_id,
            "bank": self.bank_id,
            "kind": inc.kind,
            "links": [
                {**asdict(link), "anchor": link.anchor.isoformat()} for link in inc.links.values()
            ],
        }


class Operator:
    """Оператор с мандатом (антифрод-центр): собирает дерево цепочки из сегментов банков.
    По мягкому инциденту видит только факт и число звеньев."""

    def __init__(self) -> None:
        self._segments: dict[str, dict[str, dict]] = defaultdict(dict)

    def submit(self, segment: dict) -> None:
        self._segments[segment["incident_id"]][segment["bank"]] = segment

    def tree(self, incident_id: str, role: str) -> dict:
        if role != "operator":
            raise PermissionError("дерево инцидента доступно только роли «оператор»")
        segments = self._segments.get(incident_id, {})
        links = [(bank, link) for bank, seg in segments.items() for link in seg["links"]]
        kinds = {seg["kind"] for seg in segments.values()}
        if kinds != {"confirmed"}:
            return {"incident_id": incident_id, "kind": "soft", "links": len(links)}

        incoming = {link["in_ref"]: (bank, link["account"]) for bank, link in links if link["in_ref"]}
        edges = [
            {
                "from": (bank, link["account"]),
                "to": incoming.get(out["tx_ref_hmac"], (out["bank"], None)),
                "amount": out["amount"],
                "time": out["time"],
            }
            for bank, link in links
            for out in link["out"]
        ]
        return {"incident_id": incident_id, "kind": "confirmed", "edges": edges}
