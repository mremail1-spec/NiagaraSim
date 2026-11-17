"""Foot traffic: people walking past every road node, on an average day of the calibrated year.

A full-year agent run counts residents' walked trips (there and back along the route), visitors'
stops reached on foot from their hotel, parking lot or arrival point, and visitors who drove,
counted at the door they walked to. Stored in city.json as nodes.walkers (people per day).

No public pedestrian counts exist for Niagara Falls (the City provides counts on paid request);
the check available is that attraction attendance, which the model is calibrated to, lands where
the published figures say. Usage: python3 -m nfprep.foot_traffic
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]
CITY = ROOT / "build" / "city_2025"


def main() -> list[float]:
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = CITY / "walkers.json"
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(CITY / "city.json"), "--mode", "agent", "--days", "365",
                    "--beta", str(beta), "--traffic", str(CITY / "traffic_year.json"), "--walkers", str(out)],
                   check=True, stdout=subprocess.DEVNULL)
    w = json.loads(out.read_text())
    for path in (CITY / "city.json", ROOT / "web" / "city.json"):
        c = json.loads(path.read_text())
        if len(c["nodes"]["lat"]) == len(w):
            c["nodes"]["walkers"] = [round(v) for v in w]
            path.write_text(json.dumps(c, separators=(",", ":")))
    print("busiest", round(max(w)), "people a day; nodes with walkers", sum(1 for v in w if v > 0))
    return w


if __name__ == "__main__":
    main()
