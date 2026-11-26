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
| world/parcels.geojson | 35,172 property parcels with outline, address, lot area and perimeter (Niagara Falls Property Parcels, downloaded 2026-11-26; the owner's CSV export of the same layer matched all 34,916 of its rows) | Each building's address and lot size in the 3D city (government data) |
| world/address_points.geojson | 35,697 civic address points with parcel id | Addresses per lot (units) |
| world/zoning_79200.geojson, zoning_willoughby, zoning_crowland, zoning_humberstone | Zoning by-law areas (79-200 and the three older township by-laws): zone code, zone type, site-specific provisions | What each lot may be used for |
| world/op_land_use.geojson, op_special_policy.geojson | Official Plan Schedule A land-use designations and special policy areas | Planned use of each area |
| world/building_permits.geojson | 8,182 completed building permits 2015-2023 (type, work, address, parcel) | Change since the 2018 building outlines |
| world/development_applications.geojson | 75 current planning applications (contact names not kept) | What may be built next |
| world/condominiums.geojson | 155 condominium plans with unit counts | Homes per building |
| world/heritage.geojson | 171 listed or designated heritage properties | Building card |
| world/city_property.geojson, subdivisions.geojson, bia.geojson | City-owned property, registered subdivisions, the 6 Business Improvement Areas | Building card, areas |
| world/road_segments.geojson | 3,533 road segments (Traffic Engineering): lanes, posted speed limit, road class, bike lane | Traffic model check, road widths |
| world/intersections.geojson | 2,092 intersections with their control (stop sign, all-way stop, traffic light, yield, roundabout). City roads only: signals on Regional roads are run by Niagara Region | Traffic model, the game's signals and signs |
| world/street_lights.geojson | 11,817 street light poles: material, height (mostly feet), side of road | Street lights in the game |
| world/hydrants.geojson, bike_lanes.geojson, trails.geojson | Hydrants, bike lanes, public trails with amenities | Game |
| world/public_art.geojson, playgrounds.geojson, places_of_worship.geojson, care_centres.geojson, medical.geojson, community_services.geojson | Public art and monuments (with sizes), playgrounds, places of worship, child care, medical and community services | Game, places people go |
| world/mist_areas.geojson | Where the Falls mist settles by wind direction (City maintenance) | Mist in the game |
| world/weather.geojson, precipitation.geojson | City climate stations, Nov 2024 - Nov 2026: temperature, humidity, wind (several readings a day), and daily rain | Weather in the simulation and the game |

The `world/` layers were downloaded 26 November 2026 with `data/scripts/fetch_city_world_layers.py`.
