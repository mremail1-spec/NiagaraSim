//! Tourists: parties arrive by season and day of week, stay in a hotel, rental, campground or
//! with friends (or park for the day), and spend on meals, attractions, shopping, fuel and parking.
//!
//! As with residents there are two ways to advance. Agent mode creates every party and every
//! purchase; flow mode books each business its expected share. Both use the same probabilities.
use crate::engine::{month_of_day, Engine, DAYS_IN_MONTH};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

pub const GROUP_HOTEL: usize = 0;
pub const GROUP_RENTAL: usize = 1;
pub const GROUP_CAMPING: usize = 2;
pub const GROUP_PRIVATE: usize = 3;
pub const GROUP_DAY: usize = 4;
const NO_BUSINESS: u32 = u32::MAX;

/// A place visitors are based: a lodging, a host's street, or a parking lot for day visitors.
pub struct Group {
    pub nodes: Vec<u32>,
    pub business: Vec<u32>,
    /// Cumulative share of the group's visitors based at each entry.
    pub cdf: Vec<f64>,
}

/// Fixed tourist tables, rebuilt when the distance parameter changes.
pub struct TourRt {
    pub n_cat: usize,
    pub cat_business: Vec<Vec<u32>>,
    pub cat_pull: Vec<Vec<f64>>,
    pub groups: Vec<Group>,
    /// Share of a group's spending in a category that lands at each business, [group][category][business].
    pub dist: Vec<Vec<Vec<f64>>>,
    /// Spending per person per day by segment and category (accommodation and fares excluded).
    pub seg_budget: Vec<Vec<f64>>,
    pub seg_fares: Vec<f64>,
    pub avg_nights: f64,
    pub nights_cdf: Vec<f64>,
    /// Multiplier that turns published attendance into pull, tuned so Journey Behind the Falls matches.
    pub attendance_pull: f64,
    cdf_cache: HashMap<u64, Vec<f32>>,
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Party {
    pub segment: u8,
    pub group: u8,
    pub size: u8,
    pub days_left: u8,
    pub base_node: u32,
    pub lodging: u32,
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq)]
pub struct TouristTrip {
    pub base_node: u32,
    pub business: u32,
    pub category: u8,
    pub persons: u8,
    pub spend: f32,
}

/// Everything about tourists that changes over time.
#[derive(Clone, Debug, Default, Serialize, Deserialize, PartialEq)]
pub struct TourState {
    pub parties: Vec<Party>,
    plan: Vec<Vec<TouristTrip>>,
    /// Tourist revenue and tourist person-visits so far, per business.
    pub revenue: Vec<f64>,
    pub persons: Vec<f64>,
    pub month_category_revenue: Vec<Vec<f64>>,
    pub accommodation_by_month: Vec<f64>,
    pub room_nights_by_month: Vec<f64>,
    /// Visitors arriving, [segment][month].
    pub arrivals: Vec<Vec<f64>>,
    pub fares: f64,
    /// Rooms, units or sites still free tonight, per business.
    pub free: Vec<u32>,
    pub turned_away: u64,
    pub visitors_today: f64,
}

impl Engine {
    fn tour_weights(&self, rt: &TourRt, node: usize, c: usize, out: &mut Vec<f64>) -> f64 {
        out.clear();
        let mut total = 0.0;
        for (i, &b) in rt.cat_business[c].iter().enumerate() {
            let t = self.trip_minutes(node, b as usize);
            let w = rt.cat_pull[c][i] / t.max(self.city.params.min_minutes).powf(self.beta);
            out.push(w);
            total += w;
        }
        total
    }

