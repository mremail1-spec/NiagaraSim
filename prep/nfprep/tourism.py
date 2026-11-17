"""Phase 5 (prep side): how many visitors come, when, where they stay and what they spend.

Regional figures are scaled to the City of Niagara Falls with the shares in
`tourism_city_share.json`. Everything marked SEED is a prior, not a sourced number.
"""
from __future__ import annotations

import datetime
import json

from .loader import DATA_DIR, Pack

# Niagara Parks attractions with published attendance (fiscal 2024/25) -> name on the map
PARKS_ATTRACTIONS = {"journey_behind_the_falls": "Journey Behind the Falls",
                     "power_station": "Niagara Parks Power Station",
                     "butterfly_conservatory": "Butterfly Conservatory",
                     "white_water_walk": "White Water Walk",
                     "whirlpool_aero_car": "Whirlpool Aero Car",
                     "niagaras_fury": "Niagara's Fury",
                     "floral_showhouse": "Floral Showhouse"}
# Tourist spending category -> (share key(s) in the travel survey's spending mix, businesses that serve it)
TOURIST_CATEGORIES = {
    "restaurants": (["restaurants"], {"restaurant": 1, "fast_food": 1, "cafe": 1, "bar_pub": 1, "nightlife": 1}),
    "food_from_stores": (["food_from_stores"], {"grocery_food": 1}),
    "fuel": (["vehicle_operation"], {"fuel": 1}),
    "parking": (["vehicle_operation"], {"parking": 1}),
    "vehicle_rental": (["vehicle_rental"], {"car_rental": 1}),
    "recreation": (["recreation"], {"leisure_paid": 1, "casino": 1, "entertainment": 1}),
    "attractions": (["culture_attractions"], {"attraction": 1}),
    # SEED: visitors rarely shop at warehouse and department stores, so those get a small weight
    "clothing": (["clothing"], {"apparel": 1, "general_merchandise": 0.05}),
    "other_retail": (["other_retail"], {"souvenir_gift": 1, "retail_other": 0.5, "general_merchandise": 0.05,
                                         "tobacco_cannabis": 0.5, "pharmacy": 0.3}),
}
VEHICLE_OPERATION_SPLIT = {"fuel": 0.7, "parking": 0.3}      # SEED: fuel vs parking within "vehicle operation"
# SEED: purchases per party per day in each category (sets how many stops a party makes, not what it spends)
EVENTS_PER_PARTY_DAY = {"restaurants": 2.0, "food_from_stores": 0.3, "fuel": 0.3, "parking": 0.7,
                        "vehicle_rental": 0.03, "recreation": 0.4, "attractions": 1.0, "clothing": 0.3,
                        "other_retail": 0.6}
# Where visitors' cars enter the city. SEED: the shares; the entry points are real.
GATEWAYS = [
    {"name": "QEW from Toronto and Hamilton", "lat": 43.15212, "lon": -79.150513, "domestic": 0.90, "us": 0.0, "overseas": 1.0},
    {"name": "QEW from Fort Erie and the Peace Bridge", "lat": 42.99002, "lon": -79.057051, "domestic": 0.10, "us": 0.25, "overseas": 0.0},
    {"name": "Rainbow Bridge", "lat": 43.0902, "lon": -79.0679, "domestic": 0.0, "us": 0.40, "overseas": 0.0},
    {"name": "Queenston-Lewiston Bridge (Highway 405)", "lat": 43.15643, "lon": -79.052097, "domestic": 0.0, "us": 0.25, "overseas": 0.0},
    {"name": "Whirlpool Bridge", "lat": 43.1090, "lon": -79.0592, "domestic": 0.0, "us": 0.10, "overseas": 0.0},
]
OVERSEAS_CAR_SHARE = 0.5                                      # SEED: the rest arrive by coach or train
DAY_OF_WEEK = [0.80, 0.80, 0.80, 0.85, 1.10, 1.50, 1.15]     # SEED: Monday..Sunday arrivals, mean 1.0
OVERSEAS_OVERNIGHT_SHARE = 0.6                               # SEED
HOTEL_ROOMS = {"low": 12000, "central": 13000, "high": 14000,
               "source": "Niagara Falls Tourism ('over 12,000 guestrooms'); Cvent destination listing (14,000 rooms, 55 hotels)"}
NIGHTS = {1: 1516021, 2: 906486, 3: 314480, 4: 117540, 5: 20462, 7: 52646, 12: 27140}   # 6-9 -> 7, 10+ -> 12


