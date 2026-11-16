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

/// Copy up to `max` of the last hour's trips into the buffer as (home road node, business, mode, spend).
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
        }
    }
    take as u32
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
