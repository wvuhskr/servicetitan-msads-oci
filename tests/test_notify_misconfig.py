"""A misconfigured notifier must never skip the ledger save or crash failure reporting.

Root cause: build_notifiers() can raise (a bare string where a {type: ...} mapping is
expected -> AttributeError on n.get("type"); a non-numeric SMTP_PORT -> ValueError), and
every caller assumed it could not. Two consequences, both regression-tested here:

1. build.py called it OUTSIDE the try/except that protects notifier .send(), AFTER the
   CSVs were already published to the Worker but BEFORE ledger.add_row /
   advance_watermarks / ledger.save. The exception escaped build(): rows served to
   Microsoft but never recorded, watermarks frozen, next run re-emits them.
2. cli.py's except-handler rebuilt notifiers to report a failed build. With the same
   misconfig that construction raised INSIDE the handler -> uncaught raw traceback and
   exit 1, instead of the clean exit 2 with a privacy-safe failure summary.

Hermetic: the bad entry is a bare string, so no network notifier is ever constructed.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.test_end_to_end import PAYLOAD

NOW = "2026-07-06T12:00:00Z"
MALFORMED_ACCOUNTS = (
    'servicetitan: {campaign_category: "Paid Microsoft"}\n'
    'test_identities: {emails: ["test.identity@example.com"], phones: ["(555) 555-0199"]}\n'
    'initial_watermark: "2026-06-01T00:00:00+00:00"\n'
    'notifications: ["webhook"]\n'   # bare string, not {type: webhook, url_env: ...}
)


def _run_build_with_notify(project_dir: Path, payload=PAYLOAD):
    (project_dir / "accounts.yaml").write_text(MALFORMED_ACCOUNTS)
    payload_path = project_dir / "input.json"
    payload_path.write_text(json.dumps(payload))
    map_path = project_dir / "oci_map.json"
    map_path.write_text(json.dumps({"ids": {}, "dni": {}, "forms": []}))
    env = {**os.environ, "OCI_DOWNLOADS_DIR": str(project_dir / "no_downloads"),
           "OCI_MAP_FILE": str(map_path)}
    # NOTE: no --no-notify — the notifier path must actually run for these tests to mean anything.
    return subprocess.run(
        [sys.executable, "-m", "st_msads_oci.cli", "build", "--config", str(project_dir / "accounts.yaml"),
         "--input", str(payload_path), "--project-dir", str(project_dir), "--now", NOW],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1], env=env)


def test_malformed_notifications_entry_does_not_block_ledger_save(tmp_path):
    result = _run_build_with_notify(tmp_path)

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout.strip().splitlines()[-1])

    # The misconfiguration is SURFACED, not swallowed silently ...
    assert any(e.startswith("build_notifiers:") for e in summary["notify_errors"]), summary["notify_errors"]
    # ... and never leaks a raw traceback (repo privacy rule for failure text).
    assert not any("Traceback" in e for e in summary["notify_errors"])

    # THE regression check: the ledger still saved and watermarks still advanced past the
    # 2026-06-01 initial watermark to the newest completed job in PAYLOAD (2026-07-02T15:30).
    ledger = json.loads((tmp_path / "state" / "ledger.json").read_text())
    assert ledger["watermarks"]["completed_jobs"].startswith("2026-07-02T15:30"), ledger["watermarks"]
    # and the served rows were recorded (3 tier-B rows in this payload; see test_end_to_end).
    assert len(ledger["uploaded"]) == 3


def test_build_failure_with_malformed_notifier_still_exits_cleanly(tmp_path):
    # An empty payload fails schema validation -> build() raises InputValidationError ->
    # cli's except-handler must still exit 2 with a privacy-safe summary, even though the
    # notifier it tries to build to report the failure is misconfigured.
    result = _run_build_with_notify(tmp_path, payload={})

    assert result.returncode == 2, (result.returncode, result.stderr)
    assert "input payload failed schema validation" in result.stderr
    assert "Traceback" not in result.stderr
