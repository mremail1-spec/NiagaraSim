# City of Niagara Falls open data used by the model
Downloaded 17 November 2026 from the city's open data hub (https://open.niagarafalls.ca), Terms of Use of the City of Niagara Falls. The full catalogue of 362 datasets was supplied by the project owner.

| File | What it is | Used for |
|---|---|---|
| building_footprints_2018.json.gz | 42,247 building outlines with area (m2) and height (m) | Real floor area for each business, in place of a random size |
| capital_projects.json.gz | 251 city capital projects 2022-2026 with program and dollar value | Where the city spends on roads and parks, checked against where the model puts traffic and visitors |
| tourism_districts.json | The city's 9 tourism districts (Clifton Hill, Fallsview, Lundy's Lane, ...) | Reporting by district |
| municipal_parking_lots.json | 28 city-owned parking lots | Visitor parking |
| world/rivers.geojson | Niagara River, Welland River, creeks and islands as polygons (Theme Water or Land) | Water in the 3D city |
| world/creeks.geojson | 530 creek lines | Creeks in the 3D city |
| world/parks.geojson | 109 community parks and trail lands | Parks in the 3D city |
| world/trees.geojson | 31,094 inventoried trees with species and height range | Trees in the 3D city |
| world/parking_lots.geojson | 28 parking lots | Kept for the 3D city |

The `world/` layers were downloaded 26 November 2026 with `data/scripts/fetch_city_world_layers.py`.
