#!/usr/bin/env python3
"""Attribute recorded Network/FileIO marker overlap to benchmark measure intervals."""
import argparse
import bisect
import collections
import gzip
import hashlib
import json
from pathlib import Path


def rows(table):
    schema = table["schema"]
    for row in table["data"]:
        yield {key: row[index] if index < len(row) else None
               for key, index in schema.items()}


def threads(profile, root_start=None):
    start = profile["meta"]["startTime"]
    if root_start is None:
        root_start = start
    for thread in profile.get("threads", []):
        yield thread, start - root_start, profile["meta"]
    for child in profile.get("processes", []):
        yield from threads(child, root_start)


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1][1] = max(result[-1][1], end)
        else:
            result.append([start, end])
    return result


def overlap(start, end, intervals, ends):
    total = 0
    i = bisect.bisect_right(ends, start)
    while i < len(intervals) and intervals[i][0] < end:
        total += max(0, min(end, intervals[i][1]) - max(start, intervals[i][0]))
        i += 1
    return total


def is_measure(marker):
    data = marker.get("data") or {}
    return (data.get("type") == "UserTiming"
            and data.get("entryType") == "measure"
            and data.get("name", "").endswith(("-sync", "-async")))


def analyze(profile, timing_tid=None):
    entries = list(threads(profile))
    candidates = []
    for thread, offset, meta in entries:
        measures = [m for m in rows(thread["markers"]) if is_measure(m)]
        if measures and (timing_tid is None or str(thread["tid"]) == str(timing_tid)):
            candidates.append((thread, offset, measures))
    if len(candidates) != 1:
        raise ValueError("Select exactly one timing thread with --timing-tid; found "
                         + str([t[0]["tid"] for t in candidates]))
    timing, offset, measures = candidates[0]
    windows = []
    for marker in measures:
        start, end = marker["startTime"], marker["endTime"]
        if marker.get("phase") != 1 or start is None or end is None or end < start:
            raise ValueError("Timing measure is not a complete nonnegative interval")
        windows.append((start + offset, end + offset))
    windows = union(windows)
    ends = [end for _, end in windows]
    io = collections.defaultdict(lambda: {"markers": 0, "overlapping_markers": 0,
                                        "recorded_interval_ms": 0, "overlap_ms": 0})
    incomplete = collections.Counter()
    overlap_events = []
    for thread, offset, meta in entries:
        for marker in rows(thread["markers"]):
            data = marker.get("data") or {}
            kind = data.get("type")
            if kind not in ("Network", "FileIO"):
                continue
            # Never export URIs, filenames, or stacks from private profiles.
            key = (kind, data.get("status") or "unspecified", data.get("cache") or "unspecified")
            key = " / ".join(key)
            group = io[key]
            group["markers"] += 1
            start, end = marker["startTime"], marker["endTime"]
            if marker.get("phase") != 1 or start is None or end is None or end < start:
                incomplete[kind] += 1
                continue
            duration = overlap(start + offset, end + offset, windows, ends)
            group["recorded_interval_ms"] += end - start
            group["overlap_ms"] += duration
            group["overlapping_markers"] += duration > 0
            if duration > 0:
                overlap_events.append({"pid": thread["pid"], "tid": thread["tid"],
                                       "kind": kind, "status": data.get("status"),
                                       "cache": data.get("cache"), "request_id": data.get("id"),
                                       "start_ms": start + offset, "end_ms": end + offset,
                                       "interval_ms": end - start, "overlap_ms": duration})
    return {"schema": 1, "timing_tid": timing["tid"], "measure_count": len(measures),
            "distinct_measure_names": len({m["data"]["name"] for m in measures}),
            "measure_union_ms": sum(b - a for a, b in windows),
            "io_marker_groups": dict(sorted(io.items())),
            "incomplete_io_markers": dict(incomplete),
            "overlap_events": overlap_events,
            "captured_threads": [{"pid": t["pid"], "tid": t["tid"], "name": t["name"]}
                                 for t, _, _ in entries],
            "limits": ["Marker durations may overlap and duplicate requests across processes; do not sum as CPU or wall cost.",
                       "Absent FileIO markers or uncaptured socket/cache/storage threads do not establish absence of IO.",
                       "This does not validate full20suite58step coverage or measure score improvement."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--timing-tid")
    args = parser.parse_args()
    opener = gzip.open if args.profile.suffix == ".gz" else open
    with opener(args.profile, "rt") as stream:
        profile = json.load(stream)
    result = analyze(profile, args.timing_tid)
    with args.profile.open("rb") as stream:
        result["input_sha256"] = hashlib.file_digest(stream, "sha256").hexdigest()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
