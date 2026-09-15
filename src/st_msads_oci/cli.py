import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .build import build
from .config import load_env, load_settings
from .notify import failure_summary, notify_all, safe_build_notifiers
from .rows import parse_utc


def _common(p):
    p.add_argument("--config", default="accounts.yaml")
    p.add_argument("--project-dir", default=".")
    p.add_argument("--no-notify", action="store_true")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="st-msads-oci")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); _common(b)
    b.add_argument("--input", required=True); b.add_argument("--now")
    a = sub.add_parser("alert"); _common(a); a.add_argument("message")
    v = sub.add_parser("validate-input"); v.add_argument("path")
    pl = sub.add_parser("pull"); _common(pl); pl.add_argument("--out", default="output/input-latest.json")
    args = ap.parse_args(argv)

    if args.cmd == "validate-input":
        from .schema import validate_input
        errs = validate_input(json.loads(Path(args.path).read_text()))
        print("\n".join(errs) if errs else "ok")
        return 1 if errs else 0

    project_dir = Path(args.project_dir)
    settings = load_settings(args.config)
    env = load_env(project_dir / ".env")
    now = datetime.now(timezone.utc)

    if args.cmd == "alert":
        errs = []
        if not args.no_notify:
            # A misconfigured notifier is reported as an error (exit 2), never a crash.
            notifiers, errs = safe_build_notifiers(settings, env)
            errs += notify_all(notifiers, f"MS Ads offline conversions FAILED — {now:%Y-%m-%d}", args.message)
        print(json.dumps({"alert": args.message, "notify_errors": errs}))
        return 2 if errs else 0

    if args.cmd == "pull":
        from .pull.servicetitan import run_pull
        return run_pull(settings, env, project_dir, Path(args.out), now, notify=not args.no_notify)

    now = parse_utc(args.now) if args.now else now
    try:
        print(json.dumps(build(project_dir, Path(args.input), settings, env, now, notify=not args.no_notify)))
        return 0
    except Exception as exc:
        tb = failure_summary(exc)
        if not args.no_notify:
            # Reporting a failed build must not itself crash on a misconfigured notifier.
            notifiers, cfg_errs = safe_build_notifiers(settings, env)
            for e in cfg_errs:
                print(e, file=sys.stderr)
            notify_all(notifiers, f"MS Ads offline conversions FAILED — {now:%Y-%m-%d}", tb)
        print(tb, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
