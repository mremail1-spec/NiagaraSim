//! Small deterministic random number generator (xoshiro256**), written out here so that the
//! browser build and the native build produce bit-identical cities from the same seed.
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
pub struct Rng {
    s: [u64; 4],
}

impl Rng {
    pub fn new(seed: u64) -> Self {
        let mut z = seed.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut s = [0u64; 4];
        for v in s.iter_mut() {
            z = z.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut x = z;
            x = (x ^ (x >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            x = (x ^ (x >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            *v = x ^ (x >> 31);
        }
        Rng { s }
    }

    pub fn next_u64(&mut self) -> u64 {
        let r = self.s[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = self.s[1] << 17;
        self.s[2] ^= self.s[0];
        self.s[3] ^= self.s[1];
        self.s[1] ^= self.s[2];
        self.s[0] ^= self.s[3];
        self.s[2] ^= t;
        self.s[3] = self.s[3].rotate_left(45);
        r
    }

    /// Uniform in [0, 1).
    pub fn f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }

    pub fn poisson(&mut self, lambda: f64) -> u32 {
        // Knuth's method; lambda here is a household's outings per day (about 1 to 4).
        let limit = (-lambda).exp();
        let (mut k, mut p) = (0u32, 1.0f64);
        loop {
            p *= self.f64();
            if p <= limit {
                return k;
            }
            k += 1;
        }
    }

    pub fn normal(&mut self) -> f64 {
        let u1 = (1.0 - self.f64()).max(1e-300);
        let u2 = self.f64();
        (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
    }

    /// Log-normal with mean 1 and the given sigma.
    pub fn lognormal_mean1(&mut self, sigma: f64) -> f64 {
        (sigma * self.normal() - sigma * sigma / 2.0).exp()
    }
}
