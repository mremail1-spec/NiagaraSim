#!/usr/bin/env bash
# Rebuild, search scenarios, pick the one closest to the chains' published sales as the default, rebuild with it.
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/rebuild_all.sh
python3 -m nfprep.ensemble wave 400
python3 -m nfprep.ensemble wave 400
python3 - <<'PY'
import json
runs=[json.loads(l) for l in open('../build/ensemble/runs.jsonl')]
ok=sorted([i for i,r in enumerate(runs) if r['max_implausibility']<=1], key=lambda i: runs[i]['chains_rms_log'])
json.dump(ok[:40], open('../build/ensemble/traffic_first.json','w'))
print(len(ok),'fit; best chain errors', [round(runs[i]['chains_rms_log'],3) for i in ok[:5]])
PY
python3 -m nfprep.ensemble traffic 40
python3 -m nfprep.default_scenario choose
for s in default_scenario attendance_fit tests web unit_sales_check population_check travel_times_check tourist_check street_speeds_check parking_check scorecard; do rm -f ../build/stages/$s; done
bash scripts/rebuild_all.sh
python3 -m nfprep.ensemble report
echo PIPELINE_DONE
