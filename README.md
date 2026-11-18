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

## Niagara Parks: free parkland and paid parking (17 November 2026)
- Recorded from the project owner's research (in `data/public_operators_2025.json`): Niagara Parks is a self-financing Ontario agency; its parkland, gardens, viewpoints, trails, illuminations and more than 60 parking areas are free, and paid attractions, parking, WEGO, food, retail, golf and leases pay for them. Expenses 2024/25 $131.8M; surplus before interest and depreciation $51.5M. Planned: Toronto Power Generating Station hotel redevelopment (about $300M, private lease) and a $19M marina redevelopment, recorded for future outlooks but not yet simulated.
- The "Where the Commission's Dollars Came From" chart's $71.5M for shops, restaurants and attractions is 66% of that chart's own total, not of the $145.9M from revenue-producing operations; the model is fitted to the $145.9M.
- Five Niagara Parks paid lots added (`data/niagara_parks_places.json`): Falls Parking (Lot A), Falls overflow, Rapidsview (Lot C), Dufferin Islands and the Butterfly Conservatory lot. Two are inferred from position and should be checked.
- **Split fixed:** admissions are fitted separately to an estimate of $55.8M (2.15M paid visits x about $26 realized; `data/public_operators_2025.json`) by fitting paid attraction visits per visiting party per day (now 1.82; it was a placeholder of 1). Niagara Parks' other places are fitted to the remaining $74.0M. Result: admissions $55.8M, gift shops $28.6M, restaurants and food $22.0M, parking $18.0M, golf $4.2M, other $1.2M. Attendance still matches all seven published figures within 0.1%.
- **Free sights are destinations** (`data/free_sights.json`): viewpoints, free gardens and spots on the map, plus Queen Victoria Park, Dufferin Islands and Niagara Glen, are now "free sights" that visitors go to (1.5 visits per party per day, SEED) and that earn nothing. Before this, 21 viewpoints and several free gardens were counted as paid attractions and took a share of ticket spending. Busiest in a year: Table Rock 10.1M visits, Botanical Gardens 2.8M (looks high), falls lookout 2.3M, Clifton Hill 1.5M, Queen Victoria Park 1.1M. Pull weights are estimates; no public visitor counts exist to check them. Visitors walking past shops on the way is not yet a source of spending.
- Unnamed places no longer borrow the name of a licence on the same parcel unless the licence is for that kind of business (a viewpoint had been named after a Starbucks).

## Year-by-year check, 2020-2025 (17 November 2026)
`python3 -m nfprep.yearly_check` runs the calibrated city once per year with that year's conditions (`data/yearly_conditions.json`: visitors moved with published hotel occupancy, US and overseas visitors with arrival data and the border closure, residents lower in 2020-2021) and compares each industry's revenue change with the change in businesses with employees by the next June (Statistics Canada counts). Results in `docs/yearly_check_2020_2025.json`.
- **Direction right in 25 of 30 industry-years** (6 industries x 5 years); rank correlation 0.42 with residents' spending scaled by each year's population estimate (0.40 without).
- Counts move far less than revenue: the 2022 rebound (+43% to +59% revenue in visitor industries) came with only -6% to +8% change in business counts. Businesses survive a bad year or two on savings, support programs and debt, and new ones take time to open.
- **Misses:** hotels kept shrinking in 2021-22 although the model has revenue recovering (-6%); amusement and gambling fell in 2022-23 despite the rebound (-6%); food stores did not track the model in 2023-2025. Scaling residents' spending by each year's population fixed health offices (model +6% and +3% in 2024 and 2025, counts +9% and +5%).
- What this means for forecasting: the model is a reliable guide to which kinds of business gain or lose when conditions change, and in which direction, but not to how many businesses open or close; that depends on how long owners can carry losses, which no public data shows.

## Cash balances and closures (17 November 2026)
`prep/nfprep/survival.py` gives every business a yearly profit and loss from the model's revenue under each year's real conditions and its industry's cost structure: wages tied to Ontario's real minimum wage (deflated by Ontario CPI), debt costs following the Bank of Canada prime rate (2.45% in 2021, 6.94% in 2023), wage and rent subsidies in 2020-2021, the CEBA loan in 2020 and its repayment by January 2024 (`data/economic_indicators.json`). Owners draw the normal profit; the cash balance takes the rest; a business closes when cash runs out. 300 runs per business with different margins, debts and cash buffers.
- **Industry counts 2020-2026** (`python3 -m nfprep.survival`, `docs/survival_check_2020_2026.json`): no improvement. Error 0.094 against 0.089 with closures at a flat base rate. Adding temporary lay-offs (businesses dropping off the payroll count without closing) did not help either. What the counts show (restaurants 304 in June 2020, 269 in June 2021, then a steady climb) depends on the timing of lockdowns within the year and on how long Statistics Canada keeps a business on its register after its last payroll, which yearly data cannot see.
- **Individual businesses** (`docs/survival_closure_test.json`): the chance of running out of cash in 2021-2025 ranks the 38 closures among the 485 businesses of 2021 at 63% with a 3-month cash buffer (61% with 1 month, 61% with 6). Best individual result so far (pandemic exposure alone 61-62%, site demand 48-49%), still short of the 70% pass mark. Businesses that closed had about 1.5 times the model's chance of running out of cash.
- Not changed: the forecasts on the page. The cash model is a separate layer for scoring closure risk.

