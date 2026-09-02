from st_msads_oci.ledger import Ledger
from st_msads_oci.rows import (GOAL_COMPLETED_JOBS, parse_utc, project_rows_from_payload)

NOW = parse_utc("2026-08-03T12:00:00Z")
TODAY = "2026-08-03"
CAMPS = [{"id": 1, "name": "Example Brand - Search - MS", "category": "Paid Microsoft"},
         {"id": 2, "name": "HVAC - Search - GAds", "category": "Paid Google"},
         {"id": 3, "name": "Organic", "category": "Organic"}]


def job(jid, *, status="Completed", total=0.0, completed="2026-08-01T10:00:00Z",
        created="2026-07-20T10:00:00Z", camp=1, lead_call=None, booking=None,
        partner=None, est=None, **contact):
    j = {"id": jid, "jobStatus": status, "total": total,
         "completedOn": completed if status == "Completed" else None,
         "createdOn": created, "campaignId": camp, "leadCallId": lead_call,
         "bookingId": booking, "partnerLeadCallId": partner, "createdFromEstimateId": est}
    j.update(contact)  # customerName / email / phones on the lead entry
    return j


def payload(project_jobs, calls=None):
    """Main jobs list mirrors what the runbook pulls: Completed jobs with projectId set."""
    jobs = [{"id": j["id"], "completedOn": j["completedOn"], "campaignId": j["campaignId"],
             "customerName": j.get("customerName", ""), "email": None, "phones": [],
             "invoiceTotal": j["total"], "leadCallId": j["leadCallId"],
             "bookingId": j["bookingId"], "projectId": pid}
            for pid, js in project_jobs.items() for j in js if j["jobStatus"] == "Completed"]
    return {"campaigns": CAMPS, "jobs": jobs, "calls": calls or [],
            "project_jobs": {str(pid): js for pid, js in project_jobs.items()}}


LEAD = dict(total=0.0, completed="2026-07-25T09:00:00Z", created="2026-07-01T08:00:00Z",
            lead_call=55, customerName="Jane", email="jane@x.com", phones=["(555) 555-0001"])


def test_canonical_two_leg_project():
    # spec edge 1 (mirrors project 110000002) + edge 6 (install stamped elsewhere)
    lead = job(100, **LEAD)
    install = job(101, total=17117.0, created="2026-07-10T08:00:00Z", camp=3, est=9)
    rows, members, findings, dropped = project_rows_from_payload(
        payload({110000002: [lead, install]}), Ledger(), NOW, TODAY)
    assert members == {100, 101}
    assert findings == [] and dropped == []
    [r] = rows
    assert r.st_id == 100
    assert r.value == 17117.0
    assert r.ts == parse_utc("2026-07-25T09:00:00Z")
    assert r.campaign_category == "Paid Microsoft"  # lead's campaign, not install's
    assert r.raw_email == "jane@x.com" and r.lead_call_id == 55
    assert r.project_id == 110000002 and r.member_job_ids == [100, 101]


def test_open_job_pends_project():
    # spec edge 2: install still InProgress -> not uploadable, goes pending
    ledger = Ledger()
    lead = job(100, **LEAD)
    install = job(101, status="InProgress", total=0.0)
    rows, members, findings, dropped = project_rows_from_payload(
        payload({500: [lead, install]}), ledger, NOW, TODAY)
    assert rows == [] and findings == [] and dropped == []
    assert members == {100, 101}  # members withheld from per-job path even while pending
    assert ledger.pending_projects == [{"project_id": 500, "first_seen": TODAY}]


def test_pending_over_30_days_reported():
    ledger = Ledger()
    ledger.pending_projects = [{"project_id": 500, "first_seen": "2026-06-20"}]
    lead = job(100, **LEAD)
    install = job(101, status="InProgress", total=0.0)
    rows, _, findings, _ = project_rows_from_payload(
        payload({500: [lead, install]}), ledger, NOW, TODAY)
    assert rows == []
    assert any("pending 44 days" in f for f in findings)
    assert ledger.pending_projects[0]["first_seen"] == "2026-06-20"  # not reset


