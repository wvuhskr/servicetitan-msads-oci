from st_msads_oci.config import Settings, load_settings, load_env, DEFAULT_GOAL_NAMES

YAML = """
servicetitan:
  tenant_id: "123"
  campaign_category: "Paid Bing"
microsoft_ads:
  goal_names:
    completed_jobs: "Jobs Done"
  legacy_goal_names: ["Old Goal"]
worker:
  url: https://oci.example.com
initial_watermark: "2026-05-01T00:00:00+00:00"
test_identities:
  emails: ["qa@example.com"]
  phones: ["(555) 555-0100"]
notifications:
  - type: webhook
    url_env: NOTIFY_WEBHOOK_URL
"""

def test_load_settings_merges_defaults(tmp_path):
    p = tmp_path / "a.yaml"; p.write_text(YAML)
    s = load_settings(p)
    assert s.campaign_category == "Paid Bing"
    assert s.goal_names["completed_jobs"] == "Jobs Done"
    assert s.goal_names["booked_web"] == DEFAULT_GOAL_NAMES["booked_web"]
    assert s.goal_key("Jobs Done") == "completed_jobs"
    assert s.goal_key("Old Goal") is None and "Old Goal" in s.legacy_goal_names
    assert s.tenant_id == "123" and s.worker_url == "https://oci.example.com"
    assert s.test_emails == ("qa@example.com",) and s.notifications[0]["type"] == "webhook"

def test_defaults_when_file_minimal(tmp_path):
    p = tmp_path / "a.yaml"; p.write_text("servicetitan: {campaign_category: X}\n")
    s = load_settings(p)
    assert s.goal_names == DEFAULT_GOAL_NAMES and s.notifications == () and s.worker_url is None

def test_missing_campaign_category_fails(tmp_path):
    import pytest
    p = tmp_path / "a.yaml"; p.write_text("worker: {url: x}\n")
    with pytest.raises(ValueError, match="campaign_category"):
        load_settings(p)

def test_load_env_overlay(tmp_path, monkeypatch):
    p = tmp_path / ".env"; p.write_text("# c\nA=1\nB=from_file\n")
    monkeypatch.setenv("B", "from_env")
    env = load_env(p)
    assert env["A"] == "1" and env["B"] == "from_env"
