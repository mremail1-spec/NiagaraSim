"""Attraction attendance: a correction from one full agent year.

The engine fits each published attraction's pull so its expected yearly visitors match Niagara
Parks' published attendance. A full year of individual parties (hotels filling, parties turned
away, multi-day trips) then lands a few percent off. This runs that year once and stores, per
attraction, published / simulated as a correction to the fit (data/attendance_fit.json), applied
in the build and written straight into the current city.json. The published figures themselves
are unchanged and remain what the checks compare against.

Usage: python3 -m nfprep.attendance_fit
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
FILE = DATA_DIR / "attendance_fit.json"


def stored() -> dict[str, float]:
    return json.loads(FILE.read_text())["factor"] if FILE.exists() else {}


def factors_for(names: list[str], att: list[float]) -> list[float]:
    f = stored()
    return [round(float(f.get(n, 1.0)), 4) if a > 0 and i < len(names) else 1.0
            for i, (a, n) in enumerate(zip(att, names + [""] * (len(att) - len(names))))]


def main(city_path: Path = ROOT / "build" / "city_2025" / "city.json") -> dict:
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = city_path.with_name("attendance_fit_run.json")
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "agent", "--days", "365",
                    "--beta", str(beta), "--out", str(out)], check=True, stdout=subprocess.DEVNULL)
    att = json.loads(out.read_text())["tourism"]["attendance"]
    old = stored()
    new = {n: round(old.get(n, 1.0) * v["published"] / v["simulated"], 4) for n, v in att.items() if v["simulated"] > 0}
    FILE.write_text(json.dumps({"_meta": {"what": __doc__.strip().split("\n\n")[0],
                                          "last_run": {n: {"published": v["published"], "simulated_before": v["simulated"]} for n, v in att.items()}},
                                "factor": new}, indent=1))
    city = json.loads(city_path.read_text())
    t = city["tourism"]
    t["attendance_fit_factor"] = factors_for(city["businesses"]["name"], t["attendance_target"])
    city_path.write_text(json.dumps(city, separators=(",", ":")))
    for n, v in att.items():
        print(f"{n:30s} published {v['published']:>9,.0f}  simulated {v['simulated']:>9,.0f}  factor {new.get(n)}")
    return new


if __name__ == "__main__":
    main()
