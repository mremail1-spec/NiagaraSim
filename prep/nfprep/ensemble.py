"""Scenarios that explain what we observe: history matching over the model's uncertain settings.

The model has settings nobody has measured (how far people will go, how strongly chains, the Tourist
Core and the casinos draw, how much visitors spend...). Instead of picking one value for each, this
draws many combinations, runs a year for each, and keeps every combination whose results agree with
everything measured, within each measurement's own uncertainty. The survivors are the scenarios:
where they agree, the data decide the answer; where they differ, the data cannot tell yet.

Facts matched (trust order as in CLAUDE.md; government figures labelled and given wider tolerance):
  third party / operators  chain sales per location (published by the chains), Niagara Parks' own
                           revenue (annual report), casino gaming revenue (from the City's hosting fee),
                           hotel occupancy (CBRE), road traffic counts and TomTom trip times (stage 2)
  known closures           businesses closed by Nov 2026 (Google Places, first-hand) should have been
                           weaker than their open neighbours of the same kind in 2025
  government (labelled)    Statistics Canada store sales by type; Niagara Region's employee bands

Stage 1 (about a second a run): money targets, flow mode. Stage 2 (about a minute): the survivors run
an agent year and are scored on traffic counts and monthly hotel occupancy.

Usage: python3 -m nfprep.ensemble wave N        (N new draws; repeat to narrow around survivors)
       python3 -m nfprep.ensemble traffic       (stage 2 for stage-1 survivors)
       python3 -m nfprep.ensemble report
"""
from __future__ import annotations

import csv
import difflib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

import numpy as np

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build" / "city_2025" / "city.json"
BASE = CITY.with_name("city_base.json")      # the city as calibrated, before the default scenario
OUT = ROOT / "build" / "ensemble"
RUNS = OUT / "runs.jsonl"
NFSIM = str(ROOT / "engine/target/release/nfsim")

# knob: (low, high, how drawn); each is a multiplier or value applied to the calibrated city
KNOBS = {
    "beta_mult":          (0.75, 1.30, "log"),   # how fast pull falls with travel time
    "attract_exp":        (0.30, 0.80, "lin"),   # pull grows with size ** this
    "tourist_floor_min":  (1.0, 10.0, "log"),    # minutes below which visitors stop caring about distance
    "visitors_mult":      (0.85, 1.15, "lin"),   # visitor count (StatCan 2017, government)
    "spend_mult":         (0.75, 1.30, "log"),   # visitor spend per person-day (surveys, government)
    "restaurant_capture": (0.70, 1.30, "log"),   # share of visitors' restaurant budget spent in the city
    "chain_mult":         (0.60, 1.60, "log"),   # extra pull of a chain branch
    "core_mult":          (0.70, 1.60, "log"),   # extra pull of businesses in the Tourist Core
    "casino_mult":        (0.70, 1.40, "log"),
    "parks_mult":         (0.70, 1.40, "log"),   # Niagara Parks shops and restaurants
    "safety_scale":       (0.0, 2.0, "lin"),     # how much visitors avoid documented hot spots
    "outings_mult":       (0.90, 1.10, "lin"),   # residents' shopping trips
}

# how much each chain figure is trusted (data/public_unit_sales_2025.json "quality")
QUALITY = {"company filing": 1.0, "filing sales": 0.8, "2022-23 retail sales over store count": 0.8,
           "franchise disclosure": 0.6, "estimate from system sales and approximate count": 0.5}

# tolerances: a result within tol (as a log ratio) of the fact scores 1 at the edge
TOL = {"chains": math.log(1.5), "niagara_parks": math.log(1.10), "casinos": math.log(1.15),
       "hotel_occupancy": math.log(1.08), "statcan_sales": math.log(1.40)}


def draw(n: int, rng: np.random.Generator, around: list[dict] | None = None) -> list[dict]:
    """Latin hypercube over the knob ranges; with `around`, jitter survivors instead."""
    out = []
    if around:
        for _ in range(n):
            base = around[rng.integers(len(around))]
            k = {}
            for name, (lo, hi, kind) in KNOBS.items():
                if kind == "log":
                    v = math.exp(math.log(base[name]) + rng.normal(0, 0.15 * (math.log(hi) - math.log(lo))))
                else:
                    v = base[name] + rng.normal(0, 0.15 * (hi - lo))
                k[name] = float(min(max(v, lo), hi))
            out.append(k)
        return out
    cuts = {name: rng.permutation(n) for name in KNOBS}
    for i in range(n):
        k = {}
        for name, (lo, hi, kind) in KNOBS.items():
            u = (cuts[name][i] + rng.random()) / n
            k[name] = float(math.exp(math.log(lo) + u * (math.log(hi) - math.log(lo))) if kind == "log" else lo + u * (hi - lo))
        out.append(k)
    return out


