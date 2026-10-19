"""Отравление выпусков: один банк-участник отдаёт ложные оценки. Ловит ли его репутация
(protocol/reputation.py) и сколько ложных флагов получают честные банки.

Без обучения. Данные: сохранённые выпуски изоляции (балл узла своего счёта, раунд 0, тот же
балл, что банк рассылает соседям): experiments/results/preds/isolation_s{1,2,3}_nodes.parquet,
колонки bank, account, end_day, score, label. Подтверждённый инцидент: счёт с label = 1;
по счёту один инцидент, процентиль берётся в выпуске первого дня, когда счёт стал положительным.

Шаги.
1. Для каждого запуска (seed 1-3), уровня шума выпуска (без шума: L3/исходный балл; L1 с σ 0,25
   и 0,5, та же формула, что protocol.gateway.release_l1) и атаки пересчитываем выпуск каждого
   банка за каждый день и процентиль каждого дропа в нём. Атаки:
   invert: 1 − p всем; cover: свои дропы 0; cover_half: половина своих дропов 0;
   flat: 0 всем (выпуск без содержимого); topple: 2% честных счетов 1 (утопить честных).
2. Пулы процентилей по банку объединяем по трём запускам. Поток инцидентов: каждый день у банка
   Пуассон(ставка банка на HI-Small); процентиль тянем из пула (бутстрэп). Атакующий тянет из
   пула атаки, остальные из честного. Правило применяется каждый день, горизонт DAYS дней.
3. Контроль без атакующего: доля честных банков, хоть раз получивших флаг за горизонт.
4. Грубая оценка вреда без переобучения (помечена как оценка): вклад содержимого оценок соседей
   +0,019 PR-AUC (C_L3 против C_F, cascade) делим пропорционально доле межбанковских переводов,
   где атакующий банк является второй стороной для участника (C_L3_s1_test_views).

    .venv\\Scripts\\python -m experiments.poisoning
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import polars as pl

from protocol.gateway import L1_STEP
from protocol.reputation import MIN_INCIDENTS, PEER_MARGIN, Z_ONE_SIDED, classify_arrays

RESULTS = Path(__file__).resolve().parent / "results"
PREDS = RESULTS / "preds"
OUT_CSV = RESULTS / "poisoning.csv"
OUT_MD = RESULTS / "poisoning.md"

SEEDS = (1, 2, 3)
SIGMAS = (0.0, 0.25, 0.5)
ATTACKS = ("invert", "cover", "cover_half", "flat", "topple")
DAYS = 60          # горизонт наблюдения, дней
REPS = 100         # повторов бутстрэпа на (атака, σ, атакующий)
STRONG = 0.6       # банк с информативным честным выпуском (качество не ниже)
CONTENT_GAIN = 0.019  # вклад содержимого оценок соседей, PR-AUC (README, каскад C_L3 − C_F)


def l1_noise(p: np.ndarray, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Векторная копия protocol.gateway.release_l1: клип, гауссов шум, клип, квантование."""
    if sigma == 0:
        return p
    noisy = np.clip(np.clip(p, 0, 1) + rng.normal(0, sigma, p.shape), 0, 1)
    return np.round(np.round(noisy / L1_STEP) * L1_STEP, 2)


def attack(p: np.ndarray, drop: np.ndarray, kind: str, rng: np.random.Generator) -> np.ndarray:
    p = p.copy()
    if kind == "invert":
        p = 1 - p
    elif kind == "cover":
        p[drop] = 0
    elif kind == "cover_half":
        p[drop & (rng.random(p.shape) < 0.5)] = 0
    elif kind == "flat":
        p[:] = 0
    elif kind == "topple":
        p[~drop & (rng.random(p.shape) < 0.02)] = 1
    return p


def percentiles(p: np.ndarray, group: np.ndarray) -> np.ndarray:
    """Процентиль внутри группы (банк, день): доля других счетов с баллом ниже, ничьи пополам."""
    df = pl.DataFrame({"g": group, "p": p})
    r = df.select(((pl.col("p").rank("average").over("g") - 1) / (pl.len().over("g") - 1)).fill_nan(0.5))
    return r.to_series().to_numpy()


