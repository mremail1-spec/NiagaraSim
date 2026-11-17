"""Run the whole calibration chain in order.

1. Build the city.
2. Fit store sales by type, neighbouring towns, tourist spending shares and Niagara Parks' pull
   (engine: nfsim calibrate-sales).
3. Fit hotels to published occupancy and room rates (nfprep.hotel_fit, which rebuilds the city).

Usage: python3 -m nfprep.calibrate
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .loader import DATA_DIR
from .population import BUILD_DIR

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    cal_path = DATA_DIR / "calibrated_multipliers.json"
    previous = json.loads(cal_path.read_text()).get("operator_pull", {}) if cal_path.exists() else {}
    subprocess.run(["python3", "-m", "nfprep.build", "2025", "1"], check=True, cwd=ROOT / "prep", stdout=subprocess.DEVNULL)
    city = BUILD_DIR / "city_2025"
    json.dump(json.loads((city / "city.json").read_text())["calibration"]["sales_targets"], open(city / "sales_targets.json", "w"))
    subprocess.run([str(ROOT / "engine/target/release/nfsim"), "calibrate-sales", str(city / "city.json"),
                    "--targets", str(city / "sales_targets.json"), "--out", str(cal_path)], check=True)
    cal = json.loads(cal_path.read_text())
    # the city file already carried the previous pull, so this run's factor multiplies it
    cal["operator_pull"] = {k: v * previous.get(k, 1.0) for k, v in cal["operator_pull"].items()}
    cal_path.write_text(json.dumps(cal, indent=1))
    subprocess.run(["python3", "-m", "nfprep.hotel_fit"], check=True, cwd=ROOT / "prep")
    print(json.dumps({k: cal[k] for k in ("beta", "operator_pull", "operator_revenue")}, indent=1))


if __name__ == "__main__":
    main()
