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
    assert len(errs) == 1 and "WebhookNotifier" in errs[0] and "OSError" in errs[0]

def test_smtp_builds_message(monkeypatch):
    captured = {}
    class FakeSMTP:
        def __init__(self, host, port, timeout=30): captured["hp"] = (host, port)
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): captured["tls"] = True
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


def test_smtp_requires_verified_tls_before_login(monkeypatch):
    import ssl
    for port in (25, 587, 465):
        events = []
        class MailTransport:
            def __init__(self, host, port, timeout=30, context=None):
                if port == 465:
                    assert context is not None
                    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
                    events.append('tls')
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def starttls(self, context=None):
                assert context is not None
                assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
                events.append('tls')
            def login(self, user, password):
                assert events == ['tls'], 'credentials sent before verified TLS'
                events.append('login')
            def send_message(self, message): events.append('send')
        monkeypatch.setattr('smtplib.SMTP', MailTransport)
        monkeypatch.setattr('smtplib.SMTP_SSL', MailTransport)
        SmtpNotifier('mail.example', port, 'u', 'p', 'f@example.com', 't@example.com').send('s', 'b')
        assert events == ['tls', 'login', 'send']


def test_smtp_rejects_untrusted_certificate_before_credentials(monkeypatch, tmp_path):
    """Exercise the real smtplib TLS handshake using an untrusted local certificate."""
    import socket
    import ssl
    import subprocess
    import threading
    import pytest
    import smtplib
    key, cert = tmp_path / 'key.pem', tmp_path / 'cert.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                    '-keyout', str(key), '-out', str(cert), '-days', '1',
                    '-subj', '/CN=localhost'], check=True, capture_output=True)
    client, server = socket.socketpair()
    client.settimeout(3); server.settimeout(3)
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert, key)
    def serve():
        try:
            with server_ctx.wrap_socket(server, server_side=True) as encrypted:
                encrypted.recv(1)
        except (ssl.SSLError, OSError):
            pass
    thread = threading.Thread(target=serve, daemon=True); thread.start()
    logins = []
    class LocalSMTP(smtplib.SMTP):
        def __init__(self, *args, **kwargs):
            self.sock, self.file, self._host = client, None, 'localhost'
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
        def ehlo_or_helo_if_needed(self): pass
        def has_extn(self, name): return name == 'starttls'
        def docmd(self, *args): return 220, b'ready'
        def login(self, *args): logins.append(True)
        def send_message(self, *args): pass
    monkeypatch.setattr(smtplib, 'SMTP', LocalSMTP)
    try:
        with pytest.raises(ssl.SSLCertVerificationError):
            SmtpNotifier('localhost', 587, 'u', 'p', 'f@x.com', 't@x.com').send('s', 'b')
        assert logins == []
    finally:
        client.close(); server.close(); thread.join(timeout=4)
