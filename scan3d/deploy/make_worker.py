"""Pack scan3d/web into one Cloudflare Worker script (files embedded as base64).

Why not Workers static assets: their upload step authenticates with its own short-lived key in the
Authorization header, which the cloud session's network proxy replaces with the account token.
Embedding the files avoids that step; the app is small (about 250 KB gzipped).

    python3 make_worker.py      # writes build/index.js and build/wrangler.toml
    cd build && npx wrangler@3.99.0 deploy
"""
import base64
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent / "web"
OUT = HERE / "build"
TYPES = {"html": "text/html; charset=utf-8", "js": "text/javascript; charset=utf-8", "wasm": "application/wasm",
         "json": "application/json", "css": "text/css"}

files = {}
for p in sorted(WEB.rglob("*")):
    if p.is_file():
        rel = "/" + p.relative_to(WEB).as_posix()
        files[rel] = [base64.b64encode(p.read_bytes()).decode(), TYPES.get(p.suffix[1:], "application/octet-stream")]

src = "const FILES = " + json.dumps(files) + """;
const CACHE = {};
function body(path) {
  if (!CACHE[path]) {
    const s = atob(FILES[path][0]);
    const b = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) b[i] = s.charCodeAt(i);
    CACHE[path] = b;
  }
  return CACHE[path];
}
export default {
  async fetch(request) {
    let path = new URL(request.url).pathname;
    if (path.endsWith("/")) path += "index.html";
    if (!FILES[path]) return new Response("Not found", { status: 404 });
    return new Response(body(path), { headers: {
      "content-type": FILES[path][1],
      "cache-control": "no-cache",
      "x-content-type-options": "nosniff",
      "permissions-policy": "camera=(self)",
    } });
  },
};
"""
OUT.mkdir(exist_ok=True)
(OUT / "index.js").write_text(src)
(OUT / "wrangler.toml").write_text('name = "scan3d"\nmain = "index.js"\ncompatibility_date = "2024-11-01"\nworkers_dev = true\n')
print(f"build/index.js: {len(src) // 1024} KB, {len(files)} files")
