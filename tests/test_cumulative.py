from datetime import datetime, timezone
from st_msads_oci.assemble import assemble_cumulative, PARAMS, HEADER

NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=timezone.utc)


def _write(dir, name, rows):
    (dir / name).write_text("\n".join([PARAMS, HEADER, *rows]) + "\n")


def _clickid_row(mk, name, when):
    # msclkid,ConversionName,ConversionTime,Value,Currency,EmailHash,PhoneHash
    return f"{mk},{name},{when},,,{'a'*64},{'b'*64}"


def test_window_includes_le_n_days_excludes_gt_n_days(tmp_path):
    _write(tmp_path, "oci-clickid_2026-08-13.csv", [_clickid_row("m13", "G", "8/13/2026 1:00:00 PM")])  # 5d
    _write(tmp_path, "oci-clickid_2026-08-04.csv", [_clickid_row("m04", "G", "8/4/2026 1:00:00 PM")])   # 14d
    _write(tmp_path, "oci-clickid_2026-08-03.csv", [_clickid_row("m03", "G", "8/3/2026 1:00:00 PM")])   # 15d -> out
    n = assemble_cumulative(tmp_path / "oci-clickid.csv", tmp_path, "oci-clickid", NOW)
    text = (tmp_path / "oci-clickid.csv").read_text()
    assert n == 2
    assert "m13" in text and "m04" in text
    assert "m03" not in text


def test_dedup_identical_rows_across_files(tmp_path):
    dup = _clickid_row("mdup", "G", "8/13/2026 1:00:00 PM")
    _write(tmp_path, "oci-clickid_2026-08-13.csv", [dup])
    _write(tmp_path, "oci-clickid_2026-08-12.csv", [dup])
    n = assemble_cumulative(tmp_path / "oci-clickid.csv", tmp_path, "oci-clickid", NOW)
    assert n == 1
    assert (tmp_path / "oci-clickid.csv").read_text().count("mdup") == 1


def test_single_header_and_params(tmp_path):
    _write(tmp_path, "oci-clickid_2026-08-13.csv", [_clickid_row("m1", "G", "8/13/2026 1:00:00 PM")])
    assemble_cumulative(tmp_path / "oci-clickid.csv", tmp_path, "oci-clickid", NOW)
    lines = (tmp_path / "oci-clickid.csv").read_text().splitlines()
    assert lines[0] == PARAMS and lines[1] == HEADER
    assert lines.count(PARAMS) == 1 and lines.count(HEADER) == 1


def test_tier_isolation_and_ignores_undated_latest(tmp_path):
    _write(tmp_path, "oci-clickid_2026-08-13.csv", [_clickid_row("mCLICK", "G", "8/13/2026 1:00:00 PM")])
    _write(tmp_path, "oci-pii_2026-08-13.csv", [_clickid_row("mPII", "G", "8/13/2026 1:00:00 PM")])
    _write(tmp_path, "oci-clickid.csv", [_clickid_row("mLATEST", "G", "8/13/2026 1:00:00 PM")])  # undated
    assemble_cumulative(tmp_path / "out.csv", tmp_path, "oci-clickid", NOW)
    text = (tmp_path / "out.csv").read_text()
    assert "mCLICK" in text
    assert "mPII" not in text and "mLATEST" not in text


def test_empty_window_writes_header_only(tmp_path):
    _write(tmp_path, "oci-clickid_2026-07-01.csv", [_clickid_row("mOLD", "G", "7/1/2026 1:00:00 PM")])  # >14d
    n = assemble_cumulative(tmp_path / "oci-clickid.csv", tmp_path, "oci-clickid", NOW)
    assert n == 0
    assert (tmp_path / "oci-clickid.csv").read_text() == PARAMS + "\n" + HEADER + "\n"


def test_same_day_build_preserves_earlier_conversions_in_both_tiers(tmp_path):
    import copy
    import json
    from pathlib import Path
    from st_msads_oci.build import build
    from st_msads_oci.normalize import hash_email
    from tests.conftest import make_settings
    settings = make_settings(initial_watermark='2026-06-01T00:00:00+00:00')
    now = datetime(2026, 7, 6, 12, tzinfo=timezone.utc)
    payload = json.loads((Path(__file__).parents[1] / 'examples/sample-input.json').read_text())
    mapping = {'ids': {'e:' + hash_email(payload['jobs'][0]['email']):
                       [{'m': 'original-click', 'ts': '2026-07-01T00:00:00Z'}]}, 'dni': {}, 'forms': []}
    mapfile, infile = tmp_path / 'map.json', tmp_path / 'input.json'
    mapfile.write_text(json.dumps(mapping)); infile.write_text(json.dumps(payload))
    env = {'OCI_MAP_FILE': str(mapfile), 'OCI_DOWNLOADS_DIR': str(tmp_path / 'none')}
    build(tmp_path, infile, settings, env, now, notify=False)
    out = tmp_path / 'output'
    original_a = (out / 'oci-clickid.csv').read_text().splitlines()[2:]
    original_b = (out / 'oci-pii.csv').read_text().splitlines()[2:]
    assert len(original_a) == len(original_b) == 1
    new = copy.deepcopy(payload['jobs'][0])
    new.update(id=987654, completedOn='2026-07-06T11:00:00Z', invoiceTotal=888.88,
               email='new@example.com', phones=[])
    payload['jobs'].append(new); infile.write_text(json.dumps(payload))
    second = build(tmp_path, infile, settings, env, now, notify=False)
    assert second['tier_a'] == 0 and second['tier_b'] == 1
    assert (out / 'oci-clickid.csv').read_text().splitlines()[2:] == original_a
    later_b = (out / 'oci-pii.csv').read_text().splitlines()[2:]
    assert len(later_b) == 2 and set(original_b).issubset(later_b)
    # A third run without new rows must neither drop nor duplicate anything.
    build(tmp_path, infile, settings, env, now, notify=False)
    assert (out / 'oci-pii.csv').read_text().splitlines()[2:] == later_b
