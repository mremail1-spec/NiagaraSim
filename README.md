# Niagara Falls simulator

A game-like, agent-based simulation of the Niagara Falls, Ontario economy.
Plan: https://claude.ai/artifact/CvFuGLP1nByTvzvPyyTnB4

## Layout
- `data/` — the research data pack (see `data/README.md` and `data/HANDOVER.md`).
- `prep/` — Python, one-time data preparation. Builds the city the engine loads.
- `engine/` — Rust simulation engine (WebAssembly + native). Residents done; tourists and money flow next.
- `web/` — browser map and game interface. Not started.
- `build/` — generated output (not committed).

## Status
| Phase | State | Proof |
|---|---|---|
| 1. Data loader | Done | `python3 -m nfprep.loader` — 32/32 checks |
| 2. Synthetic population | Done | 16 tests against the calibration targets |
| 3. Businesses and roads | Done | 21 tests: employer counts match the register; road network fully drivable |
| 4. Engine and resident spending | Done | 9 Rust tests: survey trip length and rate, seasonality, budget conservation, flow = agent, determinism, rewind, routes on real roads, one year in about 11 s |
| 5–10 | Not started | |

Tests: `python3 -m pytest` (37) and `cd engine && cargo test --release` (9). A picture of the generated city is in `docs/city_2025_map.png`.

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
