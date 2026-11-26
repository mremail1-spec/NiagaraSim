"""Download the City of Niagara Falls open-data layers the 3D city uses (rivers, creeks, parks, trees,
parking lots, property parcels) as GeoJSON into data/city_open_data/world/. Terms of Use of the City of Niagara Falls.

Run: python3 data/scripts/fetch_city_world_layers.py
"""
import json, sys, time, urllib.request, urllib.parse
from pathlib import Path

BASE = "https://services9.arcgis.com/oMFQlUUrLd1Uh1bd/arcgis/rest/services/{}/FeatureServer/{}/query"
LAYERS = {
    "rivers": "Niagara_Falls_Rivers_WGS_1984",
    "creeks": "Niagara_Falls_Creek_WGS_1984",
    "parks": "Niagara_Falls_Community_Parks_and_Trail_Lands_WGS_1984",
    "trees": "Niagara_Falls_Trees_Inventory_WGS_1984",
    "parking_lots": "Niagara_Falls_Parking_Lots_WGS_1984",
    "parcels": "Niagara_Falls_Property_Parcels_WGS_1984",
    # lots and planning
    "address_points": "Niagara_Falls_Address_Points_WGS_1984",
    "zoning_79200": "Niagara_Falls_Zoning_Bylaw_79200_WGS_1984",
    "zoning_willoughby": "Niagara_Falls_Zoning_Bylaw_B0395_Willoughby_WGS_1984",
    "zoning_crowland": "Niagara_Falls_Zoning_Bylaw_1538_Crowland_WGS_1984",
    "zoning_humberstone": "Niagara_Falls_Zoning_Bylaw_7069_Humberstone_WGS_1984",
    "op_land_use": "Niagara_Falls_Official_Plan_Schedule_A_Land_Use_WGS_1984",
    "op_special_policy": "Niagara_Falls_Official_Plan_Special_Policy_Areas_WGS_1984",
    "building_permits": "Niagara_Falls_Completed_Building_Permits",
    "development_applications": "Niagara_Falls_Current_Development_Applications_WGS_1984",
    "condominiums": "Niagara_Falls_Condominiums_WGS_1984",
    "heritage": "Niagara_Falls_Heritage_Properties_WGS_1984",
    "city_property": "Niagara_Falls_City_Owned_Property_WGS_1984",
    "subdivisions": "Niagara_Falls_Subdivisions_WGS_1984",
    "bia": "Niagara_Falls_Business_Improvement_Areas_WGS_1984",
    # streets
    "road_segments": "Niagara_Falls_TES_Midblocks_WGS_1984",
    "intersections": "Niagara_Falls_TES_Intersections_WGS_1984",
    "street_lights": "Niagara_Falls_Street_Light_Supports_WGS_1984",
    "hydrants": "Niagara_Falls_Hydrants_WGS_1984",
    "bike_lanes": "Niagara_Falls_Community_Bike_Lanes_WGS_1984",
    "trails": "Niagara_Falls_Community_Public_Trails_WGS_1984",
    # places
    "public_art": "Niagara_Falls_Public_Art_WGS_1984",
    "playgrounds": "Niagara_Falls_Playgrounds_WGS_1984",
    "places_of_worship": "Niagara_Falls_Places_of_Worship_WGS_1984",
    "care_centres": "Niagara_Falls_Care_Centres_WGS_1984",
    "medical": "Niagara_Falls_Medical_Services_WGS_1984",
    "community_services": "Niagara_Falls_Community_Services_WGS_1984",
    "mist_areas": "Niagara_Falls_Maintenance_Mist_Area_by_Wind_Direction_WGS_1984",
}
OUT = Path(__file__).resolve().parents[1] / "city_open_data" / "world"
TABLES = {"weather": ("Niagara_Falls_Weather_Data", 11), "precipitation": ("Niagara_Falls_Precipitation_Data", 4)}   # tables: (service, layer id)
DROP = {"Contact", "Password"}                   # contact names and Wi-Fi passwords are not kept


def fetch(service: str, table: bool = False, layer: int = 0) -> dict:
    feats, offset = [], 0
    while True:
        q = urllib.parse.urlencode({"where": "1=1", "outFields": "*", "f": "json" if table else "geojson", "outSR": "4326",
                                    "resultOffset": offset, "resultRecordCount": 1000 if table else 2000})
        with urllib.request.urlopen(BASE.format(service, layer) + "?" + q, timeout=120) as r:
            d = json.load(r)
        got = d.get("features", [])
        if table:
            got = [{"type": "Feature", "geometry": None, "properties": g["attributes"]} for g in got]
        for g in got:
            for k in DROP & set(g.get("properties") or {}):
                del g["properties"][k]
        feats += got
        if not got or not (d.get("exceededTransferLimit") or d.get("properties", {}).get("exceededTransferLimit")):
            if len(got) < (1000 if table else 2000):
                break
        offset += len(got)
        time.sleep(0.5)
    return {"type": "FeatureCollection", "features": feats}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, service in {**LAYERS, **TABLES}.items():
        if len(sys.argv) > 1 and name not in sys.argv[1:]:
            continue
        service, layer = service if isinstance(service, tuple) else (service, 0)
        fc = fetch(service, name in TABLES, layer)
        fc["_meta"] = {"source": f"City of Niagara Falls open data, {service.replace('_', ' ')}", "terms": "Terms of Use of the City of Niagara Falls",
                       "downloaded": time.strftime("%Y-%m-%d")}
        (OUT / f"{name}.geojson").write_text(json.dumps(fc, separators=(",", ":")))
        print(name, len(fc["features"]))
