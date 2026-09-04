"""Run-summary notifiers. Stdout is always on; the rest are optional and configured in
accounts.yaml `notifications:` with secrets from the environment. A failing notifier is
reported in the returned error list and never aborts the run."""
import json
import smtplib
import ssl
import sys
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

import certifi

from .schema import InputValidationError


def _ctx():
    return ssl.create_default_context(cafile=certifi.where())


class Notifier(Protocol):
    def send(self, subject: str, body: str) -> None: ...


class StdoutNotifier:
    def send(self, subject, body):
        print(f"== {subject} ==\n{body}", file=sys.stdout)


class WebhookNotifier:
    """Slack / Teams / generic incoming webhook: POSTs {"text": ...}."""
    def __init__(self, url):
        self.url = url

    @staticmethod
    def _post(url, data, headers):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
            if r.status >= 300:
                raise RuntimeError(f"webhook HTTP {r.status}")

    def send(self, subject, body):
        self._post(self.url, json.dumps({"text": f"{subject}\n\n{body}"}).encode(),
                   {"Content-Type": "application/json"})


class SmtpNotifier:
    def __init__(self, host, port, user, password, sender, to):
        self.host, self.port, self.user, self.password, self.sender, self.to = host, int(port), user, password, sender, to

    def send(self, subject, body):
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, self.sender, self.to
        msg.set_content(body)
        context = _ctx()
        transport = (smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=context)
                     if self.port == 465 else smtplib.SMTP(self.host, self.port, timeout=30))
        with transport as s:
            if self.port != 465:
                s.starttls(context=context)
            if self.user:
                s.login(self.user, self.password)
            s.send_message(msg)


def build_message(subject, body, to, attachment_path=None):
    import base64
    message = {"subject": subject, "body": {"contentType": "Text", "content": body},
               "toRecipients": [{"emailAddress": {"address": to}}]}
    if attachment_path:
        p = Path(attachment_path)
        message["attachments"] = [{"@odata.type": "#microsoft.graph.fileAttachment", "name": p.name,
                                   "contentType": "text/csv",
                                   "contentBytes": base64.b64encode(p.read_bytes()).decode()}]
    return {"message": message, "saveToSentItems": True}


class GraphNotifier:
    """Microsoft 365 via Graph sendMail (application permission Mail.Send)."""
    def __init__(self, tenant_id, client_id, client_secret, sender, to):
        self.tenant_id, self.client_id, self.client_secret, self.sender, self.to = tenant_id, client_id, client_secret, sender, to

    def _token(self):
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self.client_secret,
                                       "scope": "https://graph.microsoft.com/.default"}).encode()
        url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30, context=_ctx()) as r:
            return json.load(r)["access_token"]

    def send(self, subject, body):
        req = urllib.request.Request(
            f"https://graph.microsoft.com/v1.0/users/{self.sender}/sendMail",
            data=json.dumps(build_message(subject, body, self.to)).encode(),
            headers={"Authorization": f"Bearer {self._token()}", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30, context=_ctx()) as r:
            if r.status not in (200, 202):
                raise RuntimeError(f"sendMail failed: HTTP {r.status}")


def build_notifiers(settings, env):
    out = [StdoutNotifier()]
    for n in settings.notifications:
        t = n.get("type")
        if t == "webhook":
            url = env.get(n.get("url_env", "NOTIFY_WEBHOOK_URL"))
            if url:
                out.append(WebhookNotifier(url))
        elif t == "smtp" and env.get("SMTP_HOST") and env.get("SMTP_TO"):
            out.append(SmtpNotifier(env["SMTP_HOST"], env.get("SMTP_PORT", "587"), env.get("SMTP_USER"),
                                    env.get("SMTP_PASSWORD"), env.get("SMTP_FROM") or env.get("SMTP_USER"), env["SMTP_TO"]))
        elif t == "graph" and all(env.get(k) for k in ("GRAPH_TENANT_ID", "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET", "GRAPH_SENDER", "GRAPH_TO")):
            out.append(GraphNotifier(env["GRAPH_TENANT_ID"], env["GRAPH_CLIENT_ID"], env["GRAPH_CLIENT_SECRET"],
                                     env["GRAPH_SENDER"], env["GRAPH_TO"]))
    return out


def failure_summary(exc):
    """Do not serialize arbitrary exception messages, URLs, response bodies or traces."""
    if isinstance(exc, InputValidationError):
        return str(exc)
    result = type(exc).__name__
    status = getattr(exc, "status", None) or getattr(exc, "code", None)
    if type(status) is int and 100 <= status <= 599:
        result += f" (HTTP {status})"
    return result + ": operation failed; sensitive error details omitted."


def notify_all(notifiers, subject, body):
    errors = []
    for n in notifiers:
        try:
            n.send(subject, body)
        except Exception as exc:  # a dead notifier must not kill the run
            errors.append(f"{type(n).__name__}: {failure_summary(exc)}")
    return errors
