# Niagara Falls simulator

A game-like, agent-based simulation of the Niagara Falls, Ontario economy.
Plan: https://claude.ai/artifact/CvFuGLP1nByTvzvPyyTnB4

## Layout
- `data/` — the research data pack (see `data/README.md` and `data/HANDOVER.md`).
- `prep/` — Python, one-time data preparation. Builds the city the engine loads.
- `engine/` — Rust simulation engine (WebAssembly + native). Residents done; tourists and money flow next.
- `web/` — the single-page game: `index.html` plus `city.json`, `roads.json` and `nfsim.wasm` (built by `python3 -m nfprep.webexport`). Works on a phone, with pinch zoom. Residents and tourists.
- `build/` — generated output (not committed).

## Status
| Phase | State | Proof |
|---|---|---|
| 1. Data loader | Done | `python3 -m nfprep.loader` — 32/32 checks |
| 2. Synthetic population | Done | 16 tests against the calibration targets |
| 3. Businesses and roads | Done | 21 tests: employer counts match the register; road network fully drivable |
| 4. Engine and resident spending | Done | 9 Rust tests: survey trip length and rate, seasonality, budget conservation, flow = agent, determinism, rewind, routes on real roads, one year in about 11 s |
| 5. Tourists | Done | 5 more Rust tests: 11.05M visitors, Journey Behind the Falls 923,057 (published 917,075), six other attractions within 3%, US overnight July-August share 33.0% (32.8%), spending matches inputs. Hotel occupancy comes out at 43%, below the published 55-67%: recorded as a data gap, not tuned away |
| 7. The game (first version) | Running | Single page on a phone: real roads, businesses sized by revenue, resident and tourist trips moving, tap for details, four speeds |
| 8. Place a business (first version) | Running | Tap Build, pick a kind, tap the map: yearly revenue and profit at three sizes, tourist share, and the businesses that lose trade. Build it and it trades. 3 Rust tests: the forecast takes trade and creates none; it matches what happens once built (within 1%); location changes it |
| 9. Backtest (first run) | Run; the model did not pass | See "Backtest 2021 to 2026" below |
| 6, 10 | Not started | |

Tests: `python3 -m pytest` (37) and `cd engine && cargo test --release` (17). A full simulated year is summarised in `docs/run_2025_full_year.json`. A picture of the generated city is in `docs/city_2025_map.png`.

## Run
```bash
pip install numpy pytest matplotlib
```
```bash
cd prep && python3 -m nfprep.loader
```
```bash
cd prep && python3 -m nfprep.build 2025 1
```
```bash
python3 -m pytest
```
```bash
cd engine && cargo build --release && ./target/release/nfsim run ../build/city_2025/city.json --mode agent --days 365
```
```bash
cd engine && cargo test --release
```
`nfsim calibrate <city.json>` tunes the distance parameter; `--mode flow` runs a year in milliseconds.

`nfprep.build <year> <seed>` builds the population for any year from 2019 to 2025, plus businesses and roads, into `build/city_<year>/`.

Road data © OpenStreetMap contributors (ODbL).

## Known simplifications in phase 2
- Residents of collective dwellings (about 1.5%) are represented as private households.
- Each employed person gets the Ontario average earnings for their industry; household income comes from the census income bands, not from summing those earnings.
- Employment uses Ontario 2025 rates by age (the May 2021 census labour figures were distorted by lockdown).
- Discretionary budgets by income quintile are assigned in phase 4, with the spending model.

## Known simplifications in phase 3
- Businesses in industries that do not sell to households (construction, manufacturing, trucking and others) are not individual records yet; they enter in phase 6 as employers.
- The business list is today's. The 2019 starting list for the backtest is built in phase 9.
- Synthetic businesses are placed beside existing shops outside the Tourist Core; their exact sites are not real.
- Opening hours come from the map where given (142 places) and from category defaults otherwise.

## Phase 4 findings and simplifications
- The travel survey's 5.5 km median driving trip cannot be produced by trips inside the city (the median is 5.25 km even if distance is ignored). Neighbouring towns were added as out-of-town destinations; with them the distance parameter calibrates to about 1.49.
- That calibration sends about 30% of residents' in-person spending out of town ($442M of $1,477M). This share is an output of matching the survey, not a measured figure, and is the first thing to check in the backtest.
- Store pull uses format weights (a supermarket against a corner store) that are priors, marked SEED in `prep/nfprep/businesses.py`.
- Spend-per-visit ratios between categories are priors; they affect how many trips go where, not revenue.
- Tourism share decision recorded in `data/tourism_city_share.json` (75–85% of regional spending), to be audited against accommodation-tax receipts in phase 5.

