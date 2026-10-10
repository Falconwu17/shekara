"""Дашборд ШЕКАРЫ (раздел 3.2, компонент 6). Офлайн: читает только готовые файлы
experiments/results/ (demo_case.json, views.csv, runs.csv, retention.csv), поэтому демо
не зависит от данных и обучения.

Запуск: .venv\\Scripts\\streamlit run dashboard/app.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import altair as alt
import polars as pl
import streamlit as st

RESULTS = Path(__file__).resolve().parents[1] / "experiments" / "results"
CASE = RESULTS / "demo_case.json"

st.set_page_config(page_title="ШЕКАРА", page_icon="🛡", layout="wide")
st.title("ШЕКАРА — межбанковский поиск сетей дропов без обмена клиентскими данными")


def money(v: int) -> str:
    return f"{v:,.0f} ₸".replace(",", " ")


def pct(v) -> str:
    return "—" if v is None else f"{v:.2f}"


@st.cache_data
def load_case() -> dict | None:
    return json.loads(CASE.read_text(encoding="utf-8")) if CASE.exists() else None


@st.cache_data
def load_csv(name: str) -> pl.DataFrame | None:
    f = RESULTS / name
    return pl.read_csv(f) if f.exists() else None


case = load_case()
tab_scene, tab_bank, tab_op, tab_exp = st.tabs(["Сцена", "Банк", "Оператор", "Эксперимент"])

# ---------------- Сцена ----------------
with tab_scene:
    if case is None:
        st.warning("Нет experiments/results/demo_case.json — запустите `py -m demo.replay`.")
    else:
        st.subheader(f"Реальный тестовый паттерн AMLworld № {case['pattern_id']} ({case['pattern_type']})")
        st.caption("Суммы пересчитаны в тенге по фиксированному курсу только для показа. Баллы — те же модели, "
                   "что в метриках. «Отправитель/получатель» — балл, который этот банк видит сам.")
        thr = case["thresholds"]
        rows = []
        for s in case["steps"]:
            rows.append({
                "время": s["time"], "откуда": s["from_bank"] + (" ●" if s["from_participant"] else ""),
                "куда": s["to_bank"] + (" ●" if s["to_participant"] else ""), "сумма": money(s["amount_kzt"]),
                "канал": s["channel"],
                "изоляция: отправитель": pct(s["isolation_sender"]), "ШЕКАРА: отправитель": pct(s["L3_sender"]),
                "изоляция: получатель": pct(s["isolation_receiver"]), "ШЕКАРА: получатель": pct(s["L3_receiver"]),
                "выпуск о получателе: транзит / макс. входящий": (
                    f"{pct(s.get('release_about_receiver'))} / {pct(s.get('release_in_max'))}"
                    if s.get("release_night") else "—"),
            })
        st.dataframe(pl.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(f"● — банк-участник ШЕКАРЫ. Порог алерта (выбран на валидации): изоляция {thr['isolation']:.2f}, "
                   f"ШЕКАРА {thr['L3']:.2f}.")
        pp = [s for s in case["steps"] if s["from_participant"] and s["to_participant"]]
        for s in pp:
            iso, she = s["isolation_sender"], s["L3_sender"]
            st.markdown(
                f"**{s['time']} · банк {s['from_bank']} → банк {s['to_bank']}, {money(s['amount_kzt'])}.** "
                f"Банк {s['from_bank']} сам видит балл исходящего перевода: изоляция **{pct(iso)}**"
                f"{' (алерт)' if s['isolation_alert_sender'] else ' (нет алерта)'}, ШЕКАРА **{pct(she)}**"
                f"{' (алерт)' if s['L3_alert_sender'] else ' (нет алерта)'}. "
                f"Ночью {s.get('release_night')} банк {s['to_bank']} разослал выпуск о получателе: балл «транзитный» "
                f"**{pct(s.get('release_about_receiver'))}**, максимальный балл его входящих **{pct(s.get('release_in_max'))}**."
            )

# ---------------- Банк ----------------
with tab_bank:
    if case is None:
        st.warning("Нет данных демо.")
    else:
        banks = case["participants_in_pattern"]
        bank = st.radio("Банк", banks, horizontal=True)
        st.caption("Банк видит только свои переводы, баллы и свой сегмент. Чужие звенья после своего соседа ему не видны.")
        mine = [s for s in case["steps"] if bank in (s["from_bank"], s["to_bank"])]
        st.markdown("**Свои переводы паттерна**")
        st.dataframe(pl.DataFrame([{
            "время": s["time"], "направление": "исходящий" if s["from_bank"] == bank else "входящий",
            "свой счёт": s["from_account"] if s["from_bank"] == bank else s["to_account"],
            "банк контрагента": s["to_bank"] if s["from_bank"] == bank else s["from_bank"],
            "сумма": money(s["amount_kzt"]),
            "балл (изоляция)": pct(s["isolation_sender"] if s["from_bank"] == bank else s["isolation_receiver"]),
            "балл (ШЕКАРА)": pct(s["L3_sender"] if s["from_bank"] == bank else s["L3_receiver"]),
        } for s in mine]), use_container_width=True, hide_index=True)
        q = case["queues"]
        st.markdown("**Место своих счетов паттерна в ночной очереди** (бюджет — 0,1% активных счетов)")
        rows = []
        for mode, label in (("isolation", "изоляция"), ("L3", "ШЕКАРА")):
            for r in q.get(mode, []):
                if r["bank"] == bank:
                    rows.append({"режим": label, "счёт": r["account"], "ночь": r["end_day"], "место": r["rank"],
                                 "счетов в банке": r["accounts"], "бюджет": r["budget"],
                                 "в очереди": "да" if r["rank"] <= r["budget"] else "нет"})
        if rows:
            st.dataframe(pl.DataFrame(rows), use_container_width=True, hide_index=True)
        segs = case["protocol"].get("soft_segments", {})
        if bank in segs:
            st.markdown("**Свой сегмент мягкого инцидента**")
            st.json(segs[bank], expanded=False)

# ---------------- Оператор ----------------
with tab_op:
    if case is None:
        st.warning("Нет данных демо.")
    else:
        proto = case["protocol"]
        st.markdown("**Мягкий инцидент:** оператор видит только факт и число звеньев.")
        st.json(proto.get("soft_tree", {}))
        st.markdown("**Подтверждённый инцидент:** дерево цепочки из сегментов банков (суммы и время — только у оператора).")
        tree = proto.get("confirmed_tree", {})
        if tree.get("edges"):
            dot = ["digraph G { rankdir=LR; node [shape=box, style=rounded];"]
            for e in tree["edges"]:
                a, b = e["from"], e["to"]
                dot.append(f'"{a[0]}\\n{a[1]}" -> "{b[0]}\\n{b[1] or "?"}" [label="{e["amount"]}\\n{e["time"][11:16]}"];')
            dot.append("}")
            st.graphviz_chart("\n".join(dot))
        st.markdown("**События протокола**")
        st.dataframe(pl.DataFrame(proto.get("events", [])), use_container_width=True, hide_index=True)
        a = proto.get("audit", {})
        st.markdown(f"**Журнал хаба:** {a.get('entries')} записей, проверка хэш-цепочки: "
                    f"{'пройдена' if a.get('verify') else 'НЕ пройдена'}; голова `{str(a.get('head'))[:16]}…`")
        st.dataframe(pl.DataFrame(a.get("sample", [])), use_container_width=True, hide_index=True)

# ---------------- Эксперимент ----------------
with tab_exp:
    views = load_csv("views.csv")
    if views is None:
        st.warning("Нет experiments/results/views.csv — запустите `py -m model.view_eval`.")
    else:
        st.subheader("Качество по режимам (среднее по seed)")
        names = {"all_max": "все межбанковские (максимум двух банков)", "pp_sender": "между участниками: решает банк-отправитель",
                 "pp_receiver": "между участниками: решает банк-получатель", "pp_max": "между участниками: максимум двух",
                 "external": "участник ↔ внешний банк"}
        slice_name = st.selectbox("Срез", list(names), format_func=names.get, index=1)
        s = (views.filter(pl.col("slice") == slice_name).group_by("mode")
             .agg(pl.col("ap").mean().alias("AP"), pl.col("ap").std().alias("AP ст.откл."),
                  pl.col("f1").mean().alias("F1"), pl.len().alias("seed")).sort("AP", descending=True))
        st.dataframe(s, use_container_width=True, hide_index=True)

        st.subheader("Раскрытие ↔ качество ↔ ε")
        from protocol.accountant import epsilon, noise_multiplier  # noqa: E402

        pts = []
        for r in s.iter_rows(named=True):
            m = r["mode"]
            if m.startswith(("L1_s", "L2_s")) and "_r2" not in m:
                lvl, sig = m.split("_s")
                eps = epsilon(30, noise_multiplier(lvl, float(sig)))
                pts.append({"режим": m, "ε (30 дней)": eps, "AP": r["AP"], "уровень": lvl})
        base = {r["mode"]: r["AP"] for r in s.iter_rows(named=True)}
        if pts:
            df = pl.DataFrame(pts).to_pandas()
            chart = alt.Chart(df).mark_line(point=True).encode(
                x=alt.X("ε (30 дней):Q", scale=alt.Scale(type="log")), y="AP:Q", color="уровень:N", tooltip=["режим", "ε (30 дней)", "AP"])
            rules = [alt.Chart(pl.DataFrame({"AP": [base[k]], "линия": [k]}).to_pandas()).mark_rule(strokeDash=[4, 4]).encode(
                y="AP:Q", color=alt.value("gray"), tooltip=["линия"]) for k in ("isolation", "L3", "oracle") if k in base]
            st.altair_chart(alt.layer(chart, *rules), use_container_width=True)
            st.caption("Пунктир: изоляция, L3 (без шума), оракул. Регулятор выбирает точку на кривой. "
                       "ε — оценка сверху (RDP, δ = 1e-5, 30 выпусков).")
        else:
            st.info("Точки L1/L2 появятся после прогона полной сетки.")

    ret = load_csv("retention.csv")
    if ret is not None:
        st.subheader("Удержание до вывода (доля звеньев отмывания, деньги остались на счёте)")
        st.dataframe(ret, use_container_width=True, hide_index=True)
