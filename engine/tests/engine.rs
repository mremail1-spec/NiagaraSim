//! Phase 4 proof. Needs the city file: run `python3 -m nfprep.build 2025 1` in prep/ first.
use nfsim::{City, Engine};
use std::sync::OnceLock;
use std::time::Instant;

const CITY: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../build/city_2025/city.json");

fn city() -> City {
    static TEXT: OnceLock<String> = OnceLock::new();
    let text = TEXT.get_or_init(|| std::fs::read_to_string(CITY).expect("build the city first: python3 -m nfprep.build 2025 1"));
    City::from_json(text).unwrap()
}

fn beta() -> f64 {
    static BETA: OnceLock<f64> = OnceLock::new();
    *BETA.get_or_init(|| Engine::calibrated(city(), 1).beta)
}

/// One full year in agent mode, shared by the tests that inspect it. Returns the engine and the seconds it took.
fn year() -> &'static (Engine, f64) {
    static YEAR: OnceLock<(Engine, f64)> = OnceLock::new();
    YEAR.get_or_init(|| {
        let mut e = Engine::new(city(), beta(), 1);
        let t = Instant::now();
        for _ in 0..365 {
            e.step_day();
        }
        let secs = t.elapsed().as_secs_f64();
        (e, secs)
    })
}

#[test]
fn calibration_reaches_the_survey_median_trip() {
    let e = Engine::new(city(), beta(), 1);
    let (median, _, walked) = e.expected_trip_stats(beta());
    assert!((median - 5.5).abs() <= 0.15, "expected median {median}");
    assert!((walked - 0.07).abs() <= 0.005, "walk share {walked}");
    assert!(beta() > 0.5 && beta() < 4.0, "beta {}", beta());
}

#[test]
fn simulated_trips_match_the_travel_survey() {
    let (e, _) = year();
    let s = e.state();
    let persons5: u64 = (0..e.city.households.persons5.len()).filter(|&h| !e.is_regional(h)).map(|h| e.city.households.persons5[h] as u64).sum();
    let rate = s.outings as f64 / persons5 as f64 / 365.0;
    // 2.29 trips per person x 48% discretionary / 2 trips per outing = 0.5496
    assert!((rate - 0.5496).abs() / 0.5496 < 0.01, "outings per person per day {rate}");
    assert!((e.median_driver_km() - 5.5).abs() <= 0.2, "median driving trip {}", e.median_driver_km());
    let total: u64 = s.mode_counts.iter().sum();
    let walk = s.mode_counts[2] as f64 / total as f64;
    let transit = s.mode_counts[3] as f64 / total as f64;
    assert!((walk - 0.07).abs() < 0.005, "walk {walk}");
    assert!((transit - 0.06).abs() < 0.005, "transit {transit}");
}

#[test]
fn every_budget_dollar_is_accounted_for() {
    let (e, _) = year();
    // residents only: take tourist revenue out of the business totals
    let spent: f64 = e.state().revenue.iter().sum::<f64>() - e.tourist_revenue() + e.state().online_leakage;
    let budget = e.total_budget();
    assert!((spent - budget).abs() / budget < 0.005, "spent {spent} of {budget}");
    // spending that leaves the city and spending that comes in from the towns are both tracked
    let at_towns: f64 = (0..e.state().revenue.len()).filter(|&b| e.city.businesses.external[b] == 1).map(|b| e.state().revenue[b]).sum();
    assert!(e.out_of_town_revenue() > 0.0 && e.out_of_town_revenue() < at_towns);
    assert!(e.inbound_revenue() > 0.0 && e.inbound_revenue() < e.city_revenue());
}

#[test]
fn monthly_seasonality_is_reproduced() {
    let (e, _) = year();
    for name in ["restaurants", "groceries", "clothing"] {
        let c = e.city.categories.name.iter().position(|n| n == name).unwrap();
        let months: Vec<f64> = (0..12).map(|m| e.state().month_category_revenue[m][c]).collect();
        let per_day: f64 = months.iter().sum::<f64>() / 365.0;
        // the published index is each month's sales over the average month; compare on a per-day basis
        let target = &e.city.categories.season[c];
        let target_mean: f64 = (0..12).map(|m| target[m] * nfsim::engine::DAYS_IN_MONTH[m] as f64).sum::<f64>() / 365.0;
        for m in 0..12 {
            let got = months[m] / nfsim::engine::DAYS_IN_MONTH[m] as f64 / per_day;
            assert!((got - target[m] / target_mean).abs() < 0.02, "{name} month {m}: {got} vs {}", target[m] / target_mean);
        }
    }
}

