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
