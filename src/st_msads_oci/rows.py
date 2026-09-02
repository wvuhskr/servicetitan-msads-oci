import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

GOAL_COMPLETED_JOBS = "ServiceTitan Completed Jobs - MS"
GOAL_BOOKED_JOB = "ServiceTitan Booked Job (Website) - MS"
GOAL_BOOKED_JOB_CALL = "ServiceTitan Booked Job (Call) - MS"
ALL_GOALS = (GOAL_COMPLETED_JOBS, GOAL_BOOKED_JOB, GOAL_BOOKED_JOB_CALL)
# Old goal names — still needed to parse historical MS result files in triage
LEGACY_GOALS = ("ServiceTitan Integrated Bookings - MS", "ServiceTitan Lead - MS")


def parse_utc(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class UploadRow:
    goal: str
    st_id: int
    ts: datetime
    campaign_id: int | None
    campaign_name: str
    campaign_category: str
    customer_name: str
    raw_email: str | None
    raw_phones: list = field(default_factory=list)
    lead_call_id: int | None = None
    booking_id: int | None = None
    email_hash: str | None = None
    phone_hash: str | None = None
    value: float | None = None
    msclkid: str | None = None
    click_source: str | None = None
    withheld_reason: str | None = None
    project_id: int | None = None
    member_job_ids: list | None = None
    recovered_via: str | None = None   # "call" | "pii" — suspect fallback (spec 2026-08-05)


@dataclass
class Dropped:
    goal: str
    st_id: int
    reason: str


def rows_from_payload(payload: dict) -> list:
    camps = {c["id"]: c for c in payload.get("campaigns", [])}

    def campaign(cid):
        c = camps.get(cid, {})
        return c.get("name", "unknown"), c.get("category", "unknown")

    rows = []
    for b in payload.get("bookings", []):
        name, cat = campaign(b.get("campaignId"))
        rows.append(UploadRow(GOAL_BOOKED_JOB, b["id"], parse_utc(b["createdOn"]), b.get("campaignId"),
                              name, cat, b.get("customerName", ""), b.get("email"), b.get("phones") or []))
    for j in payload.get("jobs", []):
        name, cat = campaign(j.get("campaignId"))
        rows.append(UploadRow(GOAL_COMPLETED_JOBS, j["id"], parse_utc(j["completedOn"]), j.get("campaignId"),
                              name, cat, j.get("customerName", ""), j.get("email"), j.get("phones") or [],
                              lead_call_id=j.get("leadCallId"), booking_id=j.get("bookingId"),
                              value=j.get("invoiceTotal")))
    for c in payload.get("calls", []):
        if c.get("callType") != "Booked":
            continue  # job leadCalls / suspect candidates carry no callType — not rows
        cid = c.get("campaignId") or (c.get("campaign") or {}).get("id")
        name, cat = campaign(cid)
        rows.append(UploadRow(GOAL_BOOKED_JOB_CALL, c["id"], parse_utc(c["receivedOn"]), cid,
                              name, cat, c.get("customerName", ""), c.get("email"),
                              c.get("phones") or [], lead_call_id=c["id"]))
    # "leads" key ignored — leads are no longer pulled (goal deleted)
    return rows


PROJECT_WINDOW_DAYS = 90     # MS 90-day click window (goal setting) — older leads get rejected
PENDING_REPORT_DAYS = 30
SETTLED_STATUSES = {"Completed", "Canceled"}


def _lead_job(jobs):
    leads = [j for j in jobs
             if j.get("leadCallId") or j.get("bookingId") or j.get("partnerLeadCallId")]
    return min(leads, key=lambda j: parse_utc(j["createdOn"])) if leads else None


SUSPECT_CALL_WINDOW_D = 7    # spec 2026-08-05: lead-call inference lookback


def _digits10(p):
    d = re.sub(r"\D", "", p or "")
    return d[-10:] if len(d) >= 10 else None


def _infer_lead_call(proxy, jobs, calls):
    """Earliest inbound call from the customer's phone on the proxy leg's campaign, within
    7 days before the project's earliest job creation (spec 2026-08-05, strict match)."""
    # spec: PII (including phones) can land on any leg, not just proxy — match the same
    # contact leg _recover_suspect uses, not proxy's own (usually empty) phones.
    contact = next((j for j in jobs if j.get("email") or j.get("phones")), proxy)
    phones = {_digits10(p) for p in (contact.get("phones") or [])} - {None}
    if not phones or not calls:
        return None
    created = min(parse_utc(j["createdOn"]) for j in jobs)

    def _camp(c):
        return c.get("campaignId") or (c.get("campaign") or {}).get("id")

    cands = [c for c in calls
             if c.get("direction") == "Inbound"
             and _digits10(c.get("from")) in phones
             and _camp(c) == proxy.get("campaignId")
             and created - timedelta(days=SUSPECT_CALL_WINDOW_D)
                 <= parse_utc(c["receivedOn"]) <= created]
    return min(cands, key=lambda c: parse_utc(c["receivedOn"])) if cands else None


def _recover_suspect(pid, jobs, camps, calls, value, now):
    """Fallback ladder for settled Paid-MS projects with no lead-marked job (spec 2026-08-05).

    Returns (row, finding), (None, finding) for report-once-and-drop, or (None, None)
    for withhold-and-stay-pending."""
    proxy = min((j for j in jobs if j["jobStatus"] == "Completed"),
                key=lambda j: parse_utc(j["createdOn"]))
    c = camps.get(proxy.get("campaignId"), {})
    if c.get("category") != "Paid Microsoft":
        return None, (f"project {pid}: attribution_suspect on non-MS campaign — "
                      f"reported once, dropped")
    call = _infer_lead_call(proxy, jobs, calls)
    # spec: the customer is identical across a project's legs, so PII landing on a
    # non-proxy leg must still recover — proxy stays the fallback source
    contact = next((j for j in jobs if j.get("email") or j.get("phones")), proxy)
    if call:
        ts, via = parse_utc(call["receivedOn"]), "call"
    elif contact.get("email") or contact.get("phones"):
        ts, via = parse_utc(proxy["completedOn"]), "pii"
    else:
        return None, None
    if now - ts > timedelta(days=PROJECT_WINDOW_DAYS):
        return None, f"project {pid}: window_expired — recovered ts {ts.date()}, skipped"
    row = UploadRow(GOAL_COMPLETED_JOBS, proxy["id"], ts, proxy.get("campaignId"),
                    c.get("name", "unknown"), c.get("category", "unknown"),
                    contact.get("customerName", ""), contact.get("email"),
                    contact.get("phones") or [],
                    lead_call_id=call["id"] if call else None,
                    value=value, project_id=pid,
                    member_job_ids=sorted(j["id"] for j in jobs),
                    recovered_via=via)
    return row, f"project {pid}: suspect_recovered_{via} — recovery row built (tier {'A' if via == 'call' else 'B'})"


def project_rows_from_payload(payload, ledger, now, today):
    """One UploadRow per settled project, keyed and timed by its lead job (spec 2026-08-03).

    Returns (rows, member_job_ids, findings, dropped). Mutates ledger.pending_projects.
    member_job_ids covers every job seen in any project — the caller removes those from
    the per-job path regardless of the project's outcome (the project row, or a finding,
    represents them; an install leg must never upload on its own again).
    """
    camps = {c["id"]: c for c in payload.get("campaigns", [])}
    rows, findings, dropped = [], [], []
    project_jobs = {}
    for pid, jobs in (payload.get("project_jobs") or {}).items():
        try:
            project_jobs[int(pid)] = jobs
        except (TypeError, ValueError):
            findings.append(f"project {pid}: malformed job data (bad project id) — skipped, will retry via pending")
    member_ids = {j["id"] for jobs in project_jobs.values() for j in jobs}
    member_ids |= {j["id"] for j in payload.get("jobs", []) if j.get("projectId")}
    pending = {p["project_id"]: dict(p) for p in ledger.pending_projects}
    payload_pids = {j["projectId"] for j in payload.get("jobs", []) if j.get("projectId")}

    for pid in sorted(payload_pids | set(project_jobs) | set(pending)):
        try:
            jobs = project_jobs.get(pid)
            if not jobs:
                findings.append(f"project {pid}: missing from project_jobs — check runbook pull")
                pending.setdefault(pid, {"project_id": pid, "first_seen": today})
                continue
            prior = next((e for e in ledger.uploaded if e.get("project_id") == pid), None)
            if prior:  # already uploaded — late revenue is report-only, never a second row
                late = [j for j in jobs if j["jobStatus"] == "Completed"
                        and j["id"] not in (prior.get("member_job_ids") or [])]
                if late:
                    total = sum(j.get("total") or 0 for j in late)
                    findings.append(f"project {pid}: late_project_revenue ${total:.2f} — not uploadable")
                # late-revision detector: settled revenue moved after upload — flag it,
                # MS keeps the originally-uploaded value regardless (never re-uploadable)
                stored = prior.get("value")
                if stored is not None:
                    recomputed = sum(j.get("total") or 0 for j in jobs if j["jobStatus"] == "Completed")
                    if recomputed != stored:
                        findings.append(f"project {pid}: revenue revised {stored} -> {recomputed} "
                                        f"after upload — not re-uploadable, MS keeps original")
                pending.pop(pid, None)
                continue
            if ledger.has_project_overlap(GOAL_COMPLETED_JOBS, pid, [j["id"] for j in jobs]):
                dropped.append(Dropped(GOAL_COMPLETED_JOBS, jobs[0]["id"],
                                       f"project {pid}: member job already uploaded"))
                pending.pop(pid, None)
                continue
            if any(j["jobStatus"] not in SETTLED_STATUSES for j in jobs):
                entry = pending.setdefault(pid, {"project_id": pid, "first_seen": today})
                age = (date.fromisoformat(today) - date.fromisoformat(entry["first_seen"])).days
                if age > PENDING_REPORT_DAYS:
                    findings.append(f"project {pid}: pending {age} days (open jobs remain)")
                continue
            value = sum(j.get("total") or 0 for j in jobs if j["jobStatus"] == "Completed")
            if not value > 0:
                dropped.append(Dropped(GOAL_COMPLETED_JOBS, jobs[0]["id"],
                                       f"project {pid}: settled with no completed revenue"))
                pending.pop(pid, None)
                continue
            lead = _lead_job(jobs)
            if lead is None:
                # spec 2026-08-05 fallback ladder: call inference -> PII -> withhold+pend
                row, finding = _recover_suspect(pid, jobs, camps, payload.get("calls", []),
                                                value, now)
                if finding:
                    findings.append(finding)
                else:
                    findings.append(f"project {pid}: attribution_suspect — no lead job, withheld")
                if row:
                    rows.append(row)
                if row or finding:
                    pending.pop(pid, None)
                else:
                    # rung 3: stay pending — a lead leg or contact info added in ST later
                    # still gets picked up; the finding repeats daily so it can't go silent
                    pending.setdefault(pid, {"project_id": pid, "first_seen": today})
                continue
            if lead["jobStatus"] != "Completed" or not lead.get("completedOn"):
                findings.append(f"project {pid}: lead job {lead['id']} not Completed — "
                                f"withheld (no defensible conversion time)")
                pending.pop(pid, None)
                continue
            ts = parse_utc(lead["completedOn"])
            if now - ts > timedelta(days=PROJECT_WINDOW_DAYS):
                findings.append(f"project {pid}: window_expired — lead completed {ts.date()}, skipped")
                pending.pop(pid, None)
                continue
            c = camps.get(lead.get("campaignId"), {})
            rows.append(UploadRow(GOAL_COMPLETED_JOBS, lead["id"], ts, lead.get("campaignId"),
                                  c.get("name", "unknown"), c.get("category", "unknown"),
                                  lead.get("customerName", ""), lead.get("email"),
                                  lead.get("phones") or [],
                                  lead_call_id=lead.get("leadCallId"), booking_id=lead.get("bookingId"),
                                  value=value, project_id=pid,
                                  member_job_ids=sorted(j["id"] for j in jobs)))
            pending.pop(pid, None)
        except Exception as exc:  # one malformed job can't abort the whole run
            findings.append(f"project {pid}: malformed job data ({exc}) — skipped, will retry via pending")
            pending.setdefault(pid, {"project_id": pid, "first_seen": today})

    ledger.pending_projects = sorted(pending.values(), key=lambda p: p["project_id"])
    return rows, member_ids, findings, dropped
