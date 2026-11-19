"""Rent: Statistics Canada's industry rent shares (government) against asking rents in listings (third party).

The model charges each business its industry's average rent as a share of sales. Turned into rent per
square foot (share x model sales / floor area, floor area from Niagara Region's Employment Inventory
2025 where a business matches one), it is compared with asking rents for retail and office space on the
same street (data/real_estate/niagara_falls_commercial_lease_2026-11.json). Asking rents are not
achieved rents, and net or gross is rarely stated.
Usage: python3 -m nfprep.rent_check
"""
from __future__ import annotations

import csv
import json
import re
import statistics as st
from pathlib import Path

import numpy as np

from .backtest import same_business
from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
SKIP = {"accommodation", "parking", "attraction", "leisure_paid", "entertainment", "free_sight", "casino", "auto_dealer", "car_rental", "fuel"}
STREETS = {"Lundy's Lane": r"lundy", "Queen Street": r"\bqueen", "Victoria Avenue": r"victoria", "Portage Road": r"portage",
           "Morrison Street": r"morrison"}


def main(city_dir: Path = ROOT / "build" / "city_2025") -> dict:
    biz = list(csv.DictReader(open(city_dir / "businesses.csv")))
    rev = {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(city_dir / "business_revenue_flow.csv"))}
    inv = [x for x in csv.DictReader(open(DATA_DIR / "sources/region/employment_inventory_niagara_falls_2016_2025.csv", encoding="utf-8-sig"))
           if x["Year"] == "2025" and x["lat"] and x["indoorgfa"] not in ("", "0")]
    ilat, ilon = np.array([float(x["lat"]) for x in inv]), np.array([float(x["lon"]) for x in inv])
    psf = {}
    for i, b in enumerate(biz):
        if b["sim_category"] in SKIP or not rev.get(i) or not b["rent_pct"]:
            continue
        la, lo = float(b["lat"]), float(b["lon"])
        d = np.hypot((ilat - la) * 111_320, (ilon - lo) * 111_320 * np.cos(np.radians(la)))
        hit = [k for k in np.where(d < 80)[0] if b["name"] and same_business(b["name"], inv[k]["businessname"])]
        if hit and float(inv[hit[0]]["indoorgfa"]) >= 200:
            psf[i] = float(b["rent_pct"]) / 100 * rev[i] / float(inv[hit[0]]["indoorgfa"])
    lease = json.loads((DATA_DIR / "real_estate/niagara_falls_commercial_lease_2026-11.json").read_text())["listings"]
    ask = [(r["address"], r.get("rent_psf_year") or r.get("rent_psf_year_from_month")) for r in lease if "industrial" not in (r.get("use") or "")]
    ask = [(a, v) for a, v in ask if v]
    q = lambda v: [round(x, 1) for x in st.quantiles(v, n=4)]
    out = {"_meta": {"what": __doc__.strip().split("\n\n")[0]},
           "city": {"model_rent_psf_median": round(st.median(psf.values()), 1), "model_quartiles": q(list(psf.values())), "model_businesses": len(psf),
                    "asking_rent_psf_median": round(st.median(v for _, v in ask), 1), "asking_quartiles": q([v for _, v in ask]), "asking_listings": len(ask)},
           "by_street": {}, "by_kind": {}}
    for name, pat in STREETS.items():
        m = [v for i, v in psf.items() if re.search(pat, biz[i]["address"] or "", re.I)]
        a = [v for ad, v in ask if re.search(pat, ad, re.I)]
        out["by_street"][name] = {"model_median": round(st.median(m), 1) if m else None, "model_n": len(m),
                                  "asking": sorted(round(v, 1) for v in a)}
    kinds = {}
    for i, v in psf.items():
        kinds.setdefault(biz[i]["sim_category"], []).append(v)
    out["by_kind"] = {k: {"model_median": round(st.median(v), 1), "n": len(v)} for k, v in sorted(kinds.items(), key=lambda t: -len(t[1])) if len(v) >= 5}
    (ROOT / "docs" / "rent_check.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