    /// Build the tourist tables. Called whenever the distance parameter is set.
    pub(crate) fn build_tourism(&mut self) {
        let t = match self.city.tourism.clone() {
            Some(t) => t,
            None => return,
        };
        let n_biz = self.city.businesses.node.len();
        let n_cat = t.categories.name.len();
        let exp = self.city.params.attractiveness_exponent;

        // where each kind of visitor is based
        let mut groups: Vec<Group> = (0..5).map(|_| Group { nodes: vec![], business: vec![], cdf: vec![] }).collect();
        let mut weights: Vec<Vec<f64>> = vec![vec![]; 5];
        let parking = self.city.sim_categories.iter().position(|c| c == "parking");
        for b in 0..n_biz {
            let kind = t.lodging_type[b] as usize;
            if kind >= 1 && kind <= 3 {
                groups[kind - 1].nodes.push(self.city.businesses.node[b]);
                groups[kind - 1].business.push(b as u32);
                weights[kind - 1].push(t.capacity[b] as f64);
            }
            if Some(self.city.businesses.sim_category[b] as usize) == parking {
                groups[GROUP_DAY].nodes.push(self.city.businesses.node[b]);
                groups[GROUP_DAY].business.push(NO_BUSINESS);
                weights[GROUP_DAY].push(1.0);
            }
        }
        let mut homes = vec![0f64; self.home_nodes.len()];
        for &node in &self.city.households.node {
            homes[self.node_slot[node as usize] as usize] += 1.0;
        }
        for (slot, &node) in self.home_nodes.iter().enumerate() {
            groups[GROUP_PRIVATE].nodes.push(node);
            groups[GROUP_PRIVATE].business.push(NO_BUSINESS);
            weights[GROUP_PRIVATE].push(homes[slot]);
        }
        for g in 0..5 {
            let total: f64 = weights[g].iter().sum();
            let mut acc = 0.0;
            groups[g].cdf = weights[g].iter().map(|w| { acc += w / total.max(1e-12); acc }).collect();
        }

        // which businesses serve each tourist category
        let attraction_cat = t.categories.name.iter().position(|c| c == "attractions");
        let mut cat_business = vec![Vec::new(); n_cat];
        let mut cat_pull = vec![Vec::new(); n_cat];
        for c in 0..n_cat {
            for b in 0..n_biz {
                let w = t.categories.served_by[c][self.city.businesses.sim_category[b] as usize];
                if w > 0.0 && self.city.businesses.external[b] == 0 {
                    let size = self.city.businesses.revenue_prior[b].max(30_000.0);
                    cat_business[c].push(b as u32);
                    cat_pull[c].push(w * self.city.businesses.format_weight[b] * size.powf(exp));
                }
            }
        }

        // spending per person per day, by segment
        let nights_total: f64 = t.nights.weight.iter().sum();
        let avg_nights: f64 = t.nights.n.iter().zip(&t.nights.weight).map(|(n, w)| *n as f64 * w).sum::<f64>() / nights_total;
        let mut acc = 0.0;
        let nights_cdf: Vec<f64> = t.nights.weight.iter().map(|w| { acc += w / nights_total; acc }).collect();
        let paid_share = 1.0 - t.lodging_share.private;
        let acc_avg = t.accommodation_spend_per_paid_person_night * paid_share;
        let mut seg_budget = Vec::new();
        let mut seg_fares = Vec::new();
        for s in &t.segments {
            let other = (s.spend_per_person_day - if s.overnight == 1 { acc_avg } else { 0.0 }).max(0.0);
            seg_budget.push(t.categories.share.iter().map(|sh| other * sh).collect::<Vec<f64>>());
            seg_fares.push(other * t.transport_fare_share);
        }

        let mut rt = TourRt { n_cat, cat_business, cat_pull, groups, dist: vec![], seg_budget, seg_fares, avg_nights,
                              nights_cdf, attendance_pull: 1.0, cdf_cache: HashMap::new() };

        // tune the pull of attractions with published attendance so Journey Behind the Falls matches
        if let Some(ac) = attraction_cat {
            let base_pull = rt.cat_pull[ac].clone();
            let has_target: Vec<f64> = rt.cat_business[ac].iter().map(|&b| t.attendance_target[b as usize]).collect();
            let lead = has_target.iter().cloned().fold(0.0, f64::max);
            if lead > 0.0 {
                let lead_i = has_target.iter().position(|&x| x == lead).unwrap();
                // person-days per year in each group
                let mut pd = [0f64; 5];
                for s in &t.segments {
                    if s.overnight == 1 {
                        let d = s.persons_per_year * avg_nights;
                        pd[GROUP_HOTEL] += d * t.lodging_share.hotel;
                        pd[GROUP_RENTAL] += d * t.lodging_share.rental;
                        pd[GROUP_CAMPING] += d * t.lodging_share.camping;
                        pd[GROUP_PRIVATE] += d * t.lodging_share.private;
                    } else {
                        pd[GROUP_DAY] += s.persons_per_year;
                    }
                }
                let events = t.categories.events_per_party_day[ac];
                // distance factor for every base and attraction, computed once
                let mut decay: Vec<(usize, f64, Vec<f64>)> = Vec::new();
                for g in 0..5 {
                    let mut prev = 0.0;
                    for (i, &node) in rt.groups[g].nodes.iter().enumerate() {
                        let share = rt.groups[g].cdf[i] - prev;
                        prev = rt.groups[g].cdf[i];
                        let f: Vec<f64> = rt.cat_business[ac].iter()
                            .map(|&b| 1.0 / self.trip_minutes(node as usize, b as usize).max(self.city.params.min_minutes).powf(self.beta)).collect();
                        decay.push((g, share, f));
                    }
                }
                // Fit each published attraction's pull so its expected attendance matches: start from
                // the published figure and correct by target / expected until they agree.
                let mut pull = base_pull.clone();
                for j in 0..pull.len() {
                    if has_target[j] > 0.0 {
                        pull[j] = has_target[j] / lead * base_pull.iter().cloned().fold(0.0, f64::max);
                    }
                }
                for _ in 0..40 {
                    let mut expected = vec![0f64; pull.len()];
                    for (g, share, f) in &decay {
                        let sum: f64 = (0..f.len()).map(|j| pull[j] * f[j]).sum();
                        let k = pd[*g] * events * share / sum;
                        for j in 0..f.len() {
                            if has_target[j] > 0.0 {
                                expected[j] += k * pull[j] * f[j];
                            }
                        }
                    }
                    for j in 0..pull.len() {
                        if has_target[j] > 0.0 && expected[j] > 0.0 {
                            pull[j] *= has_target[j] / expected[j];
                        }
                    }
                }
                rt.attendance_pull = pull[lead_i] / lead;
                rt.cat_pull[ac] = pull;
            }
        }

        // expected destination shares for flow mode
        let mut dist = vec![vec![vec![0f64; n_biz]; n_cat]; 5];
        let mut w = Vec::new();
        for g in 0..5 {
            let mut prev = 0.0;
            for i in 0..rt.groups[g].nodes.len() {
                let share = rt.groups[g].cdf[i] - prev;
                prev = rt.groups[g].cdf[i];
                let node = rt.groups[g].nodes[i] as usize;
                for c in 0..n_cat {
                    let total = self.tour_weights(&rt, node, c, &mut w);
                    if total <= 0.0 {
                        continue;
                    }
                    for (j, &b) in rt.cat_business[c].iter().enumerate() {
                        dist[g][c][b as usize] += share * w[j] / total;
                    }
                }
            }
        }
        rt.dist = dist;

        if self.state.tour.revenue.len() != n_biz {
            let n_seg = t.segments.len();
            self.state.tour = TourState {
                parties: vec![], plan: vec![Vec::new(); 24], revenue: vec![0.0; n_biz], persons: vec![0.0; n_biz],
                month_category_revenue: vec![vec![0.0; n_cat]; 12], accommodation_by_month: vec![0.0; 12],
                room_nights_by_month: vec![0.0; 12], arrivals: vec![vec![0.0; 12]; n_seg], fares: 0.0,
                free: t.capacity.clone(), turned_away: 0, visitors_today: 0.0,
            };
        }
        self.tour = Some(rt);
    }

