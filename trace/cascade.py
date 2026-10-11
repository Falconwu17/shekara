"""Каскад инцидентов по всем тестовым дням (разделы 3.4, 3.6): сборка цепочки и ложные инциденты.

Событийное проигрывание в порядке времени по правилам протокола (trace/incident.py):
- алерт при зачислении: балл банка-получателя (model/inbound.py) ≥ порога бюджета 0,1% активных
  счетов (порог подобран на дне валидации, применяется в реальном времени);
- I2, мягкий инцидент: балл ≥ строгого порога (квота 0,02% активных счетов в сутки);
- I1, подтверждённый: аналитик через REVIEW_MIN после алерта подтверждает, если входящий перевод —
  отмывание (решение человека по своему счёту);
- шаг: по исходящим межбанковским переводам счёта банкам-участникам в окне 60 мин от якоря;
  банк-получатель находит перевод у себя и пересылает дальше, только если его собственный балл
  этого перевода ≥ его порога алерта;
- I0: инцидентов нет; звено известно, только если банк-получатель сам поднял на нём алерт.

Метрики: доля межбанковских звеньев отмывания между участниками, о которых оператор узнаёт
(по инциденту или по собственному алерту), задержка от зачисления звена до шага, доля мягких
инцидентов без единого перевода отмывания, входящие инциденты на банк в сутки и их доля от бюджета.

Запуск: py -m trace.cascade  → experiments/results/cascade.csv
"""

from __future__ import annotations

import heapq
import json
import math
import sys
from collections import defaultdict
from datetime import date, timedelta

import numpy as np
import polars as pl

from data.split import FULL_DAYS_FILE
from data.stats import DECISION_FILE
from trace.replay_all import ALERT_SHARE, PREDS_DIR, RESULTS_DIR, active_accounts

SOFT_SHARE = 0.0002
TRACE_MIN = 60
TRACE_SWEEP = (60, 360, 1440)  # окно Δt: 1 ч (как в протоколе), 6 ч, 24 ч — цепочки AMLworld медленные
REVIEW_MIN = 60
HOP_SEC = 2
MODES = ("isolation", "C_L3")


def thresholds(inc: pl.DataFrame, active: pl.DataFrame, val_day: date) -> dict[str, tuple[float, float]]:
    """Банк → (порог алерта, порог мягкого инцидента), подобранные по бюджету на дне валидации."""
    v = inc.filter(pl.col("day") == val_day).join(active, on=["bank", "day"])
    out = {}
    for bank, g in v.group_by("bank"):
        s = np.sort(g["score"].to_numpy())[::-1]
        a = int(g["active"][0])
        k = min(len(s), max(1, math.ceil(ALERT_SHARE * a)))
        ks = min(len(s), max(1, math.ceil(SOFT_SHARE * a)))
        out[bank[0]] = (float(s[k - 1]), float(s[ks - 1]))
    return out


