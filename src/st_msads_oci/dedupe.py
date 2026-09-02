from .rows import Dropped

SAME_GOAL_WINDOW_H = 24


def _same_identity(a, b):
    return ((a.email_hash and a.email_hash == b.email_hash)
            or (a.phone_hash and a.phone_hash == b.phone_hash))


def dedupe(rows, ledger):
    kept, dropped = [], []
    seen = set()  # Track (goal, st_id) pairs already accepted in this run
    for r in sorted(rows, key=lambda x: (x.ts, x.st_id)):
        if (r.goal, r.st_id) in seen:
            dropped.append(Dropped(r.goal, r.st_id, "duplicate within run (st id)"))
        elif ledger.has_st_id(r.goal, r.st_id):
            dropped.append(Dropped(r.goal, r.st_id, "already uploaded (st id)"))
        elif ledger.identity_recent(r.goal, r.email_hash, r.phone_hash, r.ts, hours=SAME_GOAL_WINDOW_H):
            dropped.append(Dropped(r.goal, r.st_id, "already uploaded (identity within 24h)"))
        elif any(k.goal == r.goal and _same_identity(r, k)
                 and abs((r.ts - k.ts).total_seconds()) <= SAME_GOAL_WINDOW_H * 3600
                 for k in kept):
            dropped.append(Dropped(r.goal, r.st_id, "duplicate within run (identity within 24h)"))
        else:
            kept.append(r)
            seen.add((r.goal, r.st_id))

    return kept, dropped
