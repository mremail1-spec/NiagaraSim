"""Research top-up for the Niagara Falls data pack (2026-11-15).

Builds five new files in the data pack from the existing census files plus
primary sources downloaded into a raw folder:

  tract_demographics.csv      who lives where (age x sex, households, dwellings, income bands)
  tract_distances.csv         centroid-to-centroid km between the 21 tracts
  places_tract_lookup.csv     which census tract each of the 1,128 places sits in
  tract_place_access.csv      per tract x category: places inside the tract, km to the nearest
  energy_parameters.json      NPEI / Enbridge / city water rates, heating fuel, monthly shapes
  spending_by_demographic.json  who buys what: spending by age, household type, income quintile

Usage: python3 -I build_topup.py <pack_dir> <raw_dir>
"""
import csv
import json
import math
import sys
from collections import defaultdict

PACK, RAW = sys.argv[1], sys.argv[2]


# ----------------------------------------------------------------------------
# 1. Tract demographics from census_tract_long.csv
# ----------------------------------------------------------------------------
AGE_BANDS = [
    ("0_4", "POP_0_4_YRS_10"), ("5_9", "POP_5_9_YRS_11"), ("10_14", "POP_10_14_YRS_12"),
    ("15_19", "POP_15_19_YRS_14"), ("20_24", "POP_20_24_YRS_15"), ("25_29", "POP_25_29_YRS_16"),
    ("30_34", "POP_30_34_YRS_17"), ("35_39", "POP_35_39_YRS_18"), ("40_44", "POP_40_44_YRS_19"),
    ("45_49", "POP_45_49_YRS_20"), ("50_54", "POP_50_54_YRS_21"), ("55_59", "POP_55_59_YRS_22"),
    ("60_64", "POP_60_64_YRS_23"), ("65_69", "POP_65_69_YRS_25"), ("70_74", "POP_70_74_YRS_26"),
    ("75_79", "POP_75_79_YRS_27"), ("80_84", "POP_80_84_YRS_28"), ("85_plus", "POP_85_YRS_OVER_29"),
]
# output column -> census field code (all "TOT_" fields)
HH_FIELDS = [
    ("population_2021", "TOT_POP_2021_1"),
    ("median_age", "TOT_MED_AGE_POP_40"),
    ("households", "TOT_TOT_HH_TYPE_100_100"),
    ("hh_1_person", "TOT_F1_PERSON_51"), ("hh_2_persons", "TOT_F2_PRSNS_52"),
    ("hh_3_persons", "TOT_F3_PRSNS_53"), ("hh_4_persons", "TOT_F4_PRSNS_54"),
    ("hh_5plus_persons", "TOT_F5_MR_PRSNS_55"),
    ("hh_couple_with_children", "TOT_W_CH_103"), ("hh_couple_no_children", "TOT_WOUT_CH_104"),
    ("hh_one_parent", "TOT_ONE_PARENT_FMLY_HH_105"), ("hh_multigenerational", "TOT_MULTIGENAL_HH_106"),
    ("hh_multiple_family", "TOT_MULT_CS_FMLY_HH_107"),
    ("hh_one_family_plus_others", "TOT_ONE_CS_FMLY_HH_ADDITIO_108"),
    ("hh_non_family_2plus", "TOT_TWO_MR_PERSON_NON_CS_F_109"), ("hh_one_person", "TOT_ONE_PERSON_HH_110"),
    ("maintainer_15_24", "TOT_F15_24_YRS_1457"), ("maintainer_25_34", "TOT_F25_34_YRS_1458"),
    ("maintainer_35_44", "TOT_F35_44_YRS_1459"), ("maintainer_45_54", "TOT_F45_54_YRS_1460"),
    ("maintainer_55_64", "TOT_F55_64_YRS_1461"), ("maintainer_65_74", "TOT_F65_74_YRS_1462"),
    ("maintainer_75_84", "TOT_F75_84_YRS_1463"), ("maintainer_85_plus", "TOT_F85_YRS_OVER_1464"),
    ("owner_hh", "TOT_OWN_1415"), ("renter_hh", "TOT_RENTER_1416"),
    ("dw_single_detached", "TOT_SINGLE_DETACHED_HOUSE_42"), ("dw_semi_detached", "TOT_SEMI_DETACHED_HOUSE_43"),
    ("dw_row_house", "TOT_ROW_HOUSE_44"), ("dw_duplex_apartment", "TOT_APT_FLAT_DUPLEX_45"),
    ("dw_apartment_under_5_storeys", "TOT_APT_BUILDING_THAT_HAS_46"),
    ("dw_apartment_5plus_storeys", "TOT_APT_BUILDING_THAT_HAS_47"),
    ("dw_other_attached", "TOT_OTH_SINGLE_ATTACHED_HO_48"), ("dw_movable", "TOT_MOVABLE_DWL_49"),
    ("built_1960_or_before", "TOT_F1960_BEFORE_1441"), ("built_1961_1980", "TOT_F1961_1980_1442"),
    ("built_1981_1990", "TOT_F1981_1990_1443"), ("built_1991_2000", "TOT_F1991_2000_1444"),
    ("built_2001_2005", "TOT_F2001_2005_1445"), ("built_2006_2010", "TOT_F2006_2010_1446"),
    ("built_2011_2015", "TOT_F2011_2015_1447"), ("built_2016_2021", "TOT_F2016_2021_1448"),
    ("bedrooms_0", "TOT_NO_BR_1422"), ("bedrooms_1", "TOT_F1_BR_1423"), ("bedrooms_2", "TOT_F2_BR_1424"),
    ("bedrooms_3", "TOT_F3_BR_1425"), ("bedrooms_4plus", "TOT_F4_MR_BR_1426"),
    ("avg_rooms", "TOT_AVG_NBR_RMS_PER_DWL_1433"),
    ("inc_under_5k", "TOT_UNDER_5000_261"), ("inc_5k_10k", "TOT_F5000_9999_262"),
    ("inc_10k_15k", "TOT_F10000_14999_263"), ("inc_15k_20k", "TOT_F15000_19999_264"),
    ("inc_20k_25k", "TOT_F20000_24999_265"), ("inc_25k_30k", "TOT_F25000_29999_266"),
    ("inc_30k_35k", "TOT_F30000_34999_267"), ("inc_35k_40k", "TOT_F35000_39999_268"),
    ("inc_40k_45k", "TOT_F40000_44999_269"), ("inc_45k_50k", "TOT_F45000_49999_270"),
    ("inc_50k_60k", "TOT_F50000_59999_271"), ("inc_60k_70k", "TOT_F60000_69999_272"),
    ("inc_70k_80k", "TOT_F70000_79999_273"), ("inc_80k_90k", "TOT_F80000_89999_274"),
    ("inc_90k_100k", "TOT_F90000_99999_275"), ("inc_100k_125k", "TOT_F100000_124999_277"),
    ("inc_125k_150k", "TOT_F125000_149999_278"), ("inc_150k_200k", "TOT_TOT_HH_TOT_INC_GRPS_20_279"),
    ("inc_200k_plus", "TOT_TOT_HH_TOT_INC_GRPS_20_280"),
    ("median_owner_shelter_cost_mo", "TOT_MED_MTHLY_SHR_OWNED_DW_1486"),
    ("median_rent_shelter_cost_mo", "TOT_MED_MTHLY_SHR_RENTED_D_1494"),
]

