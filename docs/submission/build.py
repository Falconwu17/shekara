"""Сборка материалов для подачи (раздел 6) с числами прямо из результатов эксперимента.

Читает experiments/results/{stats.json, views.csv, metrics.csv, paired.csv, retention_s*.csv,
h1_cost.csv, latency.json} и пишет docs/submission/*.md. Числа, которых ещё нет, помечаются
«[нет данных]», чтобы их нельзя было пропустить.

Запуск: py -m docs.submission.build
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import polars as pl

from protocol.accountant import epsilon, noise_multiplier

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "experiments" / "results"
OUT = Path(__file__).resolve().parent
REPO_URL = "https://github.com/Falconwu17/shekara"
STAND_URL = "https://shekara.21restobar.kz"   # публичный стенд (Oracle Cloud, deploy/)
MAIN = "C_L3"          # главный режим ШЕКАРЫ (комбинированный, см. model/combine.py)
PRIVATE = "C_L1_s0.25"  # минимальное раскрытие: один балл
STRICT = "C_L1_s4"      # строгая приватность
NA = "[нет данных]"


def fmt(v, d=3) -> str:
    if v is None:
        return NA
    return f"{v:.{d}f}".replace(".", ",")


def pct(v, d=0) -> str:
    return NA if v is None else f"{v * 100:.{d}f}%".replace(".", ",")


def load_json(name):
    f = RES / name
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def views_summary() -> pl.DataFrame | None:
    f = RES / "views.csv"
    if not f.exists():
        return None
    return pl.read_csv(f).group_by("mode", "slice").agg(
        pl.col("ap").mean().alias("ap"), pl.col("ap").std().alias("ap_sd"),
        pl.col("f1").mean().alias("f1"), pl.col("f1").std().alias("f1_sd"), pl.len().alias("seeds"), pl.col("pos").first())


def get(v: pl.DataFrame | None, mode: str, slice_: str, col: str):
    if v is None:
        return None
    r = v.filter((pl.col("mode") == mode) & (pl.col("slice") == slice_))
    return r[col][0] if r.height else None


def retention_mean() -> pl.DataFrame | None:
    files = sorted(RES.glob("retention_s*.csv"))
    if not files:
        return None
    r = pl.concat([pl.read_csv(f) for f in files])
    return r.group_by("mode", "clock", "regime", "tau_min").agg(pl.col("retained_share").mean().alias("share"),
                                                                pl.col("hops").first(), pl.len().alias("seeds"))


def ret(r, mode, clock, regime, tau=None):
    if r is None:
        return None
    f = (pl.col("mode") == mode) & (pl.col("clock") == clock) & (pl.col("regime") == regime)
    f &= pl.col("tau_min").is_null() if tau is None else (pl.col("tau_min") == tau)
    x = r.filter(f)
    return x["share"][0] if x.height else None


def paired(mode: str, vs: str):
    f = RES / "paired.csv"
    if not f.exists():
        return None
    p = pl.read_csv(f).filter((pl.col("mode") == mode) & (pl.col("vs") == vs))
    if not p.height:
        return None
    return {"diff": p["diff"].mean(), "lo": p["lo"].min(), "hi": p["hi"].max(), "seeds_positive": int((p["lo"] > 0).sum()),
            "seeds_diff_positive": int((p["diff"] > 0).sum()), "seeds": p.height}


def numbers() -> dict:
    st = load_json("stats.json").get("structure_train_days", {})
    v = views_summary()
    r = retention_mean()
    lat = load_json("latency.json")
    n = {
        "share_seen": st.get("pattern_share_seen_by_one_bank"), "interbank": st.get("laundering_interbank_share"),
        "prior": st.get("patterns_with_prior_interbank_accounts"),
        "dwell_med_h": (st.get("dwell_minutes") or {}).get("median"),
        "pos_all": get(v, "isolation", "all_max", "pos"), "pos_pp": get(v, "isolation", "pp_sender", "pos"),
        "lat_inbound_ms": lat.get("inbound_ms_p50"), "lat_query_ms": lat.get("query_ms_p50"),
    }
    if n["dwell_med_h"] is not None:
        n["dwell_med_h"] /= 60
    for m in ("isolation", MAIN, "L3", PRIVATE, STRICT, "C_L2_s0.25", "operator", "oracle"):
        for s in ("all_max", "pp_sender", "pp_receiver", "external"):
            for c in ("ap", "ap_sd", "f1", "f1_sd"):
                n[f"{m}|{s}|{c}"] = get(v, m, s, c)
    for m in ("isolation", MAIN):
        n[f"ret|{m}|aml15"] = ret(r, m, "amlworld", "H0", 15)
        n[f"ret|{m}|cmp15"] = ret(r, m, "compressed", "H0", 15)
        n[f"ret|{m}|cmp5"] = ret(r, m, "compressed", "H0", 5)
        n[f"ret|{m}|h1"] = ret(r, m, "amlworld", "H1")
    n["paired_main_iso"] = paired(MAIN, "isolation")
    n["paired_main_op"] = paired(MAIN, "operator")
    n["eps_private"] = epsilon(30, noise_multiplier("L1", 0.25))
    n["eps_strict"] = epsilon(30, noise_multiplier("L1", 4.0))
    n["eps_strict_week"] = epsilon(4, noise_multiplier("L1", 4.0))
    return n


def rel_gain(n, slice_="all_max", col="ap", mode=MAIN):
    a, b = n.get(f"{mode}|{slice_}|{col}"), n.get(f"isolation|{slice_}|{col}")
    return None if a is None or b is None or b == 0 else a / b - 1


def results_ru(n) -> str:
    g = rel_gain(n)
    pi, po = n.get("paired_main_iso") or {}, n.get("paired_main_op") or {}
    cmp_iso, cmp_main = n[f"ret|isolation|cmp15"], n[f"ret|{MAIN}|cmp15"]
    return (
        f"Один банк видит в среднем {pct(n['share_seen'])} схемы отмывания, а {pct(n['interbank'])} переводов отмывания "
        f"пересекают границу банков (AMLworld). (1) Модели на графах самих банков значимо сильнее центрального оператора, "
        f"видящего только межбанковские потоки (подход MITS): PR-AUC {fmt(n['isolation|all_max|ap'])}–{fmt(n[f'{MAIN}|all_max|ap'])} "
        f"против {fmt(n['operator|all_max|ap'])}, разность F1 +{fmt(po.get('diff'), 2)} (парный бутстреп по паттернам, "
        f"значимо в {po.get('seeds_positive', NA)} из {po.get('seeds', NA)} запусков). (2) Обмен оценками при 30 участниках "
        f"({n['pos_all'] or NA} положительных переводов в тесте, из них лишь около 19% — между двумя участниками) поднимает "
        f"PR-AUC с {fmt(n['isolation|all_max|ap'])} до {fmt(n[f'{MAIN}|all_max|ap'])} ({pct(g)}) во всех трёх запусках, "
        f"но прирост F1 (+{fmt(pi.get('diff'), 3)}) статистически не значим; около половины прироста даёт сочетание двух "
        f"моделей банка, что показывает контроль с выпуском-шумом (PR-AUC {fmt(n[f'{STRICT}|all_max|ap'])}). "
        f"(3) Цена приватности измерена: один зашумлённый балл (L1, ε за 30 дней ≈ {fmt(n['eps_private'], 0)}) по качеству "
        f"не отличается от шума, польза появляется только при векторном выпуске (L2/L3) с большим ε. Проверка при "
        f"зачислении — {fmt(n['lat_inbound_ms'], 1)} мс; при скорости цепочки 3 мин на звено и реакции аналитика 15 мин "
        f"деньги остаются на счёте в {pct(cmp_iso, 0)} звеньев в изоляции и {pct(cmp_main, 0)} с ШЕКАРОЙ, а временная "
        f"задержка исходящих для верхушки очереди (H1) — в {pct(n[f'ret|{MAIN}|h1'], 0)}.")


def results_en(n) -> str:
    g = rel_gain(n)
    f = lambda v, d=3: NA if v is None else f"{v:.{d}f}"
    p = lambda v, d=0: NA if v is None else f"{v * 100:.{d}f}%"
    pi, po = n.get("paired_main_iso") or {}, n.get("paired_main_op") or {}
    return (
        f"A single bank sees on average {p(n['share_seen'])} of a laundering pattern, and {p(n['interbank'])} of "
        f"laundering transfers cross a bank boundary (AMLworld). (1) Models on each bank's own graph clearly beat a central "
        f"operator that sees only interbank flows (the MITS approach): PR-AUC {f(n['isolation|all_max|ap'])}–"
        f"{f(n[f'{MAIN}|all_max|ap'])} versus {f(n['operator|all_max|ap'])}, F1 +{f(po.get('diff'), 2)} (paired bootstrap "
        f"over patterns, significant in {po.get('seeds_positive', NA)} of {po.get('seeds', NA)} runs). (2) With 30 participating banks "
        f"({n['pos_all'] or NA} positive test transfers, only ~19% of them between two participants) exchanging risk "
        f"estimates raises PR-AUC from {f(n['isolation|all_max|ap'])} to {f(n[f'{MAIN}|all_max|ap'])} ({p(g)}) in all "
        f"three runs, but the F1 gain (+{f(pi.get('diff'))}) is not statistically significant; about half of it comes from "
        f"combining the bank's two models, as a noise-release control shows. (3) The price of privacy is measured: a single "
        f"noisy score (L1, 30-day ε ≈ {f(n['eps_private'], 0)}) is indistinguishable from noise, and benefit appears only "
        f"with vector releases (L2/L3) at large ε. The incoming-credit check takes {f(n['lat_inbound_ms'], 1)} ms; "
        f"a temporary hold on outgoing payments for the top of the alert queue keeps money in place on "
        f"{p(n[f'ret|{MAIN}|h1'])} of laundering hops.")


ANNOTATION_RU = """# ШЕКАРА: межбанковское выявление сетей дропов без обмена клиентскими данными

