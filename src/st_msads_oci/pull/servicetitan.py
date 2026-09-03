"""Standalone ServiceTitan pull -> input payload (see pull/INPUT_FORMAT.md). v1 scope: campaigns,
bookings, completed jobs (+invoice totals), booked calls. project_jobs / suspect recovery are v2."""
import json
import os
import sys
import re
import traceback
from datetime import timedelta, timezone
from pathlib import Path

from ..ledger import Ledger
from ..notify import build_notifiers, notify_all
from ..rows import GOAL_BOOKED_JOB, GOAL_BOOKED_JOB_CALL, GOAL_COMPLETED_JOBS
from ..schema import validate_input
from .st_client import ServiceTitanClient, ServiceTitanError

ENDPOINTS = {
    "campaigns": "/marketing/v2/tenant/{tenant}/campaigns",
    "bookings": "/crm/v2/tenant/{tenant}/bookings",
    "jobs": "/jpm/v2/tenant/{tenant}/jobs",
    "job": "/jpm/v2/tenant/{tenant}/jobs/{id}",
    "invoices": "/accounting/v2/tenant/{tenant}/invoices",
    "customer": "/crm/v2/tenant/{tenant}/customers/{id}",
    "contacts": "/crm/v2/tenant/{tenant}/customers/{id}/contacts",
    "calls": "/telecom/v2/tenant/{tenant}/calls",
}
OVERLAP = timedelta(hours=1)
_LINE = re.compile(r"^[ \t]*([^:\n]{1,60}?)[ \t]*\*?[ \t]*:[ \t]*(.*?)[ \t]*$", re.M)


def parse_webform_summary(summary):
    email, phones = None, []
    for label, value in _LINE.findall(summary or ""):
        l = label.lower()
        if not value:
            continue
        if "mail" in l and "@" in value and not email:
            email = value
        elif "phone" in l or "tel" in l:
            phones.append(value)
    return email, phones


def windows(ledger, now):
    def iso(g):
        return (ledger.watermarks[g] - OVERLAP).astimezone(timezone.utc).isoformat()
    return {"bookings": iso(GOAL_BOOKED_JOB), "jobs": iso(GOAL_COMPLETED_JOBS), "calls": iso(GOAL_BOOKED_JOB_CALL)}


class _Contacts:
    def __init__(self, client, log):
        self.c, self.log, self.memo = client, log, {}

    def get(self, customer_id):
        if customer_id in self.memo:
            return self.memo[customer_id]
        name, email, phones = None, None, []
        try:
            cust = self.c.get_optional(ENDPOINTS["customer"].replace("{id}", str(customer_id))) or {}
            name = cust.get("name")
            for ct in self.c.get_paged(ENDPOINTS["contacts"].replace("{id}", str(customer_id))):
                t, v = (ct.get("type") or ""), ct.get("value")
                if not v:
                    continue
                if t == "Email" and not email:
                    email = v
                elif "Phone" in t:
                    phones.append(v)
        except ServiceTitanError as e:
            self.log(f"contacts for customer {customer_id} failed, leaving blank: {e}")
        self.memo[customer_id] = (name, email, phones)
        return self.memo[customer_id]


