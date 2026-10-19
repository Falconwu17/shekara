"""Объяснение балла проверки при зачислении человеческим языком (для стенда и бота аналитика).

Вклады признаков берутся точным разложением LightGBM (pred_contrib, SHAP для деревьев): сумма вкладов
и смещения равна логиту балла. Каждый признак с большим вкладом превращается в фразу со значением
признака: «получатель выводит 92% поступлений за 10 минут». Признаки описывают состояние на прошлую
ночь (профиль счёта) и сам перевод, поэтому объяснение доступно банку в момент зачисления.
"""

from __future__ import annotations

import math

import numpy as np

from data.features import CURRENCIES, EDGE_FEATURE_COLUMNS, NODE_FEATURES, PAYMENT_FORMATS

RELEASE = ["transit", "in_max", "in_mean", "out_max", "out_mean", "fwd10", "new_cp", "interbank_out"]
RELEASE_RU = {"transit": "балл «транзитный»", "in_max": "максимальный балл входящих", "in_mean": "средний балл входящих",
              "out_max": "максимальный балл исходящих", "out_mean": "средний балл исходящих",
              "fwd10": "доля вывода за 10 мин", "new_cp": "доля новых контрагентов", "interbank_out": "доля межбанковских исходящих"}
SIDE = {"s": "отправитель", "r": "получатель"}
SIDE_GEN = {"s": "отправителя", "r": "получателя"}
TOP = 4


def node_keys(extra_dim: int) -> list[str]:
    """Ключи признаков узла в матрице проверки при зачислении (model/inbound.py)."""
    keys = list(NODE_FEATURES) + ["participant"]
    if extra_dim:
        rel = ["rel_transit"] if extra_dim == 2 else [f"rel_{h}" for h in RELEASE]
        rel += ["rel_received"]
        keys += rel + [f"nin_{k}" for k in rel] + [f"nout_{k}" for k in rel] + ["nbr_flag_max"]
    return keys + ["new_account", "participant_now"]


def inbound_keys(n_cols: int) -> list[tuple[str, str]]:
    """(сторона, ключ) для каждого столбца матрицы: сторона '' у признаков самого перевода."""
    edge = [("", c) for c in EDGE_FEATURE_COLUMNS]
    per_node = (n_cols - len(edge)) // 2
    extra_dim = per_node - len(NODE_FEATURES) - 3
    node = node_keys(extra_dim)
    assert len(node) == per_node, (len(node), per_node)
    return edge + [("s", k) for k in node] + [("r", k) for k in node]


def _n(v: float) -> int:
    return max(0, round(math.expm1(v)))


def phrase(side: str, key: str, v: float) -> str | None:
    """Фраза о признаке со значением; None: признак служебный, в объяснение не выводится."""
    who, whose = SIDE.get(side, ""), SIDE_GEN.get(side, "")
    if key in ("participant", "participant_now", "interbank", "tod_sin", "tod_cos", "log_recv"):
        return None
    if key == "log_paid":
        return f"сумма перевода {math.expm1(v):,.0f}".replace(",", " ")
    if key == "fx":
        return "перевод с конвертацией валюты" if v > 0.5 else "перевод без конвертации валюты"
    if key == "log_gap":
        return "первая операция отправителя в окне" if v < 0 else f"прошлая операция отправителя {math.exp(v) - 1:.0f} мин назад"
    if key.startswith("pf"):
        name = PAYMENT_FORMATS[int(key[2:])]
        return f"формат перевода: {name}" if v > 0.5 else f"формат не «{name}»"
    if key.startswith("cur"):
        return f"валюта: {CURRENCIES[int(key[3:])]}" if v > 0.5 else None
    simple = {
        "external": lambda: f"{who} в чужом банке" if v > 0.5 else f"{who} в своём банке",
        "log_in_deg": lambda: f"у {whose} {_n(v)} входящих переводов за окно",
        "log_out_deg": lambda: f"у {whose} {_n(v)} исходящих переводов за окно",
        "log_cp_1d": lambda: f"у {whose} {_n(v)} контрагентов за день",
        "log_cp_3d": lambda: f"у {whose} {_n(v)} контрагентов за 3 дня",
        "new_cp_in_share": lambda: f"{who}: {v:.0%} входящих от новых контрагентов",
        "fwd_10m_share": lambda: f"{who} выводит {v:.0%} поступлений за 10 минут",
        "fwd_60m_share": lambda: f"{who} выводит {v:.0%} поступлений за час",
        "log_fan_in_day": lambda: f"у {whose} {_n(v)} входящих за день",
        "log_fan_out_day": lambda: f"у {whose} {_n(v)} исходящих за день",
        "interbank_out_share": lambda: f"{who}: {v:.0%} исходящих уходит в другие банки",
        "days_seen": lambda: f"{who} активен {v:.0f} дн. из окна",
        "new_account": lambda: f"{who}: новый счёт, прошлой ночью его не было" if v > 0.5 else None,
    }
    if key in simple:
        return simple[key]()
    if key == "rel_received":
        return f"о {whose} есть выпуск его банка" if v > 0.5 else f"о {whose} выпуска нет"
    if key.startswith("rel_"):
        return f"банк {whose} сообщил: {RELEASE_RU[key[4:]]} {v:+.2f}"
    if key.startswith(("nin_", "nout_")):
        who_cp = "кто платил" if key.startswith("nin_") else "кому платил"
        feat = key.split("_", 2)[2]
        if feat == "received":
            return f"доля контрагентов {whose} ({who_cp}) с выпуском своего банка: {v:.0%}"
        return f"соседние банки о контрагентах {whose} ({who_cp}): {RELEASE_RU[feat]} в среднем {v:+.2f}"
    if key == "nbr_flag_max":
        return f"среди контрагентов {whose} есть счёт с высокой оценкой соседнего банка ({v:.2f})"
    return None


def explain_rows(contrib: np.ndarray, X: np.ndarray, keys: list[tuple[str, str]], top: int = TOP) -> list[list[dict]]:
    """Для каждой строки: до `top` фраз с наибольшим по модулю вкладом (сначала повышающие риск)."""
    out = []
    for c, x in zip(contrib[:, :-1], X):
        items = []
        for j in np.argsort(-np.abs(c)):
            if len(items) == top or abs(c[j]) < 0.05:
                break
            text = phrase(*keys[j], float(x[j]))
            if text:
                items.append({"text": text, "contrib": round(float(c[j]), 3), "key": f"{keys[j][0]}:{keys[j][1]}"})
        out.append(sorted(items, key=lambda i: -i["contrib"]))
    return out


def merge(a: list[dict], b: list[dict], top: int = TOP) -> list[dict]:
    """Объяснение сочетания двух моделей (C_*): вклады обеих моделей с весом 1/2, по ключу признака."""
    acc: dict[str, dict] = {}
    for item in a + b:
        cur = acc.setdefault(item["key"], {"text": item["text"], "key": item["key"], "contrib": 0.0})
        cur["contrib"] = round(cur["contrib"] + item["contrib"] / 2, 3)
    return sorted(acc.values(), key=lambda i: -abs(i["contrib"]))[:top]