#[test]
fn flow_mode_agrees_with_agent_mode() {
    let (agent, _) = year();
    let mut flow = Engine::new(city(), beta(), 1);
    for _ in 0..365 {
        flow.step_day_flow();
    }
    // tourists turned away from full hotels in agent mode spend a little less than flow mode expects
    assert!((flow.city_revenue() - agent.city_revenue()).abs() / agent.city_revenue() < 0.025);
    assert!((flow.out_of_town_revenue() - agent.out_of_town_revenue()).abs() / agent.out_of_town_revenue() < 0.01);
    for c in 0..agent.category_count() {
        let a: f64 = (0..12).map(|m| agent.state().month_category_revenue[m][c]).sum();
        let f: f64 = (0..12).map(|m| flow.state().month_category_revenue[m][c]).sum();
        assert!((a - f).abs() / f < 0.01, "category {c}: agent {a} flow {f}");
    }
    // the largest businesses agree individually as well
    let resident = |e: &Engine, b: usize| e.state().revenue[b] - e.state().tour.revenue[b];
    let mut top: Vec<usize> = (0..agent.state().revenue.len()).collect();
    top.sort_by(|&x, &y| resident(&flow, y).partial_cmp(&resident(&flow, x)).unwrap());
    for &b in top.iter().take(50) {
        let (a, f) = (resident(agent, b), resident(&flow, b));
        assert!((a - f).abs() / f < 0.03, "business {b}: agent {a} flow {f}");
    }
}

#[test]
fn same_seed_same_city_different_seed_different_city() {
    let run = |seed| {
        let mut e = Engine::new(city(), beta(), seed);
        for _ in 0..20 {
            e.step_day();
        }
        e.snapshot()
    };
    let (a, b, c) = (run(7), run(7), run(8));
    assert!(a == b);
    assert!(a != c);
}

#[test]
fn rewinding_to_a_snapshot_replays_identically() {
    let mut e = Engine::new(city(), beta(), 3);
    for _ in 0..10 * 24 + 7 {
        e.step_hour(); // stop mid-day, at 07:00 on day 10
    }
    let saved = e.snapshot();
    let text = serde_json::to_string(&saved).unwrap(); // a snapshot survives being written out and read back
    for _ in 0..5 * 24 {
        e.step_hour();
    }
    let first = e.snapshot();
    e.restore(serde_json::from_str(&text).unwrap());
    assert_eq!((e.state().day, e.state().hour), (10, 7));
    for _ in 0..5 * 24 {
        e.step_hour();
    }
    assert!(e.snapshot() == first);
}

#[test]
fn trips_follow_real_roads_and_opening_hours() {
    let mut e = Engine::new(city(), beta(), 5);
    let mut edge = std::collections::HashMap::new();
    for i in 0..e.city.edges.from.len() {
        let k = (e.city.edges.from[i], e.city.edges.to[i]);
        let v = e.city.edges.km[i] as f64;
        edge.entry(k).and_modify(|x: &mut f64| *x = x.min(v)).or_insert(v);
    }
    let (mut checked, mut closed) = (0u32, 0u32);
    for hour in 0..48u32 {
        e.step_hour();
        for (i, t) in e.last_trips.iter().enumerate() {
            if !e.is_open(t.business as usize, hour % 24) {
                closed += 1;
            }
            if i % 50 != 0 {
                continue;
            }
            let home = e.city.households.node[t.household as usize];
            let path = e.route(home, t.business);
            assert_eq!(path[0], home);
            assert_eq!(*path.last().unwrap(), e.city.businesses.node[t.business as usize]);
            for w in path.windows(2) {
                assert!(edge.contains_key(&(w[0], w[1])), "no road from {} to {}", w[0], w[1]);
            }
            // the fastest route's length matches the distance the engine charged the trip
            let in_city = e.route_km(home, t.business) - e.city.businesses.extra_km[t.business as usize] as f64;
            let fastest: f64 = path.windows(2).map(|w| edge[&(w[0], w[1])]).sum();
            assert!(fastest <= in_city + 0.05, "route {fastest} km vs charged {in_city} km");
            checked += 1;
        }
    }
    assert!(checked > 500, "only {checked} routes checked");
    assert_eq!(closed, 0, "{closed} visits to closed businesses");
}

