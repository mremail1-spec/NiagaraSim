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