def build_pools() -> tuple[dict, dict, list[str]]:
    """pools[(σ, атака)][банк] = массив процентилей дропов; rate[банк] = инцидентов в день."""
    pools: dict = {}
    counts: dict[str, list[int]] = {}
    for seed in SEEDS:
        d = pl.read_parquet(PREDS / f"isolation_s{seed}_nodes.parquet", columns=["bank", "account", "end_day", "score", "label"])
        n_days = d["end_day"].n_unique()
        first = (d.filter(pl.col("label") == 1).group_by("bank", "account").agg(pl.col("end_day").min().alias("first")))
        d = d.join(first, on=["bank", "account"], how="left").with_columns(
            (pl.col("end_day") == pl.col("first")).fill_null(False).alias("incident"))
        for b, c in first.group_by("bank").len().iter_rows():
            counts.setdefault(b, []).append(c / n_days)
        group = (d["bank"] + "|" + d["end_day"].cast(pl.Utf8)).to_numpy()
        p0 = d["score"].to_numpy()
        drop = (d["label"] == 1).to_numpy()
        inc = d["incident"].to_numpy()
        bank = d["bank"].to_numpy()
        for sigma in SIGMAS:
            for kind in ("honest",) + ATTACKS:
                rng = np.random.default_rng(1000 * seed + int(sigma * 100) + len(kind))
                p = l1_noise(attack(p0, drop, kind, rng), sigma, rng)
                pct = percentiles(p, group)
                pool = pools.setdefault((sigma, kind), {})
                for b in np.unique(bank[inc]):
                    pool.setdefault(b, []).append(pct[inc & (bank == b)])
    pools = {k: {b: np.concatenate(v) for b, v in pool.items()} for k, pool in pools.items()}
    rate = {b: float(np.mean(v + [0] * (len(SEEDS) - len(v)))) for b, v in counts.items()}
    banks = sorted(rate)
    return pools, rate, banks


def simulate(honest: dict, attacked: np.ndarray | None, attacker: int | None, banks: list[str],
             rate: dict, rng: np.random.Generator):
    """Возвращает флаги (REPS, DAYS, B) и накопленные числа инцидентов (REPS, DAYS, B)."""
    B = len(banks)
    s = np.zeros((REPS, DAYS, B))
    n = np.zeros((REPS, DAYS, B))
    for j, b in enumerate(banks):
        k = rng.poisson(rate[b], size=(REPS, DAYS))
        pool = attacked if j == attacker else honest[b]
        tot = int(k.sum())
        draws = rng.choice(pool, size=tot, replace=True)
        # раскладываем выборку по (повтор, день) согласно k
        idx = np.repeat(np.arange(REPS * DAYS), k.ravel())
        daily = np.bincount(idx, weights=draws, minlength=REPS * DAYS).reshape(REPS, DAYS)
        s[:, :, j] = daily.cumsum(axis=1)
        n[:, :, j] = k.cumsum(axis=1)
    flag, _ = classify_arrays(s, n)
    return flag, n


def interbank_share(banks: list[str]) -> dict[str, float]:
    """Доля строк «взгляд участника на межбанковский перевод», где вторая сторона: банк b."""
    v = pl.read_parquet(PREDS / "C_L3_s1_test_views.parquet", columns=["row_no", "view"])
    two = v.join(v.rename({"view": "other"}), on="row_no").filter(pl.col("view") != pl.col("other"))
    total = v.height
    cnt = dict(two.group_by("other").len().iter_rows())
    return {b: cnt.get(b, 0) / total for b in banks}


