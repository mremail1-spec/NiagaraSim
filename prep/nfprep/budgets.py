"""Phase 4 (prep side): what each household spends, by category, and the file the engine loads.

Budgets: every household takes the Ontario Survey of Household Spending (2023)
dollars for its income quintile, tilted by the age of its maintainer and its
household type. After tilting, each quintile's city average is rescaled back to
the Ontario figure for that quintile, so the tilt moves money between
households inside a quintile without changing the quintile's total.
"""
from __future__ import annotations

import json
import re
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
    "alcohol": (["Alcoholic beverages purchased from stores"], [], "retail", True),
    "tobacco_cannabis": (["Tobacco products and smokers' supplies", "Cannabis for non-medical use"], [], "retail", True),
    "fuel": (["Gas and other fuels (all vehicles and tools)"], [], "retail", True),
    "vehicle_service": (["Maintenance and repairs of vehicles"], [], "flat", False),
    "pets": (["Pet expenses"], [], "retail", True),
    "games_of_chance": (["Games of chance"], [], "flat", False),
    # Department, warehouse and dollar stores. No survey line of its own: its budget is set from
    # Ontario's per-capita general-merchandise sales (see RETAIL_STORE_TYPES).
    "general_merchandise": ([], [], "retail", True),
}
# Household retail budgets are scaled so the city's total for each store type equals Ontario's
# retail sales per resident for that store type (Statistics Canada table 20-10-0056) times the
# population. The household survey alone covers only about two thirds of retail sales: it leaves
# out purchases by businesses and under-reports several categories.
RETAIL_STORE_TYPES = {"445": ["groceries", "alcohol"], "449": ["household_goods"], "456": ["pharmacy"],
                      "457": ["fuel"], "458": ["clothing"], "459": ["recreation_goods", "pets", "tobacco_cannabis"]}
RETAIL_SCALES: dict = {}
# LCBO retail store sales $5.87B in 2022-23 (CBC) over about 15.6M Ontarians: $376 a person; SEED: plus 30%
# for The Beer Store, wine shops and winery stores. Dollars of 2025.
ALCOHOL_PER_PERSON = 376 * 1.3
CATEGORY_NAMES = list(CATEGORIES)

