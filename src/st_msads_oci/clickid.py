"""Join ServiceTitan conversion rows to captured msclkids via the Worker's KV export."""
import json
import ssl
import urllib.request
import urllib.parse
from datetime import timedelta
from pathlib import Path

import certifi

from .normalize import last10
from .rows import GOAL_BOOKED_JOB, GOAL_BOOKED_JOB_CALL, Dropped, parse_utc

# ponytail: window is NOT the binding constraint on call-tier joins for ordinary rows — the
# identity gate in find_msclkid() is. Any row carrying an email or phone hash short-circuits to
# "no_session_captured" before the call branch runs, and ST bookings essentially always carry a
# phone, so only contactless rows ever reach it. Bake 7/31-8/3 produced 0 click_source=call rows.
# Don't re-tune this hoping to unlock call joins; that needs a design change (call fallback for
# rows whose identity hashes missed), not a bigger window.
#
# Update (spec 2026-08-05): that design change shipped — suspect rows recovered via call
# inference (recovered_via=="call") are the one exception carved out of the identity gate; they
# carry PII by construction but still fall through to the call branch. For those rows,
# POOL_WINDOW_H IS the binding constraint on tier A (matched) vs tier B (pool_ambiguous /
# no_session_captured); the gate no longer blocks them first.
#
# Update (spec 2026-08-13): second carve-out — GOAL_BOOKED_JOB_CALL rows (ST booked
# calls) also bypass the gate for the same reason; POOL_WINDOW_H binds their tier A too.
POOL_WINDOW_H = 2   # tuned 2026-07-30: live /map showed median DNI re-bind 133m; 72h made every call pool_ambiguous
PROXIMITY_S = 120


def fetch_map(base_url, bearer, timeout=30):
    """Raises on any failure — the caller must abort the run rather than build empty Click Ids."""
    result = {"ids": {}, "dni": {}, "forms": []}
    cursor, seen = None, set()
    ctx = ssl.create_default_context(cafile=certifi.where())
    while True:
        url = base_url.rstrip("/") + "/map"
        if cursor:
            url += "?" + urllib.parse.urlencode({"cursor": cursor})
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}",
                                                   "User-Agent": "servicetitan-msads-oci/1.0"})
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            page = json.loads(r.read().decode("utf-8"))
        if page.get("schema_version") != 2 or "next_cursor" not in page:
            raise ValueError("Worker must serve authenticated capture schema version 2")
        for group in ("ids", "dni"):
            for key, bindings in page[group].items():
                if not isinstance(bindings, list):
                    raise ValueError("invalid capture binding list")
                result[group].setdefault(key, []).extend(bindings)
        result["forms"].extend(page["forms"])
        cursor = page["next_cursor"]
        if cursor is None:
            return result
        if not isinstance(cursor, str) or not cursor or cursor in seen:
            raise ValueError("invalid or repeated Worker pagination cursor")
        seen.add(cursor)


def publish_file(base_url, bearer, name, path, timeout=30):
    body = Path(path).read_bytes()
    # ponytail: brief's snippet omits User-Agent; Cloudflare 403s the default urllib UA
    # (same reason fetch_map sets it) — added here to match, or prod publish fails.
    req = urllib.request.Request(base_url.rstrip("/") + f"/f/{name}", data=body, method="PUT",
                                 headers={"Authorization": f"Bearer {bearer}",
                                          "Content-Type": "text/csv",
                                          "User-Agent": "servicetitan-msads-oci/1.0"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status == 204


def _last10(phones):
    for p in phones or []:
        d = last10(p)
        if d:
            return d
    return None


def find_msclkid(row, mapping, calls_by_id):
    """Return (msclkid|None, source|None, withhold_reason|None). First match wins; ambiguity fails closed."""
    ids = mapping.get("ids", {})
    p10 = _last10(row.raw_phones)
    for key, source in (("e:" + row.email_hash if row.email_hash else None, "email"),
                        ("p:" + row.phone_hash if row.phone_hash else None, "phone"),
                        ("p10:" + p10 if p10 else None, "phone10")):
        bindings = ids.get(key, [])
        if isinstance(bindings, dict):  # trusted local maps from older releases
            bindings = [bindings]
        candidates = set()
        for binding in bindings:
            captured = parse_utc(binding["ts"])
            if captured <= row.ts <= captured + timedelta(days=90):
                candidates.add(binding["m"])
        if len(candidates) > 1:
            return None, None, "identity_ambiguous"
        if candidates:
            return candidates.pop(), source, None

    # If row has an identity hash (even unmatched), don't try proximity or call matching.
    # Exceptions: suspect rows recovered via call inference (spec 2026-08-05) and
    # booked-call goal rows (spec 2026-08-13) carry PII by construction — let them reach
    # the DNI-pool branch; a failed join lands them in tier B.
    has_identity = row.email_hash or row.phone_hash
    if (has_identity and row.goal != GOAL_BOOKED_JOB_CALL
            and getattr(row, "recovered_via", None) != "call"):
        return None, None, "no_session_captured"

    if row.lead_call_id:
        call = calls_by_id.get(row.lead_call_id)
        if call and call.get("direction") == "Inbound" and call.get("to"):
            received = parse_utc(call["receivedOn"])
            to10 = last10(call["to"])
            cands = set()
            for b in mapping.get("dni", {}).get(to10, []):
                bts = parse_utc(b["ts"])
                if bts <= received <= bts + timedelta(hours=POOL_WINDOW_H):
                    cands.add(b["m"])
            if len(cands) == 1:
                return cands.pop(), "call", None
            if len(cands) > 1:
                return None, None, "pool_ambiguous"

    if row.goal == GOAL_BOOKED_JOB:   # submit-time ≈ createdOn only holds for web bookings
        cands = set()
        for f in mapping.get("forms", []):
            if f.get("m") and abs((parse_utc(f["ts"]) - row.ts).total_seconds()) <= PROXIMITY_S:
                cands.add(f["m"])
        if len(cands) == 1:
            return cands.pop(), "proximity", None
        if len(cands) > 1:
            return None, None, "proximity_ambiguous"

    return None, None, "no_session_captured"


def tier_rows(rows, mapping, calls_by_id):
    """Partition into (tier_a, tier_b, withheld). Tier A gets .msclkid/.click_source set."""
    tier_a, tier_b, withheld = [], [], []
    for r in rows:
        m, source, reason = find_msclkid(r, mapping, calls_by_id)
        if m:
            r.msclkid = m
            r.click_source = source
            tier_a.append(r)
        elif r.email_hash or r.phone_hash:
            r.withheld_reason = reason
            tier_b.append(r)
        else:
            withheld.append(Dropped(r.goal, r.st_id, f"withheld: {reason}"))
    return tier_a, tier_b, withheld