def test_originless_project_withheld_as_suspect_but_pends():
    # spec edge 4, revised 2026-08-05: no leg carries lead origin -> withhold and report,
    # but stay pending so a lead leg attached in ST later still gets picked up.
    ledger = Ledger()
    jobs = [job(100, total=0.0), job(101, total=6729.0, est=4)]
    rows, members, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == [] and dropped == []
    assert members == {100, 101}
    assert any("attribution_suspect" in f for f in findings)
    assert ledger.pending_projects == [{"project_id": 600, "first_seen": TODAY}]
    # a later run keeps the original first_seen (setdefault, not overwrite)
    project_rows_from_payload(payload({600: jobs}), ledger, NOW, "2026-08-04")
    assert ledger.pending_projects == [{"project_id": 600, "first_seen": TODAY}]


def test_suspect_project_uploads_once_lead_origin_appears():
    # the point of staying pending: run 1 found no origin, run 2 sees a leadCallId
    ledger = Ledger()
    ledger.pending_projects = [{"project_id": 600, "first_seen": "2026-07-30"}]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: [job(100, **LEAD), job(101, total=6729.0, est=4)]}), ledger, NOW, TODAY)
    assert findings == [] and dropped == []
    [r] = rows
    assert r.st_id == 100 and r.value == 6729.0 and r.project_id == 600
    assert ledger.pending_projects == []  # settled, no longer revisited


def test_member_already_in_ledger_blocks_project():
    # spec edge 7: install leg 101 uploaded historically as a per-job row
    ledger = Ledger()
    ledger.uploaded = [{"goal": GOAL_COMPLETED_JOBS, "st_id": 101,
                        "ts": "2026-07-30T10:00:00+00:00", "email_hash": None,
                        "phone_hash": None, "campaign_name": "x",
                        "uploaded_on": "2026-07-30", "status": None}]
    lead = job(100, **LEAD)
    install = job(101, total=17117.0)
    rows, members, findings, dropped = project_rows_from_payload(
        payload({700: [lead, install]}), ledger, NOW, TODAY)
    assert rows == [] and findings == []
    assert len(dropped) == 1 and "member job already uploaded" in dropped[0].reason
    assert 101 in members  # the dedupe guarantee: never let a blocked member re-upload


def test_late_revenue_reported_never_reuploaded():
    # spec edge 3: third job lands after the project uploaded
    ledger = Ledger()
    ledger.uploaded = [{"goal": GOAL_COMPLETED_JOBS, "st_id": 100,
                        "ts": "2026-07-25T09:00:00+00:00", "email_hash": "a" * 64,
                        "phone_hash": None, "campaign_name": "x", "uploaded_on": "2026-08-01",
                        "status": None, "project_id": 800, "member_job_ids": [100, 101]}]
    lead = job(100, **LEAD)
    install = job(101, total=17117.0)
    third = job(102, total=3000.0, created="2026-07-28T08:00:00Z", est=5)
    rows, members, findings, dropped = project_rows_from_payload(
        payload({800: [lead, install, third]}), ledger, NOW, TODAY)
    assert rows == [] and dropped == []
    assert any("late_project_revenue $3000.00" in f and "not uploadable" in f
               for f in findings)
    assert 102 in members  # the late job must still be withheld from the per-job path


def test_late_revision_detector_flags_upward_revision():
    # Alex-approved spec addition: detection only, never re-uploaded
    ledger = Ledger()
    ledger.uploaded = [{"goal": GOAL_COMPLETED_JOBS, "st_id": 100,
                        "ts": "2026-07-25T09:00:00+00:00", "email_hash": None,
                        "phone_hash": None, "campaign_name": "x", "uploaded_on": "2026-08-01",
                        "status": None, "project_id": 810, "member_job_ids": [100, 101],
                        "value": 17117.0}]
    lead = job(100, **LEAD)
    install = job(101, total=20000.0)  # revised upward after upload
    rows, _, findings, dropped = project_rows_from_payload(
        payload({810: [lead, install]}), ledger, NOW, TODAY)
    assert rows == [] and dropped == []
    assert any("revenue revised 17117.0 -> 20000.0" in f and "not re-uploadable" in f
               for f in findings)


