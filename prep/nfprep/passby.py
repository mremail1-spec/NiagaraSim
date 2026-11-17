"""Pass-by trips: drivers who stop at a gas station or drive-through on the way somewhere else.

Counts the vehicles passing every road node in a four-week run of the model (shopping, dining and
visitor trips; commuting and through traffic are not simulated), stores them in city.json
(nodes.passing, vehicles per day) with each category's pass-by share (data/pass_by_shares.json).
The engine then gives each business a site factor (1 - p) + p * passing / category mean.

Usage: python3 -m nfprep.passby <city.json> [more city.json ...]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .loader import DATA_DIR

ROOT = Path(__file__).resolve().parents[2]


def shares(sim_categories: list[str]) -> list[float]:
    s = json.loads((DATA_DIR / "pass_by_shares.json").read_text())
    return [float(s.get(c, 0.0)) for c in sim_categories]


def apply(city_path: Path, days: int = 28) -> dict:
    city = json.loads(Path(city_path).read_text())
    city["nodes"].pop("passing", None)
    city["pass_by"] = []
    Path(city_path).write_text(json.dumps(city, separators=(",", ":")))
    beta = json.loads((DATA_DIR / "calibrated_multipliers.json").read_text())["beta"]
    out = Path(city_path).with_name("passing_traffic.json")
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "run", str(city_path), "--mode", "agent", "--days", str(days),
                    "--beta", str(beta), "--traffic", str(out)], check=True, stdout=subprocess.DEVNULL)
    by_source = json.loads(out.read_text())
    n = len(city["nodes"]["lat"])
    city["nodes"]["passing"] = [round(sum(by_source[i * 4:(i + 1) * 4]), 1) for i in range(n)]
    city["pass_by"] = shares(city["sim_categories"])
    Path(city_path).write_text(json.dumps(city, separators=(",", ":")))
    return city


if __name__ == "__main__":
    for p in sys.argv[1:]:
        c = apply(Path(p))
        print(p, "passing vehicles stored for", len(c["nodes"]["passing"]), "nodes")
