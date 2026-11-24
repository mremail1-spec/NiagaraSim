//! The simulation engine: resident outings, destination choice on the road network, spending.
//!
//! Time moves in hours. Two ways to advance:
//! * `step_hour` (agent mode) draws every household's outings one by one, so trips can be shown.
//! * `step_day_flow` (flow mode) books each business its expected revenue for the day directly.
//! Both use the same destination probabilities, so their totals agree.
use crate::city::City;
use crate::rng::Rng;
use serde::{Deserialize, Serialize};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

pub const DAYS_IN_MONTH: [u32; 12] = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
const HIST_BIN_KM: f64 = 0.1;
const HIST_BINS: usize = 800;

#[derive(Clone, Copy, PartialEq, Eq, Debug, Serialize, Deserialize)]
pub enum Mode {
    Driver,
    Passenger,
    Walk,
    Transit,
}

/// One spending trip, kept for the hour it happened in so the map can draw it.
#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct Trip {
    pub household: u32,
    pub business: u32,
    pub category: u8,
    pub mode: Mode,
    pub km: f32,
    pub spend: f32,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
struct Outing {
    household: u32,
    business: u32,
    category: u8,
}

/// Everything that changes as time passes. Saving and restoring this is how the game rewinds.
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Snapshot {
    pub day: u32,
    pub hour: u32,
    pub(crate) rng: Rng,
    pub revenue: Vec<f64>,
    pub visits: Vec<u64>,
    /// Local revenue by month (12) and category.
    pub month_category_revenue: Vec<Vec<f64>>,
    pub online_leakage: f64,
    /// City residents' spending in neighbouring towns, and town residents' spending in the city.
    #[serde(default)]
    pub leakage: f64,
    #[serde(default)]
    pub inbound: f64,
    pub outings: u64,
    pub mode_counts: [u64; 4],
    driver_km_hist: Vec<u64>,
    day_plan: Vec<Vec<Outing>>,
    /// Tourists in town, their plans and what they have spent.
    #[serde(default)]
    pub tour: crate::tourists::TourState,
}

