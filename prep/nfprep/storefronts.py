"""Storefront photos (KartaView) and Google ratings and reviews for the business card in the game.

Pilot area: Lundy's Lane between Montrose Rd and Kalar Rd (Westlane). Owner's decision: the game stays
for personal use and the repository private while it holds this third-party data, as for the TomTom
speeds and data/google_places.json. The project never offers a map, directory or navigation built on it.

Photos: KartaView street imagery (CC BY-SA 4.0, credited with the photo id and date on the card). For
each business the script looks at the photos taken within about 100 m and keeps the one where the
business sits within 30 degrees of the camera's heading (so it is in frame whatever the lens),
preferring a 20-45 m view at an angle and photos outside the snow months. The photo is cropped towards
the business and shrunk to 800 px wide.

Reviews: Google Places "Place Details" with rating, review count and up to five reviews, for businesses
already matched in data/google_places.json. Reviews are the Enterprise + Atmosphere kind (1,000 free a
month); the run stays below 900, counted in build/google_places_usage.json like the main Google script.
The card shows each review's author and "Google" as the source, as Google requires.

Usage (from prep/):
  python3 -m nfprep.storefronts photos    # choose, download and crop KartaView photos
  python3 -m nfprep.storefronts reviews   # fetch Google ratings and reviews
  python3 -m nfprep.storefronts export    # write web/storefronts.json for the game
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AREA = {"name": "Lundy's Lane, Montrose Rd to Kalar Rd", "lat": (43.0870, 43.0908), "lon": (-79.1372, -79.1215)}
PHOTOS_OUT = ROOT / "data" / "storefront_photos.json"     # chosen KartaView photo per business
REVIEWS_OUT = ROOT / "data" / "google_reviews.json"        # Google Place Details per business
IMG_DIR = ROOT / "web" / "storefronts"
WEB_OUT = ROOT / "web" / "storefronts.json"
USAGE = ROOT / "build" / "google_places_usage.json"
KV = "https://api.kartaview.org/2.0"
UA = {"User-Agent": "nfsim-prep/0.1"}
ATMOSPHERE_FREE = 900                                       # Google: 1,000 free a month; kept short


def get_json(url: str, tries: int = 3):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))


def get_bytes(url: str, tries: int = 3) -> bytes:
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))


def business_key(b: dict) -> str:
    """The key the game uses: OSM id, else name and address."""
    return b["osm_id"] or f"{b['name']}|{b['address']}"


def area_businesses() -> list[dict]:
    c = json.loads((ROOT / "web" / "city.json").read_text())
    b, cats = c["businesses"], c["sim_categories"]
    out = []
    for i in range(len(b["lat"])):
        lat, lon = b["lat"][i], b["lon"][i]
        if not (AREA["lat"][0] <= lat <= AREA["lat"][1] and AREA["lon"][0] <= lon <= AREA["lon"][1]):
            continue
        if (b.get("standin") and b["standin"][i]) or b["external"][i] or not b["name"][i]:
            continue
        out.append({"name": b["name"][i], "address": (b.get("address") or [""] * len(b["lat"]))[i] or "",
                    "osm_id": b["osm_id"][i] or "", "lat": lat, "lon": lon, "kind": cats[b["sim_category"][i]]})
    return out


def dist_bearing(lat1, lon1, lat2, lon2):
    """Metres and compass bearing (degrees) from point 1 to point 2."""
    dy = (lat2 - lat1) * 110_540.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)) % 360


def candidate_photos(lat: float, lon: float) -> list[dict]:
    """KartaView photos near a point. The photo search returns a sample per sequence, so the
    sequences it names are then listed in full and filtered to the neighbourhood."""
    seen, seqs = {}, set()
    q = urllib.parse.urlencode({"lat": lat, "lng": lon, "radius": 100, "itemsPerPage": 100})
    for p in get_json(f"{KV}/photo/?{q}")["result"].get("data", []):
        seen[p["id"]] = p
        seqs.add(p["sequenceId"])
    for sid in seqs:
        for p in sequence_photos(sid):
            if dist_bearing(lat, lon, float(p["lat"]), float(p["lng"]))[0] < 110:
                seen[p["id"]] = p
    return list(seen.values())


_SEQ: dict[str, list] = {}


def sequence_photos(sid: str) -> list:
    if sid not in _SEQ:
        allp, page = [], 1
        while True:
            d = get_json(f"{KV}/sequence/{sid}/photos?itemsPerPage=200&page={page}")["result"].get("data", [])
            allp += d
            if len(d) < 200:
                break
            page += 1
        _SEQ[sid] = allp
    return _SEQ[sid]


def score(b: dict, p: dict):
    """How well a photo shows the business (lower is better), or None if it does not show it."""
    d, brg = dist_bearing(float(p["lat"]), float(p["lng"]), b["lat"], b["lon"])
    head = float(p.get("heading") or "nan")
    if not math.isfinite(head) or p.get("projection") not in ("PLANE", None, ""):
        return None
    off = (brg - head + 540) % 360 - 180            # business left (-) / right (+) of the view centre
    if abs(off) > 30 or not 12 <= d <= 100:
        return None
    month = int((p.get("shotDate") or "2000-06")[5:7])
    snow = month in (12, 1, 2, 3)
    s = abs(d - 32) / 20 + abs(abs(off) - 18) / 15 + (0.8 if snow else 0) + float(p.get("gpsAccuracy") or 10) / 20
    return s, d, off


def crop(img_bytes: bytes, off_deg: float, portrait: bool):
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    hfov = 60.0 if portrait else 80.0               # unknown lens: a typical phone or dashcam field of view
    x = w / 2 + (w / 2) * math.tan(math.radians(off_deg)) / math.tan(math.radians(hfov / 2))
    cw = w * (0.75 if portrait else 0.55)
    x0 = max(0.0, min(w - cw, x - cw / 2))
    im = im.crop((int(x0), int(h * 0.12), int(x0 + cw), int(h * 0.92)))
    im.thumbnail((800, 800))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


def photos() -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    done = json.loads(PHOTOS_OUT.read_text()) if PHOTOS_OUT.exists() else {}
    bs = area_businesses()
    for n, b in enumerate(bs):
        k = business_key(b)
        if k in done:
            continue
        try:
            cands = candidate_photos(b["lat"], b["lon"])
        except Exception as e:
            print(f"{b['name']}: KartaView search failed ({e})"); continue
        scored = sorted((s, p) for p in cands if (s := score(b, p)) is not None)
        entry = None
        for (s, d, off), p in scored[:4]:           # first one that downloads
            try:
                data = get_bytes(p["imageProcUrl"])
            except Exception:
                continue
            fn = hashlib.sha1(k.encode()).hexdigest()[:12] + ".jpg"
            portrait = int(p.get("height") or 0) > int(p.get("width") or 1)
            (IMG_DIR / fn).write_bytes(crop(data, off, portrait))
            entry = {"file": f"storefronts/{fn}", "kartaview_photo": p["id"], "sequence": p["sequenceId"],
                     "date": (p.get("shotDate") or "")[:10], "distance_m": round(d), "off_axis_deg": round(off, 1)}
            break
        done[k] = entry or {"none": f"no KartaView photo within 100 m looks towards it ({len(cands)} photos nearby)"}
        print(f"[{n + 1}/{len(bs)}] {b['name']}: " + (f"photo {entry['kartaview_photo']} ({entry['date']}, {entry['distance_m']} m)" if entry else done[k]["none"]))
        PHOTOS_OUT.write_text(json.dumps(done, indent=1))
    PHOTOS_OUT.write_text(json.dumps(done, indent=1))


def google_index() -> dict:
    """Business key -> Google place id, from data/google_places.json (matched by OSM id or name and address)."""
    g = json.loads((ROOT / "data" / "google_places.json").read_text())
    by_name = {}
    for k, v in g.items():
        if v.get("match"):
            by_name[(v.get("name", "").lower(), v.get("address", "").lower())] = v["match"]["id"]
    out = {}
    for b in area_businesses():
        k = business_key(b)
        m = (g.get(b["osm_id"]) or {}).get("match") if b["osm_id"] else None
        pid = m["id"] if m else by_name.get((b["name"].lower(), b["address"].lower()))
        if pid:
            out[k] = pid
    return out


def reviews() -> None:
    u = json.loads(USAGE.read_text()) if USAGE.exists() else {}
    m = u.setdefault(time.strftime("%Y-%m"), {}).setdefault("atmosphere", 0)
    done = json.loads(REVIEWS_OUT.read_text()) if REVIEWS_OUT.exists() else {}
    idx = google_index()
    for k, pid in idx.items():
        if k in done and done[k].get("place_id") == pid:
            continue
        if u[time.strftime("%Y-%m")]["atmosphere"] >= ATMOSPHERE_FREE:
            print("Stopping: this month's free Place Details (reviews) allowance is used up."); break
        req = urllib.request.Request(
            f"https://places.googleapis.com/v1/places/{pid}", method="GET",
            headers={"x-goog-api-key": "proxy-injected",
                     "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews,businessStatus"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"{k}: Google error {e.code}"); continue
        u[time.strftime("%Y-%m")]["atmosphere"] += 1
        done[k] = {
            "place_id": pid, "name": (d.get("displayName") or {}).get("text"), "rating": d.get("rating"),
            "count": d.get("userRatingCount"), "status": d.get("businessStatus"), "checked": time.strftime("%Y-%m-%d"),
            "reviews": [{"author": (r.get("authorAttribution") or {}).get("displayName"), "rating": r.get("rating"),
                         "when": r.get("relativePublishTimeDescription"), "date": (r.get("publishTime") or "")[:10],
                         "text": ((r.get("originalText") or r.get("text") or {}).get("text") or "")}
                        for r in d.get("reviews", [])],
        }
        print(f"{done[k]['name']}: {done[k]['rating']} ({done[k]['count']}), {len(done[k]['reviews'])} reviews")
        REVIEWS_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
        USAGE.parent.mkdir(exist_ok=True); USAGE.write_text(json.dumps(u, indent=1))
    REVIEWS_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))


def export() -> None:
    ph = json.loads(PHOTOS_OUT.read_text()) if PHOTOS_OUT.exists() else {}
    rv = json.loads(REVIEWS_OUT.read_text()) if REVIEWS_OUT.exists() else {}
    out = {"_meta": {"area": AREA["name"],
                     "photo_credit": "Street photo: KartaView contributors, CC BY-SA 4.0",
                     "reviews_credit": "Ratings and reviews: Google"}, "places": {}}
    for k in set(ph) | set(rv):
        e = {}
        if ph.get(k) and "file" in ph[k]:
            e["photo"] = ph[k]
        if rv.get(k):
            e["google"] = {x: rv[k][x] for x in ("rating", "count", "status", "checked", "reviews")}
        if e:
            out["places"][k] = e
    WEB_OUT.write_text(json.dumps(out, ensure_ascii=False))
    print(f"{len(out['places'])} businesses in {WEB_OUT.relative_to(ROOT)}: "
          f"{sum('photo' in e for e in out['places'].values())} with a photo, "
          f"{sum('google' in e for e in out['places'].values())} with Google ratings")


if __name__ == "__main__":
    {"photos": photos, "reviews": reviews, "export": export}[sys.argv[1] if len(sys.argv) > 1 else "export"]()
