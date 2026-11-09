#!/usr/bin/env python3
"""Execute real copied main with only external workload/source fixtures."""
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location('scoring_under_test', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
case = Path(sys.argv[2])
# This substitutes the source/dependency fixture, never shared SOURCE. All caller
# argument parsing, lock/custodian code, workload loop and result handling are real.
module.SOURCE = case/'source'
sys.argv = ['run.py', '--root', str(case/'root'), '--official', str(case/'binary/firefox'),
            '--source-build', str(case/'binary/firefox'), '--chromium', str(case/'binary/firefox'),
            '--chromedriver', str(case/'binary/firefox'), '--only-browser', 'source-firefox', '--rounds','1']
raise SystemExit(module.main())
