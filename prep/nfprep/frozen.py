"""Forecasts written down before the data exists, committed to git, and scored when it arrives.

  python3 -m nfprep.frozen make    writes forecasts/<today>.json (never edit one afterwards)
  python3 -m nfprep.frozen score   scores every frozen forecast whose target data has appeared,
                                   after refreshing data/monthly_indicators.json and
                                   data/business_dynamics_statcan.json

Bands are the 10th-90th percentile of the same change in ordinary years (pandemic left out),
so about 8 in 10 should land inside. The US visitor band also allows a repeat of 2025's drop.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from . import outlook
from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "forecasts"


def _add_months(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:]) + k
    y += (mo - 1) // 12
    return f"{y}-{(mo - 1) % 12 + 1:02d}"


def _twelve(series: dict, end: str) -> float | None:
    ms = [_add_months(end, -k) for k in range(12)]
    return sum(series[m] for m in ms) if all(m in series for m in ms) else None


def make() -> dict:
    d = outlook.load()
    last = max(d["us_overnight"])
    fc = []
    for s, label in [("us_overnight", "US residents entering Canada overnight, Niagara ports"),
                     ("us_sameday", "US residents entering Canada same day, Niagara ports"),
                     ("canadians_back_from_us", "Canadians returning from the US, Niagara ports")]:
        lo, hi, n = outlook.band(d, s)
        if s == "us_overnight":
            lo = min(lo, outlook.year_change(d, "us_overnight", 2025))
        r = [v for _, v in outlook.changes(d, s)]
        fc.append({"id": f"{s}_12m_to_{_add_months(last, 12)}", "what": f"{label}: 12 months to {_add_months(last, 12)} against 12 months to {last}",
                   "series": s, "file": "data/monthly_indicators.json", "base_end": last, "target_end": _add_months(last, 12),
                   "base_total": _twelve(d[s], last), "expected_ratio": round(float(np.median(r)), 3), "low": round(lo, 3), "high": round(hi, 3)})
    bd = json.loads((DATA_DIR / "business_dynamics_statcan.json").read_text())["st_catharines_niagara_cma"]["Business sector industries | Active businesses"]
    ms = sorted(bd)
    ch = [bd[ms[i + 12]] / bd[ms[i]] for i in range(len(ms) - 12) if not ("2019-03" <= ms[i + 12] and ms[i] <= "2022-12")]
    last_b = ms[-1]
    fc.append({"id": f"active_businesses_{_add_months(last_b, 12)}", "what": f"Active businesses, St. Catharines-Niagara, {_add_months(last_b, 12)} against {last_b}",
               "series": "Business sector industries | Active businesses", "file": "data/business_dynamics_statcan.json",
               "base_end": last_b, "target_end": _add_months(last_b, 12), "base_total": bd[last_b],
               "expected_ratio": round(float(np.median(ch)), 4), "low": round(float(np.percentile(ch, 10)), 4), "high": round(float(np.percentile(ch, 90)), 4)})
    doc = {"_meta": {"written": dt.date.today().isoformat(), "rule": "Do not edit after writing; score with python3 -m nfprep.frozen score."},
           "forecasts": fc}
    OUT.mkdir(exist_ok=True)
    path = OUT / f"{dt.date.today().isoformat()}.json"
    if path.exists():
        raise SystemExit(f"{path} already exists; frozen forecasts are never rewritten")
    path.write_text(json.dumps(doc, indent=1))
    for f in fc:
        print(f"{f['id']}: expected {f['expected_ratio']}, range {f['low']}-{f['high']}")
    return doc


def score() -> list:
    d = outlook.load()
    bd = json.loads((DATA_DIR / "business_dynamics_statcan.json").read_text())["st_catharines_niagara_cma"]
    out = []
    for path in sorted(OUT.glob("*.json")):
        for f in json.loads(path.read_text())["forecasts"]:
            if f["file"].endswith("monthly_indicators.json"):
                got = _twelve(d.get(f["series"], {}), f["target_end"])
            else:
                got = bd.get(f["series"], {}).get(f["target_end"])
            if got is None:
                out.append({"forecast": f["id"], "status": "data not out yet"})
                continue
            ratio = got / f["base_total"]
            out.append({"forecast": f["id"], "actual_ratio": round(ratio, 4), "range": [f["low"], f["high"]],
                        "inside": f["low"] <= ratio <= f["high"], "error_vs_expected": round(ratio / f["expected_ratio"] - 1, 4)})
    for r in out:
        print(r)
    return out


if __name__ == "__main__":
    {"make": make, "score": score}[sys.argv[1] if len(sys.argv) > 1 else "score"]()
