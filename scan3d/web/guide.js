// Capture guidance: from the solved cameras, work out how much of the object
// has been covered and where the next photo should be taken from.

const BINS = 12;              // 30° sectors around the object
const STEP = 22;              // degrees to move between photos
const HIGH = 18;              // a "high" photo looks down this much more than the first one
const deg = (r) => (r * 180) / Math.PI;
const wrap = (a) => ((a + 540) % 360) - 180;

/**
 * s = { n, solved, mask: Uint8Array per frame, cams: Float32Array (13 per registered cam:
 *       index, centre xyz, R row-major), target: [x,y,z] | [] }
 * returns { arrow, title, detail, ring: bool[12], high: bool[12], cams: [{az, el, idx}], complete, phase }
 */
export function guide(s) {
  const out = { arrow: null, title: '', detail: '', ring: Array(BINS).fill(false), high: Array(BINS).fill(false), cams: [], complete: false, phase: 'ring' };
  if (s.n === 0) {
    out.title = 'Take the first photo';
    out.detail = 'Frame the whole object with a little space around it.';
    out.arrow = 'shoot';
    return out;
  }
  if (!s.solved) {
    out.arrow = 'right';
    if (s.n === 1) {
      out.title = 'Step to your right and take the next photo';
      out.detail = 'Walk sideways around the object, about one step. Keep it centred. Most of the object should still be visible in both photos.';
    } else {
      out.title = 'Keep circling to the right';
      out.detail = 'The photos do not show enough change in angle yet, or do not overlap. Walk around the object, do not just turn the phone. A plain object needs a patterned surface under it to connect the photos.';
    }
    return out;
  }
  const t = s.target.length === 3 ? s.target : [0, 0, 0];
  const cams = [];
  for (let k = 0; k < s.cams.length / 13; k++) {
    const b = 13 * k;
    const idx = s.cams[b];
    const c = [s.cams[b + 1] - t[0], s.cams[b + 2] - t[1], s.cams[b + 3] - t[2]];
    const R = Array.from(s.cams.slice(b + 4, b + 13));
    const horiz = Math.hypot(c[0], c[2]);
    cams.push({ idx, c, R, az: deg(Math.atan2(c[2], c[0])), el: deg(Math.atan2(c[1], horiz)), dist: Math.hypot(...c) });
  }
  cams.sort((a, b) => a.idx - b.idx);
  out.cams = cams;
  const el0 = cams[0].el;
  for (const cam of cams) {
    const bin = Math.floor((((cam.az % 360) + 360) % 360) / (360 / BINS)) % BINS;
    (cam.el >= el0 + HIGH ? out.high : out.ring)[bin] = true;
  }
  const ringDone = out.ring.filter(Boolean).length;
  const highDone = out.high.filter(Boolean).length;
  out.phase = ringDone >= BINS ? 'high' : 'ring';
  if (ringDone >= BINS && highDone >= 8) {
    out.complete = true;
    out.arrow = 'done';
    out.title = 'Coverage complete';
    out.detail = 'You have photos from all around the object and from above. Press Finish to build the model, or add close-ups of details first.';
    return out;
  }

  // Two unplaced frames in a row means the user has drifted off; one is often
  // just not solved yet, so steer from the latest placed camera instead.
  const unplaced = (i) => i >= 0 && !s.mask[i];
  if (s.n >= 3 && unplaced(s.n - 1) && unplaced(s.n - 2)) {
    out.arrow = 'back';
    out.title = 'Those photos did not fit. Go back a little';
    out.detail = 'The last photos did not connect to the others. Go back toward the previous good spot and take smaller steps. Plain or shiny objects give little to match: put the object on a patterned surface (a newspaper, a printed cloth) and keep some of it in every photo.';
    return out;
  }
  const lastCam = cams[cams.length - 1];
  const lastIdx = lastCam.idx;
  // Direction of travel from the last two registered photos.
  const prev = cams.filter((c) => c.idx < lastIdx).pop();
  let dir = 1, stepDeg = null;
  if (prev) {
    stepDeg = wrap(lastCam.az - prev.az);
    if (Math.abs(stepDeg) > 3) dir = Math.sign(stepDeg);
  }
  // Where the next photo should come from.
  let targetEl = lastCam.el;
  if (out.phase === 'high') targetEl = el0 + HIGH + 10;
  const az = ((lastCam.az + dir * STEP) * Math.PI) / 180;
  const elr = (targetEl * Math.PI) / 180;
  const next = [Math.cos(az) * Math.cos(elr) * lastCam.dist, Math.sin(elr) * lastCam.dist, Math.sin(az) * Math.cos(elr) * lastCam.dist];
  const d = [next[0] - lastCam.c[0], next[1] - lastCam.c[1], next[2] - lastCam.c[2]];
  const R = lastCam.R; // world -> camera rows: right, down, forward
  const right = R[0] * d[0] + R[1] * d[1] + R[2] * d[2];
  const down = R[3] * d[0] + R[4] * d[1] + R[5] * d[2];
  const side = right >= 0 ? 'right' : 'left';
  const notes = [];
  if (stepDeg !== null && Math.abs(stepDeg) > 40) notes.push('That step was large; smaller steps give a more reliable model.');
  const medDist = cams.map((c) => c.dist).sort((a, b) => a - b)[Math.floor(cams.length / 2)];
  if (lastCam.dist > medDist * 1.35) notes.push('You are farther away than in earlier photos; move a bit closer.');
  else if (lastCam.dist < medDist * 0.7) notes.push('You are closer than in earlier photos; step back a bit.');
  // Is the object centred in the last photo?
  const tc = [-lastCam.c[0], -lastCam.c[1], -lastCam.c[2]];
  const zf = R[6] * tc[0] + R[7] * tc[1] + R[8] * tc[2];
  const xr = R[0] * tc[0] + R[1] * tc[1] + R[2] * tc[2];
  if (zf <= 0 || Math.abs(xr / zf) > 0.3) notes.push('Aim the camera at the object so it stays near the centre.');

  if (out.phase === 'high' && -down > Math.abs(right) * 0.6 && lastCam.el < el0 + HIGH) {
    out.arrow = 'up';
    out.title = 'Now raise the phone and look down at the object';
    out.detail = `The first lap is complete. Hold the phone higher and tilt it down, then go around again, ${side} each time. ${highDone}/8 high views done.`;
  } else {
    out.arrow = side;
    const left = BINS - ringDone;
    out.title = `Step to your ${side} and take the next photo`;
    out.detail = out.phase === 'ring'
      ? `About ${STEP}° further around the object. ${ringDone}/${BINS} sides covered${left ? `, ${left} to go` : ''}.`
      : `Keep the phone high and looking down. ${highDone}/8 high views done.`;
  }
  if (notes.length) out.detail += ' ' + notes.join(' ');
  return out;
}

