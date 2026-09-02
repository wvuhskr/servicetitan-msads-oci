from datetime import timedelta
from st_msads_oci.config import DEFAULT_INITIAL_WATERMARK
from st_msads_oci.ledger import Ledger, seed_from_result_csv, parse_result_rows
from st_msads_oci.rows import GOAL_COMPLETED_JOBS, parse_utc
from tests.conftest import make_settings

JUNE = "tests/fixtures/june_result.csv"
_LEGACY = make_settings().legacy_goal_names
GOAL_BOOKINGS = _LEGACY[0]  # "ServiceTitan Integrated Bookings - MS" — legacy, still present in old result files
GOAL_LEAD = _LEGACY[1]


def test_parse_result_rows_counts():
    rows = parse_result_rows(JUNE, make_settings())
    assert len(rows) == 29
    assert sum(1 for r in rows if r["status"] == "Success") == 7
    assert {r["goal"] for r in rows} == {GOAL_BOOKINGS, GOAL_COMPLETED_JOBS, GOAL_LEAD}


def test_seed_and_identity_recent():
    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE, make_settings())
    assert len(ledger.uploaded) == 29
    # known June booking (6/26/2026 3:39:52 AM UTC), synthesized hashes from the June file
    # (sha256("user16@example.com") / sha256("+15555550016"), row 16 of 29 ServiceTitan rows)
    eh = "e21bf8264d12f4176c0593b02e1cdd69f8b5fcc779f194a8a21187e816b1ca51"
    ph = "4f0ea3eb6fd7ec6959af769bb54f01dd9aa20012ad94fdd764b4db1a56bfa0a5"
    ts = parse_utc("2026-06-26T03:39:52Z")
    assert ledger.identity_recent(GOAL_BOOKINGS, eh, ph, ts + timedelta(hours=1), hours=24)
    assert not ledger.identity_recent(GOAL_BOOKINGS, eh, ph, ts + timedelta(days=3), hours=24)
    assert not ledger.identity_recent(GOAL_LEAD, eh, ph, ts, hours=24)  # goal-scoped


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE, make_settings())
    ledger.save(p)
    again = Ledger.load(p)
    assert len(again.uploaded) == 29
    assert again.watermarks[GOAL_COMPLETED_JOBS] == parse_utc(DEFAULT_INITIAL_WATERMARK)


def test_load_missing_file_gives_initial_watermarks(tmp_path):
    ledger = Ledger.load(tmp_path / "nope.json")
    assert ledger.uploaded == []
    assert ledger.watermarks[GOAL_COMPLETED_JOBS] == parse_utc(DEFAULT_INITIAL_WATERMARK)


def test_parse_result_rows_iso_timestamp_format(tmp_path):
    """Test parsing ISO 8601 timestamp format (2026-06-10T06:10:56Z) from result files."""
    csv_path = tmp_path / "iso_result.csv"
    # CSV columns: [0]=clickid, [1]=goal, [2]=time, [3]=value, [4]=currency, [5]=email, [6]=phone, [7]=status
    csv_content = """,ServiceTitan Integrated Bookings - MS,2026-06-10T06:10:56Z,,,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,Success"""
    csv_path.write_text(csv_content)

    rows = parse_result_rows(csv_path, make_settings())
    assert len(rows) == 1
    assert rows[0]["goal"] == GOAL_BOOKINGS
    assert rows[0]["ts"] == "2026-06-10T06:10:56+00:00"
    assert rows[0]["status"] == "Success"


# test_migration_preserves_booking_watermark deliberately NOT carried over: it exercises
# scripts/migrate_goals.py, a one-time historical migration tool for the internal repo's
# own goal-name history. The task brief's exclusion list omits `scripts/` on purpose
# (confirmed against docs/superpowers/plans/2026-09-02-servicetitan-msads-oci-v1.md,
# where migrate_goals.py never reappears in any of the 16 tasks) and this new repo has
# no legacy ledger.json to migrate. See Task 2 report for detail.


def test_project_overlap_and_pending_roundtrip(tmp_path):
    from st_msads_oci.rows import UploadRow
    ledger = Ledger()
    row = UploadRow(goal=GOAL_COMPLETED_JOBS, st_id=100, ts=parse_utc("2026-08-01T12:00:00Z"),
                    campaign_id=1, campaign_name="Example Brand - Search - MS",
                    campaign_category="Paid Microsoft", customer_name="Jane",
                    raw_email="jane@x.com", raw_phones=[], value=17117.0,
                    project_id=555, member_job_ids=[100, 101])
    row.phone_hash = "b" * 64
    ledger.add_row(row, "2026-08-01", tier="B")
    assert ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 555, [999])       # project id match
    assert ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 777, [101, 999])  # member overlap
    assert ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 777, [100])       # st_id in members
    assert not ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 777, [999])
    assert not ledger.has_project_overlap(GOAL_LEAD, 555, [100])             # goal-scoped
    ledger.pending_projects = [{"project_id": 42, "first_seen": "2026-08-01"}]
    p = tmp_path / "ledger.json"
    ledger.save(p)
    again = Ledger.load(p)
    assert again.pending_projects == [{"project_id": 42, "first_seen": "2026-08-01"}]
    assert again.uploaded[0]["project_id"] == 555
    assert again.uploaded[0]["member_job_ids"] == [100, 101]


def test_project_overlap_tolerates_legacy_entries():
    # Historical ledger entries have no project_id / member_job_ids keys — must not crash,
    # and a legacy per-job st_id must still block its project (spec edge 7).
    ledger = Ledger()
    ledger.uploaded = [{"goal": GOAL_COMPLETED_JOBS, "st_id": 110000003,
                        "ts": "2026-07-30T10:00:00+00:00", "email_hash": None,
                        "phone_hash": None, "campaign_name": "x",
                        "uploaded_on": "2026-07-30", "status": None}]
    assert ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 9999, [110000003, 5])
    assert not ledger.has_project_overlap(GOAL_COMPLETED_JOBS, 9999, [5])


def test_pending_projects_default_empty(tmp_path):
    assert Ledger.load(tmp_path / "nope.json").pending_projects == []


def test_add_row_persists_recovered_via():
    from st_msads_oci.rows import UploadRow
    ledger = Ledger()
    row = UploadRow(GOAL_COMPLETED_JOBS, 1, parse_utc("2026-08-01T10:00:00Z"), 1,
                    "Example Brand - Search - MS", "Paid Microsoft", "Carol", "c@x.com")
    row.recovered_via = "pii"
    ledger.add_row(row, "2026-08-05", tier="B")
    assert ledger.uploaded[-1]["recovered_via"] == "pii"
    plain = UploadRow(GOAL_COMPLETED_JOBS, 2, parse_utc("2026-08-01T10:00:00Z"), 1,
                      "Example Brand - Search - MS", "Paid Microsoft", "Jane", "j@x.com")
    ledger.add_row(plain, "2026-08-05", tier="A")
    assert ledger.uploaded[-1]["recovered_via"] is None
