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
step calibrate python3 -m nfprep.calibrate
step build2 python3 -m nfprep.build 2025 1
step attendance_fit python3 -m nfprep.attendance_fit
step tests bash -c 'cd ../engine && cargo test --release 2>&1 | tee /dev/stderr | grep -q "test result: ok. 22 passed"'
step web bash -c "python3 -m nfprep.webexport 2025 $(beta)"
for c in unit_sales_check population_check travel_times_check tourist_check street_speeds_check parking_check; do
  step $c python3 -m nfprep.$c
done
step scorecard python3 -m nfprep.scorecard
echo ALL_DONE