census = defaultdict(dict)  # tract -> field -> value
with open(f"{PACK}/census_tract_long.csv", encoding="utf-8") as f:
    for r in csv.DictReader(f):
        census[r["census_tract"]][r["field"]] = r["value"]


def num(v):
    try:
        x = float(v)
        return int(x) if x == int(x) else x
    except (TypeError, ValueError):
        return ""


profiles = {r["census_tract"]: r for r in csv.DictReader(open(f"{PACK}/tract_profiles.csv", encoding="utf-8"))}
tracts = sorted(profiles)
demo_rows = []
for t in tracts:
    c = census[t]
    row = {"census_tract": t, "community": profiles[t]["community"],
           "lat": profiles[t]["lat"], "lon": profiles[t]["lon"]}
    for col, field in HH_FIELDS[:2]:
        row[col] = num(c.get(field))
    for sex, prefix in (("male", "MA_"), ("female", "FE_")):
        row[f"{sex}_total"] = num(c.get(prefix + "TOT_AGE_GRPS_POP_100_8"))
        for band, code in AGE_BANDS:
            row[f"{sex}_{band}"] = num(c.get(prefix + code))
    # convenience groups (census counts are randomly rounded to 5, so sums can differ by a few)
    tot = lambda codes: sum(num(c.get("TOT_" + x)) or 0 for x in codes)
    row["children_0_14"] = num(c.get("TOT_POP_0_14_YRS_9"))
    row["youth_15_24"] = tot(["POP_15_19_YRS_14", "POP_20_24_YRS_15"])
    row["adults_25_64"] = tot([code for b, code in AGE_BANDS[5:13]])
    row["seniors_65_plus"] = num(c.get("TOT_POP_65_YRS_OVER_24"))
    row["seniors_85_plus"] = num(c.get("TOT_POP_85_YRS_OVER_29"))
    for col, field in HH_FIELDS[2:]:
        row[col] = num(c.get(field))
    demo_rows.append(row)

