# Niagara Falls, Ontario — simulation data pack (compiled 2026-11-15)

- sim_parameters.json — every headline distribution in one file (residents + tourists), with source notes and estimates flagged.
- tract_profiles.csv — 21 census tracts: income, ownership, mortgages, kids, seniors, household types, tourism-worker share, mapped to neighbourhood names, with centroid lat/lon.
- census_tract_long.csv — the full 2021 Census for all 21 tracts (~156k rows: topic, field, description, tract, value; TOT_/MA_/FE_ = total/male/female).
- census_tracts_boundaries.geojson, neighbourhood_boundaries.geojson, tourist_core_boundary.geojson — map polygons (WGS84).
- licensed_tourism_businesses.csv — City of Niagara Falls 2026 licences (restaurants, food premises, tourist establishments, vacation rentals, B&Bs, amusements, parking lots) geocoded to neighbourhood. Business names/addresses only; personal contact fields removed. Licence list is a current-year snapshot and is not a complete inventory.

Sources: Statistics Canada 2021 Census via City of Niagara Falls Open Data; StatCan tables 17-10-0155, 11-10-0222/0223, 11-10-0004, 24-10-0066, 33-10-1176; Ontario Regional Tourism Profile Region 2; Niagara Tourism Profile (2017 data); Niagara Parks Annual Report 2024-25; Niagara Falls Bridge Commission 2024 Annual Review; 2022 Transportation Tomorrow Survey; Equifax Canada; Hoyes Michalos; Niagara Association of REALTORS.

## Added: who makes what (2026-11-15)
- cra_tax_by_postal_area_2021.csv — CRA income tax statistics for the 4 Niagara Falls postal areas (L2E, L2G, L2H, L2J): filers, income by $ band, by sex, by age, by source (employment, pension, investment, self-employment, benefits). Dollar values in $000. Latest edition = 2021 tax year.
- wages_by_occupation_hamilton_niagara_2025.csv — Job Bank 2025 low/median/high wages for 516 occupations, Hamilton–Niagara Peninsula region; hourly unless unit=annual; median_annual_fulltime_est = hourly x 1,950 h.
- avg_weekly_earnings_by_industry_ontario.csv — StatCan SEPH 2025 average weekly earnings by industry (Ontario).
- business_revenue_benchmarks.csv — Ontario urban small businesses (revenue $30k–$5M), 2023: average revenue, quartile averages and cut-offs, cost/wage/rent/net-profit % of revenue, joined to Niagara Falls employer counts by employee size (June 2026).
- insolvencies_by_postal_area_2023_2025.csv — OSB consumer and business bankruptcies/proposals with liabilities, by Niagara Falls postal area.

## Behaviour layer (added 2026-11-15)
- `places.csv` / `places.geojson`: 1,128 places to spend money inside city limits (OpenStreetMap, Nov 2026). Columns: osm_id, name, key/value (OSM tag), sim_category, brand, cuisine, stars, rooms, opening_hours, street, lat, lon, neighbourhood, community, in_tourist_core.
- `behaviour_parameters.json`: covers resident trip rates, purposes, time of day and mode (TTS 2022); monthly restaurant and retail seasonality indices; payment and e-commerce shares; US arrivals by month and mode (StatCan 24-10-0053); exchange-rate elasticities; visit timing; 2026 attraction, pass and parking prices.

## Research top-up: energy costs, who is where, who buys what (added 2026-11-15)
Built by `scripts/build_topup.py <pack_dir> <raw_dir>` (raw_dir holds StatCan tables 11-10-0223/0224/0227 as CSV and `raw_sources/normals_6135657.json`).
- `energy_parameters.json` — NPEI 2026 residential tariff, OEB electricity prices from 2026-11-01, Enbridge Rate 1 (EGD zone) from 2026-10-01, City of Niagara Falls 2026 water/wastewater rates, primary heating energy for the St. Catharines–Niagara CMA (2023), Niagara Falls heating/cooling degree-day normals, NRCan Ontario residential energy use (2022), Ontario utility spending by income quintile (2023). `derived_*` blocks are calculated: monthly gas/electricity shares, dwelling-type scaling, and worked monthly bills for a gas-heated and an electrically heated home.
- `tract_demographics.csv` — 21 tracts x 116 columns from the 2021 Census: age by sex in 5-year bands, household size and type, age of primary maintainer, tenure, dwelling type, period of construction, bedrooms, 19 household income bands. Counts are census-rounded to 5.
- `tract_distances.csv` — straight-line km between all 441 pairs of tract centroids.
- `places_tract_lookup.csv` — the census tract each of the 1,128 places sits in (point-in-polygon).
- `tract_place_access.csv` — per tract and sim_category: places inside the tract, within 2 km and 5 km of the centroid, and km to the nearest.
- `spending_by_demographic.json` — Survey of Household Spending 2023: 53 categories by age of reference person and by household type (Canada), and by income quintile (Ontario), in dollars and as an index against all households, with the mapping to census columns.
- `tract_growth_2021_2025.csv` — where new housing was built: new low-rise dwelling permits 2021–2023 per census tract (City of Niagara Falls "Completed Building Permits" open data, geocoded points matched to tract polygons; 1,234 units, cross-checked against the city's permits-by-neighbourhood totals of 598 / 581 / 185 for 2021 / 2022 / 2023), apartment-building permits (unit counts not published per permit) and subdivisions registered since 2019 (city "Subdivisions" open data). City-wide context: CMHC completions 385 / 846 / 590 / 534 for 2021–2024 (2024 Development & Housing Monitoring Report).
