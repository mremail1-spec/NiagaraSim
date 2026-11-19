"""Read the Niagara Association of REALTORS monthly figures for Niagara Falls through Gemini (page
reader on), one niagarahomeportal.ca monthly post per request. Keeps only figures Gemini quotes from
the page; months it cannot read are recorded as missing. Usage: python3 gemini_nar_months.py <out.json>"""
import json, sys, time, urllib.request, urllib.error, re

MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"]
out_path = sys.argv[1]
try:
    out = json.load(open(out_path))
except Exception:
    out = {}
PROMPT = """Read this page: {url}
If the page cannot be read or is not the Niagara market update for {mname} {year}, answer exactly {{"readable": false}}.
Otherwise answer ONE JSON object, nothing else, with the figures exactly as the page states them:
{{"readable": true,
 "niagara_falls": {{"month": "{ym}", "benchmark_price": , "sales": , "new_listings": , "days_on_market": ,
                   "prior_year": {{"benchmark_price": , "sales": , "new_listings": , "days_on_market": }}}},
 "region": {{"benchmark_price": , "sales": , "new_listings": , "days_on_market": }},
 "quotes": ["the sentences or table rows the Niagara Falls figures come from"]}}
Use null for any figure the page does not give. Numbers only, no $ or commas. Never estimate."""

def ask(url, ym, mname, year):
    body = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(url=url, ym=ym, mname=mname.title(), year=year)}]}],
            "tools": [{"url_context": {}}], "generationConfig": {"temperature": 0}}
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": "proxy-injected"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            c = d["candidates"][0]
            text = "".join(p.get("text", "") for p in c["content"]["parts"])
            status = [m.get("urlRetrievalStatus") for m in c.get("urlContextMetadata", {}).get("urlMetadata", [])]
            m = re.search(r"\{.*\}", text, re.S)
            return (json.loads(m.group(0)) if m else {"readable": False, "raw": text[:500]}), status
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(30 * (attempt + 1)); continue
            return {"readable": False, "error": e.code}, []
        except Exception as e:
            time.sleep(5)
    return {"readable": False, "error": "gave up"}, []

for year in range(2023, 2027):
    for i, mname in enumerate(MONTHS):
        ym = f"{year}-{i + 1:02d}"
        if ym > "2026-10" or (ym in out and out[ym].get("readable")):
            continue
        url = f"https://www.niagarahomeportal.ca/blog/niagara-market-update-{mname}-{year}/"
        res, status = ask(url, ym, mname, year)
        res["url"] = url
        res["retrieval"] = status
        out[ym] = res
        json.dump(out, open(out_path, "w"), indent=1)
        print(ym, res.get("readable"), (res.get("niagara_falls") or {}).get("sales"), status, flush=True)
        time.sleep(7)
