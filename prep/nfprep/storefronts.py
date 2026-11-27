"""Storefront photos (KartaView) and Google ratings and reviews for the business card in the map game (web/index.html).

Pilot area: Lundy's Lane between Montrose Rd and Kalar Rd (Westlane). Owner's decision: the game stays for
personal use and the repository private while it holds this third-party data, as for the TomTom speeds and
data/google_places.json. The project never offers a map, directory or navigation built on it.

Photos: KartaView street imagery (CC BY-SA 4.0; the card credits "KartaView contributors" with the photo id
and date). The photo search returns only a sample of each drive, so every drive it names is listed in full
(cached in build/kartaview/). A photo shows a business when the business is within 30 degrees of the camera's
heading (in frame whatever the lens) and 12-100 m away; preferred: 20-45 m, seen at an angle (about 18 degrees
off centre), outside the snow months (Dec-Mar), good GPS. The photo is cropped towards the business, assuming
an 80 degree field of view (60 for portrait photos), and shrunk to 800 px wide. Photos are checked by eye:
data/storefront_photo_overrides.json names the photo to use for a business ({"photo": id, "sequence": id,
"crop": [x0, y0, x1, y1] as fractions of the photo, set by eye}) or none ({"none": reason}). The owner's own photos (data/photos/, the project's own) come first where they show
the business: {"owner_photo": "2026-11/upload_084.jpg", "date": "2026-11", "crop": [...]}, credited "Photo: project owner".

Google: Place Details (New) with rating, review count and up to five reviews, for businesses matched to a
Google place (data/google_places.json by OSM id or name and address, else the `match` step's Text Search here,
accepted only with the same street number, a matching name and within 150 m). Reviews are the Enterprise +
Atmosphere kind (1,000 free a month): the run stops at 900, counted in build/google_places_usage.json under
"atmosphere"; Text Search counts under "pro" like nfprep.google_places. Google's terms limit storing Places
content: refresh with `reviews --refresh` rather than keeping it for long. The card shows each review's
author and "Google" as the source, as Google requires.

The card can also show the rating and review count already stored city-wide in data/google_places.json; the
export carries those for every matched business, and reviews only where fetched.

Usage (from prep/):
  python3 -m nfprep.storefronts photos [--redo]   # choose, download and crop KartaView photos
  python3 -m nfprep.storefronts match             # Google Text Search for pilot businesses not yet matched
  python3 -m nfprep.storefronts reviews [--refresh]
  python3 -m nfprep.storefronts export            # write web/storefronts.json for the game
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AREA = {"name": "Lundy's Lane, Montrose Rd to Kalar Rd", "lat": (43.0870, 43.0908), "lon": (-79.1372, -79.1215)}
PHOTOS_OUT = ROOT / "data" / "storefront_photos.json"           # chosen KartaView photo per business
OVERRIDES = ROOT / "data" / "storefront_photo_overrides.json"   # photo choices made by eye
MATCHES_OUT = ROOT / "data" / "storefront_google_matches.json"  # Text Search matches for pilot businesses
REVIEWS_OUT = ROOT / "data" / "google_reviews.json"             # Google Place Details per business
IMG_DIR = ROOT / "web" / "storefronts"
WEB_OUT = ROOT / "web" / "storefronts.json"
SEQ_CACHE = ROOT / "build" / "kartaview"
USAGE = ROOT / "build" / "google_places_usage.json"
KV = "https://api.kartaview.org/2.0"
UA = {"User-Agent": "nfsim-prep/0.1"}                            # generic client name, no personal details
ATMOSPHERE_FREE = 900                                            # Google: 1,000 free a month; kept short
MATCH_M = 150


def get_json(url: str, tries: int = 3):
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.loads(r.read())
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(3 * (a + 1))


def get_bytes(url: str, tries: int = 2) -> bytes:
    """About 1 in 10 KartaView downloads fail with HTTP 500: one retry, then the caller moves on."""
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
                return r.read()
        except Exception:
            if a == tries - 1:
                raise
            time.sleep(2)


def business_key(b: dict) -> str:
    """The key the game uses: OSM id, else name and address."""
    return b["osm_id"] or f"{b['name']}|{b['address']}"


def city_businesses(area: bool = True) -> list[dict]:
    c = json.loads((ROOT / "web" / "city.json").read_text())
    b, cats = c["businesses"], c["sim_categories"]
    out = []
    for i in range(len(b["lat"])):
        lat, lon = b["lat"][i], b["lon"][i]
        if area and not (AREA["lat"][0] <= lat <= AREA["lat"][1] and AREA["lon"][0] <= lon <= AREA["lon"][1]):
            continue
        if (b.get("standin") and b["standin"][i]) or b["external"][i] or not b["name"][i]:
            continue
        out.append({"i": i, "name": b["name"][i], "address": (b.get("address") or [""] * len(b["lat"]))[i] or "",
                    "osm_id": b["osm_id"][i] or "", "lat": lat, "lon": lon, "kind": cats[b["sim_category"][i]]})
    return out


def area_businesses() -> list[dict]:
    return city_businesses(area=True)


def dist_bearing(lat1, lon1, lat2, lon2):
    """Metres and compass bearing (degrees) from point 1 to point 2."""
    dy = (lat2 - lat1) * 110_540.0
    dx = (lon2 - lon1) * 111_320.0 * math.cos(math.radians(lat1))
    return math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)) % 360


# ---------------------------------------------------------------- KartaView photos

def sequence_photos(sid: str) -> list:
    """Every photo of one drive, cached on disk (some drives have 2,600+ photos)."""
    SEQ_CACHE.mkdir(parents=True, exist_ok=True)
    f = SEQ_CACHE / f"seq_{sid}.json"
    if f.exists():
        return json.loads(f.read_text())
    allp, page = [], 1
    while True:
        d = get_json(f"{KV}/sequence/{sid}/photos?itemsPerPage=200&page={page}")["result"].get("data", [])
        allp += d
        if len(d) < 200:
            break
        page += 1
    f.write_text(json.dumps(allp))
    return allp


def candidate_photos(lat: float, lon: float) -> list[dict]:
    """KartaView photos within about 110 m of a point, from the full listing of every drive the search names."""
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


def view(b: dict, p: dict):
    """(metres, degrees off the view centre: business left - / right +) from a photo to the business."""
    d, brg = dist_bearing(float(p["lat"]), float(p["lng"]), b["lat"], b["lon"])
    return d, (brg - float(p["heading"]) + 540) % 360 - 180


def score(b: dict, p: dict):
    """How well a photo shows the business (lower is better), or None if it does not show it."""
    try:
        head = float(p.get("heading"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(head) or (p.get("projection") or "PLANE") != "PLANE":
        return None                                   # 360 / sphere photos are left out
    d, off = view(b, p)
    # in frame: within 30 degrees of the heading, or 35 close up (an 80 degree lens still holds it)
    if not 12 <= d <= 100 or abs(off) > (35 if d <= 40 else 30):
        return None
    month = int((p.get("shotDate") or "2000-06")[5:7])
    snow = month in (12, 1, 2, 3)
    # close views matter most: a storefront 70 m ahead is a few dozen pixels wide in these forward-facing photos
    s = abs(d - 30) / 12 + abs(abs(off) - 20) / 15 + (0.8 if snow else 0) + float(p.get("gpsAccuracy") or 10) / 20
    return s, d, off


def crop(img_bytes: bytes, off_deg: float, portrait: bool, box: list | None = None) -> bytes:
    """Crop towards the business: by its bearing, or to a box chosen by eye ([x0, y0, x1, y1], fractions)."""
    from PIL import Image
    im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    w, h = im.size
    if box:
        im = im.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))
        im.thumbnail((800, 800))
        out = io.BytesIO()
        im.save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()
    hfov = 60.0 if portrait else 80.0                 # unknown lens: a typical phone or dashcam field of view
    x = w / 2 + (w / 2) * math.tan(math.radians(off_deg)) / math.tan(math.radians(hfov / 2))
    # a narrow window round the business, and the band round the horizon: no sky, no bonnet or dashboard
    cw = w * (0.45 if portrait else 0.34)
    x0 = max(0.0, min(w - cw, x - cw / 2))
    top, bottom = (0.28, 0.70) if portrait else (0.22, 0.78)
    im = im.crop((int(x0), int(h * top), int(x0 + cw), int(h * bottom)))
    im.thumbnail((800, 800))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=80, optimize=True)
    return out.getvalue()


def original(p: dict) -> bytes:
    """The full-size photo, kept in build/kartaview/img/ so a new crop needs no new download."""
    f = SEQ_CACHE / "img" / f"{p['id']}.jpg"
    if f.exists():
        return f.read_bytes()
    data = get_bytes(p["imageProcUrl"])
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    return data


def _save_photo(b: dict, k: str, p: dict, d: float, off: float, how: str, box: list | None = None) -> dict:
    data = original(p)
    fn = hashlib.sha1(k.encode()).hexdigest()[:12] + ".jpg"
    portrait = int(p.get("height") or 0) > int(p.get("width") or 1)
    (IMG_DIR / fn).write_bytes(crop(data, off, portrait, box))
    return {"file": f"storefronts/{fn}", "kartaview_photo": str(p["id"]), "sequence": str(p["sequenceId"]),
            "date": (p.get("shotDate") or "")[:10], "distance_m": round(d), "off_axis_deg": round(off, 1), "chosen": how}


def photos(redo: bool = False) -> None:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    done = json.loads(PHOTOS_OUT.read_text()) if PHOTOS_OUT.exists() and not redo else {}
    over = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    bs = area_businesses()
    for n, b in enumerate(bs):
        k = business_key(b)
        o = over.get(k)
        if o and "none" in o:
            done[k] = {"none": o["none"], "chosen": "by eye"}
            continue
        if o and "owner_photo" in o:
            if done.get(k, {}).get("owner_photo") == o["owner_photo"] and done[k].get("crop") == o.get("crop"):
                continue
            src = ROOT / "data" / "photos" / o["owner_photo"]
            fn = hashlib.sha1(k.encode()).hexdigest()[:12] + ".jpg"
            (IMG_DIR / fn).write_bytes(crop(src.read_bytes(), 0.0, False, o.get("crop") or [0, 0, 1, 1]))
            done[k] = {"file": f"storefronts/{fn}", "owner_photo": o["owner_photo"], "date": o.get("date", ""),
                       "credit": "Photo: project owner", "chosen": "by eye", **({"crop": o["crop"]} if o.get("crop") else {})}
            print(f"[{n + 1}/{len(bs)}] {b['name']}: the owner's photo {o['owner_photo']}")
            continue
        if o and "photo" in o:
            if done.get(k, {}).get("kartaview_photo") == str(o["photo"]) and done[k].get("crop") == o.get("crop"):
                continue
            p = next((q for q in sequence_photos(str(o["sequence"])) if str(q["id"]) == str(o["photo"])), None)
            if p:
                d, off = view(b, p)
                done[k] = _save_photo(b, k, p, d, off, "by eye", o.get("crop"))
                if o.get("crop"):
                    done[k]["crop"] = o["crop"]
                print(f"[{n + 1}/{len(bs)}] {b['name']}: photo {o['photo']} (by eye)")
                continue
        if k in done:
            continue
        try:
            cands = candidate_photos(b["lat"], b["lon"])
        except Exception as e:
            print(f"{b['name']}: KartaView search failed ({e})")
            continue
        scored = sorted(((s, p) for p in cands if (s := score(b, p)) is not None), key=lambda t: t[0][0])
        entry = None
        for (s, d, off), p in scored[:6]:              # the first of the best that downloads
            try:
                entry = _save_photo(b, k, p, d, off, "scored")
                break
            except Exception:
                continue
        done[k] = entry or {"none": f"no KartaView photo within 100 m looks towards it ({len(cands)} photos nearby, {len(scored)} facing it)"}
        print(f"[{n + 1}/{len(bs)}] {b['name']}: " + (f"photo {entry['kartaview_photo']} ({entry['date']}, {entry['distance_m']} m, {entry['off_axis_deg']} deg)" if entry else done[k]["none"]), flush=True)
        PHOTOS_OUT.write_text(json.dumps(done, indent=1))
    PHOTOS_OUT.write_text(json.dumps(done, indent=1))
    have = sum("file" in v for v in done.values())
    print(f"{have} of {len(bs)} businesses have a photo")


# ---------------------------------------------------------------- Google

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def google_index(area: bool = True) -> dict:
    """Business key -> Google place match (id, rating, count), from data/google_places.json (by OSM id, else
    name and address) and the Text Search matches made here."""
    g = json.loads((ROOT / "data" / "google_places.json").read_text())
    by_name = {}
    for v in g.values():
        if v.get("match"):
            v["match"]["_checked"] = v.get("checked")
            by_name[(_norm(v.get("name")), _norm(v.get("address")))] = v["match"]
    extra = json.loads(MATCHES_OUT.read_text()) if MATCHES_OUT.exists() else {}
    out = {}
    for b in city_businesses(area):
        k = business_key(b)
        e = g.get(b["osm_id"]) if b["osm_id"] else None
        m = (e or {}).get("match")
        if m:
            m["_checked"] = e.get("checked")
        m = m or by_name.get((_norm(b["name"]), _norm(b["address"])))
        if not m and (extra.get(k) or {}).get("id"):
            m = extra[k]
        if m:
            out[k] = {"id": m["id"], "rating": m.get("rating"), "count": m.get("userRatingCount"), "status": m.get("businessStatus"),
                      "checked": m.get("_checked") or m.get("checked")}
    return out


def _street_number(addr: str) -> set[str]:
    """'8201-8209 Lundy's Ln' -> {'8201', '8209'}."""
    m = re.match(r"\s*(\d+)(?:\s*-\s*(\d+))?", addr or "")
    return {x for x in (m.groups() if m else ()) if x}


