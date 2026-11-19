"""Commercial rent at any spot and each business's floor area.

Rent per square foot per year at a point = distance-weighted average of retail and office asking rents
(and of rents tenants pay, from businesses and buildings for sale: niagara_falls_inplace_rents_2026-11.json)
within 1.5 km (data/real_estate/niagara_falls_commercial_lease_2026-11.json, placed on the City's
address points), leaning toward the city median where listings are few (as if two median listings
stood at the point). Only 13 asking rents exist, so this separates districts, not blocks: approximate.
Floor area = Niagara Region's Employment Inventory 2025 indoor floor area for a business matched by
name within 80 m; otherwise the building footprint share already on the business (businesses.py).
"""
from __future__ import annotations

import csv
import json
import re

import numpy as np

from .loader import DATA_DIR

ABBR = {"LANE": "LN", "LN": "LN", "AVENUE": "AV", "AVE": "AV", "ROAD": "RD", "STREET": "ST", "PKY": "PKY",
        "PARKWAY": "PKY", "SQUARE": "SQ", "BOULEVARD": "BV", "BLVD": "BV"}
PRIOR_LISTINGS = 2
RADIUS_KM = 1.5
MIN_SQFT, MAX_SQFT = 300, 20_000


def _key(a: str):
    a = a.upper().split(",")[0].replace("’", "'").replace("LUNDYS", "LUNDY'S")
    m = re.match(r"(\d+)(?:-\d+)?\s+(.+)", a)
    if not m:
        return None
    return m.group(1) + " " + " ".join(ABBR.get(w, w) for w in m.group(2).replace(".", "").split())


class RentSurface:
    def __init__(self):
        pts = {p["address"].upper(): p for p in csv.DictReader(open(DATA_DIR / "address_points.csv"))}
        lat, lon, v = [], [], []
        for r in json.loads((DATA_DIR / "real_estate/niagara_falls_commercial_lease_2026-11.json").read_text())["listings"]:
            rent = r.get("rent_psf_year") or r.get("rent_psf_year_from_month")
            if not rent or "industrial" in (r.get("use") or ""):
                continue
            k = _key(r["address"])
            p = pts.get(k) if k else None
            if not p and k:
                p = next((q for a, q in pts.items() if a.startswith(k.rsplit(" ", 1)[0])), None)
            if p:
                lat.append(float(p["lat"])); lon.append(float(p["lon"])); v.append(float(rent))
        # rents tenants pay now or take on with a business for sale (ground floor, not future lease steps)
        for r in json.loads((DATA_DIR / "real_estate/niagara_falls_inplace_rents_2026-11.json").read_text())["rents"]:
            if r.get("upper_floor") or r["kind"] == "contracted":
                continue
            p = pts.get(_key(r["address"]))
            if p:
                lat.append(float(p["lat"])); lon.append(float(p["lon"])); v.append(float(r["rent_psf_year"]))
        self.lat, self.lon, self.v = np.array(lat), np.array(lon), np.array(v)
        self.median = float(np.median(self.v))

    def at(self, la: float, lo: float) -> float:
        d = np.hypot((self.lat - la) * 111_320, (self.lon - lo) * 111_320 * np.cos(np.radians(la))) / 1000
        w = np.where(d < RADIUS_KM, 1 / (d + 0.2) ** 2, 0.0)
        return float((w @ self.v + PRIOR_LISTINGS * self.median) / (w.sum() + PRIOR_LISTINGS))


def floor_areas_sqft(businesses: list[dict]) -> np.ndarray:
    """Each business's floor area in square feet (inventory where matched, else footprint share), clipped."""
    from .backtest import same_business
    inv = [x for x in csv.DictReader(open(DATA_DIR / "sources/region/employment_inventory_niagara_falls_2016_2025.csv", encoding="utf-8-sig"))
           if x["Year"] == "2025" and x["lat"] and x["indoorgfa"] not in ("", "0")]
    ilat, ilon = np.array([float(x["lat"]) for x in inv]), np.array([float(x["lon"]) for x in inv])
    out = np.zeros(len(businesses))
    for i, b in enumerate(businesses):
        la, lo = float(b["lat"]), float(b["lon"])
        d = np.hypot((ilat - la) * 111_320, (ilon - lo) * 111_320 * np.cos(np.radians(la)))
        hit = [k for k in np.where(d < 80)[0] if b.get("name") and same_business(b["name"], inv[k]["businessname"])]
        if hit and float(inv[hit[0]]["indoorgfa"]) >= 100:
            out[i] = float(inv[hit[0]]["indoorgfa"])
        else:
            out[i] = float(b.get("floor_area_m2") or 0) * 10.764
    return np.clip(np.where(out > 0, out, 1500), MIN_SQFT, MAX_SQFT)


def location_factors(lat, lon) -> np.ndarray:
    """Rent at each spot relative to the city's median asking rent (1.0 = a median street)."""
    rs = RentSurface()
    return np.array([rs.at(float(a), float(o)) / rs.median for a, o in zip(lat, lon)])


def for_web() -> dict:
    """The listings and settings the game needs to work out rent at a marked spot."""
    rs = RentSurface()
    return {"lat": [round(float(x), 6) for x in rs.lat], "lon": [round(float(x), 6) for x in rs.lon],
            "psf": [round(float(x), 2) for x in rs.v], "median_psf": rs.median, "radius_km": RADIUS_KM,
            "prior_listings": PRIOR_LISTINGS,
            "source": "Retail and office asking rents (lease listings) and rents tenants pay (businesses and buildings for sale), Niagara Falls, November 2026"}
