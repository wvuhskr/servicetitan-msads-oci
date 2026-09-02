import json
from datetime import datetime, timezone
from pathlib import Path

from st_msads_oci.clickid import find_msclkid, tier_rows, POOL_WINDOW_H, PROXIMITY_S
from st_msads_oci.rows import UploadRow, GOAL_COMPLETED_JOBS, parse_utc

MAPPING = json.loads((Path(__file__).parent / "fixtures" / "clickid_map.json").read_text())
E = "a" * 64
P = "b" * 64
TS = datetime(2026, 7, 22, 15, 0, 30, tzinfo=timezone.utc)

def row(**kw):
    d = dict(goal="ServiceTitan Booked Job (Website) - MS", st_id=1, ts=TS, campaign_id=1,
             campaign_name="x", campaign_category="Paid Microsoft", customer_name="c",
             raw_email=None, raw_phones=[], lead_call_id=None, booking_id=None,
             email_hash=None, phone_hash=None, value=None)
    d.update(kw)
    return UploadRow(**d)

def test_email_hash_wins():
    assert find_msclkid(row(email_hash=E), MAPPING, {}) == ("click_email", "email", None)

def test_phone_hash():
    assert find_msclkid(row(phone_hash=P), MAPPING, {}) == ("click_phone", "phone", None)

def test_phone_last10_fallback():
    assert find_msclkid(row(raw_phones=["+1 (555) 555-0111"]), MAPPING, {}) == ("click_p10", "phone10", None)

def test_call_join_single_candidate():
    calls = {77: {"receivedOn": "2026-07-21T10:30:00Z", "to": "5552596637", "direction": "Inbound"}}
    r = row(goal=GOAL_COMPLETED_JOBS, lead_call_id=77)
    assert find_msclkid(r, MAPPING, calls) == ("click_call", "call", None)

def test_call_join_ambiguous_fails_closed():
    calls = {77: {"receivedOn": "2026-07-21T11:00:00Z", "to": "5552596681", "direction": "Inbound"}}
    r = row(goal=GOAL_COMPLETED_JOBS, lead_call_id=77)
    assert find_msclkid(r, MAPPING, calls) == (None, None, "pool_ambiguous")

def test_call_outside_pool_window():
    calls = {77: {"receivedOn": "2026-07-25T09:00:01Z", "to": "5552596637", "direction": "Inbound"}}
    r = row(goal=GOAL_COMPLETED_JOBS, lead_call_id=77)
    assert find_msclkid(r, MAPPING, calls) == (None, None, "no_session_captured")

def test_proximity_booked_job_only():
    assert find_msclkid(row(), MAPPING, {})[0] == "click_prox"          # Booked Job, ±120s → match
    r = row(goal=GOAL_COMPLETED_JOBS)                                    # completedOn ≠ submit time
    assert find_msclkid(r, MAPPING, {}) == (None, None, "no_session_captured")

def test_tiering():
    a = row(st_id=1, email_hash=E)
    b = row(st_id=2, email_hash="c" * 64)   # hash present, no binding → Tier B
    w = row(st_id=3, ts=datetime(2026, 1, 1, tzinfo=timezone.utc))  # nothing → withheld
    tier_a, tier_b, withheld = tier_rows([a, b, w], MAPPING, {})
    assert [r.st_id for r in tier_a] == [1] and tier_a[0].msclkid == "click_email"
    assert [r.st_id for r in tier_b] == [2]
    assert len(withheld) == 1 and "no_session_captured" in withheld[0].reason

def test_recovered_call_row_bypasses_identity_gate():
    row = UploadRow(GOAL_COMPLETED_JOBS, 100, parse_utc("2026-07-15T09:05:00Z"), 1,
                    "Example Brand - Search - MS", "Paid Microsoft", "Carol", "carol@x.com",
                    ["5556055551"], lead_call_id=8)
    row.phone_hash = "beef" * 16    # identity present but NOT in the map
    row.recovered_via = "call"
    calls_by_id = {8: {"id": 8, "direction": "Inbound", "to": "5552224444",
                       "receivedOn": "2026-07-15T09:00:00Z"}}
    mapping = {"ids": {}, "dni": {"5552224444": [{"ts": "2026-07-15T08:30:00Z", "m": "MCLK123"}]},
               "forms": []}
    m, source, reason = find_msclkid(row, mapping, calls_by_id)
    assert (m, source, reason) == ("MCLK123", "call", None)


def test_non_recovered_row_keeps_identity_gate():
    row = UploadRow(GOAL_COMPLETED_JOBS, 101, parse_utc("2026-07-15T09:05:00Z"), 1,
                    "Example Brand - Search - MS", "Paid Microsoft", "Jane", "j@x.com",
                    ["5555550001"], lead_call_id=8)
    row.phone_hash = "beef" * 16
    calls_by_id = {8: {"id": 8, "direction": "Inbound", "to": "5552224444",
                       "receivedOn": "2026-07-15T09:00:00Z"}}
    mapping = {"ids": {}, "dni": {"5552224444": [{"ts": "2026-07-15T08:30:00Z", "m": "MCLK123"}]},
               "forms": []}
    m, source, reason = find_msclkid(row, mapping, calls_by_id)
    assert (m, source, reason) == (None, None, "no_session_captured")
