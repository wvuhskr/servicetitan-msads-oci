import json, pytest
from st_msads_oci.pull.st_client import ServiceTitanClient, ServiceTitanError

def make(responses):
    calls = []
    def opener(req):
        calls.append(req)
        status, body = responses.pop(0)
        return status, {}, json.dumps(body).encode()
    c = ServiceTitanClient("id", "sec", "key", "999", opener=opener, sleep=lambda s: None)
    return c, calls

def test_token_then_bearer_and_app_key_headers():
    c, calls = make([(200, {"access_token": "T", "expires_in": 900}), (200, {"data": [], "hasMore": False})])
    assert c.get("/jpm/v2/tenant/{tenant}/jobs") == {"data": [], "hasMore": False}
    assert calls[0].full_url.startswith("https://auth.servicetitan.io/")
    assert calls[1].get_header("Authorization") == "Bearer T" and calls[1].get_header("St-app-key") == "key"
    assert "/tenant/999/jobs" in calls[1].full_url

def test_paging_stops_on_hasmore_false():
    c, _ = make([(200, {"access_token": "T", "expires_in": 900}),
                 (200, {"data": [1, 2], "hasMore": True}), (200, {"data": [3], "hasMore": False})])
    assert list(c.get_paged("/x/{tenant}/y", page_size=2)) == [1, 2, 3]

def test_retry_once_on_500_then_raise():
    c, _ = make([(200, {"access_token": "T", "expires_in": 900}), (500, {}), (200, {"ok": 1})])
    assert c.get("/a/{tenant}") == {"ok": 1}
    c, _ = make([(200, {"access_token": "T", "expires_in": 900}), (500, {}), (500, {})])
    with pytest.raises(ServiceTitanError):
        c.get("/a/{tenant}")

def test_get_optional_404_is_none():
    c, _ = make([(200, {"access_token": "T", "expires_in": 900}), (404, {})])
    assert c.get_optional("/a/{tenant}/1") is None

def test_sends_user_agent_header():
    from st_msads_oci.pull.st_client import USER_AGENT
    c, calls = make([(200, {"access_token": "T", "expires_in": 900}), (200, {"data": [], "hasMore": False})])
    c.get("/jpm/v2/tenant/{tenant}/jobs")
    assert calls[0].get_header("User-agent") == USER_AGENT
    assert calls[1].get_header("User-agent") == USER_AGENT
