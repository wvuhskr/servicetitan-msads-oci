#!/usr/bin/env python3
"""Standalone launcher: python pull/pull_servicetitan.py --config accounts.yaml
Equivalent to `st-msads-oci pull`. Requires `pip install -e .` from the repo root."""
import sys
from st_msads_oci.cli import main

if __name__ == "__main__":
    sys.exit(main(["pull", *sys.argv[1:]]))