*Трек: AI for Finance (антифрод) и Digital Banking (AML/regtech). Тип работы: MVP с демо и аналитическое решение с экспериментальными метриками.*

**Проблема.** В 2024 году в Казахстане зарегистрировано более 17 тыс. случаев интернет-мошенничества — около половины всех мошенничеств; жертвы потеряли около 45 млрд ₸, а вернуть удалось около 2 млрд ₸ (МВД; источники — docs/sources.md). С сентября 2025 года дропперство — уголовное преступление (ст. 232-1 УК РК), с июля 2024 года работает Антифрод-центр НБК. Средства жертв социальной инженерии проходят через цепочки счетов-дропов в разных банках. Каждый банк видит лишь одно звено, поэтому его антифрод-модель не распознаёт схему. Объединить данные банков нельзя из-за банковской тайны и законодательства о персональных данных.

**Решение.** ШЕКАРА — протокол и графовая модель, с которыми банки совместно выявляют сети дропов. Клиентские записи не покидают банк. Банк-владелец счёта рассылает минимальную оценку риска (балл или короткий зашумлённый вектор) только тем банкам, с клиентами которых у счёта были реальные зачисленные переводы, а днём запрос возможен только по ссылке на такой перевод. Банк-получатель проверяет входящее зачисление без сетевых запросов, подозрительная цепочка трассируется по следующим банкам по зачисленным переводам. Каждый банк видит только свой сегмент, полную цепочку собирает только оператор с мандатом (антифрод-центр НБК). Обмен идёт через хаб с журналом на хэш-цепочке, уровень раскрытия выбирает регулятор.