def match() -> None:
    """Text Search for pilot businesses that data/google_places.json has no match for."""
    from .backtest import same_business
    from .google_places import FREE, month, search, usage
    u = usage()
    done = json.loads(MATCHES_OUT.read_text()) if MATCHES_OUT.exists() else {}
    idx = google_index()
    for b in area_businesses():
        k = business_key(b)
        if k in idx or k in done:
            continue
        if u[month()]["pro"] >= FREE["pro"]:
            print("Stopping: this month's free Text Search allowance is used up."); break
        places = search(f"{b['name']}, {b['address']}, Niagara Falls, ON", b["lat"], b["lon"], ratings=False)
        u[month()]["pro"] += 1
        nums = _street_number(b["address"])
        best = None
        for p in places:
            loc = p.get("location") or {}
            d = dist_bearing(b["lat"], b["lon"], loc.get("latitude", 0), loc.get("longitude", 0))[0]
            name = (p.get("displayName") or {}).get("text", "")
            same_num = bool(nums & _street_number(p.get("formattedAddress", "")))
            if p.get("businessStatus") and d <= MATCH_M and same_num and same_business(b["name"], name):
                if best is None or d < best[0]:
                    best = (d, p)
        if best:
            p = best[1]
            done[k] = {"id": p["id"], "google_name": (p.get("displayName") or {}).get("text"), "address": p.get("formattedAddress"),
                       "status": p.get("businessStatus"), "distance_m": round(best[0]), "checked": time.strftime("%Y-%m-%d")}
        else:
            done[k] = {"id": None, "candidates": [(p.get("displayName") or {}).get("text") for p in places][:5], "checked": time.strftime("%Y-%m-%d")}
        print(f"{b['name']} ({b['address']}): " + (f"-> {done[k]['google_name']}, {done[k]['address']}" if best else f"no match among {done[k]['candidates']}"), flush=True)
        MATCHES_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
        USAGE.write_text(json.dumps(u, indent=1))
    MATCHES_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
    USAGE.write_text(json.dumps(u, indent=1))