## Outlooks from monthly indicators (17 November 2026)
`data/monthly_indicators.json`: monthly border crossings at the Niagara ports of entry 2010 to August 2026 (Statistics Canada 24-10-0053-01: US residents overnight and same-day, Canadians returning from the US, overseas residents), the US dollar in Canadian dollars and the prime rate (Bank of Canada), and Toronto pump prices (Statistics Canada 18-10-0001-01). `python3 -m nfprep.outlook` turns them into `data/scenario_ranges.json`. The page now offers "2025, calibrated", "Next 12 months" and its low and high ends.
- **Latest months**: US overnight visitors January-August 2026 were **1.039** times the same months of 2025 (same-day 1.079; still 0.84 of 2019). Canadians' trips to the US are 1.012 times 2025, after falling to 0.699 in 2025, so the domestic shift of 2025 is holding: domestic stays at 1.0.
- **US range 0.907-1.134**: 1.039 times the 10th-90th percentile of the yearly change in ordinary months (0.961-1.091; the pandemic window is left out), with the low end lowered to a repeat of 2025's drop (0.882).
- **Out-of-sample check** (`docs/outlook_check.json`): from each August, a band built only from earlier months held the next twelve months for US overnight visitors in **7 of 9** years (80% expected); misses were 2015 (+9.9%, weak Canadian dollar) and 2024-25 (-5.7%, trade tensions). Same-day visitors: 6 of 9.
- **Exchange rate**: a weak predictor. Monthly year-on-year changes in US overnight visitors against the US dollar, 67 ordinary months: elasticity 0.84, correlation 0.15. It is kept in the data file, not used in the bands.
- **Judgement calls** (SEED in `outlook.py`): the domestic, overseas, base-stay and resident bands. The Niagara land ports see few overseas visitors, and no monthly series measures the others for the city.

## Check against monthly business openings and closings (17 November 2026)
`data/business_dynamics_statcan.json`: Statistics Canada's experimental monthly openings and closings (table 33-10-0270-01, seasonally adjusted, January 2015 to July 2026). St. Catharines-Niagara is published for all industries only; industry detail is for Ontario. A "closing" is a business that had payroll last month and none this month, so most are temporary. `python3 -c "from nfprep.survival import rate_check; rate_check()"` writes `docs/survival_rate_check.json`.
- **Closings, St. Catharines-Niagara, yearly rate**: 2019 0.49; 2020 0.68; 2021-2023 0.53; 2024 0.54; 2025 0.59; 2026 so far 0.59. Active businesses fell from 9,079 (December 2024) to 8,906 (February 2026), then recovered to 9,000 by June 2026, when openings caught up. January 2025 had the highest monthly closing share since 2020 (5.5%).
- **Cash closures, wrong timing** (correlation -0.42 across 2020-2025; 2 of 5 year-to-year moves right): with subsidies and the emergency loan, the model's businesses run out of cash in 2021-2022, not 2020, and the model does not see the 2025 rise. Industry order of 2020 cash closures against Ontario's 2020 closing rise: rank correlation -0.80.
- **Staff shedding, right timing**: the share of the model's businesses whose revenue falls below half of 2019 tracks the closing rate across 2020-2025 with **correlation 0.91** (0.60, 0.51 and 0.50 at 60%, 70% and 80% of 2019; all four thresholds tried are reported). Most of that comes from 2020.
- **Industries, not matched**: the order of the model's 2020 revenue drops against Ontario's 2020 closing rise has rank correlation -0.10. Personal services (Ontario closings up 2.0 times) were closed by public-health order, which a demand model cannot see; hotels (1.2 times) kept staff on with few guests.
- **What is missing** for timing: the public-health orders by industry and month (2020-2021), and a cause for the 2025 rise that hit the whole province (Ontario exits 0.21 in 2024, 0.26 in 2025, 0.28 in 2026 so far), not only tourism.

## Pass-by traffic and clusters (17 November 2026)
- **Pass-by**: drivers already on the road who stop at a gas station or drive-through. Shares from the Institute of Transportation Engineers' Trip Generation Handbook (`data/pass_by_shares.json`): gas station with convenience store 56%, fast food with drive-through 50%, pharmacy with drive-through 50%, supermarket 36% (weekday PM peak averages; whole-day shares are likely lower). `python3 -m nfprep.passby <city.json>` counts the vehicles passing every road node in a four-week run (now part of `nfprep.build` and the backtest) and stores them in city.json. The engine gives each business a site factor `(1 - p) + p x passing / average for its kind`, with a site's traffic counted up to twice the average (SEED: pumps, lanes and parking cap how many passing drivers one business can serve). It applies to the city's businesses, visitors' choices, forecasts and businesses added on the page.
- **Effect, 2025**: category totals hold (all spending $4,856.5M before and after; gas $201.3M to $201.9M; fast food $197.0M to $207.4M, mostly from restaurants, $372.5M to $364.8M; food service combined +0.5%). Within a kind, revenue moves to busy roads: gas stations from 0.46 to 1.31 times their previous revenue (10th-90th percentile).
- **Closure check, 2021-2026**: no change. Ranking of closures by revenue against size within each kind: 0.551 without pass-by, 0.551 with; the four pass-by kinds had only 6 closures (0.488 to 0.493).
- **Clusters**: not put into the forecasts. Among the 485 businesses of 2021, those with more of their own kind within 250 m closed slightly less often (ranking 0.57 overall, 0.59 for shops, 0.53 for restaurants), within noise for 38 closures (about one standard error). The forecast now reports the number of same-kind businesses within 250 m and the vehicles passing the spot.
- **Data gap**: the three Stanley Avenue gas stations (Esso 5486, Mobil 5687, Pioneer 5619) come out at $26-31M a year with pass-by ($19-23M before). That is high for a single station. The model's traffic is only shopping and visitor trips, which crowd onto Stanley Avenue; commuting and through traffic would spread exposure onto Lundy's Lane, McLeod Road and Thorold Stone Road.

