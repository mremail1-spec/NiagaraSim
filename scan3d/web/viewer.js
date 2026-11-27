// Small WebGL2 orbit viewer for coloured point clouds, meshes and camera frusta.

const VS_POINTS = `#version 300 es
uniform mat4 uMVP; uniform mat4 uMV; uniform float uSize;
in vec3 aPos; in vec3 aCol;
out vec3 vCol;
void main() {
  vec4 p = uMV * vec4(aPos, 1.0);
  gl_Position = uMVP * vec4(aPos, 1.0);
  gl_PointSize = clamp(uSize / max(-p.z, 0.01), 1.0, 12.0);
  vCol = aCol;
}`;
const FS_POINTS = `#version 300 es
precision mediump float; in vec3 vCol; out vec4 o;
void main() { o = vec4(vCol, 1.0); }`;
const VS_MESH = `#version 300 es
uniform mat4 uMVP; uniform mat4 uMV;
in vec3 aPos; in vec3 aCol; in vec3 aNrm;
out vec3 vCol; out vec3 vN; out vec3 vP;
void main() {
  gl_Position = uMVP * vec4(aPos, 1.0);
  vN = mat3(uMV) * aNrm; vP = (uMV * vec4(aPos, 1.0)).xyz; vCol = aCol;
}`;
const FS_MESH = `#version 300 es
precision mediump float; uniform float uShade; uniform float uTex;
in vec3 vCol; in vec3 vN; in vec3 vP; out vec4 o;
void main() {
  vec3 n = normalize(vN);
  if (!gl_FrontFacing) n = -n;
  float l = 0.35 + 0.65 * max(dot(n, normalize(-vP)), 0.0);
  vec3 base = mix(vec3(0.82), vCol, uTex);
  o = vec4(base * mix(1.0, l, uShade), 1.0);
}`;
const VS_LINE = `#version 300 es
uniform mat4 uMVP; in vec3 aPos; void main() { gl_Position = uMVP * vec4(aPos, 1.0); }`;
const FS_LINE = `#version 300 es
precision mediump float; uniform vec3 uColor; out vec4 o; void main() { o = vec4(uColor, 1.0); }`;

function compile(gl, vs, fs) {
  const p = gl.createProgram();
  for (const [type, src] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) {
    const s = gl.createShader(type);
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    gl.attachShader(p, s);
  }
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
  return p;
}

const m4 = {
  persp(fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), nf = 1 / (near - far);
    return [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) * nf, -1, 0, 0, 2 * far * near * nf, 0];
  },
  mul(a, b) {
    const o = new Array(16).fill(0);
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) for (let k = 0; k < 4; k++) o[c * 4 + r] += a[k * 4 + r] * b[c * 4 + k];
    return o;
  },
  lookAt(eye, at, up) {
    const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
    const norm = (v) => { const l = Math.hypot(...v) || 1; return v.map((x) => x / l); };
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const z = norm(sub(eye, at)), x = norm(cross(up, z)), y = cross(z, x);
    return [x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -dot(x, eye), -dot(y, eye), -dot(z, eye), 1];
  },
};

export class Viewer {
  constructor(canvas) {
    this.canvas = canvas;
    const gl = (this.gl = canvas.getContext('webgl2', { antialias: true, preserveDrawingBuffer: true }));
    if (!gl) throw new Error('WebGL2 not available');
    this.pPoints = compile(gl, VS_POINTS, FS_POINTS);
    this.pMesh = compile(gl, VS_MESH, FS_MESH);
    this.pLine = compile(gl, VS_LINE, FS_LINE);
    this.layers = {};
    this.mode = 'mesh';
    this.showCams = true;
    this.textured = true;
    this.yaw = 0.6; this.pitch = 0.45; this.dist = 4; this.target = [0, 0, 0];
    this.pointSize = 3;
    this._bindInput();
    new ResizeObserver(() => this.draw()).observe(canvas);
  }

