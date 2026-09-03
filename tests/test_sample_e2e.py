import json, os, subprocess, sys
from pathlib import Path


def test_sample_input_builds_expected_csvs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "accounts.yaml").write_text('servicetitan: {campaign_category: "Paid Microsoft"}\ninitial_watermark: "2026-06-01T00:00:00+00:00"\n')
    (tmp_path / "map.json").write_text(json.dumps({"ids": {}, "dni": {}, "forms": []}))
    env = {**os.environ, "OCI_MAP_FILE": str(tmp_path / "map.json"), "OCI_DOWNLOADS_DIR": str(tmp_path / "none")}
    r = subprocess.run([sys.executable, "-m", "st_msads_oci.cli", "build", "--config", str(tmp_path / "accounts.yaml"),
                        "--input", str(root / "examples" / "sample-input.json"), "--project-dir", str(tmp_path),
                        "--no-notify", "--now", "2026-07-06T12:00:00Z"], capture_output=True, text=True, cwd=root, env=env)
    assert r.returncode == 0, r.stderr
    s = json.loads(r.stdout.strip().splitlines()[-1])
    # Locked from an actual run, not the brief's guess: the GOOD payload's booking (id 10) has
    # email=null/phones=[]/no leadCallId, so it's dropped at filter_rows ("no contact info")
    # before ever reaching tier assignment — only job 20 and call 8 land in tier B. tier_b is
    # 2, not the 3 the brief predicted.
    assert s["tier_a"] == 0 and s["tier_b"] == 2
    # dropped = booking 10 (no contact info) + the 3 added non-Microsoft rows (booking 11,
    # job 21, call 9), all filtered out by campaign category before tier assignment.
    assert s["dropped"] == 4
    pii = (tmp_path / "output" / "oci-pii.csv").read_text()
    assert pii.count("ServiceTitan Completed Jobs - MS") == 1 and "750.25,USD" in pii
    assert (tmp_path / "output" / "summary-latest.json").exists()