def test_late_revision_detector_skips_legacy_entry_without_value():
    ledger = Ledger()
    ledger.uploaded = [{"goal": GOAL_COMPLETED_JOBS, "st_id": 100,
                        "ts": "2026-07-25T09:00:00+00:00", "email_hash": None,
                        "phone_hash": None, "campaign_name": "x", "uploaded_on": "2026-08-01",
                        "status": None, "project_id": 820, "member_job_ids": [100, 101]}]
    lead = job(100, **LEAD)
    install = job(101, total=17117.0)  # matches ledger member set, no late job either
    rows, _, findings, dropped = project_rows_from_payload(
        payload({820: [lead, install]}), ledger, NOW, TODAY)
    assert rows == [] and dropped == []
    assert findings == []  # legacy entry has no "value" — comparison skipped, not a crash


def test_malformed_project_job_finding_and_pending():
    # crash guard: a null createdOn on one project's job must not abort the whole run
    ledger = Ledger()
    good_lead = job(100, **LEAD)
    good_install = job(101, total=500.0)
    bad_lead = job(200, created=None, lead_call=77, total=0.0)  # parse_utc(None) raises
    bad_install = job(201, total=900.0)
    p = payload({900: [good_lead, good_install], 950: [bad_lead, bad_install]})
    rows, members, findings, dropped = project_rows_from_payload(p, ledger, NOW, TODAY)
    [r] = rows
    assert r.project_id == 900 and r.value == 500.0  # unaffected sibling project uploads normally
    assert any("project 950" in f and "malformed job data" in f and "skipped" in f
               for f in findings)
    assert ledger.pending_projects == [{"project_id": 950, "first_seen": TODAY}]


def test_window_expired_skipped_and_reported():
    # spec edge 8: lead completedOn 91+ days old at settle
    lead = job(100, **{**LEAD, "completed": "2026-04-01T09:00:00Z"})
    install = job(101, total=5000.0)
    rows, _, findings, _ = project_rows_from_payload(
        payload({900: [lead, install]}), Ledger(), NOW, TODAY)
    assert rows == []
    assert any("window_expired" in f for f in findings)


def test_lead_only_project_uploads():
    # spec edge 10: revenue directly on the lead job, no fulfillment leg
    lead = job(100, **{**LEAD, "total": 2500.0})
    rows, _, findings, dropped = project_rows_from_payload(
        payload({1000: [lead]}), Ledger(), NOW, TODAY)
    [r] = rows
    assert r.value == 2500.0 and r.st_id == 100
    assert findings == [] and dropped == []


def test_canceled_fulfillment_excluded_not_blocking():
    # spec edge 11: Canceled job doesn't block settle, its total not summed
    lead = job(100, **{**LEAD, "total": 500.0})
    canceled = job(101, status="Canceled", total=8000.0)
    rows, _, findings, dropped = project_rows_from_payload(
        payload({1100: [lead, canceled]}), Ledger(), NOW, TODAY)
    [r] = rows
    assert r.value == 500.0
    assert findings == [] and dropped == []


def test_settled_zero_revenue_dropped():
    lead = job(100, **LEAD)  # $0
    canceled = job(101, status="Canceled", total=8000.0)
    rows, _, findings, dropped = project_rows_from_payload(
        payload({1200: [lead, canceled]}), Ledger(), NOW, TODAY)
    assert rows == []
    assert len(dropped) == 1 and "no completed revenue" in dropped[0].reason


def test_lead_not_completed_withheld():
    # planning decision: Canceled lead has no completedOn -> no defensible conversion time
    lead = job(100, **{**LEAD, "status": "Canceled"})
    install = job(101, total=5000.0)
    rows, _, findings, _ = project_rows_from_payload(
        payload({1300: [lead, install]}), Ledger(), NOW, TODAY)
    assert rows == []
    assert any("not Completed" in f for f in findings)


