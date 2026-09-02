import shutil
import zipfile
from datetime import datetime, timezone
from st_msads_oci.ledger import Ledger, seed_from_result_csv
from st_msads_oci.rows import UploadRow
from st_msads_oci.triage import run_triage, campaign_type, find_result_files, _match_ledger_entry
from tests.conftest import make_settings

GOAL_BOOKINGS = make_settings().legacy_goal_names[0]  # "ServiceTitan Integrated Bookings - MS" — legacy, still in old result files

JUNE = "tests/fixtures/june_result.csv"


def test_campaign_type():
    assert campaign_type("Example - Performance Max - MS") == "PMax"
    assert campaign_type("Example Brand - Search - MS") == "Search"
    assert campaign_type(None) == "unknown"


def test_find_result_files_matches_pattern(tmp_path):
    inbox, downloads = tmp_path / "inbox", tmp_path / "dl"
    inbox.mkdir(); downloads.mkdir()
    shutil.copy(JUNE, inbox / "OfflineConvABC_AllResultFile.csv")
    (downloads / "unrelated.csv").write_text("x")
    found = find_result_files(inbox, downloads, processed_names=set())
    assert [p.name for p in found] == ["OfflineConvABC_AllResultFile.csv"]


def test_run_triage_updates_statuses_and_history(tmp_path):
    inbox, downloads, archive = tmp_path / "inbox", tmp_path / "dl", tmp_path / "arch"
    inbox.mkdir(); downloads.mkdir(); archive.mkdir()
    shutil.copy(JUNE, inbox / "OfflineConvABC_AllResultFile.csv")
    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE, make_settings())
    for e in ledger.uploaded:
        e["status"] = None  # pretend statuses unknown until triage
    history = tmp_path / "history.csv"
    findings = run_triage(ledger, inbox, downloads, archive, history, parsed_on="2026-07-06", settings=make_settings())
    assert sum(1 for e in ledger.uploaded if e["status"] == "Success") == 7
    assert (archive / "OfflineConvABC_AllResultFile.csv").exists()
    assert not (inbox / "OfflineConvABC_AllResultFile.csv").exists()
    assert "campaign_type" in history.read_text().splitlines()[0]
    assert any("HASH ALARM" in f for f in findings)  # June file has stableId-null rows


def test_match_ledger_entry_ignores_microsecond_mismatch():
    # ServiceTitan timestamps carry fractional seconds (7-digit sub-second, per real payloads);
    # Microsoft's result file echoes back whole seconds only. A row added via Ledger.add_row
    # must still be matched by _match_ledger_entry (and get its status updated) even though
    # the stored ts string has microseconds the result row's ts string lacks.
    ledger = Ledger()
    row = UploadRow(
        goal=GOAL_BOOKINGS, st_id=999,
        ts=datetime(2026, 6, 26, 3, 39, 52, 406670, tzinfo=timezone.utc),
        campaign_id=1, campaign_name="Example Brand - Search - MS", campaign_category="Paid Microsoft",
        customer_name="Test Customer", raw_email=None, raw_phones=[],
        email_hash="e21bf8264d12f4176c0593b02e1cdd69f8b5fcc779f194a8a21187e816b1ca51",
        phone_hash="4f0ea3eb6fd7ec6959af769bb54f01dd9aa20012ad94fdd764b4db1a56bfa0a5",
    )
    ledger.add_row(row, uploaded_on="2026-06-26")

    result_row = {
        "goal": GOAL_BOOKINGS,
        "ts": "2026-06-26T03:39:52+00:00",  # whole seconds, as echoed by MS result file
        "email_hash": row.email_hash,
        "phone_hash": row.phone_hash,
        "status": "Success",
    }
    entry = _match_ledger_entry(ledger, result_row)
    assert entry is not None
    entry["status"] = result_row["status"]
    assert ledger.uploaded[0]["status"] == "Success"


