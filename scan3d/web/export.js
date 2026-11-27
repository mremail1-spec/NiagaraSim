// Exporters: PLY (points or mesh), OBJ with vertex colours, binary glTF (GLB).

export function download(name, data, mime = 'application/octet-stream') {
  const url = URL.createObjectURL(new Blob([data], { type: mime }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function plyPoints({ pos, col, nrm }) {
  const n = pos.length / 3;
  const hasN = nrm && nrm.length === pos.length;
  const header =
    'ply\nformat binary_little_endian 1.0\ncomment scan3d\n' +
    `element vertex ${n}\nproperty float x\nproperty float y\nproperty float z\n` +
    (hasN ? 'property float nx\nproperty float ny\nproperty float nz\n' : '') +
    'property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n';
  const stride = (hasN ? 24 : 12) + 3;
  const head = new TextEncoder().encode(header);
  const buf = new ArrayBuffer(head.length + n * stride);
  new Uint8Array(buf).set(head);
  const dv = new DataView(buf);
  let o = head.length;
  for (let i = 0; i < n; i++) {
    for (let a = 0; a < 3; a++, o += 4) dv.setFloat32(o, pos[3 * i + a], true);
    if (hasN) for (let a = 0; a < 3; a++, o += 4) dv.setFloat32(o, nrm[3 * i + a], true);
    for (let a = 0; a < 3; a++, o++) dv.setUint8(o, col[3 * i + a]);
  }
  return buf;
}

export function plyMesh({ pos, col, idx }) {
  const n = pos.length / 3, f = idx.length / 3;
  const header =
    'ply\nformat binary_little_endian 1.0\ncomment scan3d\n' +
    `element vertex ${n}\nproperty float x\nproperty float y\nproperty float z\n` +
    'property uchar red\nproperty uchar green\nproperty uchar blue\n' +
    `element face ${f}\nproperty list uchar int vertex_indices\nend_header\n`;
  const head = new TextEncoder().encode(header);
  const buf = new ArrayBuffer(head.length + n * 15 + f * 13);
  new Uint8Array(buf).set(head);
  const dv = new DataView(buf);
  let o = head.length;
  for (let i = 0; i < n; i++) {
    for (let a = 0; a < 3; a++, o += 4) dv.setFloat32(o, pos[3 * i + a], true);
    for (let a = 0; a < 3; a++, o++) dv.setUint8(o, col[3 * i + a]);
  }
  for (let i = 0; i < f; i++) {
    dv.setUint8(o++, 3);
    for (let a = 0; a < 3; a++, o += 4) dv.setInt32(o, idx[3 * i + a], true);
  }
  return buf;
}

export function obj({ pos, col, idx }) {
  const lines = ['# scan3d mesh (vertex colours after xyz)'];
  for (let i = 0; i < pos.length; i += 3) {
    lines.push(`v ${pos[i].toFixed(5)} ${pos[i + 1].toFixed(5)} ${pos[i + 2].toFixed(5)} ` +
      `${(col[i] / 255).toFixed(3)} ${(col[i + 1] / 255).toFixed(3)} ${(col[i + 2] / 255).toFixed(3)}`);
  }
  for (let i = 0; i < idx.length; i += 3) lines.push(`f ${idx[i] + 1} ${idx[i + 1] + 1} ${idx[i + 2] + 1}`);
  return lines.join('\n') + '\n';
}

export function glb({ pos, col, nrm, idx }) {
  const n = pos.length / 3;
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity];
  for (let i = 0; i < n; i++) for (let a = 0; a < 3; a++) {
    min[a] = Math.min(min[a], pos[3 * i + a]);
    max[a] = Math.max(max[a], pos[3 * i + a]);
  }
  const rgba = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) { rgba.set(col.subarray(3 * i, 3 * i + 3), 4 * i); rgba[4 * i + 3] = 255; }
  const parts = [new Uint8Array(pos.buffer, pos.byteOffset, pos.byteLength),
    new Uint8Array(nrm.buffer, nrm.byteOffset, nrm.byteLength), rgba,
    new Uint8Array(idx.buffer, idx.byteOffset, idx.byteLength)];
  const views = [];
  let off = 0;
  for (const p of parts) { views.push({ buffer: 0, byteOffset: off, byteLength: p.byteLength }); off += (p.byteLength + 3) & ~3; }
  views[3].target = 34963;
  for (let i = 0; i < 3; i++) views[i].target = 34962;
  const gltf = {
    asset: { version: '2.0', generator: 'scan3d' },
    scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0, name: 'scan' }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1, COLOR_0: 2 }, indices: 3, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorFactor: [1, 1, 1, 1], metallicFactor: 0, roughnessFactor: 1 }, doubleSided: true }],
    buffers: [{ byteLength: off }],
    bufferViews: views,
    accessors: [
      { bufferView: 0, componentType: 5126, count: n, type: 'VEC3', min, max },
      { bufferView: 1, componentType: 5126, count: n, type: 'VEC3' },
      { bufferView: 2, componentType: 5121, normalized: true, count: n, type: 'VEC4' },
      { bufferView: 3, componentType: 5125, count: idx.length, type: 'SCALAR' },
    ],
  };
  let json = new TextEncoder().encode(JSON.stringify(gltf));
  const jlen = (json.length + 3) & ~3;
  const total = 12 + 8 + jlen + 8 + off;
  const out = new ArrayBuffer(total);
  const dv = new DataView(out);
  const u8 = new Uint8Array(out);
  dv.setUint32(0, 0x46546c67, true); dv.setUint32(4, 2, true); dv.setUint32(8, total, true);
  dv.setUint32(12, jlen, true); dv.setUint32(16, 0x4e4f534a, true);
  u8.set(json, 20); for (let i = json.length; i < jlen; i++) u8[20 + i] = 0x20;
  let o = 20 + jlen;
  dv.setUint32(o, off, true); dv.setUint32(o + 4, 0x004e4942, true); o += 8;
  let p0 = o;
  for (let i = 0; i < parts.length; i++) u8.set(parts[i], p0 + views[i].byteOffset);
  return out;
}