def test_earliest_created_lead_wins():
    early = job(100, **{**LEAD, "created": "2026-07-01T08:00:00Z"})
    late = job(101, total=900.0, created="2026-07-05T08:00:00Z", booking=77,
               completed="2026-07-26T09:00:00Z")
    [r], _, _, _ = project_rows_from_payload(
        payload({1400: [late, early]}), Ledger(), NOW, TODAY)
    assert r.st_id == 100
    assert r.value == 900.0  # completed sum still includes both legs ($0 + $900)


def test_google_lead_row_carries_google_campaign():
    # spec edge 5: row is built from the LEAD campaign; the downstream campaign
    # filter drops it (asserted end-to-end in test_end_to_end.py)
    lead = job(100, **{**LEAD, "camp": 2})
    install = job(101, total=2513.0, camp=1)  # install stamped Paid-MS
    [r], _, _, _ = project_rows_from_payload(
        payload({1500: [lead, install]}), Ledger(), NOW, TODAY)
    assert r.campaign_category == "Paid Google"


def test_missing_project_jobs_reported_and_pended():
    ledger = Ledger()
    p = {"campaigns": CAMPS,
         "jobs": [{"id": 100, "completedOn": "2026-08-01T10:00:00Z", "campaignId": 1,
                   "customerName": "", "email": None, "phones": [], "invoiceTotal": 100.0,
                   "leadCallId": 1, "bookingId": None, "projectId": 1600}],
         "project_jobs": {}}
    rows, members, findings, _ = project_rows_from_payload(p, ledger, NOW, TODAY)
    assert rows == []
    assert members == {100}  # still withheld from per-job path
    assert any("missing from project_jobs" in f for f in findings)
    assert ledger.pending_projects == [{"project_id": 1600, "first_seen": TODAY}]


def test_pending_project_rechecked_without_new_completed_jobs():
    # A pending project settles later; its jobs are outside the pull window, so the
    # runbook supplies project_jobs for it with NO matching entry in the main jobs list.
    ledger = Ledger()
    ledger.pending_projects = [{"project_id": 1700, "first_seen": "2026-07-30"}]
    lead = job(100, **LEAD)
    install = job(101, total=4000.0)
    p = {"campaigns": CAMPS, "jobs": [],
         "project_jobs": {"1700": [lead, install]}}
    rows, _, findings, dropped = project_rows_from_payload(p, ledger, NOW, TODAY)
    [r] = rows
    assert r.value == 4000.0
    assert ledger.pending_projects == []


def test_no_projects_in_payload_is_a_noop():
    p = {"campaigns": CAMPS,
         "jobs": [{"id": 20, "completedOn": "2026-08-01T10:00:00Z", "campaignId": 1,
                   "customerName": "", "email": "a@b.com", "phones": [],
                   "invoiceTotal": 750.0, "leadCallId": None, "bookingId": 5}]}
    assert project_rows_from_payload(p, Ledger(), NOW, TODAY) == ([], set(), [], [])


def test_nonms_suspect_reported_once_and_dropped():
    # spec 2026-08-05 scope gate: non-MS suspects would die at the campaign filter anyway;
    # persisting them repeats the finding in every daily email forever
    ledger = Ledger()
    jobs = [job(100, camp=2, created="2026-07-10T08:00:00Z"),
            job(101, total=5000.0, camp=2, est=4)]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == [] and dropped == []
    assert any("non-MS campaign" in f for f in findings)
    assert ledger.pending_projects == []


