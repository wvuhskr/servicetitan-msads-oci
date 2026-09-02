import csv
import shutil
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .ledger import parse_result_rows
from .rows import parse_utc

NULL_ID_STATUSES = {"Unattributed Reason:ClickId and stableId are null",
                    "Unattributed Reason: ClickId and stableId are null"}
HISTORY_HEADER = "parsed_on,result_file,goal,campaign_type,uploaded,success,rate"


def campaign_type(name):
    if not name:
        return "unknown"
    return "PMax" if "Performance Max" in name else "Search"


def find_result_files(inbox_dir, downloads_dir, processed_names):
    found = []
    for d in (Path(inbox_dir), Path(downloads_dir)):
        if not d.exists():
            continue
        for p in sorted(d.glob("OfflineConv*AllResultFile*")):
            if p.name in processed_names:
                continue
            found.append(p)
    return found


def _match_ledger_entry(ledger, r):
    r_ts = parse_utc(r["ts"]).replace(microsecond=0)
    for e in ledger.uploaded:
        if (e["goal"] == r["goal"]
                and parse_utc(e["ts"]).replace(microsecond=0) == r_ts
                and e.get("email_hash") == r["email_hash"]
                and e.get("phone_hash") == r["phone_hash"]):
            return e
    return None


def run_triage(ledger, inbox_dir, downloads_dir, archive_dir, history_path, parsed_on, settings):
    findings = []
    history_path = Path(history_path)
    processed = set()
    if history_path.exists():
        processed = {row.split(",")[1] for row in history_path.read_text().splitlines()[1:]}

    for path in find_result_files(inbox_dir, downloads_dir, processed):
        try:
            if path.suffix == ".zip":
                tmp = Path(tempfile.mkdtemp())
                with zipfile.ZipFile(path) as z:
                    z.extractall(tmp)
                rows = []
                for csv_path in sorted(tmp.glob("*.csv")):
                    rows.extend(parse_result_rows(csv_path, settings))
            else:
                rows = parse_result_rows(path, settings)
            stats = defaultdict(lambda: [0, 0])  # (goal, ctype) -> [uploaded, success]
            by_status = defaultdict(int)
            for r in rows:
                by_status[r["status"] or "no status"] += 1
            breakdown = ", ".join(f"{s} {n}" for s, n in
                                  sorted(by_status.items(), key=lambda kv: -kv[1]))
            findings.append(f"result {path.name}: {len(rows)} rows — {breakdown}")
            for r in rows:
                entry = _match_ledger_entry(ledger, r)
                if entry:
                    entry["status"] = r["status"]
                    ctype = campaign_type(entry.get("campaign_name"))
                else:
                    ctype = "unknown"
                stats[(r["goal"], ctype)][0] += 1
                if r["status"] == "Success":
                    stats[(r["goal"], ctype)][1] += 1
                if r["status"] in NULL_ID_STATUSES:
                    findings.append(f"HASH ALARM: identity never resolved for {r['goal']} row {r['ts']} — re-verify normalization")
            if not history_path.exists():
                history_path.write_text(HISTORY_HEADER + "\n")
            with open(history_path, "a", newline="") as f:
                w = csv.writer(f)
                for (goal, ctype), (up, ok) in sorted(stats.items()):
                    w.writerow([parsed_on, path.name, goal, ctype, up, ok, f"{ok/up:.2f}" if up else "0.00"])
            dest = Path(archive_dir) / path.name
            Path(archive_dir).mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), dest)
        except Exception:
            findings.append(f"malformed result file: {path.name} — left in place, fix or remove it")

    findings.extend(_pmax_flag(history_path))
    findings.extend(_pending_flag(ledger))
    return findings


def _pmax_flag(history_path):
    if not Path(history_path).exists():
        return []
    lines = Path(history_path).read_text().splitlines()[1:]
    files = []
    for ln in lines:
        name = ln.split(",")[1]
        if name not in files:
            files.append(name)
    recent = set(files[-3:])
    up = ok = 0
    for ln in lines:
        parts = ln.split(",")
        if parts[1] in recent and parts[3] == "PMax":
            up += int(parts[4]); ok += int(parts[5])
    if len(recent) == 3 and up >= 5 and ok == 0:
        return ["PMax match rate 0% across last 3 uploads — consider excluding PMax-attributed bookings"]
    return []


def _pending_flag(ledger):
    now = datetime.now(timezone.utc)
    stale = 0
    for e in ledger.uploaded:
        if e.get("status") is not None or e.get("uploaded_on") == "seed-june-2026":
            continue
        try:
            when = parse_utc(e["uploaded_on"] + "T00:00:00Z")
        except ValueError:
            continue
        if now - when > timedelta(days=7):
            stale += 1
    return [f"{stale} uploaded rows have no result file yet — upload pending or result file not dropped"] if stale else []
