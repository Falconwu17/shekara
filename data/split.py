"""make data: разбиение AMLworld на базы банков-участников по split_decision.json (3.3).

В базу банка (`data/processed/banks/<банк>.duckdb`, таблица `transfers`) попадают только
строки полных дней, где банк — отправитель или получатель. Колонки tx_ref нет: банк считает
его сам (protocol/matching.py) по строкам, упорядоченным по `row_no`.
Полный граф для оракула — `data/processed/full_days.parquet`.

Запуск: py -m data.split
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from data.amlworld import load_transactions, transfer_keys
from data.stats import DECISION_FILE
from protocol.matching import TransferKey

PROCESSED_DIR = Path("data/processed")
BANKS_DIR = PROCESSED_DIR / "banks"
FULL_DAYS_FILE = PROCESSED_DIR / "full_days.parquet"


def bank_db_path(bank: str, banks_dir: Path = BANKS_DIR) -> Path:
    return banks_dir / f"{bank}.duckdb"


def bank_rows(df: pl.DataFrame, bank: str) -> pl.DataFrame:
    return df.filter((pl.col("from_bank") == bank) | (pl.col("to_bank") == bank)).sort("row_no")


def write_bank_db(rows: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with duckdb.connect(str(path)) as con:
        con.execute("CREATE TABLE transfers AS SELECT * FROM rows ORDER BY row_no")


def load_bank(bank: str, banks_dir: Path = BANKS_DIR) -> pl.DataFrame:
    with duckdb.connect(str(bank_db_path(bank, banks_dir)), read_only=True) as con:
        return con.execute("SELECT * FROM transfers ORDER BY row_no").pl()


def load_bank_keys(bank: str, banks_dir: Path = BANKS_DIR) -> list[TransferKey]:
    """Строки банка для Gateway — в порядке исходного файла."""
    return transfer_keys(load_bank(bank, banks_dir))


def run(trans_path=None, decision_path=DECISION_FILE, processed_dir: Path = PROCESSED_DIR) -> dict[str, int]:
    decision = json.loads(Path(decision_path).read_text(encoding="utf-8"))
    full = [date.fromisoformat(d) for d in decision["full_days"]]
    df = load_transactions(trans_path) if trans_path else load_transactions()
    df = df.filter(pl.col("day").is_in(full))

    processed_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(processed_dir / FULL_DAYS_FILE.name)
    counts = {}
    for bank in decision["participants"]:
        rows = bank_rows(df, bank)
        write_bank_db(rows, bank_db_path(bank, processed_dir / BANKS_DIR.name))
        counts[bank] = rows.height
    return counts


def main() -> int:
    counts = run()
    for bank, n in counts.items():
        print(f"банк {bank}: {n} строк")
    print(f"Базы банков: {BANKS_DIR}, полный граф: {FULL_DAYS_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