def test_zip_result_file_is_archived_and_not_reprocessed(tmp_path):
    inbox, downloads, archive = tmp_path / "inbox", tmp_path / "dl", tmp_path / "arch"
    inbox.mkdir(); downloads.mkdir(); archive.mkdir()

    zip_path = inbox / "OfflineConvZZZ_AllResultFile.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        # inner csv keeps its own (different) name from the zip
        z.write(JUNE, arcname="june_result.csv")

    history = tmp_path / "history.csv"

    # Run 1
    ledger1 = Ledger()
    ledger1.uploaded = seed_from_result_csv(JUNE, make_settings())
    for e in ledger1.uploaded:
        e["status"] = None
    findings1 = run_triage(ledger1, inbox, downloads, archive, history, parsed_on="2026-07-06", settings=make_settings())

    assert not (inbox / "OfflineConvZZZ_AllResultFile.zip").exists()
    assert (archive / "OfflineConvZZZ_AllResultFile.zip").exists()

    history_lines_after_run1 = history.read_text().splitlines()
    assert len(history_lines_after_run1) > 1
    # history must be keyed by the ZIP's name, not the inner csv's name
    for row in history_lines_after_run1[1:]:
        assert row.split(",")[1] == "OfflineConvZZZ_AllResultFile.zip"
    assert not any("june_result.csv" in row for row in history_lines_after_run1)
    assert any("HASH ALARM" in f for f in findings1)

    # Run 2: same history path, freshly-seeded ledger, same (now-empty) inbox.
    # The zip is gone from inbox, so nothing should be reprocessed.
    ledger2 = Ledger()
    ledger2.uploaded = seed_from_result_csv(JUNE, make_settings())
    for e in ledger2.uploaded:
        e["status"] = None
    findings2 = run_triage(ledger2, inbox, downloads, archive, history, parsed_on="2026-07-06", settings=make_settings())

    history_lines_after_run2 = history.read_text().splitlines()
    assert len(history_lines_after_run2) == len(history_lines_after_run1)
    assert not any("HASH ALARM" in f for f in findings2)


def test_malformed_result_file_skip_and_report(tmp_path):
    """Test that a malformed result file (unparseable timestamp) is skipped with a finding and doesn't crash."""
    inbox, downloads, archive = tmp_path / "inbox", tmp_path / "dl", tmp_path / "arch"
    inbox.mkdir(); downloads.mkdir(); archive.mkdir()

    # Create a good file (will be processed)
    shutil.copy(JUNE, inbox / "OfflineConvGOOD_AllResultFile.csv")

    # Create a bad file with unparseable timestamp
    bad_path = inbox / "OfflineConvBAD_AllResultFile.csv"
    # CSV columns: [0]=clickid, [1]=goal, [2]=time, [3]=value, [4]=currency, [5]=email, [6]=phone, [7]=status
    bad_content = """,ServiceTitan Integrated Bookings - MS,NOT A TIME,,,aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb,Success"""
    bad_path.write_text(bad_content)

    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE, make_settings())
    for e in ledger.uploaded:
        e["status"] = None

    history = tmp_path / "history.csv"
    findings = run_triage(ledger, inbox, downloads, archive, history, parsed_on="2026-07-06", settings=make_settings())

    # Bad file should NOT be archived
    assert (inbox / "OfflineConvBAD_AllResultFile.csv").exists()
    # Good file should be archived
    assert (archive / "OfflineConvGOOD_AllResultFile.csv").exists()
    assert not (inbox / "OfflineConvGOOD_AllResultFile.csv").exists()

    # Finding should mention the bad file
    assert any("malformed result file: OfflineConvBAD_AllResultFile.csv" in f for f in findings)

    # History should only contain rows for the good file (29 rows of June data)
    history_content = history.read_text()
    assert "OfflineConvGOOD_AllResultFile.csv" in history_content
    assert "OfflineConvBAD_AllResultFile.csv" not in history_content


def test_run_triage_emits_per_status_summary(tmp_path):
    # The email must answer "why the errors" without a manual session:
    # one finding per result file breaking rows down by status.
    inbox, downloads, archive = tmp_path / "inbox", tmp_path / "dl", tmp_path / "arch"
    inbox.mkdir(); downloads.mkdir(); archive.mkdir()
    shutil.copy(JUNE, inbox / "OfflineConvABC_AllResultFile.csv")
    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE, make_settings())
    findings = run_triage(ledger, inbox, downloads, archive, tmp_path / "h.csv",
                          parsed_on="2026-07-06", settings=make_settings())
    summary = [f for f in findings if f.startswith("result OfflineConvABC_AllResultFile.csv:")]
    assert len(summary) == 1
    s = summary[0]
    assert "29 rows" in s
    assert "Success 7" in s
    assert "Unattributed Reason: Click not found 16" in s
    assert "Unattributed Reason:ClickId and stableId are null 6" in s
