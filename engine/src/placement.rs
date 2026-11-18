//! Place a business: forecast what a new business would earn at a spot, and add it to the city.
//!
//! The newcomer enters the same destination choice as everyone else. Residents' and tourists'
//! total spending does not change, so everything it earns is taken from an existing business, a
//! neighbouring town, or (for the forecast) left where it was.
use crate::engine::{Engine, DAYS_IN_MONTH};
use crate::tourists::{GROUP_CAMPING, GROUP_DAY, GROUP_HOTEL, GROUP_PRIVATE, GROUP_RENTAL};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

#[derive(Clone, Debug)]
pub struct Forecast {
    /// Expected yearly revenue from residents and from tourists.
    pub resident: f64,
    pub tourist: f64,
    /// Tourist revenue split by where the visitors come from: domestic, US, overseas.
    pub tourist_by_origin: [f64; 3],
    /// Part of the revenue that residents would otherwise have spent in neighbouring towns.
    pub from_out_of_town: f64,
    pub monthly: [f64; 12],
    /// City businesses that lose the most, with the yearly amount lost.
    pub losses: Vec<(u32, f64)>,
    /// Everything taken from existing businesses and neighbouring towns together.
    pub total_taken: f64,
}

struct Item(f32, u32);
impl PartialEq for Item {
    fn eq(&self, o: &Self) -> bool {
        self.0 == o.0
    }
}
impl Eq for Item {}
impl PartialOrd for Item {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for Item {
    fn cmp(&self, o: &Self) -> Ordering {
        o.0.partial_cmp(&self.0).unwrap_or(Ordering::Equal)
    }
}

impl Engine {
    /// Travel time, distance and next step from every road node to `dest`, along the route a
    /// driver would choose (route cost), timed in real minutes.
    fn routes_to(&self, dest: u32) -> (Vec<f32>, Vec<f32>, Vec<u16>) {
        self.routes_to_with(dest, self.city.edges.cost())
    }

    /// Like `routes_to`, with the given route cost for each edge (used to spread traffic over routes).
    fn routes_to_with(&self, dest: u32, edge_cost: &[f32]) -> (Vec<f32>, Vec<f32>, Vec<u16>) {
        let n = self.n_nodes;
        let mins = &self.city.edges.minutes;
        let mut rev: Vec<Vec<(u32, f32, f32, f32)>> = vec![Vec::new(); n];
        for e in 0..self.city.edges.from.len() {
            rev[self.city.edges.to[e] as usize].push((self.city.edges.from[e], edge_cost[e], mins[e], self.city.edges.km[e]));
        }
        let mut best = vec![f32::INFINITY; n];
        let mut minutes = vec![f32::INFINITY; n];
        let mut km = vec![0f32; n];
        let mut next = vec![u16::MAX; n];
        best[dest as usize] = 0.0;
        minutes[dest as usize] = 0.0;
        next[dest as usize] = dest as u16;
        let mut heap = BinaryHeap::new();
        heap.push(Item(0.0, dest));
        while let Some(Item(c, u)) = heap.pop() {
            if c > best[u as usize] {
                continue;
            }
            for &(v, dc, dt, dk) in &rev[u as usize] {
                if c + dc < best[v as usize] {
                    best[v as usize] = c + dc;
                    minutes[v as usize] = minutes[u as usize] + dt;
                    km[v as usize] = km[u as usize] + dk;
                    next[v as usize] = u as u16;
                    heap.push(Item(c + dc, v));
                }
            }
        }
        (minutes, km, next)
    }

    /// Vehicles per day on every road node from a list of trips (origin node, destination node,
    /// vehicles per day), each sent along its fastest route. Used for traffic the spending model
    /// does not generate itself: commuting and traffic passing through the city.
    ///
    /// With `draws` above 1, drivers do not all take the single fastest route: each draw multiplies
    /// every road's travel time by a random factor (lognormal, spread `spread`), as drivers judge
    /// times differently and know different shortcuts, and the trips are shared equally over the draws.
    pub fn route_volumes(&self, trips: &[(u32, u32, f64)], draws: u32, spread: f64, seed: u64) -> Vec<f64> {
        let mut vol = vec![0f64; self.n_nodes];
        let mut by_dest: std::collections::BTreeMap<u32, Vec<(u32, f64)>> = Default::default();
        for &(o, d, v) in trips {
            by_dest.entry(d).or_default().push((o, v / draws.max(1) as f64));
        }
        let mut rng = crate::rng::Rng::new(seed);
        let base = self.city.edges.cost().to_vec();
        let tables: Vec<Vec<f32>> = (0..draws.max(1))
            .map(|k| if k == 0 && draws <= 1 { base.clone() } else {
                base.iter().map(|&m| m * (spread * rng.normal() - spread * spread / 2.0).exp() as f32).collect()
            })
            .collect();
        for (dest, list) in &by_dest {
            for t in &tables {
                self.add_routes(*dest, list, t, &mut vol);
            }
        }
        vol
    }

