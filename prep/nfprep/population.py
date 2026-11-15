"""Phase 2: synthetic population for Niagara Falls, for any year 2019-2025.

Every household and person is fitted to the tract's own 2021 Census totals by
iterative proportional fitting (IPF), then scaled to the Statistics Canada
population estimate for the requested year, with post-2021 growth placed where
housing was actually built.

Where the census gives only one-way totals, the IPF *seed* supplies the shape
of the relationship (for example "larger households earn more"). Seeds are
structural priors, marked SEED below; the fitted result always reproduces the
census totals for the tract whatever the seed says.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from .loader import Pack, load_pack

BUILD_DIR = Path(__file__).resolve().parents[2] / "build"

SIZE_COLS = ["hh_1_person", "hh_2_persons", "hh_3_persons", "hh_4_persons", "hh_5plus_persons"]
INC_COLS = ["inc_under_5k", "inc_5k_10k", "inc_10k_15k", "inc_15k_20k", "inc_20k_25k", "inc_25k_30k",
            "inc_30k_35k", "inc_35k_40k", "inc_40k_45k", "inc_45k_50k", "inc_50k_60k", "inc_60k_70k",
            "inc_70k_80k", "inc_80k_90k", "inc_90k_100k", "inc_100k_125k", "inc_125k_150k",
            "inc_150k_200k", "inc_200k_plus"]
# top band closed at $400k, as in sim_parameters.json income bands
INC_EDGES = [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 125, 150, 200, 400]
TYPES = ["one_person", "couple_no_children", "couple_with_children", "one_parent", "multigenerational",
         "multiple_family", "one_family_plus_others", "non_family_2plus"]
TYPE_COLS = ["hh_" + t for t in TYPES]
DWELLINGS = ["single_detached", "semi_detached", "row_house", "duplex_apartment",
             "apartment_under_5_storeys", "apartment_5plus_storeys", "other_attached", "movable"]
DW_COLS = ["dw_" + d for d in DWELLINGS]
APARTMENT = {"duplex_apartment", "apartment_under_5_storeys", "apartment_5plus_storeys"}
MAINT_COLS = ["maintainer_15_24", "maintainer_25_34", "maintainer_35_44", "maintainer_45_54",
              "maintainer_55_64", "maintainer_65_74", "maintainer_75_84", "maintainer_85_plus"]
AGE_BANDS = ["0_4", "5_9", "10_14", "15_19", "20_24", "25_29", "30_34", "35_39", "40_44", "45_49",
             "50_54", "55_59", "60_64", "65_69", "70_74", "75_79", "80_84", "85_plus"]
# maintainer 10-year band -> the 5-year age bands it covers (indexes into AGE_BANDS)
MAINT_TO_AGE = [[3, 4], [5, 6], [7, 8], [9, 10], [11, 12], [13, 14], [15, 16], [17]]
INDUSTRIES = [("11", "F11_AGRICULTURE_FORESTR_2262"), ("21", "F21_MINING_QUARRYING_OI_2263"),
              ("22", "F22_UTILITIES_2264"), ("23", "F23_CONSTRUCTION_2265"),
              ("31-33", "F31_33_MANUFACTURING_2266"), ("41", "F41_WHOLESALE_TRADE_2267"),
              ("44-45", "F44_45_RETAIL_TRADE_2268"), ("48-49", "F48_49_TRANSP_WAREHOUSG_2269"),
              ("51", "F51_INFORMATION_CULTURA_2270"), ("52", "F52_FINANCE_INS_2271"),
              ("53", "F53_REAL_ESTATE_RENTAL_2272"), ("54", "F54_PROFESSIONAL_SCIENT_2273"),
              ("55", "F55_MGT_COMPANIES_ENTER_2274"), ("56", "F56_ADMINISTRATIVE_SUPP_2275"),
              ("61", "F61_EDUCATIONAL_SVCS_2276"), ("62", "F62_HEALTH_CARE_SOC_ASS_2277"),
              ("71", "F71_ARTS_ENTERTAINMENT_2278"), ("72", "F72_ACCOMMODATION_FOOD_2279"),
              ("81", "F81_OTH_SVCS_EXCEPT_PUB_2280"), ("91", "F91_PUBLIC_ADMINISTRTN_2281")]

# CMHC completions (2024 Development & Housing Monitoring Report); 2021 is already in the 2021 estimate
COMPLETIONS = {2022: 846, 2023: 590, 2024: 534}
PERMIT_TO_DWELLING = {"single_family": "single_detached", "townhouse": "row_house",
                      "semi_detached": "semi_detached", "accessory_dwelling": "duplex_apartment",
                      "duplex_fourplex_units": "duplex_apartment"}

# --- SEEDS (structural priors; the census totals override them through IPF) ---
SEED_INCOME_SIGMA = 0.75                      # spread of the lognormal used to shape income by size
SEED_SIZE_INCOME_FACTOR = [1.0, 0.9, 1.0, 1.1, 1.1]   # 2+ household median, tilted by size
SEED_TYPE_BY_SIZE = {                         # rows: size 2,3,4,5+; which household types can have that size
    "couple_no_children": [1, 0, 0, 0], "couple_with_children": [0, 1, 1, 1],
    "one_parent": [1, .6, .25, .1], "multigenerational": [0, .2, 1, 1],
    "multiple_family": [0, 0, 1, 1], "one_family_plus_others": [0, 1, 1, 1],
    "non_family_2plus": [1, .3, .1, .03]}
SEED_DWELLING_OWNER = [1, 1, .8, .4, .15, .15, 1, 1]
SEED_DWELLING_RENTER = [.25, .5, .8, 1, 1, 1, .5, .5]
SEED_DETACHED_BY_SIZE = [.5, .9, 1, 1.1, 1.2]
SEED_APARTMENT_BY_SIZE = [1, .7, .4, .25, .2]
SEED_MAINTAINER_AGE = {                       # by household type, across the 8 maintainer age bands
    "one_person": [.6, 1, .7, .8, 1.2, 1.5, 1.5, 1.5],
    "couple_no_children": [.3, .8, .4, .5, 1.3, 1.6, 1.2, .6],
    "couple_with_children": [.1, .9, 1.6, 1.5, .8, .25, .08, .03],
    "one_parent": [.3, 1, 1.4, 1.3, .8, .4, .3, .3],
    "multigenerational": [.1, .5, 1, 1.2, 1.2, 1, .7, .4],
    "multiple_family": [.1, .5, 1, 1.2, 1.2, 1, .7, .4],
    "one_family_plus_others": [.3, 1, 1, 1, 1, .7, .4, .2],
    "non_family_2plus": [1.5, 1.5, .8, .7, .7, .5, .3, .2]}
SEED_MORTGAGE_BY_AGE = [1.5, 1.5, 1.45, 1.3, .9, .45, .2, .1]
DEBT_SIGMA = 1.0                              # SEED: spread of individual non-mortgage debt around its age mean


def ipf(seed: np.ndarray, marginals: list[tuple[tuple[int, ...], np.ndarray]], iters: int = 60) -> np.ndarray:
    """Scale `seed` until it matches each (axes, target) marginal."""
    t = seed.astype(float).copy()
    all_axes = tuple(range(t.ndim))
    for _ in range(iters):
        for axes, target in marginals:
            other = tuple(a for a in all_axes if a not in axes)
            cur = t.sum(axis=other, keepdims=True)
            shape = [t.shape[a] if a in axes else 1 for a in all_axes]
            ratio = np.divide(np.asarray(target, float).reshape(shape), cur, out=np.zeros_like(cur), where=cur > 0)
            t *= ratio
    return t


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def lognormal_band_mass(median_k: float, sigma: float) -> np.ndarray:
    mu = math.log(max(median_k, 1.0))
    cdf = [0.0] + [_norm_cdf((math.log(e) - mu) / sigma) for e in INC_EDGES[1:-1]] + [1.0]
    return np.diff(cdf) + 1e-6


def points_in_ring(xs: np.ndarray, ys: np.ndarray, ring: list) -> np.ndarray:
    """Vectorised ray casting: which points fall inside the polygon ring."""
    inside = np.zeros(len(xs), bool)
    r = np.asarray(ring, float)
    xj, yj = r[-1, 0], r[-1, 1]
    for xi, yi in r[:, :2]:
        if yi != yj:
            crosses = ((yi > ys) != (yj > ys)) & (xs < (xj - xi) * (ys - yi) / (yj - yi) + xi)
            inside ^= crosses
        xj, yj = xi, yi
    return inside


def tract_population_targets(pack: Pack, year: int) -> dict[str, float]:
    """Population per tract for `year` (2019-2025).

    Up to 2021 every tract moves with the city estimate. After 2021, people are
    first placed in tracts in proportion to new homes built there, and the rest
    of the growth is spread by 2021 population.
    """
    est = {int(k): v for k, v in pack.params["sim_parameters.json"]["residents"]["population"]
           ["statcan_estimates_by_year"].items()}
    if year not in est:
        raise ValueError(f"year must be one of {sorted(est)}")
    prof = pack.by_tract("tract_profiles.csv")
    growth = pack.by_tract("tract_growth_2021_2025.csv")
    census_pop = {t: float(prof[t]["population_2021"]) for t in pack.tracts}
    total = sum(census_pop.values())
    base_year = min(year, 2021)
    targets = {t: p * est[base_year] / total for t, p in census_pop.items()}   # includes census undercount
    if year > 2021:
        city_growth = est[year] - est[2021]
        units = sum(n for y, n in COMPLETIONS.items() if y <= year)
        permits = {t: float(growth[t]["new_lowrise_units_permits_2021_2023"]) for t in pack.tracts}
        housed = {t: units * permits[t] / sum(permits.values()) * float(prof[t]["avg_hh_size"]) for t in pack.tracts}
        scale = min(1.0, city_growth / sum(housed.values()))
        rest = city_growth - scale * sum(housed.values())
        for t in pack.tracts:
            targets[t] += scale * housed[t] + rest * census_pop[t] / total
    return targets


def _address_points_by_tract(pack: Pack) -> dict[str, np.ndarray]:
    pts = pack.tables["address_points.csv"]
    lat = np.array([float(r["lat"]) for r in pts])
    lon = np.array([float(r["lon"]) for r in pts])
    out = {}
    for f in pack.geo["census_tracts_boundaries.geojson"]["features"]:
        mask = points_in_ring(lon, lat, f["geometry"]["coordinates"][0])
        out[f["properties"]["census_tract"]] = np.column_stack([lat[mask], lon[mask]])
    return out


def _draw_cell(rng: np.random.Generator, remaining: np.ndarray, base: np.ndarray, weights: np.ndarray) -> int:
    """Pick an age-sex cell, preferring cells the tract still has people left in."""
    w = np.maximum(remaining, 0) * weights
    if w.sum() <= 0:
        w = base * weights
    cell = int(rng.choice(len(w), p=w / w.sum()))
    remaining[cell] -= 1
    return cell


def _age_in_band(rng: np.random.Generator, band: int) -> int:
    if band == 17:
        return int(min(85 + rng.geometric(0.18) - 1, 104))
    return int(band * 5 + rng.integers(0, 5))


def generate(pack: Pack | None = None, year: int = 2025, seed: int = 1) -> tuple[list[dict], list[dict]]:
    """Return (households, persons) for the requested year."""
    pack = pack or load_pack()
    rng = np.random.default_rng(seed)
    demo = pack.by_tract("tract_demographics.csv")
    prof = pack.by_tract("tract_profiles.csv")
    growth = pack.by_tract("tract_growth_2021_2025.csv")
    targets = tract_population_targets(pack, year)
    addresses = _address_points_by_tract(pack)
    idx = pack.params["earnings_index_ontario.json"]["avg_weekly_earnings_cad"]
    income_factor = idx[str(year)] / idx["2020"]
    emp_rate = pack.params["labour_rates_ontario_2025.json"]["employment_rate_pct"]
    emp_by_band = [0, 0, 0] + [emp_rate[b] / 100 for b in AGE_BANDS[3:14]] + [emp_rate["70_plus"] / 100] * 4
    earn = json.loads((Path(pack_dir(pack)) / "avg_weekly_earnings_sectors_ontario_2025.json").read_text())
    weekly = {k: v["avg_weekly_earnings"] for k, v in earn["sectors"].items()}
    weekly_default = earn["_meta"]["industrial_aggregate"]
    energy = pack.params["energy_parameters.json"]["primary_heating_energy_pct_of_households_2023"]["st_catharines_niagara_cma"]
    p_elec, p_gas = energy["electricity"] / 100, energy["natural_gas"] / 100
    debt = pack.params["sim_parameters.json"]["residents"]["debt_and_insolvency"]
    veh_pct = pack.params["sim_parameters.json"]["residents"]["transport"]["vehicle_ownership_niagara_region_TTS_2022"]

    households: list[dict] = []
    persons: list[dict] = []
    for tract in pack.tracts:
        d = {k: (float(v) if v not in ("", None) else 0.0) for k, v in demo[tract].items()
             if k not in ("census_tract", "community")}
        size_m = np.array([d[c] for c in SIZE_COLS])
        persons_in_hh = pack.census_num(tract, "TOT_NBR_PRSNS_PR_HH_56")
        avg5 = max(5.0, (persons_in_hh - (size_m[:4] * [1, 2, 3, 4]).sum()) / max(size_m[4], 1))
        mean_size = (size_m[:4] * [1, 2, 3, 4]).sum() / size_m.sum() + size_m[4] / size_m.sum() * avg5
        # Everyone in the estimate is placed in a private household (about 1.5% live in
        # collective dwellings such as care homes; they are represented as households here).
        n = int(round(targets[tract] / mean_size))

        # --- size x income x tenure ---
        med1 = pack.census_num(tract, "TOT_MED_TOT_INC_ONE_PERSON_246") / 1000
        med2 = pack.census_num(tract, "TOT_MED_TOT_INC_TWO_MR_PER_249") / 1000
        seed3 = np.zeros((5, 19, 2))
        for s in range(5):
            mass = lognormal_band_mass(med1 if s == 0 else med2 * SEED_SIZE_INCOME_FACTOR[s], SEED_INCOME_SIGMA)
            p_own = 0.3 + 0.6 * np.arange(19) / 18          # SEED: owning rises with income
            seed3[s, :, 0], seed3[s, :, 1] = mass * p_own, mass * (1 - p_own)
        inc_m = np.array([d[c] for c in INC_COLS])
        ten_m = np.array([d["owner_hh"], d["renter_hh"]])
        joint = ipf(seed3, [((0,), size_m / size_m.sum()), ((1,), inc_m / inc_m.sum()), ((2,), ten_m / ten_m.sum())])
        flat = rng.choice(joint.size, size=n, p=(joint / joint.sum()).ravel())
        size_i, inc_i, ten_i = np.unravel_index(flat, joint.shape)

        # --- household type given size ---
        type_m = np.array([d[c] for c in TYPE_COLS[1:]])
        seed_t = np.array([SEED_TYPE_BY_SIZE[t] for t in TYPES[1:]]).T       # 4 sizes x 7 types
        tj = ipf(seed_t, [((0,), size_m[1:] / size_m[1:].sum()), ((1,), type_m / type_m.sum())])
        type_i = np.zeros(n, int)
        for s in range(1, 5):
            m = size_i == s
            row = tj[s - 1] / tj[s - 1].sum()
            type_i[m] = 1 + rng.choice(7, size=int(m.sum()), p=row)

        # --- dwelling given size and tenure (new homes shift the tract's mix) ---
        dw_m = np.array([d[c] for c in DW_COLS])
        if year > 2021:
            share = sum(nn for y, nn in COMPLETIONS.items() if y <= year) / 1234
            for col, dw in PERMIT_TO_DWELLING.items():
                dw_m[DWELLINGS.index(dw)] += share * float(growth[tract][col])
        seed_d = np.zeros((5, 2, 8))
        for s in range(5):
            tilt = np.ones(8)
            tilt[0] = SEED_DETACHED_BY_SIZE[s]
            tilt[3:6] = SEED_APARTMENT_BY_SIZE[s]
            seed_d[s, 0], seed_d[s, 1] = np.array(SEED_DWELLING_OWNER) * tilt, np.array(SEED_DWELLING_RENTER) * tilt
        st = np.zeros((5, 2))
        np.add.at(st, (size_i, ten_i), 1)
        dj = ipf(seed_d, [((0, 1), st / n), ((2,), dw_m / dw_m.sum())])
        dw_i = np.zeros(n, int)
        for s in range(5):
            for t in range(2):
                m = (size_i == s) & (ten_i == t)
                if m.any():
                    dw_i[m] = rng.choice(8, size=int(m.sum()), p=dj[s, t] / dj[s, t].sum())

        # --- maintainer age given type ---
        maint_m = np.array([d[c] for c in MAINT_COLS])
        tcount = np.bincount(type_i, minlength=8).astype(float)
        aj = ipf(np.array([SEED_MAINTAINER_AGE[t] for t in TYPES]),
                 [((0,), tcount / n), ((1,), maint_m / maint_m.sum())])
        maint_i = np.zeros(n, int)
        for t in range(8):
            m = type_i == t
            if m.any():
                maint_i[m] = rng.choice(8, size=int(m.sum()), p=aj[t] / aj[t].sum())

        # --- mortgage: tract share of owners, tilted toward younger maintainers ---
        own = ten_i == 0
        target_share = float(prof[tract]["pct_owners_with_mortgage"]) / 100
        w_age = np.array(SEED_MORTGAGE_BY_AGE)[maint_i]
        lo, hi = 0.0, 5.0
        for _ in range(40):
            k = (lo + hi) / 2
            if np.clip(k * w_age[own], 0, 1).mean() < target_share:
                lo = k
            else:
                hi = k
        mortgage = own & (rng.random(n) < np.clip(k * w_age, 0, 1))

        # --- actual size for the 5+ group, adults and children ---
        actual = size_i + 1
        big = size_i == 4
        actual[big] = 5 + rng.poisson(max(avg5 - 5, 0.01), int(big.sum()))
        adults = np.ones(n, int)
        for i in range(n):
            t = TYPES[type_i[i]]
            adults[i] = {"one_person": 1, "couple_no_children": 2, "couple_with_children": 2, "one_parent": 1,
                         "multigenerational": 3 if actual[i] <= 4 else 4, "multiple_family": 4,
                         "one_family_plus_others": 3, "non_family_2plus": actual[i]}[t]
            adults[i] = min(adults[i], actual[i])
        children = actual - adults

        # --- people: ages and sexes drawn against the tract's age-by-sex counts ---
        base = np.array([[d[f"male_{b}"], d[f"female_{b}"]] for b in AGE_BANDS]).ravel()   # cell = band*2 + sex
        remaining = base / base.sum() * actual.sum()
        band_of = np.arange(36) // 2
        sex_of = np.arange(36) % 2
        hh_people: list[list[tuple[int, int, str]]] = [[] for _ in range(n)]
        m_cell = np.zeros(n, int)
        for i in rng.permutation(n):                                  # 1. maintainers
            w = np.isin(band_of, MAINT_TO_AGE[maint_i[i]]).astype(float)
            w[band_of == 3] *= 0.4                                    # of 15-19, only 18-19 head households
            m_cell[i] = _draw_cell(rng, remaining, base, w)
            hh_people[i].append((band_of[m_cell[i]], sex_of[m_cell[i]], "maintainer"))
        for i in rng.permutation(n):                                  # 2. partners
            if TYPES[type_i[i]] in ("couple_no_children", "couple_with_children") and adults[i] >= 2:
                mb, ms = band_of[m_cell[i]], sex_of[m_cell[i]]
                w = (np.abs(band_of - mb) <= 1) * (band_of >= 4) * np.where(sex_of != ms, 0.95, 0.05)
                c = _draw_cell(rng, remaining, base, w.astype(float))
                hh_people[i].append((band_of[c], sex_of[c], "partner"))
        for i in rng.permutation(n):                                  # 3. children (any age, as in the census)
            mb = band_of[m_cell[i]]
            for _ in range(children[i]):
                w = ((band_of <= mb - 4) & (band_of <= 6)).astype(float)
                if w.sum() == 0:
                    w = (band_of <= 3).astype(float)
                c = _draw_cell(rng, remaining, base, w)
                hh_people[i].append((band_of[c], sex_of[c], "child"))
        for i in rng.permutation(n):                                  # 4. other adults
            for _ in range(adults[i] - sum(1 for p in hh_people[i] if p[2] != "child")):
                c = _draw_cell(rng, remaining, base, (band_of >= 3).astype(float))
                hh_people[i].append((band_of[c], sex_of[c], "other_adult"))

        # --- heating fuel: CMA shares, electric slightly more common in apartments (NRCan stock ratio) ---
        apt = np.isin(dw_i, [DWELLINGS.index(x) for x in APARTMENT])
        apt_ratio = 24.9 / (1344.6 / 6034.2 * 100)
        p_e_other = (p_elec - apt.mean() * p_elec * apt_ratio) / max(1 - apt.mean(), 1e-9)
        u = rng.random(n)
        pe = np.where(apt, p_elec * apt_ratio, p_e_other)
        heating = np.where(u < pe, "electricity", np.where(u < pe + (1 - pe) * p_gas / (1 - p_elec), "natural_gas", "other"))

        # --- home location: a real civic address point inside the tract ---
        pts = addresses[tract]
        loc = np.zeros(n, int)
        houses = ~apt
        loc[houses] = rng.choice(len(pts), size=int(houses.sum()), replace=int(houses.sum()) > len(pts))
        if apt.any():                                                 # apartments cluster at fewer addresses
            blocks = rng.choice(len(pts), size=max(1, int(apt.sum() / 20)), replace=False)
            loc[apt] = rng.choice(blocks, size=int(apt.sum()))

        ind_p = {}
        for sx, prefix in ((0, "MA_"), (1, "FE_")):
            c = np.array([max(pack.census_num(tract, prefix + code), 0) for _, code in INDUSTRIES])
            ind_p[sx] = c / c.sum()

        for i in range(n):
            hid = len(households)
            lo_k, hi_k = INC_EDGES[inc_i[i]], INC_EDGES[inc_i[i] + 1]
            inc2020 = float(rng.uniform(lo_k, hi_k) * 1000)
            hh_debt = 0.0
            n_emp = 0
            n_minor = 0
            for band, sex, role in hh_people[i]:
                age = _age_in_band(rng, band)
                if role == "maintainer" and age < 18:
                    age = 18 + int(rng.integers(0, 2))
                employed = bool(age >= 15 and rng.random() < emp_by_band[band])
                industry, earnings = "", 0
                if employed:
                    industry = INDUSTRIES[int(rng.choice(len(INDUSTRIES), p=ind_p[sex]))][0]
                    earnings = int(round(weekly.get(industry, weekly_default) * 52))
                    n_emp += 1
                n_minor += age < 18
                if age >= 18:
                    hh_debt += _debt_mean(debt, age) * float(rng.lognormal(-DEBT_SIGMA ** 2 / 2, DEBT_SIGMA))
                persons.append({"person_id": len(persons), "household_id": hid, "census_tract": tract,
                                "age": age, "sex": "F" if sex else "M", "role": role,
                                "employed": int(employed), "industry_naics": industry,
                                "annual_earnings_est": earnings})
            households.append({
                "household_id": hid, "census_tract": tract,
                "lat": round(float(pts[loc[i], 0]), 6), "lon": round(float(pts[loc[i], 1]), 6),
                "size": int(actual[i]), "adults": int(actual[i]) - n_minor, "children_under_18": n_minor,
                "type": TYPES[type_i[i]], "maintainer_age_band": MAINT_COLS[maint_i[i]][11:],
                "tenure": "owner" if ten_i[i] == 0 else "renter", "has_mortgage": int(mortgage[i]),
                "dwelling": DWELLINGS[dw_i[i]], "heating": str(heating[i]),
                "income_2020": int(inc2020), "income": int(inc2020 * income_factor),
                "employed_persons": n_emp, "non_mortgage_debt": int(hh_debt), "vehicles": 0})

    _assign_vehicles(rng, households, veh_pct)
    return households, persons


def pack_dir(pack: Pack) -> str:
    from .loader import DATA_DIR
    return str(DATA_DIR)


def _debt_mean(debt: dict, age: int) -> float:
    """Average non-mortgage debt for a consumer of this age (Equifax, Q2 2025).

    Equifax publishes the 46-55, 56-65 and 65+ averages; younger adults get the
    Ontario all-consumer average.
    """
    by_age = debt["equifax_age_debt_canada_q2_2025"]
    if age >= 66:
        return by_age["65+"]
    if age >= 56:
        return by_age["56-65"]
    if age >= 46:
        return by_age["46-55"]
    return debt["ontario_avg_non_mortgage_debt_per_consumer_q2_2025"]


def _assign_vehicles(rng: np.random.Generator, households: list[dict], veh: dict) -> None:
    """Rank households by need and means, then cut the ranking at the survey's shares (0/1/2/3/4+)."""
    score = np.array([h["adults"] + 0.6 * math.log(max(h["income"], 5000) / 50000)
                      + (0.5 if h["dwelling"] == "single_detached" else -0.7 if h["dwelling"] in APARTMENT else 0)
                      for h in households]) + rng.normal(0, 0.8, len(households))   # SEED: ranking weights
    order = np.argsort(score)
    pct = veh["pct_households_by_vehicles"]
    cuts = np.cumsum([pct["0"], pct["1"], pct["2"], pct["3"], pct["4+"]]) / 100 * len(households)
    # 4+ households get 4, 5 or 6 so the city mean equals the survey's vehicles per household
    mean_rest = (pct["1"] + 2 * pct["2"] + 3 * pct["3"]) / 100
    top_mean = (veh["vehicles_per_household"] - mean_rest) / (pct["4+"] / 100)
    for rank, i in enumerate(order):
        k = int(np.searchsorted(cuts, rank, side="right"))
        if k >= 4:
            k = 4 + int(rng.poisson(max(top_mean - 4, 0)))
        households[i]["vehicles"] = k


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def fingerprint(households: list[dict], persons: list[dict]) -> str:
    h = hashlib.sha256()
    for rows in (households, persons):
        for r in rows:
            h.update(repr(tuple(r.values())).encode())
    return h.hexdigest()[:16]


