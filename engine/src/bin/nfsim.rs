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
        eprintln!("usage: nfsim calibrate|calibrate-sales|route|run <city.json> [--days N] [--mode agent|flow] [--seed S] [--beta B] [--out FILE]");
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
    if args[1] == "route" {
        // nfsim route <city.json> --trips trips.json --out volumes.json ; trips: [[from_node, to_node, vehicles_per_day], ...]
        let trips: Vec<(u32, u32, f64)> = serde_json::from_str(&std::fs::read_to_string(arg(&args, "--trips").expect("--trips FILE")).unwrap())
            .expect("trips: [[from, to, vehicles], ...]");
        let draws: u32 = arg(&args, "--draws").map(|s| s.parse().unwrap()).unwrap_or(1);
        let spread: f64 = arg(&args, "--spread").map(|s| s.parse().unwrap()).unwrap_or(0.0);
        let vol: Vec<f64> = engine.route_volumes(&trips, draws, spread, seed).iter().map(|v| (v * 10.0).round() / 10.0).collect();
        std::fs::write(arg(&args, "--out").expect("--out FILE"), serde_json::to_string(&vol).unwrap()).unwrap();
        return;
    }
    if args[1] == "forecast" {
        // nfsim forecast <city.json> --sites sites.json --out out.json
        // sites: [[lat, lon, sim_category, size], ...] -> [[resident, tourist], ...] yearly revenue
        let sites: Vec<(f64, f64, u32, f64)> = serde_json::from_str(&std::fs::read_to_string(arg(&args, "--sites").expect("--sites FILE")).unwrap())
            .expect("sites: [[lat, lon, category, size], ...]");
        let (nl, no) = (&engine.city.nodes.lat, &engine.city.nodes.lon);
        let out: Vec<[f64; 2]> = sites.iter().map(|&(la, lo, c, size)| {
            let k = la.to_radians().cos();
            let node = (0..nl.len()).min_by(|&a, &b| {
                let da = (nl[a] - la).powi(2) + ((no[a] - lo) * k).powi(2);
                let db = (nl[b] - la).powi(2) + ((no[b] - lo) * k).powi(2);
                da.partial_cmp(&db).unwrap()
            }).unwrap() as u32;
            let f = engine.forecast(node, c, size, 1.0);
            [f.resident.round(), f.tourist.round()]
        }).collect();
        std::fs::write(arg(&args, "--out").expect("--out FILE"), serde_json::to_string(&out).unwrap()).unwrap();
        return;
    }
    if args[1] == "calibrate-sales" {
        calibrate_sales(&mut engine, arg(&args, "--targets").expect("--targets FILE"), arg(&args, "--out"));
        return;
    }
    if let Some(sc) = arg(&args, "--scenario") {
        // "us,domestic,overseas,base_stays,resident"
        let v: Vec<f64> = sc.split(',').map(|x| x.parse().expect("scenario numbers")).collect();
        engine.set_scenario(v[0], v[1], v[2], v[3], v[4]);
    }
    let traffic_out = arg(&args, "--traffic");
    if traffic_out.is_some() {
        engine.enable_traffic();
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
    let persons5: u64 = (0..engine.city.households.persons5.len()).filter(|&h| !engine.is_regional(h)).map(|h| engine.city.households.persons5[h] as u64).sum();
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
    let tour = &s.tour;
    let tourism = match &engine.city.tourism {
        None => serde_json::json!(null),
        Some(t) => {
            let mut by_cat = serde_json::Map::new();
            for (c, name) in t.categories.name.iter().enumerate() {
                let v: f64 = (0..12).map(|m| tour.month_category_revenue[m][c]).sum();
                by_cat.insert(name.clone(), serde_json::json!((v / 1e4).round() / 100.0));
            }
            let mut arrivals = serde_json::Map::new();
            let mut jul_aug = serde_json::Map::new();
            let mut total_visitors = 0.0;
            for (i, seg) in t.segments.iter().enumerate() {
                let year: f64 = tour.arrivals[i].iter().sum();
                total_visitors += year;
                arrivals.insert(seg.name.clone(), serde_json::json!(year.round()));
                jul_aug.insert(seg.name.clone(), serde_json::json!(((tour.arrivals[i][6] + tour.arrivals[i][7]) / year.max(1.0) * 1000.0).round() / 10.0));
            }
            let mut attendance = serde_json::Map::new();
            for b in 0..t.attendance_target.len() {
                if t.attendance_target[b] > 0.0 {
                    attendance.insert(engine.city.businesses.name[b].clone(), serde_json::json!({
                        "simulated": tour.persons[b].round(), "published": t.attendance_target[b]}));
                }
            }
            let fs = engine.city.sim_categories.iter().position(|c| c == "free_sight").map(|x| x as u32);
            let mut free: Vec<(String, f64)> = (0..tour.persons.len())
                .filter(|&b| Some(engine.city.businesses.sim_category[b]) == fs)
                .map(|b| (if engine.city.businesses.name[b].is_empty() { format!("Viewpoint #{b}") } else { engine.city.businesses.name[b].clone() }, tour.persons[b].round()))
                .collect();
            free.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
            free.truncate(8);
            let free_sights = serde_json::json!(free);
            let core: f64 = (0..tour.revenue.len()).filter(|&b| engine.city.businesses.core[b] == 1).map(|b| tour.revenue[b]).sum();
            let occ_month: Vec<f64> = (0..12).map(|m| (tour.room_nights_by_month[m] / (engine.hotel_rooms() * nfsim::engine::DAYS_IN_MONTH[m] as f64) * 1000.0).round() / 1000.0).collect();
            let mut top: Vec<usize> = (0..tour.revenue.len()).collect();
            top.sort_by(|&a, &b| tour.revenue[b].partial_cmp(&tour.revenue[a]).unwrap());
            let top10: Vec<_> = top.iter().take(10).map(|&b| serde_json::json!({"name": engine.city.businesses.name[b], "tourist_revenue": tour.revenue[b].round()})).collect();
            serde_json::json!({
                "visitors": total_visitors.round(), "visitors_by_segment": arrivals, "july_august_share_pct": jul_aug,
                "tourist_spending_at_businesses_musd": (engine.tourist_revenue() / 1e4).round() / 100.0,
                "accommodation_musd": (tour.accommodation_by_month.iter().sum::<f64>() / 1e4).round() / 100.0,
                "transport_fares_musd": (tour.fares / 1e4).round() / 100.0,
                "spending_by_category_musd": by_cat,
                "share_of_tourist_spending_in_tourist_core_pct": (core / engine.tourist_revenue().max(1.0) * 1000.0).round() / 10.0,
                "hotel_rooms": engine.hotel_rooms(), "hotel_occupancy": (engine.hotel_occupancy() * 1000.0).round() / 1000.0,
                "hotel_occupancy_by_month": occ_month, "parties_turned_away_from_full_lodging": tour.turned_away,
                "attendance": attendance, "top_tourist_businesses": top10,
                "parties": serde_json::to_value(&tour.stats).unwrap_or_default(),
                "arrivals_by_segment_month": tour.arrivals.iter().map(|m| m.iter().map(|x| x.round()).collect::<Vec<f64>>()).collect::<Vec<_>>(),
                "free_sight_visits": free_sights,
            })
        }
    };
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
        "inbound_from_neighbouring_towns_musd": (engine.inbound_revenue() / 1e4).round() / 100.0,
        "online_leakage_musd": (s.online_leakage / 1e4).round() / 100.0,
        "annual_budget_musd": (engine.total_budget() / 1e4).round() / 100.0,
        "revenue_by_category_musd": by_cat,
        "monthly_index_by_category": season,
        "top_businesses": top10,
        "tourism": tourism,
        "setup_seconds": (setup * 100.0).round() / 100.0,
        "run_seconds": (run * 100.0).round() / 100.0,
    });
    if let Some(path) = arg(&args, "--businesses") {
        // one line per city business: index, revenue, tourist revenue
        let mut csv = String::from("business,revenue,tourist_revenue\n");
        for b in 0..s.revenue.len() {
            if engine.city.businesses.external[b] == 0 {
                csv.push_str(&format!("{},{:.0},{:.0}\n", b, s.revenue[b], tour.revenue.get(b).copied().unwrap_or(0.0)));
            }
        }
        std::fs::write(&path, csv).expect("cannot write businesses file");
    }
    if let (Some(path), Some(w)) = (arg(&args, "--walkers"), engine.walkers.as_ref()) {
        let per_day: Vec<f64> = w.iter().map(|c| (c / days.max(1) as f64 * 10.0).round() / 10.0).collect();
        std::fs::write(&path, serde_json::to_string(&per_day).unwrap()).expect("cannot write walkers file");
    }
    if let (Some(path), Some(counts)) = (traffic_out, engine.traffic.as_ref()) {
        // average vehicles per day passing each road node
        let per_day: Vec<f64> = counts.iter().map(|c| (c / days.max(1) as f64).round()).collect();
        std::fs::write(&path, serde_json::to_string(&per_day).unwrap()).expect("cannot write traffic file");
    }
    let text = serde_json::to_string_pretty(&report).unwrap();
    if let Some(path) = arg(&args, "--out") {
        std::fs::write(&path, &text).expect("cannot write report");
    }
    println!("{}", text);
}