    fn add_routes(&self, dest: u32, list: &[(u32, f64)], edge_minutes: &[f32], vol: &mut [f64]) {
        let dest = dest;
        {
            let (minutes, _, next) = self.routes_to_with(dest, edge_minutes);
            for &(o, v) in list {
                if !minutes[o as usize].is_finite() {
                    continue;
                }
                let mut at = o;
                vol[at as usize] += v;
                let mut steps = 0;
                while at != dest && steps < self.n_nodes {
                    at = next[at as usize] as u32;
                    vol[at as usize] += v;
                    steps += 1;
                }
            }
        }
    }

    /// What a new business of kind `sim_category` and scale `size` (typical yearly revenue of a
    /// business its size) would earn at road node `node`, and who it would take the trade from.
    pub fn forecast(&self, node: u32, sim_category: u32, size: f64, format_weight: f64) -> Forecast {
        let (minutes, _, _) = self.routes_to(node);
        let n_biz = self.city.businesses.node.len();
        let (nlat, nlon) = (self.city.nodes.lat[node as usize], self.city.nodes.lon[node as usize]);
        let safe_res = self.city.safety_factor_at(nlat, nlon, sim_category, false);
        let safe_vis = self.city.safety_factor_at(nlat, nlon, sim_category, true);
        let cluster = self.city.cluster_factor_at(nlat, nlon, sim_category, None);
        let scale = format_weight * self.city.site_factor_at(node, sim_category) * cluster * safe_res * size.max(30_000.0).powf(self.city.params.attractiveness_exponent);
        let floor = self.city.params.min_minutes;
        let mut out = Forecast { resident: 0.0, tourist: 0.0, tourist_by_origin: [0.0; 3], from_out_of_town: 0.0, monthly: [0.0; 12], losses: vec![], total_taken: 0.0 };
        let mut loss = vec![0f64; n_biz];
        let mut w = Vec::new();

        // residents
        let mut budget_at = vec![0f64; self.home_nodes.len() * self.n_cat];
        for h in 0..self.hh_rate.len() {
            let slot = self.node_slot[self.city.households.node[h] as usize] as usize;
            for c in 0..self.n_cat {
                budget_at[slot * self.n_cat + c] += self.city.households.budget[h * self.n_cat + c] as f64;
            }
        }
        for c in 0..self.n_cat {
            let served = self.city.categories.served_by[c][sim_category as usize];
            if served <= 0.0 {
                continue;
            }
            let local = if self.city.categories.retail[c] == 1 { 1.0 - self.city.params.online_share_of_retail } else { 1.0 };
            for (slot, &home) in self.home_nodes.iter().enumerate() {
                let spend = budget_at[slot * self.n_cat + c] * local;
                if spend <= 0.0 {
                    continue;
                }
                let total = self.weights(home as usize, c, self.beta, &mut w);
                let mine = served * scale / (minutes[home as usize] as f64).max(floor).powf(self.beta);
                let share = mine / (total + mine);
                out.resident += spend * share;
                for m in 0..12 {
                    out.monthly[m] += spend * share * self.city.categories.season[c][m] * DAYS_IN_MONTH[m] as f64 / 365.0;
                }
                for (i, &b) in self.cat_business[c].iter().enumerate() {
                    loss[b as usize] += spend * share * w[i] / total;
                }
            }
        }

        // tourists
        if let (Some(rt), Some(t)) = (self.tour.as_ref(), self.city.tourism.as_ref()) {
            for c in 0..rt.n_cat {
                let served = t.categories.served_by[c][sim_category as usize];
                if served <= 0.0 {
                    continue;
                }
                // yearly spending in this category by group, and its split over the months
                let mut amount = [[0f64; 12]; 5];
                let mut origin_amount = [[0f64; 3]; 5];
                for (si, seg) in t.segments.iter().enumerate() {
                    let origin = match seg.origin.as_str() { "us" => 1, "overseas" => 2, _ => 0 };
                    let days = if seg.overnight == 1 { rt.avg_nights } else { 1.0 };
                    let shares = if seg.overnight == 1 {
                        [(GROUP_HOTEL, t.lodging_share.hotel), (GROUP_RENTAL, t.lodging_share.rental),
                         (GROUP_CAMPING, t.lodging_share.camping), (GROUP_PRIVATE, t.lodging_share.private)].to_vec()
                    } else {
                        vec![(GROUP_DAY, 1.0)]
                    };
                    for (g, sh) in shares {
                        for m in 0..12 {
                            let a = seg.persons_per_year * seg.month_share[m] * days * sh * rt.seg_budget[si][c];
                            amount[g][m] += a;
                            origin_amount[g][origin] += a;
                        }
                    }
                }
                for g in 0..5 {
                    let year: f64 = amount[g].iter().sum();
                    if year <= 0.0 {
                        continue;
                    }
                    let mut prev = 0.0;
                    for (i, &base) in rt.groups[g].nodes.iter().enumerate() {
                        let base_share = rt.groups[g].cdf[i] - prev;
                        prev = rt.groups[g].cdf[i];
                        if base_share <= 0.0 {
                            continue;
                        }
                        let total = self.tour_weights(rt, base as usize, c, &mut w);
                        let mine = served * scale * safe_vis / safe_res.max(1e-9) / (minutes[base as usize] as f64).max(self.city.params.tourist_floor()).powf(self.beta);
                        let share = base_share * mine / (total + mine);
                        out.tourist += year * share;
                        for o in 0..3 {
                            out.tourist_by_origin[o] += origin_amount[g][o] * share;
                        }
                        for m in 0..12 {
                            out.monthly[m] += amount[g][m] * share;
                        }
                        for (j, &b) in rt.cat_business[c].iter().enumerate() {
                            loss[b as usize] += year * share * w[j] / total.max(1e-12);
                        }
                    }
                }
            }
        }

        out.total_taken = loss.iter().sum();
        let mut ranked: Vec<(u32, f64)> = Vec::new();
        for b in 0..n_biz {
            if self.city.businesses.external[b] == 1 {
                out.from_out_of_town += loss[b];
            } else if loss[b] > 0.0 {
                ranked.push((b as u32, loss[b]));
            }
        }
        ranked.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(Ordering::Equal));
        ranked.truncate(8);
        out.losses = ranked;
        out
    }