## Running the game locally
```bash
cd engine && cargo build --release --target wasm32-unknown-unknown --lib
```
```bash
cd prep && python3 -m nfprep.build 2025 1 && python3 -m nfprep.webexport 2025 1.4928
```
```bash
cd web && python3 -m http.server 8765
```
Then open http://127.0.0.1:8765/index.html. The published version is at https://claude.ai/artifact/HcMmPCXM4AyL4CzrXfvz3M.

## Data checks raised so far
- Hotel room rate implied by the travel survey's spending mix is about $371 a night, against a published average of $194 (2022). One of the inputs (spending total, share spent on accommodation, people per room, or nights) does not fit the others.
- Hotel occupancy implied by visitor counts and 13,000 rooms is about 43%, below the published 55% to 67%.

## Place a business: what the forecast is and is not
- Revenue is the newcomer's share of existing resident and tourist spending at that spot. Total spending in the city does not rise.
- "Small / typical / large" are the industry's lower-quartile average, median and upper-quartile average revenue; they set how strongly the business pulls customers.
- Profit uses the industry's average cost shares: goods and labour rise with sales, other overheads are sized for the scale chosen.
- Not in this version: survival odds, a city-wide heat map, side-by-side comparison, accommodation and casinos. A remote site still forecasts a sizeable revenue (about $200k for a restaurant at the rural south edge), which suggests the distance effect is too gentle far from town; to be checked in the backtest.

## Calibration against government figures (17 November 2026)
Sources and method are in `data/calibration_benchmarks_ontario_2025.json`; results in `data/calibrated_multipliers.json`.
- **What each store type sells in the city** is estimated as Ontario sales per employee (Statistics Canada retail and food-service surveys, employment survey) times that store type's employees in Niagara Falls (business register size bands).
- **Household retail demand** is scaled to Ontario retail sales per resident by store type. The household spending survey alone covers about two thirds of retail sales.
- **Neighbouring towns** (St. Catharines, Welland, Thorold, Niagara-on-the-Lake, Fort Erie) both take spending from city residents and send shoppers in.
- **Fitted:** how strongly the towns pull, per category, and what share of tourists' surveyed spending on restaurants (61%), fuel (32%) and clothing (23%) lands at city businesses. All eight store types then match their targets within 1%.
- **Not identified by the data:** one sales figure per store type cannot separate "more leakage" from "less tourist spending in the city"; the correction is shared between them.
- **Not calibrated:** hotels, attractions and casinos (too many 100+ employee firms for the size bands to pin down), services and health care (no sales survey).
- Every mapped business now carries the nearest civic address from the city's address points, or its licence address.

## Traffic counts (17 November 2026)
- `data/traffic_counts_provincial_highways.json`: Ontario Ministry of Transportation counts (AADT, 2005-2019) for the QEW and Highways 420, 405, 58 and 20 around the city.
- The engine can count simulated vehicles on every road (`nfsim run ... --traffic FILE`); `python3 -m nfprep.traffic_check` compares them with the counts. Result: `docs/traffic_check_2025.json`.
- Visitors' cars now enter through five gateways (QEW north and south, Rainbow, Queenston-Lewiston and Whirlpool bridges).
- **What the counts changed:** shoppers from Fort Erie and Welland were generating more traffic on the QEW south than was counted in total. Town households now put 12% of their spending in play for other towns (fitted), which cut inbound spending from $737M to $245M. Simulated shopping, dining and visitor trips now make up 11-60% of counted traffic on every section, leaving room for the commuting, trucks and through traffic the model does not simulate.
- **Still missing:** Niagara Region's counts for Lundy's Lane, McLeod Road, Stanley Avenue and the other regional roads. Its portal blocks automated downloads; download the 2025 CSV from https://niagaraopendata.ca/dataset/regional-road-traffic-volumes and add it to `data/` to extend the check to city streets.
- One store type still misses its target after calibration: specialty and miscellaneous retail (459) reaches $226M against $311M.

## Backtest 2021 to 2026 (first run, 17 November 2026)
`python3 -m nfprep.backtest` rebuilds the city with the 485 named restaurants and shops that were on OpenStreetMap in central Niagara Falls on 1 January 2021, scores each with the model, and checks which had gone or been replaced by July 2026 (38 had, 7.8%). Full results: `docs/backtest_2021_2026.json`.