# Which kinds of business serve each spending category, and how strongly.
# 1.0 = the category's main outlet. SEED: the fractional weights (general merchandise
# stores selling groceries, pharmacies selling personal care, and so on) are priors.
SERVED_BY = {
    "groceries": {"grocery_food": 1.0},
    "restaurants": {"restaurant": 1.0, "fast_food": 1.0, "cafe": 1.0, "bar_pub": 1.0, "nightlife": 1.0},
    "clothing": {"apparel": 1.0},
    "household_goods": {"home_electronics": 1.0},
    "general_merchandise": {"general_merchandise": 1.0},
    "personal_care": {"personal_services": 1.0, "pharmacy": 0.5},
    "pharmacy": {"pharmacy": 1.0},
    "health_services": {"health": 1.0},
    "recreation_goods": {"retail_other": 1.0, "craft": 0.5},
    "recreation_services": {"leisure_paid": 1.0, "entertainment": 1.0, "attraction": 1.0},
    # SEED: since 2024 Ontario grocery and convenience stores may sell beer and wine; LCBO and The Beer
    # Store still sell most of it
    "alcohol": {"liquor": 1.0, "grocery_food": 0.02},   # tested 0.1 / 0.02 / 0: LCBO at 0.20 / 0.37 / 0.54 of published sales
    "tobacco_cannabis": {"tobacco_cannabis": 1.0, "grocery_food": 0.3},
    "fuel": {"fuel": 1.0},
    "vehicle_service": {"auto_service": 1.0},
    "pets": {"pet": 1.0, "grocery_food": 0.2},
    "games_of_chance": {"casino": 1.0},
}
# SEED: typical spend per visit. Only the ratios matter: they split a household's outings
# between categories. The engine rescales them so total outings equal the travel survey's
# trip rate, and revenue is fixed by the budgets whatever these values are.
TYPICAL_VISIT_SPEND = {"general_merchandise": 80, "groceries": 70, "restaurants": 40, "clothing": 90, "household_goods": 130,
                       "personal_care": 50, "pharmacy": 35, "health_services": 150, "recreation_goods": 70,
                       "recreation_services": 45, "alcohol": 45, "tobacco_cannabis": 30, "fuel": 60,
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
        if not plus:
            continue
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
    out *= idx[str(year)] / idx[str(SPENDING_DOLLAR_YEAR)]
    # scale retail categories to Ontario's per-capita retail sales by store type
    from .calibration import sales_targets
    population = sum(h["size"] for h in households)
    targets = sales_targets(pack, population)
    to_year = idx[str(year)] / idx["2025"]
    col = {n: i for i, n in enumerate(CATEGORY_NAMES)}
    basket = out[:, col["groceries"]] + out[:, col["clothing"]] + out[:, col["household_goods"]]
    for group, names in RETAIL_STORE_TYPES.items():
        cols = [col[n] for n in names]
        total = targets[group]["per_capita_sales"] * to_year
        if group == "445":
            # alcohol bought from stores: the household survey under-reports it badly ($28M for the city);
            # set it from LCBO sales per Ontarian instead, and give the rest of the store type to groceries
            a = col["alcohol"]
            want = ALCOHOL_PER_PERSON * population * to_year
            out[:, a] *= want / out[:, a].sum()
            g = col["groceries"]
            out[:, g] *= (total - want) / out[:, g].sum()
            RETAIL_SCALES[group] = round(float(total / out[:, cols].sum()), 3)
            continue
        k = total / out[:, cols].sum()
        out[:, cols] *= k
        RETAIL_SCALES[group] = round(float(k), 3)
    out[:, col["general_merchandise"]] = targets["455"]["per_capita_sales"] * to_year * basket / basket.sum()
    return out


def _gateways(tour: dict, nodes: list[dict], edges: list[dict], toward_node: int) -> list[dict]:
    """Road node for each gateway: among nodes within 250 m of the gateway point, the one with the
    fastest drive into the Tourist Core, which picks the inbound carriageway of a divided highway."""
    import heapq
    rev: dict[int, list] = {}
    for e in edges:
        rev.setdefault(e["to_node"], []).append((e["from_node"], e["minutes"]))
    time = {toward_node: 0.0}
    heap = [(0.0, toward_node)]
    while heap:
        t, u = heapq.heappop(heap)
        if t > time.get(u, 1e18):
            continue
        for v, dt in rev.get(u, []):
            if t + dt < time.get(v, 1e18):
                time[v] = t + dt
                heapq.heappush(heap, (t + dt, v))
    lat, lon = np.array([n["lat"] for n in nodes]), np.array([n["lon"] for n in nodes])
    out = []
    for g in tour["gateways"]:
        d = np.hypot((lat - g["lat"]) * 111.32, (lon - g["lon"]) * 111.32 * np.cos(np.radians(g["lat"])))
        near = [int(i) for i in np.where(d < max(0.25, float(d.min()) + 0.05))[0]]
        node = min(near, key=lambda i: time.get(i, 1e18))
        out.append({"name": g["name"], "node": node, "domestic": g["domestic"], "us": g["us"], "overseas": g["overseas"]})
    return out


def _tourism_block(tour: dict, sim_cats: list[str], n_total: int) -> dict:
    """Tourism inputs with per-business arrays aligned to the engine's business list."""
    kind = {"hotel": 1, "rental": 2, "camping": 3}
    cap, typ, att = [0] * n_total, [0] * n_total, [0.0] * n_total
    for i, v in tour["capacity"].items():
        cap[i], typ[i] = int(v), kind[tour["lodging_type"][i]]
    for i, v in tour["attendance_target"].items():
        att[i] = float(v)
    c = tour["categories"]
    return {"segments": tour["segments"], "party_size_mean": tour["party_size_mean"], "nights": tour["nights"],
            "lodging_share": tour["lodging_share"],
            "accommodation_spend_per_paid_person_night": tour["accommodation_spend_per_paid_person_night"],
            "categories": {"name": c["name"], "share": c["share"], "events_per_party_day": c["events_per_party_day"],
                           "served_by": [[float(sv.get(x, 0.0)) for x in sim_cats] for sv in c["served_by"]]},
            "transport_fare_share": tour["transport_fare_share"], "day_of_week": tour["day_of_week"],
            "hotel": tour.get("hotel"),
            "first_weekday": tour["first_weekday"], "capacity": cap, "lodging_type": typ, "attendance_target": att,
            "hotel_rooms_published_range": [tour["hotel_rooms"]["low"], tour["hotel_rooms"]["high"]]}


BUILDABLE = [("restaurant", "Restaurant"), ("fast_food", "Fast food"), ("cafe", "Cafe"), ("bar_pub", "Bar or pub"),
             ("grocery_food", "Grocery or convenience store"), ("apparel", "Clothing store"),
             ("general_merchandise", "General merchandise store"), ("home_electronics", "Home, hardware or electronics store"),
             ("retail_other", "Specialty shop"), ("souvenir_gift", "Souvenir and gift shop"),
             ("tobacco_cannabis", "Cannabis or tobacco shop"), ("pet", "Pet store"), ("pharmacy", "Pharmacy"),
             ("health", "Clinic or dental office"), ("personal_services", "Salon or personal services"),
             ("fuel", "Gas station"), ("auto_service", "Auto repair"), ("attraction", "Attraction"),
             ("leisure_paid", "Recreation venue"), ("entertainment", "Entertainment venue")]


def _build_menu(pack: Pack, sim_cats: list[str]) -> list[dict]:
    """Kinds of business a player can place, with the industry's typical sizes and cost shares."""
    from .businesses import CATEGORY_NAICS, DEFAULT_HOURS, FALLBACK_HOURS
    bench = {r["naics"]: r for r in pack.tables["business_revenue_benchmarks.csv"]}
    menu = []
    for cat, label in BUILDABLE:
        b = bench[CATEGORY_NAICS[cat]]
        f = lambda k: float(b[k] or 0)
        o, c = DEFAULT_HOURS.get(cat, FALLBACK_HOURS)
        menu.append({"sim_category": sim_cats.index(cat), "label": label, "naics": b["naics"], "industry": b["industry"],
                     "sizes": {"small": f("q1_avg_revenue"), "typical": f("median_cutoff"), "large": f("q3_avg_revenue")},
                     "cost_of_sales_pct": f("cost_of_sales_pct"), "net_profit_pct": f("net_profit_pct"),
                     "wages_pct": f("wages_benefits_pct") + f("labour_commissions_pct"),
                     "open": o, "close": c})
    return menu


NIAGARA_PARKS = re.compile(r"niagara parks|journey behind|butterfly conservatory|white water walk|whirlpool aero|niagara.s fury|"
                           r"floral showhouse|table rock|queen victoria place|whirlpool golf|legends on the niagara|falls incline|"
                           r"botanical gardens|elements on the falls|queenston heights|the tunnel at", re.I)
OPERATORS = ["", "niagara_parks", "casino"]
# SEED: Niagara Parks' WEGO buses (1.1M riders) and Falls Incline Railway (0.9M) are not shops on the map;
# about $16M of its $145.8M is set aside for them.
NPC_TRANSIT_ESTIMATE = 16_000_000


def operator_of(b: dict) -> int:
    """1 = run by the Niagara Parks Commission, 2 = an OLG casino, 0 = anyone else."""
    if b["sim_category"] == "casino":
        return 2
    return 1 if NIAGARA_PARKS.search(b["name"] or "") else 0


def safety_block(sim_categories: list[str]) -> dict:
    """Documented trouble spots (data/safety_hotspots.json) in the form the engine reads."""
    sd = json.loads((DATA_DIR / "safety_hotspots.json").read_text())
    keys = ("lat", "lon", "scale_m", "visitor_penalty", "resident_penalty")
    return {"hotspots": [{**{k: h[k] for k in keys}, "name": h["name"]} for h in sd["hotspots"]],
            "exempt": [sim_categories.index(c) for c in sd["exempt_categories"] if c in sim_categories],
            "scale": 1.0, "scenarios": sd["scenarios"]}


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
    # Each town becomes a road node of its own, joined to the nearest city road node by a highway
    # link. Its shops are one stand-in business there, and its residents are grouped into
    # "blocks" of 100 households who can shop at home or drive into Niagara Falls.
    town_nodes, town_edges, town_households = [], [], []
    growth = len(city["persons"]) / 94415                      # SEED: towns grew in step with Niagara Falls
    hh_size = len(city["persons"]) / len(hh)
    block = 100
    # Towns join the city where people actually drive in: a highway or main road node, not the
    # nearest side street (which sent every Thorold shopper down Taylor Road).
    main_nodes = sorted({e["from_node"] for e in edges if e["road_class"] in ("motorway", "trunk", "primary", "motorway_link", "trunk_link")})
    mask = np.full(len(nodes), np.inf)
    mask[main_nodes] = 0.0
    for t in towns["towns"]:
        jl, jo = t.get("join_lat", t["lat"]), t.get("join_lon", t["lon"])
        d = np.hypot((nlat - jl) * 111.32, (nlon - jo) * 111.32 * np.cos(np.radians(jl))) + mask
        km = float(np.hypot((t["lat"] - jl) * 111.32, (t["lon"] - jo) * 111.32 * np.cos(np.radians(jl)))) * towns["detour_factor"]
        node = len(nodes) + len(town_nodes)
        town_nodes.append((t["lat"], t["lon"]))
        # town_link_time_factor: how much longer a trip to another town feels than its driving time.
        # Fitted so that shopping trips do not exceed their plausible share of counted highway traffic.
        town_edges.append((int(d.argmin()), node, round(km, 3),
                           round(km / towns["highway_kmh"] * 60 * towns.get("town_link_time_factor", 1.0), 3)))
        ext.append({"name": t["name"] + " (out of town)", "node": node, "lat": t["lat"], "lon": t["lon"],
                    # shops inside a town are a typical local trip away: the survey's median driving trip
                    "extra_km": trips["median_driver_trip_km"],
                    "extra_min": round(trips["median_driver_trip_km"] / towns["in_town_kmh"] * 60, 2),
                    "pull_scale": round(t["population_2021"] / 94415, 4)})
        # Only part of a town's household spending is open to being won by another town; the rest is
        # bought at home whatever the alternatives. That share is fitted to the highway traffic counts.
        town_households += [node] * int(round(t["population_2021"] * growth / hh_size / block
                                              * towns.get("town_contestable_share", 1.0)))
    n_real = len(biz)
    from .calibration import GROUPS, sales_targets
    group_names = list(GROUPS)
    group_of = {n: i for i, g in enumerate(group_names) for n in GROUPS[g]}
    cal_path = DATA_DIR / "calibrated_multipliers.json"
    cal = json.loads(cal_path.read_text()) if cal_path.exists() else {}
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
        "nodes": {"lat": [n["lat"] for n in nodes] + [t[0] for t in town_nodes],
                  "lon": [n["lon"] for n in nodes] + [t[1] for t in town_nodes]},
        "edges": {"from": [e["from_node"] for e in edges] + [a for a, b, _, _ in town_edges] + [b for a, b, _, _ in town_edges],
                  "to": [e["to_node"] for e in edges] + [b for a, b, _, _ in town_edges] + [a for a, b, _, _ in town_edges],
                  "km": [round(e["length_m"] / 1000, 4) for e in edges] + [k for _, _, k, _ in town_edges] * 2,
                  "minutes": [e["minutes"] for e in edges] + [m for _, _, _, m in town_edges] * 2},
        "sim_categories": sim_cats,
        "businesses": {"node": [b["road_node"] for b in biz] + [e["node"] for e in ext],
                       "lat": [b["lat"] for b in biz] + [e["lat"] for e in ext],
                       "lon": [b["lon"] for b in biz] + [e["lon"] for e in ext],
                       "sim_category": [sim_cats.index(b["sim_category"]) for b in biz] + [len(sim_cats) - 1] * len(ext),
                       # towns keep ordinary shop hours, so early-morning trips are not pushed out of town
                       "open": [b["open_hour"] for b in biz] + [8] * len(ext),
                       "close": [b["close_hour"] for b in biz] + [22] * len(ext),
                       "core": [b["in_tourist_core"] for b in biz] + [0] * len(ext),
                       "staff": [b["staff"] for b in biz] + [0] * len(ext),
                       "revenue_prior": [b["revenue_prior"] or 0 for b in biz] + [0] * len(ext),
                       "name": [b["name"] for b in biz] + [e["name"] for e in ext],
                       "format_weight": [b["format_weight"] for b in biz] + [1.0] * len(ext),
                       "group": [group_of.get(b["naics"], len(group_names)) for b in biz] + [len(group_names)] * len(ext),
                       "address": [b.get("address", "") for b in biz] + [""] * len(ext),
                       "standin": [int(b["source"] == "synthetic") for b in biz] + [0] * len(ext),
                       "osm_id": [b.get("osm_id", "") for b in biz] + [""] * len(ext),
                       "employer": [int(b.get("employer", 0)) for b in biz] + [0] * len(ext),
                       "operator": [operator_of(b) for b in biz] + [0] * len(ext),
                       "external": [0] * n_real + [1] * len(ext),
                       "extra_km": [0.0] * n_real + [e["extra_km"] for e in ext],
                       "extra_min": [0.0] * n_real + [e["extra_min"] for e in ext],
                       "pull_scale": [0.0] * n_real + [e["pull_scale"] for e in ext]},
        "households": {"node": [h["road_node"] for h in hh] + town_households,
                       "persons5": persons5.tolist() + [int(round(block * persons5.mean()))] * len(town_households),
                       "vehicles": [h["vehicles"] for h in hh] + [2] * len(town_households),
                       # a block of town households spends like 100 average Niagara Falls households (SEED)
                       "budget": [round(float(x), 1) for x in budgets.ravel()]
                                 + [round(float(x), 1) for x in (budgets.mean(axis=0) * block)] * len(town_households),
                       "regional": [0] * len(hh) + [1] * len(town_households)},
        "categories": {
            "name": CATEGORY_NAMES,
            "typical_visit_spend": [TYPICAL_VISIT_SPEND[c] for c in CATEGORY_NAMES],
            "retail": [int(CATEGORIES[c][3]) for c in CATEGORY_NAMES],
            "season": [[1.0] * 12 if CATEGORIES[c][2] == "flat" else season[CATEGORIES[c][2]] for c in CATEGORY_NAMES],
            # every category can also be bought out of town, except casino gaming (both casinos are in the city)
            "served_by": [[(0.0 if c == "games_of_chance" else 1.0) if s == "external" else SERVED_BY[c].get(s, 0.0)
                           for s in sim_cats] for c in CATEGORY_NAMES],
            "external_scale": [cal.get("external_scale", {}).get(c, 1.0) for c in CATEGORY_NAMES],
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
    doc["business_groups"] = group_names
    ops = json.loads((DATA_DIR / "public_operators_2025.json").read_text())
    doc["operator_targets"] = {"niagara_parks": ops["niagara_parks"]["revenue_producing_operations_cad"] - NPC_TRANSIT_ESTIMATE,
                               "niagara_parks_admissions": ops["niagara_parks"]["admissions_estimate_cad"],
                               "casino": ops["casinos"]["gaming_revenue_estimate_cad"]}
    pull = cal.get("operator_pull", {})
    for i, b in enumerate(biz):
        name = OPERATORS[operator_of(b)]
        if name in pull and not (name == "niagara_parks" and b["sim_category"] == "attraction"):
            doc["businesses"]["format_weight"][i] = round(doc["businesses"]["format_weight"][i] * pull[name], 4)
    doc["calibration"] = {"sales_targets": sales_targets(pack, len(city["persons"])), "beta": cal.get("beta")}
    if city.get("tourism"):
        doc["tourism"] = _tourism_block(city["tourism"], sim_cats, n_real + len(ext))
        falls = next(x["road_node"] for x in biz if x["name"] == "Journey Behind the Falls")
        doc["tourism"]["gateways"] = _gateways(city["tourism"], nodes, edges, falls)
        tc = doc["tourism"]["categories"]
        if "attraction_visits_per_party_day" in cal and "attractions" in tc["name"]:
            tc["events_per_party_day"][tc["name"].index("attractions")] = cal["attraction_visits_per_party_day"]
        doc["tourism"]["car_share"] = city["tourism"]["car_share"]
        doc["tourism"]["categories"]["capture"] = [cal.get("tourist_capture", {}).get(c, 1.0)
                                                   for c in doc["tourism"]["categories"]["name"]]
    doc["build_menu"] = _build_menu(pack, sim_cats)
    doc["scenarios"] = json.loads((DATA_DIR / "scenario_ranges.json").read_text())
    if (DATA_DIR / "safety_hotspots.json").exists():
        doc["safety"] = safety_block(doc["sim_categories"])
    if (DATA_DIR / "new_business_closure_rates.json").exists():
        doc["new_business_closure"] = json.loads((DATA_DIR / "new_business_closure_rates.json").read_text())
    if (DATA_DIR / "forecast_error.json").exists():
        doc["forecast_error"] = json.loads((DATA_DIR / "forecast_error.json").read_text())
    if (DATA_DIR / "closure_rates_2021_2026.json").exists():
        doc["closure_rates"] = json.loads((DATA_DIR / "closure_rates_2021_2026.json").read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, separators=(",", ":")))
    return doc
