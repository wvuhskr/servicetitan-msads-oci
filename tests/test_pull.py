import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from st_msads_oci.ledger import Ledger
from st_msads_oci.pull.servicetitan import (parse_webform_summary, windows, pull_all, write_payload_atomic)
from st_msads_oci.pull.st_client import ServiceTitanError
from tests.conftest import make_settings

NOW = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
SUMMARY = "First Name*: Kay \nLast Name*: Pitt\nEmail*: Kay.Pitt@Example.com\nPhone Number*: (555) 555-0100\nHome Address*: x\n"

class FakeClient:
    def __init__(self, routes): self.routes, self.log = routes, []
    def _hit(self, path, params):
        self.log.append((path, params or {}))
        for k, v in self.routes.items():
            if path.startswith(k) or path == k:
                return v(params or {}) if callable(v) else v
        raise ServiceTitanError(404, path, b"")
    def get(self, path, params=None): return self._hit(path, params)
    def get_optional(self, path, params=None):
        try: return self._hit(path, params)
        except ServiceTitanError: return None
    def get_paged(self, path, params=None, page_size=200):
        r = self._hit(path, params); yield from (r.get("data") if isinstance(r, dict) else r)

def routes():
    return {
        "/marketing/v2/tenant/{tenant}/campaigns": {"data": [
            {"id": 1, "name": "Brand MS", "category": {"name": "Paid Microsoft"}},
            {"id": 2, "name": "Google", "category": {"name": "Paid Google"}}]},
        "/crm/v2/tenant/{tenant}/bookings": {"data": [
            {"id": 10, "createdOn": "2026-07-01T14:00:00Z", "campaignId": 1, "name": "Kay Pitt",
             "source": "WebFormIntegration", "summary": SUMMARY, "jobId": None},
            {"id": 11, "createdOn": "2026-07-01T15:00:00Z", "campaignId": 1, "name": "Sched",
             "source": "SchedulingPro", "summary": "", "jobId": 500},
            {"id": 12, "createdOn": "2026-07-01T16:00:00Z", "campaignId": 2, "name": "G",
             "source": "WebFormIntegration", "summary": SUMMARY, "jobId": None}]},
        "/jpm/v2/tenant/{tenant}/jobs/500": {"id": 500, "customerId": 77},
        "/jpm/v2/tenant/{tenant}/jobs": {"data": [
            {"id": 20, "completedOn": "2026-07-02T15:00:00Z", "campaignId": 1, "customerId": 77,
             "leadCallId": None, "bookingId": 11, "projectId": None},
            {"id": 21, "completedOn": None, "campaignId": 1, "customerId": 77},
            {"id": 22, "completedOn": "2026-07-02T16:00:00Z", "campaignId": 2, "customerId": 88,
             "leadCallId": 9, "bookingId": None, "projectId": 3}]},
        "/accounting/v2/tenant/{tenant}/invoices": lambda p: {"data": [{"total": 500.0}, {"total": 250.25}]} if p.get("jobId") == 20 else {"data": []},
        "/crm/v2/tenant/{tenant}/customers/77/contacts": {"data": [{"type": "Email", "value": "s@example.com"},
                                                                   {"type": "MobilePhone", "value": "555-555-0102"}]},
        "/crm/v2/tenant/{tenant}/customers/77": {"id": 77, "name": "Sched Customer"},
        "/telecom/v2/tenant/{tenant}/calls": {"data": [
            {"id": 1, "leadCall": {"id": 700, "receivedOn": "2026-07-03T10:00:00Z", "from": "5555550103", "to": "5555550190",
             "direction": "Inbound", "callType": "Booked", "campaign": {"id": 1},
             "customer": {"id": 5, "name": "Caller", "email": "c@example.com", "contacts": [{"type": "Phone", "value": "5555550103"}]}}},
            {"id": 2, "leadCall": {"id": 701, "receivedOn": "2026-07-03T11:00:00Z", "from": "x", "to": "y",
             "direction": "Inbound", "callType": "Abandoned", "campaign": {"id": 1}}},
            {"id": 3, "leadCall": {"id": 702, "receivedOn": "2026-07-03T12:00:00Z", "from": "5555550104", "to": "5555550190",
             "direction": "Inbound", "callType": "Booked", "campaign": {"id": 2}, "customer": {"id": 6, "name": "G Caller"}}}]},
    }

