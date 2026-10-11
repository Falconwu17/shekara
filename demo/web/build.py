"""Сборка веб-демонстрации ШЕКАРЫ: один HTML-файл с данными из experiments/results.

Берёт demo_case.json (реальный тестовый паттерн, баллы, протокол), views.csv, coverage.csv,
paired.csv, retention_s*.csv, latency.json, stats.json, checks.md и вставляет их в шаблон.
Результат — demo/web/shekara.html (его же отдаёт API по адресу /).

Запуск: py -m demo.web.build
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import polars as pl

from protocol.accountant import epsilon, noise_multiplier

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "experiments" / "results"
HERE = Path(__file__).resolve().parent
OUT = HERE / "shekara.html"


def views_summary() -> dict:
    v = pl.read_csv(RES / "views.csv")
    s = v.group_by("mode", "slice").agg(pl.col("ap").mean().alias("ap"), pl.col("ap").std().alias("sd"),
                                         pl.col("f1").mean().alias("f1"), pl.col("pos").first())
    out: dict = {}
    for r in s.iter_rows(named=True):
        out.setdefault(r["mode"], {})[r["slice"]] = {"ap": r["ap"], "sd": r["sd"] or 0.0, "f1": r["f1"], "pos": r["pos"]}
    return out


def privacy_points(views: dict) -> list[dict]:
    pts = []
    for lvl, sigmas in (("L1", ("4", "2", "0.5", "0.25", "0.1")), ("L2", ("0.5", "0.25", "0.1"))):
        for s in sigmas:
            m = f"C_{lvl}_s{s}"
            if m in views:
                pts.append({"mode": m, "level": lvl, "sigma": float(s), "eps": epsilon(30, noise_multiplier(lvl, float(s))),
                            "eps_week": epsilon(4, noise_multiplier(lvl, float(s))), "ap": views[m]["all_max"]["ap"]})
    return pts


def coverage() -> list[dict]:
    f = RES / "coverage.csv"
    if not f.exists():
        return []
    c = pl.read_csv(f).filter(pl.col("slice") == "all_max").group_by("n", "mode").agg(
        pl.col("ap").mean(), pl.len().alias("seeds"))
    return c.sort("n", "mode").to_dicts()


def paired() -> dict:
    p = pl.read_csv(RES / "paired.csv")
    out = {}
    for vs in ("isolation", "operator"):
        x = p.filter((pl.col("mode") == "C_L3") & (pl.col("vs") == vs))
        out[vs] = {"diff": x["diff"].mean(), "sig": int((x["lo"] > 0).sum()), "n": x.height}
    return out


def retention() -> dict:
    files = sorted(RES.glob("retention_s*.csv"))
    r = pl.concat([pl.read_csv(f) for f in files])
    out = {}
    for m in ("isolation", "C_L3"):
        x = r.filter(pl.col("mode") == m)
        h0 = x.filter((pl.col("clock") == "compressed") & (pl.col("regime") == "H0") & (pl.col("tau_min") == 15))
        h1 = x.filter((pl.col("clock") == "amlworld") & (pl.col("regime") == "H1"))
        out[m] = {"h0_15": h0["retained_share"].mean(), "h1": h1["retained_share"].mean()}
    return out


def results() -> dict:
    """Итоги эксперимента для приложения (online/api.py, /v1/results)."""
    views = views_summary()
    stats = json.loads((RES / "stats.json").read_text(encoding="utf-8"))["structure_train_days"]
    checks = (RES / "checks.md").read_text(encoding="utf-8")
    return {"views": views, "privacy": privacy_points(views), "coverage": coverage(), "paired": paired(),
            "retention": retention(), "latency": json.loads((RES / "latency.json").read_text(encoding="utf-8")),
            "checks": {"passed": checks.count("✅"), "total": checks.count("✅") + checks.count("❌")},
            "stats": {"share_seen": stats["pattern_share_seen_by_one_bank"], "interbank": stats["laundering_interbank_share"],
                      "prior": stats["patterns_with_prior_interbank_accounts"], "dwell_h": stats["dwell_minutes"]["median"] / 60}}


def main() -> int:
    case = json.loads((RES / "demo_case.json").read_text(encoding="utf-8"))
    views = views_summary()
    stats = json.loads((RES / "stats.json").read_text(encoding="utf-8"))["structure_train_days"]
    checks = (RES / "checks.md").read_text(encoding="utf-8")
    data = {
        "case": case,
        "views": views,
        "privacy": privacy_points(views),
        "coverage": coverage(),
        "paired": paired(),
        "retention": retention(),
        "latency": json.loads((RES / "latency.json").read_text(encoding="utf-8")),
        "checks": {"passed": checks.count("✅"), "total": checks.count("✅") + checks.count("❌")},
        "stats": {"share_seen": stats["pattern_share_seen_by_one_bank"], "interbank": stats["laundering_interbank_share"],
                  "prior": stats["patterns_with_prior_interbank_accounts"],
                  "dwell_h": stats["dwell_minutes"]["median"] / 60},
    }
    payload = json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = (HERE / "template.html").read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    OUT.write_text(html, encoding="utf-8")
    print(f"{OUT} — {len(html) // 1024} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