struct HeapItem(f32, u32);
impl PartialEq for HeapItem {
    fn eq(&self, o: &Self) -> bool {
        self.0 == o.0
    }
}
impl Eq for HeapItem {}
impl PartialOrd for HeapItem {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for HeapItem {
    fn cmp(&self, o: &Self) -> Ordering {
        o.0.partial_cmp(&self.0).unwrap_or(Ordering::Equal)
    }
}

pub struct Engine {
    pub city: City,
    pub beta: f64,
    pub(crate) n_nodes: usize,
    pub(crate) n_cat: usize,
    /// Each business's index into the list of distinct destination road nodes.
    pub(crate) dest_of_business: Vec<u32>,
    pub(crate) n_dest: usize,
    /// Travel time and distance from every road node to every destination, [dest * n_nodes + node].
    pub(crate) minutes: Vec<f32>,
    pub(crate) km: Vec<f32>,
    /// Next road node on the fastest route from `node` toward `dest`.
    pub(crate) next_hop: Vec<u16>,
    /// Businesses serving each category, and their pull.
    pub(crate) cat_business: Vec<Vec<u32>>,
    pub(crate) cat_pull: Vec<Vec<f64>>,
    /// Road nodes that have homes, and each node's position in that list (-1 if none).
    pub(crate) home_nodes: Vec<u32>,
    pub(crate) node_slot: Vec<i32>,
    /// Cumulative destination probabilities, [slot * n_cat + category] over cat_business[category].
    pub(crate) cdf: Vec<Vec<f32>>,
    /// Expected local revenue per year for each business from each category (flow mode).
    /// People walking past each road node, when traffic counting is on.
    pub walkers: Option<Vec<f64>>,
    pub(crate) flow: Vec<Vec<f64>>,
    /// Expected yearly leakage out of the city and inbound spending from the towns, by category.
    pub(crate) flow_leak: Vec<f64>,
    pub(crate) flow_inbound: Vec<f64>,
    pub(crate) hh_rate: Vec<f64>,
    pub(crate) hh_pcat: Vec<f32>,
    pub(crate) hh_ticket: Vec<f32>,
    pub(crate) hour_cdf: [f64; 24],
    pub walk_near: f64,
    pub(crate) state: Snapshot,
    /// Trips made in the hour most recently stepped (agent mode).
    pub last_trips: Vec<Trip>,
    /// Index of the first business added after the city was loaded (equals the count when none were).
    pub(crate) first_added: usize,
    /// Tourist model (absent when the city file has no tourism block).
    pub(crate) tour: Option<crate::tourists::TourRt>,
    /// Outlook multipliers on the calibrated year: [US, domestic, overseas, hotel base stays, resident spending].
    pub scenario: [f64; 5],
    pub(crate) scenario_base: Option<(Vec<f64>, Vec<f32>)>,
    /// Attraction pulls fitted to published attendance, kept so an outlook does not refit them.
    pub(crate) fitted_attraction_pull: Option<Vec<f64>>,
    /// Format weights and lodging capacities as built, kept while closures are applied.
    pub(crate) before_closures: Option<(Vec<f64>, Vec<u32>)>,
    /// Vehicles counted passing each road node so far (agent mode), when switched on.
    pub traffic: Option<Vec<f64>>,
    /// Tourist purchases made in the hour most recently stepped (agent mode).
    pub last_tourist_trips: Vec<crate::tourists::TouristTrip>,
}

pub fn month_of_day(day_of_year: u32) -> usize {
    let mut d = day_of_year % 365;
    for (m, len) in DAYS_IN_MONTH.iter().enumerate() {
        if d < *len {
            return m;
        }
        d -= len;
    }
    11
}

impl Engine {
    /// Build the engine with a fixed distance-decay `beta`.
    pub fn new(mut city: City, beta: f64, seed: u64) -> Engine {
        city.prepare_sites();
        let n_nodes = city.nodes.lat.len();
        let n_cat = city.categories.name.len();
        let n_biz = city.businesses.node.len();
        let n_hh = city.households.node.len();
        assert!(n_nodes < u16::MAX as usize, "road network too large for 16-bit node ids");

        // --- shortest routes from every node to every distinct business node ---
        let mut dest_index = vec![u32::MAX; n_nodes];
        let mut dests: Vec<u32> = Vec::new();
        let mut dest_of_business = Vec::with_capacity(n_biz);
        for &node in &city.businesses.node {
            if dest_index[node as usize] == u32::MAX {
                dest_index[node as usize] = dests.len() as u32;
                dests.push(node);
            }
            dest_of_business.push(dest_index[node as usize]);
        }
        let n_dest = dests.len();
        // reversed adjacency: to reach `to`, you can come from `from`. Routes are chosen on the
        // route cost; the minutes recorded are the real driving minutes along the chosen route.
        let cost = city.edges.cost();
        let mut rev: Vec<Vec<(u32, f32, f32, f32)>> = vec![Vec::new(); n_nodes];
        for e in 0..city.edges.from.len() {
            rev[city.edges.to[e] as usize].push((city.edges.from[e], cost[e], city.edges.minutes[e], city.edges.km[e]));
        }
        let mut minutes = vec![f32::INFINITY; n_dest * n_nodes];
        let mut km = vec![0f32; n_dest * n_nodes];
        let mut next_hop = vec![u16::MAX; n_dest * n_nodes];
        let mut best = vec![f32::INFINITY; n_nodes];
        for (d, &dest) in dests.iter().enumerate() {
            let base = d * n_nodes;
            best.iter_mut().for_each(|x| *x = f32::INFINITY);
            let mut heap = BinaryHeap::new();
            best[dest as usize] = 0.0;
            minutes[base + dest as usize] = 0.0;
            next_hop[base + dest as usize] = dest as u16;
            heap.push(HeapItem(0.0, dest));
            while let Some(HeapItem(c, u)) = heap.pop() {
                if c > best[u as usize] {
                    continue;
                }
                for &(v, dc, dt, dk) in &rev[u as usize] {
                    let nc = c + dc;
                    if nc < best[v as usize] {
                        best[v as usize] = nc;
                        minutes[base + v as usize] = minutes[base + u as usize] + dt;
                        km[base + v as usize] = km[base + u as usize] + dk;
                        next_hop[base + v as usize] = u as u16;
                        heap.push(HeapItem(nc, v));
                    }
                }
            }
        }

        // --- which businesses serve which category, and how strongly they pull ---
        let mut cat_business = vec![Vec::new(); n_cat];
        let mut cat_pull = vec![Vec::new(); n_cat];
        for c in 0..n_cat {
            for b in 0..n_biz {
                let w = city.categories.served_by[c][city.businesses.sim_category[b] as usize];
                if w > 0.0 && city.businesses.external[b] == 0 {
                    let size = city.businesses.revenue_prior[b].max(30_000.0);
                    cat_business[c].push(b as u32);
                    cat_pull[c].push(w * city.pull_weight(b) * size.powf(city.params.attractiveness_exponent));
                }
            }
            // a neighbouring town pulls like the whole city's offer in this category, scaled by its size
            let city_pull: f64 = cat_pull[c].iter().sum();
            for b in 0..n_biz {
                let w = city.categories.served_by[c][city.businesses.sim_category[b] as usize];
                if w > 0.0 && city.businesses.external[b] == 1 && city_pull > 0.0 {
                    cat_business[c].push(b as u32);
                    let cal = city.categories.external_scale.get(c).copied().unwrap_or(1.0);
                    cat_pull[c].push(w * city_pull * city.businesses.pull_scale[b] * cal);
                }
            }
        }

        // --- households: outings per day, how they split by category, spend per visit ---
        let mut node_slot = vec![-1i32; n_nodes];
        let mut home_nodes = Vec::new();
        let mut hh_rate = vec![0f64; n_hh];
        let mut hh_pcat = vec![0f32; n_hh * n_cat];
        let mut hh_ticket = vec![0f32; n_hh * n_cat];
        for h in 0..n_hh {
            let node = city.households.node[h] as usize;
            if node_slot[node] < 0 {
                node_slot[node] = home_nodes.len() as i32;
                home_nodes.push(node as u32);
            }
            let rate = city.households.persons5[h] as f64 * city.params.outings_per_person5_per_day;
            hh_rate[h] = rate;
            let b = &city.households.budget[h * n_cat..(h + 1) * n_cat];
            let visits: Vec<f64> = (0..n_cat)
                .map(|c| if cat_business[c].is_empty() { 0.0 } else { b[c] as f64 / city.categories.typical_visit_spend[c] })
                .collect();
            let total: f64 = visits.iter().sum();
            for c in 0..n_cat {
                let p = if total > 0.0 { visits[c] / total } else { 0.0 };
                hh_pcat[h * n_cat + c] = p as f32;
                // spend per visit chosen so that a year of visits spends exactly the annual budget
                hh_ticket[h * n_cat + c] = if p > 0.0 && rate > 0.0 { (b[c] as f64 / (365.0 * rate * p)) as f32 } else { 0.0 };
            }
        }

        let mut hour_cdf = [0f64; 24];
        let total: f64 = city.params.hour_weights.iter().sum();
        let mut acc = 0.0;
        for h in 0..24 {
            acc += city.params.hour_weights[h] / total;
            hour_cdf[h] = acc;
        }

        let state = Snapshot {
            day: 0,
            hour: 0,
            rng: Rng::new(seed),
            revenue: vec![0.0; n_biz],
            visits: vec![0; n_biz],
            month_category_revenue: vec![vec![0.0; n_cat]; 12],
            online_leakage: 0.0,
            leakage: 0.0,
            inbound: 0.0,
            outings: 0,
            mode_counts: [0; 4],
            driver_km_hist: vec![0; HIST_BINS],
            day_plan: vec![Vec::new(); 24],
            tour: crate::tourists::TourState::default(),
        };
        let mut e = Engine {
            city, beta, n_nodes, n_cat, dest_of_business, n_dest, minutes, km, next_hop, cat_business, cat_pull,
            home_nodes, node_slot, cdf: Vec::new(), flow: Vec::new(), flow_leak: Vec::new(), flow_inbound: Vec::new(), hh_rate, hh_pcat, hh_ticket, hour_cdf,
            walk_near: 0.0, state, last_trips: Vec::new(), tour: None, last_tourist_trips: Vec::new(), first_added: n_biz, traffic: None, walkers: None, scenario: [1.0; 5], scenario_base: None, fitted_attraction_pull: None, before_closures: None,
        };
        e.set_beta(beta);
        e
    }

