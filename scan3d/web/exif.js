// Read the lens field of view from a photo's EXIF data (JPEG only).
// Phones record FocalLengthIn35mmFilm, which fixes the field of view exactly.

/** Field of view (degrees) along the image's long side, or null. */
export async function exifFov(file) {
  if (!/jpe?g$/i.test(file.type) && !/\.jpe?g$/i.test(file.name || '')) return null;
  const buf = await file.slice(0, 256 * 1024).arrayBuffer();
  const d = new DataView(buf);
  if (d.byteLength < 4 || d.getUint16(0) !== 0xffd8) return null;
  let off = 2;
  while (off + 4 < d.byteLength) {
    const marker = d.getUint16(off);
    const len = d.getUint16(off + 2);
    if (marker === 0xffe1 && d.getUint32(off + 4) === 0x45786966) return parseTiff(d, off + 10);
    if ((marker & 0xff00) !== 0xff00 || marker === 0xffda) break;
    off += 2 + len;
  }
  return null;
}

function parseTiff(d, t) {
  const le = d.getUint16(t) === 0x4949;
  const u16 = (o) => d.getUint16(t + o, le);
  const u32 = (o) => d.getUint32(t + o, le);
  const readIfd = (o) => {
    const tags = {};
    if (t + o + 2 > d.byteLength) return tags;
    const n = u16(o);
    for (let i = 0; i < n; i++) {
      const e = o + 2 + 12 * i;
      if (t + e + 12 > d.byteLength) break;
      const tag = u16(e), type = u16(e + 2);
      if (type === 3) tags[tag] = u16(e + 8);
      else if (type === 4) tags[tag] = u32(e + 8);
      else if (type === 5) { const p = u32(e + 8); tags[tag] = u32(p) / (u32(p + 4) || 1); }
    }
    return tags;
  };
  const ifd0 = readIfd(u32(4));
  if (!ifd0[0x8769]) return null;
  const ex = readIfd(ifd0[0x8769]);
  const f35 = ex[0xa405];
  const w = ex[0xa002], h = ex[0xa003];
  if (!f35) return null;
  // 35 mm equivalence refers to the 43.27 mm full-frame diagonal.
  const aspect = w && h ? Math.max(w, h) / Math.min(w, h) : 4 / 3;
  const longMm = 43.27 * aspect / Math.hypot(aspect, 1);
  return (2 * Math.atan(longMm / 2 / f35) * 180) / Math.PI;
}