def main() -> int:
    if "--md-only" in sys.argv:  # перерисовать отчёт по готовому CSV
        write_md(pl.read_csv(OUT_CSV, schema_overrides={"bank": pl.Utf8}))
        return 0
    rng = np.random.default_rng(20261020)
    pools, rate, banks = build_pools()
    share = interbank_share(banks)
    rows = []
    # контроль: все честные
    for sigma in SIGMAS:
        honest = pools[(sigma, "honest")]
        flag, n = simulate(honest, None, None, banks, rate, rng)
        ever = flag.any(axis=1)  # (REPS, B)
        for j, b in enumerate(banks):
            rows.append({"sigma": sigma, "attack": "none", "bank": b, "role": "честный (контроль)",
                         "pool_n": len(honest[b]), "pool_quality": float(honest[b].mean()),
                         "attacked_quality": None, "rate_per_day": rate[b],
                         "caught_share": None, "days_to_flag_median": None, "incidents_to_flag_median": None,
                         "incidents_to_flag_p90": None, "false_flag_share": float(ever[:, j].mean()),
                         "interbank_share": share[b]})
    for sigma in SIGMAS:
        honest = pools[(sigma, "honest")]
        for kind in ATTACKS:
            for a, b in enumerate(banks):
                attacked = pools[(sigma, kind)][b]
                flag, n = simulate(honest, attacked, a, banks, rate, rng)
                fa = flag[:, :, a]
                caught = fa.any(axis=1)
                day = np.where(caught, fa.argmax(axis=1), -1)
                inc = np.array([n[r, day[r], a] for r in range(REPS) if day[r] >= 0])
                others = np.delete(flag.any(axis=1), a, axis=1)
                rows.append({"sigma": sigma, "attack": kind, "bank": b, "role": "атакующий",
                             "pool_n": len(honest[b]), "pool_quality": float(honest[b].mean()),
                             "attacked_quality": float(attacked.mean()), "rate_per_day": rate[b],
                             "caught_share": float(caught.mean()),
                             "days_to_flag_median": float(np.median(day[caught] + 1)) if caught.any() else None,
                             "incidents_to_flag_median": float(np.median(inc)) if len(inc) else None,
                             "incidents_to_flag_p90": float(np.quantile(inc, 0.9)) if len(inc) else None,
                             "false_flag_share": float(others.mean()), "interbank_share": share[b]})
            print(f"σ={sigma} {kind}: готово", flush=True)
    df = pl.DataFrame(rows, infer_schema_length=None)
    df.write_csv(OUT_CSV)
    write_md(df)
    return 0


def _f(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}".replace(".", ",")


