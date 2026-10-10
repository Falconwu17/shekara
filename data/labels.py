"""Метки счетов из Is Laundering (раздел 3.4).

«Транзитный счёт» — за выбранные дни и получил, и отправил хотя бы один перевод отмывания.
Грубая метка — касался хотя бы одного перевода отмывания. Счёт — пара (банк, номер)."""

from __future__ import annotations

from datetime import date
from typing import Iterable

import polars as pl


def _laundering(df: pl.DataFrame, days: Iterable[date] | None) -> pl.DataFrame:
    df = df.filter(pl.col("is_laundering") == 1)
    return df if days is None else df.filter(pl.col("day").is_in(list(days)))


def _senders(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account")).unique()


def _receivers(df: pl.DataFrame) -> pl.DataFrame:
    return df.select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account")).unique()


def transit_accounts(df: pl.DataFrame, days: Iterable[date] | None = None) -> pl.DataFrame:
    laund = _laundering(df, days)
    return _senders(laund).join(_receivers(laund), on=["bank", "account"], how="inner")


def touched_accounts(df: pl.DataFrame, days: Iterable[date] | None = None) -> pl.DataFrame:
    laund = _laundering(df, days)
    return pl.concat([_senders(laund), _receivers(laund)]).unique()
