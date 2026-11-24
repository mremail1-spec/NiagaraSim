//! The city file written by the Python preparation step (`nfprep.build`).
use serde::Deserialize;

#[derive(Clone, Deserialize)]
pub struct City {
    pub meta: Meta,
    pub nodes: Nodes,
    pub edges: Edges,
    pub sim_categories: Vec<String>,
    pub businesses: Businesses,
    pub households: Households,
    pub categories: Categories,
    pub params: Params,
    #[serde(default)]
    pub business_groups: Vec<String>,
    /// Calibration targets for public operators: yearly revenue of the places each one runs.
    #[serde(default)]
    pub operator_targets: std::collections::HashMap<String, f64>,
    #[serde(default)]
    pub tourism: Option<Tourism>,
    /// Share of each sim category's trips made by drivers already passing (pass-by trips); empty when absent.
    #[serde(default)]
    pub pass_by: Vec<f64>,
    /// Places people avoid (documented open-air drug use, encampments); empty when absent.
    #[serde(default)]
    pub safety: Option<Safety>,
    /// Cluster bonus per sim category: pull times (1 + same-kind businesses within 300 m)^exponent,
    /// scaled to average 1 over the category's businesses (restaurants draw more on a dining street).
    #[serde(default)]
    pub cluster_exponent: Vec<f64>,
    /// Mean raw cluster factor per sim category; filled by `prepare_sites`.
    #[serde(default)]
    pub cluster_mean: Vec<f64>,
    /// Mean vehicles per day passing the businesses of each sim category; filled by `prepare_sites`.
    #[serde(default)]
    pub passing_mean: Vec<f64>,
}

pub const PASSING_CAP: f64 = 2.0;

#[derive(Clone, Deserialize, Default)]
pub struct Hotspot {
    pub lat: f64,
    pub lon: f64,
    pub scale_m: f64,
    pub visitor_penalty: f64,
    pub resident_penalty: f64,
}

#[derive(Clone, Deserialize, Default)]
pub struct Safety {
    pub hotspots: Vec<Hotspot>,
    /// Sim category indices not affected (health and recovery services).
    #[serde(default)]
    pub exempt: Vec<u32>,
    /// Multiplier on every penalty (outlooks: worse or better than now).
    #[serde(default = "one")]
    pub scale: f64,
}

fn one() -> f64 {
    1.0
}

impl City {
    /// Site factor of a business of `cat` at road `node`: (1 - p) + p * passing / mean passing for the
    /// category, where p is the category's pass-by share. Averages 1 over the category's businesses.
    pub fn site_factor_at(&self, node: u32, cat: u32) -> f64 {
        let p = self.pass_by.get(cat as usize).copied().unwrap_or(0.0);
        let mean = self.passing_mean.get(cat as usize).copied().unwrap_or(0.0);
        if p <= 0.0 || mean <= 0.0 || self.nodes.passing.is_empty() {
            return 1.0;
        }
        // SEED: a site's traffic counts up to twice the category average; pumps, drive-through
        // lanes and parking cap how many passing drivers a single business can serve.
        (1.0 - p) + p * (self.nodes.passing[node as usize] as f64 / mean).min(PASSING_CAP)
    }

    /// Share of pull kept at a point because people avoid nearby trouble spots (visitor or resident).
    pub fn safety_factor_at(&self, lat: f64, lon: f64, cat: u32, visitor: bool) -> f64 {
        let Some(s) = &self.safety else { return 1.0 };
        if s.exempt.contains(&cat) {
            return 1.0;
        }
        let mut keep = 1.0;
        for h in &s.hotspots {
            let d = ((lat - h.lat) * 111_320.0).hypot((lon - h.lon) * 111_320.0 * h.lat.to_radians().cos());
            let p = if visitor { h.visitor_penalty } else { h.resident_penalty } * s.scale;
            keep *= 1.0 - (p * (-d / h.scale_m.max(1.0)).exp()).min(0.95);
        }
        keep
    }

    /// Visitor pull before size: format weight, site factor and visitors' safety factor.
    pub fn pull_weight_visitor(&self, b: usize) -> f64 {
        let bz = &self.businesses;
        if bz.external[b] == 1 {
            return self.pull_weight(b);
        }
        bz.format_weight[b] * bz.site.get(b).copied().unwrap_or(1.0)
            * self.safety_factor_at(bz.lat[b], bz.lon[b], bz.sim_category[b], true)
    }

