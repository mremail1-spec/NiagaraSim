"""Every business in the simulation checked against Google Places: open, temporarily closed or
permanently closed, and (where the month's free allowance allows) its rating and review count.

Why: two-thirds of the model's businesses come from OpenStreetMap, which changes only when a
volunteer edits it, so shops that closed years ago (6611 Lundy's Lane) are still on it. Google
marks a place closed from its own visits and reviews.

One Text Search per business (name and address, biased to its spot). Google bills by the fields
asked for: status alone is the 'Pro' kind (5,000 free a month), with rating and review count the
'Enterprise' kind (1,000 free a month, then $35 per 1,000). The run stops short of both free
allowances, counted per calendar month in build/google_places_usage.json; run it again next month
to fill in the ratings still missing.

Google's replies stay in build/ (their terms limit storing Places content); only our own
open/closed decision per business is used in the build. The project never offers a map or
directory built on them.

Usage: python3 -m nfprep.google_places            (both kinds, up to the free allowances)
       python3 -m nfprep.google_places --test N   (first N businesses only)
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "google_places.json"
USAGE = ROOT / "build" / "google_places_usage.json"
URL = "https://places.googleapis.com/v1/places:searchText"
BASE_FIELDS = ["places.id", "places.displayName", "places.formattedAddress", "places.location",
               "places.businessStatus", "places.primaryType"]
RATING_FIELDS = ["places.rating", "places.userRatingCount"]
FREE = {"pro": 4900, "enterprise": 990}          # Google: 5,000 and 1,000 free a month; kept short of both
MATCH_M = 150
# businesses whose ratings matter most go first (shops and restaurants before parking lots and rentals)
LOW_PRIORITY = ("licence:parking_lot", "licence:vacation_rental", "licence:bed_and_breakfast")


def month() -> str:
    return time.strftime("%Y-%m")


def usage() -> dict:
    u = json.loads(USAGE.read_text()) if USAGE.exists() else {}
    return u.setdefault(month(), {"pro": 0, "enterprise": 0}) and u


def search(text: str, lat: float, lon: float, ratings: bool):
    body = {"textQuery": text, "maxResultCount": 5,
            "locationBias": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": 300.0}}}
    fields = BASE_FIELDS + (RATING_FIELDS if ratings else [])
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": "proxy-injected",
                                          "X-Goog-FieldMask": ",".join(fields)})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read()).get("places", [])
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(5 * (attempt + 1)); continue
            raise


def dist_m(la, lo, la2, lo2):
    return float(np.hypot((la - la2) * 111_320, (lo - lo2) * 111_320 * np.cos(np.radians(la))))


def best_match(b: dict, places: list):
    """The Google place that is this business: close by and with a matching name (or, for a business
    known only by its address, the closest place at that address)."""
    from .backtest import same_business
    la, lo = float(b["lat"]), float(b["lon"])
    unnamed = b["name_source"].startswith("description")
    cands = []
    for p in places:
        if not p.get("businessStatus"):
            continue                               # a bare street address, not a business
        loc = p.get("location") or {}
        d = dist_m(la, lo, loc.get("latitude", 0), loc.get("longitude", 0))
        name = (p.get("displayName") or {}).get("text", "")
        named = bool(b["name"]) and same_business(b["name"], name)
        if d <= MATCH_M and (named or (unnamed and d <= 40)):
            cands.append((0 if named else 1, d, p))
    return min(cands, key=lambda c: (c[0], c[1]))[2] if cands else None


def same_address(b: dict, places: list):
    """A business Google lists at this exact street number and street (a renamed or replacing business)."""
    num = b["address"].split()[0] if b["address"] else ""
    street = (b["address"].split()[1:2] or [""])[0].lower().replace("'", "").replace("’", "")[:5]
    for p in places:
        a = (p.get("formattedAddress") or "").lower().replace("'", "").replace("`", "").replace("’", "")
        if p.get("businessStatus") and num and a.startswith(num + " ") and street and street in a.split(",")[0]:
            return {"name": (p.get("displayName") or {}).get("text"), "status": p["businessStatus"],
                    "address": p.get("formattedAddress"), "id": p.get("id")}
    return None


def main(limit: int | None = None) -> dict:
    rows = list(csv.DictReader(open(ROOT / "build" / "city_2025" / "businesses.csv", encoding="utf-8")))
    rows.sort(key=lambda r: (r["source"].startswith(LOW_PRIORITY), int(r["business_id"])))
    done = json.loads(OUT.read_text()) if OUT.exists() else {}
    u = usage()
    m = u[month()]
    asked = 0
    for b in rows[:limit] if limit else rows:
        key = b["osm_id"] or f"{b['source']}:{b['name']}:{b['address']}"
        have = done.get(key)
        if have and (not have["match"] or have["ratings_asked"]):
            continue                               # done (not on Google, or ratings already asked)
        ratings = m["enterprise"] < FREE["enterprise"]
        if not ratings and (have or m["pro"] >= FREE["pro"]):
            continue                               # status already known, or both allowances used
        text = f"{b['name']}, {b['address']}, Niagara Falls, ON" if b["name"] and not b["name_source"].startswith("description") \
            else f"{b['address']}, Niagara Falls, ON"
        places = search(text, float(b["lat"]), float(b["lon"]), ratings)
        m["enterprise" if ratings else "pro"] += 1
        asked += 1
        hit = best_match(b, places)
        done[key] = {"business_id": int(b["business_id"]), "name": b["name"], "address": b["address"], "query": text,
                     "match": hit, "same_address": None if hit else same_address(b, places), "ratings_asked": ratings, "checked": time.strftime("%Y-%m-%d"),
                     "candidates": [{"name": (p.get("displayName") or {}).get("text"), "status": p.get("businessStatus"),
                                     "address": p.get("formattedAddress")} for p in places[:5]]}
        if asked % 50 == 0:
            OUT.write_text(json.dumps(done)); USAGE.write_text(json.dumps(u, indent=1))
            print(f"{asked} asked; this month pro {m['pro']}, enterprise {m['enterprise']}", flush=True)
        time.sleep(0.1)
    OUT.write_text(json.dumps(done)); USAGE.write_text(json.dumps(u, indent=1))
    found = [v for v in done.values() if v["match"]]
    status = {}
    for v in found:
        s = v["match"].get("businessStatus", "UNKNOWN")
        status[s] = status.get(s, 0) + 1
    summary = {"businesses_checked": len(done), "found_on_google": len(found), "not_found": len(done) - len(found),
               "status": status, "with_review_count": sum("userRatingCount" in v["match"] for v in found),
               "asked_this_run": asked, "this_month": m}
    print(json.dumps(summary, indent=1))
    return summary


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[a.index("--test") + 1]) if "--test" in a else None)
