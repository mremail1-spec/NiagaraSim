//! Minimal grayscale image utilities (float pixels, 0..1 range).

#[derive(Clone)]
pub struct Gray {
    pub w: usize,
    pub h: usize,
    pub data: Vec<f32>,
}

impl Gray {
    pub fn new(w: usize, h: usize) -> Self {
        Gray { w, h, data: vec![0.0; w * h] }
    }

    pub fn from_rgba(rgba: &[u8], w: usize, h: usize) -> Self {
        let mut g = Gray::new(w, h);
        for i in 0..w * h {
            let r = rgba[4 * i] as f32;
            let gg = rgba[4 * i + 1] as f32;
            let b = rgba[4 * i + 2] as f32;
            g.data[i] = (0.299 * r + 0.587 * gg + 0.114 * b) / 255.0;
        }
        g
    }

    #[inline]
    pub fn at(&self, x: usize, y: usize) -> f32 {
        self.data[y * self.w + x]
    }

    #[inline]
    pub fn at_clamped(&self, x: isize, y: isize) -> f32 {
        let x = x.clamp(0, self.w as isize - 1) as usize;
        let y = y.clamp(0, self.h as isize - 1) as usize;
        self.data[y * self.w + x]
    }

    /// Bilinear sample; None when outside the image.
    #[inline]
    pub fn sample(&self, x: f32, y: f32) -> Option<f32> {
        if !(x >= 0.0 && y >= 0.0 && x <= (self.w - 1) as f32 && y <= (self.h - 1) as f32) {
            return None;
        }
        let x0 = (x as usize).min(self.w - 2);
        let y0 = (y as usize).min(self.h - 2);
        let fx = x - x0 as f32;
        let fy = y - y0 as f32;
        let i = y0 * self.w + x0;
        let a = self.data[i];
        let b = self.data[i + 1];
        let c = self.data[i + self.w];
        let d = self.data[i + self.w + 1];
        Some(a + (b - a) * fx + (c - a) * fy + (a - b - c + d) * fx * fy)
    }

    pub fn blur(&self, sigma: f32) -> Gray {
        if sigma <= 0.01 {
            return self.clone();
        }
        let r = (3.0 * sigma).ceil() as isize;
        let mut k: Vec<f32> = (-r..=r).map(|i| (-(i * i) as f32 / (2.0 * sigma * sigma)).exp()).collect();
        let s: f32 = k.iter().sum();
        k.iter_mut().for_each(|v| *v /= s);
        let mut tmp = Gray::new(self.w, self.h);
        for y in 0..self.h {
            for x in 0..self.w {
                let mut acc = 0.0;
                for (j, kv) in k.iter().enumerate() {
                    acc += kv * self.at_clamped(x as isize + j as isize - r, y as isize);
                }
                tmp.data[y * self.w + x] = acc;
            }
        }
        let mut out = Gray::new(self.w, self.h);
        for y in 0..self.h {
            for x in 0..self.w {
                let mut acc = 0.0;
                for (j, kv) in k.iter().enumerate() {
                    acc += kv * tmp.at_clamped(x as isize, y as isize + j as isize - r);
                }
                out.data[y * self.w + x] = acc;
            }
        }
        out
    }

    /// Bilinear resize (caller blurs first when shrinking a lot).
    pub fn resize(&self, nw: usize, nh: usize) -> Gray {
        let mut out = Gray::new(nw, nh);
        let sx = self.w as f32 / nw as f32;
        let sy = self.h as f32 / nh as f32;
        for y in 0..nh {
            let fy = ((y as f32 + 0.5) * sy - 0.5).clamp(0.0, (self.h - 1) as f32);
            for x in 0..nw {
                let fx = ((x as f32 + 0.5) * sx - 0.5).clamp(0.0, (self.w - 1) as f32);
                out.data[y * nw + x] = self.sample(fx, fy).unwrap_or(0.0);
            }
        }
        out
    }

    /// Shrink by `factor` (>1) with anti-alias blur.
    pub fn downscale(&self, factor: f32) -> Gray {
        let nw = ((self.w as f32 / factor).round() as usize).max(2);
        let nh = ((self.h as f32 / factor).round() as usize).max(2);
        let b = if factor > 1.0 { self.blur(0.5 * (factor * factor - 1.0).sqrt()) } else { self.clone() };
        b.resize(nw, nh)
    }
}

/// Bilinear RGB sample from an RGBA buffer, returns 0..255 floats.
pub fn sample_rgb(rgba: &[u8], w: usize, h: usize, x: f32, y: f32) -> [f32; 3] {
    let x = x.clamp(0.0, (w - 1) as f32);
    let y = y.clamp(0.0, (h - 1) as f32);
    let x0 = (x as usize).min(w.saturating_sub(2));
    let y0 = (y as usize).min(h.saturating_sub(2));
    let fx = x - x0 as f32;
    let fy = y - y0 as f32;
    let mut out = [0.0f32; 3];
    for c in 0..3 {
        let p = |xx: usize, yy: usize| rgba[4 * (yy * w + xx) + c] as f32;
        let a = p(x0, y0);
        let b = p(x0 + 1, y0);
        let cc = p(x0, y0 + 1);
        let d = p(x0 + 1, y0 + 1);
        out[c] = a + (b - a) * fx + (cc - a) * fy + (a - b - cc + d) * fx * fy;
    }
    out
}
