#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Summarize paired independent Speedometer rounds, excluding profiles/failures."""

import argparse
import json
import math
import random
import statistics
from pathlib import Path


def comparison(firefox, chromium):
    ratios = [left / right for left, right in zip(firefox, chromium, strict=True)]
    logs = [math.log(ratio) for ratio in ratios]
    generator = random.Random(0)
    resampled = sorted(
        math.exp(statistics.mean(generator.choices(logs, k=len(logs))))
        for _ in range(10000)
    )
    return {
        "paired_ratios": ratios,
        "geometric_mean_ratio": math.exp(statistics.mean(logs)),
        "paired_bootstrap_95_percentile_interval": [resampled[249], resampled[9749]],
        "method": "10000 paired-round resamples, geometric mean, seed 0; exploratory small-sample interval",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    args = parser.parse_args()
    root = args.run_directory
    metadata = json.loads((root / "metadata.json").read_text())
    if metadata["profile_only"]:
        raise SystemExit("Profiles cannot be included in comparison scores")
    if metadata["rounds"] < 5:
        raise SystemExit("At least five independent paired rounds are required")
    samples = {
        browser: {} for browser in ("chromium", "official-firefox", "source-firefox")
    }
    for run in sorted(root.iterdir()):
        if not run.is_dir() or run.name == "harness":
            continue
        status = json.loads((run / "exit.json").read_text())
        if status["exit_code"]:
            raise SystemExit(
                f"Failed run must be resolved/documented before summarizing: {run}"
            )
        if any(status["swap_io_delta_pages"].values()):
            raise SystemExit(f"Swap I/O contaminated run: {run}")
        if status["binary_changed"]:
            raise SystemExit(f"Browser changed during run: {run}")
        score = json.loads((run / "score.json").read_text())
        by_round = samples[score["browser"]]
        if score["round"] in by_round:
            raise SystemExit(f"Duplicate round: {run}")
        by_round[score["round"]] = score["score"]
    expected = set(range(1, metadata["rounds"] + 1))
    if any(set(values) != expected for values in samples.values()):
        raise SystemExit("Incomplete paired rounds")
    ordered = {
        browser: [values[round_number] for round_number in sorted(expected)]
        for browser, values in samples.items()
    }
    result = {
        "rounds": metadata["rounds"],
        "scores": ordered,
        "medians": {
            browser: statistics.median(scores) for browser, scores in ordered.items()
        },
        "official_vs_chromium": comparison(
            ordered["official-firefox"], ordered["chromium"]
        ),
        "source_vs_chromium": comparison(
            ordered["source-firefox"], ordered["chromium"]
        ),
        "limits": "Shared VM, no GPU; one workload; five-round bootstrap interval is exploratory. Source ThinLTO/no-PGO configuration differs from official PGO/full-LTO. No source optimization gain is established.",
    }
    (root / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
