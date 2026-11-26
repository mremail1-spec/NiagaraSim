"""Download the City of Niagara Falls open-data layers the 3D city uses (rivers, creeks, parks, trees,
parking lots, property parcels) as GeoJSON into data/city_open_data/world/. Terms of Use of the City of Niagara Falls.

Run: python3 data/scripts/fetch_city_world_layers.py
"""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path

BASE = "https://services9.arcgis.com/oMFQlUUrLd1Uh1bd/arcgis/rest/services/{}/FeatureServer/0/query"
LAYERS = {
    "rivers": "Niagara_Falls_Rivers_WGS_1984",
    "creeks": "Niagara_Falls_Creek_WGS_1984",
    "parks": "Niagara_Falls_Community_Parks_and_Trail_Lands_WGS_1984",
    "trees": "Niagara_Falls_Trees_Inventory_WGS_1984",
    "parking_lots": "Niagara_Falls_Parking_Lots_WGS_1984",
    "parcels": "Niagara_Falls_Property_Parcels_WGS_1984",
}
OUT = Path(__file__).resolve().parents[1] / "city_open_data" / "world"


def fetch(service: str) -> dict:
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "geojson", "outSR": "4326",
                                    "resultOffset": offset, "resultRecordCount": 2000})
        with urllib.request.urlopen(BASE.format(service) + "?" + q, timeout=120) as r:
            d = json.load(r)
        got = d.get("features", [])
        feats += got
        if not got or not (d.get("exceededTransferLimit") or d.get("properties", {}).get("exceededTransferLimit")):
            if len(got) < 2000:
                break
        offset += len(got)
        time.sleep(0.5)
    return {"type": "FeatureCollection", "features": feats}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, service in LAYERS.items():
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        fc = fetch(service)
        fc["_meta"] = {"source": f"City of Niagara Falls open data, {service.replace('_', ' ')}", "terms": "Terms of Use of the City of Niagara Falls",
                       "downloaded": time.strftime("%Y-%m-%d")}
        (OUT / f"{name}.geojson").write_text(json.dumps(fc, separators=(",", ":")))
        print(name, len(fc["features"]))
