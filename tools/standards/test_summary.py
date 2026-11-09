import json
from pathlib import Path
import tempfile
import unittest

from summarize import read_run


class ReportEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path / "invocation.json").write_text(json.dumps({
            "executed": True, "label": "control", "source_head": "test-fixture",
            "runner_exit": 1,
        }))

    def report(self, results):
        (self.path / "wptreport.json").write_text(json.dumps({
            "time_end": 100, "run_info": {}, "results": results,
        }))

    def test_expected_failure_remains_failure_and_unrun_remains_unrun(self):
        self.report([{"test": "/example.html", "status": "OK", "subtests": [
            {"name": "known gap", "status": "FAIL"},
            {"name": "new failure", "status": "TIMEOUT", "expected": "PASS"},
            {"name": "never executed", "status": "NOTRUN"},
        ]}])
        _, summary, records = read_run(self.path)
        self.assertEqual(summary["tests"], {"OK": 1})
        self.assertEqual(summary["subtests"], {"FAIL": 1, "TIMEOUT": 1, "NOTRUN": 1})
        self.assertEqual(len(summary["unexpected"]), 1)
        self.assertEqual(summary["unexpected"][0]["subtest"], "new failure")
        self.assertEqual(records[("", "/example.html", "known gap")], "FAIL")

    def test_missing_generated_variant_is_unrun_and_extras_are_visible(self):
        invocation = json.loads((self.path / "invocation.json").read_text())
        invocation["selection"] = ["example.any.js"]
        (self.path / "invocation.json").write_text(json.dumps(invocation))
        (self.path / "MANIFEST.json").write_text(json.dumps({"items": {"testharness": {
            "example.any.js": ["file-hash", ["example.any.html", {}], ["example.any.worker.html", {}]],
        }}}))
        self.report([{"test": "/example.any.html", "status": "OK", "subtests": []},
                     {"test": "/extra.html", "status": "PASS", "subtests": []}])
        _, summary, records = read_run(self.path)
        self.assertEqual(summary["missing_expected_ids"], ["/example.any.worker.html"])
        self.assertEqual(summary["extra_ids"], ["/extra.html"])
        self.assertEqual(summary["tests"], {"OK": 1, "PASS": 1})
        self.assertNotIn(("", "/example.any.worker.html", None), records)

    def test_legacy_missing_manifest_is_explicitly_unknown(self):
        self.report([{"test": "/example.html", "status": "OK", "subtests": []}])
        _, summary, _ = read_run(self.path)
        self.assertIn("unavailable", summary["completeness_evidence"])
        self.assertNotIn("missing_expected_ids", summary)

    def test_empty_report_cannot_be_success(self):
        self.report([])
        with self.assertRaisesRegex(ValueError, "empty or incomplete"):
            read_run(self.path)

    def test_duplicate_attempts_cannot_silently_replace_results(self):
        result = {"test": "/example.html", "status": "CRASH", "subtests": []}
        self.report([result, result])
        with self.assertRaisesRegex(ValueError, "repeated test ID"):
            read_run(self.path)

    def test_dry_plan_is_not_result(self):
        (self.path / "invocation.json").write_text('{"executed": false}')
        with self.assertRaisesRegex(ValueError, "not executed"):
            read_run(self.path)


if __name__ == "__main__":
    unittest.main()
