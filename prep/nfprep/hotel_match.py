"""Match hotel listings (Google Hotels) to the model's hotels by name."""
from __future__ import annotations

import re

STOP = {"niagara", "falls", "hotel", "the", "by", "and", "on", "suites", "at", "of", "resort", "spa", "ihg", "an", "a",
        "canada", "ontario", "hotels", "inc", "ca"}
# most specific first: a sub-brand decides before its parent company
BRANDS = ["embassy suites", "spark", "hampton", "homewood", "hilton garden", "doubletree", "tapestry", "hilton",
          "courtyard", "fairfield", "towneplace", "residence inn", "marriott", "four points", "sheraton",
          "7 days", "days inn", "travelodge", "super 8", "econo lodge", "howard johnson", "ramada", "wyndham garden", "wyndham",
          "best western plus", "best western", "comfort", "quality", "holiday inn express", "holiday inn", "crowne plaza",
          "country inn", "radisson", "great wolf", "oakes", "old stone", "skyline", "americana", "sterling", "clarion",
          "knights inn", "rodeway", "motel 6", "hyatt", "brock"]


def tokens(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9']+", s.lower().replace("’", "'")) if w not in STOP}


def brand(s: str):
    s = s.lower()
    for b in BRANDS:
        if b in s:
            return b
    return None


def match(model_names: list[str], listing_names: list[str]) -> dict[int, int]:
    """model index -> listing index. Same brand needed when both have one; best name overlap wins;
    each listing used once, strongest pairs first."""
    cand = []
    for i, a in enumerate(model_names):
        ta, ba = tokens(a), brand(a)
        for j, b in enumerate(listing_names):
            tb, bb = tokens(b), brand(b)
            if ba != bb:
                continue
            jac = len(ta & tb) / max(1, len(ta | tb))
            need = 0.34 if ba else 0.6
            if jac >= need or (ba and ta - {ba} <= tb):
                cand.append((jac, i, j))
    out, used_i, used_j = {}, set(), set()
    for jac, i, j in sorted(cand, reverse=True):
        if i not in used_i and j not in used_j:
            out[i] = j; used_i.add(i); used_j.add(j)
    return out
