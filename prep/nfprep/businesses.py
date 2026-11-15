"""Phase 3a: the business list.

One record per mapped place (OpenStreetMap), plus licensed accommodation and
parking the map misses, plus synthetic businesses so each consumer-facing
industry reaches its count on the Statistics Canada business register.

`revenue_prior` is a starting estimate drawn from the industry's revenue
quartiles. The engine replaces it with revenue earned from simulated spending;
the prior is used for calibration and for the break-even cost base.
"""
from __future__ import annotations

import math

import numpy as np

from .loader import Pack
from .population import points_in_ring

# sim_category -> NAICS group used for register counts and cost structure
CATEGORY_NAICS = {
    "restaurant": "722", "fast_food": "722", "cafe": "722", "bar_pub": "722", "nightlife": "722",
    "accommodation": "721",
    "attraction": "713", "leisure_paid": "713", "casino": "713", "entertainment": "713",
    "grocery_food": "445", "apparel": "458", "general_merchandise": "455", "home_electronics": "449",
    "retail_other": "459", "souvenir_gift": "459", "tobacco_cannabis": "459", "pet": "459", "craft": "459",
    "pharmacy": "456", "health": "621", "personal_services": "812", "fuel": "457",
    "auto_service": "811", "car_rental": "532", "parking": "812", "childcare": "624",
    "bank": "", "atm": "", "services": "", "office": "",
}
# Register groups topped up with synthetic businesses, and the category the synthetic ones get.
# 449 also absorbs 444 (building material and garden), which the map files under home_electronics.
TOP_UP = {"445": "grocery_food", "455": "general_merchandise", "449": "home_electronics", "456": "pharmacy",
          "621": "health", "812": "personal_services", "811": "auto_service", "532": "car_rental",
          "441": "auto_dealer"}
EXTRA_REGISTER = {"449": ["444"]}
SIZE_BANDS = ["nf_1_4", "nf_5_9", "nf_10_19", "nf_20_49", "nf_50_99", "nf_100_plus"]
BAND_STAFF = {"non_employer": (0, 0), "nf_1_4": (1, 4), "nf_5_9": (5, 9), "nf_10_19": (10, 19),
              "nf_20_49": (20, 49), "nf_50_99": (50, 99), "nf_100_plus": (100, 250)}  # SEED: 100+ capped at 250
LICENCE_CATEGORY = {"vacation_rental": "accommodation", "bed_and_breakfast": "accommodation",
                    "owner_occupied_str": "accommodation", "campground": "accommodation",
                    "parking_lot": "parking"}
# SEED: default opening hours (open, close; close > 24 runs past midnight) where the map gives none
DEFAULT_HOURS = {"restaurant": (11, 22), "fast_food": (7, 23), "cafe": (7, 19), "bar_pub": (11, 26),
                 "nightlife": (20, 27), "accommodation": (0, 24), "attraction": (9, 21), "leisure_paid": (9, 21),
                 "casino": (0, 24), "entertainment": (12, 23), "souvenir_gift": (9, 22), "grocery_food": (8, 21),
                 "fuel": (6, 23), "parking": (0, 24), "pharmacy": (9, 21), "atm": (0, 24)}
FALLBACK_HOURS = (9, 18)
# SEED: pull of a store format relative to a typical outlet in its category, keyed by the
# OpenStreetMap tag. A supermarket draws far more grocery spending than a corner store.
FORMAT_WEIGHT = {"supermarket": 10, "wholesale": 10, "department_store": 6, "mall": 3, "convenience": 0.5,
                 "alcohol": 1.5, "wine": 0.8, "bakery": 0.4, "butcher": 0.4, "deli": 0.4, "greengrocer": 0.4,
                 "confectionery": 0.3, "health_food": 0.4, "frozen_food": 0.4, "variety_store": 1.0,
                 "doityourself": 5, "hardware": 3, "furniture": 3, "appliance": 2, "electronics": 3,
                 "garden_centre": 2, "houseware": 1, "bed": 1, "lighting": 0.5, "paint": 0.5,
                 "mobile_phone": 0.2, "computer": 0.5, "telecommunication": 0.2,
                 "ticket": 0.1, "money_lender": 0.05, "travel_agency": 0.1, "vacant": 0.0, "storage_rental": 0.05,
                 "kiosk": 0.2, "sports": 2, "bicycle": 1, "second_hand": 0.7}
REVENUE_FLOOR, REVENUE_CAP = 30_000, 5_000_000      # the benchmark covers businesses in this range