with open(f"{PACK}/tract_demographics.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(demo_rows[0]))
    w.writeheader()
    w.writerows(demo_rows)


# ----------------------------------------------------------------------------
# 2. Geography: tract distances, place -> tract lookup, access by category
# ----------------------------------------------------------------------------
def km(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in km. Straight line, not road distance."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def in_ring(x, y, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def in_polygon(x, y, geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        if in_ring(x, y, poly[0]) and not any(in_ring(x, y, hole) for hole in poly[1:]):
            return True
    return False


cent = {t: (float(profiles[t]["lat"]), float(profiles[t]["lon"])) for t in tracts}
with open(f"{PACK}/tract_distances.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["from_tract", "to_tract", "km_straight_line"])
    for a in tracts:
        for b in tracts:
            w.writerow([a, b, round(km(*cent[a], *cent[b]), 3)])

polys = {ft["properties"]["census_tract"]: ft["geometry"]
         for ft in json.load(open(f"{PACK}/census_tracts_boundaries.geojson"))["features"]}
places = list(csv.DictReader(open(f"{PACK}/places.csv", encoding="utf-8")))
lookup = []
for p in places:
    la, lo = float(p["lat"]), float(p["lon"])
    tract = next((t for t in tracts if in_polygon(lo, la, polys[t])), None)
    method = "inside_polygon"
    if tract is None:  # on a boundary / just outside the tract layer: use nearest centroid
        tract = min(tracts, key=lambda t: km(la, lo, *cent[t]))
        method = "nearest_centroid"
    lookup.append({"osm_id": p["osm_id"], "name": p["name"], "sim_category": p["sim_category"],
                   "in_tourist_core": p["in_tourist_core"], "lat": p["lat"], "lon": p["lon"],
                   "census_tract": tract, "tract_match": method,
                   "km_to_tract_centroid": round(km(la, lo, *cent[tract]), 3)})
with open(f"{PACK}/places_tract_lookup.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(lookup[0]))
    w.writeheader()
    w.writerows(lookup)

cats = sorted({p["sim_category"] for p in places})
with open(f"{PACK}/tract_place_access.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["census_tract", "sim_category", "places_in_tract", "places_within_2km_of_centroid",
                "places_within_5km_of_centroid", "km_to_nearest_from_centroid"])
    for t in tracts:
        for cat in cats:
            d = [km(*cent[t], float(p["lat"]), float(p["lon"])) for p in places if p["sim_category"] == cat]
            n_in = sum(1 for r in lookup if r["census_tract"] == t and r["sim_category"] == cat)
            w.writerow([t, cat, n_in, sum(x <= 2 for x in d), sum(x <= 5 for x in d), round(min(d), 3)])


# ----------------------------------------------------------------------------
# 3. Energy parameters
# ----------------------------------------------------------------------------
DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
normals = json.load(open(f"{RAW}/normals_6135657.json"))
HDD = [normals["Total degree-days below 18 deg C"][str(m)] for m in range(1, 13)]
CDD = [normals["Total degree-days Above 18 deg C"][str(m)] for m in range(1, 13)]
TEMP = [normals["Mean daily temperature deg C"][str(m)] for m in range(1, 13)]
share = lambda xs: [x / sum(xs) for x in xs]
hdd_s, cdd_s, day_s = share(HDD), share(CDD), share(DAYS)

# NRCan Comprehensive Energy Use Database, Ontario residential, 2022 (PJ)
NR = {"space_heating": {"electricity": 52.3, "natural_gas": 236.8, "heating_oil": 7.8, "other": 10.2, "wood": 18.3},
      "water_heating": {"electricity": 9.7, "natural_gas": 83.2, "heating_oil": 0.4, "other": 0.3, "wood": 0.1},
      "appliances": {"electricity": 53.5, "natural_gas": 4.2},
      "lighting": {"electricity": 24.3}, "space_cooling": {"electricity": 18.3}}
HH_K = {"all": 5810.9, "single_detached": 3225.3, "single_attached": 930.9, "apartments": 1640.8}
HH_BY_SOURCE_K = {"electricity": 1472.1, "natural_gas": 3934.6, "heating_oil": 103.6, "other": 244.8, "wood": 55.8}
SPACE_BY_TYPE = {"single_detached": 238.4, "single_attached": 43.3, "apartments": 41.9}
WATER_BY_TYPE = {"single_detached": 57.1, "single_attached": 15.0, "apartments": 21.4}

gas_total = NR["space_heating"]["natural_gas"] + NR["water_heating"]["natural_gas"] + NR["appliances"]["natural_gas"]
gas_heat_share = NR["space_heating"]["natural_gas"] / gas_total
elec = {k: v["electricity"] for k, v in NR.items()}
elec_nonheat = sum(v for k, v in elec.items() if k != "space_heating")
GJ_PER_KWH = 0.0036
kwh_nonheat_per_hh = elec_nonheat * 1e6 / (HH_K["all"] * 1e3) / GJ_PER_KWH
cool_share_of_nonheat = elec["space_cooling"] / elec_nonheat
kwh_heat_per_elec_hh = NR["space_heating"]["electricity"] * 1e6 / (HH_BY_SOURCE_K["electricity"] * 1e3) / GJ_PER_KWH

gas_month_share = [(1 - gas_heat_share) * d + gas_heat_share * h for d, h in zip(day_s, hdd_s)]
elec_month_share_gas_home = [(1 - cool_share_of_nonheat) * d + cool_share_of_nonheat * c for d, c in zip(day_s, cdd_s)]

# --- rates ---
GAS = {"customer_charge_month": 27.69,
       "delivery_tiers_cents_per_m3": [[30, 14.2691], [55, 13.4308], [85, 12.7744], [None, 12.2850]],
       "facility_carbon_cents_per_m3": 0.0145, "transportation_cents_per_m3": 5.4818,
       "gas_supply_commodity_cents_per_m3": 9.4741, "cost_adjustment_cents_per_m3": 0.5322,
       "typical_residential_m3_per_year": 2400}
ELEC = {"service_charge_month": 42.10, "smart_meter_entity_month": 0.42, "sss_admin_month": 0.25,
        "low_voltage_per_kwh": 0.0021, "dva_rider_2026_per_kwh": 0.0006,
        "transmission_network_per_kwh": 0.0130, "transmission_connection_per_kwh": 0.0078,
        "wholesale_market_service_per_kwh": 0.0041, "capacity_based_recovery_per_kwh": 0.0006,
        "rural_remote_protection_per_kwh": 0.0006, "total_loss_factor": 1.0423,
        "reference_residential_kwh_per_month": 750}
RPP = {"effective": "2026-11-01",
       "tou_cents_per_kwh": {"off_peak": 9.1, "mid_peak": 14.6, "on_peak": 18.9},
       "ulo_cents_per_kwh": {"ultra_low_overnight": 3.7, "weekend_off_peak": 9.1, "mid_peak": 14.6, "on_peak": 37.7},
       "tiered_cents_per_kwh": {"tier_1": 11.2, "tier_2": 13.2,
                                "tier_1_threshold_kwh_winter_nov_apr": 1000, "tier_1_threshold_kwh_summer_may_oct": 600},
       "previous_nov_2025_to_oct_2026": {"tou": {"off_peak": 9.8, "mid_peak": 15.7, "on_peak": 20.3},
                                         "tiered": {"tier_1": 12.0, "tier_2": 14.2}}}
WATER = {"effective": "2026-01-01", "water_per_m3": 1.490, "wastewater_per_m3": 2.010, "combined_per_m3": 3.500,
         "fixed_month_5_8_inch_meter": {"water": 25.61, "wastewater": 33.76, "total": 59.37},
         "city_average_bill_two_months": 224, "city_average_use_m3_two_months": 30}
HST, OER = 0.13, 0.157


def gas_bill(m3):
    left, delivery = m3, 0.0
    for width, rate in GAS["delivery_tiers_cents_per_m3"]:
        use = left if width is None else min(left, width)
        delivery += use * rate / 100
        left -= use
    per_m3 = (GAS["facility_carbon_cents_per_m3"] + GAS["transportation_cents_per_m3"]
              + GAS["gas_supply_commodity_cents_per_m3"] + GAS["cost_adjustment_cents_per_m3"]) / 100
    return (GAS["customer_charge_month"] + delivery + m3 * per_m3) * (1 + HST)


def elec_bill(kwh, month):  # month 1-12; Tiered plan (needs no time-of-day split)
    t = RPP["tiered_cents_per_kwh"]
    thr = t["tier_1_threshold_kwh_summer_may_oct"] if 5 <= month <= 10 else t["tier_1_threshold_kwh_winter_nov_apr"]
    adj = kwh * ELEC["total_loss_factor"]
    commodity = (min(adj, thr) * t["tier_1"] + max(adj - thr, 0) * t["tier_2"]) / 100
    delivery = (ELEC["service_charge_month"] + ELEC["smart_meter_entity_month"]
                + kwh * (ELEC["low_voltage_per_kwh"] + ELEC["dva_rider_2026_per_kwh"])
                + adj * (ELEC["transmission_network_per_kwh"] + ELEC["transmission_connection_per_kwh"]))
    regulatory = ELEC["sss_admin_month"] + adj * (ELEC["wholesale_market_service_per_kwh"]
                                                   + ELEC["capacity_based_recovery_per_kwh"]
                                                   + ELEC["rural_remote_protection_per_kwh"])
    return (commodity + delivery + regulatory) * (1 + HST - OER)


water_month = WATER["fixed_month_5_8_inch_meter"]["total"] + WATER["combined_per_m3"] * 15
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
example = []
for i, mname in enumerate(MONTHS):
    m3 = GAS["typical_residential_m3_per_year"] * gas_month_share[i]
    kwh = ELEC["reference_residential_kwh_per_month"] * 12 * elec_month_share_gas_home[i]
    g, e = gas_bill(m3), elec_bill(kwh, i + 1)
    example.append({"month": mname, "gas_m3": round(m3, 1), "gas_bill": round(g, 2), "electricity_kwh": round(kwh),
                    "electricity_bill": round(e, 2), "water_bill": round(water_month, 2),
                    "total": round(g + e + water_month, 2)})

# Electrically heated home (18% of Niagara CMA households): no gas bill, heating kWh follow HDD.
example_elec = []
for i, mname in enumerate(MONTHS):
    kwh = kwh_nonheat_per_hh * elec_month_share_gas_home[i] + kwh_heat_per_elec_hh * hdd_s[i]
    e = elec_bill(kwh, i + 1)
    example_elec.append({"month": mname, "electricity_kwh": round(kwh), "electricity_bill": round(e, 2),
                         "water_bill": round(water_month, 2), "total": round(e + water_month, 2)})

per_hh = lambda pj, k: round(pj * 1e6 / (k * 1e3), 1)
space_gj = {k: per_hh(v, HH_K[k]) for k, v in SPACE_BY_TYPE.items()}
water_gj = {k: per_hh(v, HH_K[k]) for k, v in WATER_BY_TYPE.items()}
space_all = per_hh(sum(NR["space_heating"].values()), HH_K["all"])

energy = {
    "_meta": {
        "built": "2026-11-15",
        "purpose": "Household utility costs and their seasonality, so resident budgets tighten in winter. Tourists do not pay these.",
        "rule": "Blocks named 'derived_*' are calculated by scripts/build_topup.py from the sourced blocks; everything else is copied from the cited source.",
        "sources": {
            "npei_tariff": "Niagara Peninsula Energy Inc. Tariff of Rates and Charges effective 2026-01-01, OEB EB-2025-0020 — https://www.npei.ca/media/4721/download",
            "oeb_decision": "OEB Decision and Rate Order EB-2025-0020 (750 kWh reference customer; ~59,000 customers in Niagara Falls, Lincoln, West Lincoln, Pelham) — https://www.rds.oeb.ca/CMWebDrawer/Record/923364/File/document",
            "oeb_rpp": "Ontario Energy Board electricity rates, prices set for 2026-11-01 — https://www.oeb.ca/consumer-information-and-protection/electricity-rates",
            "oer": "Ontario Electricity Rebate 15.7% from 2026-11-01 (23.5% before) — CP24 2026-10-19, https://www.cp24.com/local/toronto/2026/10/19/ontarios-electricity-rates-are-falling-this-november-heres-how-much-it-will-cost/ ; 23.5% confirmed on NPEI winter 2025/26 rate sheet https://www.npei.ca/media/4651/download",
            "enbridge": "Enbridge Gas Rate 1 (EGD rate zone) residential notice, rates effective 2026-10-01 — https://www.enbridgegas.com/-/media/Extranet-Pages/ontario/residential/myaccount/rates/EGD---Rate-1---System-Notice.pdf",
            "water": "City of Niagara Falls 2026 Water and Wastewater Rates card — https://niagarafalls.ca/media/j0znyfgs/cnf_www_ratecard-2026_85x11-v2.pdf",
            "heating_fuel": "Statistics Canada table 38-10-0286-01, Primary heating systems and type of energy, 2023 (Households and the Environment Survey)",
            "climate": "Environment and Climate Change Canada, Canadian Climate Normals 1981-2010, station NIAGARA FALLS NPCSH (6135657) — https://api.weather.gc.ca/collections/climate-normals",
            "nrcan": "Natural Resources Canada, Comprehensive Energy Use Database, Residential Sector, Ontario, 2022, tables 2, 5, 6, 10, 11, 12, 14, 24 — https://oee.nrcan.gc.ca/corporate/statistics/neud/dpa/menus/trends/comprehensive/trends_res_on.cfm",
            "shs": "Statistics Canada table 11-10-0223-01, Survey of Household Spending 2023, Ontario, by before-tax income quintile",
        },
    },
    "electricity_npei_residential_2026": dict(ELEC, note="Distribution is fully fixed for NPEI residential (no per-kWh distribution rate). Per-kWh lines are $/kWh. Riders expire 2026-12-31."),
    "electricity_commodity_oeb_rpp": RPP,
    "ontario_electricity_rebate": {"rate_from_2026_11_01": OER, "rate_2025_11_01_to_2026_10_31": 0.235,
                                   "applies_to": "pre-tax bill subtotal"},
    "hst": HST,
    "natural_gas_enbridge_rate1_egd_2026_10_01": dict(GAS, note="Niagara Falls is in the Enbridge Gas Distribution (EGD) rate zone. Tier widths are m3 per month: first 30, next 55, next 85, over 170."),
    "water_city_of_niagara_falls_2026": WATER,
    "primary_heating_energy_pct_of_households_2023": {
        "st_catharines_niagara_cma": {"natural_gas": 70, "electricity": 18, "electricity_quality_flag": "E (use with caution)",
                                      "households_reporting_a_primary_heating_system": 94,
                                      "forced_air_furnace": 69, "forced_air_furnaces_using_natural_gas": 87},
        "ontario": {"natural_gas": 58, "electricity": 18, "propane": 4, "oil": 1, "wood": 1,
                    "forced_air_furnace": 66, "electric_baseboard": 7, "boiler_radiators": 7, "heat_pump": 4},
    },
    "ontario_apartment_heating_system_stock_pct_2022": {"natural_gas": 67.3, "electric": 24.9,
                                                         "note": "NRCan table 24: gas medium 9.3% + high efficiency 58.0%; electric 24.9%"},
    "climate_normals_niagara_falls_1981_2010": {
        "months": MONTHS, "mean_daily_temp_c": TEMP,
        "heating_degree_days_below_18c": HDD, "cooling_degree_days_above_18c": CDD,
        "annual_hdd": normals["Total degree-days below 18 deg C"]["13"],
        "annual_cdd": normals["Total degree-days Above 18 deg C"]["13"]},
    "ontario_residential_energy_use_2022_pj": NR,
    "ontario_households_2022_thousands": {"by_building_type": HH_K, "by_heating_energy_source": HH_BY_SOURCE_K},
    "ontario_space_heating_pj_by_building_type_2022": SPACE_BY_TYPE,
    "ontario_water_heating_pj_by_building_type_2022": WATER_BY_TYPE,
    "ontario_utility_spending_2023_by_income_quintile": {
        "columns": ["all", "lowest", "second", "third", "fourth", "highest"],
        "water_fuel_electricity_total": [3193, 1927, 2644, 2812, 3830, 4742],
        "electricity": [1299, 832, 1099, 1198, 1530, 1831],
        "natural_gas": [1016, 583, 807, 895, 1254, 1539],
        "other_fuel": [180, None, 153, 150, 221, 282],
        "water_and_sewage": [697, 418, 585, 569, 824, 1090],
        "shelter_total": [29136, 16052, 24768, 27105, 33084, 44578],
        "note": "Average per household across ALL households in the quintile, including those whose rent covers utilities and those with no gas service. $ per year.",
    },
    "derived_seasonal_shapes": {
        "method": "gas month share = (1 - h) x days/365 + h x HDD share, h = space-heating share of residential gas use. Electricity in a gas-heated home = flat base + cooling by CDD share. Electrically heated homes add heating kWh by HDD share.",
        "gas_space_heating_share_of_gas_use": round(gas_heat_share, 4),
        "months": MONTHS,
        "hdd_month_share": [round(x, 4) for x in hdd_s],
        "cdd_month_share": [round(x, 4) for x in cdd_s],
        "gas_use_month_share": [round(x, 4) for x in gas_month_share],
        "electricity_use_month_share_gas_heated_home": [round(x, 4) for x in elec_month_share_gas_home],
        "cooling_share_of_non_heating_electricity": round(cool_share_of_nonheat, 4),
        "ontario_avg_non_heating_electricity_kwh_per_household_year": round(kwh_nonheat_per_hh),
        "ontario_avg_space_heating_kwh_per_electrically_heated_household_year": round(kwh_heat_per_elec_hh),
    },
    "derived_dwelling_scaling_ontario_2022": {
        "space_heating_gj_per_household": dict(space_gj, all=space_all),
        "space_heating_index_vs_all": {k: round(v / space_all, 3) for k, v in space_gj.items()},
        "water_heating_gj_per_household": dict(water_gj, all=per_hh(sum(NR["water_heating"].values()), HH_K["all"])),
        "census_mapping": "single_detached = single-detached house; single_attached = semi-detached, row house, other single-attached; apartments = duplex apartment and both apartment-building classes",
    },
    "derived_example_gas_heated_home_monthly_bills_cad": {
        "assumptions": "Enbridge typical customer 2,400 m3/yr; NPEI reference 750 kWh/month average on the Tiered plan at 2026-11-01 prices; city-average water use 15 m3/month on a 5/8 inch meter. Bills include HST; electricity includes the 15.7% rebate. Gas excludes the one-time October 2026 refund.",
        "months": example,
        "annual": {k: round(sum(r[k] for r in example), 2) for k in ("gas_bill", "electricity_bill", "water_bill", "total")},
    },
    "derived_example_electrically_heated_home_monthly_bills_cad": {
        "assumptions": "Ontario averages from NRCan 2022: non-heating electricity per household plus space-heating kWh per electrically heated household, heating spread by HDD share. NPEI Tiered plan at 2026-11-01 prices, HST and 15.7% rebate included. Same water bill.",
        "months": example_elec,
        "annual": {k: round(sum(r[k] for r in example_elec), 2) for k in ("electricity_bill", "water_bill", "total")},
    },
    "data_gaps": [
        "Share of Niagara Falls renters whose rent includes heat and/or hydro is not published; the census shelter cost covers utilities whether paid directly or through rent.",
        "No utility-specific average consumption for NPEI residential customers was retrieved; 750 kWh/month is the OEB reference customer for NPEI bill impacts.",
        "Heating intensity by dwelling age: NRCan publishes space-heating energy by vintage but not households by vintage, so no per-home age index is included.",
        "Electricity primary-heating share for the St. Catharines-Niagara CMA carries StatCan quality flag E.",
        "Monthly shapes use 1981-2010 climate normals (latest published for the Niagara Falls station); a given year will differ.",
    ],
}
json.dump(energy, open(f"{PACK}/energy_parameters.json", "w"), indent=1)


# ----------------------------------------------------------------------------
# 4. Who buys what: Survey of Household Spending 2023
# ----------------------------------------------------------------------------
CAT_COL = "Household expenditures, summary-level categories"
CATS = [
    "Total expenditure", "Total current consumption", "Food expenditures", "Food purchased from stores",
    "Food purchased from restaurants", "Shelter", "Rent", "Mortgage paid for owned living quarters",
    "Water, fuel and electricity for principal accommodation", "Electricity for principal accommodation",
    "Natural gas for principal accommodation", "Accommodation away from home", "Household operations",
    "Communications", "Child care", "Pet expenses", "Household furnishings and equipment",
    "Clothing and accessories", "Women's and girls' wear (women and girls aged 14 years and over)",
    "Men's and boys' wear (men and boys aged 14 years and over)", "Children's wear (children under 14 years of age)",
    "Transportation", "Private transportation", "Gas and other fuels (all vehicles and tools)",
    "Public transportation", "Health care", "Prescribed medicines, pharmaceutical products and cannabis for medical use",
    "Dental services", "Personal care", "Personal care products", "Personal care services", "Hair grooming services",
    "Recreation", "Recreational equipment and related services", "Children's toys", "Entertainment",
    "Movie theatres", "Live sporting and performing arts events",
    "Dues and fees for sports and recreational activities and athletic and recreational facilities",
    "Package trips", "Education", "Reading materials and other printed matter",
    "Tobacco products, alcoholic beverages and cannabis for non-medical use", "Tobacco products and smokers' supplies",
    "Alcoholic beverages", "Alcoholic beverages served on licensed premises and in restaurants",
    "Alcoholic beverages purchased from stores", "Cannabis for non-medical use", "Games of chance",
    "Miscellaneous expenditures", "Income taxes", "Personal insurance payments and pension contributions",
    "Gifts of money, support payments and charitable contributions",
]


def shs(table, dim, geo, order):
    vals = defaultdict(dict)
    with open(f"{RAW}/{table}.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if r["REF_DATE"] == "2023" and r["GEO"] == geo and r[CAT_COL] in CATS:
                vals[r[CAT_COL]][r[dim]] = float(r["VALUE"]) if r["VALUE"] else None
    missing = [c for c in CATS if c not in vals]
    assert not missing, missing
    dollars = {c: [vals[c].get(k) for k in order] for c in CATS}
    index = {c: [None if (v is None or not row[0]) else round(v / row[0], 3) for v in row] for c, row in dollars.items()}
    return {"columns": order, "dollars_per_household_year": dollars, "index_vs_all_households": index}


spend = {
    "_meta": {
        "built": "2026-11-15",
        "purpose": "Who buys what. Average annual spending per household by age of the reference person, by household type and by income quintile, so each synthetic household's category mix follows its demographics.",
        "how_to_use": "Start from the Ontario income-quintile dollars (the level). Multiply by the Canada age index and household-type index for the same category (the tilt), then renormalise so the household's total matches its budget. Age and household-type tables are published for Canada only.",
        "unit": "Average $ per household per year, 2023. null = suppressed by Statistics Canada.",
        "sources": {
            "by_age": "Statistics Canada table 11-10-0227-01, Household spending by age of reference person, Canada, 2023",
            "by_household_type": "Statistics Canada table 11-10-0224-01, Household spending by household type, Canada, 2023",
            "by_income_quintile": "Statistics Canada table 11-10-0223-01, Household spending by household income quintile, Ontario, 2023",
        },
        "data_gaps": [
            "Spending is published per household, not per person: there is no official split by sex. The only sex-specific lines are women's/girls' wear, men's/boys' wear and children's wear. Sex enters the model through household composition (e.g. one-person and lone-parent households) and through tract age-by-sex counts.",
            "Age and household-type breakdowns are Canada-wide; Ontario is published by income quintile only.",
            "'Age' is the age of the household reference person, matched in the census to the age of the primary household maintainer.",
        ],
        "census_mapping": {
            "age_of_reference_person": {"Less than 30 years": "maintainer_15_24 + half of maintainer_25_34",
                                        "30 to 39 years": "half of maintainer_25_34 + half of maintainer_35_44",
                                        "40 to 54 years": "half of maintainer_35_44 + maintainer_45_54",
                                        "55 to 64 years": "maintainer_55_64",
                                        "65 years and over": "maintainer_65_74 + maintainer_75_84 + maintainer_85_plus"},
            "household_type": {"One person households": "hh_one_person",
                               "Couples without children": "hh_couple_no_children",
                               "Couples with children": "hh_couple_with_children",
                               "Lone-parent households with no additional persons": "hh_one_parent",
                               "Couples with other related or unrelated persons": "hh_multigenerational + hh_one_family_plus_others + hh_multiple_family",
                               "Other households with related or unrelated persons": "hh_non_family_2plus"},
        },
    },
    "by_age_of_reference_person_canada_2023": shs("11100227", "Age of reference person", "Canada",
        ["All classes", "Less than 30 years", "30 to 39 years", "40 to 54 years", "55 to 64 years", "65 years and over"]),
    "by_household_type_canada_2023": shs("11100224", "Household type", "Canada",
        ["All classes", "One person households", "Couples without children", "Couples with children",
         "Lone-parent households with no additional persons", "Couples with other related or unrelated persons",
         "Other households with related or unrelated persons"]),
    "by_income_quintile_ontario_2023": shs("11100223", "Before-tax household income quintile", "Ontario",
        ["All quintiles", "Lowest quintile", "Second quintile", "Third quintile", "Fourth quintile", "Highest quintile"]),
}
json.dump(spend, open(f"{PACK}/spending_by_demographic.json", "w"), indent=1)
print("built: tract_demographics.csv tract_distances.csv places_tract_lookup.csv tract_place_access.csv "
      "energy_parameters.json spending_by_demographic.json")