    /// Rebuild the lists of who serves each resident category after the business list changed.
    pub(crate) fn rebuild_categories(&mut self) {
        let n_biz = self.city.businesses.node.len();
        let exp = self.city.params.attractiveness_exponent;
        for c in 0..self.n_cat {
            let (mut list, mut pull) = (Vec::new(), Vec::new());
            let mut original_pull = 0.0;
            for b in 0..n_biz {
                let w = self.city.categories.served_by[c][self.city.businesses.sim_category[b] as usize];
                if w > 0.0 && self.city.businesses.external[b] == 0 {
                    let p = w * self.city.pull_weight(b) * self.city.businesses.revenue_prior[b].max(30_000.0).powf(exp);
                    list.push(b as u32);
                    pull.push(p);
                    if b < self.first_added {
                        original_pull += p;
                    }
                }
            }
            // neighbouring towns keep the pull they had before anything was added
            for b in 0..n_biz {
                let w = self.city.categories.served_by[c][self.city.businesses.sim_category[b] as usize];
                if w > 0.0 && self.city.businesses.external[b] == 1 && original_pull > 0.0 {
                    list.push(b as u32);
                    let cal = self.city.categories.external_scale.get(c).copied().unwrap_or(1.0);
                    pull.push(w * original_pull * self.city.businesses.pull_scale[b] * cal);
                }
            }
            self.cat_business[c] = list;
            self.cat_pull[c] = pull;
        }
    }

