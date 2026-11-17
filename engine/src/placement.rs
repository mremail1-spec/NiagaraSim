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
    /// Fastest travel time, distance and next step from every road node to `dest`.
    fn routes_to(&self, dest: u32) -> (Vec<f32>, Vec<f32>, Vec<u16>) {
        let n = self.n_nodes;
        let mut rev: Vec<Vec<(u32, f32, f32)>> = vec![Vec::new(); n];
        for e in 0..self.city.edges.from.len() {
            rev[self.city.edges.to[e] as usize].push((self.city.edges.from[e], self.city.edges.minutes[e], self.city.edges.km[e]));
        }
        let mut minutes = vec![f32::INFINITY; n];
        let mut km = vec![0f32; n];
        let mut next = vec![u16::MAX; n];
        minutes[dest as usize] = 0.0;
        next[dest as usize] = dest as u16;
        let mut heap = BinaryHeap::new();
        heap.push(Item(0.0, dest));
        while let Some(Item(t, u)) = heap.pop() {
            if t > minutes[u as usize] {
                continue;
            }
            for &(v, dt, dk) in &rev[u as usize] {
                if t + dt < minutes[v as usize] {
                    minutes[v as usize] = t + dt;
                    km[v as usize] = km[u as usize] + dk;
                    next[v as usize] = u as u16;
                    heap.push(Item(t + dt, v));
                }
            }
        }
        (minutes, km, next)
    }

    /// What a new business of kind `sim_category` and scale `size` (typical yearly revenue of a
    /// business its size) would earn at road node `node`, and who it would take the trade from.
    pub fn forecast(&self, node: u32, sim_category: u32, size: f64, format_weight: f64) -> Forecast {
        let (minutes, _, _) = self.routes_to(node);
        let n_biz = self.city.businesses.node.len();
        let scale = format_weight * size.max(30_000.0).powf(self.city.params.attractiveness_exponent);
        let floor = self.city.params.min_minutes;
        let mut out = Forecast { resident: 0.0, tourist: 0.0, from_out_of_town: 0.0, monthly: [0.0; 12], losses: vec![], total_taken: 0.0 };
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
                for (si, seg) in t.segments.iter().enumerate() {
                    let days = if seg.overnight == 1 { rt.avg_nights } else { 1.0 };
                    let shares = if seg.overnight == 1 {
                        [(GROUP_HOTEL, t.lodging_share.hotel), (GROUP_RENTAL, t.lodging_share.rental),
                         (GROUP_CAMPING, t.lodging_share.camping), (GROUP_PRIVATE, t.lodging_share.private)].to_vec()
                    } else {
                        vec![(GROUP_DAY, 1.0)]
                    };
                    for (g, sh) in shares {
                        for m in 0..12 {
                            amount[g][m] += seg.persons_per_year * seg.month_share[m] * days * sh * rt.seg_budget[si][c];
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
                        let mine = served * scale / (minutes[base as usize] as f64).max(floor).powf(self.beta);
                        let share = base_share * mine / (total + mine);
                        out.tourist += year * share;
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
    fn rebuild_categories(&mut self) {
        let n_biz = self.city.businesses.node.len();
        let exp = self.city.params.attractiveness_exponent;
        for c in 0..self.n_cat {
            let (mut list, mut pull) = (Vec::new(), Vec::new());
            let mut original_pull = 0.0;
            for b in 0..n_biz {
                let w = self.city.categories.served_by[c][self.city.businesses.sim_category[b] as usize];
                if w > 0.0 && self.city.businesses.external[b] == 0 {
                    let p = w * self.city.businesses.format_weight[b] * self.city.businesses.revenue_prior[b].max(30_000.0).powf(exp);
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
                    pull.push(w * original_pull * self.city.businesses.pull_scale[b]);
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
        let b = &mut self.city.businesses;
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
