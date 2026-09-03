"""Minimal ServiceTitan REST client: OAuth client-credentials, ST-App-Key, paging, one retry."""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import certifi


class ServiceTitanError(Exception):
    def __init__(self, status, path, body):
        super().__init__(f"ServiceTitan HTTP {status} on {path}: {body}")
        self.status = status


def _default_opener(req):
    ctx = ssl.create_default_context(cafile=certifi.where())
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


class ServiceTitanClient:
    def __init__(self, client_id, client_secret, app_key, tenant_id, base="https://api.servicetitan.io",
                 auth_url="https://auth.servicetitan.io/connect/token", opener=None, sleep=time.sleep):
        self.client_id, self.client_secret, self.app_key, self.tenant = client_id, client_secret, app_key, str(tenant_id)
        self.base, self.auth_url, self._open, self._sleep = base.rstrip("/"), auth_url, opener or _default_opener, sleep
        self._token, self._token_exp = None, 0.0

    def _bearer(self):
        if self._token and time.time() < self._token_exp:
            return self._token
        data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self.client_id,
                                       "client_secret": self.client_secret}).encode()
        req = urllib.request.Request(self.auth_url, data=data, method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        status, _, body = self._open(req)
        if status != 200:
            raise ServiceTitanError(status, "connect/token", body[:300])
        tok = json.loads(body)
        self._token, self._token_exp = tok["access_token"], time.time() + int(tok.get("expires_in", 900)) - 60
        return self._token

    def _request(self, path, params):
        url = self.base + path.replace("{tenant}", self.tenant)
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        for attempt in (0, 1):
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._bearer()}",
                                                       "ST-App-Key": self.app_key, "Accept": "application/json"})
            try:
                status, headers, body = self._open(req)
            except urllib.error.URLError as e:
                if attempt:
                    raise ServiceTitanError(0, path, str(e))
                self._sleep(2); continue
            if status == 429 or status >= 500:
                if attempt:
                    raise ServiceTitanError(status, path, body[:300])
                ra = headers.get("Retry-After") or headers.get("retry-after")
                self._sleep(min(int(ra), 30) if ra and str(ra).isdigit() else 2); continue
            return status, body
        raise AssertionError("unreachable")

    def get(self, path, params=None):
        status, body = self._request(path, params)
        if status != 200:
            raise ServiceTitanError(status, path, body[:300])
        return json.loads(body)

    def get_optional(self, path, params=None):
        status, body = self._request(path, params)
        if status == 404:
            return None
        if status != 200:
            raise ServiceTitanError(status, path, body[:300])
        return json.loads(body)

    def get_paged(self, path, params=None, page_size=200):
        page = 1
        while True:
            res = self.get(path, {**(params or {}), "page": page, "pageSize": page_size})
            data = res.get("data") or []
            yield from data
            if not res.get("hasMore") or len(data) < page_size:
                return
            page += 1