const CRC = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
function crc32(u8) { let c = 0xffffffff; for (let i = 0; i < u8.length; i++) c = CRC[(c ^ u8[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }

/** Uncompressed ZIP archive from [name, string|ArrayBuffer|Uint8Array] entries. */
export function zip(entries) {
  const enc = new TextEncoder(), parts = [], central = [];
  let off = 0;
  for (const [name, data] of entries) {
    const d = typeof data === 'string' ? enc.encode(data) : new Uint8Array(data.buffer || data, data.byteOffset || 0, data.byteLength);
    const nm = enc.encode(name), crc = crc32(d);
    const h = new DataView(new ArrayBuffer(30));
    [[0, 0x04034b50, 4], [4, 20, 2], [8, 0, 2], [10, 0, 2], [12, 0x21, 2], [14, crc, 4], [18, d.length, 4], [22, d.length, 4], [26, nm.length, 2], [28, 0, 2]]
      .forEach(([o, v, s]) => (s === 4 ? h.setUint32(o, v, true) : h.setUint16(o, v, true)));
    const c = new DataView(new ArrayBuffer(46));
    [[0, 0x02014b50, 4], [4, 20, 2], [6, 20, 2], [8, 0, 2], [10, 0, 2], [12, 0, 2], [14, 0x21, 2], [16, crc, 4], [20, d.length, 4], [24, d.length, 4],
      [28, nm.length, 2], [30, 0, 2], [32, 0, 2], [34, 0, 2], [36, 0, 2], [38, 0, 4], [42, off, 4]]
      .forEach(([o, v, s]) => (s === 4 ? c.setUint32(o, v, true) : c.setUint16(o, v, true)));
    parts.push(new Uint8Array(h.buffer), nm, d);
    central.push(new Uint8Array(c.buffer), nm);
    off += 30 + nm.length + d.length;
  }
  const cdSize = central.reduce((a, b) => a + b.length, 0);
  const e = new DataView(new ArrayBuffer(22));
  e.setUint32(0, 0x06054b50, true); e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
  e.setUint32(12, cdSize, true); e.setUint32(16, off, true);
  return new Blob([...parts, ...central, new Uint8Array(e.buffer)], { type: 'application/zip' });
}

/** Photo bytes from an <img> whose src is a data: or same-origin URL. */
export async function imageBytes(img) {
  return new Uint8Array(await (await fetch(img.src)).arrayBuffer());
}