def apply(city: dict, k: dict) -> dict:
    c = json.loads(json.dumps(city))
    p = c["params"]
    p["attractiveness_exponent"] = k["attract_exp"]
    p["tourist_min_minutes"] = k["tourist_floor_min"]
    p["outings_per_person5_per_day"] *= k["outings_mult"]
    t = c["tourism"]
    for s in t["segments"]:
        s["persons_per_year"] *= k["visitors_mult"]
        if "spend_per_person_day" in s:
            s["spend_per_person_day"] *= k["spend_mult"]
    names = t["categories"]["name"]
    if "capture" in t["categories"] and "restaurants" in names:
        i = names.index("restaurants")
        t["categories"]["capture"][i] = min(1.0, t["categories"]["capture"][i] * k["restaurant_capture"])
    b = c["businesses"]
    n = len(b["node"])
    site = b.get("site") or [1.0] * n
    for i in range(n):
        if b["external"][i]:
            continue
        f = 1.0
        if b["chain"] and b["chain"][i] >= 0:
            f *= k["chain_mult"]
        if b["operator"][i] == 2:
            f *= k["casino_mult"]
        elif b["operator"][i] == 1:
            f *= k["parks_mult"]
        b["format_weight"][i] *= f
        if b["core"][i]:
            site[i] *= k["core_mult"]
    b["site"] = site
    if c.get("safety"):
        c["safety"]["scale"] = k["safety_scale"]
    return c


