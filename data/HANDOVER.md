# Handover: Niagara Falls, Ontario spending simulation

This is for Claude Code. Read all of it before writing any code.

## 1. What we are building

An agent-based simulation of Niagara Falls, Ontario. It has three layers:

1. **Population.** Synthetic resident households, placed in the city's real 21 census tracts, with income, housing, cars, children, jobs and debt.
2. **Businesses.** Real places from `places.csv`, plus businesses on the register that the map misses. Each has a category, a location, revenue and cost structure, and staff.
3. **Spending behaviour.** Residents and tourists decide when to go out, where to go and how much to spend. Money flows from people to businesses, to wages, and back to households.

The research phase is finished. This pack is the input data, and every number in it comes from a cited source. The next phase is code.

## 2. How the user works (follow this strictly)

- Build exactly to the spec the user gives. Don't substitute your own idea of what should be built.
- Back every "done" claim with proof: tests run, output shown, numbers checked against the targets in section 6.
- Once a scope decision is made, don't reopen it.
- No hedging and no "this is hard" framing. Agent-based models, gravity/Huff destination choice and synthetic populations are solved, documented patterns. Apply them.
- Keep disclaimers out. Put caveats in code comments or the data-gaps list, not in front of answers.

## 3. What's in this pack

All CSV files are UTF-8 with a header row. Coordinates are WGS84 (EPSG:4326).

| File | Rows | What it is |
|---|---|---|
| `tract_profiles.csv` | 21 | **Core resident input.** One row per census tract, 40 columns: population, households, household size, median/average household income (before and after tax), % of households over $100k and under $40k, low income, owner/renter, % of owners with a mortgage, dwelling value, shelter costs, dwelling types, household types, children and seniors, % female, community and neighbourhood names, centroid lat/lon, and % in the Tourist Core. |
| `census_tract_long.csv` | 155,917 | The full 2021 Census for each tract in long format (`topic, field, description, census_tract, value`). Use it for anything `tract_profiles` doesn't have: income bands, age by sex, industries, commute mode. |
| `census_tracts_boundaries.geojson` | 21 | Tract polygons. |
| `neighbourhood_boundaries.geojson` | 67 | City neighbourhood polygons (NAME, COMMUNITY). |
| `tourist_core_boundary.geojson` | 1 | The city's Tourist Core polygon (Clifton Hill, Fallsview, the Falls), about 2.3 km². |
| `places.csv` / `places.geojson` | 1,128 | **Core business input.** Places to spend money from OpenStreetMap, Nov 2026: `osm_id, name, key, value, sim_category, brand, cuisine, stars, rooms, opening_hours, street, lat, lon, neighbourhood, community, in_tourist_core`. |
| `licensed_tourism_businesses.csv` | 915 | City business licences in tourism categories (category, business, address, community, neighbourhood, in_tourist_core, lat, lon). Cross-check against `places.csv`, and use it to fill in accommodation such as B&Bs and short-term rentals. |
| `business_revenue_benchmarks.csv` | 107 | **Revenue model.** For each NAICS industry: urban-Ontario small-business average revenue, revenue quartiles and cut-offs, cost structure as % of revenue (cost of sales, wages, rent, advertising, utilities, net profit), and **the number of Niagara Falls employer businesses in June 2026 by size band** (1–4, 5–9, …, 100+). |
| `cra_tax_by_postal_area_2021.csv` | 360 | CRA income-tax statistics by Niagara Falls postal area (FSA): filers, income by source and so on. |
| `wages_by_occupation_hamilton_niagara_2025.csv` | 516 | Job Bank wages by job title (low/median/high, with an annualised full-time estimate). |
| `avg_weekly_earnings_by_industry_ontario.csv` | 165 | Ontario average weekly earnings by industry, 2025, with an annualised figure. |
| `insolvencies_by_postal_area_2023_2025.csv` | 12 | Official bankruptcy and consumer-proposal counts and liabilities by Niagara Falls postal area and year. |
| `sim_parameters.json` | — | Resident, tourism and income parameters (structure in section 4). |
| `behaviour_parameters.json` | — | Trip rates, time of day, seasonality, payment methods, US arrivals by month, exchange-rate elasticities, visit timing and 2026 prices. |
| `scripts/` | — | The scripts that built the pack. Paths are hard-coded to the old workspace, so they are reference only. |

`places.csv` uses these `sim_category` values: restaurant, fast_food, cafe, bar_pub, nightlife, accommodation, attraction, leisure_paid, casino, entertainment, souvenir_gift, grocery_food, apparel, general_merchandise, home_electronics, retail_other, personal_services, tobacco_cannabis, fuel, auto_service, car_rental, parking, pharmacy, health, bank, atm, services, office, craft, pet, childcare.

## 4. Key parameters (where to find them)

