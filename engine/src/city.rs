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
    pub nights: Nights,
    pub lodging_share: LodgingShare,
    pub accommodation_spend_per_paid_person_night: f64,
    pub categories: TouristCategories,
    pub transport_fare_share: f64,
    pub day_of_week: Vec<f64>,
    pub first_weekday: u32,
    /// Rooms (hotels), units (rentals) or sites (campgrounds) per business; 0 for everything else.
    pub capacity: Vec<u32>,
    /// 0 none, 1 hotel or motel, 2 rental or B&B, 3 campground.
    pub lodging_type: Vec<u8>,
    /// Published yearly attendance for the attractions that report it; 0 otherwise.
    pub attendance_target: Vec<f64>,
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
}

#[derive(Clone, Deserialize)]
pub struct Edges {
    pub from: Vec<u32>,
    pub to: Vec<u32>,
    pub km: Vec<f32>,
    pub minutes: Vec<f32>,
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
}

impl City {
    pub fn from_json(text: &str) -> Result<City, String> {
        serde_json::from_str(text).map_err(|e| e.to_string())
    }
}