def write_md(df: pl.DataFrame) -> None:
    att = df.filter(pl.col("role") == "атакующий")
    ctl = df.filter(pl.col("role") != "атакующий")
    summ = (att.group_by("sigma", "attack").agg(
        pl.col("caught_share").mean().alias("caught"),
        pl.col("incidents_to_flag_median").median().alias("inc_med"),
        pl.col("incidents_to_flag_p90").median().alias("inc_p90"),
        pl.col("days_to_flag_median").median().alias("days_med"),
        pl.col("false_flag_share").mean().alias("ff"),
        pl.col("pool_quality").mean().alias("q_honest"),
        pl.col("attacked_quality").mean().alias("q_att"),
    ).sort("sigma", "attack"))
    ctl_s = ctl.group_by("sigma").agg(pl.col("false_flag_share").mean().alias("ff"),
                                      (pl.col("false_flag_share") > 0.05).sum().alias("banks_over5")).sort("sigma")
    worst = ctl.sort("false_flag_share", descending=True).head(5)
    share = att.filter((pl.col("sigma") == 0) & (pl.col("attack") == "invert")).select("bank", "interbank_share")
    smax = float(share["interbank_share"].max())
    smed = float(share["interbank_share"].median())
    total_rate = float(ctl.filter(pl.col("sigma") == 0)["rate_per_day"].sum())
    strong = att.filter(pl.col("pool_quality") >= STRONG).group_by("sigma", "attack").agg(
        pl.col("caught_share").mean().alias("caught_s"), pl.col("incidents_to_flag_median").median().alias("inc_s"),
        pl.len().alias("n_strong"))
    summ = summ.join(strong, on=["sigma", "attack"], how="left").sort("sigma", "attack")
    ctl_ok = ctl.filter(pl.col("pool_quality") >= 0.5).group_by("sigma").agg(pl.col("false_flag_share").mean().alias("ff_ok"))
    ctl_s = ctl_s.join(ctl_ok, on="sigma").sort("sigma")
    g = {(r["sigma"], r["attack"]): r for r in summ.iter_rows(named=True)}
    c = {r["sigma"]: r for r in ctl_s.iter_rows(named=True)}
    n_strong = g[(0.0, "invert")]["n_strong"]
    summary = [
        f"1. Без шума (режим L3, основной) банк, который покрывает своих дропов или переворачивает баллы, получает флаг «на проверку» "
        f"на {_f(g[(0.0, 'cover')]['inc_s'], 0)}-{_f(g[(0.0, 'invert')]['inc_s'], 0)}-м подтверждённом инциденте: у {n_strong} банков с информативным выпуском "
        f"(честное качество не ниже {_f(STRONG, 1)}) пойман в {_f(100 * g[(0.0, 'cover')]['caught_s'], 0)}% и {_f(100 * g[(0.0, 'invert')]['caught_s'], 0)}% повторов за {DAYS} дней; "
        f"после флага его оценки не подаются соседям.",
        f"2. Ложные флаги: за {DAYS} дней флаг получают {_f(100 * c[0.0]['ff'], 1)}% честных банков; почти все это банки, чей честный выпуск "
        f"на HI-Small и так не лучше случайного (без них {_f(100 * c[0.0]['ff_ok'], 1)}%). Флаг означает проверку регулятором, а не санкцию, "
        "и отключение такого выпуска соседям не вредит.",
        f"3. Не ловится: точечное «утопление» 2% честных клиентов и обнуление выпуска (за {DAYS} дней пойман в "
        f"{_f(100 * g[(0.0, 'topple')]['caught'], 0)}% и {_f(100 * g[(0.0, 'flat')]['caught'], 0)}% повторов). Обнуление равно неучастию, "
        "вред ограничен долей банка во вкладе содержимого (ниже); утопление клиентов: канал жалоб клиента и выборочная проверка, не этот модуль.",
        f"4. С шумом L1 (σ 0,25-0,5) честный выпуск почти неинформативен (качество {_f(g[(0.25, 'invert')]['q_honest'])}-{_f(g[(0.5, 'invert')]['q_honest'])}), "
        "поэтому отравление в L1 почти не обнаружимо, но и почти безвредно: отравлять нечего.",
        "5. Вред до поимки (оценка без переобучения, метод ниже): порядка десятитысячных PR-AUC на банк; точная величина требует переобучения с атакующим.",
    ]

    names = {"invert": "инверсия 1 − p", "cover": "покрыть своих дропов (0)", "cover_half": "покрыть половину дропов",
             "flat": "обнулить выпуск (0 всем)", "topple": "утопить 2% честных (1)"}
    lines = ["# Отравление выпусков и репутация банка", "",
             "Скрипт: `experiments/poisoning.py`, правило: `protocol/reputation.py`, построчно: `poisoning.csv`.",
             f"Правило: не меньше {MIN_INCIDENTS} подтверждённых инцидентов; флаг, если верхняя граница интервала Уилсона "
             f"(z = {_f(Z_ONE_SIDED)}) среднего процентиля дропов в выпусках ниже 0,5 или ниже медианы соседей минус {_f(PEER_MARGIN)}. "
             f"Горизонт {DAYS} дней, {REPS} повторов бутстрэпа на каждый банк-атакующий, атакующим по очереди становится каждый из {att['bank'].n_unique()} банков.",
             f"Поток инцидентов: ставка банка на HI-Small (всего {total_rate:.0f} подтверждённых счетов-дропов в день на 30 банков). "
             "Дни зависят от этой ставки и на реальных данных будут другими; число инцидентов переносится лучше.", "",
             "## Вывод для доклада", "", *summary, "",
             "## Атакующий банк (среднее по банкам-атакующим)", "",
             f"| σ выпуска | атака | качество честно | качество атаки | пойман за {DAYS} дней | пойман, банки с качеством ≥ {_f(STRONG, 1)} | инцидентов до флага (медиана / 90%) | дней до флага (медиана) | ложные флаги у честных |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in summ.iter_rows(named=True):
        lines.append(f"| {_f(r['sigma'])} | {names[r['attack']]} | {_f(r['q_honest'])} | {_f(r['q_att'])} | "
                     f"{_f(100 * r['caught'], 0)}% | {_f(100 * r['caught_s'], 0)}% | {_f(r['inc_med'], 0)} / {_f(r['inc_p90'], 0)} | {_f(r['days_med'], 0)} | {_f(100 * r['ff'], 1)}% |")
    lines += ["", "Качество: средний процентиль подтверждённых дропов в выпуске своего банка (AUC выпуска; 0,5: случайно).",
              "Ложные флаги: доля честных банков, хоть раз получивших флаг за горизонт, когда рядом есть атакующий.", "",
              "## Контроль: все банки честные", "", "| σ выпуска | доля честных банков с флагом за горизонт | то же без банков с честным качеством < 0,5 | банков с вероятностью флага > 5% |", "|---|---|---|---|"]
    for r in ctl_s.iter_rows(named=True):
        lines.append(f"| {_f(r['sigma'])} | {_f(100 * r['ff'], 1)}% | {_f(100 * r['ff_ok'], 1)}% | {r['banks_over5']} из {ctl['bank'].n_unique()} |")
    lines += ["", "Банки с наибольшей вероятностью ложного флага (контроль):", ""]
    for r in worst.iter_rows(named=True):
        lines.append(f"- {r['bank']}, σ = {_f(r['sigma'])}: {_f(100 * r['false_flag_share'], 0)}% повторов; "
                     f"пул {r['pool_n']} инцидентов, качество {_f(r['pool_quality'])}")
    lines += ["", "## Вред для соседей: грубая оценка без переобучения", "",
              f"Метод (оценка, не измерение): вклад содержимого оценок всех соседей +{_f(CONTENT_GAIN, 3)} PR-AUC (каскад C_L3 против C_F). "
              "Доля атакующего в нём принимается пропорциональной доле межбанковских переводов, где он вторая сторона для участника "
              f"(тест seed 1): медиана по банкам {_f(100 * smed, 1)}%, максимум {_f(100 * smax, 1)}%.",
              f"- Обнуление (выпуск без содержимого): потеря до {_f(CONTENT_GAIN * smax, 4)} PR-AUC для самого крупного банка, медиана {_f(CONTENT_GAIN * smed, 4)}.",
              f"- Инверсия: допущение, что перевёрнутый сигнал вредит не больше чем вдвое против потерянного: до {_f(2 * CONTENT_GAIN * smax, 4)}. "
              "Не измерено: модель соседей обучена доверять выпускам, точную величину даст только переобучение с атакующим.",
              "- После флага вес выпусков банка 0: соседи работают без его оценок (как с неучастником), вред ограничен уровнем обнуления.", "",
              "## Ограничения", "",
              "- Инцидент: счёт с меткой отмывания в окне графа (160 счетов за 4 дня выпусков на seed). Строгая метка «транзит вперёд» "
              "(model/node_eval.py) даёт лишь 36 счетов за 3 дня, на ней сверку не построить.",
              "- Пулы процентилей маленькие (3-60 инцидентов на банк за 3 seed); бутстрэп считает их истинным распределением. "
              "Качество честного банка поэтому оценено грубо, особенно у малых банков.",
              "- Атакующий один; сговор нескольких банков сдвигает медиану соседей, но не порог 0,5.",
              "- Сверке нужен выпуск банка за ночь до инцидента целиком (для процентиля): банк хранит выпуски, голова журнала сверена с хабом.", ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
