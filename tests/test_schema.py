import json
from st_msads_oci.schema import validate_input

GOOD = {"generated_at": "2026-07-06T12:00:00Z",
        "campaigns": [{"id": 1, "name": "Brand", "category": "Paid Microsoft"}],
        "bookings": [{"id": 10, "createdOn": "2026-07-01T14:00:00Z", "campaignId": 1,
                      "customerName": "J", "email": None, "phones": []}],
        "jobs": [{"id": 20, "completedOn": "2026-07-02T15:00:00Z", "campaignId": 1, "customerName": None,
                  "email": "a@b.co", "phones": ["(555) 555-0100"], "invoiceTotal": 750.25,
                  "leadCallId": None, "bookingId": 5, "projectId": None}],
        "calls": [{"id": 7, "receivedOn": "2026-07-02T15:00:00Z", "to": "5555550100", "from": "5555550101",
                   "direction": "Inbound", "campaign": {"id": 1}},
                  {"id": 8, "receivedOn": "2026-07-02T16:00:00Z", "to": None, "from": None,
                   "direction": "Inbound", "callType": "Booked", "campaign": {"id": 1},
                   "customerName": "K", "email": None, "phones": ["5555550102"]}]}

def test_good_payload_valid():
    assert validate_input(GOOD) == []

def test_project_jobs_optional_but_shaped():
    p = dict(GOOD, project_jobs={"99": [{"id": 1, "jobStatus": "Completed", "total": 1.0, "completedOn": "2026-07-01T00:00:00Z",
                                         "createdOn": "2026-06-01T00:00:00Z", "campaignId": 1, "leadCallId": None,
                                         "bookingId": None, "partnerLeadCallId": None, "createdFromEstimateId": None}]})
    assert validate_input(p) == []
    p["project_jobs"]["99"][0].pop("jobStatus")
    assert any("jobStatus" in e for e in validate_input(p))

def test_missing_required_and_wrong_types_reported():
    bad = json.loads(json.dumps(GOOD))
    bad["jobs"][0].pop("completedOn"); bad["bookings"][0]["phones"] = "555"
    errs = validate_input(bad)
    assert any("completedOn" in e for e in errs) and any("phones" in e for e in errs)

def test_email_must_be_null_not_empty_string():
    bad = json.loads(json.dumps(GOOD)); bad["jobs"][0]["email"] = ""
    assert validate_input(bad)

def test_top_level_keys_required():
    assert any("campaigns" in e for e in validate_input({"generated_at": "x"}))


def test_validation_errors_never_include_customer_values_or_dynamic_keys():
    import copy
    bad = copy.deepcopy(GOOD)
    bad['jobs'][0]['email'] = ['private.person@example.com']
    bad['jobs'][0]['phones'] = {'5551234567': 'private.person@example.com'}
    bad['project_jobs'] = {'private.person@example.com': [{'total': 'SECRET_VALUE'}]}
    errors = '\n'.join(validate_input(bad))
    for sensitive in ['private.person@example.com', '5551234567', 'SECRET_VALUE']:
        assert sensitive not in errors
    assert 'email' in errors and 'phones' in errors and 'project_jobs' in errors
