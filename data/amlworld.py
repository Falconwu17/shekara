"""Загрузка AMLworld (HI-Small): переводы и файл паттернов отмывания (раздел 3.3).

Поля читаются строками как есть: из них банки сами считают tx_ref (3.5), поэтому суммы
не парсятся в float. `row_no` — номер строки исходного файла (с нуля): по нему банки
упорядочивают свои строки перед расчётом счётчика дублей."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from protocol.matching import TransferKey

RAW_DIR = Path("data/raw")
TRANS_FILE = RAW_DIR / "HI-Small_Trans.csv"
PATTERNS_FILE = RAW_DIR / "HI-Small_Patterns.txt"
TS_FORMAT = "%Y/%m/%d %H:%M"

# В заголовке файла две колонки «Account», поэтому имена задаются здесь.
COLUMNS = [
    "timestamp",
    "from_bank",
    "sender_account",
    "to_bank",
    "receiver_account",
    "amount_received",
    "receiving_currency",
    "amount_paid",
    "payment_currency",
    "payment_format",
    "is_laundering",
]
EXPECTED_HEADER_START = "Timestamp,From Bank,Account,To Bank,Account"
KEY_COLUMNS = list(TransferKey._fields)


def _with_derived(df: pl.DataFrame) -> pl.DataFrame:
    return df.with_columns(
        pl.col("is_laundering").cast(pl.Int8),
        pl.col("timestamp").str.strptime(pl.Datetime, TS_FORMAT).alias("time"),
    ).with_columns(pl.col("time").dt.date().alias("day"))


def load_transactions(path: Path = TRANS_FILE) -> pl.DataFrame:
    with open(path, encoding="utf-8") as f:
        header = f.readline().strip()
    if not header.startswith(EXPECTED_HEADER_START):
        raise ValueError(f"неожиданный заголовок {path}: {header!r}")
    df = pl.read_csv(path, has_header=False, skip_rows=1, new_columns=COLUMNS, infer_schema=False)
    return _with_derived(df.with_row_index("row_no"))


def parse_patterns(path: Path = PATTERNS_FILE) -> pl.DataFrame:
    """Строки паттернов: pattern_id, pattern_type и те же поля, что у переводов."""
    records: list[list[str]] = []
    pattern_id, pattern_type = -1, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("BEGIN LAUNDERING ATTEMPT"):
                pattern_id += 1
                pattern_type = line.split("-", 1)[1].split(":", 1)[0].strip()
            elif line.startswith("END LAUNDERING ATTEMPT"):
                pattern_type = None
            elif pattern_type is not None:
                fields = line.split(",")
                if len(fields) != len(COLUMNS):
                    raise ValueError(f"строка паттерна не из {len(COLUMNS)} полей: {line!r}")
                records.append([str(pattern_id), pattern_type, *fields])
    schema = ["pattern_id", "pattern_type", *COLUMNS]
    df = pl.DataFrame(records, schema=schema, orient="row")
    return _with_derived(df.with_columns(pl.col("pattern_id").cast(pl.Int64)))


def _join_norm(df: pl.DataFrame) -> pl.DataFrame:
    # Номера банков в файле паттернов могут отличаться ведущими нулями, суммы — записью
    # числа: для сопоставления строк паттерна с переводами всё нормализуется.
    return df.with_columns(
        pl.col("from_bank").str.strip_chars_start("0").alias("_fb"),
        pl.col("to_bank").str.strip_chars_start("0").alias("_tb"),
        (pl.col("amount_paid").cast(pl.Float64) * 100).round().cast(pl.Int64).alias("_amt"),
    )


_JOIN_ON = ["timestamp", "_fb", "sender_account", "_tb", "receiver_account", "_amt", "payment_currency", "payment_format"]


_RAW_FROM_TRANS = ["from_bank", "to_bank", "amount_received", "amount_paid"]


def align_patterns(patterns: pl.DataFrame, trans: pl.DataFrame) -> pl.DataFrame:
    """Строки паттернов с `row_no` перевода и полями банков и сумм в записи файла
    переводов, чтобы сравнивать их с участниками и строками баз банков.
    Несопоставленные строки остаются с null в `row_no`. При дублях — первая строка."""
    right = (
        _join_norm(trans)
        .select([*_JOIN_ON, "row_no", *[pl.col(c).alias(f"{c}_t") for c in _RAW_FROM_TRANS]])
        .unique(subset=_JOIN_ON, keep="first")
    )
    joined = _join_norm(patterns).join(right, on=_JOIN_ON, how="left")
    joined = joined.with_columns([pl.coalesce(f"{c}_t", c).alias(c) for c in _RAW_FROM_TRANS])
    return joined.drop([c for c in joined.columns if c.startswith("_") or c.endswith("_t")])


def transfer_keys(df: pl.DataFrame) -> list[TransferKey]:
    return [TransferKey(*r) for r in df.select(KEY_COLUMNS).iter_rows()]
