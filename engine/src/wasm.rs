//! Browser interface: plain exported functions the page calls directly (no bundler needed).
//! Data moves through raw buffers inside the module's memory.
#![allow(static_mut_refs)]
use crate::{City, Engine};

static mut ENGINE: Option<Engine> = None;
static mut BUF: Vec<f32> = Vec::new();

fn engine() -> &'static mut Engine {
    unsafe { ENGINE.as_mut().expect("call nf_init first") }
}

/// Reserve `len` bytes for the page to copy the city file into.
#[no_mangle]
pub extern "C" fn nf_alloc(len: usize) -> *mut u8 {
    let mut v = Vec::<u8>::with_capacity(len);
    let p = v.as_mut_ptr();
    std::mem::forget(v);
    p
}

/// Build the city from the JSON at `ptr`. `beta` <= 0 means calibrate. Returns 1 on success, 0 on a bad file.
#[no_mangle]
pub unsafe extern "C" fn nf_init(ptr: *mut u8, len: usize, seed: u32, beta: f64) -> i32 {
    let bytes = Vec::from_raw_parts(ptr, len, len);
    let text = match String::from_utf8(bytes) {
        Ok(t) => t,
        Err(_) => return 0,
    };
    let city = match City::from_json(&text) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    drop(text);
    ENGINE = Some(if beta > 0.0 { Engine::new(city, beta, seed as u64) } else { Engine::calibrated(city, seed as u64) });
    1
}

#[no_mangle]
pub extern "C" fn nf_step_hour() {
    engine().step_hour();
}

#[no_mangle]
pub extern "C" fn nf_step_day() {
    engine().step_day();
}

/// Fast mode: book a day's expected revenue without simulating trips. Only valid at midnight.
#[no_mangle]
pub extern "C" fn nf_step_day_flow() {
    engine().step_day_flow();
}

#[no_mangle]
pub extern "C" fn nf_day() -> u32 {
    engine().state().day
}

#[no_mangle]
pub extern "C" fn nf_hour() -> u32 {
    engine().state().hour
}

#[no_mangle]
pub extern "C" fn nf_beta() -> f64 {
    engine().beta
}

#[no_mangle]
pub extern "C" fn nf_business_count() -> u32 {
    engine().state().revenue.len() as u32
}

/// Pointer to revenue so far, one f64 per business.
#[no_mangle]
pub extern "C" fn nf_revenue_ptr() -> *const f64 {
    engine().state().revenue.as_ptr()
}

/// Pointer to visits so far, one u64 per business.
#[no_mangle]
pub extern "C" fn nf_visits_ptr() -> *const u64 {
    engine().state().visits.as_ptr()
}

#[no_mangle]
pub extern "C" fn nf_city_revenue() -> f64 {
    engine().city_revenue()
}

#[no_mangle]
pub extern "C" fn nf_out_of_town() -> f64 {
    engine().out_of_town_revenue()
}

#[no_mangle]
pub extern "C" fn nf_online() -> f64 {
    engine().state().online_leakage
}

#[no_mangle]
pub extern "C" fn nf_outings() -> f64 {
    engine().state().outings as f64
}

/// Copy up to `max` of the last hour's trips into the buffer as (home road node, business, mode, spend,
/// household, category, minutes, km).
/// Returns how many were copied; they are spread evenly through the hour's trips.
#[no_mangle]
pub extern "C" fn nf_trips(max: u32) -> u32 {
    let e = engine();
    let n = e.last_trips.len();
    let take = n.min(max as usize);
    unsafe {
        BUF.clear();
        for i in 0..take {
            let t = &e.last_trips[i * n / take.max(1)];
            BUF.push(e.city.households.node[t.household as usize] as f32);
            BUF.push(t.business as f32);
            BUF.push(t.mode as u8 as f32);
            BUF.push(t.spend);
            BUF.push(t.household as f32);
            BUF.push(t.category as f32);
            let home = e.city.households.node[t.household as usize] as usize;
            BUF.push(e.trip_minutes(home, t.business as usize) as f32);
            BUF.push(t.km);
        }
    }
    take as u32
}

/// Every trip one household made in the last hour, as (business, mode, spend, category, minutes, km).
#[no_mangle]
pub extern "C" fn nf_household_trips(household: u32) -> u32 {
    let e = engine();
    let home = e.city.households.node[household as usize] as usize;
    unsafe {
        BUF.clear();
        for t in e.last_trips.iter().filter(|t| t.household == household) {
            BUF.extend([t.business as f32, t.mode as u8 as f32, t.spend, t.category as f32,
                        e.trip_minutes(home, t.business as usize) as f32, t.km]);
        }
        (BUF.len() / 6) as u32
    }
}

/// Every purchase one tourist party made in the last hour, as (business, persons, spend, category, minutes, km).
#[no_mangle]
pub extern "C" fn nf_party_trips(party: u32) -> u32 {
    let e = engine();
    unsafe {
        BUF.clear();
        for t in e.last_tourist_trips.iter().filter(|t| t.party == party) {
            BUF.extend([t.business as f32, t.persons as f32, t.spend, t.category as f32,
                        e.trip_minutes(t.base_node as usize, t.business as usize) as f32,
                        e.trip_km(t.base_node as usize, t.business as usize) as f32]);
        }
        (BUF.len() / 6) as u32
    }
}

/// Number of trips made in the last hour stepped.
#[no_mangle]
pub extern "C" fn nf_trip_count() -> u32 {
    engine().last_trips.len() as u32
}

