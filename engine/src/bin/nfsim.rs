//! Command-line runner: calibrate, run a year, report.
//!
//!   nfsim calibrate <city.json>
//!   nfsim run <city.json> [--days 365] [--mode agent|flow] [--seed 1] [--beta B] [--out report.json]
use nfsim::{City, Engine};
use std::time::Instant;

fn arg(args: &[String], name: &str) -> Option<String> {
    args.iter().position(|a| a == name).and_then(|i| args.get(i + 1).cloned())
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 3 {
        eprintln!("usage: nfsim calibrate|run <city.json> [--days N] [--mode agent|flow] [--seed S] [--beta B] [--out FILE]");
        std::process::exit(2);
    }
    let text = std::fs::read_to_string(&args[2]).expect("cannot read city file");
    let city = City::from_json(&text).expect("cannot parse city file");
    let seed: u64 = arg(&args, "--seed").map(|s| s.parse().unwrap()).unwrap_or(1);
    let t0 = Instant::now();
    let mut engine = match arg(&args, "--beta") {
        Some(b) => Engine::new(city, b.parse().unwrap(), seed),
        None => Engine::calibrated(city, seed),
    };
    let setup = t0.elapsed().as_secs_f64();
    let (median, short, walked) = engine.expected_trip_stats(engine.beta);
    if args[1] == "calibrate" {
        println!("beta {:.4}", engine.beta);
        println!("expected median driving trip km {:.2} (target {:.2})", median, engine.city.params.median_driver_trip_km_target);
        println!("share of trips under {:.1} km {:.3}; share walked {:.3}", engine.city.params.walk_max_km, short, walked);
        println!("setup seconds {:.2}", setup);
        return;
    }
    let days: u32 = arg(&args, "--days").map(|s| s.parse().unwrap()).unwrap_or(365);
    let mode = arg(&args, "--mode").unwrap_or_else(|| "agent".into());
    let t1 = Instant::now();
    for _ in 0..days {
        if mode == "flow" { engine.step_day_flow() } else { engine.step_day() }
    }
    let run = t1.elapsed().as_secs_f64();

    let s = engine.state();
    let cats = &engine.city.categories.name;
    let persons5: u64 = engine.city.households.persons5.iter().map(|&x| x as u64).sum();
    let total: f64 = s.revenue.iter().sum();
    let mut by_cat = serde_json::Map::new();
    let mut season = serde_json::Map::new();
    for (c, name) in cats.iter().enumerate() {
        let months: Vec<f64> = (0..12).map(|m| s.month_category_revenue[m][c]).collect();
        let year: f64 = months.iter().sum();
        by_cat.insert(name.clone(), serde_json::json!((year / 1e4).round() / 100.0));
        // daily-rate index: month's revenue per day over the year's revenue per day
        let idx: Vec<f64> = (0..12).map(|m| ((months[m] / nfsim::engine::DAYS_IN_MONTH[m] as f64) / (year / 365.0) * 1000.0).round() / 1000.0).collect();
        season.insert(name.clone(), serde_json::json!(idx));
    }
    let mut top: Vec<usize> = (0..s.revenue.len()).filter(|&b| engine.city.businesses.external[b] == 0).collect();
    top.sort_by(|&a, &b| s.revenue[b].partial_cmp(&s.revenue[a]).unwrap());
    let top10: Vec<_> = top.iter().take(10).map(|&b| serde_json::json!({
        "name": engine.city.businesses.name[b], "revenue": s.revenue[b].round(), "visits": s.visits[b]})).collect();
    let modes: u64 = s.mode_counts.iter().sum();
    let pct = |i: usize| if modes > 0 { (s.mode_counts[i] as f64 / modes as f64 * 1000.0).round() / 10.0 } else { 0.0 };
    let report = serde_json::json!({
        "mode": mode, "days": days, "seed": seed, "beta": (engine.beta * 1e4).round() / 1e4,
        "expected_median_driver_km": median,
        "simulated_median_driver_km": engine.median_driver_km(),
        "outings": s.outings,
        "outings_per_person5_per_day": if days > 0 { s.outings as f64 / persons5 as f64 / days as f64 } else { 0.0 },
        "mode_pct": {"driver": pct(0), "passenger": pct(1), "walk": pct(2), "transit": pct(3)},
        "all_in_person_spending_musd": (total / 1e4).round() / 100.0,
        "city_business_revenue_musd": (engine.city_revenue() / 1e4).round() / 100.0,
        "out_of_town_leakage_musd": (engine.out_of_town_revenue() / 1e4).round() / 100.0,
        "online_leakage_musd": (s.online_leakage / 1e4).round() / 100.0,
        "annual_budget_musd": (engine.total_budget() / 1e4).round() / 100.0,
        "revenue_by_category_musd": by_cat,
        "monthly_index_by_category": season,
        "top_businesses": top10,
        "setup_seconds": (setup * 100.0).round() / 100.0,
        "run_seconds": (run * 100.0).round() / 100.0,
    });
    let text = serde_json::to_string_pretty(&report).unwrap();
    if let Some(path) = arg(&args, "--out") {
        std::fs::write(&path, &text).expect("cannot write report");
    }
    println!("{}", text);
}
