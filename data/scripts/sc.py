import sys,json,subprocess,zipfile,csv,io,re
pid=sys.argv[1]; pat=re.compile(sys.argv[2],re.I)
r=subprocess.run(["curl","-s","-m","60",f"https://www150.statcan.gc.ca/t1/wds/rest/getFullTableDownloadCSV/{pid}/en"],capture_output=True)
url=json.loads(r.stdout)['object']
subprocess.run(["curl","-s","-m","600","-o",f"{pid}.zip",url])
z=zipfile.ZipFile(f"{pid}.zip");name=[n for n in z.namelist() if not n.endswith('MetaData.csv')][0]
n=0
with z.open(name) as fh, open(f"{pid}_filtered.csv","w",newline='') as out:
    rd=csv.reader(io.TextIOWrapper(fh,encoding='utf-8-sig'));hdr=next(rd);w=csv.writer(out);w.writerow(hdr)
    gi=hdr.index('GEO')
    for row in rd:
        if pat.search(row[gi]): w.writerow(row);n+=1
print(pid,name,n,'rows kept; cols:',hdr)