  _vao(attrs, indices) {
    const gl = this.gl;
    const vao = gl.createVertexArray();
    gl.bindVertexArray(vao);
    attrs.forEach(([loc, data, size, type, normalized]) => {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, b);
      gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, size, type, normalized, 0, 0);
    });
    if (indices) {
      const b = gl.createBuffer();
      gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, b);
      gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, indices, gl.STATIC_DRAW);
    }
    gl.bindVertexArray(null);
    return vao;
  }

  setPoints(name, pos, col) {
    const gl = this.gl;
    this.layers[name] = { kind: 'points', count: pos.length / 3, pos,
      vao: this._vao([[0, pos, 3, gl.FLOAT, false], [1, col, 3, gl.UNSIGNED_BYTE, true]]) };
    this.draw();
  }

  setMesh(pos, col, nrm, idx) {
    const gl = this.gl;
    this.layers.mesh = { kind: 'mesh', count: idx.length, pos,
      vao: this._vao([[0, pos, 3, gl.FLOAT, false], [1, col, 3, gl.UNSIGNED_BYTE, true], [2, nrm, 3, gl.FLOAT, false]], idx) };
    this.draw();
  }

  /** cams: per camera [index, cx, cy, cz, R (world->cam) row-major 9] */
  setCameras(cams, aspect, focalOverW) {
    const lines = [];
    const n = cams.length / 13;
    const s = 0.12;
    const hw = 0.5 / focalOverW, hh = hw / aspect;
    for (let i = 0; i < n; i++) {
      const c = cams.slice(13 * i + 1, 13 * i + 4), R = cams.slice(13 * i + 4, 13 * i + 13);
      // camera axes in world = rows of R
      const toW = (x, y, z) => [0, 1, 2].map((a) => c[a] + s * (R[a] * x + R[3 + a] * y + R[6 + a] * z));
      const corners = [toW(-hw, -hh, 1), toW(hw, -hh, 1), toW(hw, hh, 1), toW(-hw, hh, 1)];
      for (let k = 0; k < 4; k++) lines.push(...c, ...corners[k], ...corners[k], ...corners[(k + 1) % 4]);
    }
    const gl = this.gl;
    const pos = new Float32Array(lines);
    this.layers.cams = { kind: 'lines', count: pos.length / 3, vao: this._vao([[0, pos, 3, gl.FLOAT, false]]) };
    this.draw();
  }

  clear() { this.layers = {}; this.draw(); }

  fit(pos) {
    if (!pos || pos.length < 3) return;
    const n = pos.length / 3;
    const axes = [0, 1, 2].map((a) => {
      const v = new Float32Array(Math.min(n, 20000));
      const step = Math.max(1, Math.floor(n / v.length));
      for (let i = 0; i < v.length; i++) v[i] = pos[3 * Math.min(n - 1, i * step) + a];
      v.sort();
      return [v[Math.floor(v.length * 0.02)], v[Math.floor(v.length * 0.5)], v[Math.floor(v.length * 0.98)]];
    });
    this.target = axes.map((x) => x[1]);
    const ext = Math.max(...axes.map((x) => x[2] - x[0]));
    this.dist = Math.max(0.1, ext * 1.4);
    this.draw();
  }

  _bindInput() {
    const c = this.canvas;
    const pts = new Map();
    let lastPinch = null;
    c.addEventListener('pointerdown', (e) => { c.setPointerCapture(e.pointerId); pts.set(e.pointerId, [e.clientX, e.clientY]); });
    const up = (e) => { pts.delete(e.pointerId); lastPinch = null; };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('pointermove', (e) => {
      if (!pts.has(e.pointerId)) return;
      const prev = pts.get(e.pointerId);
      pts.set(e.pointerId, [e.clientX, e.clientY]);
      if (pts.size === 1 && !e.shiftKey && e.buttons !== 4 && e.buttons !== 2) {
        this.yaw -= (e.clientX - prev[0]) * 0.008;
        this.pitch = Math.max(-1.5, Math.min(1.5, this.pitch + (e.clientY - prev[1]) * 0.008));
      } else if (pts.size === 1) {
        this._pan(e.clientX - prev[0], e.clientY - prev[1]);
      } else if (pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        const mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
        if (lastPinch) {
          this.dist *= lastPinch.d / d;
          this._pan(mid[0] - lastPinch.mid[0], mid[1] - lastPinch.mid[1]);
        }
        lastPinch = { d, mid };
      }
      this.draw();
    });
    c.addEventListener('wheel', (e) => { e.preventDefault(); this.dist *= Math.exp(e.deltaY * 0.001); this.draw(); }, { passive: false });
    c.addEventListener('contextmenu', (e) => e.preventDefault());
    c.addEventListener('dblclick', () => this.fit(this._content()));
  }

  _pan(dx, dy) {
    const k = this.dist * 0.0015;
    const cy = Math.cos(this.yaw), sy = Math.sin(this.yaw), sp = Math.sin(this.pitch), cp = Math.cos(this.pitch);
    const right = [cy, 0, -sy];
    const upv = [-sy * sp, cp, -cy * sp];
    for (let a = 0; a < 3; a++) this.target[a] += (-dx * right[a] + dy * upv[a]) * k;
  }

  _content() {
    const l = this.layers[this.mode] || this.layers.dense || this.layers.sparse;
    return l && l.pos;
  }

  draw() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this._draw(); });
  }

  _draw() {
    const gl = this.gl, c = this.canvas;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
    gl.viewport(0, 0, w, h);
    const bg = getComputedStyle(c).getPropertyValue('--viewer-bg').trim() || '#15171c';
    const rgb = bg.match(/\w\w/g)?.map((x) => parseInt(x, 16) / 255) || [0.08, 0.09, 0.11];
    gl.clearColor(rgb[0], rgb[1], rgb[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST);
    const eye = [
      this.target[0] + this.dist * Math.cos(this.pitch) * Math.sin(this.yaw),
      this.target[1] + this.dist * Math.sin(this.pitch),
      this.target[2] + this.dist * Math.cos(this.pitch) * Math.cos(this.yaw),
    ];
    const mv = m4.lookAt(eye, this.target, [0, 1, 0]);
    const proj = m4.persp(0.9, w / h, this.dist * 0.01, this.dist * 100);
    const mvp = m4.mul(proj, mv);
    const L = this.layers[this.mode];
    if (L && L.kind === 'points') {
      gl.useProgram(this.pPoints);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.pPoints, 'uMVP'), false, mvp);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.pPoints, 'uMV'), false, mv);
      const base = this.mode === 'sparse' ? 2.2 : 1.0;
      gl.uniform1f(gl.getUniformLocation(this.pPoints, 'uSize'), this.pointSize * base * dpr * this.dist * 0.6);
      gl.bindVertexArray(L.vao);
      gl.drawArrays(gl.POINTS, 0, L.count);
    } else if (L && L.kind === 'mesh') {
      gl.useProgram(this.pMesh);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.pMesh, 'uMVP'), false, mvp);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.pMesh, 'uMV'), false, mv);
      gl.uniform1f(gl.getUniformLocation(this.pMesh, 'uShade'), this.textured ? 0.35 : 1.0);
      gl.uniform1f(gl.getUniformLocation(this.pMesh, 'uTex'), this.textured ? 1.0 : 0.0);
      gl.bindVertexArray(L.vao);
      gl.drawElements(gl.TRIANGLES, L.count, gl.UNSIGNED_INT, 0);
    }
    if (this.showCams && this.layers.cams) {
      gl.useProgram(this.pLine);
      gl.uniformMatrix4fv(gl.getUniformLocation(this.pLine, 'uMVP'), false, mvp);
      gl.uniform3f(gl.getUniformLocation(this.pLine, 'uColor'), 0.98, 0.75, 0.2);
      gl.bindVertexArray(this.layers.cams.vao);
      gl.drawArrays(gl.LINES, 0, this.layers.cams.count);
    }
    gl.bindVertexArray(null);
  }
}
