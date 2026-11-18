import json, re, sys
USD_CAD = 1.4135   # Bank of Canada, data/policy_levers.json 'today'
pages=json.load(open(sys.argv[1]))
recs={}
for t in pages:
    L=[l.strip() for l in t.split('\n')]
    for i,l in enumerate(L):
        m=re.match(r'^Amenities for (.+?)(?:, an? (\d)-star hotel)?\.?:', l)
        if not m: continue
        name=m.group(1).strip(); stars=int(m.group(2)) if m.group(2) else None
        back=L[max(0,i-10):i]
        price=None
        for x in back:
            mm=re.fullmatch(r'(US|CA)?\$([\d,]+)',x)
            if mm:
                v=int(mm.group(2).replace(',',''))
                price=round(v*USD_CAD) if mm.group(1)=='US' else v   # Google switches to US$ after the first page
                break
        rating=next((float(x) for x in back if re.fullmatch(r'[1-5]\.\d',x)),None)
        rev=next((x for x in back if re.fullmatch(r'\([\d.,]+K?\)',x)),None)
        n=None
        if rev:
            v=rev.strip('()'); n=round(float(v[:-1].replace(',',''))*1000) if v.endswith('K') else int(float(v.replace(',','')))
        after=' '.join(L[i:i+16])
        rental='VACATION RENTAL' in after or bool(re.search(r'Sleeps \d+', l))
        if name not in recs:
            recs[name]={"name":name,"stars":stars,"price":price,"rating":rating,"reviews":n,"rental":rental}
r=list(recs.values())
hot=[x for x in r if not x['rental']]
print(len(r),'listings;',len(hot),'hotels/motels;',sum(1 for x in hot if x['price']),'with price;',sum(1 for x in hot if x['rating']),'with rating;',sum(1 for x in hot if x['stars']),'with stars')
json.dump(r,open(sys.argv[2],'w'),indent=1)