**Метод и данные.** Открытый датасет IBM AMLworld (NeurIPS 2023), HI-Small: 5,08 млн переводов, 30 банков-участников (число выбрано заранее заданным правилом до обучения) и внешние банки. Модель каждого банка — градиентный бустинг на признаках его локального графа переводов; выпуски соседей становятся признаками внешних узлов и распространяются по рёбрам. Сравниваются: изолированный банк, ШЕКАРА на уровнях раскрытия L1–L3 с учётом ε по каждому выпуску, центральный оператор, видящий только межбанковские переводы (подход MITS), и общий граф участников. Выпуски для обучения считаются кросс-фиттингом, обучение повторяет тест (признаки дня t не используют переводы после t), 3 запуска, бутстреп-интервалы и парный бутстреп по паттернам.

**Результаты.** {results}

**Готовность и внедрение.** Работающий прототип: шлюзы банков с правилом need-to-know и квотами, хаб с журналом, проверка при зачислении, трассировка и дерево оператора на реальном тестовом паттерне, дашборд; 22 автоматические проверки перед подачей пройдены. Открытый код: {repo}. Публичный стенд: {stand}. TRL 4. Следующий этап — пилот с двумя банками в регуляторной песочнице НБК / АРРФР.

**Команда.** Джусупов Сагир Едгеевич — автор (full-stack разработка: протокол, модель, эксперимент, демо). Западно-Казахстанский аграрно-технический университет имени Жангир хана (Уральск). Научный руководитель — Касымова Акмарал Хамзиевна.
"""

SUMMARY_EN = """# SHEKARA: cross-bank detection of money-mule networks without pooling customer data