fn visitor_of(total: &[f64], resident: &[f64], g: usize) -> f64 {
    total[g] - resident[g]
}

/// Tune out-of-town leakage and the in-city share of tourist spending until each store type's
/// simulated sales match the government-based target.
fn calibrate_sales(engine: &mut Engine, targets_path: String, out: Option<String>) {
    let targets: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(targets_path).expect("cannot read targets")).unwrap();
    let groups = engine.city.business_groups.clone();
    let cats = engine.city.categories.name.clone();
    let tcats = engine.city.tourism.as_ref().map(|t| t.categories.name.clone()).unwrap_or_default();
    // each store type's main spending categories: (group, resident categories, tourist categories, led by tourists?)
    let plan: Vec<(&str, Vec<&str>, Vec<&str>, bool)> = vec![
        ("445", vec!["groceries", "alcohol"], vec!["food_from_stores"], true),
        ("449", vec!["household_goods"], vec![], false),
        ("455", vec!["general_merchandise"], vec![], false),
        ("456", vec!["pharmacy"], vec![], false),
        ("457", vec!["fuel"], vec!["fuel"], true),
        ("458", vec!["clothing"], vec!["clothing"], true),
        ("459", vec!["recreation_goods", "pets"], vec!["other_retail"], true),
        ("722", vec!["restaurants"], vec!["restaurants"], true),
    ];
    let mut ext = vec![1.0f64; cats.len()];
    let mut cap = vec![1.0f64; tcats.len()];
    let by_group = |e: &Engine| -> (Vec<f64>, Vec<f64>) {
        let (all, res) = (e.expected_yearly_revenue(), e.expected_yearly_resident_revenue());
        let (mut total, mut resident) = (vec![0f64; groups.len()], vec![0f64; groups.len()]);
        for b in 0..e.city.businesses.group.len() {
            let g = e.city.businesses.group[b] as usize;
            if g < groups.len() && e.city.businesses.external[b] == 0 {
                total[g] += all[b];
                resident[g] += res[b];
            }
        }
        (total, resident)
    };
    let (before, _) = by_group(engine);
    let mut npc_pull = 1.0f64;
    for round in 0..24 {
        let (total, resident) = by_group(engine);
        for (g, rc, tc, tourist_led) in &plan {
            let gi = match groups.iter().position(|x| x == g) { Some(i) => i, None => continue };
            let target = targets[*g]["supply_side_sales"].as_f64().unwrap_or(0.0);
            if target <= 0.0 { continue; }
            let ratio = target / total[gi].max(1.0);
            let _ = (visitor_of(&total, &resident, gi), tourist_led);
            // Leakage to the towns and the in-city share of tourist spending can both explain a gap,
            // and one sales figure cannot tell them apart. Where tourists matter for a store type the
            // correction is shared between the two; elsewhere it all goes to leakage.
            let mut shared = false;
            if *tourist_led {
                for name in tc {
                    if let Some(c) = tcats.iter().position(|x| x == name) {
                        cap[c] = (cap[c] * ratio.powf(0.5)).clamp(0.05, 1.0);
                        shared = true;
                    }
                }
            }
            for name in rc {
                if let Some(c) = cats.iter().position(|x| x == name) {
                    ext[c] = (ext[c] / ratio.powf(if shared { 0.7 } else { 1.2 })).clamp(0.02, 50.0);
                }
            }
        }
        engine.set_multipliers(ext.clone(), cap.clone());
        // Niagara Parks. Admissions: fit paid attraction visits per party (attendance stays fitted, so more
        // visits per party means less spent per admission). Everything else it runs: scale its pull.
        if let (Some(&total), Some(&adm)) = (engine.city.operator_targets.get("niagara_parks"), engine.city.operator_targets.get("niagara_parks_admissions")) {
            let got_adm = engine.operator_revenue_kind(1, true);
            if got_adm > 0.0 {
                let v = (engine.attraction_visits_per_party_day() * (got_adm / adm).powf(0.9)).clamp(0.2, 6.0);
                engine.set_attraction_visits_per_party_day(v);
            }
            let got_rest = engine.operator_revenue_kind(1, false);
            if got_rest > 0.0 {
                let f = ((total - adm) / got_rest).powf(0.8).clamp(0.2, 5.0);
                npc_pull *= f;
                engine.scale_operator_pull_kind(1, false, f);
            }
        }
        eprintln!("round {round}: beta {:.3}, Niagara Parks {:.1}M, casinos {:.1}M", engine.beta,
                  engine.operator_revenue(1) / 1e6, engine.operator_revenue(2) / 1e6);
        eprintln!("          Niagara Parks admissions {:.1}M, other {:.1}M, attraction visits per party-day {:.2}",
                  engine.operator_revenue_kind(1, true) / 1e6, engine.operator_revenue_kind(1, false) / 1e6, engine.attraction_visits_per_party_day());
    }
    let (after, resident) = by_group(engine);
    let mut table = serde_json::Map::new();
    println!("{:<6}{:>12}{:>12}{:>12}{:>10}", "group", "target $M", "before $M", "after $M", "tourist%");
    for (gi, g) in groups.iter().enumerate() {
        let target = targets[g]["supply_side_sales"].as_f64().unwrap_or(0.0);
        if before[gi] <= 0.0 { continue; }
        println!("{:<6}{:>12.1}{:>12.1}{:>12.1}{:>10.0}", g, target / 1e6, before[gi] / 1e6, after[gi] / 1e6, (1.0 - resident[gi] / after[gi].max(1.0)) * 100.0);
        table.insert(g.clone(), serde_json::json!({"target": target.round(), "before": before[gi].round(), "after": after[gi].round()}));
    }
    let result = serde_json::json!({
        "beta": engine.beta,
        "external_scale": cats.iter().cloned().zip(ext.iter().map(|&v| serde_json::json!(v))).collect::<serde_json::Map<String, serde_json::Value>>(),
        "tourist_capture": tcats.iter().cloned().zip(cap.iter().map(|&v| serde_json::json!(v))).collect::<serde_json::Map<String, serde_json::Value>>(),
        "sales_by_group": table,
        "operator_pull": {"niagara_parks": npc_pull},
        "attraction_visits_per_party_day": engine.attraction_visits_per_party_day(),
        "operator_revenue": {"niagara_parks": engine.operator_revenue(1).round(), "casino": engine.operator_revenue(2).round(),
                             "targets": &engine.city.operator_targets},
    });
    if let Some(path) = out {
        std::fs::write(path, serde_json::to_string_pretty(&result).unwrap()).expect("cannot write");
    }
}
