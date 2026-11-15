import json,glob,csv,collections
from shapely.geometry import shape,Point
from shapely.strtree import STRtree
C='/home/claude/data/city/'; O='/home/claude/out/niagara_falls_data/'
nb=json.load(open(C+'Neighbourhood_Community.geojson'))['features']
polys=[shape(f['geometry']) for f in nb]; tree=STRtree(polys)
core=shape(json.load(open(C+'Tourist_Core.geojson'))['features'][0]['geometry'])
from shapely.ops import unary_union
city=unary_union(polys)
seen={};
for fn in sorted(glob.glob('/home/claude/osm/*.json')):
    try: d=json.load(open(fn))
    except: continue
    for e in d.get('elements',[]):
        seen[(e['type'],e['id'])]=e
# category mapping
SPEND_AMEN={'restaurant':'restaurant','fast_food':'fast_food','cafe':'cafe','bar':'bar_pub','pub':'bar_pub','biergarten':'bar_pub','nightclub':'nightlife','ice_cream':'cafe','food_court':'fast_food',
 'fuel':'fuel','pharmacy':'pharmacy','bank':'bank','atm':'atm','bureau_de_change':'bank','casino':'casino','gambling':'casino','cinema':'entertainment','theatre':'entertainment','arts_centre':'entertainment',
 'car_rental':'car_rental','car_wash':'auto_service','parking':'parking','doctors':'health','dentist':'health','clinic':'health','veterinary':'pet','childcare':'childcare','marketplace':'retail_other','events_venue':'entertainment','conference_centre':'entertainment','post_office':'services','charging_station':'fuel'}
GROC={'supermarket','convenience','greengrocer','butcher','bakery','deli','alcohol','beverages','wine','seafood','frozen_food','health_food','confectionery','farm'}
APP={'clothes','shoes','jewelry','bag','boutique','fashion_accessories','watches'}
TOUR_SHOP={'gift','souvenir','chocolate','candy'}
AUTO={'car','car_repair','car_parts','tyres','motorcycle'}
HOME={'furniture','hardware','doityourself','garden_centre','houseware','interior_decoration','appliance','electronics','mobile_phone','computer','paint','bed','kitchen','lighting','trade','flooring'}
def cat(t):
    a=t.get('amenity')
    if a in SPEND_AMEN: return 'amenity',a,SPEND_AMEN[a]
    s=t.get('shop')
    if s:
        if s in GROC: g='grocery_food'
        elif s in APP: g='apparel'
        elif s in TOUR_SHOP: g='souvenir_gift'
        elif s in AUTO: g='auto_service'
        elif s in HOME: g='home_electronics'
        elif s in('mall','department_store','variety_store','general','wholesale'): g='general_merchandise'
        elif s in('hairdresser','beauty','massage','tattoo','cosmetics','nail_salon','laundry','dry_cleaning','optician','hearing_aids','medical_supply','chemist'): g='personal_services'
        elif s in('tobacco','e-cigarette','cannabis'): g='tobacco_cannabis'
        elif s in('vacant','yes') and not t.get('name'): return None
        else: g='retail_other'
        return 'shop',s,g
    tr=t.get('tourism')
    if tr in('hotel','motel','guest_house','hostel','apartment','camp_site','caravan_site','chalet'): return 'tourism',tr,'accommodation'
    if tr in('attraction','museum','theme_park','zoo','aquarium','gallery','viewpoint'): return 'tourism',tr,'attraction'
    l=t.get('leisure')
    if l in('water_park','amusement_arcade','miniature_golf','golf_course','bowling_alley','escape_game','fitness_centre','sports_centre','adult_gaming_centre','ice_rink','trampoline_park'): return 'leisure',l,'leisure_paid'
    if t.get('office') and t.get('name'): return 'office',t['office'],'office'
    if t.get('craft') and t.get('name'): return 'craft',t['craft'],'craft'
    return None
rows=[]
for (ty,i),e in seen.items():
    t=e.get('tags',{}); c=cat(t)
    if not c: continue
    if c[2] in('parking','atm') and not t.get('name'): continue
    lat=e.get('lat') or e.get('center',{}).get('lat'); lon=e.get('lon') or e.get('center',{}).get('lon')
    if lat is None: continue
    p=Point(lon,lat)
    if not city.contains(p): continue
    hits=[int(g) for g in tree.query(p)]
    nm=comm=''
    for h in hits:
        if polys[h].contains(p): nm=nb[h]['properties']['NAME']; comm=nb[h]['properties']['COMMUNITY']; break
    rows.append({'osm_id':f'{ty}/{i}','name':t.get('name',''),'key':c[0],'value':c[1],'sim_category':c[2],'brand':t.get('brand',''),
      'cuisine':t.get('cuisine',''),'stars':t.get('stars',''),'rooms':t.get('rooms',''),'opening_hours':t.get('opening_hours',''),
      'street':(t.get('addr:housenumber','')+' '+t.get('addr:street','')).strip(),'lat':round(lat,6),'lon':round(lon,6),
      'neighbourhood':nm,'community':comm,'in_tourist_core':int(core.contains(p))})
rows.sort(key=lambda r:(r['sim_category'],r['name']))
ded=[];last={}
for r in rows:
    k=(r['sim_category'],r['name'].lower().replace('the ',''))
    if r['name'] and k in last and abs(last[k]['lat']-r['lat'])<0.002 and abs(last[k]['lon']-r['lon'])<0.002: continue
    last[k]=r; ded.append(r)
print('dupes removed',len(rows)-len(ded)); rows=ded
with open(O+'places.csv','w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
json.dump({'type':'FeatureCollection','features':[{'type':'Feature','geometry':{'type':'Point','coordinates':[r['lon'],r['lat']]},'properties':{k:v for k,v in r.items() if k not in('lat','lon')}} for r in rows]},open(O+'places.geojson','w'))
print('raw',len(seen),'kept',len(rows))
c=collections.Counter(r['sim_category'] for r in rows); cc=collections.Counter(r['sim_category'] for r in rows if r['in_tourist_core'])
for k,v in c.most_common(): print(f'{k:20s}{v:5d}  core {cc[k]}')
print('core total',sum(cc.values()))
comm=collections.Counter(r['community'] for r in rows); print(comm.most_common())