    /// Same-kind (cluster-group) businesses within 300 m of a point, not counting one at `skip`.
    fn cluster_count(&self, lat: f64, lon: f64, cat: u32, skip: Option<usize>) -> f64 {
        let b = &self.businesses;
        let k = lat.to_radians().cos();
        let mut n = 0.0;
        for i in 0..b.node.len() {
            if Some(i) == skip || b.external[i] == 1 {
                continue;
            }
            let c = b.sim_category[i] as usize;
            if self.cluster_exponent.get(c).copied().unwrap_or(0.0) <= 0.0 || !self.same_cluster(c, cat as usize) {
                continue;
            }
            let d = ((b.lat[i] - lat) * 111_320.0).hypot((b.lon[i] - lon) * 111_320.0 * k);
            if d < 300.0 {
                n += 1.0;
            }
        }
        n
    }

    /// Food kinds (restaurant, fast food, cafe, bar, nightlife) count as one cluster; others only themselves.
    fn same_cluster(&self, a: usize, b: usize) -> bool {
        const FOOD: [&str; 5] = ["restaurant", "fast_food", "cafe", "bar_pub", "nightlife"];
        let (na, nb) = (self.sim_categories[a].as_str(), self.sim_categories[b].as_str());
        a == b || (FOOD.contains(&na) && FOOD.contains(&nb))
    }

    /// Cluster factor for a business of `cat` at a point (1 when the category has no cluster bonus).
    pub fn cluster_factor_at(&self, lat: f64, lon: f64, cat: u32, skip: Option<usize>) -> f64 {
        let e = self.cluster_exponent.get(cat as usize).copied().unwrap_or(0.0);
        let m = self.cluster_mean.get(cat as usize).copied().unwrap_or(0.0);
        if e <= 0.0 || m <= 0.0 {
            return 1.0;
        }
        (1.0 + self.cluster_count(lat, lon, cat, skip)).powf(e) / m
    }

    /// Work out every business's site factor (call once after loading).
    pub fn prepare_sites(&mut self) {
        let n_cat = self.sim_categories.len();
        let b = &self.businesses;
        let (mut sum, mut cnt) = (vec![0f64; n_cat], vec![0f64; n_cat]);
        if !self.nodes.passing.is_empty() {
            for i in 0..b.node.len() {
                if b.external[i] == 0 {
                    let c = b.sim_category[i] as usize;
                    sum[c] += self.nodes.passing[b.node[i] as usize] as f64;
                    cnt[c] += 1.0;
                }
            }
        }
        self.passing_mean = (0..n_cat).map(|c| if cnt[c] > 0.0 { sum[c] / cnt[c] } else { 0.0 }).collect();
        let site: Vec<f64> = (0..b.node.len())
            .map(|i| if b.external[i] == 1 { 1.0 } else { self.site_factor_at(b.node[i], b.sim_category[i]) })
            .collect();
        self.businesses.site = site;
        // cluster bonus, scaled so each category's businesses average 1
        let n_b = self.businesses.node.len();
        let mut sum = vec![0f64; n_cat];
        let mut cnt = vec![0f64; n_cat];
        let mut raw = vec![1f64; n_b];
        for i in 0..n_b {
            let c = self.businesses.sim_category[i] as usize;
            let e = self.cluster_exponent.get(c).copied().unwrap_or(0.0);
            if e > 0.0 && self.businesses.external[i] == 0 {
                raw[i] = (1.0 + self.cluster_count(self.businesses.lat[i], self.businesses.lon[i], c as u32, Some(i))).powf(e);
                sum[c] += raw[i];
                cnt[c] += 1.0;
            }
        }
        self.cluster_mean = (0..n_cat).map(|c| if cnt[c] > 0.0 { sum[c] / cnt[c] } else { 0.0 }).collect();
        for i in 0..n_b {
            let c = self.businesses.sim_category[i] as usize;
            if cnt[c] > 0.0 && self.businesses.external[i] == 0 {
                self.businesses.site[i] *= raw[i] / self.cluster_mean[c];
            }
        }
    }

    /// A business's pull before size: its format weight times its site factor.
    pub fn pull_weight(&self, b: usize) -> f64 {
        let bz = &self.businesses;
        let safe = if bz.external[b] == 1 { 1.0 } else { self.safety_factor_at(bz.lat[b], bz.lon[b], bz.sim_category[b], false) };
        bz.format_weight[b] * bz.site.get(b).copied().unwrap_or(1.0) * safe
    }
}