## Closure probabilities, properly scored (17 November 2026)
`python3 -m nfprep.closure_risk` fits a small logistic model on the 485 businesses on the map in January 2021 (38 gone by July 2026), with ten-fold cross-validation so every probability comes from a model that did not see that business. Results in `docs/closure_risk_check.json`.
- **Baseline** (everyone at the overall rate, 7.8%): Brier score 0.0728. Each kind's own rate, shrunk toward the overall rate: 0.0724 (skill +0.6%).
- **Full model** (kind rate, site demand rank, revenue against size, visitor share, Tourist Core, same-kind neighbours, share kept in the pandemic): Brier 0.0768, **skill -5.4%**. It is overconfident: businesses it gave 31-44% closed at 16%.
- **All 16 variants** (4 penalties x 4 feature sets) are listed in the check file. The best, kind rate plus Tourist Core plus same-kind neighbours with a strong penalty, reached skill +1.3%, picked from 16 so optimistic. Its ranking score is 0.56.
- **Conclusion**: with 38 closures, nothing the model knows about a site predicts closure beyond the kind's own rate. The page shows that rate (`data/closure_rates_2021_2026.json`, added to city.json by the export) on every business sheet and forecast: souvenir shops 15%, restaurants 12%, bars 11%, other shops 11%, electronics 10%, grocery, apparel, cafés and auto repair about 5%. These are closures seen on the map; edits lag, so real rates are likely higher.
- **What would change this**: more labelled outcomes (City business-licence renewals by year, or a second map period), and Statistics Canada exits by industry for the metropolitan area if they are ever published.

## Traffic, foot traffic, site sales and the scorecard (17 November 2026)
The pandemic years 2020-2022 are left out of the scoring: they were not predictable.

**Traffic layers** (`python3 -m nfprep.traffic_layers`, `docs/traffic_layers_check.json`). Every layer is sized from the census or from counts at the city's edges; the count stations inside the city are the test.
- Commuting (2021 census): 7,473 resident round trips a day inside the city, 4,885 to other Niagara towns, 1,127 beyond the region, 8,239 workers driving in to fill business jobs (SEED: residents fill 60% of the model's business jobs).
- Trucks from the QEW edge counts (4,400 north-south; 2,400 to the Queenston-Lewiston Bridge, matching the Bridge Commission's 391,500 a year).
- Bridge cars: the Bridge Commission's 2.38 million a year west-bound (2023-24), both directions, less the US visitors the model already sends (`data/bridge_crossings_nfbc.json`).
- QEW through cars: 24,872 a day, what the south-edge count leaves.
- Routes spread over 12 draws of road times (lognormal spread 0.3), as drivers do not all take one fastest route.
- Result on 77 sections: regional roads correlation 0.62 (0.61 with shopping trips only, 0.59 without spreading), highways 0.75, highways carry 48% of counted traffic (29% before). Regional roads still carry only 24.5% of their counted traffic: school trips, trips between stops, trips by other towns' residents and deliveries are not simulated.
- Fixed on the way: north-bound traffic was first tied to the nearest road node to the QEW join point, which is on Taylor Road, and sent 35,000 vehicles a day down Taylor Road (5,900 counted). It now uses St. Catharines' town node on the QEW.
- Passing traffic at every business (pass-by) now includes commuters and through traffic.
- The Region's 2025 road counts are behind a bot check on niagaraopendata.ca; a manual download would add a newer test.

**Foot traffic** (`python3 -m nfprep.foot_traffic`): a full-year run counts residents' walked trips, visitors' stops reached on foot, and visitors who drove counted at the door they walked to. Busiest: Clifton Hill about 33,900 people a day, Table Rock 29,300 (10.7 million a year), the boat dock 19,000. No public pedestrian counts exist for Niagara Falls; the City provides counts on paid request, which would be the test.

**Map**: "Show" switches between businesses, cars a day and people on foot a day. Tapping a street gives both, and the share from commuters and through traffic. Forecasts give the people walking past the spot.

**Sales at a site** (`python3 -m nfprep.unit_sales_check`, `data/public_unit_sales_2025.json`): branches in Niagara Falls against published sales per location. Pizza Pizza 1.66 times the national average, The Keg 0.94, Boston Pizza 1.47, Tim Hortons 1.07 (median of 16). Median 1.27, plausible for a tourist city. Three Tim Hortons in the Tourist Core ($4.5-6.4M) are above the brand's best unit ($4.0M): the model over-concentrates visitor spending on fast food in the core (the Fallsview food court's Subway at $10.3M). McDonald's, Subway, A&W and Dollarama publish no Canadian per-location sales.

**Ranges**: forecast ranges now combine the outlook range with the site error measured above (x0.66 to x1.51, P10-P90 from the 4 chains, `data/forecast_error.json`), and profit has a range too.

**Scorecard** (`python3 -m nfprep.scorecard`, `docs/scorecard.json`), 5 of 9 pass:
| Test | Score | Mark |
|---|---|---|
| Regional roads, correlation | 0.62 | 0.6 pass |
| Highways, correlation | 0.75 | 0.6 pass |
| Regional roads, share of counted traffic | 24.5% | 50% miss |
| Chain branches against published sales | 1.27 | 0.67-1.5 pass |
| Branches above the brand's best unit | 3 | 0 miss |
| US visitor band, out of sample | 7 of 9 | 70% pass |
| Industry direction 2023-2025 | 14 of 18 | 70% pass |
| 2025 rise in closings | not seen | miss |
| Closure chance skill | -0.054 | 0.05 miss |

**Frozen forecasts** (`python3 -m nfprep.frozen make|score`, `forecasts/2026-11-17.json`): US overnight visitors through Niagara, 12 months to August 2027: 1.026 times the year before (0.873-1.092); same-day 1.017 (0.927-1.110); Canadians returning from the US 0.953 (0.812-1.078); active businesses in St. Catharines-Niagara, July 2027: 1.0066 (0.989-1.028). Never edited; scored when Statistics Canada publishes.
- Tried and reverted: a separate minimum trip time for visitors (3, 5, 8 minutes instead of 1), on the idea that visitors on foot treat everything nearby as equally near. Every chain moved further from its published sales (median ratio 1.27 to 1.40, 1.59, 1.68) and the three Tim Hortons stayed above the brand's best unit. The parameter (`params.tourist_min_minutes`) stays in the engine at its default (the residents' 1 minute). The three are at Table Rock, the Fallsview casino and Murray Street, plausibly among the busiest in Canada; the "best unit" figure is second-hand and its scope (Canada or US) unconfirmed.