| Test | Pass mark | Result |
|---|---|---|
| Closed business scored below a surviving one (expected revenue, ranked within its kind) | 70% | 49%: no better than a coin flip |
| Closure rate in the model's weakest tenth | 15.7% (double the average) | 10.4% |

- **The model did not predict which businesses closed.** Its score describes how well a site fits the city's normal spending. The closures of 2021-2026 were not about that: 18 of the 38 were inside the Tourist Core, the strongest sites in normal times.
- **Reliance on tourists did carry a signal.** Businesses the model says get over 60% of sales from tourists closed at 10.4%, against 6.1% for those under 25% (ranking score 61%). That fits the border closure of 2020-2021, which this run did not feed in.
- **Why the test is weak:** only 38 closures; OpenStreetMap misses many closures and some "gone" entries are rebrands or map clean-up; each business's size is assigned by the model at random within its industry, not observed; demand is 2025, not year by year.
- **What would make it a fair test:** run the years 2020-2022 with the real collapse in visitors; business licence lists by year from the city for reliable openings and closures; real sizes (seats, floor area) where available.

## Hotels, outlooks and city-street traffic (17 November 2026)
- **Hotel occupancy 2025** (`data/hotel_market_2025.json`, supplied by the project owner from CBRE, Niagara Economic Development, Hotelier, Horwath HTL and HVS): 16,000 rooms. `python3 -m nfprep.hotel_fit` fits two numbers to March (47.4%) and January-July (59.5%): parties take 1.15 rooms on average, and 22% of rooms are filled every night by stays the travel survey does not count (business, groups, long stays, contracts). Simulated: March 47.4%, January-July 58.7%, full year 61.4% (published about 63%, not fitted), July 90%. Room rates run from $123 (January) to $372 (July), through the published $149.66 (March) and $206.28 (January-July).
- **Outlooks** (`data/scenario_ranges.json`): forward runs use low, expected and high multipliers on the calibrated 2025 year for US, domestic and overseas visitors, hotel stays outside leisure travel, and resident spending. US demand has the widest band (-20% to +10%) because it is the most exposed to political decisions. `nfsim run ... --scenario us,domestic,overseas,base_stays,resident`.
- **Regional road counts** (`data/traffic_counts_regional_roads.json`, Niagara Region 2018, supplied by the project owner): 67 counted sections in the city. Simulated shopping, dining and visitor traffic is a median 20% of counted traffic (middle half 11-32%), and follows the pattern of counted volumes with correlation 0.56. The comparison found that St. Catharines shoppers were routed down Taylor Road at four times the counted traffic; towns now enter the city at their real entry points.
- **Known traffic gaps:** almost no simulated traffic on the Queenston-Lewiston bridge approach (Highway 405, Niagara Townline Road, north Stanley Avenue), so US visitors arriving by that bridge are not reaching those roads in the model; and Mountain Road near the QEW carries more simulated visitor arrivals than its counts allow.

## Backtest, second run: through the pandemic years (17 November 2026)
The same 485 businesses were run through a pandemic year: US and overseas visitors at 10% of normal, domestic visitors at 62%, residents at 85%. That gives hotel occupancy of 39%, above the published 28% (2020) and 34% (2021): the model keeps the 22% of rooms filled by non-leisure stays unchanged, and in the pandemic those fell too. Visitor demand is therefore cut less than it really was.

| Test | Result |
|---|---|
| Closed businesses kept less of their revenue in the pandemic run than survivors did (ranking score) | 61% (pass mark 70%) |
| Closure rate where the model says a business keeps under 60% of normal revenue | 11.5% (22 of 192) |
| Closure rate where it keeps 60-80% | 5.0% (9 of 179) |
| Closure rate where it keeps 80% or more | 6.1% (7 of 114) |

Businesses the model marks as most exposed to a collapse in visitors closed at about twice the rate of the rest. That is a real signal, but too weak to rank individual businesses: the test still fails its 70% pass mark. The same weaknesses apply (38 closures, closures read from the map, random sizes).

