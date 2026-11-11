# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Pure retained Speedometer3 matrix check; no execution or provenance attestation.

The caller must authenticate the declaration and observed invocation/workload
binding separately. expected_iterations MUST be configured, never inferred from
this result. This function does not authorize scoring or change score math.
"""
import hashlib
import json
import math
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _number(value, *, positive=False):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, "invalid numeric timing/score")
    _require(value > 0 if positive else value >= 0, "invalid timing/score sign")


def validate_suites(raw_json, *, expected_steps, expected_iterations):
    """Validate raw coverage against caller-authenticated workload expectations.

    This does not authenticate HTTP-loaded bytes, invocation URL, or runtime.
    """
    _require(type(expected_iterations) is int and expected_iterations > 0, "invalid configured iteration count")
    _require(type(expected_steps) is dict and len(expected_steps) == 20, "expected exactly 20 suite IDs")
    for suite, steps in expected_steps.items():
        _require(type(suite) is str and bool(suite) and type(steps) in (list, tuple) and bool(steps), "invalid expected suite/steps")
        _require(all(type(x) is str and x for x in steps) and len(set(steps)) == len(steps), "duplicate/invalid expected steps")
    _require(sum(map(len, expected_steps.values())) == 58, "expected exactly 58 steps")
    raw = json.loads(raw_json, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    _require(type(raw) is list and len(raw) == 1 and type(raw[0]) is dict, "expected one browser-cycle result")
    result = raw[0]
    _require(type(result.get("markedAsFailure")) is int and result["markedAsFailure"] == 0, "Browsertime failure marker")
    _require(result.get("failureMessages") == [] and result.get("errors") == [[]], "Browsertime errors or unexpected cycle shape")
    extras = result.get("extras")
    _require(type(extras) is list and len(extras) == 1 and type(extras[0]) is dict, "expected one page-cycle extra")
    extra = extras[0]
    s3 = extra.get("s3")
    _require(type(s3) is dict, "missing/malformed s3 aggregate")
    aggregate = s3.get("score", {})
    _require(type(aggregate) is dict and aggregate.get("isValid") is True, "invalid aggregate")
    _number(aggregate.get("mean"), positive=True)
    iterations = extra.get("s3_internal")
    _require(type(iterations) is list and len(iterations) == expected_iterations, "internal iteration mismatch")
    for iteration in iterations:
        _require(type(iteration) is dict and set(iteration) == {"geomean", "mean", "score", "tests", "total"}, "partial iteration")
        for key in ("geomean", "mean", "score", "total"):
            _number(iteration[key], positive=True)
        suites = iteration["tests"]
        _require(type(suites) is dict and set(suites) == set(expected_steps), "missing/unexpected suite")
        for name, steps in expected_steps.items():
            suite = suites[name]
            _require(type(suite) is dict and set(suite) == {"tests", "total"}, "partial suite")
            _number(suite["total"], positive=True)
            actual_steps = suite["tests"]
            _require(type(actual_steps) is dict and set(actual_steps) == set(steps), "missing/unexpected suite step")
            for step in actual_steps.values():
                _require(type(step) is dict and set(step) == {"tests", "total"}, "partial step")
                _number(step["total"])
                _require(type(step["tests"]) is dict and set(step["tests"]) == {"Sync", "Async"}, "partial Sync/Async measurement")
                for value in step["tests"].values():
                    _number(value)
    return {"internal_iterations": expected_iterations, "suites_per_iteration": 20,
            "suite_records": 20 * expected_iterations,
            "step_records": sum(len(x) for x in expected_steps.values()) * expected_iterations}


def workload_url(raw):
    info = raw[0].get("info", {})
    url = info.get("url")
    _require(type(url) is str, "missing observed workload URL")
    parsed = urlsplit(url)
    _require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
             and parsed.username is None and parsed.password is None
             and parsed.port is not None
             and parsed.path == "/Speedometer3/index.html"
             and parse_qsl(parsed.query, keep_blank_values=True) == [("raptor", "")]
             and not parsed.fragment, "unexpected workload URL/selection")
    scripts = raw[0].get("browserScripts")
    _require(type(scripts) is list and len(scripts) == 1
             and type(scripts[0]) is dict
             and type(scripts[0].get("pageinfo")) is dict
             and scripts[0]["pageinfo"].get("url") == url,
             "loaded page URL disagrees with requested workload")
    return url


def check_run(run, declaration):
    """Verify retained measurements, never substitute aggregate statistics."""
    path = Path(run) / "artifacts/mozharness/browsertime-results/speedometer3/browsertime.json"
    raw = path.read_bytes()
    counts = validate_suites(raw, expected_steps=declaration["steps"],
                             expected_iterations=declaration["iterations"])
    observed_url = workload_url(json.loads(raw, object_pairs_hook=_pairs, parse_constant=_reject_constant))
    return {"raw_result": str(path.relative_to(run)), "observed_url": observed_url,
            "raw_sha256": hashlib.sha256(raw).hexdigest(), **counts,
            "limits": "Raw matrix coverage only; source hashes do not attest HTTP-loaded bytes, browser security or runtime build provenance."}
