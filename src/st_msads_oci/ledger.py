import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_INITIAL_WATERMARK
from .rows import ALL_GOALS, parse_utc


def parse_result_rows(path, settings):
    """Parse a Microsoft OCI result file (upload template + Status column)."""
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for rec in csv.reader(f):
            if len(rec) < 7 or rec[1] not in settings.known_result_names:
                continue  # instruction/parameter/header rows
            # Try US format first, fall back to ISO 8601
            try:
                ts = datetime.strptime(rec[2], "%m/%d/%Y %I:%M:%S %p").replace(tzinfo=timezone.utc)
            except ValueError:
                ts = datetime.fromisoformat(rec[2].replace("Z", "+00:00")).astimezone(timezone.utc)
            rows.append({
                "goal": settings.goal_key(rec[1]) or rec[1], "ts": ts.isoformat(),
                "email_hash": rec[5] or None, "phone_hash": rec[6] or None,
                "status": rec[7].strip() if len(rec) > 7 and rec[7] else None,
            })
    return rows


def seed_from_result_csv(path, settings):
    entries = []
    for r in parse_result_rows(path, settings):
        entries.append({"goal": r["goal"], "st_id": None, "ts": r["ts"],
                        "email_hash": r["email_hash"], "phone_hash": r["phone_hash"],
                        "campaign_name": None, "uploaded_on": "seed-june-2026",
                        "status": r["status"]})
    return entries


class Ledger:
    def __init__(self, data=None, initial_watermark=DEFAULT_INITIAL_WATERMARK):
        data = data or {}
        wm = data.get("watermarks", {})
        self.watermarks = {g: parse_utc(wm.get(g, initial_watermark)) for g in ALL_GOALS}
        self.uploaded = data.get("uploaded", [])
        self.pending_projects = data.get("pending_projects", [])

    @classmethod
    def load(cls, path, initial_watermark=DEFAULT_INITIAL_WATERMARK):
        p = Path(path)
        return (cls(json.loads(p.read_text()), initial_watermark) if p.exists()
                else cls(initial_watermark=initial_watermark))

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps({
            "watermarks": {g: self.watermarks[g].isoformat() for g in ALL_GOALS},
            "uploaded": self.uploaded,
            "pending_projects": self.pending_projects,
        }, indent=1))

    def has_st_id(self, goal, st_id):
        return st_id is not None and any(
            e["goal"] == goal and e.get("st_id") == st_id for e in self.uploaded)

    def identity_recent(self, goal, email_hash, phone_hash, ts, hours=24):
        for e in self.uploaded:
            if e["goal"] != goal:
                continue
            same = ((email_hash and e.get("email_hash") == email_hash)
                    or (phone_hash and e.get("phone_hash") == phone_hash))
            if same and abs((parse_utc(e["ts"]) - ts).total_seconds()) <= hours * 3600:
                return True
        return False

    def add_row(self, row, uploaded_on, tier=None):
        self.uploaded.append({"goal": row.goal, "st_id": row.st_id,
                              "ts": row.ts.replace(microsecond=0).isoformat(),
                              "email_hash": row.email_hash, "phone_hash": row.phone_hash,
                              "campaign_name": row.campaign_name, "uploaded_on": uploaded_on,
                              "status": None, "tier": tier,
                              "msclkid": bool(getattr(row, "msclkid", None)),
                              "value": row.value,
                              "project_id": getattr(row, "project_id", None),
                              "member_job_ids": getattr(row, "member_job_ids", None),
                              "recovered_via": getattr(row, "recovered_via", None)})

    def has_project_overlap(self, goal, project_id, member_ids):
        """A project is blocked if its id OR any member job already appears in the ledger —
        covers history where an install leg uploaded as a per-job row (spec edge 7)."""
        ids = set(member_ids)
        for e in self.uploaded:
            if e["goal"] != goal:
                continue
            if project_id is not None and e.get("project_id") == project_id:
                return True
            if e.get("st_id") in ids or ids & set(e.get("member_job_ids") or []):
                return True
        return False

    def advance_watermarks(self, rows):
        for r in rows:
            if r.ts > self.watermarks.get(r.goal, r.ts):
                self.watermarks[r.goal] = r.ts