    /// Add a business to the running city. Returns its index. It starts trading on the next step.
    #[allow(clippy::too_many_arguments)]
    pub fn add_business(&mut self, node: u32, lat: f64, lon: f64, sim_category: u32, size: f64, format_weight: f64,
                        open: u32, close: u32, in_core: bool, name: &str) -> u32 {
        let existing = (0..self.city.businesses.node.len()).find(|&b| self.city.businesses.node[b] == node);
        let dest = match existing {
            Some(b) => self.dest_of_business[b],
            None => {
                let (minutes, km, next) = self.routes_to(node);
                self.minutes.extend(minutes);
                self.km.extend(km);
                self.next_hop.extend(next);
                self.n_dest += 1;
                (self.n_dest - 1) as u32
            }
        };
        let site = self.city.site_factor_at(node, sim_category) * self.city.cluster_factor_at(lat, lon, sim_category, None);
        let b = &mut self.city.businesses;
        b.site.push(site);
        b.node.push(node);
        b.lat.push(lat);
        b.lon.push(lon);
        b.sim_category.push(sim_category);
        b.open.push(open);
        b.close.push(close);
        b.core.push(in_core as u8);
        b.staff.push(0);
        b.revenue_prior.push(size);
        b.name.push(name.to_string());
        b.format_weight.push(format_weight);
        b.external.push(0);
        b.extra_km.push(0.0);
        b.extra_min.push(0.0);
        b.pull_scale.push(0.0);
        if !b.chain.is_empty() {
            b.chain.push(-1);
        }
        self.dest_of_business.push(dest);
        if let Some(t) = self.city.tourism.as_mut() {
            t.capacity.push(0);
            t.lodging_type.push(0);
            t.attendance_target.push(0.0);
        }
        self.state.revenue.push(0.0);
        self.state.visits.push(0);
        if self.tour.is_some() {
            self.state.tour.revenue.push(0.0);
            self.state.tour.persons.push(0.0);
            self.state.tour.free.push(0);
        }
        self.rebuild_categories();
        let beta = self.beta;
        self.set_beta(beta);
        (self.city.businesses.node.len() - 1) as u32
    }

    /// Apply an outlook: multipliers on the calibrated year for US, domestic and overseas visitors,
    /// hotel stays outside leisure travel, and resident spending. All 1.0 restores the calibrated year.
    /// Scale every safety penalty (1 = as documented now) and rebuild the tables that use it.
    pub fn set_safety_scale(&mut self, scale: f64) {
        if let Some(s) = self.city.safety.as_mut() {
            s.scale = scale;
            self.rebuild_categories();
            let beta = self.beta;
            self.set_beta(beta);
        }
    }

    pub fn set_scenario(&mut self, us: f64, domestic: f64, overseas: f64, base_stays: f64, resident: f64) {
        if self.scenario_base.is_none() {
            let persons = self.city.tourism.as_ref().map(|t| t.segments.iter().map(|s| s.persons_per_year).collect()).unwrap_or_default();
            self.scenario_base = Some((persons, self.city.households.budget.clone()));
        }
        let (persons, budget) = self.scenario_base.clone().unwrap();
        if let Some(t) = self.city.tourism.as_mut() {
            for (i, s) in t.segments.iter_mut().enumerate() {
                s.persons_per_year = persons[i] * match s.origin.as_str() { "us" => us, "overseas" => overseas, _ => domestic };
            }
        }
        // placed businesses add no households, so the stored budgets still line up
        for (i, b) in self.city.households.budget.iter_mut().enumerate() {
            *b = budget[i] * resident as f32;
        }
        self.scenario = [us, domestic, overseas, base_stays, resident];
        let beta = self.beta;
        self.set_beta(beta);
    }

    /// Set the calibration multipliers and rebuild every table that depends on them.
    pub fn set_multipliers(&mut self, external_scale: Vec<f64>, capture: Vec<f64>) {
        self.city.categories.external_scale = external_scale;
        if let Some(t) = self.city.tourism.as_mut() {
            t.categories.capture = capture;
        }
        self.rebuild_categories();
        self.recalibrate_beta();
    }

    /// Multiply the pull of every place run by one operator (1 = Niagara Parks, 2 = casinos).
    pub fn scale_operator_pull(&mut self, operator: u8, factor: f64) {
        for b in 0..self.city.businesses.operator.len() {
            if self.city.businesses.operator[b] == operator {
                self.city.businesses.format_weight[b] *= factor;
            }
        }
        self.rebuild_categories();
        self.recalibrate_beta();
    }