    /// Visitors expected to arrive today in a segment (persons).
    fn tour_arrivals_today(&self, seg: usize) -> f64 {
        let t = self.city.tourism.as_ref().unwrap();
        let m = month_of_day(self.state.day);
        let dow = t.day_of_week[((t.first_weekday + self.state.day) % 7) as usize];
        t.segments[seg].persons_per_year * t.segments[seg].month_share[m] / DAYS_IN_MONTH[m] as f64 * dow
    }

    fn group_shares(&self, seg: usize) -> [f64; 5] {
        let t = self.city.tourism.as_ref().unwrap();
        if t.segments[seg].overnight == 1 {
            [t.lodging_share.hotel, t.lodging_share.rental, t.lodging_share.camping, t.lodging_share.private, 0.0]
        } else {
            [0.0, 0.0, 0.0, 0.0, 1.0]
        }
    }

    /// Agent mode, start of day: new parties arrive, everyone in town plans the day's purchases.
    pub(crate) fn tour_plan_day(&mut self) {
        let mut rt = match self.tour.take() {
            Some(rt) => rt,
            None => return,
        };
        let t = self.city.tourism.clone().unwrap();
        let month = month_of_day(self.state.day);
        for bucket in self.state.tour.plan.iter_mut() {
            bucket.clear();
        }
        // arrivals
        for seg in 0..t.segments.len() {
            let persons = self.tour_arrivals_today(seg);
            let parties = self.state.rng.poisson(persons / t.party_size_mean);
            let shares = self.group_shares(seg);
            for _ in 0..parties {
                let size = (1 + self.state.rng.poisson(t.party_size_mean - 1.0)).min(12) as u8;
                let mut g = GROUP_DAY;
                let mut days = 1u8;
                if t.segments[seg].overnight == 1 {
                    let u = self.state.rng.f64();
                    let mut acc = 0.0;
                    g = GROUP_PRIVATE;
                    for k in 0..4 {
                        acc += shares[k];
                        if u < acc {
                            g = k;
                            break;
                        }
                    }
                    let un = self.state.rng.f64();
                    let k = rt.nights_cdf.iter().position(|&x| un < x).unwrap_or(rt.nights_cdf.len() - 1);
                    days = t.nights.n[k].min(250) as u8;
                }
                // pick a base; paid lodging must have a free room, else the party stays with friends
                let mut base = None;
                for _ in 0..6 {
                    if rt.groups[g].cdf.is_empty() {
                        break;
                    }
                    let u = self.state.rng.f64();
                    let i = rt.groups[g].cdf.partition_point(|&x| x <= u).min(rt.groups[g].cdf.len() - 1);
                    let b = rt.groups[g].business[i];
                    if b == NO_BUSINESS || self.state.tour.free[b as usize] > 0 {
                        base = Some(i);
                        break;
                    }
                }
                if base.is_none() {
                    if g != GROUP_PRIVATE && g != GROUP_DAY {
                        self.state.tour.turned_away += 1;
                    }
                    g = if g == GROUP_DAY { GROUP_DAY } else { GROUP_PRIVATE };
                    let u = self.state.rng.f64();
                    base = Some(rt.groups[g].cdf.partition_point(|&x| x <= u).min(rt.groups[g].cdf.len() - 1));
                }
                let i = base.unwrap();
                let lodging = rt.groups[g].business[i];
                if lodging != NO_BUSINESS {
                    self.state.tour.free[lodging as usize] -= 1;
                }
                self.state.tour.arrivals[seg][month] += size as f64;
                self.state.tour.parties.push(Party { segment: seg as u8, group: g as u8, size, days_left: days,
                                                     base_node: rt.groups[g].nodes[i], lodging });
            }
        }
        // everyone in town: pay for the room, plan purchases
        let mut present = 0.0;
        let mut w = Vec::new();
        let sigma = self.city.params.spend_sigma;
        let parties = std::mem::take(&mut self.state.tour.parties);
        for p in &parties {
            present += p.size as f64;
            let seg = p.segment as usize;
            if p.lodging != NO_BUSINESS {
                let pay = t.accommodation_spend_per_paid_person_night * p.size as f64;
                self.state.revenue[p.lodging as usize] += pay;
                self.state.tour.revenue[p.lodging as usize] += pay;
                self.state.tour.accommodation_by_month[month] += pay;
                if p.group as usize == GROUP_HOTEL {
                    self.state.tour.room_nights_by_month[month] += 1.0;
                }
            }
            self.state.tour.fares += rt.seg_fares[seg] * p.size as f64;
            for c in 0..rt.n_cat {
                let per_day = t.categories.events_per_party_day[c];
                let n = self.state.rng.poisson(per_day);
                if n == 0 || rt.cat_business[c].is_empty() {
                    continue;
                }
                let key = p.base_node as u64 * 64 + c as u64;
                if !rt.cdf_cache.contains_key(&key) {
                    let total = self.tour_weights(&rt, p.base_node as usize, c, &mut w);
                    let mut acc = 0.0;
                    let row: Vec<f32> = w.iter().map(|x| { acc += x / total; acc as f32 }).collect();
                    rt.cdf_cache.insert(key, row);
                }
                for _ in 0..n {
                    let row = &rt.cdf_cache[&key];
                    let u = self.state.rng.f64() as f32;
                    let b = rt.cat_business[c][row.partition_point(|&x| x <= u).min(row.len() - 1)];
                    let spend = rt.seg_budget[seg][c] * p.size as f64 / per_day * self.state.rng.lognormal_mean1(sigma);
                    // an hour when the business is open, weighted like other trips
                    let mut open_total = 0.0;
                    for hr in 0..24 {
                        if self.is_open(b as usize, hr) {
                            open_total += self.city.params.hour_weights[hr as usize];
                        }
                    }
                    let mut x = self.state.rng.f64() * open_total;
                    let mut hour = 12usize;
                    if open_total > 0.0 {
                        for hr in 0..24usize {
                            if self.is_open(b as usize, hr as u32) {
                                if x < self.city.params.hour_weights[hr] {
                                    hour = hr;
                                    break;
                                }
                                x -= self.city.params.hour_weights[hr];
                            }
                        }
                    }
                    self.state.tour.plan[hour].push(TouristTrip { base_node: p.base_node, business: b, category: c as u8,
                                                                  persons: p.size, spend: spend as f32 });
                }
            }
        }
        self.state.tour.visitors_today = present;
        // departures free their rooms for tomorrow's arrivals
        let mut staying = Vec::with_capacity(parties.len());
        for mut p in parties {
            p.days_left -= 1;
            if p.days_left == 0 {
                if p.lodging != NO_BUSINESS {
                    self.state.tour.free[p.lodging as usize] += 1;
                }
            } else {
                staying.push(p);
            }
        }
        self.state.tour.parties = staying;
        self.tour = Some(rt);
    }

