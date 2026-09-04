import re
import os
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .rows import ALL_GOALS, GOAL_COMPLETED_JOBS

PARAMS = "Parameters:TimeZone=+0000"
HEADER = ("Microsoft Click Id,Conversion Name,Conversion Time,Conversion Value,"
          "Conversion Currency,Hashed Email Address,Hashed Phone Number")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class InvariantError(Exception):
    pass


def format_time(dt) -> str:
    h = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    return f"{dt.month}/{dt.day}/{dt.year} {h}:{dt.minute:02d}:{dt.second:02d} {ampm}"


def validate(rows, now=None):
    now = now or datetime.now(timezone.utc)
    for r in rows:
        if r.goal not in ALL_GOALS:
            raise InvariantError(f"unknown goal {r.goal!r} (st id {r.st_id})")
        if r.ts > now:
            raise InvariantError(f"future timestamp {r.ts} (st id {r.st_id})")
        for h in (r.email_hash, r.phone_hash):
            if h is not None and not _HEX64.match(h):
                raise InvariantError(f"bad hash {h!r} (st id {r.st_id})")
        if not getattr(r, "msclkid", None) and not r.email_hash and not r.phone_hash:
            raise InvariantError(f"no identifier (st id {r.st_id})")
        if r.goal == GOAL_COMPLETED_JOBS and not (r.value and r.value > 0):
            raise InvariantError(f"completed job without value (st id {r.st_id})")
        if r.goal != GOAL_COMPLETED_JOBS and r.value is not None:
            raise InvariantError(f"value on non-job goal (st id {r.st_id})")


def _write_atomic(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A unique sibling avoids clobbering unrelated files with the same stem.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def assemble_csv(rows, path, goal_names, *, merge=False) -> int:
    lines = [PARAMS, HEADER]
    for r in sorted(rows, key=lambda x: x.ts):
        is_job = r.goal == GOAL_COMPLETED_JOBS
        lines.append(",".join([
            getattr(r, "msclkid", None) or "", goal_names[r.goal], format_time(r.ts),
            f"{r.value:.2f}" if is_job else "",
            "USD" if is_job else "",
            r.email_hash or "", r.phone_hash or "",
        ]))
    if merge and Path(path).exists():
        previous = Path(path).read_text().splitlines()
        if previous[:2] != [PARAMS, HEADER]:
            raise InvariantError("existing dated CSV has an invalid header")
        lines = [PARAMS, HEADER, *dict.fromkeys([*previous[2:], *lines[2:]])]
    _write_atomic(path, "\n".join(lines) + "\n")
    return len(lines)


CUMULATIVE_DAYS = 14  # rolling window served to Microsoft (spec 2026-08-18)

_DATED = re.compile(r"^(?P<prefix>.+)_(?P<date>\d{4}-\d{2}-\d{2})\.csv$")


def _dated_files_in_window(dated_dir, prefix, now, days):
    """Dated `{prefix}_YYYY-MM-DD.csv` files whose date is within `days` of now.date(), oldest first.
    The undated served file `{prefix}.csv` has no date suffix and is excluded."""
    cutoff = now.date() - timedelta(days=days)
    found = []
    for p in Path(dated_dir).glob(f"{prefix}_*.csv"):
        m = _DATED.match(p.name)
        if not m or m.group("prefix") != prefix:
            continue
        try:
            d = datetime.strptime(m.group("date"), "%Y-%m-%d").date()
        except ValueError:
            continue
        if cutoff <= d <= now.date():
            found.append((d, p))
    return [p for _, p in sorted(found)]


def assemble_cumulative(out_path, dated_dir, prefix, now, days=CUMULATIVE_DAYS) -> int:
    """Rebuild the served `{prefix}.csv` as the deduped union of the body rows of every dated
    `{prefix}_YYYY-MM-DD.csv` within the last `days`. Text-level concat — dated files are already
    in the exact target format (written by assemble_csv), so rows need no re-serialization and MS
    ignores row order. Returns the data-row count."""
    rows = []
    for p in _dated_files_in_window(dated_dir, prefix, now, days):
        for ln in p.read_text().splitlines()[2:]:  # drop PARAMS + HEADER
            if ln.strip():
                rows.append(ln)
    rows = list(dict.fromkeys(rows))  # dedup, preserve first-seen order
    _write_atomic(out_path, "\n".join([PARAMS, HEADER, *rows]) + "\n")
    return len(rows)