    /// Multiply the pull of one operator's places of one kind (`attraction` true) or of all its other kinds.
    pub fn scale_operator_pull_kind(&mut self, operator: u8, attraction: bool, factor: f64) {
        let ac = self.city.sim_categories.iter().position(|c| c == "attraction").map(|x| x as u32);
        for b in 0..self.city.businesses.operator.len() {
            if self.city.businesses.operator[b] == operator && (Some(self.city.businesses.sim_category[b]) == ac) == attraction {
                self.city.businesses.format_weight[b] *= factor;
            }
        }
        self.rebuild_categories();
        self.recalibrate_beta();
    }

    /// Expected yearly revenue of one operator's attractions (`attraction` true) or of its other places.
    pub fn operator_revenue_kind(&self, operator: u8, attraction: bool) -> f64 {
        let r = self.expected_yearly_revenue();
        let ac = self.city.sim_categories.iter().position(|c| c == "attraction").map(|x| x as u32);
        (0..self.city.businesses.operator.len())
            .filter(|&b| self.city.businesses.operator[b] == operator && (Some(self.city.businesses.sim_category[b]) == ac) == attraction)
            .map(|b| r[b]).sum()
    }

    /// Paid attraction visits per visiting party per day (tourist category "attractions").
    pub fn attraction_visits_per_party_day(&self) -> f64 {
        let t = self.city.tourism.as_ref().unwrap();
        t.categories.events_per_party_day[t.categories.name.iter().position(|n| n == "attractions").unwrap()]
    }

    pub fn set_attraction_visits_per_party_day(&mut self, v: f64) {
        let t = self.city.tourism.as_mut().unwrap();
        let i = t.categories.name.iter().position(|n| n == "attractions").unwrap();
        t.categories.events_per_party_day[i] = v;
        let beta = self.beta;
        self.set_beta(beta);
    }

    /// Expected yearly revenue of all places run by one operator.
    pub fn operator_revenue(&self, operator: u8) -> f64 {
        let r = self.expected_yearly_revenue();
        (0..self.city.businesses.operator.len()).filter(|&b| self.city.businesses.operator[b] == operator).map(|b| r[b]).sum()
    }

    /// Tune beta again so the median driving trip matches the travel survey.
    pub fn recalibrate_beta(&mut self) {
        let target = self.city.params.median_driver_trip_km_target;
        let (mut lo, mut hi) = (0.0f64, 6.0f64);
        for _ in 0..30 {
            let mid = (lo + hi) / 2.0;
            if self.expected_trip_stats(mid).0 > target { lo = mid } else { hi = mid }
        }
        self.set_beta((lo + hi) / 2.0);
    }

    /// Yearly revenue each business can expect from residents alone.
    pub fn expected_yearly_resident_revenue(&self) -> Vec<f64> {
        let n_biz = self.city.businesses.node.len();
        let mut out = vec![0f64; n_biz];
        for c in 0..self.n_cat {
            for b in 0..n_biz {
                out[b] += self.flow[c][b];
            }
        }
        out
    }

    /// Yearly revenue each business can expect at the current settings (flow mode, no randomness).
    pub fn expected_yearly_revenue(&self) -> Vec<f64> {
        let n_biz = self.city.businesses.node.len();
        let mut out = vec![0f64; n_biz];
        for c in 0..self.n_cat {
            for b in 0..n_biz {
                out[b] += self.flow[c][b];
            }
        }
        if let (Some(rt), Some(t)) = (self.tour.as_ref(), self.city.tourism.as_ref()) {
            for (si, seg) in t.segments.iter().enumerate() {
                let days = if seg.overnight == 1 { rt.avg_nights } else { 1.0 };
                let shares = if seg.overnight == 1 {
                    [t.lodging_share.hotel, t.lodging_share.rental, t.lodging_share.camping, t.lodging_share.private, 0.0]
                } else {
                    [0.0, 0.0, 0.0, 0.0, 1.0]
                };
                for g in 0..5 {
                    let pd = seg.persons_per_year * days * shares[g];
                    if pd <= 0.0 {
                        continue;
                    }
                    for c in 0..rt.n_cat {
                        for &b in &rt.cat_business[c] {
                            out[b as usize] += pd * rt.seg_budget[si][c] * rt.dist[g][c][b as usize];
                        }
                    }
                }
            }
        }
        out
    }
}