class Facts:
    """Everything a run is scored against, loaded once."""

    def __init__(self, city: dict):
        self.city = city
        b = city["businesses"]
        self.n = len(b["node"])
        cal = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())
        self.cal_beta = cal.get("beta_fit", cal["beta"])
        self.op = np.array(b["operator"])
        self.cat = np.array(b["sim_category"])
        self.external = np.array(b["external"], bool)
        self.closed = np.array([bool(x) for x in b["closed_from"]])
        self.targets = city["operator_targets"]
        # chains: published sales per location
        self.brands = []
        for brand, v in json.loads((DATA_DIR / "public_unit_sales_2025.json").read_text())["brands"].items():
            ids = [i for i, nm in enumerate(b["name"]) if re.search(v["pattern"], nm.lower())
                   and ("category" not in v or city["sim_categories"][b["sim_category"][i]] == v["category"])]
            if ids:
                self.brands.append((brand, ids, v["average"], v.get("high"), QUALITY.get(v["quality"].split(",")[0].split(" /")[0], 0.6)))
        # Statistics Canada store sales by type (government): businesses per NAICS
        biz = list(csv.DictReader(open(CITY.with_name("businesses.csv"), encoding="utf-8")))
        naics = [r["naics"] for r in biz] + [""] * (self.n - len(biz))
        self.sales = []
        for code, v in city["calibration"]["sales_targets"].items():
            if not v.get("calibrate"):
                continue
            ids = [i for i, x in enumerate(naics) if x == code]
            target = math.sqrt(v["supply_side_sales"] * v["per_capita_sales"])
            if ids and target > 0:
                self.sales.append((code, ids, target))
        # Niagara Region Employment Inventory 2025 (government survey): employee band per matched business
        from .inventory_check import BAND, INV, kind_of, norm
        lat, lon = np.array(b["lat"]), np.array(b["lon"])
        cats = city["sim_categories"]
        self.staff = []
        for x in csv.DictReader(open(INV, encoding="utf-8-sig")):
            if x["Year"] != "2025" or x["sizerangeemployees"] not in BAND or not x["lat"] or not kind_of(x["primarynaics"]):
                continue
            la, lo = float(x["lat"]), float(x["lon"])
            d = np.hypot((lat - la) * 111_320, (lon - lo) * 81_200)
            best, score = None, 0.0
            for j in np.argsort(d)[:6]:
                if d[j] < 40 and not b["external"][j]:
                    s = difflib.SequenceMatcher(None, norm(b["name"][j]), norm(x["businessname"])).ratio()
                    if s > score:
                        best, score = int(j), s
            if best is not None and score >= 0.6:
                self.staff.append((cats[b["sim_category"][best]], BAND[x["sizerangeemployees"]], best))

    def score(self, rev: np.ndarray, tour: np.ndarray, report: dict) -> dict:
        r = {}
        # chains
        ratios, above = [], 0
        logs, ws = [], []
        per = {}
        for brand, ids, avg, high, w in self.brands:
            vals = rev[ids]
            ratio = float(np.median(vals)) / avg
            ratios.append(ratio); per[brand] = round(ratio, 3)
            logs.append(math.log(ratio)); ws.append(w)
            above += int(sum(1 for v in vals if high and v > high))
        r["chains_median_ratio"] = float(np.median(ratios))
        # how close every chain is to its own published figure, trusted filings counting most
        r["chains_rms_log"] = float(math.sqrt(np.average(np.square(logs), weights=ws)))
        r["chains_by_brand"] = per
        r["chains_above_best"] = above
        r["niagara_parks_musd"] = float(rev[self.op == 1].sum()) / 1e6
        r["casinos_musd"] = float(rev[self.op == 2].sum()) / 1e6
        r["hotel_occupancy"] = float(report["tourism"].get("hotel_occupancy") or 0)
        errs = [math.log(max(rev[ids].sum(), 1) / tgt) for _, ids, tgt in self.sales]
        r["statcan_sales_rms_log"] = float(math.sqrt(np.mean(np.square(errs))))
        # employee bands: rank correlation within each kind, weighted by matches
        by = defaultdict(list)
        for k, band, j in self.staff:
            by[k].append((band, rev[j]))
        cs, ws = [], []
        for v in by.values():
            if len(v) >= 12 and len({a for a, _ in v}) > 1:
                a = np.argsort(np.argsort([x for x, _ in v])); bb = np.argsort(np.argsort([y for _, y in v]))
                cs.append(float(np.corrcoef(a, bb)[0, 1])); ws.append(len(v))
        r["staff_rank_corr"] = float(np.average(cs, weights=ws)) if ws else float("nan")
        # known closures: closed businesses' 2025 revenue rank within their kind (0.5 = no signal)
        pct = np.full(self.n, np.nan)
        for c in np.unique(self.cat):
            idx = np.where((self.cat == c) & ~self.external)[0]
            if len(idx) > 3:
                pct[idx] = np.argsort(np.argsort(rev[idx])) / (len(idx) - 1)
        cl = pct[self.closed & ~np.isnan(pct)]
        op = pct[~self.closed & ~self.external & ~np.isnan(pct)]
        r["closed_below_open"] = float((cl[:, None] < op[None, :]).mean()) if len(cl) and len(op) else float("nan")
        # where visitor money goes
        core = np.array(self.city["businesses"]["core"], bool)
        tt = tour.sum()
        r["tourist_musd"] = float(tt) / 1e6
        r["tourist_core_share"] = float(tour[core].sum() / tt) if tt else float("nan")
        srt = np.sort(tour)[::-1]
        r["tourist_top10_share"] = float(srt[:10].sum() / tt) if tt else float("nan")
        r["tourist_top50_share"] = float(srt[:50].sum() / tt) if tt else float("nan")
        r["province_share"] = float(tour[(self.op == 1) | (self.op == 2)].sum() / tt) if tt else float("nan")
        # implausibility of each hard fact (1 = at the edge of its tolerance)
        imp = {"chains": abs(math.log(r["chains_median_ratio"])) / TOL["chains"],
               "niagara_parks": abs(math.log(r["niagara_parks_musd"] * 1e6 / self.targets["niagara_parks"])) / TOL["niagara_parks"],
               "casinos": abs(math.log(r["casinos_musd"] * 1e6 / self.targets["casino"])) / TOL["casinos"],
               "statcan_sales": r["statcan_sales_rms_log"] / TOL["statcan_sales"]}
        if r["hotel_occupancy"] > 0:
            imp["hotel_occupancy"] = abs(math.log(r["hotel_occupancy"] / 0.62)) / TOL["hotel_occupancy"]
        r["implausibility"] = imp
        r["max_implausibility"] = max(imp.values())
        return r