def summarise(households: list[dict], persons: list[dict]) -> dict:
    n = len(households)
    owners = [h for h in households if h["tenure"] == "owner"]
    ages = np.array([p["age"] for p in persons])
    return {
        "population": len(persons),
        "households": n,
        "avg_household_size": round(len(persons) / n, 3),
        "median_household_income_2020": float(np.median([h["income_2020"] for h in households])),
        "median_household_income_sim_year": float(np.median([h["income"] for h in households])),
        "pct_owner": round(100 * len(owners) / n, 2),
        "pct_owners_with_mortgage": round(100 * sum(h["has_mortgage"] for h in owners) / len(owners), 2),
        "vehicles_per_household": round(sum(h["vehicles"] for h in households) / n, 3),
        "pct_female": round(100 * sum(p["sex"] == "F" for p in persons) / len(persons), 2),
        "pct_under_18": round(100 * float((ages < 18).mean()), 2),
        "pct_age_0_14": round(100 * float((ages <= 14).mean()), 2),
        "pct_age_65_plus": round(100 * float((ages >= 65).mean()), 2),
        "pct_employed_of_15_plus": round(100 * sum(p["employed"] for p in persons) / int((ages >= 15).sum()), 2),
        "pct_heating_gas": round(100 * sum(h["heating"] == "natural_gas" for h in households) / n, 2),
        "pct_heating_electric": round(100 * sum(h["heating"] == "electricity" for h in households) / n, 2),
        "avg_non_mortgage_debt_per_adult": round(sum(h["non_mortgage_debt"] for h in households)
                                                 / int((ages >= 18).sum())),
    }


def main(year: int = 2025, seed: int = 1) -> None:
    households, persons = generate(year=year, seed=seed)
    write_csv(BUILD_DIR / f"households_{year}.csv", households)
    write_csv(BUILD_DIR / f"persons_{year}.csv", persons)
    report = summarise(households, persons)
    report["fingerprint"] = fingerprint(households, persons)
    (BUILD_DIR / f"population_report_{year}.json").write_text(json.dumps(report, indent=1))
    for k, v in report.items():
        print(f"{k:38s} {v}")


if __name__ == "__main__":
    import sys
    main(*(int(a) for a in sys.argv[1:3]))
