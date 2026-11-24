#!/usr/bin/env bash
# Full rebuild in resumable steps: each finished step leaves a marker in build/stages/, a rerun skips it.
# Start fresh with: rm -rf build/stages
set -euo pipefail
cd "$(dirname "$0")/.."
ST=../build/stages; LOG=../build/logs; mkdir -p $ST $LOG
step() {  # name command...
  local name=$1; shift
  if [ -f $ST/$name ]; then echo "${name}_SKIPPED"; return; fi
  if "$@" > $LOG/$name.log 2>&1; then touch $ST/$name; echo "${name}_OK"; else echo "${name}_FAILED"; tail -20 $LOG/$name.log; exit 1; fi
}
beta() { python3 -c "import json;print(json.load(open('../data/calibrated_multipliers.json'))['beta'])"; }
step build1 python3 -m nfprep.build 2025 1
step distance_fit python3 -m nfprep.distance_fit
step calibrate python3 -m nfprep.calibrate
step build2 python3 -m nfprep.build 2025 1
step default_scenario python3 -m nfprep.default_scenario apply
step attendance_fit python3 -m nfprep.attendance_fit
step forecast_error python3 -m nfprep.default_scenario forecast_error
step traffic_final bash -c "python3 -m nfprep.passby ../build/city_2025/city.json && python3 -m nfprep.traffic_layers"
step tests bash -c 'cd ../engine && cargo test --release 2>&1 | tee /dev/stderr | grep -q "test result: ok. 22 passed"'
step web bash -c "python3 -m nfprep.webexport 2025 $(beta)"
for c in unit_sales_check population_check travel_times_check tourist_check street_speeds_check parking_check; do
  step $c python3 -m nfprep.$c
done
step scorecard python3 -m nfprep.scorecard
echo ALL_DONE