def simulate(trans: pl.DataFrame, scores: pl.DataFrame, thr: dict, active: pl.DataFrame, participants: list[str],
             days: list[date], mode: str, trace_min: int = TRACE_MIN) -> dict:
    part = set(participants)
    t = trans.filter(pl.col("day").is_in(days)).sort("time", "row_no")
    sc = dict(zip(scores["row_no"].to_list(), scores["score"].to_list()))
    rows = t.select("row_no", "time", "day", "from_bank", "sender_account", "to_bank", "receiver_account",
                    "is_laundering").rows()
    out_index: dict[tuple[str, str], list] = defaultdict(list)  # исходящие межбанковские к участникам
    for r in rows:
        if r[3] != r[5] and r[5] in part:
            out_index[(r[3], r[4])].append(r)
    budget = {(b, d): max(1, math.ceil(ALERT_SHARE * a)) for b, d, a in active.rows()}
    soft_quota = {(b, d): max(1, math.ceil(SOFT_SHARE * a)) for b, d, a in active.rows()}

    alerted_links: set[int] = set()       # звенья, на которых банк-получатель сам поднял алерт
    hop_delay: dict[int, float] = {}      # звено → задержка шага от зачисления, с
    soft_used = defaultdict(int)
    incoming = defaultdict(int)           # (банк, день) → входящих инцидентов
    incidents = []                        # (id, kind, звенья с отмыванием?)
    events: list = []                     # (время, порядок, тип, данные)
    seq = 0

    def push(time, kind, data):
        nonlocal seq
        heapq.heappush(events, (time, seq, kind, data))
        seq += 1

    for r in rows:
        row_no, time, day, fb, fa, tb, ta, laund = r
        if tb in part and tb in thr:
            s = sc.get(row_no)
            if s is None:
                continue
            a_thr, s_thr = thr[tb]
            if s >= a_thr:
                alerted_links.add(row_no)
                if mode == "I1" and laund:
                    push(time + timedelta(minutes=REVIEW_MIN), "open", (tb, ta, time, "confirmed", row_no))
            if mode == "I2" and s >= s_thr and soft_used[(tb, day)] < soft_quota.get((tb, day), 1):
                soft_used[(tb, day)] += 1
                push(time, "open", (tb, ta, time, "soft", row_no))

    seen_out: set[tuple[int, int]] = set()
    while events:
        now, _, kind, data = heapq.heappop(events)
        if kind == "open":
            bank, acc, anchor, ikind, origin_row = data
            iid = len(incidents)
            incidents.append({"kind": ikind, "rows": [origin_row]})
            push(now, "trace", (iid, bank, acc, anchor))
        elif kind == "trace":
            iid, bank, acc, anchor = data
            for r in out_index.get((bank, acc), []):
                row_no, time = r[0], r[1]
                if time < anchor or time > anchor + timedelta(minutes=trace_min) or (iid, row_no) in seen_out:
                    continue
                seen_out.add((iid, row_no))
                arrive = max(time, now) + timedelta(seconds=HOP_SEC)
                incidents[iid]["rows"].append(row_no)
                incoming[(r[5], r[2])] += 1
                delay = (arrive - time).total_seconds()
                hop_delay[row_no] = min(hop_delay.get(row_no, delay), delay)
                s = sc.get(row_no)
                if s is not None and r[5] in thr and s >= thr[r[5]][0]:
                    push(arrive, "trace", (iid, r[5], r[6], time))

    laund_rows = set(t.filter(pl.col("is_laundering") == 1)["row_no"].to_list())
    pp_laund = t.filter((pl.col("is_laundering") == 1) & (pl.col("from_bank") != pl.col("to_bank"))
                        & pl.col("from_bank").is_in(participants) & pl.col("to_bank").is_in(participants))
    links = set(pp_laund["row_no"].to_list())
    traced = {r for r in links if r in hop_delay}
    known = traced | (links & alerted_links)
    soft = [i for i in incidents if i["kind"] == "soft"]
    false_soft = [i for i in soft if not (set(i["rows"]) & laund_rows)]
    inc_vals = list(incoming.values())
    shares = [n / budget.get(k, 1) for k, n in incoming.items()]
    delays = [hop_delay[r] for r in traced]
    return {
        "links": len(links),
        "links_alerted_by_receiver": len(links & alerted_links) / max(len(links), 1),
        "links_traced_by_incident": len(traced) / max(len(links), 1),
        "links_known_to_operator": len(known) / max(len(links), 1),
        "trace_delay_median_s": float(np.median(delays)) if delays else None,
        "incidents": len(incidents),
        "soft_incidents": len(soft),
        "false_soft_share": len(false_soft) / max(len(soft), 1) if soft else None,
        "incoming_per_bank_day_mean": float(np.mean(inc_vals)) if inc_vals else 0.0,
        "incoming_per_bank_day_max": int(max(inc_vals)) if inc_vals else 0,
        "incoming_budget_share_max": float(max(shares)) if shares else 0.0,
    }


def main(seeds=(1, 2, 3)) -> int:
    d = json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    part = d["participants"]
    days = [date.fromisoformat(x) for x in d["test_days"]]
    val_day = date.fromisoformat(d["val_days"][0])
    trans = pl.read_parquet(FULL_DAYS_FILE).select("row_no", "time", "day", "from_bank", "sender_account", "to_bank",
                                                   "receiver_account", "is_laundering")
    active = active_accounts(trans, part)
    rows = []
    for seed in seeds:
        for m in MODES:
            f = PREDS_DIR / f"inbound_{m}_s{seed}.parquet"
            if not f.exists():
                continue
            p = pl.read_parquet(f).filter(pl.col("view_is_receiver")).select("row_no", "score", pl.col("view").alias("bank"))
            inc = p.join(trans.select("row_no", "day"), on="row_no")
            thr = thresholds(inc, active, val_day)
            for im in ("I0", "I1", "I2"):
                for tm in ((TRACE_MIN,) if im == "I0" else TRACE_SWEEP):
                    r = simulate(trans, p, thr, active, part, days, im, tm)
                    rows.append({"model": m, "seed": seed, "incidents_mode": im, "trace_min": tm, **r})
                print(m, seed, im, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
    t = pl.DataFrame(rows)
    t.write_csv(RESULTS_DIR / "cascade.csv")
    with pl.Config(tbl_rows=40, tbl_cols=14, float_precision=3):
        print(t.group_by("model", "incidents_mode", "trace_min").agg(pl.col("links_known_to_operator", "links_traced_by_incident", "trace_delay_median_s", "soft_incidents", "false_soft_share", "incoming_per_bank_day_mean").mean()).sort("model", "incidents_mode", "trace_min"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
