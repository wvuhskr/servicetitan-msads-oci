"""Build the two-tier Microsoft Ads offline-conversion CSVs from a ServiceTitan payload
and publish them to the Cloudflare Worker for Microsoft's daily pull.

Non-secret config arrives as a Settings object; secrets and hermetic-test knobs arrive as
an env dict (OCI_WORKER_BEARER, OCI_MAP_FILE, OCI_DOWNLOADS_DIR). Run summaries go to the
notifiers configured in accounts.yaml, never a hardcoded email."""
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .assemble import assemble_csv, assemble_cumulative, validate
from .clickid import fetch_map, publish_file, tier_rows
from .dedupe import dedupe
from .filter import attach_hashes, filter_rows
from .ledger import Ledger
from .notify import build_notifiers, notify_all
from .rows import GOAL_COMPLETED_JOBS, project_rows_from_payload, rows_from_payload
from .schema import validate_input, InputValidationError
from .triage import run_triage


def build(project_dir: Path, input_path: Path, settings, env: dict, now: datetime, notify: bool) -> dict:
    state_path = project_dir / "state" / "ledger.json"
    ledger = Ledger.load(state_path, settings.initial_watermark)
    today = now.strftime("%Y-%m-%d")

    # Worker map FIRST — unreachable Worker must abort before any CSV exists (spec §9).
    # OCI_MAP_FILE env override keeps tests hermetic (no network).
    map_file = env.get("OCI_MAP_FILE")
    if map_file:
        mapping = json.loads(Path(map_file).read_text())
    else:
        if not settings.worker_url:
            raise ValueError("accounts.yaml worker.url is required unless OCI_MAP_FILE is set")
        mapping = fetch_map(settings.worker_url, env["OCI_WORKER_BEARER"])

    downloads_dir = Path(env.get("OCI_DOWNLOADS_DIR", str(Path.home() / "Downloads")))
    findings = run_triage(ledger, project_dir / "results" / "inbox", downloads_dir,
                          project_dir / "results" / "archive", project_dir / "match_rate_history.csv",
                          parsed_on=today, settings=settings)
    ledger.save(state_path)

    payload = json.loads(Path(input_path).read_text())
    errs = validate_input(payload)
    if errs:
        raise InputValidationError("input payload failed schema validation:\n  " + "\n  ".join(errs[:30]))
    calls_by_id = {c["id"]: c for c in payload.get("calls", [])}
    all_rows = rows_from_payload(payload)
    proj_rows, project_member_ids, proj_findings, proj_dropped = \
        project_rows_from_payload(payload, ledger, now, today, settings.campaign_category)
    # Project members leave the per-job path — the project row (or a finding) represents
    # them. all_rows stays intact for advance_watermarks below.
    per_job_rows = [r for r in all_rows
                    if not (r.goal == GOAL_COMPLETED_JOBS and r.st_id in project_member_ids)]
    kept, dropped = filter_rows(per_job_rows + proj_rows, settings)
    dropped += proj_dropped
    kept, hash_failed = attach_hashes(kept)
    dropped += hash_failed
    kept, dd = dedupe(kept, ledger)
    dropped += dd

    tier_a, tier_b, withheld = tier_rows(kept, mapping, calls_by_id)
    validate(tier_a + tier_b, now=now)
    assert all(r.msclkid for r in tier_a) and not any(r.msclkid for r in tier_b)

    out = project_dir / "output"
    # Dated audit copies: only when there ARE new rows (unchanged behavior).
    if tier_a or tier_b:
        assemble_csv(tier_a, out / f"oci-clickid_{today}.csv", settings.goal_names, merge=True)
        assemble_csv(tier_b, out / f"oci-pii_{today}.csv", settings.goal_names, merge=True)
    # Served "latest" files = deduped last-CUMULATIVE_DAYS union of the dated copies, rebuilt and
    # published on EVERY run (incl. 0-new-row days) so a row survives later runs / sleep gaps until
    # MS pulls it (spec 2026-08-18). Built AFTER the dated write above so today's rows are included.
    assemble_cumulative(out / "oci-clickid.csv", out, "oci-clickid", now)
    assemble_cumulative(out / "oci-pii.csv", out, "oci-pii", now)
    published = False
    if not map_file:  # skip network publish in hermetic tests
        ok_a = publish_file(settings.worker_url, env["OCI_WORKER_BEARER"],
                            "oci-clickid.csv", out / "oci-clickid.csv")
        ok_b = publish_file(settings.worker_url, env["OCI_WORKER_BEARER"],
                            "oci-pii.csv", out / "oci-pii.csv")
        published = ok_a and ok_b
        if not published:
            raise RuntimeError("publish to Worker failed — MS will serve the header-only staleness file")

    total = len(tier_a) + len(tier_b) + len(withheld)
    capture = round(len(tier_a) / total, 3) if total else None
    per_goal = Counter(r.goal for r in tier_a + tier_b)

    notify_errors = []
    if notify and (tier_a or tier_b or withheld or findings or proj_findings):
        body_lines = [f"Tier A (click id): {len(tier_a)}   Tier B (PII only): {len(tier_b)}   "
                      f"Withheld: {len(withheld)}   Capture rate: {capture}"]
        body_lines += [f"  {g}: {n}" for g, n in sorted(per_goal.items())]
        body_lines += [f"  A: {r.goal} st_id={r.st_id} via {r.click_source}" for r in tier_a]
        body_lines += [f"  withheld: {d.reason} (st id {d.st_id})" for d in withheld[:20]]
        body_lines.append(f"Dropped (filters/dedupe): {len(dropped)}")
        body_lines += [f"  {d.reason} (st id {d.st_id})" for d in dropped[:20]]
        if findings:
            body_lines.append("Triage:")
            body_lines += [f"  {f}" for f in findings]
        if proj_findings:
            body_lines.append("Projects:")
            body_lines += [f"  {f}" for f in proj_findings]
        body_lines.append("Files published to the Worker; Microsoft pulls daily. No manual upload.")
        notify_errors = notify_all(build_notifiers(settings, env),
                                   f"MS Ads offline conversions — {today} (A:{len(tier_a)} B:{len(tier_b)} W:{len(withheld)})",
                                   "\n".join(body_lines))

    for r in tier_a:
        ledger.add_row(r, today, tier="A")
    for r in tier_b:
        ledger.add_row(r, today, tier="B")
    ledger.advance_watermarks(all_rows)
    ledger.save(state_path)

    summary = {"tier_a": len(tier_a), "tier_b": len(tier_b), "withheld": len(withheld),
               "capture_rate": capture, "dropped": len(dropped), "findings": len(findings),
               "project_findings": len(proj_findings), "published": published,
               "notify_errors": notify_errors}
    (out / "summary-latest.json").write_text(json.dumps(summary, indent=1))
    return summary
