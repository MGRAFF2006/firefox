#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Exercise the real summary command's publication and contamination guards."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.write("metadata.json", {"profile_only": False, "rounds": 5})
        (self.root / "harness").mkdir()
        for number in range(1, 6):
            for browser, score in (
                ("chromium", 10),
                ("official-firefox", 20),
                ("source-firefox", 5),
            ):
                run = f"{number:02d}-{browser}"
                self.write(
                    f"{run}/exit.json",
                    {
                        "exit_code": 0,
                        "swap_io_delta_pages": {"pswpin": 0, "pswpout": 0},
                        "binary_changed": False,
                    },
                )
                self.write(
                    f"{run}/score.json",
                    {"round": number, "browser": browser, "score": score},
                )

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def run_summary(self):
        return subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("summarize.py")),
                str(self.root),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_snapshot_directory_is_not_a_browser_run(self):
        result = self.run_summary()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads((self.root / "summary.json").read_text())
        self.assertEqual(summary["official_vs_chromium"]["geometric_mean_ratio"], 2)
        self.assertEqual(summary["source_vs_chromium"]["geometric_mean_ratio"], 0.5)

    def test_incomplete_pairs_are_rejected(self):
        self.write(
            "05-chromium/score.json", {"round": 4, "browser": "chromium", "score": 10}
        )
        result = self.run_summary()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Duplicate round", result.stderr)

    def test_profiles_and_small_cohorts_are_rejected(self):
        for metadata, reason in (
            ({"profile_only": True, "rounds": 5}, "Profiles cannot"),
            ({"profile_only": False, "rounds": 4}, "At least five"),
        ):
            with self.subTest(metadata=metadata):
                self.write("metadata.json", metadata)
                result = self.run_summary()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)

    def test_failed_contaminated_or_changed_runs_are_rejected(self):
        for field, value, reason in (
            ("exit_code", 1, "Failed run"),
            ("swap_io_delta_pages", {"pswpin": 1, "pswpout": 0}, "Swap I/O"),
            ("binary_changed", True, "Browser changed"),
        ):
            with self.subTest(field=field):
                status = {
                    "exit_code": 0,
                    "swap_io_delta_pages": {"pswpin": 0, "pswpout": 0},
                    "binary_changed": False,
                }
                status[field] = value
                self.write("01-chromium/exit.json", status)
                result = self.run_summary()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)


if __name__ == "__main__":
    unittest.main()
