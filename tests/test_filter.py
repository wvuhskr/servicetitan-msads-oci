from st_msads_oci.rows import rows_from_payload, GOAL_BOOKED_JOB, GOAL_COMPLETED_JOBS
from st_msads_oci.filter import filter_rows, attach_hashes

PAYLOAD = {
    "generated_at": "2026-07-06T12:00:00Z",
    "campaigns": [
        {"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"},
        {"id": 2, "name": "HVAC - Search - GAds", "category": "Paid Google"},
    ],
    "bookings": [
        {"id": 10, "createdOn": "2026-07-01T14:00:00Z", "campaignId": 1,
         "customerName": "Jane Doe", "email": "jane@x.com", "phones": ["(555) 555-1234"]},
        {"id": 11, "createdOn": "2026-07-01T14:05:00Z", "campaignId": 2,
         "customerName": "Google Person", "email": "g@x.com", "phones": []},
        {"id": 12, "createdOn": "2026-07-01T14:10:00Z", "campaignId": 1,
         "customerName": "Art Vandelay", "email": "synthetic05@example.com", "phones": ["(555) 259-0002"]},
        {"id": 13, "createdOn": "2026-07-01T14:15:00Z", "campaignId": 1,
         "customerName": "No Contact", "email": None, "phones": []},
    ],
    "jobs": [
        {"id": 20, "completedOn": "2026-07-01T15:00:00Z", "campaignId": 1, "customerName": "Web Job",
         "email": "web@x.com", "phones": [], "invoiceTotal": 500.0, "leadCallId": None, "bookingId": 99},
        {"id": 21, "completedOn": "2026-07-01T15:30:00Z", "campaignId": 1, "customerName": "Call Job",
         "email": "call@x.com", "phones": [], "invoiceTotal": 900.0, "leadCallId": 555, "bookingId": None},
    ],
    "leads": [
        {"id": 30, "createdOn": "2026-07-01T16:00:00Z", "campaignId": 1,
         "customerName": "Lead Person", "email": "lead@x.com", "phones": []},
    ],
}
TEST_EMAILS = ["synthetic05@example.com"]
TEST_PHONES = ["(555) 259-0002"]


def test_rows_from_payload_counts_and_goals():
    rows = rows_from_payload(PAYLOAD)
    assert len(rows) == 6  # "leads" key ignored — goal deleted
    goals = {r.goal for r in rows}
    assert goals == {GOAL_BOOKED_JOB, GOAL_COMPLETED_JOBS}
    job = next(r for r in rows if r.st_id == 20)
    assert job.value == 500.0 and job.booking_id == 99


def test_filter_keeps_only_paid_microsoft_web_real():
    rows = rows_from_payload(PAYLOAD)
    kept, dropped = filter_rows(rows, TEST_EMAILS, TEST_PHONES)
    kept_ids = {r.st_id for r in kept}
    assert kept_ids == {10, 20, 21}  # 21 is call-origin, now kept (unlocked)
    reasons = {d.st_id: d.reason for d in dropped}
    assert "Paid Microsoft" not in reasons.get(11, "") and "Paid Google" in reasons[11]
    assert reasons[12] == "test identity"
    assert reasons[13] == "no contact info"


def test_filter_drops_completed_jobs_with_no_positive_invoice_value():
    payload = {
        "generated_at": "2026-07-06T12:00:00Z",
        "campaigns": [{"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"}],
        "bookings": [], "leads": [],
        "jobs": [
            {"id": 100, "completedOn": "2026-07-01T15:00:00Z", "campaignId": 1,
             "customerName": "Zero Invoice", "email": "zero@x.com", "phones": [],
             "invoiceTotal": 0, "leadCallId": None, "bookingId": 50},
            {"id": 101, "completedOn": "2026-07-01T15:30:00Z", "campaignId": 1,
             "customerName": "None Invoice", "email": "none@x.com", "phones": [],
             "invoiceTotal": None, "leadCallId": None, "bookingId": 51},
        ],
    }
    rows = rows_from_payload(payload)
    kept, dropped = filter_rows(rows, TEST_EMAILS, TEST_PHONES)
    assert kept == []
    reasons = {d.st_id: d.reason for d in dropped}
    assert reasons[100] == "no positive invoice value"
    assert reasons[101] == "no positive invoice value"


def test_attach_hashes():
    rows = rows_from_payload(PAYLOAD)
    kept, _ = filter_rows(rows, TEST_EMAILS, TEST_PHONES)
    hashed, failed = attach_hashes(kept)
    assert failed == []
    booking = next(r for r in hashed if r.st_id == 10)
    assert booking.phone_hash == "de6067d59c3a3654e3a3694427e494a4d33eafcbaa940c7e3921cf921972b014"
    assert booking.email_hash is not None
