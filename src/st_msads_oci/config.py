"""Per-shop configuration: accounts.yaml (non-secret) + environment (secrets)."""
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_GOAL_NAMES = {
    "completed_jobs": "ServiceTitan Completed Jobs - MS",
    "booked_web": "ServiceTitan Booked Job (Website) - MS",
    "booked_call": "ServiceTitan Booked Job (Call) - MS",
}
DEFAULT_INITIAL_WATERMARK = "2026-01-01T00:00:00+00:00"


@dataclass(frozen=True)
class Settings:
    campaign_category: str
    goal_names: dict
    legacy_goal_names: tuple
    initial_watermark: str
    test_emails: tuple
    test_phones: tuple
    worker_url: str | None
    tenant_id: str | None
    notifications: tuple

    def goal_name(self, key):
        return self.goal_names[key]

    def goal_key(self, name):
        for k, v in self.goal_names.items():
            if v == name:
                return k
        return None

    @property
    def known_result_names(self):
        return tuple(self.goal_names.values()) + tuple(self.legacy_goal_names)


def load_settings(path) -> Settings:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    st = raw.get("servicetitan") or {}
    ms = raw.get("microsoft_ads") or {}
    ti = raw.get("test_identities") or {}
    cat = st.get("campaign_category")
    if not cat:
        raise ValueError("accounts.yaml: servicetitan.campaign_category is required "
                         "(the ServiceTitan campaign category that marks Microsoft Ads traffic)")
    names = {**DEFAULT_GOAL_NAMES, **(ms.get("goal_names") or {})}
    unknown = set(names) - set(DEFAULT_GOAL_NAMES)
    if unknown:
        raise ValueError(f"accounts.yaml: unknown goal keys {sorted(unknown)}")
    return Settings(
        campaign_category=cat,
        goal_names=names,
        legacy_goal_names=tuple(ms.get("legacy_goal_names") or ()),
        initial_watermark=str(raw.get("initial_watermark") or DEFAULT_INITIAL_WATERMARK),
        test_emails=tuple(ti.get("emails") or ()),
        test_phones=tuple(ti.get("phones") or ()),
        worker_url=(raw.get("worker") or {}).get("url"),
        tenant_id=str(st["tenant_id"]) if st.get("tenant_id") is not None else None,
        notifications=tuple(raw.get("notifications") or ()),
    )


def load_env(dotenv_path=None) -> dict:
    """Values from an optional .env file, overridden by the real environment."""
    out = {}
    p = Path(dotenv_path) if dotenv_path else None
    if p and p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    out.update(os.environ)
    return out