_facts = None


def _init():
    global _facts
    _facts = Facts(json.loads((BASE if BASE.exists() else CITY).read_text()))


def run_one(k: dict) -> dict:
    with tempfile.TemporaryDirectory(dir=OUT) as d:
        cp, bp, rp = Path(d) / "city.json", Path(d) / "biz.csv", Path(d) / "run.json"
        cp.write_text(json.dumps(apply(_facts.city, k), separators=(",", ":")))
        subprocess.run([NFSIM, "run", str(cp), "--mode", "flow", "--days", "365", "--beta", str(_facts.cal_beta * k["beta_mult"]),
                        "--businesses", str(bp), "--out", str(rp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rev, tour = np.zeros(_facts.n), np.zeros(_facts.n)
        for row in csv.DictReader(open(bp)):
            i = int(row["business"])
            if i < _facts.n:
                rev[i], tour[i] = float(row["revenue"]), float(row["tourist_revenue"])
        return {"knobs": k, **_facts.score(rev, tour, json.loads(rp.read_text()))}


def wave(n: int, seed: int | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    done = [json.loads(l) for l in open(RUNS)] if RUNS.exists() else []
    rng = np.random.default_rng(seed if seed is not None else len(done) + 1)
    good = [d["knobs"] for d in done if d["max_implausibility"] <= 1.0]
    ks = draw(n, rng, around=good if len(good) >= 5 else None)
    with Pool(max(1, (os.cpu_count() or 2) - 0), initializer=_init) as pool, open(RUNS, "a") as f:
        for i, res in enumerate(pool.imap_unordered(run_one, ks)):
            f.write(json.dumps(res) + "\n"); f.flush()
            if (i + 1) % 25 == 0:
                print(f"{i + 1}/{n}", flush=True)
    summary()


def summary() -> dict:
    done = [json.loads(l) for l in open(RUNS)]
    good = [d for d in done if d["max_implausibility"] <= 1.0]
    fails = defaultdict(int)
    for d in done:
        for k, v in d["implausibility"].items():
            fails[k] += v > 1.0
    print(f"{len(done)} runs, {len(good)} fit every hard fact; facts missed most: {dict(fails)}")
    if good:
        for key in ("chains_median_ratio", "chains_above_best", "niagara_parks_musd", "casinos_musd", "hotel_occupancy",
                    "statcan_sales_rms_log", "staff_rank_corr", "closed_below_open", "tourist_musd", "tourist_core_share",
                    "tourist_top10_share", "tourist_top50_share", "province_share"):
            v = np.array([g[key] for g in good], float)
            print(f"  {key:24s} {np.nanpercentile(v, 10):9.3f} {np.nanmedian(v):9.3f} {np.nanpercentile(v, 90):9.3f}")
    return {"runs": len(done), "fit": len(good)}


def _traffic_one(item):
    i, k = item
    from . import traffic_layers as T, traffic_check
    with tempfile.TemporaryDirectory(dir=OUT) as d:
        cp, tp, rp = Path(d) / "city.json", Path(d) / "traffic.json", Path(d) / "run.json"
        cp.write_text(json.dumps(apply(_facts.city, k), separators=(",", ":")))
        subprocess.run([NFSIM, "run", str(cp), "--mode", "agent", "--days", "365", "--beta", str(_facts.cal_beta * k["beta_mult"]),
                        "--traffic", str(tp), "--out", str(rp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        spend = np.array(json.loads(tp.read_text())).reshape(-1, 4).sum(axis=1)
        bg = np.array(json.loads(CITY.with_name("background_volumes.json").read_text()))
        a, _ = T.score(_facts.city, spend + bg, f"ens{os.getpid()}")
        traffic_check.REGIONAL_FILE = "traffic_counts_regional_roads_2025.json"
        b25, _ = T.score(_facts.city, spend + bg, f"ens{os.getpid()}b")
        traffic_check.REGIONAL_FILE = "traffic_counts_regional_roads.json"
        rep = json.loads(rp.read_text())["tourism"]
    cbre = json.loads((DATA_DIR / "hotel_monthly_cbre.json").read_text())["monthly"]
    occ = rep["hotel_occupancy_by_month"]
    pairs = [(v["occupancy"], occ[int(m[5:]) - 1]) for m, v in cbre.items() if m >= "2024"]
    hotel_err = float(np.median([abs(math.log(s_ / p_)) for p_, s_ in pairs if s_ > 0]))
    return {"run": i, "regional_2019": a["correlation_regional_roads"], "regional_2025": b25["correlation_regional_roads"],
            "highways": a["correlation_highways"], "share_regional_pct": a["median_share_explained_regional_pct"],
            "hotel_monthly_median_abs_log": hotel_err, "hotel_turned_away": rep.get("parties_turned_away_from_full_lodging")}


def traffic(limit: int = 60) -> None:
    """Stage 2: road counts and monthly hotel occupancy for the stage-1 survivors (one agent year each)."""
    done = [json.loads(l) for l in open(RUNS)]
    good = [(i, d["knobs"]) for i, d in enumerate(done) if d["max_implausibility"] <= 1.0]
    tp = OUT / "traffic.jsonl"
    have = {json.loads(l)["run"] for l in open(tp)} if tp.exists() else set()
    first = OUT / "traffic_first.json"
    if first.exists():                      # the closest to the chains first
        order = {i: n for n, i in enumerate(json.loads(first.read_text()))}
        good.sort(key=lambda g: order.get(g[0], 10**9))
    todo = [g for g in good if g[0] not in have][:limit]
    print(f"{len(good)} survivors, {len(todo)} to score on traffic", flush=True)
    with Pool(max(1, (os.cpu_count() or 2)), initializer=_init) as pool, open(tp, "a") as f:
        for j, res in enumerate(pool.imap_unordered(_traffic_one, todo)):
            f.write(json.dumps(res) + "\n"); f.flush()
            print(f"{j + 1}/{len(todo)} {res}", flush=True)


LUNDY = re.compile(r"lundy", re.I)
BIGBOX = re.compile(r"montrose|mcleod|oakwood|optimist|niagara square|costco|pin oak", re.I)
DOWNTOWN = re.compile(r"queen st|bridge st|erie av|park st|ontario av|crysler|huron st|zimmerman", re.I)
MAINFERRY = re.compile(r"main st|ferry st|lundy.s ln e", re.I)


def district(b: dict, i: int) -> str:
    a, la = b["address"][i] or "", b["lat"][i]
    if b["operator"][i] == 1:
        return "Niagara Parks (province)"
    if b["operator"][i] == 2:
        return "Casinos (province)"
    if b["core"][i]:
        return "Fallsview" if la < 43.0875 else "Clifton Hill, Falls Ave, Centre St"
    if la < 43.062:
        return "Chippawa and the south"
    if LUNDY.search(a):
        return "Lundy's Lane"
    if BIGBOX.search(a):
        return "Montrose, McLeod, Oakwood (big stores)"
    if MAINFERRY.search(a):
        return "Main and Ferry"
    if DOWNTOWN.search(a) or (re.search(r"victoria", a, re.I) and la > 43.098):
        return "Downtown and Victoria Ave north (with Great Wolf Lodge)"
    return "Neighbourhood streets elsewhere"


def owner(b: dict, brand: list[str], i: int) -> str:
    if b["operator"][i] in (1, 2):
        return "Province (casinos, Niagara Parks)"
    if (b["chain"] and b["chain"][i] >= 0) or (i < len(brand) and brand[i]):
        return "Chains and brands"
    return "Independent"


def report(n: int = 7) -> dict:
    """Where visitor money goes, across a spread of the scenarios that fit every fact."""
    _init()
    runs = [json.loads(l) for l in open(RUNS)]
    tr = {json.loads(l)["run"]: json.loads(l) for l in open(OUT / "traffic.jsonl")}
    ok = [(i, runs[i]) for i in tr]
    ok.sort(key=lambda x: x[1]["tourist_core_share"])
    pick = [ok[round(q * (len(ok) - 1))] for q in np.linspace(0, 1, n)]
    city = _facts.city
    b = city["businesses"]
    biz = list(csv.DictReader(open(CITY.with_name("businesses.csv"), encoding="utf-8")))
    brand = [r.get("brand", "") for r in biz]
    cats = city["sim_categories"]
    dist = [district(b, i) for i in range(_facts.n)]
    own = [owner(b, brand, i) for i in range(_facts.n)]
    scen = []
    for i, r in pick:
        k = r["knobs"]
        with tempfile.TemporaryDirectory(dir=OUT) as d:
            cp, bp, rp = Path(d) / "city.json", Path(d) / "biz.csv", Path(d) / "run.json"
            cp.write_text(json.dumps(apply(city, k), separators=(",", ":")))
            subprocess.run([NFSIM, "run", str(cp), "--mode", "flow", "--days", "365", "--beta", str(_facts.cal_beta * k["beta_mult"]),
                            "--businesses", str(bp)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            tour = np.zeros(_facts.n)
            rev = np.zeros(_facts.n)
            for row in csv.DictReader(open(bp)):
                j = int(row["business"])
                if j < _facts.n:
                    tour[j], rev[j] = float(row["tourist_revenue"]), float(row["revenue"])
        tt = tour.sum()
        by_d, by_o, by_c, closed_d = defaultdict(float), defaultdict(float), defaultdict(float), defaultdict(float)
        for j in range(_facts.n):
            if b["external"][j]:
                continue
            by_d[dist[j]] += tour[j]; by_o[own[j]] += tour[j]; by_c[cats[b["sim_category"][j]]] += tour[j]
            if b["closed_from"][j]:
                closed_d[dist[j]] += tour[j]
        # one business can have several records (Great Wolf Lodge: hotel, waterpark, parking): group by name and street number
        grp = defaultdict(float)
        for j in range(_facts.n):
            if not b["external"][j]:
                num = (b["address"][j] or "").split(" ")[0]
                grp[(b["name"][j], num, dist[j])] += tour[j]
        top = sorted(grp.items(), key=lambda z: -z[1])[:15]
        scen.append({"run": i, "knobs": k, "facts": {x: r[x] for x in ("chains_median_ratio", "niagara_parks_musd", "casinos_musd", "hotel_occupancy",
                                                                     "statcan_sales_rms_log", "staff_rank_corr", "closed_below_open")},
                     "traffic": tr[i], "visitor_musd": round(tt / 1e6, 1),
                     "by_district_pct": {x: round(100 * v / tt, 1) for x, v in sorted(by_d.items(), key=lambda z: -z[1])},
                     "by_owner_pct": {x: round(100 * v / tt, 1) for x, v in sorted(by_o.items(), key=lambda z: -z[1])},
                     "by_kind_musd": {x: round(v / 1e6, 1) for x, v in sorted(by_c.items(), key=lambda z: -z[1]) if v > 1e6},
                     "top10_pct": round(100 * np.sort(tour)[::-1][:10].sum() / tt, 1),
                     "top50_pct": round(100 * np.sort(tour)[::-1][:50].sum() / tt, 1),
                     "at_businesses_now_closed_musd": round(sum(closed_d.values()) / 1e6, 1),
                     "now_closed_by_district_musd": {x: round(v / 1e6, 1) for x, v in sorted(closed_d.items(), key=lambda z: -z[1])},
                     "top15": [{"name": nm, "district": ds, "visitor_musd": round(v / 1e6, 1)} for (nm, _, ds), v in top]})
    # businesses per district and how many are now closed
    counts = defaultdict(lambda: [0, 0])
    for j in range(_facts.n):
        if not b["external"][j] and cats[b["sim_category"][j]] not in ("free_sight", "parking"):
            counts[dist[j]][0] += 1
            counts[dist[j]][1] += bool(b["closed_from"][j])
    doc = {"_meta": {"what": "Where visitors' money goes in Niagara Falls (2025), across scenarios that fit every measured fact (nfprep.ensemble).",
                     "runs_tried": len(runs), "fit_money_facts": sum(1 for x in runs if x["max_implausibility"] <= 1),
                     "also_fit_traffic_and_hotel_months": len(tr), "scenarios_shown": len(scen)},
           "businesses_by_district": {k: {"businesses": v[0], "now_closed": v[1]} for k, v in sorted(counts.items(), key=lambda z: -z[1][0])},
           "scenarios": scen}
    (ROOT / "docs" / "scenarios_tourist_money.json").write_text(json.dumps(doc, indent=1))
    return doc


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "wave":
        wave(int(a[1]))
    elif a and a[0] == "report":
        report()
    elif a and a[0] == "traffic":
        traffic(int(a[1]) if len(a) > 1 else 60)
    else:
        summary()