    /// Agent mode: book the tourist purchases planned for the current hour.
    pub(crate) fn tour_step_hour(&mut self) {
        if self.tour.is_none() {
            return;
        }
        let hour = self.state.hour as usize;
        let month = month_of_day(self.state.day);
        let trips = std::mem::take(&mut self.state.tour.plan[hour]);
        for t in &trips {
            let b = t.business as usize;
            self.state.revenue[b] += t.spend as f64;
            self.state.tour.revenue[b] += t.spend as f64;
            self.state.tour.persons[b] += t.persons as f64;
            self.state.tour.month_category_revenue[month][t.category as usize] += t.spend as f64;
        }
        self.last_tourist_trips = trips;
    }

    /// Flow mode: book a day's expected tourist revenue.
    pub(crate) fn tour_flow_day(&mut self) {
        let rt = match self.tour.take() {
            Some(rt) => rt,
            None => return,
        };
        let t = self.city.tourism.clone().unwrap();
        let month = month_of_day(self.state.day);
        let mut present = 0.0;
        for seg in 0..t.segments.len() {
            let arriving = self.tour_arrivals_today(seg);
            self.state.tour.arrivals[seg][month] += arriving;
            let overnight = t.segments[seg].overnight == 1;
            let person_days = arriving * if overnight { rt.avg_nights } else { 1.0 };
            present += person_days;
            self.state.tour.fares += rt.seg_fares[seg] * person_days;
            let shares = self.group_shares(seg);
            for g in 0..5 {
                let pd = person_days * shares[g];
                if pd <= 0.0 {
                    continue;
                }
                if g <= GROUP_CAMPING {
                    let pay = pd * t.accommodation_spend_per_paid_person_night;
                    let mut prev = 0.0;
                    for (i, &b) in rt.groups[g].business.iter().enumerate() {
                        let share = rt.groups[g].cdf[i] - prev;
                        prev = rt.groups[g].cdf[i];
                        self.state.revenue[b as usize] += pay * share;
                        self.state.tour.revenue[b as usize] += pay * share;
                    }
                    self.state.tour.accommodation_by_month[month] += pay;
                    if g == GROUP_HOTEL {
                        self.state.tour.room_nights_by_month[month] += pd / t.party_size_mean;
                    }
                }
                for c in 0..rt.n_cat {
                    let amount = pd * rt.seg_budget[seg][c];
                    let persons = pd * t.categories.events_per_party_day[c];
                    let mut booked = 0.0;
                    for &b in &rt.cat_business[c] {
                        let share = rt.dist[g][c][b as usize];
                        if share > 0.0 {
                            self.state.revenue[b as usize] += amount * share;
                            self.state.tour.revenue[b as usize] += amount * share;
                            self.state.tour.persons[b as usize] += persons * share;
                            booked += amount * share;
                        }
                    }
                    self.state.tour.month_category_revenue[month][c] += booked;
                }
            }
        }
        self.state.tour.visitors_today = present;
        self.tour = Some(rt);
    }

    /// Tourist revenue so far across all city businesses.
    pub fn tourist_revenue(&self) -> f64 {
        self.state.tour.revenue.iter().sum()
    }

    /// Hotel rooms in the city file.
    pub fn hotel_rooms(&self) -> f64 {
        match &self.city.tourism {
            Some(t) => (0..t.capacity.len()).filter(|&b| t.lodging_type[b] == 1).map(|b| t.capacity[b] as f64).sum(),
            None => 0.0,
        }
    }

    /// Share of hotel room-nights sold over the days simulated so far.
    pub fn hotel_occupancy(&self) -> f64 {
        let nights: f64 = self.state.tour.room_nights_by_month.iter().sum();
        let days = self.state.day.max(1) as f64;
        if self.hotel_rooms() > 0.0 { nights / (self.hotel_rooms() * days) } else { 0.0 }
    }
}