## City open data added (17 November 2026)
The project owner supplied the city's open data catalogue (362 datasets). Downloaded from it: building footprints, capital projects 2022-2026, tourism districts and municipal parking lots (`data/city_open_data/`). Most of the rest are maps of city assets (hydrants, sewers, trees, election results) with no bearing on spending.
- **Real floor areas:** 1,127 of 1,283 real businesses now sit in a city building footprint; floor area (shared among tenants, times storeys) replaces part of the random element in each business's size. The backtest barely moved (ranking 49%, pandemic exposure 62%), so business size was not what limited it.
- **Actions over words** (`python3 -m nfprep.capital_check`, `docs/capital_spending_check.json`): the city's $210.5M of road and works projects 2022-2026, by census tract, compared with the model. Rank correlation with simulated traffic 0.43, simulated visitor traffic 0.38, new homes built 2021-2023 0.33, population -0.17. Spending lines up best with where the model puts traffic, but with only 21 tracts every one of these is moderate at best.

## CBRE monthly hotel figures (17 November 2026)
CBRE Hotels' monthly National Market Reports carry a Niagara Falls row and are posted publicly by Destination Northern Ontario. Seven were found (January, June, July, October 2024; January, March, November 2025); they are in `data/sources/cbre/` and summarised in `data/hotel_monthly_cbre.json`. The hotel model is now fitted by least squares to six 2025 figures:

| Period | Published | Model |
|---|---|---|
| January | 36.8% | 37.0% |
| March | 47.4% | 44.0% |
| November | 45.3% | 50.3% |
| January-March | 41.6% | 40.1% |
| January-July | 59.5% | 56.9% |
| January-November | 63.2% | 60.8% |

Room rates fit within about $5 of the published averages. November is the weakest month (model 5 points high): November 2025 fell 11.7 points from November 2024, a drop the model's monthly visitor pattern does not contain.

Still not found anywhere public: business licence lists by year (only the current year is published) and Niagara Falls Municipal Accommodation Tax receipts by year.

## Filling in missing names and addresses (17 November 2026)
- **Real businesses added:** 111 from the city's current licences (repair shops, estheticians, hairdressers, car dealers) and 2 clinics from a wider OpenStreetMap pull. Each replaces one stand-in, so stand-ins fell from 392 to 338. The wider pull reached into Niagara Falls, New York; anything outside the city's census tracts is now dropped.
- **Names and addresses:** the address comes from the source record, then OpenStreetMap, then the city licence whose parcel the place sits in, then the nearest civic address within 150 m. A missing name is taken from the licence on the same parcel. Real places with no address: 155 -> 28; with no name: 55 -> 27.
- **Stand-ins are marked:** they are drawn as rings, not dots, and their panel says "Estimated ... (not a real business)". Most remaining stand-ins are health offices (200) and pharmacies (40): the register counts far more than any public list shows.
- **Corrections:** `data/local_corrections.json` takes businesses people know about (name, kind, and an address from the city's address list, or coordinates). Each one becomes a real business and replaces a stand-in.
- Statistics Canada's Open Database of Businesses would fill much of the gap, but its download page was unavailable on 17 November 2026.

## Public operators and business counts (17 November 2026)
- **Niagara Parks** (`data/public_operators_2025.json`, annual report 2024/25 in `data/sources/operators/`): $145.8M from its own operations. About $16M is set aside for WEGO and the Falls Incline Railway (estimate), and the 20 Niagara Parks places on the map are fitted to the remaining $129.8M (model $129.9M). Its tenants' payments (boat tours $19.2M, zipline $3.0M, land rent $8.8M) are recorded but not modelled.
- **Casinos:** OLG does not publish revenue by site. Gaming revenue of about $550M is estimated from the city's $18M hosting fee through Ontario's fee formula (rates quoted from memory; to be checked), consistent with the government's "over $500 million". Casino gaming is added as visitor demand on top of the travel survey; the two casinos now earn $528M (was $46M).
- **Business counts by industry** (`data/business_counts_niagara_falls_2020_2026.json`): Statistics Canada counts of businesses with employees in Niagara Falls, by industry and size, every six months from June 2020 to June 2026 (13 editions). Food services fell from 304 to 269 in the first pandemic year and recovered to 326-332; hotels and motels fell from 88 to 74 and stand at 76; clothing stores fell from 39 to 30 since December 2022.
- **Industry-level check** (`docs/industry_check_2020_2021.json`): the industries the model's pandemic run says keep the least revenue are the ones that lost the most employer businesses from June 2020 to June 2021 (rank correlation 0.83 over 6 industries). Personal services is the exception: the model expects losses, the count rose 8%.
- `python3 -m nfprep.calibrate` now runs the whole calibration chain. Engine tests: 19.
