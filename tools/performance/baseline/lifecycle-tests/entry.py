#!/usr/bin/env python3
"""Execute real copied main with only external workload/source fixtures."""
import importlib.util
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve().parent))

spec = importlib.util.spec_from_file_location('scoring_under_test', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
case = Path(sys.argv[2])
if hasattr(module, "workload_declaration"):
    import json
    # Synthetic external harness identity only; real matrix validation remains.
    declaration = json.loads((Path(sys.argv[1]).resolve().parent / "speedometer3-workload.json").read_text())
    module.workload_declaration = lambda source: declaration
# The explicit fixture source never supplies the executed tooling/custodian.
# Caller argument parsing, lock/custodian and workload/result handling stay real.
sys.argv = ['run.py', '--source',str(case/'source'), '--root', str(case/'root'), '--official', str(case/'binary/firefox'),
            '--source-build', str(case/'binary/firefox'), '--chromium', str(case/'binary/firefox'),
            '--chromedriver', str(case/'binary/firefox'), '--only-browser', 'source-firefox', '--rounds','1']
raise SystemExit(module.main())
