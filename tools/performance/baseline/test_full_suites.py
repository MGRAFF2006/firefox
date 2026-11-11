# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Deterministic raw-schema fixtures; no browser performance measurements."""

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import full_suites
import run as runner


def declaration():
    return json.loads((Path(__file__).parent / "speedometer3-workload.json").read_text())


def synthetic_matrix():
    expected = declaration()
    iteration = {
        "geomean": 1, "mean": 1, "score": 1, "total": 1,
        "tests": {
            suite: {"total": 1, "tests": {
                step: {"total": 1, "tests": {"Sync": 1, "Async": 0}}
                for step in steps
            }} for suite, steps in expected["steps"].items()
        },
    }
    url = "http://127.0.0.1:12345/Speedometer3/index.html?raptor"
    return [{"info": {"url": url}, "browserScripts": [{"pageinfo": {"url": url}}],
             "markedAsFailure": 0, "failureMessages": [], "errors": [[]],
             "extras": [{"s3": {"score": {"isValid": True, "mean": 1}},
                         "s3_internal": [copy.deepcopy(iteration) for _ in range(10)]}]}]


class FullSuiteTests(unittest.TestCase):
    def check(self, raw, **overrides):
        expected = declaration()
        arguments = dict(expected_steps=expected["steps"], expected_iterations=10)
        arguments.update(overrides)
        return full_suites.validate_suites(raw, **arguments)

    def test_complete_matrix_and_retained_hash(self):
        raw = json.dumps(synthetic_matrix())
        self.assertEqual(self.check(raw), {
            "internal_iterations": 10, "suites_per_iteration": 20,
            "suite_records": 200, "step_records": 580,
        })
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "artifacts/mozharness/browsertime-results/speedometer3/browsertime.json"
            path.parent.mkdir(parents=True)
            path.write_text(raw)
            result = full_suites.check_run(root, declaration())
            self.assertEqual(result["raw_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_incomplete_and_invalid_raw_rejected_despite_valid_aggregate(self):
        for case in ("missing-matrix", "statistics-only", "iteration", "suite", "extra-suite",
                     "step", "component", "failure", "boolean-failure", "errors",
                     "extra-cycle", "extra-page", "aggregate", "negative", "boolean",
                     "nan", "infinity", "overflow", "huge-integer"):
            with self.subTest(case=case):
                raw = synthetic_matrix()
                extra = raw[0]["extras"][0]
                iterations = extra["s3_internal"]
                suites = iterations[0]["tests"]
                steps = suites["Charts-chartjs"]["tests"]
                step = steps["Draw scatter"]
                if case == "missing-matrix": del extra["s3_internal"]
                elif case == "statistics-only": extra["s3_internal"] = {"statistics": iterations[0]}
                elif case == "iteration": iterations.pop()
                elif case == "suite": del suites["Charts-chartjs"]
                elif case == "extra-suite": suites["undeclared"] = suites["Charts-chartjs"]
                elif case == "step": del steps["Draw scatter"]
                elif case == "component": del step["tests"]["Async"]
                elif case == "failure": raw[0]["markedAsFailure"] = 1
                elif case == "boolean-failure": raw[0]["markedAsFailure"] = False
                elif case == "errors": raw[0]["errors"] = [["failed"]]
                elif case == "extra-cycle": raw.append(copy.deepcopy(raw[0]))
                elif case == "extra-page": raw[0]["extras"].append(copy.deepcopy(extra))
                elif case == "aggregate": extra["s3"] = []
                else:
                    step["tests"]["Sync"] = {
                        "negative": -1, "boolean": True, "nan": float("nan"),
                        "infinity": float("inf"), "overflow": float("inf"),
                        "huge-integer": 10 ** 400,
                    }[case]
                encoded = json.dumps(raw)
                if case == "overflow": encoded = encoded.replace("Infinity", "1e999")
                with self.assertRaises(ValueError): self.check(encoded)

    def test_duplicate_keys_and_wrong_expectations_rejected(self):
        raw = json.dumps(synthetic_matrix())
        for original, duplicate in [('"markedAsFailure": 0', '"markedAsFailure": 1, "markedAsFailure": 0'),
                                    ('"Sync": 1', '"Sync": 2, "Sync": 1')]:
            with self.subTest(original=original), self.assertRaisesRegex(ValueError, "duplicate"):
                self.check(raw.replace(original, duplicate, 1))
        wrong = declaration()["steps"]
        wrong["Charts-chartjs"][0] = "undeclared"
        for overrides in ({"expected_iterations": 9}, {"expected_iterations": True},
                          {"expected_steps": wrong}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.check(raw, **overrides)

    def test_observed_url_rejects_overrides_and_wrong_loaded_document(self):
        raw = synthetic_matrix()
        url = raw[0]["info"]["url"]
        self.assertEqual(full_suites.workload_url(raw), url)
        for wrong in (url + "&iterationCount=1", url + "&suite=TodoMVC-jQuery",
                      url + "&tags=todo", url.replace("127.0.0.1", "example.org"),
                      url.replace("?raptor", "?raptor&raptor"), url + "#different",
                      url.replace("?raptor", "")):
            with self.subTest(wrong=wrong):
                raw[0]["info"]["url"] = wrong
                with self.assertRaises(ValueError): full_suites.workload_url(raw)
        raw[0]["info"]["url"] = url
        raw[0]["browserScripts"][0]["pageinfo"]["url"] = "about:blank"
        with self.assertRaisesRegex(ValueError, "loaded page"):
            full_suites.workload_url(raw)

    def test_declaration_and_source_hashes_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)
            with patch.object(runner, "HERE", source):
                (source / "speedometer3-workload.json").write_text('{}')
                with self.assertRaisesRegex(ValueError, "reviewed bytes"):
                    runner.workload_declaration(source)
            # Use the real declaration, but synthetic source bytes. Only this
            # test patches the expected source hashes, never the matrix checker.
            expected = declaration()
            expected["source_files"] = {"definition": hashlib.sha256(b"fixture").hexdigest()}
            encoded = json.dumps(expected).encode()
            (source / "speedometer3-workload.json").write_bytes(encoded)
            (source / "definition").write_bytes(b"fixture")
            with patch.object(runner, "HERE", source), patch.object(runner, "WORKLOAD_SHA256", hashlib.sha256(encoded).hexdigest()):
                self.assertEqual(runner.workload_declaration(source), expected)
                (source / "definition").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "source differs"):
                    runner.workload_declaration(source)


if __name__ == "__main__":
    unittest.main()
