#!/usr/bin/env python3
"""Summarize raw WPT statuses without converting expected failures into passes."""
import argparse
from collections import Counter
import json
from pathlib import Path

from run import digest


def completeness(directory, invocation, results):
    manifest_path = Path(invocation.get("manifest_path", directory / "MANIFEST.json"))
    if not manifest_path.is_file() or "selection" not in invocation:
        return {"completeness_evidence": "unavailable; emitted IDs alone do not establish selection coverage"}
    provenance = invocation.get("manifest_provenance")
    if provenance and digest(manifest_path) != provenance["sha256"]:
        raise ValueError(f"{directory}: manifest differs from recorded run provenance")
    manifest = json.loads(manifest_path.read_text())
    file_ids = {}
    for selected in invocation["selection"]:
        ids = []
        for tree in manifest["items"].values():
            node = tree
            for component in selected.split("/"):
                if not isinstance(node, dict) or component not in node:
                    node = None
                    break
                node = node[component]
            if isinstance(node, list):
                for variant in node[1:]:
                    ids.append("/" + (variant[0] or selected).lstrip("/"))
        file_ids[selected] = sorted(set(ids))
    expected = {test_id for ids in file_ids.values() for test_id in ids}
    emitted = {result["test"] for result in results}
    return {"completeness_evidence": "recorded manifest" if provenance else "legacy manifest; no contemporaneous hash recorded",
            "expected_ids_by_source_file": file_ids,
            "missing_expected_ids": sorted(expected - emitted),
            "extra_ids": sorted(emitted - expected),
            "unresolved_source_files": sorted(name for name, ids in file_ids.items() if not ids)}


def read_run(directory):
    invocation = json.loads((directory / "invocation.json").read_text())
    if not invocation.get("executed"):
        raise ValueError(f"{directory}: planned run was not executed")
    report = json.loads((directory / "wptreport.json").read_text())
    if "time_end" not in report or not report.get("results"):
        raise ValueError(f"{directory}: empty or incomplete WPT report")
    statuses = Counter()
    substatuses = Counter()
    unexpected = []
    records = {}
    for result in report["results"]:
        key = (result.get("subsuite", ""), result["test"], None)
        if key in records:
            raise ValueError(f"{directory}: repeated test ID; inspect raw attempts: {key}")
        records[key] = result["status"]
        statuses[result["status"]] += 1
        for item in [result, *result["subtests"]]:
            name = item.get("name")
            if name is not None:
                key = (result.get("subsuite", ""), result["test"], name)
                if key in records:
                    raise ValueError(f"{directory}: repeated subtest name: {key}")
                records[key] = item["status"]
                substatuses[item["status"]] += 1
            if "expected" in item and item["status"] != item["expected"]:
                unexpected.append({"test": result["test"], "subtest": name,
                                   "status": item["status"], "expected": item["expected"],
                                   "known_intermittent": item.get("known_intermittent", []),
                                   "message": item.get("message")})
    summary = {"label": invocation["label"], "source_head": invocation["source_head"],
               "runner_exit": invocation["runner_exit"], "tests": dict(statuses),
               "subtests": dict(substatuses), "unexpected": unexpected,
               "run_info": report["run_info"],
               "note": "Absent tests are unrun; SKIP is counted only if emitted by WPT."}
    summary.update(completeness(directory, invocation, report["results"]))
    return invocation, summary, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--compare", type=Path, help="Compare statuses, not standards authority")
    args = parser.parse_args()
    first, summary, records = read_run(args.run)
    if args.compare:
        other, other_summary, other_records = read_run(args.compare)
        for field in ("wpt_tree", "selection_sha256"):
            if first[field] != other[field]:
                raise ValueError(f"Cannot compare different {field}")
        summary["comparison"] = {
            "other": other_summary,
            "metadata_modes": [first["metadata_mode"], other["metadata_mode"]],
            "only_first": sorted([list(key) for key in records.keys() - other_records.keys()], key=str),
            "only_other": sorted([list(key) for key in other_records.keys() - records.keys()], key=str),
            "changed": [{"id": list(key), "first": records[key], "other": other_records[key]}
                        for key in sorted(records.keys() & other_records.keys(), key=str)
                        if records[key] != other_records[key]],
            "note": "Status differences require spec and configuration review before calling a fork regression.",
        }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
