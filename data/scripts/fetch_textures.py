"""Download the surface textures the 3D city uses from Poly Haven (CC0, public domain: free to use and share)
into web/world/tex/pbr/: colour at 1024 px, surface normal and ambient-occlusion/roughness/metal at 512 px.

Run: python3 data/scripts/fetch_textures.py
"""
import io, json, time, urllib.request
from pathlib import Path
from PIL import Image

OUT = Path(__file__).resolve().parents[2] / "web" / "world" / "tex" / "pbr"
SETS = {   # our name: Poly Haven asset
    "brick": "red_brick_03", "stucco": "white_stucco", "block": "concrete_block_wall", "siding": "weathered_plank_siding",
    "asphalt": "asphalt_02", "sidewalk": "concrete_pavement", "grass": "leafy_grass", "rock": "cliff_side",
    "shingles": "grey_roof_01", "metal": "box_profile_metal_sheet", "stone": "old_stone_wall", "gravelroof": "gravel_concrete",
}
MAPS = {"diff": ("Diffuse", 1024), "nor_gl": ("nor_gl", 512), "arm": ("arm", 512)}


def get(url):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "niagara-sim-research"})
            return urllib.request.urlopen(req, timeout=120).read()
        except OSError:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    credits = {}
    for name, asset in SETS.items():
        files = json.loads(get(f"https://api.polyhaven.com/files/{asset}"))
        for key, (ph_key, size) in MAPS.items():
            entry = files.get(ph_key) or files.get(key) or (files.get("me_arm") if key == "arm" else None)
            src = entry["1k"]["jpg"]["url"]
            im = Image.open(io.BytesIO(get(src))).convert("RGB")
            if im.width != size:
                im = im.resize((size, size), Image.Resampling.LANCZOS)
            im.save(OUT / f"{name}_{key}.jpg", "JPEG", quality=82, optimize=True)
        credits[name] = {"asset": asset, "url": f"https://polyhaven.com/a/{asset}", "licence": "CC0"}
        print(name, asset)
    (OUT / "credits.json").write_text(json.dumps({"source": "Poly Haven (polyhaven.com)", "licence": "CC0 1.0", "textures": credits}, indent=1))
