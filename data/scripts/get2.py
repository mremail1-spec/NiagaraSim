import subprocess,json,time,itertools,os
lat=[42.98,43.075,43.17]; lon=[-79.25,-79.145,-79.04]
todo=[]
for k in ['shop','tourism','leisure']:
    for i,j in itertools.product(range(2),range(2)):
        fn=f"{k}_{i}{j}.json"
        try: json.load(open(fn)); continue
        except: pass
        # split quadrant into 4 sub-tiles
        la=[lat[i],(lat[i]+lat[i+1])/2,lat[i+1]]; lo=[lon[j],(lon[j]+lon[j+1])/2,lon[j+1]]
        for a,b in itertools.product(range(2),range(2)):
            todo.append((k,f"{k}_{i}{j}_{a}{b}.json",f"{la[a]},{lo[b]},{la[a+1]},{lo[b+1]}"))
for k,fn,bb in todo:
    for attempt in range(6):
        try: json.load(open(fn)); break
        except: pass
        q=f'[out:json][timeout:50];nwr["{k}"]({bb});out center tags;'
        r=subprocess.run(["curl","-s","-m","70","-o",fn,"-w","%{http_code}","--data-urlencode",f"data={q}","https://maps.mail.ru/osm/tools/overpass/api/interpreter"],capture_output=True,text=True)
        print(fn,r.stdout,attempt,flush=True)
        time.sleep(3)
print('DONE')