/// Write the road nodes of the fastest route into the buffer; returns how many.
#[no_mangle]
pub extern "C" fn nf_route(from_node: u32, business: u32) -> u32 {
    let path = engine().route(from_node, business);
    unsafe {
        BUF.clear();
        BUF.extend(path.iter().map(|&n| n as f32));
    }
    path.len() as u32
}

#[no_mangle]
pub extern "C" fn nf_buf_ptr() -> *const f32 {
    unsafe { BUF.as_ptr() }
}

// ---- tourists ----

#[no_mangle]
pub extern "C" fn nf_tourist_revenue() -> f64 {
    engine().tourist_revenue()
}

/// Visitors in town on the day most recently started.
#[no_mangle]
pub extern "C" fn nf_visitors_today() -> f64 {
    engine().state().tour.visitors_today
}

/// Pointer to tourist revenue so far, one f64 per business (null if the city has no tourism data).
#[no_mangle]
pub extern "C" fn nf_tourist_revenue_ptr() -> *const f64 {
    engine().state().tour.revenue.as_ptr()
}

/// Share of hotel rooms in use tonight.
#[no_mangle]
pub extern "C" fn nf_rooms_in_use_share() -> f64 {
    let e = engine();
    match &e.city.tourism {
        None => 0.0,
        Some(t) => {
            let (mut rooms, mut free) = (0f64, 0f64);
            for b in 0..t.capacity.len() {
                if t.lodging_type[b] == 1 {
                    rooms += t.capacity[b] as f64;
                    free += e.state().tour.free[b] as f64;
                }
            }
            if rooms > 0.0 { 1.0 - free / rooms } else { 0.0 }
        }
    }
}

#[no_mangle]
pub extern "C" fn nf_tourist_trip_count() -> u32 {
    engine().last_tourist_trips.len() as u32
}

/// Copy up to `max` of the last hour's tourist purchases into the buffer as
/// (base road node, business, persons, spend, party, segment, group, lodging (-1 none), nights left,
/// category, minutes, km).
#[no_mangle]
pub extern "C" fn nf_tourist_trips(max: u32) -> u32 {
    let e = engine();
    let n = e.last_tourist_trips.len();
    let take = n.min(max as usize);
    unsafe {
        BUF.clear();
        for i in 0..take {
            let t = &e.last_tourist_trips[i * n / take.max(1)];
            BUF.push(t.base_node as f32);
            BUF.push(t.business as f32);
            BUF.push(t.persons as f32);
            BUF.push(t.spend);
            BUF.push(t.party as f32);
            BUF.push(t.segment as f32);
            BUF.push(t.group as f32);
            BUF.push(if t.lodging == u32::MAX { -1.0 } else { t.lodging as f32 });
            BUF.push(t.days_left as f32);
            BUF.push(t.category as f32);
            BUF.push(e.trip_minutes(t.base_node as usize, t.business as usize) as f32);
            BUF.push(e.trip_km(t.base_node as usize, t.business as usize) as f32);
        }
    }
    take as u32
}

// ---- place a business ----

/// Forecast a new business at a road node. Fills the buffer with:
/// resident revenue, tourist revenue, amount won back from out of town, 12 monthly revenues,
/// number of competitors listed, then (business index, yearly loss) pairs. Returns the buffer length.
#[no_mangle]
pub extern "C" fn nf_forecast(node: u32, sim_category: u32, size: f64) -> u32 {
    let f = engine().forecast(node, sim_category, size, 1.0);
    unsafe {
        BUF.clear();
        BUF.push(f.resident as f32);
        BUF.push(f.tourist as f32);
        BUF.push(f.from_out_of_town as f32);
        BUF.extend(f.monthly.iter().map(|&m| m as f32));
        BUF.push(f.losses.len() as f32);
        for (b, loss) in &f.losses {
            BUF.push(*b as f32);
            BUF.push(*loss as f32);
        }
        BUF.len() as u32
    }
}

/// Add a business to the running city; returns its index.
#[no_mangle]
pub extern "C" fn nf_add_business(node: u32, lat: f64, lon: f64, sim_category: u32, size: f64, open: u32, close: u32, in_core: u32) -> u32 {
    engine().add_business(node, lat, lon, sim_category, size, 1.0, open, close, in_core == 1, "New business")
}

/// Spending so far by residents of neighbouring towns at city businesses.
#[no_mangle]
pub extern "C" fn nf_inbound() -> f64 {
    engine().inbound_revenue()
}

// ---- outlooks ----

/// Apply an outlook (multipliers on the calibrated year). Call before stepping, or after a restart.
#[no_mangle]
pub extern "C" fn nf_set_safety(scale: f64) {
    engine().set_safety_scale(scale);
}

#[no_mangle]
pub extern "C" fn nf_set_scenario(us: f64, domestic: f64, overseas: f64, base_stays: f64, resident: f64) {
    engine().set_scenario(us, domestic, overseas, base_stays, resident);
}

/// Like nf_forecast, but the buffer holds: resident revenue, then tourist revenue from domestic,
/// US and overseas visitors. Lets the page work out a range without rebuilding the city.
#[no_mangle]
pub extern "C" fn nf_forecast_by_origin(node: u32, sim_category: u32, size: f64) -> u32 {
    let f = engine().forecast(node, sim_category, size, 1.0);
    unsafe {
        BUF.clear();
        BUF.push(f.resident as f32);
        BUF.extend(f.tourist_by_origin.iter().map(|&x| x as f32));
        BUF.len() as u32
    }
}