## Safety: places people avoid (17 November 2026)
`data/safety_hotspots.json` lists places where the Niagara Regional Police or the City have documented open-air drug use, repeated calls for service or encampments, each with its sources: Main Street and Ferry Street (police open-air drug initiative, July 2026; trafficking arrest near the shelter, May 2026), the Summer Street shelter (74 police calls in four months, 2024), Buchanan Avenue (transitional housing, council told crime is rising) and Thundering Waters Boulevard (encampment concerns).
- **Effect** (SEED, no study measured it for Niagara Falls): a business's pull falls by up to 30% for visitors and 10% for residents at Main and Ferry (20%/10% Summer Street, 15%/5% Buchanan, 10%/5% Thundering Waters), fading as exp(-distance / 200-250 m). Health and recovery services are exempt. The engine applies it to residents' and visitors' choices and to forecasts (`City::safety_factor_at`).
- **Result, 2025**: city spending unchanged ($4,857.3M); businesses within 80 m of Main and Ferry lose 13-24% (a restaurant -24%, a grocery -19%); the trade moves mostly to the supermarkets elsewhere.
- **Outlooks**: low = the problem worsens (penalties x1.5), high = enforcement and the City's Unhoused Action Plan work (x0.5).
- **Local check** (2021 businesses, closed by July 2026): within 600 m of Main and Ferry 1 of 26 closed (city 7.8%); within 1 km 11 of 67 (16%, p = 0.015). Mixed, and the problems were documented in 2024-2026, after most of that period; not enough to set the strengths.
- **City level**: Crime Severity Index St. Catharines-Niagara 54.7 in 2023 (Region dashboard), Canada 75.0 in 2025: the effect is local, not city-wide.
- **Page**: "Show: Safety concerns" shades the places; business sheets and forecasts within reach say how much pull is cut.
- **Better data**: the police crime map (CityProtect, incidents to the 100 block) could not be downloaded automatically; an export of it would replace this hand-made list and allow the strengths to be measured.
- Walking routes still follow the fastest road; people walking around a spot rather than through it is not yet modelled.

## Niagara Region road file (17 November 2026)
The Region's road network file (`data/sources/region/niagara_region_roads_opendata.csv`, 23,817 segments, 4,493 in or bordering Niagara Falls) has posted speed limits, lanes, one-way flags and road owner, but no shapes and no traffic counts. Speed limits are filled for regional and provincial roads only (750 of 4,493 segments); lanes for 736.
- `data/region_road_speeds.json`: each street's posted limit and the share of its length at that limit; 23 of 32 streets have one value over at least 80% of their length (e.g. Sodom Road, Thorold Townline and Schisler Road 80 km/h, Kalar and Taylor 70, Montrose and Mountain 60, where the model used 50).
- Tested: applying them changed 442 road edges and made the fit to counted traffic worse (regional roads correlation 0.62 to 0.61, highways 0.75 to 0.70, highways' share of counted traffic 48% to 42%); with the QEW at a 110 km/h operating speed as well, 0.57 and 0.70. The model has no traffic signals or congestion, so posted limits overstate speeds on signalled arterials and draw trips the wrong way. Switched off (`USE_REGION_SPEEDS` in `prep/nfprep/roads.py`) until intersection delay and congestion (from the file's lane counts) are in.

