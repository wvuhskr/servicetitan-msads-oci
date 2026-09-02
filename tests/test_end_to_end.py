import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skip(reason="CLI arrives in Task 9")

from st_msads_oci.ledger import Ledger, seed_from_result_csv

JUNE = "tests/fixtures/june_result.csv"

PAYLOAD = {
    "generated_at": "2026-07-06T12:00:00Z",
    "campaigns": [
        {"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"},
        {"id": 2, "name": "HVAC - Search - GAds", "category": "Paid Google"},
    ],
    "bookings": [
        # kept — ServiceTitan-style fractional-second timestamp (7-digit sub-second)
        {"id": 10, "createdOn": "2026-07-01T14:00:00.4066702Z", "campaignId": 1,
         "customerName": "Jane Doe", "email": "Jane.Doe+x@Yahoo.com", "phones": ["(555) 555-0001"]},
        # rapid resubmits -> dropped
        {"id": 11, "createdOn": "2026-07-01T14:01:00Z", "campaignId": 1,
         "customerName": "Jane Doe", "email": "jane.doe@yahoo.com", "phones": ["(555) 555-0001"]},
        # non-MS -> dropped
        {"id": 12, "createdOn": "2026-07-01T14:02:00Z", "campaignId": 2,
         "customerName": "G Person", "email": "g@x.com", "phones": []},
        # test identity -> dropped
        {"id": 13, "createdOn": "2026-07-01T14:03:00Z", "campaignId": 1,
         "customerName": "Art Vandelay", "email": "synthetic05@example.com", "phones": []},
    ],
    "jobs": [
        {"id": 20, "completedOn": "2026-07-02T15:00:00Z", "campaignId": 1, "customerName": "Web Job",
         "email": "webjob@x.com", "phones": [], "invoiceTotal": 750.25, "leadCallId": None, "bookingId": 5},
        {"id": 21, "completedOn": "2026-07-02T15:30:00Z", "campaignId": 1, "customerName": "Call Job",
         "email": "calljob@x.com", "phones": [], "invoiceTotal": 900.0, "leadCallId": 7, "bookingId": None},
    ],
}


def run_cli(project_dir, payload_path, now="2026-07-06T12:00:00Z"):
    import os
    secrets_path = project_dir / "secrets.env"
    if not secrets_path.exists():
        secrets_path.write_text("OCI_WORKER_URL=https://example.invalid\nOCI_WORKER_BEARER=x\n"
                                 "OCI_EMAIL_TO=synthetic00@example.com\n")
    map_path = project_dir / "oci_map.json"
    if not map_path.exists():
        map_path.write_text(json.dumps({"ids": {}, "dni": {}, "forms": []}))
    env = {**os.environ, "OCI_DOWNLOADS_DIR": str(project_dir / "no_downloads"),
           "OCI_MAP_FILE": str(map_path)}
    return subprocess.run(
        [sys.executable, "build_upload.py", "--input", str(payload_path),
         "--project-dir", str(project_dir), "--no-email", "--now", now],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env)


def test_end_to_end_and_idempotency(tmp_path):
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(PAYLOAD))
    result = run_cli(tmp_path, payload_path)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    # booking 10 + job 20 + call-origin job 21 (now unlocked); empty map fixture -> no click-id
    # matches, so all 3 land in Tier B (PII-only).
    assert summary["tier_a"] == 0
    assert summary["tier_b"] == 3
    assert summary["withheld"] == 0
    csv_text = (tmp_path / "output" / "oci-pii.csv").read_text()
    assert csv_text.startswith("Parameters:TimeZone=+0000\n")
    assert csv_text.count("ServiceTitan Booked Job (Website) - MS") == 1
    assert "750.25,USD" in csv_text
    clickid_text = (tmp_path / "output" / "oci-clickid.csv").read_text()
    assert clickid_text == "Parameters:TimeZone=+0000\n" + (
        "Microsoft Click Id,Conversion Name,Conversion Time,Conversion Value,Conversion Currency,"
        "Hashed Email Address,Hashed Phone Number\n")
    # second run over the same payload: ledger blocks everything
    result2 = run_cli(tmp_path, payload_path)
    summary2 = json.loads(result2.stdout.strip().splitlines()[-1])
    assert summary2["tier_a"] == 0 and summary2["tier_b"] == 0 and summary2["withheld"] == 0
    # watermarks advanced
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    assert ledger["watermarks"]["ServiceTitan Completed Jobs - MS"].startswith("2026-07-02T15:30")


