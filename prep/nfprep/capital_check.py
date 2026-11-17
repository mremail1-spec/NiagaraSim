"""Actions over words: does the city's road spending go where the model puts traffic and visitors?

Compares the city's capital projects 2022-2026 (Municipal Works) by census tract with the model's
simulated vehicle volumes, tourist spending and population in the same tract.

Usage: python3 -m nfprep.capital_check <city.json> <node_volumes.json> <businesses.csv> <revenue.csv>
"""
from __future__ import annotations

import csv
import gzip
import json
import sys

import numpy as np

from .loader import DATA_DIR
from .population import points_in_ring


def spearman(a, b) -> float:
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main(city_path: str, volumes_path: str, biz_path: str, revenue_path: str) -> dict:
    city = json.loads(open(city_path).read())
    vol = np.array(json.loads(open(volumes_path).read())).reshape(-1, 4)
    tracts = {f["properties"]["census_tract"]: f["geometry"]["coordinates"][0]
              for f in json.loads((DATA_DIR / "census_tracts_boundaries.geojson").read_text())["features"]}
    names = sorted(tracts)
    projects = json.loads(gzip.open(DATA_DIR / "city_open_data" / "capital_projects.json.gz").read())
    spend = {t: 0.0 for t in names}
    for p in projects:
        a = p["attributes"]
        if a["Department"] != "Municipal Works" or not a["Value"] or not p.get("geometry"):
            continue
        ring = p["geometry"]["rings"][0]
        x, y = np.mean([q[0] for q in ring]), np.mean([q[1] for q in ring])
        for t, r in tracts.items():
            if points_in_ring(np.array([x]), np.array([y]), r)[0]:
                spend[t] += a["Value"]
                break
    lat, lon = np.array(city["nodes"]["lat"]), np.array(city["nodes"]["lon"])
    traffic = {t: 0.0 for t in names}
    visitor_traffic = {t: 0.0 for t in names}
    for t, r in tracts.items():
        m = points_in_ring(lon, lat, r)
        traffic[t] = float(vol[m].sum())
        visitor_traffic[t] = float(vol[m][:, 2:].sum())
    biz = list(csv.DictReader(open(biz_path)))
    rev = {int(r["business"]): r for r in csv.DictReader(open(revenue_path))}
    tourist = {t: 0.0 for t in names}
    for i, b in enumerate(biz):
        if i in rev and b["census_tract"] in tourist:
            tourist[b["census_tract"]] += float(rev[i]["tourist_revenue"])
    prof = {r["census_tract"]: r for r in csv.DictReader(open(DATA_DIR / "tract_profiles.csv"))}
    pop = {t: float(prof[t]["population_2021"]) for t in names}
    s = np.array([spend[t] for t in names])
    out = {"road_and_works_spending_2022_2026_musd": round(s.sum() / 1e6, 1),
           "rank_correlation_with_spending": {
               "simulated_traffic_all": round(spearman(s, [traffic[t] for t in names]), 2),
               "simulated_visitor_traffic": round(spearman(s, [visitor_traffic[t] for t in names]), 2),
               "simulated_tourist_spending": round(spearman(s, [tourist[t] for t in names]), 2),
               "population_2021": round(spearman(s, [pop[t] for t in names]), 2)},
           "by_tract": [{"tract": t, "spending_musd": round(spend[t] / 1e6, 2), "simulated_vehicle_visits": round(traffic[t]),
                         "visitor_share_of_traffic": round(visitor_traffic[t] / max(traffic[t], 1), 3),
                         "tourist_spending_musd": round(tourist[t] / 1e6, 1), "population_2021": int(pop[t])}
                        for t in sorted(names, key=lambda t: -spend[t])]}
    print(json.dumps({k: v for k, v in out.items() if k != "by_tract"}, indent=1))
    for r in out["by_tract"][:6]:
        print(r)
    return out


if __name__ == "__main__":
    res = main(*sys.argv[1:5])
    if len(sys.argv) > 5:
        open(sys.argv[5], "w").write(json.dumps(res, indent=1))
