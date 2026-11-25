"""Food places within walking reach of each high school, from Google Places (Nearby Search, basic fields).

Plazas next to schools hold food places that neither OpenStreetMap nor the City's licences list (Little
Caesars at 8123 Lundy's Ln was missing; owner's report). Every food place Google has within 800 m of a
high school is kept in data/google_nearby_food.json; the build adds those it does not already have.
Same terms as google_places.py: kept in the private repository, never offered as a map or directory.
Usage: python3 -m nfprep.food_sweep
"""
import json, time, urllib.request
from pathlib import Path
from .google_places import USAGE, usage, month
from .daytime import places

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "google_nearby_food.json"
URL = "https://places.googleapis.com/v1/places:searchNearby"
FIELDS = ["places.id", "places.displayName", "places.formattedAddress", "places.location", "places.businessStatus", "places.primaryType"]
GROUPS = [["fast_food_restaurant", "pizza_restaurant", "hamburger_restaurant", "sandwich_shop", "meal_takeaway"],
          ["cafe", "coffee_shop", "bakery", "ice_cream_shop"],
          ["restaurant"],
          ["convenience_store", "grocery_store", "supermarket"]]
RADIUS = 800.0
PRO_LIMIT = 4900


def nearby(lat, lon, types):
    body = {"includedTypes": types, "maxResultCount": 20, "rankPreference": "DISTANCE",
            "locationRestriction": {"circle": {"center": {"latitude": lat, "longitude": lon}, "radius": RADIUS}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": "proxy-injected", "X-Goog-FieldMask": ",".join(FIELDS)})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read()).get("places", [])


def main():
    u = usage()
    schools = [p for p in places() if p["kind"] == "high school"]
    if u[month()]["pro"] + len(schools) * len(GROUPS) > PRO_LIMIT:
        raise SystemExit("would pass the month's free allowance")
    found = {}
    for s in schools:
        for g in GROUPS:
            res = nearby(s["lat"], s["lon"], g)
            u[month()]["pro"] += 1
            for p in res:
                found.setdefault(p["id"], dict(p, near_school=s["name"], asked_types=g))
            time.sleep(0.3)
    USAGE.write_text(json.dumps(u, indent=1))
    OUT.write_text(json.dumps({"_meta": {"what": __doc__.strip().split("\n\n")[0], "checked": time.strftime("%Y-%m-%d"), "radius_m": RADIUS},
                               "places": list(found.values())}, indent=1, ensure_ascii=False))
    print(len(found), "food places;", u[month()])


if __name__ == "__main__":
    main()