**`sim_parameters.json`**
- `residents.population`: 2021 Census count 94,415; StatCan estimate for July 2025 113,983 (scale to this); land area 210.3 km²; 37,793 occupied dwellings (2021).
- `residents.households_2021`, `sex_2021`, `age_pyramid_2021`, `income_2020` (household income bands), `housing_2021`, `labour`, `transport` (vehicles), `debt_and_insolvency`.
- `residents.spending_ontario_2023_by_income_quintile`: household spending in Ontario by income quintile (Survey of Household Spending 2023), by category.
- `residents.discretionary_spending_estimate`: discretionary spending per household by quintile: [6,158, 10,983, 13,368, 17,362, 25,329]. Niagara Falls' median household income is $74.5k against roughly $91k for Ontario, so the city skews toward the lower quintiles.
- `tourism.*`: about 13M visitors a year to the region; 2017 split by origin and overnight vs same-day; 2022 domestic spending of $2.10B and the spending mix; length of stay; where visitors stay; party size 2.63 with 25% of parties bringing children; activity shares; Niagara Parks attendance by attraction; hotel occupancy and average room rate; short-term rentals; casinos; how visitors arrive (about 90% of Ontario visitors come by car).
- `who_makes_what.*`: CRA 2021 (77,160 tax filers, $3.49B total income, average $45,275); revenue-to-payroll ratios; big public operators (Niagara Parks, casinos).

**`behaviour_parameters.json`**
- `residents.trips`: 2.29 trips per person aged 5+ per day; 48% home-based discretionary; 19.9% in the morning peak and 33.8% in the afternoon peak; 70% made driving; median driving trip 5.5 km.
- `residents.monthly_seasonality_index`: monthly indexes for restaurants and retail (average month = 1.0).
- `residents.payment`: cash 20% of purchases by count and 11% by value; online 14% by count and 21% by value; e-commerce 6.1% of retail in 2025 and 7.7% in June 2026; median debit purchase $25, credit $36.
- `tourists.us_arrivals_ontario_monthly_pct_2025`: monthly shares for overnight, same-day, by car and on foot.
- `tourists.us_trend`, `canadian_cross_border_substitution`, `exchange_rate_elasticity`.
- `tourists.visit_timing`, `prices_2026_cad_before_tax` (attractions, passes, parking).

## 5. Known gaps and how to handle them

- **Visitor household income** isn't published for the Canadian side. Use the census incomes of the places visitors come from (the Toronto area and Hamilton for Ontario visitors) as a proxy.
- **OSM coverage:** restaurants, hotels, attractions and big retail are near-complete (348 places to eat on the map vs 326 food-service employers on the register). Personal services (47 vs 74) and health retail (35 vs 65) are under-counted, so add synthetic businesses to reach the register counts in `business_revenue_benchmarks.csv`.
- **Revenue** for individual businesses isn't public. Draw each business's revenue from the industry quartiles, weighted by its size band. Tourist-core businesses should sit in the upper quartiles; calibrate against tourism spending.
- **Mortgage sizes** are estimates ($470k–$530k for recent buyers). The census gives median owner shelter cost per tract.
- **Discretionary spending by quintile** is Ontario-wide; map Niagara Falls households onto it by their income.
- **Seasonality** for resident spending is Ontario-wide. Tourist-facing businesses get the tourist arrival curve on top.

## 6. Calibration targets (the model must reproduce these)

| Target | Value | Source |
|---|---|---|
| Population 2025 | 113,983 | StatCan estimate |
| Private households (2021) | ~37,790 | Census |
| Median household income (2020 income year) | $74.5k | Census |
| Owners / owners with a mortgage | 70.4% / 59% | Census |
| Vehicles per household | 1.75 | Transportation Tomorrow Survey (Niagara Region) |
| Tax filers / total income (2021) | 77,160 / $3.49B | CRA |
| Consumer insolvency filings 2025 | 502 | Office of the Superintendent of Bankruptcy |
| Food-service employer businesses | 326 | StatCan business register June 2026 |
| Regional domestic tourism spending (2022) | $2.10B | StatCan national travel survey |
| International visitor spending (2025, Niagara Falls & Wine Country) | $1.231B | Destination Canada |
| Journey Behind the Falls attendance (fiscal 2024/25) | 917,075 | Niagara Parks |
| Hotel occupancy | 0.55 (2022) to 0.67 (2017) | Industry reports |
| Share of US overnight drive-in arrivals in July–August | 32.8% | StatCan 24-10-0053 |

## 7. Suggested build order (a proposal; the user decides the stack and scope)

1. **Data loader and validation.** Load every file, check row counts and that tract totals add up to 94,415, then scale to 113,983.
2. **Synthetic population.** Create households per tract by matching the tract totals (iterative proportional fitting) on household size, income band, tenure and children; give each household cars, debt and a discretionary budget by income quintile; give adults jobs from the labour data with wages from the wages tables.
3. **Businesses.** One agent per place in `places.csv`, plus synthetic businesses to reach the register counts. Draw revenue and cost structure from the benchmarks, and add staff and opening hours.
4. **Tourist generator.** Daily arrivals by origin and mode, shaped by the monthly curves and the day of the week; party size, length of stay, where they stay, and a budget per day.
5. **Choice model.** Decide when someone goes out (trip rates by time of day), what for (purpose shares) and where. For "where", use a Huff/gravity model: pull = attractiveness ÷ distance^β, using the 5.5 km median trip and the Tourist Core for tourists. Then decide how much they spend (ticket size and the mix of categories) and how they pay.
6. **Money flow.** Business revenue goes to wages, rent and profit; wages return to households; online and chain spending leaks out of the city.
7. **Calibration and reporting.** Tune the parameters until the section 6 targets match, and report dashboards by tract, by category and by month.

Run on a daily tick with hourly time slots; that is enough resolution for the time-of-day and seasonal patterns.

## 8. The research document

The full write-up with sources is the Claude Docs artifact "Niagara Falls" at https://claude.ai/artifact/D2HDHGhuCsaSpcsFJovHim. Each number in this pack is explained and sourced there.
