from .normalize import normalize_email, normalize_phone, hash_email, hash_phone
from .rows import Dropped, GOAL_COMPLETED_JOBS


def _is_test_identity(row, test_emails, test_phones):
    ne = normalize_email(row.raw_email)
    if ne and ne in {normalize_email(e) for e in test_emails}:
        return True
    phones = {normalize_phone(p) for p in row.raw_phones} - {None}
    return bool(phones & ({normalize_phone(p) for p in test_phones} - {None}))


def filter_rows(rows, settings):
    kept, dropped = [], []
    for r in rows:
        if r.campaign_category != settings.campaign_category:
            dropped.append(Dropped(r.goal, r.st_id, f"campaign category {r.campaign_category}: {r.campaign_name}"))
        elif _is_test_identity(r, settings.test_emails, settings.test_phones):
            dropped.append(Dropped(r.goal, r.st_id, "test identity"))
        elif r.goal == GOAL_COMPLETED_JOBS and not (r.value and r.value > 0):
            dropped.append(Dropped(r.goal, r.st_id, "no positive invoice value"))
        elif not r.raw_email and not r.raw_phones and not r.lead_call_id:
            dropped.append(Dropped(r.goal, r.st_id, "no contact info"))
        else:
            kept.append(r)
    return kept, dropped


def attach_hashes(rows):
    hashed, failed = [], []
    for r in rows:
        r.email_hash = hash_email(r.raw_email)
        r.phone_hash = next((h for h in (hash_phone(p) for p in r.raw_phones) if h), None)
        if r.email_hash or r.phone_hash or r.lead_call_id:
            hashed.append(r)
        else:
            failed.append(Dropped(r.goal, r.st_id, "no hashable contact"))
    return hashed, failed