#[test]
fn a_simulated_year_runs_well_inside_five_minutes() {
    let (_, secs) = year();
    println!("one simulated year, agent mode: {secs:.1} s");
    assert!(*secs < 300.0, "{secs} s");
}

// ---------------------------------------------------------------- phase 5: tourists

#[test]
fn visitor_numbers_match_the_inputs() {
    let (e, _) = year();
    let t = e.city.tourism.as_ref().unwrap();
    let expected: f64 = t.segments.iter().map(|s| s.persons_per_year).sum();
    let got: f64 = e.state().tour.arrivals.iter().flatten().sum();
    assert!((got - expected).abs() / expected < 0.01, "visitors {got} vs {expected}");
    assert!((expected - 11_050_000.0).abs() < 60_000.0, "city visitors {expected}");
}

#[test]
fn journey_behind_the_falls_and_other_attendance_match() {
    let (e, _) = year();
    let t = e.city.tourism.as_ref().unwrap();
    let mut checked = 0;
    for b in 0..t.attendance_target.len() {
        let target = t.attendance_target[b];
        if target <= 0.0 {
            continue;
        }
        let got = e.state().tour.persons[b];
        let limit = if target == 917_075.0 { 0.02 } else { 0.05 };
        assert!((got - target).abs() / target < limit, "{}: {got} vs {target}", e.city.businesses.name[b]);
        checked += 1;
    }
    assert!(checked >= 6, "only {checked} attractions with published attendance");
}

#[test]
fn us_overnight_arrivals_peak_in_july_and_august() {
    let (e, _) = year();
    let t = e.city.tourism.as_ref().unwrap();
    let seg = t.segments.iter().position(|s| s.name == "us_overnight").unwrap();
    let a = &e.state().tour.arrivals[seg];
    let share = (a[6] + a[7]) / a.iter().sum::<f64>();
    assert!((share - 0.328).abs() < 0.01, "July-August share {share}");
}

#[test]
fn tourist_spending_matches_the_inputs() {
    let (e, _) = year();
    let t = e.city.tourism.as_ref().unwrap();
    let nights: f64 = t.nights.n.iter().zip(&t.nights.weight).map(|(n, w)| *n as f64 * w).sum::<f64>() / t.nights.weight.iter().sum::<f64>();
    let expected: f64 = t.segments.iter().map(|s| s.persons_per_year * s.spend_per_person_day * if s.overnight == 1 { nights } else { 1.0 }).sum();
    // hotel bills follow published room rates, so compare everything except accommodation
    let acc_avg = t.accommodation_spend_per_paid_person_night * (1.0 - t.lodging_share.private);
    let expected: f64 = t.segments.iter().map(|s| s.persons_per_year * (s.spend_per_person_day - if s.overnight == 1 { acc_avg } else { 0.0 }) * if s.overnight == 1 { nights } else { 1.0 }).sum();
    // casino gaming is added on top of the survey (it is calibrated to the casinos' revenue instead)
    let gaming = match t.categories.name.iter().position(|n| n == "gaming") {
        Some(g) => (0..12).map(|m| e.state().tour.month_category_revenue[m][g]).sum::<f64>(),
        None => 0.0,
    };
    let got = e.tourist_revenue() + e.state().tour.fares + e.state().tour.elsewhere - e.state().tour.accommodation_by_month.iter().sum::<f64>() - gaming;
    // parties that find every room taken stay with friends and pay no hotel bill, so a few percent is lost
    assert!(got <= expected * 1.01 && got >= expected * 0.95, "tourist spending {got} vs {expected}");
    // about 80% of a regional total near $3.8 billion
    assert!(expected > 1.9e9 && expected < 2.6e9, "{expected}");
}

