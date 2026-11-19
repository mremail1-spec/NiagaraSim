"""Ask Gemini (Google's API, connected to this session) with Google Search and page reading on.
Prints the answer and the pages Gemini actually used (grounding metadata), so every figure can be
traced. Usage: python3 gemini_ask.py <prompt file> [model]"""
import json, sys, urllib.request

prompt = open(sys.argv[1]).read()
model = sys.argv[2] if len(sys.argv) > 2 else "gemini-2.5-flash"
body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}, {"url_context": {}}],
        "generationConfig": {"temperature": 0}}
req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                             data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "x-goog-api-key": "proxy-injected"})
with urllib.request.urlopen(req, timeout=600) as r:
    d = json.loads(r.read())
c = d["candidates"][0]
print("".join(p.get("text", "") for p in c["content"]["parts"]))
meta = c.get("urlContextMetadata", {}).get("urlMetadata", [])
gm = c.get("groundingMetadata", {})
print("\n---PAGES READ---")
for m in meta:
    print(m.get("urlRetrievalStatus"), m.get("retrievedUrl"))
print("---SEARCH SOURCES---")
for ch in gm.get("groundingChunks", []):
    w = ch.get("web", {})
    print(w.get("title"), w.get("uri"))