def test_contactless_call_origin_row_withheld_not_validated(tmp_path):
    # Carried finding from Task 5 review: a contactless call-origin row (lead_call_id only,
    # no email/phone hashes) that fails the click-id join must land in `withheld` and never
    # reach validate() — build() must not raise.
    payload = {
        "generated_at": "2026-07-06T12:00:00Z",
        "campaigns": [{"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"}],
        "bookings": [],
        "jobs": [{"id": 30, "completedOn": "2026-07-02T15:00:00Z", "campaignId": 1,
                  "customerName": "No Contact", "email": None, "phones": [],
                  "invoiceTotal": 500.0, "leadCallId": 999, "bookingId": None}],
    }
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(payload))
    result = run_cli(tmp_path, payload_path)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["tier_a"] == 0 and summary["tier_b"] == 0 and summary["withheld"] == 1


def test_alert_mode_send_failure_exits_2_with_json(tmp_path):
    # no secrets.env in tmp project dir -> send path fails -> graceful contract
    result = subprocess.run(
        [sys.executable, "build_upload.py", "--alert", "boom",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 2
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary == {"alert": "boom", "emailed": False}


def test_triage_state_saved_even_when_build_fails_after_triage(tmp_path):
    # Seed a ledger with statuses unset, drop a matching result file in the inbox, then
    # run the CLI with a payload path that does not exist so build() raises after run_triage()
    # has already archived the file and mutated ledger entries in memory. The updated
    # statuses must survive the crash — triage progress should not be silently discarded.
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_path = state_dir / "ledger.json"
    ledger = Ledger()
    ledger.uploaded = seed_from_result_csv(JUNE)
    for e in ledger.uploaded:
        e["status"] = None
    ledger.save(state_path)

    inbox = tmp_path / "results" / "inbox"
    inbox.mkdir(parents=True)
    shutil.copy(JUNE, inbox / "OfflineConvABC_AllResultFile.csv")

    missing_payload = tmp_path / "does_not_exist.json"
    result = run_cli(tmp_path, missing_payload)

    assert result.returncode == 2
    saved = json.loads(state_path.read_text())
    assert sum(1 for e in saved["uploaded"] if e["status"] == "Success") == 7
    assert (tmp_path / "results" / "archive" / "OfflineConvABC_AllResultFile.csv").exists()


def test_missing_input_and_alert_exits_2_with_clean_message(tmp_path):
    # Neither --input nor --alert given: must fail fast with a clean usage error,
    # not crash deep inside build() with an unhandled TypeError traceback.
    result = subprocess.run(
        [sys.executable, "build_upload.py", "--project-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 2
    assert "TypeError" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr.strip() != ""


def test_alert_mode_no_email_exits_0(tmp_path):
    result = subprocess.run(
        [sys.executable, "build_upload.py", "--alert", "boom", "--no-email",
         "--project-dir", str(tmp_path)],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary == {"alert": "boom", "emailed": False}


PROJECT_PAYLOAD = {
    "generated_at": "2026-08-03T12:00:00Z",
    "campaigns": [
        {"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"},
        {"id": 2, "name": "HVAC - Search - GAds", "category": "Paid Google"},
    ],
    "bookings": [],
    "jobs": [
        # project 5001: $0 Paid-MS lead + $17,117 install stamped Google (spec edges 1+6)
        {"id": 200, "completedOn": "2026-07-25T09:00:00Z", "campaignId": 1, "customerName": "Jane",
         "email": "jane@x.com", "phones": [], "invoiceTotal": 0.0, "leadCallId": 9,
         "bookingId": None, "projectId": 5001},
        {"id": 201, "completedOn": "2026-08-02T10:00:00Z", "campaignId": 2, "customerName": "Jane",
         "email": None, "phones": [], "invoiceTotal": 17117.0, "leadCallId": None,
         "bookingId": None, "projectId": 5001},
        # project 5002: Google lead + install stamped Paid-MS -> excluded entirely (edge 5)
        {"id": 300, "completedOn": "2026-07-26T09:00:00Z", "campaignId": 2, "customerName": "G",
         "email": "g@x.com", "phones": [], "invoiceTotal": 0.0, "leadCallId": 8,
         "bookingId": None, "projectId": 5002},
        {"id": 301, "completedOn": "2026-08-01T10:00:00Z", "campaignId": 1, "customerName": "G",
         "email": None, "phones": [], "invoiceTotal": 2513.0, "leadCallId": None,
         "bookingId": None, "projectId": 5002},
    ],
    "project_jobs": {
        "5001": [
            {"id": 200, "jobStatus": "Completed", "total": 0.0,
             "completedOn": "2026-07-25T09:00:00Z", "createdOn": "2026-07-01T08:00:00Z",
             "campaignId": 1, "leadCallId": 9, "bookingId": None, "partnerLeadCallId": None,
             "createdFromEstimateId": None, "customerName": "Jane", "email": "jane@x.com",
             "phones": []},
            {"id": 201, "jobStatus": "Completed", "total": 17117.0,
             "completedOn": "2026-08-02T10:00:00Z", "createdOn": "2026-07-10T08:00:00Z",
             "campaignId": 2, "leadCallId": None, "bookingId": None, "partnerLeadCallId": None,
             "createdFromEstimateId": 44},
        ],
        "5002": [
            {"id": 300, "jobStatus": "Completed", "total": 0.0,
             "completedOn": "2026-07-26T09:00:00Z", "createdOn": "2026-07-02T08:00:00Z",
             "campaignId": 2, "leadCallId": 8, "bookingId": None, "partnerLeadCallId": None,
             "createdFromEstimateId": None, "customerName": "G", "email": "g@x.com",
             "phones": []},
            {"id": 301, "jobStatus": "Completed", "total": 2513.0,
             "completedOn": "2026-08-01T10:00:00Z", "createdOn": "2026-07-12T08:00:00Z",
             "campaignId": 1, "leadCallId": None, "bookingId": None, "partnerLeadCallId": None,
             "createdFromEstimateId": 45},
        ],
    },
}


def test_project_rollup_end_to_end(tmp_path):
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(PROJECT_PAYLOAD))
    result = run_cli(tmp_path, payload_path, now="2026-08-03T12:00:00Z")
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    # one project row (5001); 5002 dropped at the campaign filter; no per-job install rows
    assert summary["tier_a"] == 0 and summary["tier_b"] == 1
    assert summary["project_findings"] == 0
    csv_text = (tmp_path / "output" / "oci-pii.csv").read_text()
    assert csv_text.count("ServiceTitan Completed Jobs - MS") == 1
    assert "17117.00,USD" in csv_text
    assert "2513" not in csv_text
    assert "7/25/2026 9:00:00 AM" in csv_text       # conversion time = LEAD completedOn
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    entry = next(e for e in ledger["uploaded"] if e.get("project_id") == 5001)
    assert entry["st_id"] == 200 and entry["member_job_ids"] == [200, 201]
    # watermark still advances on job completedOn (install leg, 8/2) — unchanged semantics
    assert ledger["watermarks"]["ServiceTitan Completed Jobs - MS"].startswith("2026-08-02T10:00")
    assert ledger["pending_projects"] == []
    # second run: ledger blocks the project (id + members)
    result2 = run_cli(tmp_path, payload_path, now="2026-08-03T12:00:00Z")
    summary2 = json.loads(result2.stdout.strip().splitlines()[-1])
    assert summary2["tier_a"] == 0 and summary2["tier_b"] == 0


def test_empty_run_republishes_cumulative_window(tmp_path):
    # Day 1: rows land and a dated CSV is written. Day 2 (2 days later): same payload, ledger
    # blocks everything (0 new rows, no dated CSV written) — but the served oci-pii.csv must be
    # REBUILT from the still-in-window day-1 dated file, not left empty. This is the overwrite-race fix.
    payload_path = tmp_path / "input.json"
    payload_path.write_text(json.dumps(PAYLOAD))
    r1 = run_cli(tmp_path, payload_path, now="2026-07-06T12:00:00Z")
    assert r1.returncode == 0, r1.stderr
    assert (tmp_path / "output" / "oci-pii_2026-07-06.csv").exists()

    r2 = run_cli(tmp_path, payload_path, now="2026-07-08T12:00:00Z")
    assert r2.returncode == 0, r2.stderr
    summary2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert summary2["tier_a"] == 0 and summary2["tier_b"] == 0  # nothing new
    assert not (tmp_path / "output" / "oci-pii_2026-07-08.csv").exists()  # no new dated file
    # served latest STILL carries day-1's rows (rebuilt from the in-window dated file)
    pii = (tmp_path / "output" / "oci-pii.csv").read_text()
    assert pii.count("ServiceTitan Booked Job (Website) - MS") == 1
    assert "750.25,USD" in pii


def test_later_day_with_new_rows_does_not_clobber_earlier_still_in_window_rows(tmp_path):
    # Reproduces the confirmed 8/13->8/14 production incident (.git/sdd/progress.md): a day WITH
    # its own new tier_b rows used to overwrite oci-pii.csv with ONLY that day's rows via a bare
    # assemble_csv() call, silently destroying an earlier day's still-unpulled rows that happened
    # to still be sitting in the "latest" alias. The brief's own test can't catch this — its day 2
    # has zero new rows on both tiers, so the old `if tier_a or tier_b:` block never runs at all
    # and the file is (accidentally) left untouched either way. This test uses a day 2 with a
    # DIFFERENT new row instead of a quiet day, which is what actually clobbered production.
    day1 = {
        "generated_at": "2026-07-06T12:00:00Z",
        "campaigns": [{"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"}],
        "bookings": [],
        "jobs": [{"id": 500, "completedOn": "2026-07-06T09:00:00Z", "campaignId": 1,
                  "customerName": "Alice Clobber", "email": "alice.clobber@example.com",
                  "phones": [], "invoiceTotal": 500.00, "leadCallId": None, "bookingId": None}],
    }
    day2 = {
        "generated_at": "2026-07-08T12:00:00Z",
        "campaigns": [{"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"}],
        "bookings": [],
        "jobs": [{"id": 501, "completedOn": "2026-07-08T09:00:00Z", "campaignId": 1,
                  "customerName": "Bob Clobber", "email": "bob.clobber@example.com",
                  "phones": [], "invoiceTotal": 800.00, "leadCallId": None, "bookingId": None}],
    }
    day1_path = tmp_path / "day1.json"
    day1_path.write_text(json.dumps(day1))
    r1 = run_cli(tmp_path, day1_path, now="2026-07-06T12:00:00Z")
    assert r1.returncode == 0, r1.stderr
    summary1 = json.loads(r1.stdout.strip().splitlines()[-1])
    assert summary1["tier_b"] == 1
    assert "500.00,USD" in (tmp_path / "output" / "oci-pii.csv").read_text()

    day2_path = tmp_path / "day2.json"
    day2_path.write_text(json.dumps(day2))
    r2 = run_cli(tmp_path, day2_path, now="2026-07-08T12:00:00Z")
    assert r2.returncode == 0, r2.stderr
    summary2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert summary2["tier_b"] == 1  # Bob is genuinely new, not a dupe of Alice

    pii = (tmp_path / "output" / "oci-pii.csv").read_text()
    assert "800.00,USD" in pii   # Bob (today's new row) present
    assert "500.00,USD" in pii   # Alice (still in the 14-day window) must survive, not be clobbered
