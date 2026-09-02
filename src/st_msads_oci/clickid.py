"""Join ServiceTitan conversion rows to captured msclkids via the Worker's KV export."""
import json
import re
import ssl
import urllib.request
from datetime import timedelta
from pathlib import Path

import certifi

from .rows import GOAL_BOOKED_JOB_CALL, Dropped, parse_utc

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

GOAL_BOOKED_JOB_NAME = "ServiceTitan Booked Job (Website) - MS"


def fetch_map(base_url, bearer, timeout=30):
    """Raises on any failure — the caller must abort the run rather than build empty Click Ids."""
    req = urllib.request.Request(base_url.rstrip("/") + "/map",
                                 headers={"Authorization": f"Bearer {bearer}", "User-Agent": "ms-oci-upload/1.0"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return json.loads(r.read().decode("utf-8"))


def publish_file(base_url, bearer, name, path, timeout=30):
    body = Path(path).read_bytes()
    # ponytail: brief's snippet omits User-Agent; Cloudflare 403s the default urllib UA
    # (same reason fetch_map sets it) — added here to match, or prod publish fails.
    req = urllib.request.Request(base_url.rstrip("/") + f"/f/{name}", data=body, method="PUT",
                                 headers={"Authorization": f"Bearer {bearer}",
                                          "Content-Type": "text/csv",
                                          "User-Agent": "ms-oci-upload/1.0"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.status == 204


def _last10(phones):
    for p in phones or []:
        d = re.sub(r"\D", "", p or "")
        if len(d) >= 10:
            return d[-10:]
    return None


def find_msclkid(row, mapping, calls_by_id):
    """Return (msclkid|None, source|None, withhold_reason|None). First match wins; ambiguity fails closed."""
    ids = mapping.get("ids", {})
    if row.email_hash and "e:" + row.email_hash in ids:
        return ids["e:" + row.email_hash]["m"], "email", None
    if row.phone_hash and "p:" + row.phone_hash in ids:
        return ids["p:" + row.phone_hash]["m"], "phone", None
    p10 = _last10(row.raw_phones)
    if p10 and "p10:" + p10 in ids:
        return ids["p10:" + p10]["m"], "phone10", None

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
            to10 = re.sub(r"\D", "", call["to"])[-10:]
            cands = set()
            for b in mapping.get("dni", {}).get(to10, []):
                bts = parse_utc(b["ts"])
                if bts <= received <= bts + timedelta(hours=POOL_WINDOW_H):
                    cands.add(b["m"])
            if len(cands) == 1:
                return cands.pop(), "call", None
            if len(cands) > 1:
                return None, None, "pool_ambiguous"

    if row.goal == GOAL_BOOKED_JOB_NAME:   # submit-time ≈ createdOn only holds for web bookings
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
