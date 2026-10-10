"""Графы банков и признаки счетов/переводов (раздел 3.4).

Граф окна: переводы со дня первого дня окна по `end_day` включительно. Признаки узлов и
рёбер считаются только по переводам графа, поэтому граф для дня t не видит переводов после t.
Локальный граф банка — его строки: свои счета (все их переводы есть в базе банка) и внешние
узлы-контрагенты, о которых банк знает только по переводам со своими клиентами.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import polars as pl

PAYMENT_FORMATS = ["ACH", "Bitcoin", "Cash", "Cheque", "Credit Card", "Reinvestment", "Wire"]
CURRENCIES = [
    "Australian Dollar", "Bitcoin", "Brazil Real", "Canadian Dollar", "Euro", "Mexican Peso",
    "Ruble", "Rupee", "Saudi Riyal", "Shekel", "Swiss Franc", "UK Pound", "US Dollar", "Yen", "Yuan",
]
FORWARD_WINDOWS_MIN = (10, 60)

NODE_FEATURES = [
    "external", "log_in_deg", "log_out_deg", "log_cp_1d", "log_cp_3d", "new_cp_in_share",
    "fwd_10m_share", "fwd_60m_share", "log_fan_in_day", "log_fan_out_day", "interbank_out_share",
    "days_seen",
]


@dataclass
class Graph:
    """Граф одного окна. Узлы — (банк, счёт); `owner_bank` — банк-хозяин узла."""

    node_bank: np.ndarray
    node_account: np.ndarray
    x: np.ndarray  # [n_nodes, len(NODE_FEATURES)]
    edge_index: np.ndarray  # [2, n_edges]: отправитель → получатель
    edge_attr: np.ndarray
    edge_row_no: np.ndarray
    edge_day: np.ndarray
    edge_label: np.ndarray
    node_label: np.ndarray  # транзитный счёт за окно
    owner: str | None  # банк, чей это локальный граф (None — общий граф)

    @property
    def num_nodes(self) -> int:
        return len(self.node_bank)


def _one_hot(col: pl.Expr, values: list[str], prefix: str) -> list[pl.Expr]:
    return [(col == v).cast(pl.Float32).alias(f"{prefix}{i}") for i, v in enumerate(values)]


def edge_features(df: pl.DataFrame) -> pl.DataFrame:
    """Признаки переводов: суммы в log-шкале, формат, валюты, время суток, интервал
    с предыдущей операцией отправителя (по переводам того же графа)."""
    df = df.sort("time", "row_no").with_columns(
        (pl.col("time") - pl.col("time").shift(1).over("from_bank", "sender_account"))
        .dt.total_minutes()
        .alias("gap")
    )
    minutes = pl.col("time").dt.hour() * 60 + pl.col("time").dt.minute()
    angle = minutes.cast(pl.Float64) / 1440 * 2 * np.pi
    feats = [
        pl.col("amount_paid").cast(pl.Float64).log1p().alias("log_paid"),
        pl.col("amount_received").cast(pl.Float64).log1p().alias("log_recv"),
        (pl.col("payment_currency") != pl.col("receiving_currency")).cast(pl.Float32).alias("fx"),
        (pl.col("from_bank") != pl.col("to_bank")).cast(pl.Float32).alias("interbank"),
        angle.sin().alias("tod_sin"),
        angle.cos().alias("tod_cos"),
        pl.when(pl.col("gap").is_null()).then(-1.0).otherwise((pl.col("gap").cast(pl.Float64) + 1).log()).alias("log_gap"),
        *_one_hot(pl.col("payment_format"), PAYMENT_FORMATS, "pf"),
        *_one_hot(pl.col("payment_currency"), CURRENCIES, "cur"),
    ]
    return df.with_columns(feats)


EDGE_FEATURE_COLUMNS = (
    ["log_paid", "log_recv", "fx", "interbank", "tod_sin", "tod_cos", "log_gap"]
    + [f"pf{i}" for i in range(len(PAYMENT_FORMATS))]
    + [f"cur{i}" for i in range(len(CURRENCIES))]
)


def _incoming_outgoing(df: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    inc = df.select(
        pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account"),
        pl.col("from_bank").alias("cp_bank"), pl.col("sender_account").alias("cp"),
        "time", "day", "row_no",
    )
    out = df.select(
        pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account"),
        pl.col("to_bank").alias("cp_bank"), pl.col("receiver_account").alias("cp"),
        "time", "day", "row_no", (pl.col("from_bank") != pl.col("to_bank")).alias("interbank"),
    )
    return inc, out


def node_features(df: pl.DataFrame, end_day: date) -> pl.DataFrame:
    """Признаки счетов по переводам окна (без заглядывания за `end_day`)."""
    inc, out = _incoming_outgoing(df)
    key = ["bank", "account"]
    both = pl.concat([inc.select(*key, "cp_bank", "cp", "day", "time"), out.select(*key, "cp_bank", "cp", "day", "time")])
    d1, d3 = end_day, end_day - timedelta(days=2)

    base = both.group_by(key).agg(
        pl.col("time").min().alias("first_seen"),
        pl.struct("cp_bank", "cp").filter(pl.col("day") == d1).n_unique().alias("cp_1d"),
        pl.struct("cp_bank", "cp").filter(pl.col("day") >= d3).n_unique().alias("cp_3d"),
    )
    deg_in = inc.group_by(key).agg(pl.len().alias("in_deg"))
    deg_out = out.group_by(key).agg(pl.len().alias("out_deg"), pl.col("interbank").mean().alias("interbank_out_share"))

    first_pair = both.group_by(*key, "cp_bank", "cp").agg(pl.col("time").min().alias("pair_first"))
    new_cp = (
        inc.join(first_pair, on=[*key, "cp_bank", "cp"])
        .group_by(key)
        .agg((pl.col("time") <= pl.col("pair_first")).mean().alias("new_cp_in_share"))
    )

    out_sorted = out.select(*key, pl.col("time").alias("out_time")).sort("out_time")
    nxt = inc.select(*key, "time").sort("time").join_asof(
        out_sorted, left_on="time", right_on="out_time", by=key, strategy="forward", check_sortedness=False
    ).with_columns((pl.col("out_time") - pl.col("time")).dt.total_minutes().alias("wait"))
    fwd = nxt.group_by(key).agg(
        [(pl.col("wait") <= w).fill_null(False).mean().alias(f"fwd_{w}m_share") for w in FORWARD_WINDOWS_MIN]
    )

    fan_in = inc.group_by(*key, "day").agg(pl.struct("cp_bank", "cp").n_unique().alias("f")).group_by(key).agg(pl.col("f").max().alias("fan_in_day"))
    fan_out = out.group_by(*key, "day").agg(pl.struct("cp_bank", "cp").n_unique().alias("f")).group_by(key).agg(pl.col("f").max().alias("fan_out_day"))

    feats = base
    for t in (deg_in, deg_out, new_cp, fwd, fan_in, fan_out):
        feats = feats.join(t, on=key, how="left")
    end_ts = pl.lit(end_day).cast(pl.Datetime("us")) + pl.duration(days=1)
    return feats.with_columns(
        pl.col("in_deg").fill_null(0).log1p().alias("log_in_deg"),
        pl.col("out_deg").fill_null(0).log1p().alias("log_out_deg"),
        pl.col("cp_1d").log1p().alias("log_cp_1d"),
        pl.col("cp_3d").log1p().alias("log_cp_3d"),
        pl.col("new_cp_in_share").fill_null(0.0),
        pl.col("fwd_10m_share").fill_null(0.0),
        pl.col("fwd_60m_share").fill_null(0.0),
        pl.col("fan_in_day").fill_null(0).log1p().alias("log_fan_in_day"),
        pl.col("fan_out_day").fill_null(0).log1p().alias("log_fan_out_day"),
        pl.col("interbank_out_share").fill_null(0.0),
        ((end_ts - pl.col("first_seen")).dt.total_hours() / 24).alias("days_seen"),
    )


def build_graph(df: pl.DataFrame, end_day: date, owner: str | None = None) -> Graph:
    """Граф по переводам `df` с днями ≤ `end_day`. Для локального графа `owner` — банк:
    узлы других банков помечаются внешними."""
    df = df.filter(pl.col("day") <= end_day)
    edges = edge_features(df)
    nodes = node_features(df, end_day)
    if owner is None:
        nodes = nodes.with_columns(pl.lit(0.0).alias("external"))
    else:
        nodes = nodes.with_columns((pl.col("bank") != owner).cast(pl.Float64).alias("external"))
    nodes = nodes.sort("bank", "account").with_row_index("nid")

    transit = (
        edges.filter(pl.col("is_laundering") == 1)
        .pipe(lambda l: l.select(pl.col("from_bank").alias("bank"), pl.col("sender_account").alias("account")).unique()
              .join(l.select(pl.col("to_bank").alias("bank"), pl.col("receiver_account").alias("account")).unique(), on=["bank", "account"]))
        .with_columns(pl.lit(1).alias("transit"))
    )
    nodes = nodes.join(transit, on=["bank", "account"], how="left").with_columns(pl.col("transit").fill_null(0)).sort("nid")

    ids = nodes.select("bank", "account", "nid")
    edges = (
        edges.join(ids.rename({"bank": "from_bank", "account": "sender_account", "nid": "src"}), on=["from_bank", "sender_account"])
        .join(ids.rename({"bank": "to_bank", "account": "receiver_account", "nid": "dst"}), on=["to_bank", "receiver_account"])
        .sort("time", "row_no")
    )
    return Graph(
        node_bank=nodes["bank"].to_numpy(),
        node_account=nodes["account"].to_numpy(),
        x=nodes.select(NODE_FEATURES).to_numpy().astype(np.float32),
        edge_index=np.stack([edges["src"].to_numpy(), edges["dst"].to_numpy()]).astype(np.int64),
        edge_attr=edges.select(EDGE_FEATURE_COLUMNS).to_numpy().astype(np.float32),
        edge_row_no=edges["row_no"].to_numpy(),
        edge_day=edges["day"].to_numpy(),
        edge_label=edges["is_laundering"].to_numpy().astype(np.int64),
        node_label=nodes["transit"].to_numpy().astype(np.int64),
        owner=owner,
    )