def test_suspect_with_pii_recovers_as_tier_b_shape():
    # rung 2: no lead job, no qualifying call, but customer PII present
    ledger = Ledger()
    jobs = [job(100, created="2026-07-10T08:00:00Z", completed="2026-08-01T10:00:00Z",
                email="carol@x.com", phones=["(555) 605-5551"], customerName="Carol"),
            job(101, total=14297.31, created="2026-07-15T08:00:00Z", est=4)]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert dropped == []
    [r] = rows
    assert r.recovered_via == "pii"
    assert r.st_id == 100                       # proxy = earliest-created Completed leg
    assert r.value == 14297.31 and r.project_id == 600
    assert r.ts == parse_utc("2026-08-01T10:00:00Z")   # proxy completedOn, NOT a call time
    assert r.lead_call_id is None
    assert r.raw_email == "carol@x.com" and r.member_job_ids == [100, 101]
    assert any("suspect_recovered_pii" in f for f in findings)
    assert ledger.pending_projects == []


def test_zero_revenue_suspect_dropped_not_recovered():
    # zero-revenue precedence unchanged: the value check fires before the ladder
    ledger = Ledger()
    jobs = [job(100, email="c@x.com", phones=["5555550001"]),
            job(101, status="Canceled", total=0.0)]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == []
    assert any("no completed revenue" in d.reason for d in dropped)
    assert not any("suspect_recovered" in f for f in findings)
    assert ledger.pending_projects == []


def test_recovered_pii_window_expired_reported_and_dropped():
    # 90-day window applies to the rung's timestamp
    ledger = Ledger()
    jobs = [job(100, created="2026-04-20T08:00:00Z", completed="2026-05-01T10:00:00Z",
                email="c@x.com", phones=["5555550001"]),
            job(101, total=9000.0, created="2026-04-22T08:00:00Z",
                completed="2026-05-02T10:00:00Z", est=4)]
    rows, _, findings, _ = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == []
    assert any("window_expired" in f for f in findings)


def test_pii_recovers_from_non_proxy_leg():
    # review finding 1: PII lands on the later-created install leg, not the proxy —
    # still recovers, since the customer is identical across a project's legs
    ledger = Ledger()
    jobs = [job(100, created="2026-07-10T08:00:00Z", completed="2026-08-01T10:00:00Z"),
            job(101, total=14297.31, created="2026-07-15T08:00:00Z", est=4,
                email="carol@x.com", phones=["(555) 605-5551"], customerName="Carol")]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert dropped == []
    [r] = rows
    assert r.recovered_via == "pii"
    assert r.st_id == 100                              # proxy = earliest-created Completed leg
    assert r.ts == parse_utc("2026-08-01T10:00:00Z")    # proxy completedOn, not install's
    assert r.raw_email == "carol@x.com"                 # PII pulled from the install leg
    assert any("suspect_recovered_pii" in f for f in findings)
    assert ledger.pending_projects == []


def test_call_inference_recovers_from_non_proxy_leg():
    # review finding 2: contact phone lives on the later-created install leg (101), not the
    # proxy (100) — call inference's contact-leg lookup must still resolve via that leg's phone
    ledger = Ledger()
    jobs = [job(100, created="2026-07-20T10:00:00Z", completed="2026-08-01T10:00:00Z"),
            job(101, total=14297.31, created="2026-07-22T08:00:00Z", est=4,
                email="carol@x.com", phones=["(555) 605-5551"], customerName="Carol")]
    calls = [call(8, received="2026-07-15T09:00:00Z")]   # from the 101 leg's phone
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}, calls=calls), ledger, NOW, TODAY)
    assert dropped == []
    [r] = rows
    assert r.recovered_via == "call"
    assert r.lead_call_id == 8
    assert any("suspect_recovered_call" in f for f in findings)


def test_unsettled_project_no_lead_pends_silently():
    # review finding 2a: an unsettled project with no lead-origin leg pends as a normal
    # pending project — the "not settled" branch beats the ladder, no finding at all
    ledger = Ledger()
    jobs = [job(100, created="2026-07-10T08:00:00Z"),
            job(101, status="InProgress", total=0.0, created="2026-07-15T08:00:00Z")]
    rows, _, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == [] and findings == [] and dropped == []
    assert ledger.pending_projects == [{"project_id": 600, "first_seen": TODAY}]


