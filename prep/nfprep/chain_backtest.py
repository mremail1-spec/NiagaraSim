"""Back-test of the opportunity forecast on chain branches: take out one branch at a time, forecast a newcomer of its
kind and published size at each of its real addresses, compare with the chain's published sales per store."""
import json, re, subprocess, tempfile, numpy as np
from pathlib import Path
from .daytime import places, dist
ROOT = Path(__file__).resolve().parents[2]
city = json.load(open(ROOT/'build/city_2025/city.json')); b = city['businesses']; cats = city['sim_categories']
cal = json.load(open(ROOT/'data/calibrated_multipliers.json')); beta = cal.get('beta_fit', cal['beta'])
pub = json.load(open(ROOT/'data/public_unit_sales_2025.json'))['brands']
hs = [p for p in places() if p['kind'] == 'high school']
out = {}
for br, v in pub.items():
    ids = [i for i, n in enumerate(b['name']) if re.search(v['pattern'], n.lower()) and not b['external'][i]
           and not b['closed_from'][i] and ('category' not in v or cats[b['sim_category'][i]] == v['category'])]
    if not ids: continue
    cat = cats[b['sim_category'][ids[0]]]
    def one(i):
        # this branch alone taken out; a newcomer of its kind and the chain's published size forecast at its address
        c = json.loads(json.dumps(city))
        c['businesses']['closed_from'][i] = '200001'
        with tempfile.TemporaryDirectory() as d:
            cp, sp, op = Path(d)/'c.json', Path(d)/'s.json', Path(d)/'o.json'
            cp.write_text(json.dumps(c, separators=(',', ':')))
            sp.write_text(json.dumps([[b['lat'][i], b['lon'][i], b['sim_category'][i], v['average']]]))
            subprocess.run([str(ROOT/'engine/target/release/nfsim'), 'forecast', str(cp), '--beta', str(beta), '--closures-as-of', '209912',
                            '--sites', str(sp), '--out', str(op)], check=True, capture_output=True, text=True)
            return float(np.array(json.load(open(op)), float).sum())
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        f = np.array(list(ex.map(one, ids)))
    near = []
    for k, i in enumerate(ids):
        s = min(hs, key=lambda q: dist(b['lat'][i], b['lon'][i], q['lat'], q['lon'])); m = dist(b['lat'][i], b['lon'][i], s['lat'], s['lon'])
        near.append(dict(address=b['address'][i], school=s['name'], metres=round(m), forecast=round(f[k]), vs_published=round(f[k]/v['average'], 2), core=bool(b['core'][i])))
    out[br] = dict(category=cat, published=v['average'], quality=v['quality'], branches=len(ids),
                   median_forecast=round(float(np.median(f))), median_vs_published=round(float(np.median(f))/v['average'], 2),
                   branches_detail=sorted(near, key=lambda x: x['metres']))
    print(f"{br:20s} {cat:18s} n={len(ids):2d} forecast median {np.median(f)/1e6:5.2f}M published {v['average']/1e6:5.2f}M ratio {np.median(f)/v['average']:.2f}")
    for x in out[br]['branches_detail']:
        if x['metres'] <= 800: print(f"     near {x['school'][:20]:20s} {x['metres']:4d} m {x['address']}: {x['forecast']/1e6:.2f}M = {x['vs_published']}x published")
json.dump(out, open(ROOT/'docs/chain_backtest.json', 'w'), indent=1)
