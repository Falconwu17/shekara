"""Комбинированный балл ШЕКАРЫ: среднее балла изолированной модели банка и модели с
выпусками, по каждому (перевод, банк). Правило выбрано 10.10.2026 на дне валидации по главной
метрике (AP всех межбанковских переводов: изоляция 0,366, L3 0,375, среднее 0,399, максимум
0,389, геометрическое 0,397) до просмотра теста. Смысл: банк держит обе модели: выпуски
добавляют сигнал о контрагенте, свой полный вид на клиента не теряется.

Создаёт preds/C_<режим>_s<seed>_{val,test}_views.parquet (+ свёрнутые) и
preds/inbound_C_<режим>_s<seed>.parquet. Запуск: py -m model.combine
"""

from __future__ import annotations

import sys

import polars as pl

from model.gbt import edge_scores
from model.train_rounds import PREDS_DIR

KEY = ["row_no", "view"]


def combine(iso: pl.DataFrame, other: pl.DataFrame) -> pl.DataFrame:
    o = other.select(*KEY, pl.col("score").alias("_s"))
    return iso.join(o, on=KEY, how="inner").with_columns(((pl.col("score") + pl.col("_s")) / 2).alias("score")).drop("_s")


def main() -> int:
    made = 0
    for f in sorted(PREDS_DIR.glob("*_test_views.parquet")):
        mode, seed = f.name[: -len("_test_views.parquet")].rsplit("_s", 1)
        if mode in ("isolation", "operator", "oracle") or mode.startswith("C_"):
            continue
        for part in ("val", "test"):
            iso_f = PREDS_DIR / f"isolation_s{seed}_{part}_views.parquet"
            if not iso_f.exists():
                break
            views = combine(pl.read_parquet(iso_f), pl.read_parquet(PREDS_DIR / f"{mode}_s{seed}_{part}_views.parquet"))
            views.write_parquet(PREDS_DIR / f"C_{mode}_s{seed}_{part}_views.parquet")
            edge_scores(views).write_parquet(PREDS_DIR / f"C_{mode}_s{seed}_{part}.parquet")
        else:
            made += 1
    for f in sorted(PREDS_DIR.glob("inbound_*_s*.parquet")):
        mode, seed = f.name[len("inbound_"): -len(".parquet")].rsplit("_s", 1)
        iso_f = PREDS_DIR / f"inbound_isolation_s{seed}.parquet"
        if mode == "isolation" or mode.startswith("C_") or not iso_f.exists():
            continue
        combine(pl.read_parquet(iso_f), pl.read_parquet(f)).write_parquet(PREDS_DIR / f"inbound_C_{mode}_s{seed}.parquet")
        made += 1
    print(f"Комбинированных наборов баллов: {made}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
