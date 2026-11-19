"""Gemini with a spending guard (owner's paid key, $6 budget; this stops at $5).

Every request is logged to build/gemini_ledger.json with its tokens and an estimated cost at
deliberately high prices, and no request is sent once the running total would pass the cap.
Usage: python3 gemini_paid.py <prompt file> [--search] [--model M] [--max-out N]
  Ask for ONE page per request and never "follow next pages": each extra page Gemini opens re-sends
  everything read so far (one such request read 966,000 tokens).
  --search  switches on Google Search grounding (billed per query; off by default: page reading only)."""
import json, sys, time, urllib.request, urllib.error
from pathlib import Path

LEDGER = Path(__file__).resolve().parents[2] / "build" / "gemini_ledger.json"
CAP = 4.00   # US$, under the owner's CA$6.00 monthly cap on Google's side
# USD per million tokens (input, output incl. thinking), set high on purpose; search per query
PRICE = {"flash": (0.50, 3.00), "pro": (2.00, 12.00)}
SEARCH_QUERY = 0.035


def spent() -> float:
    return sum(x["cost"] for x in json.loads(LEDGER.read_text())) if LEDGER.exists() else 0.0


def ask(prompt: str, model="gemini-3-flash-preview", search=False, max_out=8192, note=""):
    if spent() >= CAP - 0.05:
        raise SystemExit(f"stopped: estimated spend ${spent():.2f} has reached the ${CAP:.2f} cap")
    if search:
        # One grounded request ran 151 Google searches on its own (Gemini 3 picks how many) and cost
        # about $2-5 of the owner's $6 budget. Search stays off unless the owner approves a request.
        raise SystemExit("search is disabled: ask the owner first (one request ran 151 searches)")
    tools = [{"url_context": {}}]
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "tools": tools,
            "generationConfig": {"temperature": 0, "maxOutputTokens": max_out,
                                 # least hidden reasoning: thinking tokens are billed as output
                                 "thinkingConfig": ({"thinkingLevel": "low"} if "gemini-3" in model else {"thinkingBudget": 0})}}
    req = urllib.request.Request(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                                 data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": "proxy-injected"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                d = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < 3:
                time.sleep(20 * (attempt + 1)); continue
            raise
    u = d.get("usageMetadata", {})
    pin, pout = PRICE["pro" if "pro" in model else "flash"]
    tin = u.get("promptTokenCount", 0) + u.get("toolUsePromptTokenCount", 0)
    tout = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    c = (d.get("candidates") or [{}])[0]
    if not d.get("candidates"):
        print("no answer returned:", json.dumps({k: v for k, v in d.items() if k != "usageMetadata"})[:800], file=sys.stderr)
    queries = len(c.get("groundingMetadata", {}).get("webSearchQueries", []))
    cost = tin / 1e6 * pin + tout / 1e6 * pout + queries * SEARCH_QUERY
    LEDGER.parent.mkdir(exist_ok=True)
    led = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    led.append({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "note": note, "tokens_in": tin,
                "tokens_out": tout, "search_queries": queries, "cost": round(cost, 5)})
    LEDGER.write_text(json.dumps(led, indent=1))
    text = "".join(p.get("text", "") for p in c.get("content", {}).get("parts", []))
    pages = [(m.get("urlRetrievalStatus"), m.get("retrievedUrl")) for m in c.get("urlContextMetadata", {}).get("urlMetadata", [])]
    if pages and any(s != "URL_RETRIEVAL_STATUS_SUCCESS" for s, _ in pages):
        # Gemini answers anyway when a page fails to open, with made-up content (seen 2026-11-19)
        print("WARNING: a page did NOT open; do not use this answer for that page", file=sys.stderr)
        text = "PAGE NOT RETRIEVED (answer discarded)\n" + ("" if all(s != "URL_RETRIEVAL_STATUS_SUCCESS" for s, _ in pages) else text)
    if cost > 0.05:
        print(f"WARNING: this request cost about ${cost:.2f} ({tin:,} tokens in, {tout:,} out): ask for less per request", file=sys.stderr)
    return text, pages, cost


if __name__ == "__main__":
    a = sys.argv[1:]
    model = a[a.index("--model") + 1] if "--model" in a else "gemini-3-flash-preview"
    max_out = int(a[a.index("--max-out") + 1]) if "--max-out" in a else 8192
    text, pages, cost = ask(open(a[0]).read(), model=model, search="--search" in a, max_out=max_out, note=a[0])
    print(text)
    print("\n---PAGES READ---")
    for s, u in pages:
        print(s, u)
    print(f"---COST this request ${cost:.4f}; total ${spent():.4f} of ${CAP:.2f} cap---")