def test_originless_project_overlap_beats_ladder():
    # review finding 2b: overlap check runs before the ladder — an originless settled
    # project with a member job already in the ledger drops, never reaches suspect recovery
    ledger = Ledger()
    ledger.uploaded.append({"goal": GOAL_COMPLETED_JOBS, "st_id": 101,
                            "ts": "2026-07-30T10:00:00+00:00", "member_job_ids": None,
                            "project_id": None})
    jobs = [job(100, created="2026-07-10T08:00:00Z", email="c@x.com", phones=["5555550001"]),
            job(101, total=5000.0, created="2026-07-15T08:00:00Z", est=4)]
    rows, members, findings, dropped = project_rows_from_payload(
        payload({600: jobs}), ledger, NOW, TODAY)
    assert rows == [] and findings == []
    assert len(dropped) == 1 and "member job already uploaded" in dropped[0].reason
    assert ledger.pending_projects == []
    assert 101 in members


def call(cid, *, received, frm="5556055551", camp=1, direction="Inbound"):
    return {"id": cid, "receivedOn": received, "from": frm,
            "direction": direction, "campaignId": camp}


SUSPECT_JOBS = [job(100, created="2026-07-20T10:00:00Z", completed="2026-08-01T10:00:00Z",
                    email="carol@x.com", phones=["(555) 605-5551"], customerName="Carol"),
                job(101, total=14297.31, created="2026-07-22T08:00:00Z", est=4)]


def test_suspect_call_inference_earliest_qualifying_call_wins():
    calls = [
        call(9, received="2026-07-19T09:00:00Z"),                      # qualifies, later
        call(8, received="2026-07-15T09:00:00Z"),                      # qualifies, EARLIEST
        call(7, received="2026-07-16T09:00:00Z", camp=2),              # campaign mismatch
        call(6, received="2026-07-01T09:00:00Z"),                      # outside 7-day window
        call(5, received="2026-07-17T09:00:00Z", direction="Outbound"),  # not inbound
        call(4, received="2026-07-18T09:00:00Z", frm="5550001111"),    # wrong phone
    ]
    rows, _, findings, _ = project_rows_from_payload(
        payload({600: SUSPECT_JOBS}, calls=calls), Ledger(), NOW, TODAY)
    [r] = rows
    assert r.recovered_via == "call"
    assert r.lead_call_id == 8
    assert r.ts == parse_utc("2026-07-15T09:00:00Z")   # call receivedOn, not completedOn
    assert any("suspect_recovered_call" in f for f in findings)


def test_suspect_call_inference_accepts_nested_campaign_shape():
    calls = [{"id": 8, "receivedOn": "2026-07-15T09:00:00Z", "from": "5556055551",
              "direction": "Inbound", "campaign": {"id": 1}}]
    rows, _, _, _ = project_rows_from_payload(
        payload({600: SUSPECT_JOBS}, calls=calls), Ledger(), NOW, TODAY)
    [r] = rows
    assert r.recovered_via == "call" and r.lead_call_id == 8


def test_suspect_no_qualifying_call_falls_to_pii():
    # mutation guard: one distractor per filter clause, each otherwise a qualifying call —
    # any dropped filter (campaign/direction/phone/window) lets one through and flips pii->call
    calls = [call(7, received="2026-07-16T09:00:00Z", camp=2),                # campaign
             call(5, received="2026-07-17T09:00:00Z", direction="Outbound"),  # direction
             call(4, received="2026-07-18T09:00:00Z", frm="5550001111"),      # phone
             call(3, received="2026-07-20T11:00:00Z"),                        # after createdOn
             call(2, received="2026-07-13T09:59:59Z")]                        # before window
    rows, _, findings, _ = project_rows_from_payload(
        payload({600: SUSPECT_JOBS}, calls=calls), Ledger(), NOW, TODAY)
    [r] = rows
    assert r.recovered_via == "pii" and r.lead_call_id is None
    assert any("suspect_recovered_pii" in f for f in findings)
