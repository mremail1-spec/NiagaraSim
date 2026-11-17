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
| 6, 9, 10 | Not started | |

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