/** Top-down coverage ring as SVG markup (all values computed locally, no user text). */
export function ringSvg(g, size = 150) {
  const c = size / 2, r1 = size * 0.30, r2 = size * 0.40, r3 = size * 0.48;
  const seg = (i, ra, rb, on, cls) => {
    const a0 = ((i * 30 + 1.5) * Math.PI) / 180, a1 = (((i + 1) * 30 - 1.5) * Math.PI) / 180;
    const p = (rr, a) => `${(c + rr * Math.cos(a)).toFixed(1)},${(c + rr * Math.sin(a)).toFixed(1)}`;
    return `<path d="M${p(ra, a0)} A${ra},${ra} 0 0 1 ${p(ra, a1)} L${p(rb, a1)} A${rb},${rb} 0 0 0 ${p(rb, a0)} Z" class="${cls}${on ? ' on' : ''}"/>`;
  };
  let s = `<svg viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" role="img" aria-label="Coverage: ${g.ring.filter(Boolean).length} of 12 sides, ${g.high.filter(Boolean).length} high views">`;
  for (let i = 0; i < 12; i++) s += seg(i, r1, r2 - 2, g.ring[i], 'seg') + seg(i, r2, r3, g.high[i], 'seg hi');
  s += `<circle cx="${c}" cy="${c}" r="${size * 0.07}" class="obj"/>`;
  g.cams.forEach((cam, k) => {
    const a = (cam.az * Math.PI) / 180, rr = size * 0.2;
    const last = k === g.cams.length - 1;
    s += `<circle cx="${(c + rr * Math.cos(a)).toFixed(1)}" cy="${(c + rr * Math.sin(a)).toFixed(1)}" r="${last ? 4 : 2.5}" class="cam${last ? ' last' : ''}"/>`;
  });
  return s + '</svg>';
}
