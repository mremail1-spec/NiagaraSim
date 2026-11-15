import json,glob,csv,re
dic={r['FIELD_NAME']:(r['ALIAS_NAME'],r['TOPIC_NAME']) for r in json.load(open('dict.json'))}
skip={'OBJECTID','CTUID','DGUID','CTNAME','LANDAREA','PRUID','GEO_NAME','Shape__Area','Shape__Length'}
rows=[]
for fn in sorted(glob.glob('L*.geojson')):
    if fn=='L54.geojson': continue
    for f in json.load(open(fn))['features']:
        p=f['properties'];ct=p['CTNAME']
        for k,v in p.items():
            if k in skip or v is None: continue
            a,t=dic.get(k,(k,fn))
            rows.append((t,k,a,ct,v))
with open('census_tract_long.csv','w',newline='') as fh:
    w=csv.writer(fh);w.writerow(['topic','field','description','census_tract','value']);w.writerows(rows)
print(len(rows))