def _monthly_from_quarters(quarters: dict, shape: list[float]) -> list[float]:
    """Monthly shares that add to the published quarter totals, shaped inside each quarter by `shape`."""
    out = []
    for q, key in enumerate(("Q1", "Q2", "Q3", "Q4")):
        part = shape[q * 3:q * 3 + 3]
        out += [quarters[key] / 100 * p / sum(part) for p in part]
    return out


def build_tourism(pack: Pack, businesses: list[dict], year: int) -> dict:
    t = pack.params["sim_parameters.json"]["tourism"]
    b = pack.params["behaviour_parameters.json"]["tourists"]
    share = json.loads((DATA_DIR / "tourism_city_share.json").read_text())
    idx = pack.params["earnings_index_ontario.json"]["avg_weekly_earnings_cad"]
    region = t["annual_visitors_niagara_region"]
    us_trend = b["us_trend"]["all_us_into_ontario"]
    totals = b["us_arrivals_ontario_totals"]
    curves = b["us_arrivals_ontario_monthly_pct_2025"]

    regional_total = 13_000_000                              # "13 million+" (Ontario government, 2025)
    us = region["2017_detail"]["usa"] * us_trend["2025"] / us_trend["2019"]
    overseas = region["2017_detail"]["overseas"]
    domestic = regional_total - us - overseas
    dom22 = region["2022_domestic_only"]
    night_share = {"domestic": dom22["overnight"] / dom22["person_visits"],
                   "us": totals["2025_tourists_automobile"] / (totals["2025_tourists_automobile"] + totals["2025_excursionists_automobile"]),
                   "overseas": OVERSEAS_OVERNIGHT_SHARE}
    visitors_share = share["city_share_of_regional_visitors"]
    spend_per_visitor_factor = share["city_share_of_regional_tourism_spending"]["central"] / visitors_share
    avg_nights = sum(k * v for k, v in NIGHTS.items()) / sum(NIGHTS.values())

    # --- spend per person (regional dollars of the simulated year) ---
    sp = t["spending"]
    infl = idx[str(year)] / idx["2022"]
    dom_day = sp["domestic_2022_per_person"]["same_day"] * infl
    dom_night = sp["domestic_2022_per_person"]["overnight_per_night"] * infl
    ratio = dom_night / dom_day
    intl = sp["international_niagara_falls_wine_country_by_year"]
    spend = {"domestic": (dom_day, dom_night)}
    for origin, persons, total in (("us", us, intl["usa_2025"]), ("overseas", overseas, intl["2025"] - intl["usa_2025"])):
        days = persons * (1 - night_share[origin]) + persons * night_share[origin] * avg_nights * ratio
        spend[origin] = (total / days, total / days * ratio)   # same night-to-day ratio as domestic visitors

    quarters = t["seasonality_2022_domestic_visits_pct"]
    month_curve = {
        ("domestic", 0): _monthly_from_quarters(quarters, curves["us_same_day"]),
        ("domestic", 1): _monthly_from_quarters(quarters, curves["us_overnight"]),
        ("us", 0): [x / 100 for x in curves["us_same_day"]],
        ("us", 1): [x / 100 for x in curves["us_by_car_overnight"]],
        ("overseas", 0): [x / 100 for x in curves["us_overnight"]],
        ("overseas", 1): [x / 100 for x in curves["us_overnight"]],
    }
    segments = []
    for origin, persons in (("domestic", domestic), ("us", us), ("overseas", overseas)):
        for overnight in (0, 1):
            frac = night_share[origin] if overnight else 1 - night_share[origin]
            m = month_curve[(origin, overnight)]
            segments.append({"name": f"{origin}_{'overnight' if overnight else 'same_day'}", "origin": origin,
                             "overnight": overnight,
                             "persons_per_year": round(persons * frac * visitors_share),
                             "month_share": [round(x / sum(m), 5) for x in m],
                             "spend_per_person_day": round(spend[origin][overnight] * spend_per_visitor_factor, 2)})

    # --- accommodation: who pays for a room, and how much ---
    acc = t["accommodation_2022_domestic_overnight_visitors"]
    acc_total = sum(acc.values())
    lodging_share = {"hotel": (acc["hotels"] + acc["motels"]) / acc_total,
                     "rental": acc["other_roofed_commercial"] / acc_total,
                     "camping": acc["camping_rv"] / acc_total,
                     "private": (acc["private_homes_friends"] + acc["other"]) / acc_total}
    mix = sp["domestic_2022_mix_pct"]
    total_spend = sum(s["persons_per_year"] * s["spend_per_person_day"] * (avg_nights if s["overnight"] else 1) for s in segments)
    night_persons = sum(s["persons_per_year"] * avg_nights for s in segments if s["overnight"])
    acc_per_night_avg = mix["accommodation"] / 100 * total_spend / night_persons          # averaged over all overnight visitors
    acc_per_paid_night = acc_per_night_avg / (1 - lodging_share["private"])              # paid by those not staying with friends
    rest = {k: v for k, v in mix.items() if k != "accommodation"}
    cats, served, shares, events = [], [], [], []
    for name, (keys, serves) in TOURIST_CATEGORIES.items():
        s = sum(rest[k] for k in keys) * VEHICLE_OPERATION_SPLIT.get(name, 1.0)
        cats.append(name)
        served.append(serves)
        shares.append(s / sum(rest.values()))
        events.append(EVENTS_PER_PARTY_DAY[name])
    transport_share = (rest["public_transport"] + rest["local_transport"]) / sum(rest.values())

    # --- rooms: the city total spread over mapped hotels and motels by staff, known counts kept ---
    hotels = [x for x in businesses if x["sim_category"] == "accommodation" and x["source"] == "osm"]
    weight = {x["business_id"]: max(x["staff"], 2) for x in hotels}
    known = {x["business_id"]: int(float(x["rooms"])) for x in hotels if x["rooms"]}
    market = json.loads((DATA_DIR / "hotel_market_2025.json").read_text())
    spread = market["hotel_rooms"] - sum(known.values())
    wsum = sum(w for i, w in weight.items() if i not in known)
    capacity, lodging_type = {}, {}
    for x in businesses:
        if x["sim_category"] != "accommodation":
            continue
        i = x["business_id"]
        if x["source"] == "osm":
            capacity[i] = known.get(i) or max(4, round(spread * weight[i] / wsum))
            lodging_type[i] = "hotel"
        elif x["source"].endswith("campground"):
            capacity[i], lodging_type[i] = 150, "camping"                                  # SEED: sites per campground
        else:
            capacity[i], lodging_type[i] = (3 if x["source"].endswith("bed_and_breakfast") else 1), "rental"

    att = t["attractions_attendance_niagara_parks_fy2024_25"]
    attendance = {}
    for key, name in PARKS_ATTRACTIONS.items():
        match = [x["business_id"] for x in businesses
                 if x["name"].replace("\u2019", "'") == name and x["sim_category"] in ("attraction", "leisure_paid", "entertainment")]
        if match:
            attendance[match[0]] = att[key]

    car_share = {"domestic": t["arrival_modes"]["ontario_domestic_visits_by_car_2024_pct"] / 100,
                 "us": (totals["2025_tourists_automobile"] + totals["2025_excursionists_automobile"]) / totals["2025_travellers_all"],
                 "overseas": OVERSEAS_CAR_SHARE}
    first = datetime.date(year, 1, 1).weekday()
    return {
        "segments": segments,
        "party_size_mean": t["party"]["avg_party_size"],
        "nights": {"n": list(NIGHTS), "weight": list(NIGHTS.values())},
        "lodging_share": lodging_share,
        "accommodation_spend_per_paid_person_night": round(acc_per_paid_night * 1.0, 2),
        "categories": {"name": cats, "share": [round(x, 5) for x in shares], "events_per_party_day": events,
                       "served_by": served},
        "transport_fare_share": round(transport_share, 5),
        "day_of_week": DAY_OF_WEEK, "first_weekday": first,
        "gateways": GATEWAYS, "car_share": car_share,
        "capacity": capacity, "lodging_type": lodging_type, "attendance_target": attendance,
        "hotel": (json.loads((DATA_DIR / "calibrated_hotels.json").read_text())["hotel"]
                  if (DATA_DIR / "calibrated_hotels.json").exists() else None),
        "hotel_rooms": HOTEL_ROOMS,
        "checks": {"regional_visitors": round(domestic + us + overseas), "regional_domestic": round(domestic),
                   "regional_us": round(us), "regional_overseas": round(overseas),
                   "city_visitors": sum(s["persons_per_year"] for s in segments),
                   "city_tourism_spending_musd": round(total_spend / 1e6, 1),
                   "regional_tourism_spending_musd": round(total_spend / share["city_share_of_regional_tourism_spending"]["central"] / 1e6, 1),
                   "avg_nights": round(avg_nights, 3),
                   "implied_room_rate_per_night": round(acc_per_paid_night * t["party"]["avg_party_size"], 2),
                   "published_room_rate_2022": t["hotel_market_region"]["adr"]["2022"]},
    }
