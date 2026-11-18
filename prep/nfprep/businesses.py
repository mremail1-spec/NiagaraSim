"""Phase 3a: the business list.

One record per real place: mapped places (OpenStreetMap), licensed accommodation and parking the
map misses, and named establishments from Niagara Region's Employment Inventory 2025 that the
map misses. No synthetic stand-ins: every business is a real, named place.

`revenue_prior` is a starting estimate drawn from the industry's revenue
quartiles. The engine replaces it with revenue earned from simulated spending;
the prior is used for calibration and for the break-even cost base.
"""
from __future__ import annotations

import json
import re
import math

import numpy as np

from .loader import DATA_DIR, Pack
from .population import points_in_ring

# sim_category -> NAICS group used for register counts and cost structure
CATEGORY_NAICS = {
    "restaurant": "722", "fast_food": "722", "cafe": "722", "bar_pub": "722", "nightlife": "722",
    "accommodation": "721",
    "attraction": "713", "leisure_paid": "713", "casino": "713", "entertainment": "713",
    "grocery_food": "445", "apparel": "458", "general_merchandise": "455", "home_electronics": "449",
    "retail_other": "459", "souvenir_gift": "459", "tobacco_cannabis": "459", "pet": "459", "craft": "459",
    "pharmacy": "456", "liquor": "445", "tobacconist": "459", "health": "621", "personal_services": "812", "fuel": "457",
    "auto_service": "811", "car_rental": "532", "parking": "812", "childcare": "624",
    "bank": "", "atm": "", "services": "", "office": "", "free_sight": "",
}
# Kinds of business added from the Region's Employment Inventory when the map misses them
# (NAICS prefix -> model category; 444 building and garden is filed with home_electronics, as on the map).
INVENTORY_KINDS = [("445320", None), ("445", "grocery_food"), ("455", "general_merchandise"), ("449", "home_electronics"),
                   ("444", "home_electronics"), ("456", "pharmacy"), ("6211", "health"), ("6212", "health"), ("6213", "health"),
                   ("6214", "health"), ("6215", "health"), ("8111", "auto_service"), ("5321", "car_rental"),
                   ("441", "auto_dealer"), ("812", "personal_services")]


def inventory_kind(naics: str):
    for prefix, kind in INVENTORY_KINDS:
        if naics.startswith(prefix):
            return kind
    return None
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
def nice_case(text: str) -> str:
    """'5619 STANLEY AV' -> '5619 Stanley Av'; keeps "Lundy's" and "McLeod" right."""
    out = []
    for w in text.split(" "):
        w = w.capitalize()
        if w.startswith("Mc") and len(w) > 2:
            w = "Mc" + w[2:].capitalize()
        out.append(w)
    return " ".join(out)


