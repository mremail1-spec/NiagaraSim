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
    let persons5: u64 = e.city.households.persons5.iter().map(|&x| x as u64).sum();
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
    assert!((e.city_revenue() + e.out_of_town_revenue() - e.state().revenue.iter().sum::<f64>()).abs() < 1.0);
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
    let got = e.tourist_revenue() + e.state().tour.fares;
    // parties that find every room taken stay with friends and pay no hotel bill, so a few percent is lost
    assert!(got <= expected * 1.01 && got >= expected * 0.95, "tourist spending {got} vs {expected}");
    // about 80% of a regional total near $3.8 billion
    assert!(expected > 2.8e9 && expected < 3.4e9, "{expected}");
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
            used[p.lodging as usize] += 1;
        }
    }
    for b in 0..used.len() {
        assert!(used[b] <= t.capacity[b], "{} holds {} parties in {} rooms", e.city.businesses.name[b], used[b], t.capacity[b]);
        assert_eq!(used[b] + e.state().tour.free[b], t.capacity[b]);
    }
    println!("hotel occupancy to mid-July: {:.3}", e.hotel_occupancy());
}
