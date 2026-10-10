"""Маленький синтетический AMLworld в формате HI-Small для тестов конвейера данных.

10 полных дней + 2 дня редкого хвоста, где почти всё — отмывание; цепочки
src → mid1 → mid2 → dst (+ mid1 → fan) через разные банки; одна полная дублирующаяся строка.
В файле паттернов банки записаны с лишним ведущим нулём, а суммы — без хвостовых нулей,
чтобы проверить нормализацию при сопоставлении с переводами."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

HEADER = (
    "Timestamp,From Bank,Account,To Bank,Account,Amount Received,Receiving Currency,"
    "Amount Paid,Payment Currency,Payment Format,Is Laundering"
)
BANKS = [f"{i:03d}" for i in range(1, 13)]
START = datetime(2022, 9, 1)
FULL_DAYS = 10
TAIL_DAYS = 2


@dataclass
class Synth:
    trans_path: Path
    patterns_path: Path
    transit: dict[int, set[tuple[str, str]]] = field(default_factory=dict)  # день → {(банк, счёт)}
    chains: list[list[str]] = field(default_factory=list)  # банки каждой цепочки


def _ts(t: datetime) -> str:
    return t.strftime("%Y/%m/%d %H:%M")


def make_synth(out_dir: Path, seed: int = 0, normal_per_day: int = 300) -> Synth:
    rng = random.Random(seed)
    weights = [len(BANKS) - i for i in range(len(BANKS))]
    accounts = {b: [f"{b}0{j:04X}0" for j in range(40)] for b in BANKS}
    rows: list[list[str]] = []
    patterns: list[tuple[str, list[list[str]]]] = []
    synth = Synth(out_dir / "HI-Small_Trans.csv", out_dir / "HI-Small_Patterns.txt")

    def transfer(t, fb, fa, tb, ta, amount, fmt, label):
        return [_ts(t), fb, fa, tb, ta, amount, "US Dollar", amount, "US Dollar", fmt, label]

    for day in range(FULL_DAYS + TAIL_DAYS):
        n = normal_per_day if day < FULL_DAYS else 15
        for _ in range(n):
            fb = rng.choices(BANKS, weights)[0]
            tb = fb if rng.random() < 0.3 else rng.choices(BANKS, weights)[0]
            t = START + timedelta(days=day, minutes=rng.randrange(1440))
            rows.append(
                transfer(t, fb, rng.choice(accounts[fb]), tb, rng.choice(accounts[tb]), f"{rng.uniform(10, 5000):.2f}",
                         rng.choice(["Wire", "ACH", "Cheque", "Credit Card", "Cash"]), "0")
            )

        synth.transit[day] = set()
        for c in range(3 if day < FULL_DAYS else 5):
            src_b, m1_b, m2_b, dst_b, fan_b = rng.sample(BANKS[:6], 5)
            src, dst, fan = f"S{day:02d}{c}0", f"D{day:02d}{c}0", f"F{day:02d}{c}0"
            m1, m2 = f"M{day:02d}{c}1", f"M{day:02d}{c}2"
            t0 = START + timedelta(days=day, minutes=rng.randrange(1300))
            chain = [
                transfer(t0, src_b, src, m1_b, m1, "9000.50", "Wire", "1"),
                transfer(t0 + timedelta(minutes=7), m1_b, m1, m2_b, m2, "4500.00", "ACH", "1"),
                transfer(t0 + timedelta(minutes=9), m1_b, m1, fan_b, fan, "4400.00", "Wire", "1"),
                transfer(t0 + timedelta(minutes=30), m2_b, m2, dst_b, dst, "4300.10", "Wire", "1"),
            ]
            rows.extend(chain)
            patterns.append(("STACK" if c % 2 else "FAN-OUT:  Max 2-degree Fan-Out", chain))
            synth.transit[day] |= {(m1_b, m1), (m2_b, m2)}
            synth.chains.append([src_b, m1_b, m2_b, dst_b, fan_b])

    rows.append(list(rows[5]))  # полный дубль строки
    rows.sort(key=lambda r: r[0])

    with open(synth.trans_path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER + "\n")
        for r in rows:
            f.write(",".join(r) + "\n")

    with open(synth.patterns_path, "w", encoding="utf-8", newline="") as f:
        for kind, chain in patterns:
            f.write(f"BEGIN LAUNDERING ATTEMPT - {kind}\n")
            for r in chain:
                r = list(r)
                r[1], r[3] = "0" + r[1], "0" + r[3]
                r[5] = r[7] = repr(float(r[7]))
                f.write(",".join(r) + "\n")
            f.write(f"END LAUNDERING ATTEMPT - {kind.split(':')[0]}\n\n")
    return synth
