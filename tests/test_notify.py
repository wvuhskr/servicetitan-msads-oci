import json
from st_msads_oci.notify import (StdoutNotifier, WebhookNotifier, SmtpNotifier, GraphNotifier,
                                 build_notifiers, notify_all, build_message)
from tests.conftest import make_settings

def test_stdout_always_first_and_only_by_default(capsys):
    ns = build_notifiers(make_settings(), {})
    assert len(ns) == 1 and isinstance(ns[0], StdoutNotifier)
    assert notify_all(ns, "S", "B") == []
    assert "S" in capsys.readouterr().out

def test_webhook_needs_env_or_is_skipped_with_error():
    s = make_settings(notifications=({"type": "webhook", "url_env": "HOOK"},))
    ns = build_notifiers(s, {})
    assert len(ns) == 1  # missing env -> skipped
    ns = build_notifiers(s, {"HOOK": "https://hooks.example/x"})
    assert isinstance(ns[1], WebhookNotifier) and ns[1].url == "https://hooks.example/x"

def test_webhook_payload_and_failure_is_reported_not_raised(monkeypatch):
    sent = {}
    def fake_post(url, data, headers): sent.update(url=url, data=json.loads(data), headers=headers)
    monkeypatch.setattr(WebhookNotifier, "_post", staticmethod(fake_post))
    w = WebhookNotifier("https://h")
    assert notify_all([w], "Sub", "Body") == []
    assert sent["data"] == {"text": "Sub\n\nBody"}
    def boom(*a, **k): raise OSError("down")
    monkeypatch.setattr(WebhookNotifier, "_post", staticmethod(boom))
    errs = notify_all([w], "Sub", "Body")
    assert len(errs) == 1 and "WebhookNotifier" in errs[0] and "down" in errs[0]

def test_smtp_builds_message(monkeypatch):
    captured = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout=30): captured["hp"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self): captured["tls"] = True
        def login(self, u, p): captured["login"] = (u, p)
        def send_message(self, msg): captured["msg"] = msg
    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)
    n = SmtpNotifier("mail.example", 587, "u", "p", "from@example.com", "to@example.com")
    n.send("Hello", "World")
    assert captured["hp"] == ("mail.example", 587) and captured["tls"] and captured["login"] == ("u", "p")
    assert captured["msg"]["Subject"] == "Hello" and captured["msg"]["To"] == "to@example.com"

def test_build_message_graph():
    m = build_message("s", "b", "to@x.com")["message"]
    assert m["subject"] == "s" and m["toRecipients"][0]["emailAddress"]["address"] == "to@x.com"
    assert "attachments" not in m

def test_build_notifiers_smtp_and_graph_from_env():
    s = make_settings(notifications=({"type": "smtp"}, {"type": "graph"}))
    env = {"SMTP_HOST": "h", "SMTP_PORT": "25", "SMTP_USER": "u", "SMTP_PASSWORD": "p",
           "SMTP_FROM": "f@x", "SMTP_TO": "t@x",
           "GRAPH_TENANT_ID": "t", "GRAPH_CLIENT_ID": "c", "GRAPH_CLIENT_SECRET": "s",
           "GRAPH_SENDER": "snd@x", "GRAPH_TO": "to@x"}
    ns = build_notifiers(s, env)
    assert isinstance(ns[1], SmtpNotifier) and ns[1].port == 25
    assert isinstance(ns[2], GraphNotifier)