#[test]
fn hotels_never_hold_more_parties_than_rooms() {
    let mut e = Engine::new(city(), beta(), 9);
    for _ in 0..(190 * 24) {
        e.step_hour(); // into July
    }
    let t = e.city.tourism.as_ref().unwrap();
    let mut used = vec![0u32; t.capacity.len()];
    for p in &e.state().tour.parties {
        if p.lodging != u32::MAX {
            used[p.lodging as usize] += p.rooms.max(1) as u32;
        }
    }
    for b in 0..used.len() {
        assert!(used[b] <= t.capacity[b], "{} holds {} parties in {} rooms", e.city.businesses.name[b], used[b], t.capacity[b]);
        assert_eq!(used[b] + e.state().tour.free[b] + Engine::base_rooms(t, b), t.capacity[b]);
    }
    println!("hotel occupancy to mid-July: {:.3}", e.hotel_occupancy());
}

// ---------------------------------------------------------------- phase 8: place a business

fn sim_category(e: &Engine, name: &str) -> u32 {
    e.city.sim_categories.iter().position(|c| c == name).unwrap() as u32
}

fn node_of(e: &Engine, business_name: &str) -> u32 {
    let b = e.city.businesses.name.iter().position(|n| n == business_name).unwrap();
    e.city.businesses.node[b]
}

#[test]
fn a_new_business_takes_trade_it_does_not_create_it() {
    let e = Engine::new(city(), beta(), 1);
    let f = e.forecast(node_of(&e, "Costco"), sim_category(&e, "restaurant"), 483_000.0, 1.0);
    let earned = f.resident + f.tourist;
    assert!(earned > 50_000.0, "forecast {earned}");
    assert!((earned - f.total_taken).abs() / earned < 1e-9, "earned {earned} but took {}", f.total_taken);
    assert!((f.monthly.iter().sum::<f64>() - earned).abs() / earned < 0.01);
    assert!(f.from_out_of_town > 0.0 && f.from_out_of_town < f.resident);
    assert!(!f.losses.is_empty() && f.losses[0].1 >= f.losses.last().unwrap().1);
}

#[test]
fn the_forecast_matches_what_happens_when_the_business_is_built() {
    let mut e = Engine::new(city(), beta(), 1);
    let (node, kind) = (node_of(&e, "Costco"), sim_category(&e, "restaurant"));
    let f = e.forecast(node, kind, 483_000.0, 1.0);
    let before: f64 = e.expected_yearly_revenue().iter().sum();
    let hurt = f.losses[0];
    let hurt_before = e.expected_yearly_revenue()[hurt.0 as usize];
    let b = e.add_business(node, e.city.nodes.lat[node as usize], e.city.nodes.lon[node as usize], kind, 483_000.0, 1.0, 11, 22, false, "Test Diner") as usize;
    let after = e.expected_yearly_revenue();
    let built = after[b];
    assert!((built - (f.resident + f.tourist)).abs() / built < 0.01, "built {built} vs forecast {}", f.resident + f.tourist);
    assert!((after.iter().sum::<f64>() - before).abs() / before < 1e-6, "total spending changed");
    assert!(((hurt_before - after[hurt.0 as usize]) - hurt.1).abs() / hurt.1 < 0.02, "competitor loss off");
    for _ in 0..48 {
        e.step_hour(); // and it trades once the clock runs
    }
    assert!(e.state().revenue[b] > 0.0);
    assert!(e.route(e.city.households.node[0], b as u32).len() > 1);
}