## 2025 regional road counts (17 November 2026)
The Region's 2025 traffic volumes (`data/sources/region/regional_road_aadt_2025.csv`, 441 sections region-wide, supplied by the user; the open-data page is behind a bot check) have no geometry, so each section takes its road line from the earlier shapefile by station ID: all 69 sections in the city matched, all counted in 2025 (`data/traffic_counts_regional_roads_2025.json`). At the same stations, 2025 traffic is a median 8.9% above the earlier counts (correlation 0.97).
- Model against 2025 counts: regional roads correlation **0.65** (0.62 against the earlier counts); the model carries 22.7% of counted traffic (median), still too little volume (school trips, trips between stops, other towns' trips and deliveries are not simulated). Highways stay on MTO's 2019 counts: 0.75.
- `python3 -m nfprep.traffic_layers` scores both; the scorecard includes the 2025 test.

## Open data through the user's gateway (17 November 2026)
niagaraopendata.ca blocks automated requests with a bot check; its official CKAN API (`/en/api/3/action/...`) answers through the user's own gateway (`gw.easycrazy.com/fetch?url=`). Downloaded:
- Regional road traffic volumes for 2015, 2016, 2017, 2018/2019, 2020 and 2024 (`data/sources/region/regional_road_aadt_*.csv`; 2025 was supplied by the user). At the city's stations, counted traffic against 2025 (median): 2015 0.87, 2018 0.92, 2021 0.86 (pandemic), 2025 1.00.
- From the City's map service (services9.arcgis.com, no bot check): the sidewalk sweeper routes and all 4,101 sidewalk segments (`data/city_open_data/`).
- No published crime, traffic-signal or collision datasets on the portal ("crime rates" is only a data request). The City's intersection layer has names and positions, no signal or stop control.

**Foot traffic against where the City sweeps sidewalks** (`python3 -m nfprep.foot_check`, `docs/foot_traffic_check.json`): the City sweeps where people walk most (8 routes, 56 km). Road nodes within 40 m of a route carry a median 107 model walkers a day, against 6 elsewhere; a node on a route outranks one off it 79% of the time; 79 of the model's 100 busiest walking spots are on a route, though only 11% of road nodes are. Independent of the model's inputs: a pass (scorecard 7 of 11).

**Traffic signals**: not published by the Region or the City; OpenStreetMap's Overpass servers were down or refusing (timeouts, resets, 504/406 also through the gateway). Needed for intersection delay; to retry.

## Traffic signals, stop signs and street speeds (17 November 2026)
`data/sources/osm_traffic_controls.json`: 246 traffic signals and 621 stop signs from OpenStreetMap (run by the user on Overpass Turbo; the Overpass servers refuse requests from cloud machines). `prep/nfprep/intersections.py` ties them to real intersections (152 signalled, 55 all-way stops, 342 other stops) and adds delay to the approaches.
Tested against the 2019 and 2025 regional counts (correlation, regional roads 2019 / 2025, highways; share of counted regional traffic carried):
| Variant | Regional 2019 | Regional 2025 | Highways | Share |
|---|---|---|---|---|
| Before | 0.62 | 0.65 | 0.75 | 22% |
| Residential and service streets at 35 km/h | **0.65** | **0.66** | **0.76** | 25% |
| + signals 8 s, stops 10 s | 0.59 | 0.61 | 0.76 | 29% |
| + signals 15 s | 0.53 | 0.56 | 0.75 | 23% |
| + signals 25 s | 0.42 | 0.45 | 0.73 | 20% |
| Posted limits from the Region's file, residential 35 | 0.64 | 0.64 | 0.71 | 27% |
Kept: residential, service and unclassified streets with no posted limit at an effective 35 km/h (`LOCAL_KMH` in `roads.py`; the page and the 2025 city updated). Intersection delays and posted limits stay off: without road capacity and coordinated signals, delay on main roads pushes trips onto side streets. Foot traffic against the sweeper routes after the change: 0.79 (unchanged).

## Niagara Region Employment Inventory and City licences (18 November 2026)
Supplied by the user: the Region's Employment Inventory for Niagara Falls (`data/sources/region/employment_inventory_niagara_falls_2016_2025.csv`, 22,448 rows, 3,744 businesses, surveyed 2016-2019 and 2022-2025 with employee band, floor area, year opened, NAICS and position) and the City's 2026 business licences (saved as `data/city_open_data/business_licences_2026_no_personal.csv`: owners' and contacts' names, phones and emails removed before saving).
`python3 -m nfprep.inventory_check` (`docs/inventory_check.json`), with a new engine command `nfsim forecast` (yearly forecast for a list of sites):
- **Sales at a site**: 394 inventory businesses matched to the model's (within 40 m, similar name). Model revenue against surveyed employee band, rank correlation within each kind, weighted: **0.24** (auto repair 0.43, hotels 0.38, grocery 0.35, fast food 0.17, restaurants 0.10). Positive but weak: a miss against 0.3.
- **Closures after the pandemic** (open 2023, closed by 2025: 155 of 1,298 in mapped kinds; four times the earlier labels). Ten-fold cross-validation:
  - the model's location forecast (rank within kind, visitor share): ranking 0.535, skill -1.2% against each kind's rate. **Location demand does not predict who closes.**
  - the inventory's own facts (age, employee band, floor area): ranking 0.66, skill +2.9%; with the model's location as well, 0.667.
  - same pattern 2016-2019 (pre-pandemic): facts and model together 0.669, skill +1.4%.
- **New businesses** (`data/new_business_closure_rates.json`): open two years or less, the chance of closing within two years was 25% in 2023-2025 (29 of 115) and 12% in 2016-2019; food 31% (14% before), retail 22% (15%), services 22% (11%); 1-4 staff 33% against 18% for 5+. Businesses over 10 years old: 10% (2023-2025). The page's forecast now gives this range for the kind placed.
- **Commuting to real job locations**: the commuting layer now sends residents who work in the city, and workers driving in, to every employer in the 2025 Employment Inventory (about 44,400 jobs from employee bands; 80% held by someone travelling to that workplace on an average day, SEED), instead of the model's shop staff plus a population spread. Workers driving in: 12,093 round trips a day (8,239 before). Regional roads correlation 0.65 to **0.68** (2019 counts) and 0.66 to **0.69** (2025); share of counted traffic 25% to 30%; highways 0.76, 52%. Schools are not in the inventory (public boards are not surveyed); school runs need school locations (OpenStreetMap).

## More chain benchmarks (18 November 2026)
Added A&W (2025 system sales $1.33B for three quarters, about 1,060 restaurants: about $1.67M each, an estimate) and LCBO ($5.87B retail store sales over about 680 stores, 2022-23: $8.6M a store). Model against published, median of each chain's branches: The Keg 0.96, Tim Hortons 1.04, Boston Pizza 1.47, Pizza Pizza 1.84, **A&W 0.36, LCBO 0.33**. Median 1.0; site-level error widens to x0.42-x2.39 (P10-P90, 6 chains), now used for the page's forecast ranges.
- **Found**: liquor stores (LCBO, The Beer Store, Wine Rack) are filed as grocery stores, and alcohol bought from stores goes mostly to tobacco and cannabis shops (grocery stores serve it at weight 0.3). In Ontario most alcohol is bought at the LCBO and The Beer Store. Fix: a liquor-store kind serving alcohol, with alcohol split from tobacco and cannabis in the budgets; needs a rebuild and the sales calibration rerun.

## Liquor stores and alcohol spending (18 November 2026)
- New kind `liquor` for the LCBO, The Beer Store, Wine Rack and wine shops (by map tag or name), NAICS 445. Alcohol bought from stores is now its own spending category, split from tobacco and cannabis (which stay with tobacco and cannabis shops and count in store type 459).
- The household survey puts the city's alcohol bought from stores at only $28M; it is set instead from LCBO retail store sales per Ontarian ($5.87B, 2022-23, over about 15.6M: $376) plus 30% for The Beer Store and wine shops (SEED): about $490 a person. Groceries take the rest of store type 445, which stays on Statistics Canada's target.
- Grocery and convenience stores' share of alcohol: tested at weights 0.1, 0.02 and 0 (LCBO branches at 0.20, 0.37 and 0.54 of published sales per store); kept 0.02, since Ontario grocery and convenience stores now sell some beer and wine. Visitors' store food and drink also reaches liquor stores (weight 0.3).
- Full recalibration (`python3 -m nfprep.calibrate`): distance parameter 1.35 (residential streets now 35 km/h), store types within 1% of targets except 459 ($208M against $311M, as before), hotel occupancy 62.1% for January-November against 63.2% published.
- Schools: the Region's school list (`data/schools_niagara_falls.json`, 43 schools) drives school runs in the traffic layers (about 2,900 elementary and 960 secondary round trips a day; 35% and 25% of students driven, SEED).

## Cannabis (18 November 2026)
- Cannabis is its own spending category, split from tobacco. Residents: Ontario's legal sales per person (OCS: $2.15B in 2024, about $135 each); the survey under-reports it. Tobacco stays at the survey level, sold mostly at convenience and grocery stores (tobacconists and vape shops weight 0.5).
- Visitors: a cannabis budget (`data/cannabis_market.json`), sized so the city's 22 cannabis stores (Employment Inventory 2025, against 5 in 2019) average about Ontario's store average ($1.2M; the Ontario store count, about 1,750, is not verified). Niagara Falls has about twice the stores per resident of the province, which visitors fill. Cannabis is legal in New York State since 2021, so US visitors' motive is weaker than it was.
- 14 cannabis stores the map missed were added from the inventory (25 tobacco and cannabis shops now, 11 before).
- Result after recalibration: tobacco and cannabis shops $29.6M a year, $17.0M (57%) from visitors; tourist-area stores mostly from visitors (Canna Cabana on Victoria Avenue $5.0M, 69% visitors). Store type 459 rose from $208M to $252M against the $311M target. Chains: median 1.09 of published sales; A&W 0.59 (was 0.36), LCBO 0.30; one Tim Hortons above the brand's best unit (was three). Regional roads 0.69 (2019) and 0.70 (2025).
- Known issue: cigar and vape shops share the cannabis stores' kind, so they also draw cannabis spending (two Cuban cigar shops at $2.0-2.8M). Fix: a separate tobacco-shop kind at the next rebuild.

## Closure signals and the spot closure model (18 November 2026)
Following retail-survival research (neighbourhood activity and visit trends predict closures better than demand or ratings), `python3 -m nfprep.closure_signals` tests neighbourhood and site signals against the Employment Inventory (`docs/closure_signals_check.json`): vacancy within 300 m, the area's trend in open businesses, new same-kind competitors within 500 m, same-kind neighbours, the model's people on foot and passing vehicles, bus departures within 300 m (Niagara Region Transit GTFS through the user's gateway, `data/transit_stops_niagara_falls.json`), and the model's forecast rank.
- 2023-2025 alone (155 closures): nothing adds to kind, age, size and floor area (skill +3.6%, ranking 0.67).
- Pooled two-year windows 2016-18, 2017-19, 2022-24, 2023-25 (388 closures; a business can appear in several windows, so slightly optimistic): facts +4.0% (ranking 0.70); with neighbourhood signals **+4.2%, ranking 0.71**. Vacancy within 300 m is the strongest new signal (0.63 alone). The model's own location outputs (forecast, people on foot, passing vehicles) carry no closure signal (0.50-0.51).
- `python3 -m nfprep.closure_model` fits kind rate, age, employee band, floor area and vacancy on the pooled windows (`data/closure_model.json`) and stores vacancy for every road node (2025; median 32%). The page's forecast now gives a new business's two-year closure chance at that spot, for 1-4 staff and 5+ (e.g. a new restaurant at Main and Ferry, 52% vacancy nearby: 36% and 18%).

## Tobacconists, visitors' alcohol, store size (18 November 2026)
- Cigar, tobacco, vape and smoke shops are now their own kind (`tobacconist`): they sell tobacco, not cannabis. Cannabis stores (20) sell $28.5M a year; tobacconists (5) $1.4M (two Cuban cigar shops had drawn $2.0-2.8M of cannabis spending).
- Visitors' store food and drink reaches liquor stores at the same weight as grocery stores (was 0.3; tested 0.3 / 1 / 2: LCBO 0.30 / 0.41 / 0.56 of published sales per store; kept 1.0, not the best-scoring 2.0, so the LCBO stays a test). Liquor stores $30.7M a year. LCBO now 0.41.
- Store size exponent (how strongly size raises pull) tested at 0.3, 0.5, 0.7, 0.9: no change in the match with surveyed employee bands (0.21-0.22); lower values fit the chains slightly better on 6 chains, too few to change it. Kept 0.5.
- After recalibration: chains median 1.09 (Keg 0.93, Tim Hortons 1.25, A&W 0.59, LCBO 0.41, Boston Pizza 1.79, Pizza Pizza 2.10); site error P10-P90 x0.48-x2.10 (was x0.43-x2.35); scorecard 8 of 14.

## Missing car trips and road capacity (18 November 2026)
- **Other car trips** (`traffic_layers.py`): the Transportation Tomorrow Survey's 2.29 trips per person a day, 70% as drivers (x0.9 from a weekday to an average day, SEED), is 157,400 car trips a day by residents; the model already sends 104,900 (shopping 0.475 outings per person, driven 67.7%; commuting; school runs). The gap, 52,500 a day, is added: 25,200 between stops (the survey's 16% non-home-based, between workplaces and businesses) and 27,300 other home-based (errands and visits, to workplaces and homes), falling off with distance as exp(-km / 4) (SEED). Sized from the survey, not from the counts. Regional roads 0.69 to **0.74** (2019 counts) and 0.70 to **0.74** (2025); share of counted traffic 31% to **39%**; highways 0.77, 54%.
- **Road capacity** (`python3 -m nfprep.traffic_capacity [rounds]`, `docs/traffic_capacity_check.json`): daily capacity per edge from lanes (the Region's lane counts where a street has one count over 80% of its length, otherwise by class) times vehicles a day per lane (SEED: motorway 18,000, primary 9,000, local 5,000); travel time on the BPR curve t0 (1 + 0.15 (V/C)^4); spending trips and every layer re-routed each round, times averaged over rounds. After 8 rounds: regional roads 0.734 / 0.748, share 41%, highways 0.75; about 1% of road edges over capacity. Within noise of no congestion (0.735 / 0.744, 39%, 0.77), so the city runs without it (switching it on would need the sales calibration rerun). It matters once volumes are closer to the counts.

## Clusters, vacancy and size (18 November 2026)
Two-year closure rates in the Employment Inventory, pooled 2016-2025 (388 closures among 4,322 business-windows):
- **Vacancy within 300 m**: under 15% vacant or closed 5.0%; 15-30% 11.5%; 30-45% 14.3%; 45%+ 14.5%.
- **Food businesses near other food businesses** (within 300 m): none 11.8%; 1-2 12.6%; 3-7 8.3%; 8+ 7.2%. Dining streets keep their restaurants.
- **Busy areas as such do not protect**: 40+ open businesses of any kind within 300 m 11.2%, against 5.6% for 0-4 (busy strips turn over more).
- **Size**: 1-4 staff 13.7%, 5-99 5.5%, 100+ 2.8%; the strongest factor with the others held equal (standardised: size -0.53, vacancy +0.36, all businesses nearby +0.11, same kind -0.09). Bigger businesses also have deeper pockets, so this shows that big survives, not that building bigger draws customers.
- **Cluster bonus for sales** (engine: `cluster_exponent`, pull times (1 + food businesses within 300 m)^e, scaled to average 1): tested at e = 0, 0.1, 0.2, 0.3, 0.5. Restaurant revenue against surveyed staff got worse as e rose (0.14 to 0.07); chains mixed (Tim Hortons 1.25 to 1.02 at 0.1, Pizza Pizza 1.92 to 2.04). Left at 0: clustered restaurants survive better but the data does not show them selling more.
- **Restaurant cluster in the closure model**: skill +4.25% to +4.51%, ranking 0.712 to 0.712: no real gain (clustered streets also have low vacancy). Not added; the page states the measured rates for food businesses at the spot instead.

## Conditions: politics and the economy (18 November 2026)
The page has a **Conditions** panel (`data/policy_levers.json`, exported into city.json as `policy`): prime interest rate, the US dollar in Canadian dollars, Ontario's minimum wage, US trade (none / 2025 tensions / escalation) and event cards (border restrictions, recession, gas price spike, hotel tax up to 5%). Buttons reset to 2025 (calibrated) or today's settings (prime 4.45%, US$1 = C$1.41, minimum wage $17.60).
- Visitors and residents: US visitors move with trade (2025's measured drop 0.882; none undoes it, escalation repeats it, SEED) and the exchange rate (measured elasticity 0.84, weak); residents' spending -0.7% per point of prime (SEED); events multiply visitor groups (SEED, sources listed). These feed the engine (`nf_set_scenario`) on top of the chosen outlook, so the moving city and every forecast change.
- Business costs, as a share of sales: debt (20% of revenue, SEED) times the change in prime; goods cost under tariffs (SEED, full for shops, half for others); wages linked to the minimum wage by industry (SEED). Forecast profit and its range include them.
- Closure chance: odds times exp(40 x squeeze), the squeeze being extra costs plus gross margin lost on sales lost to the conditions (SEED gamma: yearly closures roughly doubled from 2016-19 to 2022-25 as prime rose about 2.3 points, part of which is credited to rates).
- Example, restaurant on Clifton Hill: 2025 settings $1.9M a year, profit $352k, closure chance 26% (1-4 staff) / 12% (5+); with escalation, prime 7% and a recession: $1.7M, $310k, 47% / 26%. A headline banner on the map lists the active conditions.

## Towns, 2025 conditions and the scorecard (18 November 2026)
- **Traffic between other towns** (`traffic_layers.py`): Statistics Canada's commuting flows between municipalities (98-10-0459) are behind its bot check, also through the user's gateway (403). Estimated instead from the travel survey's 1.03 million trips a day across the region, shared by population and distance (gravity), keeping only pairs whose real route crosses Niagara Falls (Fort Erie with St. Catharines, Thorold or Niagara-on-the-Lake; Welland with Niagara-on-the-Lake; SEED list): 55 trips a day, no effect on the fit. Routing every pair through the city (the model's towns join only through its roads) gave 118,500 trips and wrecked the fit (0.36), so the pair list matters. The volume gap (39% of counted traffic) is more likely trucks and commercial vehicles, 10-20% of traffic on Ontario's main roads, which the model does not simulate.
- **2025 conditions** (`python3 -m nfprep.conditions_check`, `docs/conditions_check.json`): the Conditions panel's formula fed 2024 and 2025's real settings (prime 6.77% to 4.91%, minimum wage $16.71 to $17.30, US overnight visitors x0.873) for every business at its customer mix predicts closure odds x1.045. Observed: St. Catharines-Niagara closing rate x1.094, Ontario exits x1.197. Direction right, size about half. Tourist businesses were squeezed (attractions 1.7% of sales, restaurants 0.8%); resident-serving ones gained from lower rates.
- **Scorecard**: the "2025 rise" test now uses this check (pass on direction); the old five-year closure test (38 map edits) is retired, superseded by the Employment Inventory tests.

## History back to 1972 (18 November 2026)
`data/monthly_history_1972_2026.json`: monthly border crossings at the Niagara ports since January 1972 (Statistics Canada 24-10-0053) and the US dollar in Canadian dollars since January 1999 (Bank of Canada IEXM0101 to April 2017, then FXMUSDCAD; FRED's older series timed out). `docs/history_shocks.json` measures past shocks on US visitors:
- Casino Niagara (December 1996) and a dollar near 70 US cents: US overnight visitors +7% in 1997, +9% in 1998.
- 9/11: overnight x1.03 the following year (held up), same-day x0.945.
- SARS 2003: overnight x0.655 in April-June, x0.76 for the year.
- Passport rule at the land border (June 2009): overnight x0.872, same-day x0.819.
- Dollar at par: overnight 2011 x0.686 of 2002 as the US dollar's price fell 37%.
- 2025 tariffs: overnight x0.873; Canadians' US trips x0.689.
- US same-day trips fell from 8.5 million (2001) to 2.3 million (2009) and never came back.
- Exchange rate, yearly 2000-2025 without 2020-22: US overnight visitors 0.71% per 1% change in the US dollar's price (correlation 0.35, 23 years); Canadians' US trips -1.0%.
The Conditions panel now uses these: exchange-rate elasticity 0.71; event cards "Passport-type border rule" (x0.85), "Health scare (SARS-like)" (x0.76), "Security shock (9/11-like)" (x0.98); tariff escalation x0.873.
Older data found but not yet used: MTO highway traffic volumes 1988-2016 (PDF and CSV, Ministry of Transportation library), which would give a 30-year traffic test. No Niagara Falls hotel occupancy history before the CBRE reports was found.

## Highway traffic 1988-2024 (18 November 2026)
The Ministry of Transportation's Traffic Volumes 1988-2024 file (`data/sources/mto/traffic_volumes_1988_2024.csv`, downloaded directly; the QEW is highway "1") gives yearly AADT and truck share for all 20 highway sections in and around Niagara Falls (`data/traffic_history_mto_1988_2024.json`).
- Highway 420, the main tourist route (four sections): 79,100 vehicles a day summed in 1988, 113,000 in 2002, 150,800 in 2019, 141,400 in 2024. QEW in the city (four sections): 143,600 to 278,900. Over the same years US visitors through the Niagara ports fell from 10.5 million to 4.6 million. Levels move in opposite directions (correlation -0.79) and yearly changes are unrelated (-0.04): the city's traffic grew with Canadian visitors, the casinos (1996, 2004) and population, not with Americans.
- The Ministry estimates volumes in years without a count: Highway 420 shows almost no 2021 dip, so single years are not reliable.
- Truck shares: QEW 4-15%, Highway 58 7-10%, Highway 20 8%, Highway 7186 6%, Highway 420 1%, the Queenston-Lewiston Bridge approach 53%. Trucks are likely 5-10% of regional-road traffic, part of the gap between the model's 39% of counted traffic and the counts.

## Commercial vehicles and the 1988-2024 replay (18 November 2026)
- **Commercial vehicles** (`traffic_layers.py`): FHWA's Quick Response Freight Manual (1996, Table 4.1, Phoenix study; confirmed from the original report) gives commercial vehicle trips per employee per day by sector (retail including eating places 1.206, office and services 0.514, manufacturing-transport-wholesale 1.284, agriculture-construction 1.573) and per household (0.388), split into four-tire vehicles, single-unit trucks and combinations. Applied to the Employment Inventory's 44,400 jobs and the city's households: 41,300 van and pickup trips and 15,300 truck trips a day (half the trucks from the QEW edges, SEED). Not fitted to counts. Regional roads 0.74 (2019) and 0.75 (2025); **share of counted traffic 39% to 51%** (passes); highways 0.76, 62%.
- **Replay 1988-2024** (`docs/replay_1988_2024.json`): the model puts 91% of Highway 420's traffic on city-driven trips (residents, jobs, deliveries) and 9% on visitors. Highway 420 traffic grew 80% from 1988 to 2019 while the census population grew 24% (72,107 in 1986 to 94,415 in 2021), so scaling city traffic with population cannot replay the history: the implied visitor index comes out negative before 2005. Long-run growth came from more driving per person, jobs growing faster than people, the city spreading west and the casino and Fallsview visitors; the model's 9% visitor share on its main tourist route is probably too low. For projections years ahead, city traffic needs a driving-per-person trend and job growth, not population alone.
