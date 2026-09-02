"""Booked-call goal (spec 2026-08-13): constant wiring, emission, tiering, e2e."""
from st_msads_oci.config import DEFAULT_GOAL_NAMES, DEFAULT_INITIAL_WATERMARK
from st_msads_oci.ledger import Ledger
from st_msads_oci.rows import (ALL_GOALS, GOAL_BOOKED_JOB, GOAL_COMPLETED_JOBS,
                               GOAL_BOOKED_JOB_CALL, parse_utc)
from tests.conftest import make_settings


def test_goal_string_exact():
    # The goal is now identified by a stable key; the byte-exact MS display name is
    # applied only at the CSV seam (see test_golden_csv_row).
    assert GOAL_BOOKED_JOB_CALL == "booked_call"
    assert GOAL_BOOKED_JOB_CALL in ALL_GOALS


def test_new_goal_watermark_defaults_to_initial():
    # Absent from a ledger file -> DEFAULT_INITIAL_WATERMARK -> full backfill (decision A)
    led = Ledger({"watermarks": {}})
    assert led.watermarks[GOAL_BOOKED_JOB_CALL] == parse_utc(DEFAULT_INITIAL_WATERMARK)


def test_watermark_roundtrips_through_save_load(tmp_path):
    p = tmp_path / "ledger.json"
    led = Ledger({})
    led.watermarks[GOAL_BOOKED_JOB_CALL] = parse_utc("2026-08-13T12:00:00+00:00")
    led.save(p)
    led2 = Ledger.load(p)
    assert led2.watermarks[GOAL_BOOKED_JOB_CALL] == parse_utc("2026-08-13T12:00:00+00:00")


from st_msads_oci.rows import rows_from_payload

CAMPAIGNS = [{"id": 100000001, "name": "Example - Performance Max - MS",
              "category": "Paid Microsoft"}]
BOOKED_CALL = {"id": 110000001, "receivedOn": "2026-08-12T13:37:54.266Z",
               "to": "5552166314", "from": "5552590001", "direction": "Inbound",
               "callType": "Booked", "campaign": {"id": 100000001},
               "customerName": "Pat Example", "email": "synthetic09@gmail.com",
               "phones": ["5552590001"]}


def _payload(calls):
    return {"campaigns": CAMPAIGNS, "bookings": [], "jobs": [], "calls": calls}


def test_booked_call_emits_row():
    rows = rows_from_payload(_payload([BOOKED_CALL]))
    (r,) = [r for r in rows if r.goal == GOAL_BOOKED_JOB_CALL]
    assert r.st_id == 110000001
    assert r.ts == parse_utc("2026-08-12T13:37:54.266Z")
    assert r.lead_call_id == 110000001          # points the DNI join at itself
    assert r.value is None                       # assemble.validate: no value on non-job goals
    assert r.campaign_category == "Paid Microsoft"
    assert r.raw_email == "synthetic09@gmail.com" and r.raw_phones == ["5552590001"]


def test_flat_campaign_id_also_resolves():
    call = dict(BOOKED_CALL, campaign=None, campaignId=100000001)
    (r,) = [r for r in rows_from_payload(_payload([call])) if r.goal == GOAL_BOOKED_JOB_CALL]
    assert r.campaign_category == "Paid Microsoft"


def test_non_booked_calls_emit_nothing():
    # legacy job-leadCall entries (no callType) and non-Booked callTypes stay row-less
    legacy = {"id": 5, "receivedOn": "2026-08-12T13:00:00Z", "to": "5552166314",
              "from": "5552590001", "direction": "Inbound", "campaign": {"id": 100000001}}
    excused = dict(BOOKED_CALL, id=6, callType="Excused")
    rows = rows_from_payload(_payload([legacy, excused]))
    assert not [r for r in rows if r.goal == GOAL_BOOKED_JOB_CALL]


from st_msads_oci.clickid import find_msclkid, tier_rows
from st_msads_oci.rows import UploadRow

E = "a" * 64
TS_CALL = parse_utc("2026-08-12T13:37:54.266Z")


def _call_row(**kw):
    d = dict(goal=GOAL_BOOKED_JOB_CALL, st_id=110000001, ts=TS_CALL,
             campaign_id=100000001, campaign_name="Example - Performance Max - MS",
             campaign_category="Paid Microsoft", customer_name="Pat Example",
             raw_email="synthetic09@gmail.com", raw_phones=["5552590001"],
             lead_call_id=110000001, booking_id=None, email_hash=E,
             phone_hash=None, value=None)
    d.update(kw)
    return UploadRow(**d)


