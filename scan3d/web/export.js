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
