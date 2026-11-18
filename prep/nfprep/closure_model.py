"""Two-year closure chance for a business at any spot, fitted on the Employment Inventory.

Pooled two-year windows (2016-18, 2017-19, 2022-24, 2023-25; 388 closures among 4,322
business-windows) and a ridge logistic model on: the kind's closure rate, age, employee band,
floor area and the share of business units within 300 m that are vacant or closed. Cross-validated
skill +4.2% against each kind's rate, ranking 0.71 (docs/closure_signals_check.json; the same
business can appear in more than one window, so slightly optimistic).
Writes data/closure_model.json and each road node's vacancy (2025) into city.json (nodes.vacancy_300m).

Usage: python3 -m nfprep.closure_model
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .closure_risk import fit
from .closure_signals import _km, rows_for
from .inventory_check import load

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = [(2016, 2018), (2017, 2019), (2022, 2024), (2023, 2025)]


def main() -> dict:
    import nfprep.closure_risk as cr
    cr.L2 = 10.0
    status, rec = load()
    city_path = ROOT / "build/city_2025/city.json"
    city = json.loads(city_path.read_text())
    rows = [r for s, e in WINDOWS for r in rows_for(status, rec, s, e, city)]
    y = np.array([float(r["closed"]) for r in rows])
    o = y.mean()
    kinds = sorted({r["kind"] for r in rows})
    rate = {k: float((y[[r["kind"] == k for r in rows]].sum() + 20 * o) / (sum(r["kind"] == k for r in rows) + 20)) for k in kinds}
    med_age = float(np.median([r["age"] for r in rows if r["age"] is not None]))
    X = np.column_stack([[np.log(rate[r["kind"]] / (1 - rate[r["kind"]])) for r in rows],
                         [np.log1p(r["age"] if r["age"] is not None else med_age) for r in rows],
                         [r["band"] for r in rows], [np.log1p(r["gfa"]) for r in rows],
                         [r["vacancy_300m"] for r in rows]])
    w, mu, sd = fit(X, y)
    gfa = {k: float(np.median([r["gfa"] for r in rows if r["kind"] == k and r["gfa"] > 0] or [150])) for k in kinds}
    model = {"_meta": {"what": __doc__.strip().split("\n\n")[0], "features": ["kind rate (logit)", "log(1 + age)", "employee band (1 micro, 2 small, 3 medium, 4 large)",
                                                                             "log(1 + floor area m2... as surveyed)", "vacancy share within 300 m"],
                       "fitted_on": f"{len(rows)} business-windows, {int(y.sum())} closures", "overall_two_year_rate": round(float(o), 4)},
             "weights": [round(float(v), 5) for v in w], "mean": [round(float(v), 5) for v in mu], "sd": [round(float(v), 5) for v in sd],
             "kind_rate": {k: round(v, 4) for k, v in rate.items()}, "median_floor_area": gfa}
    (ROOT / "data" / "closure_model.json").write_text(json.dumps(model, indent=1))
    # vacancy around every road node, from the 2025 inventory
    pts = [(float(rec[i][2025]["lat"]), float(rec[i][2025]["lon"]), st[2025]) for i, st in status.items()
           if 2025 in st and rec[i][2025]["lat"] and st[2025] in ("open", "closed")]
    pl, po, ps = np.array([p[0] for p in pts]), np.array([p[1] for p in pts]), np.array([p[2] == "closed" for p in pts])
    vac = []
    for la, lo in zip(city["nodes"]["lat"], city["nodes"]["lon"]):
        m = _km(pl, po, la, lo) < 0.3
        vac.append(round(float(ps[m].mean()), 3) if m.sum() >= 3 else None)
    known = [v for v in vac if v is not None]
    for path in (city_path, ROOT / "web" / "city.json"):
        c = json.loads(path.read_text())
        if len(c["nodes"]["lat"]) == len(vac):
            c["nodes"]["vacancy_300m"] = vac
            c["closure_model"] = model
            path.write_text(json.dumps(c, separators=(",", ":")))
    print(f"fitted on {len(rows)} windows, {int(y.sum())} closures; weights {model['weights']}; "
          f"vacancy known at {len(known)} nodes, median {np.median(known):.3f}")
    return model


if __name__ == "__main__":
    main()
