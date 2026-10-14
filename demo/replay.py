"""Проигрывание демо-паттерна (раздел 4.1, п. 5-6): реальный тестовый паттерн AMLworld,
те же модели и баллы, что в метриках, и настоящий протокол: шлюзы банков, хаб с журналом,
мягкий и подтверждённый инцидент, дерево оператора.

Результат: experiments/results/demo_case.json для дашборда (dashboard/app.py).
Запуск: py -m demo.replay [--seed 1]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from data.amlworld import align_patterns, load_transactions, parse_patterns
from data.channel_map import channel_label
from data.split import load_bank_keys
from data.stats import DECISION_FILE
from demo.pick_case import CANDIDATES_FILE
from model.train_rounds import PREDS_DIR, RESULTS_DIR
from protocol.gateway import Gateway
from protocol.hub import Hub
from protocol.tokens import new_pair_key
from trace.incident import IncidentDesk, Operator

CASE_FILE = RESULTS_DIR / "demo_case.json"
MODES = ("isolation", "L3", "C_L3")  # C_L3: главный режим ШЕКАРЫ (model/combine.py)
# Курсы только для отображения сумм в тенге (приблизительно, на 2026 год: проверить).
KZT_RATE = {"US Dollar": 480, "Euro": 520, "UK Pound": 610, "Swiss Franc": 540, "Yuan": 66, "Yen": 3.2,
            "Rupee": 5.7, "Ruble": 5.5, "Australian Dollar": 315, "Canadian Dollar": 350, "Mexican Peso": 26,
            "Brazil Real": 88, "Saudi Riyal": 128, "Shekel": 130, "Bitcoin": 30_000_000}


def to_kzt(amount: str, currency: str) -> int:
    return int(round(float(amount) * KZT_RATE.get(currency, 1)))


def chosen_pattern() -> int:
    return int(pl.read_csv(CANDIDATES_FILE).sort(["level", "n_tx"], descending=[False, True])["pattern_id"][0])


def view_scores(mode: str, seed: int) -> pl.DataFrame:
    parts = [pl.read_parquet(PREDS_DIR / f"{mode}_s{seed}_{k}_views.parquet") for k in ("val", "test")]
    return pl.concat(parts).select("row_no", "view", "score")


def thresholds(seed: int) -> dict[str, float]:
    """Порог F1 каждого режима: с валидации (как в метриках)."""
    from model.train_rounds import best_threshold

    out = {}
    for m in MODES:
        v = pl.read_parquet(PREDS_DIR / f"{m}_s{seed}_val.parquet")
        out[m] = best_threshold(v["score"].to_numpy(), v["label"].to_numpy())
    return out


def node_scores(mode: str, seed: int) -> pl.DataFrame:
    if mode.startswith("C_"):  # сочетание: среднее балла изоляции и модели с выпусками (model/combine.py)
        iso, other = node_scores("isolation", seed), node_scores(mode[2:], seed)
        key = ["bank", "account", "end_day"]
        return iso.join(other.select(*key, pl.col("score").alias("_s")), on=key).with_columns(
            ((pl.col("score") + pl.col("_s")) / 2).alias("score")).drop("_s")
    return pl.read_parquet(PREDS_DIR / f"{mode}_s{seed}_nodes.parquet")


def build_case(seed: int = 1, pattern_id: int | None = None) -> dict:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    part = set(d["participants"])
    full_days = {datetime.fromisoformat(x).date() for x in d["full_days"]}
    pattern_id = pattern_id if pattern_id is not None else chosen_pattern()
    trans = load_transactions()
    pat = (align_patterns(parse_patterns(), trans).filter(pl.col("pattern_id") == pattern_id)
           .filter(pl.col("day").is_in(list(full_days))).sort("time"))
    thr = thresholds(seed)
    views = {m: view_scores(m, seed) for m in MODES}
    explain = {}
    for m in MODES:
        f = PREDS_DIR / f"{m}_s{seed}_explain.json"
        if f.exists():
            for e in json.loads(f.read_text(encoding="utf-8")):
                explain[(m, e["row_no"], e["view"])] = e["top"]
    release_nodes = node_scores("isolation", seed)  # балл раунда 0 = выпуск L1 банка-владельца

    steps = []
    for r in pat.iter_rows(named=True):
        step = {
            "row_no": r["row_no"], "time": r["timestamp"], "from_bank": r["from_bank"], "from_account": r["sender_account"],
            "to_bank": r["to_bank"], "to_account": r["receiver_account"], "amount": r["amount_paid"],
            "currency": r["payment_currency"], "amount_kzt": to_kzt(r["amount_paid"], r["payment_currency"]),
            "channel": channel_label(r["payment_format"]),
            "from_participant": r["from_bank"] in part, "to_participant": r["to_bank"] in part,
        }
        for m in MODES:
            v = views[m].filter(pl.col("row_no") == r["row_no"])
            for side, bank in (("sender", r["from_bank"]), ("receiver", r["to_bank"])):
                s = v.filter(pl.col("view") == bank)["score"]
                step[f"{m}_{side}"] = float(s[0]) if len(s) else None
            step[f"{m}_alert_sender"] = step[f"{m}_sender"] is not None and step[f"{m}_sender"] >= thr.get(m, 1.0)
            for side, bank in (("sender", r["from_bank"]), ("receiver", r["to_bank"])):
                top = explain.get((m, r["row_no"], bank))
                if top:
                    step[f"{m}_{side}_explain"] = top
        if step["from_participant"] and step["to_participant"]:
            night = r["time"].date() - timedelta(days=1)
            rel = release_nodes.filter((pl.col("bank") == r["to_bank"]) & (pl.col("account") == r["receiver_account"])
                                       & (pl.col("end_day") == night))["score"]
            step["release_about_receiver"] = float(rel[0]) if len(rel) else None
            step["release_night"] = night.isoformat()
            # компонента выпуска L3 «максимальный балл входящих» по переводам на получателя до ночи
            inc = (views["isolation"].join(trans.select("row_no", "receiver_account", "day"), on="row_no")
                   .filter((pl.col("view") == r["to_bank"]) & (pl.col("receiver_account") == r["receiver_account"])
                           & (pl.col("day") <= night)))
            step["release_in_max"] = float(inc["score"].max()) if inc.height else None
        steps.append(step)

    return {
        "pattern_id": pattern_id,
        "pattern_type": pat["pattern_type"][0],
        "seed": seed,
        "thresholds": {m: thr.get(m) for m in MODES},
        "participants_in_pattern": sorted({b for s in steps for b in (s["from_bank"], s["to_bank"]) if b in part}),
        "steps": steps,
        "protocol": protocol_trace(steps, d["participants"]),
        "queues": queues(steps, seed),
    }


def protocol_trace(steps: list[dict], participants: list[str]) -> dict:
    """Межбанковские шаги паттерна через настоящий протокол: первый перевод между участниками,
    где ШЕКАРА подняла алерт у банка-отправителя, открывает инцидент и пересылает его дальше."""
    pp = [s for s in steps if s["from_participant"] and s["to_participant"] and s["from_bank"] != s["to_bank"]]
    if not pp:
        return {"note": "в паттерне нет переводов между участниками"}
    banks = sorted({b for s in pp for b in (s["from_bank"], s["to_bank"])})
    keys = {frozenset((a, b)): new_pair_key() for a in banks for b in banks if a < b}
    hub = Hub()
    desks = {}
    for b in banks:
        rows = load_bank_keys(b)
        gw = Gateway(b, rows, {o: keys[frozenset((b, o))] for o in banks if o != b})
        active = len({r.sender_account if r.from_bank == b else r.receiver_account for r in rows})
        desks[b] = IncidentDesk(gw, "I2", active)
        hub.register(gw, desks[b])

    trigger = next((s for s in pp if s.get("C_L3_alert_sender")), pp[0])
    t = datetime.strptime(trigger["time"], "%Y/%m/%d %H:%M")
    events = []
    out = {"trigger_row": trigger["row_no"], "trigger_alerted_by_shekara": bool(trigger.get("C_L3_alert_sender"))}
    for kind in ("soft", "confirmed"):
        src = desks[trigger["from_bank"]]
        iid = src.open(trigger["from_account"], kind, now=t, anchor=t)
        events.append({"bank": trigger["from_bank"], "event": f"открыт {kind}-инцидент", "incident": iid})
        hops = src.forward(iid, trigger["from_account"], t)
        link = next(l for l in src.my_segment(iid)["links"] if l["account"] == trigger["from_account"])
        for hop, sent in zip(hops, link["out"][-len(hops):] if hops else []):
            target = sent["bank"]
            result = hub.send_hop(trigger["from_bank"], target, hop, t)
            events.append({"bank": target, "event": f"шаг {hop.hop_no}: входящий инцидент по ссылке", "incident": iid,
                           "result": result if isinstance(result, str) else result.get("error")})
        op = Operator()
        for b in banks:
            if desks[b].has(iid):
                op.submit(desks[b].my_segment(iid))
        out[f"{kind}_tree"] = op.tree(iid, role="operator")
        out[f"{kind}_segments"] = {b: desks[b].my_segment(iid) for b in banks if desks[b].has(iid)}
    out["events"] = events
    out["audit"] = {"entries": len(hub.log.entries()), "verify": hub.log.verify(), "head": hub.log.head()[1],
                    "sample": hub.log.entries()[-3:]}
    return out


def queues(steps: list[dict], seed: int) -> dict:
    """Место счетов паттерна в очереди своего банка (ночной балл) в изоляции и в ШЕКАРЕ."""
    out = {}
    for m in MODES:
        nodes = node_scores(m, seed).with_columns(
            pl.col("score").rank("ordinal", descending=True).over("bank", "end_day").alias("rank"),
            pl.len().over("bank", "end_day").alias("accounts"),
        )
        rows = []
        for s in steps:
            for bank, acc in ((s["from_bank"], s["from_account"]), (s["to_bank"], s["to_account"])):
                r = nodes.filter((pl.col("bank") == bank) & (pl.col("account") == acc))
                for x in r.iter_rows(named=True):
                    rows.append({"bank": bank, "account": acc, "end_day": x["end_day"].isoformat(), "rank": x["rank"],
                                 "accounts": x["accounts"], "budget": math.ceil(0.001 * x["accounts"]),
                                 "score": x["score"]})
        out[m] = pl.DataFrame(rows).unique().sort("bank", "account", "end_day").to_dicts() if rows else []
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    case = build_case(args.seed)
    CASE_FILE.write_text(json.dumps(case, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    pp = [s for s in case["steps"] if s["from_participant"] and s["to_participant"]]
    print(f"Паттерн {case['pattern_id']} ({case['pattern_type']}): {len(case['steps'])} переводов, между участниками {len(pp)}")
    for s in pp:
        print(f"  {s['time']} {s['from_bank']}→{s['to_bank']} {s['amount_kzt']:,} ₸: отправитель видит "
              f"изоляция {s['isolation_sender']:.3f}, ШЕКАРА {s['C_L3_sender']:.3f}; выпуск о получателе {s.get('release_about_receiver')}")
    print(f"Журнал хаба: {case['protocol']['audit']['entries']} записей, verify={case['protocol']['audit']['verify']}")
    print(f"→ {CASE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