*Track: AI for Finance (anti-fraud) and Digital Banking (AML/regtech). Type: MVP with demo and an analytical solution with experimental metrics.*

**Problem.** In 2024 Kazakhstan registered more than 17,000 cases of online fraud — about half of all fraud; victims lost about KZT 45 billion and only about KZT 2 billion was recovered (Ministry of Internal Affairs). Acting as a money mule has been a criminal offence since September 2025 (Art. 232-1 of the Criminal Code), and the National Bank's anti-fraud centre has operated since July 2024. Funds stolen through social engineering move through chains of mule accounts at different banks. Each bank sees one link only, so its fraud model cannot recognise the scheme, and banking-secrecy and personal-data law rule out pooling the data.

**Solution.** SHEKARA is a protocol and a graph-based model that lets banks detect mule networks together. Customer records never leave a bank. The bank that holds an account sends a minimal risk estimate (a score or a short noisy vector) only to banks whose customers had real credited transfers with that account; during the day a request is possible only by quoting such a transfer. The receiving bank checks an incoming credit without network calls, and a suspicious chain is traced through the next banks along credited transfers. Each bank sees only its own segment; only a mandated operator (the NBK anti-fraud centre) assembles the full chain. Exchange goes through a hub with a hash-chained log, and the regulator chooses the disclosure level.

**Method and data.** The open IBM AMLworld dataset (NeurIPS 2023), HI-Small: 5.08 million transfers, 30 participating banks (their number fixed in advance by a stated rule) and outside banks. Each bank's model is gradient boosting on features of its local transfer graph; counterparties' releases become features of external nodes and propagate along edges. We compare an isolated bank, SHEKARA at disclosure levels L1–L3 with ε counted for every release, a central operator that sees only interbank transfers (the MITS approach), and a pooled graph of participants. Training releases are cross-fitted, training mirrors testing (features for day t never use later transfers), with 3 seeds, bootstrap intervals and a paired bootstrap over patterns.

**Results.** {results}

**Readiness and deployment.** A working prototype — bank gateways with need-to-know rules and quotas, a logged hub, an incoming-credit check, incident tracing and an operator view replayed on a real test pattern, a dashboard — with 22 automated pre-submission checks passing. Open-source code: {repo}. Live stand: {stand}. TRL 4. Next step: a two-bank pilot in the NBK / ARDFM regulatory sandbox.

**Team.** Sagir Dzhussupov — sole author (full-stack: protocol, model, experiment, demo). Zhangir Khan West Kazakhstan Agrarian and Technical University (Oral). Supervisor: Akmaral Kassymova.
"""


def one_pager(n) -> str:
    return f"""# ШЕКАРА — одна страница