    /// Build the engine and tune `beta` so the median driving trip matches the travel survey.
    pub fn calibrated(city: City, seed: u64) -> Engine {
        let target = city.params.median_driver_trip_km_target;
        let mut e = Engine::new(city, 1.0, seed);
        let (mut lo, mut hi) = (0.0f64, 6.0f64);
        for _ in 0..30 {
            let mid = (lo + hi) / 2.0;
            // a larger beta keeps people closer to home, so the median falls as beta rises
            if e.expected_trip_stats(mid).0 > target {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        e.set_beta((lo + hi) / 2.0);
        e
    }

    /// Travel time in minutes from a road node to a business (including the leg beyond the city for a town).
    pub fn trip_minutes(&self, node: usize, b: usize) -> f64 {
        (self.minutes[self.dest_of_business[b] as usize * self.n_nodes + node] + self.city.businesses.extra_min[b]) as f64
    }

    /// Road distance in km from a road node to a business.
    pub fn trip_km(&self, node: usize, b: usize) -> f64 {
        (self.km[self.dest_of_business[b] as usize * self.n_nodes + node] + self.city.businesses.extra_km[b]) as f64
    }

    pub(crate) fn weights(&self, node: usize, c: usize, beta: f64, out: &mut Vec<f64>) -> f64 {
        out.clear();
        for (i, &b) in self.cat_business[c].iter().enumerate() {
            let t = self.trip_minutes(node, b as usize);
            out.push(self.cat_pull[c][i] / t.max(self.city.params.min_minutes).powf(beta));
        }
        self.nearest_branch_only(node, &self.cat_business[c], &self.cat_pull[c], out)
    }

    /// Keep only the nearest branch of each chain among `list` (weights in `out`), and return the total.
    /// People choose the chain, then go to its nearest branch: the chain keeps the share it would have
    /// with every branch weighed at its own distance, and all of it goes to the nearest branch.
    pub(crate) fn nearest_branch_only(&self, node: usize, list: &[u32], _pull: &[f64], out: &mut [f64]) -> f64 {
        let chain = &self.city.businesses.chain;
        if !chain.is_empty() {
            // chain -> (nearest index, its minutes, the chain's summed weights)
            let mut best: std::collections::HashMap<i32, (usize, f64, f64)> = std::collections::HashMap::new();
            for (i, &b) in list.iter().enumerate() {
                let k = chain.get(b as usize).copied().unwrap_or(-1);
                if k < 0 {
                    continue;
                }
                let t = self.trip_minutes(node, b as usize);
                let e = best.entry(k).or_insert((i, t, 0.0));
                e.2 += out[i];
                if t < e.1 {
                    e.0 = i;
                    e.1 = t;
                }
            }
            for (i, &b) in list.iter().enumerate() {
                let k = chain.get(b as usize).copied().unwrap_or(-1);
                if k < 0 {
                    continue;
                }
                let (j, _, total) = best[&k];
                if i == j {
                    out[i] = total;
                } else {
                    out[i] = 0.0;
                }
            }
        }
        out.iter().sum()
    }

    /// Outings per day leaving each home node for each category.
    fn outing_weights(&self) -> Vec<f64> {
        let mut o = vec![0f64; self.home_nodes.len() * self.n_cat];
        for h in 0..self.hh_rate.len() {
            let slot = self.node_slot[self.city.households.node[h] as usize] as usize;
            if self.is_regional(h) {
                continue;       // the travel survey target describes city residents' trips
            }
            for c in 0..self.n_cat {
                o[slot * self.n_cat + c] += self.hh_rate[h] * self.hh_pcat[h * self.n_cat + c] as f64;
            }
        }
        o
    }

    /// For a given beta, without simulating: (median driving trip km, share of trips short enough
    /// to walk, share of trips walked).
    pub fn expected_trip_stats(&self, beta: f64) -> (f64, f64, f64) {
        let outings = self.outing_weights();
        let mut hist = vec![0f64; HIST_BINS];
        let mut w = Vec::new();
        for (slot, &node) in self.home_nodes.iter().enumerate() {
            for c in 0..self.n_cat {
                let o = outings[slot * self.n_cat + c];
                if o <= 0.0 {
                    continue;
                }
                let total = self.weights(node as usize, c, beta, &mut w);
                for (i, &b) in self.cat_business[c].iter().enumerate() {
                    let d = self.trip_km(node as usize, b as usize);
                    hist[((d / HIST_BIN_KM) as usize).min(HIST_BINS - 1)] += o * w[i] / total;
                }
            }
        }
        let all: f64 = hist.iter().sum();
        let short_bins = (self.city.params.walk_max_km / HIST_BIN_KM) as usize;
        let short: f64 = hist[..short_bins].iter().sum::<f64>() / all;
        let m = &self.city.params.mode_pct;
        let walk_target = m.walk_cycle / (m.auto_driver + m.auto_passenger + m.walk_cycle + m.transit_school_bus_other);
        let walk_near = (walk_target / short).min(1.0);
        // driving trips: everything not walked (walking only happens on short trips)
        let mut acc = 0.0;
        let driven: f64 = all * (1.0 - short * walk_near);
        let mut median = 0.0;
        for (i, v) in hist.iter().enumerate() {
            acc += if i < short_bins { v * (1.0 - walk_near) } else { *v };
            if acc >= driven / 2.0 {
                median = (i as f64 + 0.5) * HIST_BIN_KM;
                break;
            }
        }
        (median, short, short * walk_near)
    }

    /// Fix beta and rebuild the destination tables.
    pub fn set_beta(&mut self, beta: f64) {
        self.beta = beta;
        let (_, short, _) = self.expected_trip_stats(beta);
        let m = &self.city.params.mode_pct;
        let walk_target = m.walk_cycle / (m.auto_driver + m.auto_passenger + m.walk_cycle + m.transit_school_bus_other);
        self.walk_near = (walk_target / short).min(1.0);

        let n_slots = self.home_nodes.len();
        let n_biz = self.city.businesses.node.len();
        let mut budget_at = vec![0f64; n_slots * self.n_cat];
        for h in 0..self.hh_rate.len() {
            let slot = self.node_slot[self.city.households.node[h] as usize] as usize;
            for c in 0..self.n_cat {
                budget_at[slot * self.n_cat + c] += self.city.households.budget[h * self.n_cat + c] as f64;
            }
        }
        let mut slot_regional = vec![false; n_slots];
        for h in 0..self.hh_rate.len() {
            if self.is_regional(h) {
                slot_regional[self.node_slot[self.city.households.node[h] as usize] as usize] = true;
            }
        }
        let (mut flow_leak, mut flow_inbound) = (vec![0f64; self.n_cat], vec![0f64; self.n_cat]);
        let mut cdf = Vec::with_capacity(n_slots * self.n_cat);
        let mut flow = vec![vec![0f64; n_biz]; self.n_cat];
        let mut w = Vec::new();
        for (slot, &node) in self.home_nodes.iter().enumerate() {
            for c in 0..self.n_cat {
                let total = self.weights(node as usize, c, beta, &mut w);
                let local = if self.city.categories.retail[c] == 1 { 1.0 - self.city.params.online_share_of_retail } else { 1.0 };
                let mut acc = 0.0;
                let mut row = Vec::with_capacity(w.len());
                for (i, &b) in self.cat_business[c].iter().enumerate() {
                    let p = if total > 0.0 { w[i] / total } else { 0.0 };
                    acc += p;
                    row.push(acc as f32);
                    let amount = budget_at[slot * self.n_cat + c] * p * local;
                    flow[c][b as usize] += amount;
                    let external = self.city.businesses.external[b as usize] == 1;
                    if slot_regional[slot] && !external {
                        flow_inbound[c] += amount;
                    } else if !slot_regional[slot] && external {
                        flow_leak[c] += amount;
                    }
                }
                cdf.push(row);
            }
        }
        self.cdf = cdf;
        self.flow = flow;
        self.flow_leak = flow_leak;
        self.flow_inbound = flow_inbound;
        self.build_tourism();
    }

    pub fn state(&self) -> &Snapshot {
        &self.state
    }

    pub fn snapshot(&self) -> Snapshot {
        self.state.clone()
    }

    pub fn restore(&mut self, snapshot: Snapshot) {
        self.state = snapshot;
        self.last_trips.clear();
        self.last_tourist_trips.clear();
    }

    pub(crate) fn season(&self, c: usize) -> f64 {
        self.city.categories.season[c][month_of_day(self.state.day)]
    }

    pub fn is_open(&self, b: usize, hour: u32) -> bool {
        let (o, c) = (self.city.businesses.open[b], self.city.businesses.close[b]);
        (hour >= o && hour < c) || (c > 24 && hour + 24 < c)
    }

    /// Decide every household's outings for the coming day and file them by hour.
    fn plan_day(&mut self) {
        let n_cat = self.n_cat;
        let season: Vec<f64> = (0..n_cat).map(|c| self.season(c)).collect();
        for bucket in self.state.day_plan.iter_mut() {
            bucket.clear();
        }
        let mut rates = vec![0f64; n_cat];
        for h in 0..self.hh_rate.len() {
            let mut sum = 0.0;
            for c in 0..n_cat {
                rates[c] = self.hh_pcat[h * n_cat + c] as f64 * season[c];
                sum += rates[c];
            }
            if sum <= 0.0 {
                continue;
            }
            for _ in 0..self.state.rng.poisson(self.hh_rate[h] * sum) {
                let mut u = self.state.rng.f64() * sum;
                let mut cat = n_cat - 1;
                for c in 0..n_cat {
                    if u < rates[c] {
                        cat = c;
                        break;
                    }
                    u -= rates[c];
                }
                // destination first, then an hour when that business is open: closing times
                // move a visit to another hour, they never send it to a different shop
                let node = self.city.households.node[h] as usize;
                let row = &self.cdf[self.node_slot[node] as usize * n_cat + cat];
                if row.is_empty() {
                    continue;
                }
                let u = self.state.rng.f64() as f32;
                let b = self.cat_business[cat][row.partition_point(|&x| x <= u).min(row.len() - 1)] as usize;
                let mut open_w = [0f64; 24];
                let mut open_total = 0.0;
                for hr in 0..24 {
                    if self.is_open(b, hr as u32) {
                        open_w[hr] = self.city.params.hour_weights[hr];
                        open_total += open_w[hr];
                    }
                }
                let uh = self.state.rng.f64();
                let hour = if open_total > 0.0 {
                    let mut x = uh * open_total;
                    let mut pick = 23;
                    for hr in 0..24 {
                        if x < open_w[hr] {
                            pick = hr;
                            break;
                        }
                        x -= open_w[hr];
                    }
                    pick
                } else {
                    self.hour_cdf.iter().position(|&x| uh < x).unwrap_or(23)
                };
                self.state.day_plan[hour].push(Outing { household: h as u32, business: b as u32, category: cat as u8 });
            }
        }
    }

    /// Advance one hour, simulating each outing (agent mode).
    pub fn step_hour(&mut self) {
        if self.state.hour == 0 {
            self.plan_day();
            self.tour_plan_day();
        }
        self.tour_step_hour();
        let hour = self.state.hour;
        let month = month_of_day(self.state.day);
        let outings = std::mem::take(&mut self.state.day_plan[hour as usize]);
        self.last_trips.clear();
        let m = self.city.params.mode_pct.clone();
        let transit_share = m.transit_school_bus_other / (m.auto_driver + m.auto_passenger + m.transit_school_bus_other);
        let driver_share = m.auto_driver / (m.auto_driver + m.auto_passenger);
        for o in &outings {
            let h = o.household as usize;
            let c = o.category as usize;
            let node = self.city.households.node[h] as usize;
            let b = o.business as usize;
            let d = self.trip_km(node, b);
            let mode = if d <= self.city.params.walk_max_km && self.state.rng.f64() < self.walk_near {
                Mode::Walk
            } else if self.state.rng.f64() < transit_share {
                Mode::Transit
            } else if self.city.households.vehicles[h] > 0 && self.state.rng.f64() < driver_share {
                Mode::Driver
            } else {
                Mode::Passenger
            };
            let spend = self.hh_ticket[h * self.n_cat + c] as f64 * self.scenario[4] * self.state.rng.lognormal_mean1(self.city.params.spend_sigma);
            let online = if self.city.categories.retail[c] == 1 { self.city.params.online_share_of_retail } else { 0.0 };
            let local = spend * (1.0 - online);
            self.state.revenue[b] += local;
            self.state.visits[b] += 1;
            let external = self.city.businesses.external[b] == 1;
            if self.is_regional(h) && !external {
                self.state.inbound += local;
            } else if !self.is_regional(h) && external {
                self.state.leakage += local;
            }
            self.state.month_category_revenue[month][c] += local;
            self.state.online_leakage += spend * online;
            if mode == Mode::Walk && !self.is_regional(h) {
                self.count_walkers(node as u32, b as u32, 2.0);                                      // there and back
            }
            if mode == Mode::Driver || self.is_regional(h) {
                self.count_vehicles(self.is_regional(h) as usize, node as u32, b as u32, 2.0);        // there and back
            }
            if self.is_regional(h) {
                self.last_trips.push(Trip { household: o.household, business: b as u32, category: o.category, mode: Mode::Driver, km: d as f32, spend: spend as f32 });
                continue;       // town shoppers are not part of the city's trip statistics
            }
            self.state.outings += 1;
            self.state.mode_counts[mode as usize] += 1;
            if mode == Mode::Driver {
                self.state.driver_km_hist[((d / HIST_BIN_KM) as usize).min(HIST_BINS - 1)] += 1;
            }
            self.last_trips.push(Trip { household: o.household, business: b as u32, category: o.category, mode, km: d as f32, spend: spend as f32 });
        }
        self.state.hour += 1;
        if self.state.hour == 24 {
            self.state.hour = 0;
            self.state.day += 1;
        }
    }

    /// Advance one day in agent mode.
    pub fn step_day(&mut self) {
        for _ in 0..24 {
            self.step_hour();
        }
    }

    /// Advance one day by booking expected revenue directly (flow mode). Must start at hour 0.
    pub fn step_day_flow(&mut self) {
        assert_eq!(self.state.hour, 0, "flow mode advances whole days");
        self.tour_flow_day();
        let month = month_of_day(self.state.day);
        for c in 0..self.n_cat {
            let k = self.season(c) / 365.0;
            let mut cat_total = 0.0;
            for (b, f) in self.flow[c].iter().enumerate() {
                if *f > 0.0 {
                    self.state.revenue[b] += f * k;
                    cat_total += f * k;
                }
            }
            self.state.month_category_revenue[month][c] += cat_total;
            self.state.leakage += self.flow_leak[c] * k;
            self.state.inbound += self.flow_inbound[c] * k;
            if self.city.categories.retail[c] == 1 {
                let share = self.city.params.online_share_of_retail;
                self.state.online_leakage += cat_total * share / (1.0 - share);
            }
        }
        self.state.day += 1;
    }

    /// Median distance of the driving trips simulated so far.
    pub fn median_driver_km(&self) -> f64 {
        let total: u64 = self.state.driver_km_hist.iter().sum();
        let mut acc = 0;
        for (i, v) in self.state.driver_km_hist.iter().enumerate() {
            acc += v;
            if acc * 2 >= total && total > 0 {
                return (i as f64 + 0.5) * HIST_BIN_KM;
            }
        }
        0.0
    }

    /// Road nodes along the fastest route from a home node to a business.
    pub fn route(&self, from_node: u32, business: u32) -> Vec<u32> {
        let base = self.dest_of_business[business as usize] as usize * self.n_nodes;
        let dest = self.city.businesses.node[business as usize];
        let mut path = vec![from_node];
        let mut at = from_node;
        while at != dest && path.len() <= self.n_nodes {
            at = self.next_hop[base + at as usize] as u32;
            path.push(at);
        }
        path
    }

    /// Start counting vehicles at every road node (agent mode only).
    pub fn enable_traffic(&mut self) {
        self.walkers = Some(vec![0.0; self.n_nodes]);
        self.traffic = Some(vec![0.0; self.n_nodes * 4]);
    }

    /// Add `vehicles` to every road node on the fastest route from `from_node` to a business.
    /// `source`: 0 city residents, 1 shoppers from neighbouring towns, 2 visitors getting around, 3 visitors arriving and leaving.
    /// Add `people` walking every road node on the route from `from_node` to a business (foot traffic).
    pub(crate) fn count_walkers(&mut self, from_node: u32, business: u32, people: f64) {
        if self.walkers.is_none() {
            return;
        }
        let base = self.dest_of_business[business as usize] as usize * self.n_nodes;
        let dest = self.city.businesses.node[business as usize];
        let n = self.n_nodes;
        let mut path = vec![from_node];
        let mut at = from_node;
        while at != dest && path.len() < n {
            at = self.next_hop[base + at as usize] as u32;
            path.push(at);
        }
        let w = self.walkers.as_mut().unwrap();
        for p in path {
            w[p as usize] += people;
        }
    }

    pub(crate) fn count_vehicles(&mut self, source: usize, from_node: u32, business: u32, vehicles: f64) {
        if self.traffic.is_none() {
            return;
        }
        let base = self.dest_of_business[business as usize] as usize * self.n_nodes;
        let dest = self.city.businesses.node[business as usize];
        let counts = self.traffic.as_mut().unwrap();
        let mut at = from_node;
        let mut steps = 0;
        counts[at as usize * 4 + source] += vehicles;
        while at != dest && steps < self.n_nodes {
            at = self.next_hop[base + at as usize] as u32;
            counts[at as usize * 4 + source] += vehicles;
            steps += 1;
        }
    }

    pub fn route_km(&self, from_node: u32, business: u32) -> f64 {
        self.trip_km(from_node as usize, business as usize)
    }

    /// City residents' spending so far that went to neighbouring towns.
    pub fn out_of_town_revenue(&self) -> f64 {
        self.state.leakage
    }

    /// Spending so far by residents of neighbouring towns at city businesses.
    pub fn inbound_revenue(&self) -> f64 {
        self.state.inbound
    }

    pub fn is_regional(&self, household: usize) -> bool {
        self.city.households.regional.get(household).copied().unwrap_or(0) == 1
    }

    /// Revenue so far earned by businesses inside the city.
    pub fn city_revenue(&self) -> f64 {
        (0..self.state.revenue.len()).filter(|&b| self.city.businesses.external[b] == 0).map(|b| self.state.revenue[b]).sum()
    }

    pub fn total_budget(&self) -> f64 {
        self.city.households.budget.iter().map(|&x| x as f64).sum()
    }

    pub fn category_count(&self) -> usize {
        self.n_cat
    }

    pub fn destination_count(&self) -> usize {
        self.n_dest
    }
}