FORMAT_WEIGHT = {"charging_station": 0.02, "supermarket": 10, "wholesale": 10, "department_store": 6, "mall": 3, "convenience": 0.5,
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


def assign_floor_area(rows: list[dict]) -> None:
    import gzip
    from .loader import DATA_DIR
    data = json.loads(gzip.open(DATA_DIR / "city_open_data" / "building_footprints_2018.json.gz").read())["buildings"]
    blat = np.array([b["lat"] for b in data])
    blon = np.array([b["lon"] for b in data])
    home = []
    for r in rows:
        r["floor_area_m2"] = 0.0
        if r["source"] == "synthetic":
            home.append(-1)
            continue
        d = _dist_m(r["lat"], r["lon"], blat, blon)
        near = np.argsort(d)[:12]
        found = -1
        for i in near:
            if d[i] > 400:
                break
            if points_in_ring(np.array([r["lon"]]), np.array([r["lat"]]), data[i]["ring"])[0]:
                found = int(i)
                break
        if found < 0 and d[near[0]] < 25:
            found = int(near[0])
        home.append(found)
    tenants = {}
    for h in home:
        if h >= 0:
            tenants[h] = tenants.get(h, 0) + 1
    for r, h in zip(rows, home):
        if h >= 0:
            b = data[h]
            storeys = max(1, round(b["h"] / 3.5)) if b["h"] else 1
            r["floor_area_m2"] = round(b["a"] * storeys / tenants[h], 1)


EXTRA_OSM_CATEGORY = {
    "pharmacy": "pharmacy", "chemist": "pharmacy", "cosmetics": "pharmacy", "medical_supply": "pharmacy",
    "doctors": "health", "dentist": "health", "clinic": "health", "optician": "health", "optometrist": "health",
    "hearing_aids": "health", "physiotherapist": "health", "alternative": "health", "laboratory": "health",
    "hairdresser": "personal_services", "beauty": "personal_services", "massage": "personal_services",
    "tattoo": "personal_services", "nails": "personal_services",
    "car_repair": "auto_service", "tyres": "auto_service", "car_parts": "auto_service", "car_wash": "auto_service",
    "car": "auto_dealer", "motorcycle": "auto_dealer", "car_rental": "car_rental", "fuel": "fuel",
    "supermarket": "grocery_food", "convenience": "grocery_food", "greengrocer": "grocery_food", "butcher": "grocery_food",
    "bakery": "grocery_food", "deli": "grocery_food", "department_store": "general_merchandise",
    "variety_store": "general_merchandise", "hardware": "home_electronics", "doityourself": "home_electronics",
    "electronics": "home_electronics", "furniture": "home_electronics", "appliance": "home_electronics",
    "mobile_phone": "home_electronics", "garden_centre": "home_electronics", "veterinary": "pet"}
LICENCE_NAME_FITS = {"RS": ("restaurant", "fast_food", "cafe", "bar_pub", "nightlife"),
                     "FS": ("restaurant", "fast_food", "cafe", "bar_pub", "grocery_food"),
                     "TE": ("accommodation",), "BE": ("accommodation",), "VR": ("accommodation",), "OS": ("accommodation",),
                     "AM": ("attraction", "leisure_paid", "entertainment"), "PL": ("parking",),
                     "GE": ("auto_service",), "GB": ("auto_dealer",), "ES": ("personal_services",), "HA": ("personal_services",)}
TOBACCONIST_NAME = re.compile(r"cigar|tobacc|vape|vapour|smoke|hookah|shisha", re.I)
LIQUOR_NAME = re.compile(r"^(lcbo|the beer store|beer store|wine rack|wine shop|the wine shop)\b", re.I)
DESCRIBE = {"accommodation": "Lodging", "free_sight": "Viewpoint", "fuel": "Gas station", "leisure_paid": "Paid activity",
            "fast_food": "Food stand", "souvenir_gift": "Gift shop", "grocery_food": "Food shop", "restaurant": "Restaurant",
            "retail_other": "Shop", "auto_service": "Auto repair", "bar_pub": "Bar", "home_electronics": "Electronics shop",
            "personal_services": "Personal services", "liquor": "Liquor store", "tobacconist": "Tobacco shop", "cafe": "Caf\u00e9", "attraction": "Attraction", "parking": "Parking"}
PAID_VIEWPOINTS = {"Skylon Tower", "Incline Railway", "The Niagara SkyWheel", "Skywheel"}
FREE_SPOTS = {"Thompson Point", "Turtle Pond", "Balancing Rock", "Birthday Boulders", "Immaculate Boulder", "Knee Bar Boulder",
              "Locomotive Boulders", "The V Boulder", "Super Flywall", "Sydney's Dock"}
LICENCE_TYPE_CATEGORY = {"GE": "auto_service", "ES": "personal_services", "HA": "personal_services", "GB": "auto_dealer"}


def _already_listed(rows: list[dict], name: str, lat: float, lon: float, cat: str) -> bool:
    """Same kind of business within 40 m, or the same name within 150 m."""
    from .backtest import same_business
    for r in rows:
        d = _dist_m(lat, lon, np.array([r["lat"]]), np.array([r["lon"]]))[0]
        if d < 40 and r["sim_category"] == cat:
            return True
        if d < 150 and name and r["name"] and same_business(name, r["name"]):
            return True
    return False


def _licence_at(licences: list[dict], lat: float, lon: float):
    for l in licences:
        ring = l["ring"]
        if abs(ring[0][1] - lat) > 0.01 or abs(ring[0][0] - lon) > 0.01:
            continue
        if points_in_ring(np.array([lon]), np.array([lat]), ring)[0]:
            return l
    return None


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

    # Real businesses the main map extract missed: a wider OpenStreetMap pull, and the city's current
    # licences for repair shops, estheticians, hairdressers and car dealers. Each real one found here
    # replaces a stand-in below, because stand-ins only fill the gap up to the register count.
    licences = json.loads((DATA_DIR / "city_open_data" / "business_licences_current.json").read_text())["licences"]
    city_rings = [f["geometry"]["coordinates"][0] for f in pack.geo["census_tracts_boundaries.geojson"]["features"]]

    def in_city(lat_: float, lon_: float) -> bool:
        return any(points_in_ring(np.array([lon_]), np.array([lat_]), ring)[0] for ring in city_rings)

    for p in json.loads((DATA_DIR / "osm_extra_places_2026.json").read_text())["places"]:
        tag = p["amenity"] or p["shop"] or p["healthcare"] or p["craft"]
        cat = EXTRA_OSM_CATEGORY.get(tag)
        if not in_city(p["lat"], p["lon"]):
            continue                                    # the download box reaches into Niagara Falls, New York
        if cat and not _already_listed(rows, p["name"], p["lat"], p["lon"], cat):
            add("osm", p["name"], cat, p["lat"], p["lon"], osm_id=p["osm_id"], brand=p["brand"], hours=p["opening_hours"], tag=tag)
            rows[-1]["address_hint"] = p["address"]
    for l in licences:
        cat = LICENCE_TYPE_CATEGORY.get(l["type"][:2])
        if not cat:
            continue
        lat_c = sum(q[1] for q in l["ring"]) / len(l["ring"])
        lon_c = sum(q[0] for q in l["ring"]) / len(l["ring"])
        if in_city(lat_c, lon_c) and not _already_listed(rows, l["name"], lat_c, lon_c, cat):
            add("licence:" + l["type"][:2], nice_case(l["name"]), cat, lat_c, lon_c)
            rows[-1]["address_hint"] = nice_case(l["address"])

    # free sights: viewpoints and free gardens and spots on the map, plus big free parks it lacks
    free = json.loads((DATA_DIR / "free_sights.json").read_text())
    for r in rows:
        if r["sim_category"] == "attraction" and (
                (r.get("osm_tag") == "viewpoint" and r["name"] not in PAID_VIEWPOINTS)
                or r["name"] in free["pull_of_existing"] or r["name"] in FREE_SPOTS or not r["name"].strip()):
            r["sim_category"], r["naics"] = "free_sight", ""
            r["format_weight"] = float(free["pull_of_existing"].get(r["name"], 1.0))
    for c in free["add"]:
        if in_city(c["lat"], c["lon"]) and not _already_listed(rows, c["name"], c["lat"], c["lon"], "free_sight"):
            add("free_sight", c["name"], "free_sight", c["lat"], c["lon"])
            rows[-1]["format_weight"] = float(c["pull"])

    # corrections supplied by people who know the city (data/local_corrections.json)
    by_address = {r["address"].upper(): r for r in pack.tables["address_points.csv"]}
    for c in (json.loads((DATA_DIR / "local_corrections.json").read_text())["add"]
              + json.loads((DATA_DIR / "niagara_parks_places.json").read_text())["add"]):
        if "lat" in c and "lon" in c:
            la, lo = float(c["lat"]), float(c["lon"])
        elif c.get("address", "").upper() in by_address:
            a_ = by_address[c["address"].upper()]
            la, lo = float(a_["lat"]), float(a_["lon"])
        else:
            print(f"correction skipped, address not found in the city's address points: {c.get('address')}")
            continue
        add("correction", c["name"], c["category"], la, lo)
        rows[-1]["address_hint"] = nice_case(c.get("address", ""))
        rows[-1]["format_weight"] *= float(c.get("pull", 1.0))      # measured draw, e.g. Niagara Parks lot counts

    # real businesses the map misses, from Niagara Region's Employment Inventory 2025 (each one a named
    # establishment at a surveyed address). These replace the synthetic stand-ins the build used to add
    # up to the business register's counts: every business in the model is now a real, named place.
    import csv as _csv0
    inv0 = DATA_DIR / "sources" / "region" / "employment_inventory_niagara_falls_2016_2025.csv"
    if inv0.exists():
        for x in _csv0.DictReader(open(inv0, encoding="utf-8-sig")):
            if x["Year"] != "2025" or x["sizerangeemployees"].startswith("Permanently") or not x["lat"]:
                continue
            kind = inventory_kind(x["primarynaics"])
            if not kind:
                continue
            la, lo = float(x["lat"]), float(x["lon"])
            name = x["businessname"].strip()
            if in_city(la, lo) and not _already_listed(rows, name, la, lo, kind):
                add("inventory", name, kind, la, lo)
                rows[-1]["address_hint"] = f'{x["businessstreetnumber"]} {x["businessstreetname"].strip()}'.strip()
                rows[-1]["inventory_size"] = x["sizerangeemployees"]

    # civic address: the licence's own address, else the nearest city address point within 80 m
    pts = pack.tables["address_points.csv"]
    alat = np.array([float(r["lat"]) for r in pts])
    alon = np.array([float(r["lon"]) for r in pts])
    licence_address = {(l["business"], l["lat"]): l["address"] for l in pack.tables["licensed_tourism_businesses.csv"]}
    osm_street = {r["osm_id"]: r["street"] for r in pack.tables["places.csv"] if r.get("street")}
    for r in rows:
        r["address"], r["address_source"], r["name_source"] = "", "", ""
        r.setdefault("address_hint", "")
        if r["source"] == "synthetic":
            continue
        if r.get("address_hint"):
            r["address"], r["address_source"] = r["address_hint"], "source record"
            continue
        if osm_street.get(r["osm_id"], "")[:1].isdigit():
            r["address"], r["address_source"] = osm_street[r["osm_id"]], "OpenStreetMap"
            continue
        # the city licence whose parcel the place sits in
        lic = _licence_at(licences, r["lat"], r["lon"])
        if lic:
            r["address"], r["address_source"] = nice_case(lic["address"]), "city licence parcel"
            # borrow the licence's name only when the licence is for this kind of business
            if not r["name"].strip() and r["sim_category"] in LICENCE_NAME_FITS.get(lic["type"][:2], ()):
                r["name"], r["name_source"] = nice_case(lic["name"]), "city licence parcel"
            continue
        d = _dist_m(r["lat"], r["lon"], alat, alon)
        i = int(d.argmin())
        if d[i] <= 150:
            r["address"], r["address_source"] = nice_case(pts[i]["address"]), "nearest civic address (%d m)" % round(d[i])
    for l in pack.tables["licensed_tourism_businesses.csv"]:
        for r in rows:
            if r["source"].startswith("licence") and r["name"] == l["business"] and l["lat"] and abs(r["lat"] - float(l["lat"])) < 1e-6:
                r["address"], r["address_source"] = nice_case(l["address"]), "city licence"

    # cannabis stores the map misses: Niagara Region's Employment Inventory 2025 (NAICS 459993)
    import csv as _csv
    inv = DATA_DIR / "sources" / "region" / "employment_inventory_niagara_falls_2016_2025.csv"
    if inv.exists():
        for x in _csv.DictReader(open(inv, encoding="utf-8-sig")):
            if x["Year"] != "2025" or x["sizerangeemployees"].startswith("Permanently") or not x["lat"]:
                continue
            if not (x["primarynaics"].startswith("459993") or re.search(r"cannabis", x["businessname"], re.I)):
                continue
            la, lo = float(x["lat"]), float(x["lon"])
            if in_city(la, lo) and not _already_listed(rows, x["businessname"].strip(), la, lo, "tobacco_cannabis"):
                add("inventory", x["businessname"].strip(), "tobacco_cannabis", la, lo)
                rows[-1]["address"] = f'{x["businessstreetnumber"]} {x["businessstreetname"].strip()}'.strip()
                rows[-1]["address_source"] = "Niagara Region Employment Inventory 2025"

    # cigar, tobacco, vape and smoke shops: their own kind, selling tobacco but not cannabis
    for r in rows:
        if r["sim_category"] == "tobacco_cannabis" and TOBACCONIST_NAME.search(r["name"] or "") and not re.search(r"cannabis|weed|bud", r["name"] or "", re.I):
            r["sim_category"] = "tobacconist"

    # liquor stores (LCBO, The Beer Store, Wine Rack, wine shops): their own kind, serving alcohol
    # bought from stores, which in Ontario is mostly sold there (not by grocery or cannabis shops)
    for r in rows:
        if r["sim_category"] in ("grocery_food", "retail_other", "tobacco_cannabis") and (
                r.get("osm_tag") in ("alcohol", "wine", "beverages") or LIQUOR_NAME.search(r["name"] or "")):
            r["sim_category"], r["naics"] = "liquor", "445"

    # places the map lists without a name: a plain description, so the page never shows a blank
    for r in rows:
        if r["source"] != "synthetic" and not r["name"].strip():
            what = DESCRIBE.get(r["sim_category"], r["sim_category"].replace("_", " ").capitalize())
            street = re.sub(r"^[\d\-]+\s*", "", r.get("address") or "").strip()
            r["name"] = f"{what} on {street}" if street else what
            r["name_source"] = "description (no name on the map)"

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

    # floor area from the city's 2018 building footprints: the building a business sits in (or the
    # nearest within 25 m), shared equally among the businesses in it, times its storeys (3.5 m each)
    assign_floor_area(rows)

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
                               # bigger premises, bigger business: weight 1.5 per tenfold floor area
                               + (1.5 * math.log10(r["floor_area_m2"] / 300) if r["floor_area_m2"] else 0)
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