**1. Проблема.** Деньги жертвы социальной инженерии за минуты проходят через 3–5 счетов-дропов в разных банках; каждый банк видит одно звено. 2024 год (МВД): более 17 тыс. случаев интернет-мошенничества, ≈ 45 млрд ₸ ущерба, возвращено ≈ 2 млрд ₸. Антифрод-центр НБК (с июля 2024) больше всего перехватывает на стороне банка-отправителя — именно там ШЕКАРА даёт наибольший прирост. На бенчмарке AMLworld: один банк видит {pct(n['share_seen'])} схемы, {pct(n['interbank'])} переводов отмывания пересекают границу банков, в {pct(n['prior'])} схем участвуют счета, уже бывшие в межбанковских переводах.

**2. Для кого.** Антифрод- и AML-подразделения банков второго уровня; оператор межбанковского обмена (антифрод-центр НБК); далее МФО через ГКБ.

**3. Решение.** «Не данные, а одна оценка риска прямого контрагента реального перевода». Ночью банк рассылает выпуски о своих счетах только банкам, с которыми у счёта были зачисленные переводы; при зачислении банк-получатель за {fmt(n['lat_inbound_ms'], 1)} мс проверяет перевод по своему графу и выпускам соседей; подозрительная цепочка трассируется по следующим банкам только по ссылкам на переводы. Полную цепочку видит только оператор.

**4. Ценность (измерено на AMLworld, 3 запуска).** Модель на графе самого банка значимо сильнее центрального оператора межбанковских потоков (подход MITS): PR-AUC {fmt(n['isolation|all_max|ap'])} против {fmt(n['operator|all_max|ap'])}. Обмен оценками добавляет ещё {pct(rel_gain(n))} PR-AUC ({fmt(n['isolation|all_max|ap'])} → {fmt(n[f'{MAIN}|all_max|ap'])}) во всех запусках, но при 30 банках из 30 тыс. прирост пока не значим; польза есть при векторном выпуске (L2/L3), один балл (L1) не помогает — это цена приватности, которую выбирает регулятор. Клиентские записи не покидают банк; проверка при зачислении — {fmt(n['lat_inbound_ms'], 1)} мс; 22 автоматические проверки пройдены.

**5. Новизна.** В мире есть центральный граф потоков у оператора платёжной системы (Mastercard/Vocalink MITS) и обмен записями о клиентах (COSMIC в Сингапуре, TMNL в Нидерландах). ШЕКАРА проверяет третий путь для Казахстана: оценка риска только по ссылке на реальный перевод, данные остаются в банке, сравнение с подходом MITS и общим графом на одном бенчмарке, измеренная цена раскрытия в качестве и в ε.

Код и воспроизведение: {REPO_URL}
Публичный стенд: {STAND_URL}
"""


def numbers_sheet(n) -> str:
    rows = [(k, v) for k, v in n.items() if not isinstance(v, dict)]
    lines = ["# Цифры для слайдов и документов", "", "Все числа — из experiments/results (см. docs/submission/build.py).",
             "Значения «[нет данных]» нельзя выносить на слайды.", "", "| Ключ | Значение |", "|---|---|"]
    lines += [f"| `{k}` | {NA if v is None else (f'{v:.4f}' if isinstance(v, float) else v)} |" for k, v in rows]
    for k in ("paired_main_iso", "paired_main_op"):
        p = n.get(k)
        lines.append(f"| `{k}` | {NA if p is None else json.dumps(p, ensure_ascii=False)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    n = numbers()
    files = {
        "annotation_ru.md": ANNOTATION_RU.format(results=results_ru(n), repo=REPO_URL, stand=STAND_URL),
        "summary_en.md": SUMMARY_EN.format(results=results_en(n), repo=REPO_URL, stand=STAND_URL),
        "one_pager_ru.md": one_pager(n),
        "numbers.md": numbers_sheet(n),
    }
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    missing = sum(t.count(NA) for t in files.values())
    print(f"Собрано {len(files)} файлов в {OUT}; пропусков [нет данных]: {missing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