CALLS_BY_ID = {110000001: {"id": 110000001, "receivedOn": "2026-08-12T13:37:54.266Z",
                           "to": "5552166314", "from": "5552590001",
                           "direction": "Inbound", "callType": "Booked"}}


def _mapping(bindings):
    # binding ts 30min before receivedOn -> inside POOL_WINDOW_H (2h)
    return {"ids": {}, "forms": [],
            "dni": {"5552166314": [{"ts": "2026-08-12T13:07:54Z", "m": m}
                                   for m in bindings]}}


def test_booked_call_with_pii_reaches_dni_branch_tier_a():
    got = find_msclkid(_call_row(), _mapping(["mclk_call_1"]), CALLS_BY_ID)
    assert got == ("mclk_call_1", "call", None)


def test_booked_call_join_miss_lands_tier_b():
    tier_a, tier_b, withheld = tier_rows([_call_row()], _mapping([]), CALLS_BY_ID)
    assert not tier_a and not withheld
    assert tier_b[0].withheld_reason == "no_session_captured"


def test_booked_call_ambiguous_pool_lands_tier_b():
    got = find_msclkid(_call_row(), _mapping(["m1", "m2"]), CALLS_BY_ID)
    assert got == (None, None, "pool_ambiguous")


def test_website_goal_with_pii_still_gated():
    # regression: the carve-out must NOT loosen the gate for other goals
    r = _call_row(goal=GOAL_BOOKED_JOB)
    got = find_msclkid(r, _mapping(["mclk_call_1"]), CALLS_BY_ID)
    assert got == (None, None, "no_session_captured")


from datetime import datetime, timezone

from st_msads_oci.assemble import InvariantError, assemble_csv, validate
from st_msads_oci.dedupe import dedupe
from st_msads_oci.filter import attach_hashes, filter_rows

NOW = datetime(2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc)


def test_value_none_passes_filter_and_validate():
    kept, dropped = filter_rows([_call_row(email_hash=None)], make_settings())
    assert len(kept) == 1 and not dropped     # $0 gate is Completed-Jobs-keyed
    kept, failed = attach_hashes(kept)
    assert len(kept) == 1
    validate(kept, now=NOW)                    # value=None on non-job goal: legal


def test_value_set_on_booked_call_raises():
    import pytest
    r = _call_row()
    r.value = 123.0
    with pytest.raises(InvariantError):
        validate([r], now=NOW)


def test_dedupe_same_call_id_and_cross_goal_survival():
    led = Ledger({})
    a, b = _call_row(), _call_row()            # same (goal, st_id)
    completed = _call_row(goal=GOAL_COMPLETED_JOBS, value=500.0)
    kept, dropped = dedupe([a, b, completed], led)
    assert {(r.goal, r.st_id) for r in kept} == {
        (GOAL_BOOKED_JOB_CALL, 110000001),
        (GOAL_COMPLETED_JOBS, 110000001)}  # different goals both survive
    assert dropped[0].reason == "duplicate within run (st id)"


def test_golden_csv_row(tmp_path):
    rows = rows_from_payload(_payload([BOOKED_CALL]))
    kept, _ = filter_rows(rows, make_settings())
    kept, _ = attach_hashes(kept)
    kept, _ = dedupe(kept, Ledger({}))
    tier_a, tier_b, withheld = tier_rows(kept, {"ids": {}, "dni": {}, "forms": []},
                                         {BOOKED_CALL["id"]: BOOKED_CALL})
    assert len(tier_b) == 1 and not tier_a and not withheld   # backfill shape: tier B PII
    validate(tier_b, now=NOW)
    out = tmp_path / "oci-pii.csv"
    assemble_csv(tier_b, out, DEFAULT_GOAL_NAMES)
    line = out.read_text().splitlines()[2]     # params, header, row
    cols = line.split(",")
    assert cols[1] == "ServiceTitan Booked Job (Call) - MS"   # byte-exact goal name
    assert cols[0] == "" and cols[3] == "" and cols[4] == ""  # no click id, no value/currency
    assert cols[5] and cols[6]                                # email + phone hashes present


def test_watermark_advances_on_booked_call_rows():
    led = Ledger({})
    rows = rows_from_payload(_payload([BOOKED_CALL]))
    led.advance_watermarks(rows)
    assert led.watermarks[GOAL_BOOKED_JOB_CALL] == parse_utc("2026-08-12T13:37:54.266Z")
