"""Lanes and posted speed limit of every street, from the City of Niagara Falls Traffic Engineering road segments
(data/city_open_data/world/road_segments.geojson; government data), for the traffic model.

Per street name: the lane count and the posted limit that cover most of its length, with the share of length they
cover, plus the street's length and whether any part has a bike lane. Written to data/city_streets.json.

Not yet used by the model: posted limits were tried before (roads.USE_REGION_SPEEDS) and made the fit to counted
traffic worse while the model had no delay at intersections. The City's intersection controls (stop signs,
all-way stops, traffic lights: data/city_open_data/world/intersections.geojson) can supply that delay; use both
together, and keep them only if the fit to the measured counts and TomTom speeds improves.

Run from prep/: python3 -m nfprep.city_streets
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    feats = json.loads((ROOT / "data/city_open_data/world/road_segments.geojson").read_text())["features"]
    by = defaultdict(list)
    for f in feats:
        p = f["properties"]
        name = (p.get("StreetName") or "").strip()
        if name:
            by[name.lower()].append(p)
    streets = {}
    for name, rows in sorted(by.items()):
        total = sum(r.get("Length_m") or 0 for r in rows) or 1
        rec = {"km": round(total / 1000, 2), "bike_lane": any(r.get("BikeLane") == "yes" for r in rows)}
        for key, out in (("NumLanes", "lanes"), ("PostSpLim", "speed_kmh")):
            c = Counter()
            for r in rows:
                if r.get(key):
                    c[r[key]] += r.get("Length_m") or 0
            if c:
                v, length = c.most_common(1)[0]
                rec[out], rec[out + "_share"] = v, round(length / total, 2)
        cls = Counter((r.get("RoadClass") or "").title() for r in rows if r.get("RoadClass"))
        if cls:
            rec["class"] = cls.most_common(1)[0][0]
        streets[name] = rec
    doc = {"_meta": {"source": "City of Niagara Falls open data, TES Midblocks (road segments): lanes, posted speed limit, road class, bike lane. Government data.",
                     "what": __doc__.strip().split("\n\n")[0]},
           "streets": streets}
    (ROOT / "data/city_streets.json").write_text(json.dumps(doc, indent=1))
    print(f"{len(streets)} streets, {sum('speed_kmh' in s for s in streets.values())} with a posted limit, "
          f"{sum('lanes' in s for s in streets.values())} with lanes")


if __name__ == "__main__":
    main()
