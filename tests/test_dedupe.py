from datetime import timedelta
from st_msads_oci.dedupe import dedupe
from st_msads_oci.ledger import Ledger
from st_msads_oci.rows import UploadRow, GOAL_BOOKED_JOB, parse_utc

T0 = parse_utc("2026-07-01T10:00:00Z")
EH1, PH1 = "a" * 64, "b" * 64
EH2, PH2 = "c" * 64, "d" * 64


def row(goal, st_id, ts, eh, ph):
    return UploadRow(goal, st_id, ts, 1, "Example Brand - Search - MS", "Paid Microsoft",
                     "X", "x@x.com", [], email_hash=eh, phone_hash=ph)


def test_rapid_resubmits_collapse_to_earliest():
    rows = [row(GOAL_BOOKED_JOB, 1, T0, EH1, PH1),
            row(GOAL_BOOKED_JOB, 2, T0 + timedelta(minutes=1), EH1, PH1),
            row(GOAL_BOOKED_JOB, 3, T0 + timedelta(minutes=2), EH1, PH1)]
    kept, dropped = dedupe(rows, Ledger())
    assert [r.st_id for r in kept] == [1]
    assert {d.st_id for d in dropped} == {2, 3}


def test_ledger_blocks_reupload():
    ledger = Ledger()
    prior = row(GOAL_BOOKED_JOB, 1, T0, EH1, PH1)
    ledger.add_row(prior, "2026-07-01")
    rows = [row(GOAL_BOOKED_JOB, 1, T0, EH1, PH1),                       # same st id
            row(GOAL_BOOKED_JOB, 9, T0 + timedelta(hours=2), EH1, PH1)]  # same identity within 24h
    kept, dropped = dedupe(rows, ledger)
    assert kept == []
    reasons = {d.st_id: d.reason for d in dropped}
    assert "st id" in reasons[1] and "identity" in reasons[9]


def test_same_identity_far_apart_both_kept():
    rows = [row(GOAL_BOOKED_JOB, 1, T0, EH1, PH1),
            row(GOAL_BOOKED_JOB, 2, T0 + timedelta(days=10), EH1, PH1)]
    kept, _ = dedupe(rows, Ledger())
    assert {r.st_id for r in kept} == {1, 2}


def test_same_batch_duplicate_st_id_collapses():
    rows = [row(GOAL_BOOKED_JOB, 1, T0, EH1, PH1),
            row(GOAL_BOOKED_JOB, 1, T0 + timedelta(days=10), EH1, PH1)]
    kept, dropped = dedupe(rows, Ledger())
    assert [r.st_id for r in kept] == [1] and len(kept) == 1
    assert dropped and "st id" in dropped[0].reason


def test_same_batch_duplicate_st_id_no_hashes():
    a = row(GOAL_BOOKED_JOB, 7, T0, None, None)
    b = row(GOAL_BOOKED_JOB, 7, T0 + timedelta(hours=1), None, None)
    kept, dropped = dedupe([a, b], Ledger())
    assert len(kept) == 1 and len(dropped) == 1