#[derive(Clone, Deserialize)]
pub struct Segment {
    pub name: String,
    pub origin: String,
    pub overnight: u8,
    pub persons_per_year: f64,
    pub month_share: Vec<f64>,
    pub spend_per_person_day: f64,
}

#[derive(Clone, Deserialize)]
pub struct Nights {
    pub n: Vec<u32>,
    pub weight: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct LodgingShare {
    pub hotel: f64,
    pub rental: f64,
    pub camping: f64,
    pub private: f64,
}

#[derive(Clone, Deserialize)]
pub struct TouristCategories {
    pub name: Vec<String>,
    pub share: Vec<f64>,
    pub events_per_party_day: Vec<f64>,
    pub served_by: Vec<Vec<f64>>,
    /// Calibration: share of the travel survey's spending in each category that is spent at
    /// businesses inside the city (empty = all of it).
    #[serde(default)]
    pub capture: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct Gateway {
    pub name: String,
    pub node: u32,
    pub domestic: f64,
    pub us: f64,
    pub overseas: f64,
}

#[derive(Clone, Deserialize, Default)]
pub struct CarShare {
    pub domestic: f64,
    pub us: f64,
    pub overseas: f64,
}

/// Hotel demand fitted to published occupancy and room rates.
#[derive(Clone, Deserialize)]
pub struct HotelParams {
    /// Rooms a hotel party takes on average (above 1: some parties take two rooms).
    pub rooms_per_party: f64,
    /// Share of every hotel's rooms filled each night by stays the travel survey does not count
    /// (business, groups, long stays).
    pub base_share: f64,
    /// Average room rate by month.
    pub rate_by_month: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct PartyProfile {
    pub children_share_of_multi: f64,
    pub adult_age_share: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct Tourism {
    #[serde(default)]
    pub hotel: Option<HotelParams>,
    /// Where visitors' cars enter the city, with each origin's share.
    #[serde(default)]
    pub gateways: Vec<Gateway>,
    #[serde(default)]
    pub car_share: CarShare,
    pub segments: Vec<Segment>,
    pub party_size_mean: f64,
    /// Who is in a party: chance a party of 2+ has children, and the adults' age band shares
    /// (18-24, 25-34, 35-44, 45-54, 55-64, 65+), from the travel survey.
    #[serde(default)]
    pub party_profile: Option<PartyProfile>,
    /// Hotel class per business (0 economy, 1 midscale, 2 upper midscale, 3 upscale, 4 upper upscale and luxury),
    /// room price factor per class, and each visitor segment's preference for each class ([segment][class]).
    #[serde(default)]
    pub hotel_tier: Vec<u8>,
    #[serde(default)]
    pub tier_price: Vec<f64>,
    #[serde(default)]
    pub tier_pref: Vec<Vec<f64>>,
    /// Per business: room price relative to the city average (from published prices; 0 = use the class price),
    /// and how strongly guest ratings draw visitors (1 = average).
    #[serde(default)]
    pub hotel_price: Vec<f64>,
    #[serde(default)]
    pub hotel_quality: Vec<f64>,
    pub nights: Nights,
    pub lodging_share: LodgingShare,
    pub accommodation_spend_per_paid_person_night: f64,
    pub categories: TouristCategories,
    pub transport_fare_share: f64,
    pub day_of_week: Vec<f64>,
    pub first_weekday: u32,
    /// Room price factor by the night's weekday (0 = Monday night ... 6 = Sunday night), averaging 1 over a week.
    #[serde(default)]
    pub night_price_factor: Vec<f64>,
    /// Rooms (hotels), units (rentals) or sites (campgrounds) per business; 0 for everything else.
    pub capacity: Vec<u32>,
    /// 0 none, 1 hotel or motel, 2 rental or B&B, 3 campground.
    pub lodging_type: Vec<u8>,
    /// Published yearly attendance for the attractions that report it; 0 otherwise.
    pub attendance_target: Vec<f64>,
    /// Correction applied to the target when fitting pulls (published / simulated from a full agent
    /// year, nfprep.attendance_fit): the fit is exact in expectation, the agent year shifts it a little.
    #[serde(default)]
    pub attendance_fit_factor: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct Meta {
    pub year: u32,
    pub seed: u64,
    pub population: u32,
    pub fingerprint: String,
}

#[derive(Clone, Deserialize)]
pub struct Nodes {
    pub lat: Vec<f64>,
    pub lon: Vec<f64>,
    /// Vehicles passing each road node per day in the calibrated year (empty when absent).
    #[serde(default)]
    pub passing: Vec<f32>,
}

#[derive(Clone, Deserialize)]
pub struct Edges {
    pub from: Vec<u32>,
    pub to: Vec<u32>,
    pub km: Vec<f32>,
    /// Real driving time on the segment (what the clock shows).
    pub minutes: Vec<f32>,
    /// What a driver weighs when choosing a route: the minutes with main roads preferred.
    /// Empty = choose routes on the minutes themselves.
    #[serde(default)]
    pub route_cost: Vec<f32>,
}

impl Edges {
    /// Route-choice cost of every edge.
    pub fn cost(&self) -> &[f32] {
        if self.route_cost.len() == self.minutes.len() { &self.route_cost } else { &self.minutes }
    }
}

#[derive(Clone, Deserialize)]
pub struct Businesses {
    pub node: Vec<u32>,
    pub lat: Vec<f64>,
    pub lon: Vec<f64>,
    pub sim_category: Vec<u32>,
    pub open: Vec<u32>,
    pub close: Vec<u32>,
    pub core: Vec<u8>,
    pub staff: Vec<u32>,
    pub revenue_prior: Vec<f64>,
    pub name: Vec<String>,
    /// Industry group used for calibration (index into `business_groups`; empty when absent).
    #[serde(default)]
    pub group: Vec<u32>,
    /// 1 = Niagara Parks, 2 = OLG casino, 0 = other (empty when absent).
    #[serde(default)]
    pub operator: Vec<u8>,
    /// How much a store of this format pulls compared with a typical one in its group
    /// (a supermarket against a corner store).
    pub format_weight: Vec<f64>,
    /// 1 for a neighbouring town standing in for all its shops (out-of-town spending).
    pub external: Vec<u8>,
    /// Road distance and time beyond the city road node the town is reached through.
    pub extra_km: Vec<f32>,
    pub extra_min: Vec<f32>,
    /// A town's pull as a multiple of the whole city's pull in the same category.
    pub pull_scale: Vec<f64>,
    /// Chain id shared by branches of the same chain (-1: not a chain). Shoppers consider only the
    /// nearest branch of each chain: nobody drives past one Shoppers Drug Mart to reach another.
    #[serde(default)]
    pub chain: Vec<i32>,
    /// Pull from the site itself (passing traffic), 1 when absent; filled by `City::prepare_sites`.
    #[serde(default)]
    pub site: Vec<f64>,
    /// Month a business closed for good ("YYYY-MM"), "" if open; see Engine::apply_closures.
    #[serde(default)]
    pub closed_from: Vec<String>,
}

#[derive(Clone, Deserialize)]
pub struct Households {
    pub node: Vec<u32>,
    pub persons5: Vec<u32>,
    pub vehicles: Vec<u32>,
    /// 1 for a block of households in a neighbouring town (empty = all are city households).
    #[serde(default)]
    pub regional: Vec<u8>,
    /// Annual budget, households x categories, row by row.
    pub budget: Vec<f32>,
}

#[derive(Clone, Deserialize)]
pub struct Categories {
    pub name: Vec<String>,
    pub typical_visit_spend: Vec<f64>,
    pub retail: Vec<u8>,
    pub season: Vec<Vec<f64>>,
    pub served_by: Vec<Vec<f64>>,
    /// Calibration: multiplier on neighbouring towns' pull for each category (empty = 1 for all).
    #[serde(default)]
    pub external_scale: Vec<f64>,
}

#[derive(Clone, Deserialize)]
pub struct ModePct {
    pub auto_driver: f64,
    pub auto_passenger: f64,
    pub walk_cycle: f64,
    pub transit_school_bus_other: f64,
}

#[derive(Clone, Deserialize)]
pub struct Params {
    pub outings_per_person5_per_day: f64,
    pub hour_weights: Vec<f64>,
    pub mode_pct: ModePct,
    pub median_driver_trip_km_target: f64,
    pub online_share_of_retail: f64,
    pub walk_max_km: f64,
    pub spend_sigma: f64,
    pub attractiveness_exponent: f64,
    pub min_minutes: f64,
    /// Visitors on foot in a walkable core treat every place within this many minutes as equally
    /// near (0 = use min_minutes).
    #[serde(default)]
    pub tourist_min_minutes: f64,
}

impl Params {
    pub fn tourist_floor(&self) -> f64 {
        if self.tourist_min_minutes > 0.0 { self.tourist_min_minutes } else { self.min_minutes }
    }
}

impl City {
    pub fn from_json(text: &str) -> Result<City, String> {
        serde_json::from_str(text).map_err(|e| e.to_string())
    }
}