def pull_all(client, settings, ledger, now, log=print):
    win = windows(ledger, now)
    contacts = _Contacts(client, log)
    enriched = 0

    campaigns = [{"id": c["id"], "name": c.get("name"), "category": (c.get("category") or {}).get("name")}
                 for c in client.get_paged(ENDPOINTS["campaigns"])]
    ms_ids = {c["id"] for c in campaigns if c["category"] == settings.campaign_category}
    log(f"{len(campaigns)} campaigns, {len(ms_ids)} in category {settings.campaign_category!r}")

    bookings = []
    for b in client.get_paged(ENDPOINTS["bookings"], {"createdOnOrAfter": win["bookings"]}):
        row = {"id": b["id"], "createdOn": b.get("createdOn"), "campaignId": b.get("campaignId"),
               "customerName": b.get("name"), "email": None, "phones": []}
        if b.get("campaignId") in ms_ids:
            enriched += 1
            if b.get("source") == "WebFormIntegration":
                row["email"], row["phones"] = parse_webform_summary(b.get("summary"))
            elif b.get("jobId"):
                job = client.get_optional(ENDPOINTS["job"].replace("{id}", str(b["jobId"]))) or {}
                if job.get("customerId"):
                    name, email, phones = contacts.get(job["customerId"])
                    row["customerName"] = name or row["customerName"]
                    row["email"], row["phones"] = email, phones
        bookings.append(row)

    jobs = []
    for j in client.get_paged(ENDPOINTS["jobs"], {"completedOnOrAfter": win["jobs"]}):
        if not j.get("completedOn"):
            continue
        row = {"id": j["id"], "completedOn": j["completedOn"], "campaignId": j.get("campaignId"),
               "customerName": None, "email": None, "phones": [], "invoiceTotal": None,
               "leadCallId": j.get("leadCallId"), "bookingId": j.get("bookingId"), "projectId": j.get("projectId")}
        if j.get("campaignId") in ms_ids:
            enriched += 1
            # ServiceTitan returns money fields as decimal strings
            row["invoiceTotal"] = sum(float(inv.get("total") or 0)
                                      for inv in client.get_paged(ENDPOINTS["invoices"], {"jobId": j["id"]}))
            if j.get("customerId"):
                row["customerName"], row["email"], row["phones"] = contacts.get(j["customerId"])
        jobs.append(row)

    calls = []
    for item in client.get_paged(ENDPOINTS["calls"], {"createdOnOrAfter": win["calls"]}):
        lc = item.get("leadCall") or {}
        if lc.get("callType") != "Booked":
            continue
        campaign_id = (lc.get("campaign") or {}).get("id")
        row = {"id": lc["id"], "receivedOn": lc.get("receivedOn"), "to": lc.get("to"), "from": lc.get("from"),
               "direction": lc.get("direction"), "callType": "Booked", "campaign": {"id": campaign_id},
               "customerName": None, "email": None, "phones": []}
        if campaign_id in ms_ids:
            enriched += 1
            cust = lc.get("customer") or {}
            row["customerName"] = cust.get("name")
            row["email"] = cust.get("email") or None
            row["phones"] = [c["value"] for c in cust.get("contacts") or []
                             if "Phone" in (c.get("type") or "") and c.get("value")]
            if not (cust.get("contacts") or []) and cust.get("id"):
                name, email, phones = contacts.get(cust["id"])
                row["customerName"] = row["customerName"] or name
                row["email"] = row["email"] or email
                row["phones"] = phones
        calls.append(row)

    log(f"pulled {len(bookings)} bookings, {len(jobs)} jobs, {len(calls)} booked calls, {enriched} enriched")
    return {"generated_at": now.isoformat(), "campaigns": campaigns, "bookings": bookings,
            "jobs": jobs, "calls": calls, "_enriched": enriched}


def write_payload_atomic(payload, out_path):
    payload = {k: v for k, v in payload.items() if k != "_enriched"}
    errors = validate_input(payload)
    if errors:
        raise ValueError("input payload is invalid:\n  " + "\n  ".join(errors[:20]))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1))
    os.replace(tmp, out_path)


def run_pull(settings, env, project_dir, out_path, now):
    try:
        creds = {}
        for key in ("ST_CLIENT_ID", "ST_CLIENT_SECRET", "ST_APP_KEY"):
            if not env.get(key):
                raise ValueError(f"missing {key} in .env — needed for the ServiceTitan pull")
            creds[key] = env[key]
        if not settings.tenant_id:
            raise ValueError("missing tenant_id in the config file — needed for the ServiceTitan pull")
        client = ServiceTitanClient(creds["ST_CLIENT_ID"], creds["ST_CLIENT_SECRET"], creds["ST_APP_KEY"],
                                    settings.tenant_id)
        ledger = Ledger.load(Path(project_dir) / "state" / "ledger.json", settings.initial_watermark)
        payload = pull_all(client, settings, ledger, now)
        write_payload_atomic(payload, out_path)
        print(json.dumps({"bookings": len(payload["bookings"]), "jobs": len(payload["jobs"]),
                          "calls": len(payload["calls"]), "campaigns": len(payload["campaigns"]),
                          "enriched": payload["_enriched"], "out": str(out_path)}))
        return 0
    except Exception:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        notify_all(build_notifiers(settings, env), f"ServiceTitan pull FAILED — {now:%Y-%m-%d}", tb)
        return 2
