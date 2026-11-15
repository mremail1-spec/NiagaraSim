import json,csv,collections
from shapely.geometry import shape
V=collections.defaultdict(dict)
for r in csv.DictReader(open('census/census_tract_long.csv')): V[r['census_tract']][r['field']]=float(r['value'])
geo={f['properties']['CTNAME']:shape(f['geometry']) for f in json.load(open('census/L8.geojson'))['features']}
nb=[(f['properties']['COMMUNITY'],f['properties']['NAME'],shape(f['geometry'])) for f in json.load(open('city/Neighbourhood_Community.geojson'))['features']]
cm=[(f['properties']['NAME'],shape(f['geometry'])) for f in json.load(open('city/Community_Boundaries.geojson'))['features']]
tc=shape(json.load(open('city/Tourist_Core.geojson'))['features'][0]['geometry'])
def g(ct,k): return V[ct].get(k)
def pct(a,b): return round(100*a/b,1) if a is not None and b else None
rows=[]
for ct,G in sorted(geo.items()):
    c=G.centroid
    comms=sorted([(G.intersection(s).area/G.area,n) for n,s in cm if G.intersects(s)],reverse=True)
    nbs=sorted([(G.intersection(s).area/G.area,n) for _,n,s in nb if G.intersects(s)],reverse=True)
    v=lambda k:g(ct,k)
    pop=v('TOT_POP_2021_1')
    r=dict(census_tract=ct,lat=round(c.y,5),lon=round(c.x,5),land_km2=round(v('TOT_LAND_AREA_SQUARE_KILOM_7') or 0,2),
      community=' / '.join(n.title() for a,n in comms if a>0.15),
      neighbourhoods=', '.join(n.title() for a,n in nbs if a>0.08)[:120],
      pct_in_tourist_core=round(100*G.intersection(tc).area/G.area,1),
      population_2021=pop, pop_change_16_21_pct=v('TOT_POP_PERCENTAGE_CHANGE_3'),
      density_per_km2=v('TOT_POP_DENSITY_PER_SQUARE_6'),
      households=v('TOT_TOT_HH_TYPE_100_100'), avg_hh_size=v('TOT_AVG_HH_SIZE_57'),
      median_hh_income=v('TOT_MED_TOT_INC_HH_2020_243'), median_hh_income_after_tax=v('TOT_MED_AF_TAX_INC_HH_2020_244'),
      avg_hh_income=v('TOT_AVG_TOT_INC_HH_2020_252'),
      pct_hh_income_100k_plus=pct(v('TOT_F100000_OVER_276'),v('TOT_TOT_HH_TOT_INC_GRPS_20_260')),
      pct_hh_income_under_40k=pct(sum(v(k) or 0 for k in ['TOT_UNDER_5000_261','TOT_F5000_9999_262','TOT_F10000_14999_263','TOT_F15000_19999_264','TOT_F20000_24999_265','TOT_F25000_29999_266','TOT_F30000_34999_267','TOT_F35000_39999_268']),v('TOT_TOT_HH_TOT_INC_GRPS_20_260')),
      low_income_LIMAT_pct=v('TOT_PRV_LOW_INC_BASED_ON_L_345'), child_low_income_pct=v('TOT_F0_17_YRS_PCT_346'),
      pct_owner=pct(v('TOT_OWN_1415'),v('TOT_TOT_PR_HH_TENURE25_1414')), pct_renter=pct(v('TOT_RENTER_1416'),v('TOT_TOT_PR_HH_TENURE25_1414')),
      pct_owners_with_mortgage=v('TOT_PCT_OWN_HH_MORTGAGE_1483'),
      median_dwelling_value=v('TOT_MED_VAL_DWLS_1488'),
      median_owner_shelter_cost_mo=v('TOT_MED_MTHLY_SHR_OWNED_DW_1486'), median_rent_shelter_cost_mo=v('TOT_MED_MTHLY_SHR_RENTED_D_1494'),
      pct_hh_30pct_plus_on_shelter=pct(v('TOT_SP_30PCT_MR_INC_ON_SHR_1467'),v('TOT_TOT_OWN_TNT_HH_TOT_INC_1465')),
      pct_tenants_subsidized=v('TOT_PCT_TNT_HH_SUBSIDIZED_1491'),
      pct_single_detached=pct(v('TOT_SINGLE_DETACHED_HOUSE_42'),v('TOT_TOT_OCC_PRDWL_STRUCTUR_41')),
      pct_apartments=pct((v('TOT_APT_BUILDING_THAT_HAS_46') or 0)+(v('TOT_APT_BUILDING_THAT_HAS_47') or 0)+(v('TOT_APT_FLAT_DUPLEX_45') or 0),v('TOT_TOT_OCC_PRDWL_STRUCTUR_41')),
      pct_built_pre1981=pct((v('TOT_F1960_BEFORE_1441') or 0)+(v('TOT_F1961_1980_1442') or 0),v('TOT_TOT_OCC_PRDWL_PERIOD_C_1440')),
      pct_couple_with_children=pct(v('TOT_W_CH_103'),v('TOT_TOT_HH_TYPE_100_100')),
      pct_couple_no_children=pct(v('TOT_WOUT_CH_104'),v('TOT_TOT_HH_TYPE_100_100')),
      pct_one_parent=pct(v('TOT_ONE_PARENT_FMLY_HH_105'),v('TOT_TOT_HH_TYPE_100_100')),
      pct_one_person=pct(v('TOT_ONE_PERSON_HH_110'),v('TOT_TOT_HH_TYPE_100_100')),
      pct_multigenerational=pct(v('TOT_MULTIGENAL_HH_106'),v('TOT_TOT_HH_TYPE_100_100')),
      kids_0_17=v('TOT_F0_17_YRS_336'), seniors_65plus=v('TOT_F65_YRS_OVER_339'),
      pct_kids_0_17=pct(v('TOT_F0_17_YRS_336'),v('TOT_TOT_LIM_LIS_2020_POP_P_335')),
      pct_seniors_65plus=pct(v('TOT_F65_YRS_OVER_339'),v('TOT_TOT_LIM_LIS_2020_POP_P_335')),
      pct_female=pct(v('FE_TOT_LIM_LIS_2020_POP_PR_335'),v('TOT_TOT_LIM_LIS_2020_POP_P_335')),
      pct_visible_minority=pct(v('TOT_TOT_VISIBLE_MIN_POP_1684'),v('TOT_TOT_VISIBLE_MIN_POP_PR_1683')),
      pct_immigrants=pct(v('TOT_IMMS_1529'),v('TOT_TOT_IMM_STAT_PERIOD_IM_1527')),
      pct_accom_food_workers=pct(v('TOT_F72_ACCOMMODATION_FOOD_2279'),v('TOT_ALL_INDUSTRIES_2261')),
      pct_commute_car=pct(v('TOT_CAR_TRUCK_VAN_2604'),v('TOT_TOT_MN_MO_CMT_EM_LBR_F_2603')),
      pct_commute_transit_walk_bike=pct((v('TOT_PUBLIC_TRANSIT_2607') or 0)+(v('TOT_WALKED_2608') or 0)+(v('TOT_BICYCLE_2609') or 0),v('TOT_TOT_MN_MO_CMT_EM_LBR_F_2603')),
    )
    rows.append(r)
with open('tract_profiles.csv','w',newline='') as fh:
    w=csv.DictWriter(fh,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
for r in sorted(rows,key=lambda r:r['median_hh_income'] or 0):
    print(r['census_tract'],r['community'][:28].ljust(28),r['neighbourhoods'][:40].ljust(40),int(r['population_2021']),'med$',r['median_hh_income'],'LIM',r['low_income_LIMAT_pct'],'own',r['pct_owner'],'kids',r['pct_kids_0_17'],'65+',r['pct_seniors_65plus'],'TC',r['pct_in_tourist_core'],'acc/food',r['pct_accom_food_workers'])
