"""Where does each high school's lunch money go in the model, and does it match what people see?

For each high school, its students' daytime group is taken out and the year rerun; the difference in
each business's sales is the money those students spend there. The places that get the most are
compared with what the owner has seen at lunch (data/lunch_observations.json).
Usage: python3 -m nfprep.lunch_check
"""
import csv, json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NFSIM = str(ROOT / "engine/target/release/nfsim")
CITY = ROOT / "build/city_2025/city.json"


def run(city: dict, beta: float) -> dict:
    with tempfile.TemporaryDirectory() as d:
        cp, bp = Path(d) / "c.json", Path(d) / "b.csv"
        cp.write_text(json.dumps(city, separators=(",", ":")))
        subprocess.run([NFSIM, "run", str(cp), "--mode", "flow", "--days", "365", "--beta", str(beta), "--closures-as-of", "209912",
                        "--businesses", str(bp)], check=True, capture_output=True)
        return {int(r["business"]): float(r["revenue"]) for r in csv.DictReader(open(bp))}


def main() -> dict:
    city = json.loads(CITY.read_text())
    cal = json.loads((ROOT / "data/calibrated_multipliers.json").read_text()); beta = cal.get("beta_fit", cal["beta"])
    obs = json.loads((ROOT / "data/lunch_observations.json").read_text())["observations"]
    h, b = city["households"], city["businesses"]
    n_cat = len(city["categories"]["name"])
    base = run(city, beta)
    out = {}
    for school in sorted({o["school"] for o in obs}):
        g = h["daytime_from"] + h["daytime_label"].index(school)
        c = json.loads(json.dumps(city))
        c["households"]["budget"][g * n_cat:(g + 1) * n_cat] = [0.0] * n_cat
        rev = run(c, beta)
        gets = sorted(((base[i] - rev.get(i, 0.0), i) for i in base if base[i] - rev.get(i, 0.0) > 1), reverse=True)
        total = sum(v for v, _ in gets)
        top = [{"name": b["name"][i], "address": b["address"][i], "money": round(v), "share_of_school": round(v / total, 3),
                "share_of_its_sales": round(v / base[i], 3)} for v, i in gets[:10]]
        seen = []
        for o in obs:
            if o["school"] != school:
                continue
            hit = [t for t in gets if o["match"].lower() in b["name"][t[1]].lower()]
            if hit:
                v, i = hit[0]
                rank = [x[1] for x in gets].index(i) + 1
                seen.append({"observed": o["what"], "in_model": b["name"][i], "rank": rank, "money": round(v), "share_of_school": round(v / total, 3),
                             "share_of_its_sales": round(v / base[i], 3)})
            else:
                seen.append({"observed": o["what"], "in_model": None, "rank": None})
        out[school] = {"money_total": round(total), "observations": seen, "top_places": top}
        print(school, round(total)); [print("   seen:", s) for s in seen]; [print("   top:", t) for t in top[:6]]
    (ROOT / "docs/lunch_check.json").write_text(json.dumps({"_meta": {"what": __doc__.strip().split("\n\n")[0]}, "schools": out}, indent=1))
    return out


if __name__ == "__main__":
    main()
