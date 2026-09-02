import pytest
from datetime import timedelta, datetime, timezone
from st_msads_oci.assemble import assemble_csv, validate, format_time, InvariantError
from st_msads_oci.rows import UploadRow, GOAL_BOOKED_JOB, GOAL_COMPLETED_JOBS, parse_utc


def row(goal, ts, value=None):
    return UploadRow(goal, 1, parse_utc(ts), 1, "Example Brand - Search - MS", "Paid Microsoft", "X",
                     "x@x.com", [], email_hash="a" * 64, phone_hash="b" * 64, value=value)


def test_format_time_no_leading_zeros():
    assert format_time(parse_utc("2026-06-24T02:20:16Z")) == "6/24/2026 2:20:16 AM"
    assert format_time(parse_utc("2026-06-22T14:40:19Z")) == "6/22/2026 2:40:19 PM"
    assert format_time(parse_utc("2026-07-06T00:05:07Z")) == "7/6/2026 12:05:07 AM"


def test_golden_csv(tmp_path):
    rows = [row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z"),
            row(GOAL_COMPLETED_JOBS, "2026-07-02T15:30:00Z", value=1234.5)]
    out = tmp_path / "u.csv"
    assemble_csv(rows, out)
    assert out.read_text() == (
        "Parameters:TimeZone=+0000\n"
        "Microsoft Click Id,Conversion Name,Conversion Time,Conversion Value,Conversion Currency,"
        "Hashed Email Address,Hashed Phone Number\n"
        f",ServiceTitan Booked Job (Website) - MS,7/1/2026 2:00:00 PM,,,{'a'*64},{'b'*64}\n"
        f",ServiceTitan Completed Jobs - MS,7/2/2026 3:30:00 PM,1234.50,USD,{'a'*64},{'b'*64}\n"
    )


def test_click_id_written_when_present(tmp_path):
    r = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    r.msclkid = "abc123xyz"
    p = tmp_path / "out.csv"
    assemble_csv([r], p)
    line = p.read_text().splitlines()[2]
    assert line.startswith("abc123xyz,")


def test_validate_accepts_clickid_only_row():
    r = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    r.msclkid, r.email_hash, r.phone_hash = "abc123xyz", None, None
    validate([r])


def test_validate_rejects_row_with_no_identifier_at_all():
    r = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    r.msclkid = r.email_hash = r.phone_hash = None
    with pytest.raises(InvariantError):
        validate([r])


def test_invariants():
    good = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    validate([good])
    bad_hash = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    bad_hash.email_hash, bad_hash.phone_hash = "XYZ", None
    with pytest.raises(InvariantError):
        validate([bad_hash])
    future = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z")
    future.ts = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(InvariantError):
        validate([future])
    no_value = row(GOAL_COMPLETED_JOBS, "2026-07-01T14:00:00Z")  # value None
    with pytest.raises(InvariantError):
        validate([no_value])
    stray_value = row(GOAL_BOOKED_JOB, "2026-07-01T14:00:00Z", value=5.0)
    with pytest.raises(InvariantError):
        validate([stray_value])