#[test]
fn location_changes_the_forecast() {
    let e = Engine::new(city(), beta(), 1);
    let kind = sim_category(&e, "restaurant");
    let falls = e.forecast(node_of(&e, "Journey Behind the Falls"), kind, 483_000.0, 1.0);
    // the southernmost road node inside the city (neighbouring towns are nodes too, so leave them out)
    let towns: Vec<u32> = (0..e.city.businesses.node.len()).filter(|&b| e.city.businesses.external[b] == 1).map(|b| e.city.businesses.node[b]).collect();
    let south = (0..e.city.nodes.lat.len()).filter(|n| !towns.contains(&(*n as u32)))
        .min_by(|&a, &b| e.city.nodes.lat[a].partial_cmp(&e.city.nodes.lat[b]).unwrap()).unwrap();
    let farm = e.forecast(south as u32, kind, 483_000.0, 1.0);
    let (at_falls, at_farm) = (falls.resident + falls.tourist, farm.resident + farm.tourist);
    println!("restaurant at the falls: ${at_falls:.0} ({:.0}% tourists); at the rural south edge: ${at_farm:.0}", falls.tourist / at_falls * 100.0);
    assert!(at_falls > 1.5 * at_farm, "falls {at_falls} vs farm {at_farm}");
    assert!(falls.tourist / at_falls > 0.4, "tourist share at the falls {}", falls.tourist / at_falls);
    assert!(farm.tourist / at_farm < falls.tourist / at_falls);
    // a bigger business draws more, but not in proportion
    let big = e.forecast(node_of(&e, "Journey Behind the Falls"), kind, 992_000.0, 1.0);
    assert!(big.resident + big.tourist > at_falls && big.resident + big.tourist < 2.0 * at_falls);
}

#[test]
fn hotel_occupancy_matches_the_published_2025_figures() {
    // CBRE Hotels (January, March, November, January-March, January-November) and Niagara Tourism Profile (January-July)
    let (e, _) = year();
    let nights = &e.state().tour.room_nights_by_month;
    let rooms = e.hotel_rooms();
    let days = nfsim::engine::DAYS_IN_MONTH;
    let occ = |months: std::ops::Range<usize>| nights[months.clone()].iter().sum::<f64>() / (rooms * days[months].iter().sum::<u32>() as f64);
    assert!((rooms - 16_000.0).abs() < 100.0, "rooms {rooms}");
    for (label, months, published, tolerance) in [("January", 0..1, 0.368, 0.04), ("March", 2..3, 0.474, 0.05), ("November", 10..11, 0.453, 0.06),
                                                  ("January-March", 0..3, 0.416, 0.03), ("January-July", 0..7, 0.595, 0.03), ("January-November", 0..11, 0.632, 0.03)] {
        let got = occ(months);
        println!("hotel occupancy {label}: model {got:.3}, published {published}");
        assert!((got - published).abs() < tolerance, "{label}: {got} vs {published}");
    }
}

#[test]
fn public_operators_match_their_published_revenue() {
    // Niagara Parks 2024/25 own operations less transit; casinos' gaming revenue estimated from the hosting fee
    let e = Engine::new(city(), beta(), 1);
    let npc = e.operator_revenue(1);
    let casinos = e.operator_revenue(2);
    println!("Niagara Parks places ${:.1}M, casinos ${:.1}M", npc / 1e6, casinos / 1e6);
    assert!((npc - 129_848_000.0).abs() / 129_848_000.0 < 0.05, "Niagara Parks {npc}");
    assert!(casinos > 500e6 && casinos < 600e6, "casinos {casinos}");
}

#[test]
fn passing_traffic_lifts_pass_by_kinds_only_and_averages_one() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../build/city_2025/city.json");
    let Ok(text) = std::fs::read_to_string(&path) else { return };
    let mut city: nfsim::city::City = serde_json::from_str(&text).unwrap();
    if city.nodes.passing.is_empty() {
        return;
    }
    city.prepare_sites();
    let b = &city.businesses;
    for c in 0..city.sim_categories.len() {
        let idx: Vec<usize> = (0..b.node.len()).filter(|&i| b.external[i] == 0 && b.sim_category[i] as usize == c).collect();
        if idx.is_empty() {
            continue;
        }
        let p = city.pass_by[c];
        for &i in &idx {
            let f = b.site[i];
            if p == 0.0 {
                assert_eq!(f, 1.0);
            } else {
                assert!(f >= 1.0 - p - 1e-9 && f <= 1.0 + p * (nfsim::city::PASSING_CAP - 1.0) + 1e-9, "{} {}", city.sim_categories[c], f);
            }
        }
        let mean: f64 = idx.iter().map(|&i| b.site[i]).sum::<f64>() / idx.len() as f64;
        assert!(mean <= 1.0 + 1e-9 && mean > 0.8, "{} mean site factor {}", city.sim_categories[c], mean);
    }
}