def test_parse_webform_summary():
    assert parse_webform_summary(SUMMARY) == ("Kay.Pitt@Example.com", ["(555) 555-0100"])
    assert parse_webform_summary("Phone Number*: \nEmail*: \n") == (None, [])
    assert parse_webform_summary(None) == (None, [])

def test_windows_are_watermark_minus_one_hour():
    led = Ledger({"watermarks": {"booked_web": "2026-07-05T10:00:00+00:00", "completed_jobs": "2026-07-04T00:00:00+00:00",
                                 "booked_call": "2026-07-05T23:30:00+00:00"}})
    w = windows(led, NOW)
    assert w == {"bookings": "2026-07-05T09:00:00+00:00", "jobs": "2026-07-03T23:00:00+00:00", "calls": "2026-07-05T22:30:00+00:00"}

def test_pull_all_shapes_and_enrichment_scope():
    c = FakeClient(routes())
    p = pull_all(c, make_settings(), Ledger(), NOW, log=lambda *a: None)
    assert [x["category"] for x in p["campaigns"]] == ["Paid Microsoft", "Paid Google"]
    b = {x["id"]: x for x in p["bookings"]}
    assert b[10]["email"] == "Kay.Pitt@Example.com" and b[10]["phones"] == ["(555) 555-0100"]
    assert b[11]["email"] == "s@example.com" and b[11]["phones"] == ["555-555-0102"] and b[11]["customerName"] == "Sched Customer"
    assert b[12]["email"] is None and b[12]["phones"] == []          # non-MS: never enriched, still present
    j = {x["id"]: x for x in p["jobs"]}
    assert set(j) == {20, 22}                                          # uncompleted job 21 skipped
    assert j[20]["invoiceTotal"] == 750.25 and j[20]["email"] == "s@example.com" and j[20]["bookingId"] == 11
    assert j[22]["invoiceTotal"] is None and j[22]["email"] is None and j[22]["projectId"] == 3
    calls = {x["id"]: x for x in p["calls"]}
    assert set(calls) == {700, 702}                                    # only Booked
    assert calls[700]["callType"] == "Booked" and calls[700]["email"] == "c@example.com" and calls[700]["phones"] == ["5555550103"]
    assert calls[700]["to"] == "5555550190" and calls[700]["campaign"] == {"id": 1}
    assert calls[702]["email"] is None and calls[702]["phones"] == []
    assert "project_jobs" not in p
    # no per-customer calls for non-MS rows
    assert not any("customers/88" in path for path, _ in c.log)
    # window params were sent
    assert any(pr.get("createdOnOrAfter") for path, pr in c.log if path.endswith("/bookings"))
    assert any(pr.get("completedOnOrAfter") for path, pr in c.log if path.endswith("/jobs"))

def test_per_customer_failure_is_skipped_not_fatal():
    r = routes(); r.pop("/crm/v2/tenant/{tenant}/customers/77/contacts"); r.pop("/crm/v2/tenant/{tenant}/customers/77")
    p = pull_all(FakeClient(r), make_settings(), Ledger(), NOW, log=lambda *a: None)
    j = {x["id"]: x for x in p["jobs"]}
    assert j[20]["email"] is None and j[20]["invoiceTotal"] == 750.25

def test_top_level_failure_raises():
    r = routes(); r.pop("/jpm/v2/tenant/{tenant}/jobs")
    with pytest.raises(ServiceTitanError):
        pull_all(FakeClient(r), make_settings(), Ledger(), NOW, log=lambda *a: None)

def test_write_payload_atomic_validates_and_replaces(tmp_path):
    out = tmp_path / "in.json"; out.write_text("old")
    p = pull_all(FakeClient(routes()), make_settings(), Ledger(), NOW, log=lambda *a: None)
    write_payload_atomic(p, out)
    assert json.loads(out.read_text())["jobs"] and not out.with_suffix(".tmp").exists()
    with pytest.raises(ValueError):
        write_payload_atomic({"generated_at": "x"}, out)
    assert json.loads(out.read_text())["jobs"]   # old content intact after failed write

def test_empty_servicetitan_email_normalizes_to_null(tmp_path):
    r = routes()
    r["/telecom/v2/tenant/{tenant}/calls"]["data"][0]["leadCall"]["customer"]["email"] = ""
    p = pull_all(FakeClient(r), make_settings(), Ledger(), NOW, log=lambda *a: None)
    calls = {x["id"]: x for x in p["calls"]}
    assert calls[700]["email"] is None
    write_payload_atomic(p, tmp_path / "x.json")  # must not raise ValueError from schema