def reviews(refresh: bool = False) -> None:
    u = json.loads(USAGE.read_text()) if USAGE.exists() else {}
    m = u.setdefault(time.strftime("%Y-%m"), {})
    m.setdefault("atmosphere", 0)
    done = json.loads(REVIEWS_OUT.read_text()) if REVIEWS_OUT.exists() else {}
    for k, g in google_index().items():
        pid = g["id"]
        if not refresh and k in done and done[k].get("place_id") == pid:
            continue
        if m["atmosphere"] >= ATMOSPHERE_FREE:
            print("Stopping: this month's free Place Details (reviews) allowance is used up."); break
        req = urllib.request.Request(
            f"https://places.googleapis.com/v1/places/{pid}", method="GET",
            headers={"x-goog-api-key": "proxy-injected",
                     "X-Goog-FieldMask": "id,displayName,rating,userRatingCount,reviews,businessStatus"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            print(f"{k}: Google error {e.code}")
            continue
        finally:
            m["atmosphere"] += 1                      # a call is billed whether or not it succeeds
            USAGE.write_text(json.dumps(u, indent=1))
        done[k] = {
            "place_id": pid, "name": (d.get("displayName") or {}).get("text"), "rating": d.get("rating"),
            "count": d.get("userRatingCount"), "status": d.get("businessStatus"), "checked": time.strftime("%Y-%m-%d"),
            "reviews": [{"author": (r.get("authorAttribution") or {}).get("displayName"),
                         "author_url": (r.get("authorAttribution") or {}).get("uri"), "rating": r.get("rating"),
                         "when": r.get("relativePublishTimeDescription"), "date": (r.get("publishTime") or "")[:10],
                         "text": ((r.get("originalText") or r.get("text") or {}).get("text") or "")}
                        for r in d.get("reviews", [])],
        }
        print(f"{done[k]['name']}: {done[k]['rating']} ({done[k]['count']}), {len(done[k]['reviews'])} reviews", flush=True)
        REVIEWS_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
    REVIEWS_OUT.write_text(json.dumps(done, indent=1, ensure_ascii=False))
    print(f"Place Details this month: {m['atmosphere']}")


# ---------------------------------------------------------------- the game's file

def export() -> None:
    ph = json.loads(PHOTOS_OUT.read_text()) if PHOTOS_OUT.exists() else {}
    rv = json.loads(REVIEWS_OUT.read_text()) if REVIEWS_OUT.exists() else {}
    stored = google_index(area=False)                 # ratings already stored city-wide (no extra calls)
    out = {"_meta": {"area": AREA["name"],
                     "photo_credit": "Street photo: KartaView contributors, CC BY-SA 4.0",
                     "reviews_credit": "Ratings and reviews: Google",
                     "note": "For personal use; third-party data (see prep/nfprep/storefronts.py)."}, "places": {}}
    for k in set(ph) | set(rv) | set(stored):
        e = {}
        if ph.get(k) and "file" in ph[k]:
            e["photo"] = {x: ph[k][x] for x in ("file", "kartaview_photo", "date", "credit") if ph[k].get(x)}
        if rv.get(k):
            e["google"] = {x: rv[k][x] for x in ("rating", "count", "status", "checked", "reviews")}
        elif stored.get(k) and stored[k].get("rating"):
            e["google"] = {x: stored[k][x] for x in ("rating", "count", "status", "checked") if stored[k].get(x)}
        if e:
            out["places"][k] = e
    WEB_OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    P = out["places"].values()
    print(f"{len(out['places'])} businesses in {WEB_OUT.relative_to(ROOT)}: {sum('photo' in e for e in P)} with a photo, "
          f"{sum('google' in e for e in P)} with a Google rating, {sum(bool((e.get('google') or {}).get('reviews')) for e in P)} with reviews")


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else "export"
    flags = set(sys.argv[2:])
    {"photos": lambda: photos("--redo" in flags), "match": match,
     "reviews": lambda: reviews("--refresh" in flags), "export": export}[step]()