def _f(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_hours(text: str, category: str) -> tuple[int, int]:
    """Read the common OpenStreetMap forms ("24/7", "Mo-Su 10:00-23:00"); otherwise use the default."""
    t = (text or "").strip()
    if t == "24/7" or "00:00-24:00" in t:
        return 0, 24
    for part in t.split(";"):
        bits = part.strip().split(" ")
        span = bits[-1]
        if len(span) == 11 and span[2] == ":" and span[5] == "-" and span[8] == ":":
            try:
                o, c = int(span[:2]), int(span[6:8])
            except ValueError:
                continue
            return o, c + 24 if c <= o else c
    return DEFAULT_HOURS.get(category, FALLBACK_HOURS)


def _draw_with_mean(rng: np.random.Generator, lo: float, hi: float, mean: float) -> float:
    """A draw on [lo, hi] whose average is `mean` (beta-shaped toward whichever end the mean sits)."""
    mean = min(max(mean, lo + 1), hi - 1)
    frac = (mean - lo) / (hi - lo)
    if frac <= 0.5:
        return lo + (hi - lo) * rng.beta(1, 1 / frac - 1)
    return hi - (hi - lo) * rng.beta(1, 1 / (1 - frac) - 1)


def _dist_m(lat1: float, lon1: float, lat2: np.ndarray, lon2: np.ndarray) -> np.ndarray:
    k = 111_320.0
    return np.hypot((lat2 - lat1) * k, (lon2 - lon1) * k * math.cos(math.radians(lat1)))


def build_businesses(pack: Pack, seed: int = 1) -> list[dict]:
    rng = np.random.default_rng(seed + 7_000)
    bench = {r["naics"]: r for r in pack.tables["business_revenue_benchmarks.csv"]}
    lookup = {r["osm_id"]: r["census_tract"] for r in pack.tables["places_tract_lookup.csv"]}
    earn = pack.params.get("avg_weekly_earnings_sectors")
    ratios = pack.params["sim_parameters.json"]["who_makes_what"]["revenue_to_payroll_ontario"]
    rev_per_payroll = {"721": ratios["accommodation_721"]["revenue_per_payroll_dollar"],
                       "722": ratios["food_services_722"]["revenue_per_payroll_dollar"],
                       "713": ratios["other_amusement_7139"]["revenue_per_payroll_dollar"]}
    retail_ratio = ratios["retail_44_45"]["revenue_per_payroll_dollar"]
    tract_rings = {f["properties"]["census_tract"]: f["geometry"]["coordinates"][0]
                   for f in pack.geo["census_tracts_boundaries.geojson"]["features"]}
    core_ring = pack.geo["tourist_core_boundary.geojson"]["features"][0]["geometry"]["coordinates"][0]

    rows: list[dict] = []

    def add(source, name, category, lat, lon, tract="", core=None, osm_id="", brand="", rooms="", hours="", tag=""):
        o, c = parse_hours(hours, category)
        rows.append({"business_id": len(rows), "source": source, "osm_id": osm_id, "name": name,
                     "sim_category": category, "naics": CATEGORY_NAICS.get(category, "441" if category == "auto_dealer" else ""),
                     "lat": round(float(lat), 6), "lon": round(float(lon), 6), "census_tract": tract,
                     "in_tourist_core": core, "brand": brand, "rooms": rooms, "open_hour": o, "close_hour": c,
                     "osm_tag": tag, "format_weight": FORMAT_WEIGHT.get(tag, 1.0)})

    for p in pack.tables["places.csv"]:
        add("osm", p["name"], p["sim_category"], p["lat"], p["lon"], lookup[p["osm_id"]],
            int(p["in_tourist_core"]), p["osm_id"], p["brand"], p["rooms"], p["opening_hours"], p["value"])

    # licensed accommodation and parking not already on the map (nothing of that category within 40 m)
    for lic in pack.tables["licensed_tourism_businesses.csv"]:
        cat = LICENCE_CATEGORY.get(lic["category"])
        if not cat or not lic["lat"]:
            continue
        same = [r for r in rows if r["sim_category"] == cat]
        d = _dist_m(float(lic["lat"]), float(lic["lon"]), np.array([r["lat"] for r in same]),
                    np.array([r["lon"] for r in same]))
        if len(d) and d.min() < 40:
            continue
        add("licence:" + lic["category"], lic["business"], cat, lic["lat"], lic["lon"])

    # synthetic businesses up to the register count, placed beside existing shops outside the Tourist Core
    anchors = [r for r in rows if r["source"] == "osm" and r["in_tourist_core"] == 0
               and r["naics"] in ("445", "455", "449", "456", "459", "812", "811", "722", "457")]
    for naics, category in TOP_UP.items():
        register = sum(int(_f(bench[n]["nf_employer_businesses_2026"])) for n in [naics] + EXTRA_REGISTER.get(naics, []))
        have = sum(1 for r in rows if r["naics"] == naics)
        for k in range(max(0, register - have)):
            a = anchors[int(rng.integers(len(anchors)))]
            ang, dist = rng.uniform(0, 2 * math.pi), rng.uniform(30, 90)
            add("synthetic", f"Synthetic {category} {k + 1}", category,
                a["lat"] + dist * math.sin(ang) / 111_320,
                a["lon"] + dist * math.cos(ang) / (111_320 * math.cos(math.radians(a["lat"]))))

    # tract and Tourist Core flags for everything added after the map places
    lat = np.array([r["lat"] for r in rows])
    lon = np.array([r["lon"] for r in rows])
    core = points_in_ring(lon, lat, core_ring)
    tract_of = np.array([""] * len(rows), dtype=object)
    for t, ring in tract_rings.items():
        tract_of[points_in_ring(lon, lat, ring)] = t
    cent = {t: (float(r["lat"]), float(r["lon"])) for t, r in pack.by_tract("tract_profiles.csv").items()}
    for i, r in enumerate(rows):
        if not r["census_tract"]:
            r["census_tract"] = tract_of[i] or min(cent, key=lambda t: _dist_m(r["lat"], r["lon"],
                                                   np.array([cent[t][0]]), np.array([cent[t][1]]))[0])
        if r["in_tourist_core"] is None:
            r["in_tourist_core"] = int(core[i])

    # size bands: the register's employer counts go to the most prominent businesses in each group
    for naics in sorted({r["naics"] for r in rows if r["naics"]}):
        group = [r for r in rows if r["naics"] == naics]
        counts = {b: sum(int(_f(bench[n][b])) for n in [naics] + EXTRA_REGISTER.get(naics, []) if n in bench)
                  for b in SIZE_BANDS}
        bands = [b for b in reversed(SIZE_BANDS) for _ in range(counts[b])]
        prominence = np.array([(1.0 if r["brand"] else 0) + 0.8 * r["in_tourist_core"]
                               + min(_f(r["rooms"]) / 100, 3) + (1.0 if r["sim_category"] == "casino" else 0)
                               + 1.5 * math.log10(max(r["format_weight"], 0.01))
                               - (1.5 if r["source"].startswith("licence") else 0)
                               for r in group]) + rng.normal(0, 0.7, len(group))      # SEED: ranking weights
        for rank, gi in enumerate(np.argsort(-prominence)):
            group[gi]["size_band"] = bands[rank] if rank < len(bands) else "non_employer"
    for r in rows:
        r.setdefault("size_band", "non_employer")

    # staff, revenue prior and cost structure
    for naics in sorted({r["naics"] for r in rows}):
        group = [r for r in rows if r["naics"] == naics]
        b = bench.get(naics)
        order = sorted(group, key=lambda r: (SIZE_BANDS.index(r["size_band"]) if r["size_band"] in SIZE_BANDS else -1,
                                             r["in_tourist_core"], rng.random()))
        employers = [r for r in order if r["size_band"] != "non_employer"]
        for r in group:
            lo, hi = BAND_STAFF[r["size_band"]]
            r["staff"] = int(rng.integers(lo, hi + 1))
            r["employer"] = int(r["size_band"] != "non_employer")
            for col, key in (("cost_of_sales_pct", "cost_of_sales_pct"), ("rent_pct", "rent_pct"),
                             ("utilities_pct", "utilities_pct"), ("advertising_pct", "advertising_pct"),
                             ("net_profit_pct", "net_profit_pct")):
                r[col] = _f(b[key]) if b else ""
            r["wages_pct"] = round(_f(b["wages_benefits_pct"]) + _f(b["labour_commissions_pct"]), 1) if b else ""
            r["revenue_prior"] = ""
        if not b:
            continue
        cuts = [REVENUE_FLOOR, _f(b["q1_q2_cutoff"]), _f(b["median_cutoff"]), _f(b["q3_q4_cutoff"]), REVENUE_CAP]
        means = [_f(b[f"q{q}_avg_revenue"]) for q in (1, 2, 3, 4)]
        for rank, r in enumerate(employers):                 # larger and Tourist Core businesses sit in upper quartiles
            q = min(3, int(4 * rank / max(len(employers), 1)))
            r["revenue_prior"] = int(_draw_with_mean(rng, cuts[q], cuts[q + 1], means[q]))
            if r["staff"] >= 50:                             # beyond the small-business benchmark: scale from payroll
                weekly = earn[naics[:2] if naics[:2] in earn else "72"] if earn else 0
                ratio = rev_per_payroll.get(naics, retail_ratio if naics[:1] == "4" else 3.0)
                r["revenue_prior"] = max(r["revenue_prior"], int(r["staff"] * weekly * 52 * ratio))
        for r in group:
            if r["size_band"] == "non_employer":
                r["revenue_prior"] = int(_draw_with_mean(rng, cuts[0], cuts[1], means[0]))
    return rows
