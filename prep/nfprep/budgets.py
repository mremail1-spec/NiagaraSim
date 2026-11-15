"""Phase 4 (prep side): what each household spends, by category, and the file the engine loads.

Budgets: every household takes the Ontario Survey of Household Spending (2023)
dollars for its income quintile, tilted by the age of its maintainer and its
household type. After tilting, each quintile's city average is rescaled back to
the Ontario figure for that quintile, so the tilt moves money between
households inside a quintile without changing the quintile's total.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .loader import DATA_DIR, Pack

# engine category -> (survey lines added, survey lines subtracted, seasonal index used, sold in shops?)
CATEGORIES = {
    "groceries": (["Food purchased from stores"], [], "retail", True),
    "restaurants": (["Food purchased from restaurants",
                     "Alcoholic beverages served on licensed premises and in restaurants"], [], "restaurants", False),
    "clothing": (["Clothing and accessories"], [], "retail", True),
    "household_goods": (["Household furnishings and equipment"], [], "retail", True),
    "personal_care": (["Personal care"], [], "flat", False),
    "pharmacy": (["Prescribed medicines, pharmaceutical products and cannabis for medical use",
                  "Non-prescribed medicines, pharmaceutical products, health care supplies and equipment"], [], "retail", True),
    "health_services": (["Direct health care costs to household"],
                        ["Prescribed medicines, pharmaceutical products and cannabis for medical use",
                         "Non-prescribed medicines, pharmaceutical products, health care supplies and equipment"], "flat", False),
    "recreation_goods": (["Recreational equipment and related services"], [], "retail", True),
    "recreation_services": (["Recreational services"], ["Package trips"], "flat", False),
    "alcohol_tobacco_cannabis": (["Tobacco products and smokers' supplies", "Alcoholic beverages purchased from stores",
                                  "Cannabis for non-medical use"], [], "retail", True),
    "fuel": (["Gas and other fuels (all vehicles and tools)"], [], "retail", True),
    "vehicle_service": (["Maintenance and repairs of vehicles"], [], "flat", False),
    "pets": (["Pet expenses"], [], "retail", True),
    "games_of_chance": (["Games of chance"], [], "flat", False),
}
CATEGORY_NAMES = list(CATEGORIES)

# Which kinds of business serve each spending category, and how strongly.
# 1.0 = the category's main outlet. SEED: the fractional weights (general merchandise
# stores selling groceries, pharmacies selling personal care, and so on) are priors.
SERVED_BY = {
    "groceries": {"grocery_food": 1.0, "general_merchandise": 0.5},
    "restaurants": {"restaurant": 1.0, "fast_food": 1.0, "cafe": 1.0, "bar_pub": 1.0, "nightlife": 1.0},
    "clothing": {"apparel": 1.0, "general_merchandise": 0.5},
    "household_goods": {"home_electronics": 1.0, "general_merchandise": 0.5},
    "personal_care": {"personal_services": 1.0, "pharmacy": 0.5},
    "pharmacy": {"pharmacy": 1.0},
    "health_services": {"health": 1.0},
    "recreation_goods": {"retail_other": 1.0, "general_merchandise": 0.3, "craft": 0.5},
    "recreation_services": {"leisure_paid": 1.0, "entertainment": 1.0, "attraction": 1.0},
    "alcohol_tobacco_cannabis": {"tobacco_cannabis": 1.0, "grocery_food": 0.3},
    "fuel": {"fuel": 1.0},
    "vehicle_service": {"auto_service": 1.0},
    "pets": {"pet": 1.0, "grocery_food": 0.2, "general_merchandise": 0.3},
    "games_of_chance": {"casino": 1.0},
}
# SEED: typical spend per visit. Only the ratios matter: they split a household's outings
# between categories. The engine rescales them so total outings equal the travel survey's
# trip rate, and revenue is fixed by the budgets whatever these values are.
TYPICAL_VISIT_SPEND = {"groceries": 70, "restaurants": 40, "clothing": 90, "household_goods": 130,
                       "personal_care": 50, "pharmacy": 35, "health_services": 150, "recreation_goods": 70,
                       "recreation_services": 45, "alcohol_tobacco_cannabis": 40, "fuel": 60,
                       "vehicle_service": 250, "pets": 55, "games_of_chance": 100}

AGE_COLUMN = {"15_24": "Less than 30 years", "25_34": "30 to 39 years", "35_44": "40 to 54 years",
              "45_54": "40 to 54 years", "55_64": "55 to 64 years", "65_74": "65 years and over",
              "75_84": "65 years and over", "85_plus": "65 years and over"}
# 25-34 and 35-44 straddle two survey groups; the nearer one is used.
TYPE_COLUMN = {"one_person": "One person households", "couple_no_children": "Couples without children",
               "couple_with_children": "Couples with children",
               "one_parent": "Lone-parent households with no additional persons",
               "multigenerational": "Couples with other related or unrelated persons",
               "multiple_family": "Couples with other related or unrelated persons",
               "one_family_plus_others": "Couples with other related or unrelated persons",
               "non_family_2plus": "Other households with related or unrelated persons"}
SPENDING_DOLLAR_YEAR = 2023


def assign_budgets(pack: Pack, households: list[dict], year: int) -> np.ndarray:
    """Annual spending per household and category, in dollars of `year`. Shape (households, categories)."""
    spend = pack.params["spending_by_demographic.json"]
    on = spend["by_income_quintile_ontario_2023"]
    age = spend["by_age_of_reference_person_canada_2023"]
    typ = spend["by_household_type_canada_2023"]
    cuts = json.loads((DATA_DIR / "ontario_income_quintiles_2023.json").read_text())
    idx = pack.params["earnings_index_ontario.json"]["avg_weekly_earnings_cad"]

    def line(block, name, col):
        v = block["dollars_per_household_year"][name][block["columns"].index(col)]
        return v

    def tilt(block, name, col):
        v = block["index_vs_all_households"][name][block["columns"].index(col)]
        return 1.0 if v is None else v           # suppressed cell: no tilt

    q_names = on["columns"][1:]
    to_cut_year = idx[str(cuts["_meta"]["dollar_year"])] / idx["2020"]
    quint = np.array([int(np.searchsorted(cuts["quintile_upper_limits"], h["income_2020"] * to_cut_year, side="right"))
                      for h in households])
    out = np.zeros((len(households), len(CATEGORY_NAMES)))
    for c, name in enumerate(CATEGORY_NAMES):
        plus, minus, _, _ = CATEGORIES[name]
        target = np.array([sum(line(on, l, q) or 0 for l in plus) - sum(line(on, l, q) or 0 for l in minus)
                           for q in q_names])
        raw = np.zeros(len(households))
        for i, h in enumerate(households):
            a, t = AGE_COLUMN[h["maintainer_age_band"]], TYPE_COLUMN[h["type"]]
            # the tilt of the category is the tilt of its largest survey line
            raw[i] = target[quint[i]] * tilt(age, plus[0], a) * tilt(typ, plus[0], t)
        for q in range(5):                       # keep each quintile's city average at the Ontario figure
            m = quint == q
            if m.any() and raw[m].mean() > 0:
                raw[m] *= target[q] / raw[m].mean()
        out[:, c] = np.maximum(raw, 0)
    for i, h in enumerate(households):
        h["income_quintile_ontario"] = int(quint[i]) + 1
    return out * idx[str(year)] / idx[str(SPENDING_DOLLAR_YEAR)]


def export_city(pack: Pack, city: dict, budgets: np.ndarray, year: int, path: Path) -> dict:
    """Write the single file the engine loads (columnar JSON)."""
    hh, biz, nodes, edges = city["households"], city["businesses"], city["road_nodes"], city["road_edges"]
    beh = pack.params["behaviour_parameters.json"]["residents"]
    trips = beh["trips"]
    season = beh["monthly_seasonality_index"]
    sim_cats = sorted({b["sim_category"] for b in biz}) + ["external"]
    # neighbouring towns as out-of-town destinations, reached through the nearest city road node
    towns = json.loads((DATA_DIR / "neighbouring_municipalities.json").read_text())
    nlat, nlon = np.array([n["lat"] for n in nodes]), np.array([n["lon"] for n in nodes])
    ext = []
    for t in towns["towns"]:
        d = np.hypot((nlat - t["lat"]) * 111.32, (nlon - t["lon"]) * 111.32 * np.cos(np.radians(t["lat"])))
        km = float(d.min()) * towns["detour_factor"]
        ext.append({"name": t["name"] + " (out of town)", "node": int(d.argmin()), "lat": t["lat"], "lon": t["lon"],
                    "extra_km": round(km, 2), "extra_min": round(km / towns["highway_kmh"] * 60, 2),
                    "pull_scale": round(t["population_2021"] / 94415, 4)})
    n_real = len(biz)
    persons5 = np.zeros(len(hh), int)
    for p in city["persons"]:
        if p["age"] >= 5:
            persons5[p["household_id"]] += 1
    tod = trips["time_of_day_pct"]
    # SEED: the survey gives two peaks and "other"; "other" is spread evenly over 9-14h and 19-23h.
    hour_w = [0.0] * 24
    for h in (6, 7, 8):
        hour_w[h] = tod["am_peak_6_9"] / 3
    for h in (15, 16, 17, 18):
        hour_w[h] = tod["pm_peak_15_19"] / 4
    other = [9, 10, 11, 12, 13, 14, 19, 20, 21, 22, 23]
    for h in other:
        hour_w[h] = tod["other"] / len(other)
    ecommerce = beh["payment"]["ecommerce_share_of_retail_2025"]
    doc = {
        "meta": {"year": year, "seed": city["report"]["seed"], "population": len(city["persons"]),
                 "fingerprint": city["report"]["fingerprint"]},
        "nodes": {"lat": [n["lat"] for n in nodes], "lon": [n["lon"] for n in nodes]},
        "edges": {"from": [e["from_node"] for e in edges], "to": [e["to_node"] for e in edges],
                  "km": [round(e["length_m"] / 1000, 4) for e in edges], "minutes": [e["minutes"] for e in edges]},
        "sim_categories": sim_cats,
        "businesses": {"node": [b["road_node"] for b in biz] + [e["node"] for e in ext],
                       "lat": [b["lat"] for b in biz] + [e["lat"] for e in ext],
                       "lon": [b["lon"] for b in biz] + [e["lon"] for e in ext],
                       "sim_category": [sim_cats.index(b["sim_category"]) for b in biz] + [len(sim_cats) - 1] * len(ext),
                       "open": [b["open_hour"] for b in biz] + [0] * len(ext),
                       "close": [b["close_hour"] for b in biz] + [24] * len(ext),
                       "core": [b["in_tourist_core"] for b in biz] + [0] * len(ext),
                       "staff": [b["staff"] for b in biz] + [0] * len(ext),
                       "revenue_prior": [b["revenue_prior"] or 0 for b in biz] + [0] * len(ext),
                       "name": [b["name"] for b in biz] + [e["name"] for e in ext],
                       "format_weight": [b["format_weight"] for b in biz] + [1.0] * len(ext),
                       "external": [0] * n_real + [1] * len(ext),
                       "extra_km": [0.0] * n_real + [e["extra_km"] for e in ext],
                       "extra_min": [0.0] * n_real + [e["extra_min"] for e in ext],
                       "pull_scale": [0.0] * n_real + [e["pull_scale"] for e in ext]},
        "households": {"node": [h["road_node"] for h in hh], "persons5": persons5.tolist(),
                       "vehicles": [h["vehicles"] for h in hh],
                       "budget": [round(float(x), 1) for x in budgets.ravel()]},
        "categories": {
            "name": CATEGORY_NAMES,
            "typical_visit_spend": [TYPICAL_VISIT_SPEND[c] for c in CATEGORY_NAMES],
            "retail": [int(CATEGORIES[c][3]) for c in CATEGORY_NAMES],
            "season": [[1.0] * 12 if CATEGORIES[c][2] == "flat" else season[CATEGORIES[c][2]] for c in CATEGORY_NAMES],
            # every category can also be bought out of town, except casino gaming (both casinos are in the city)
            "served_by": [[(0.0 if c == "games_of_chance" else 1.0) if s == "external" else SERVED_BY[c].get(s, 0.0)
                           for s in sim_cats] for c in CATEGORY_NAMES],
        },
        "params": {
            # one outing = a trip out and a trip home, so outings = discretionary trips / 2
            "outings_per_person5_per_day": trips["trips_per_person_5plus"] * trips["purpose_pct"]["home_based_discretionary"] / 100 / 2,
            "hour_weights": hour_w,
            "mode_pct": trips["mode_pct"],
            "median_driver_trip_km_target": trips["median_driver_trip_km"],
            "online_share_of_retail": ecommerce,
            "walk_max_km": 1.5,                 # SEED: longest trip made on foot or by bike
            "spend_sigma": 0.5,                 # SEED: spread of one visit's spend around its typical value
            "attractiveness_exponent": 0.5,     # SEED: pull grows with the square root of a business's size
            "min_minutes": 1.0,
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":")))
    return doc
