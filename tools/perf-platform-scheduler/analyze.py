#!/usr/bin/env python3
"""Attribute parser deadline yields to measured intervals in raw Gecko profiles."""

import argparse
import bisect
import gzip
import hashlib
import json
import math
from pathlib import Path


def cell(row, schema, key):
    index = schema.get(key)
    return row[index] if index is not None and index < len(row) else None


def union(intervals):
    result = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


class Windows:
    def __init__(self, intervals):
        self.intervals = union(intervals)
        self.starts = [start for start, _ in self.intervals]

    def contains(self, time):
        index = bisect.bisect_right(self.starts, time) - 1
        return index >= 0 and time < self.intervals[index][1]

    def clip(self, start, end):
        index = max(0, bisect.bisect_right(self.starts, start) - 1)
        result = []
        for left, right in self.intervals[index:]:
            if left >= end:
                break
            if right > start:
                result.append((max(left, start), min(right, end)))
        return result


def markers(thread):
    table = thread['markers']
    if 'schema' not in table or 'data' not in table:
        raise ValueError('Expected raw Gecko row tables, not a processed profile')
    strings = thread['stringTable']
    for row in table['data']:
        schema = table['schema']
        name = cell(row, schema, 'name')
        yield {
            'name': strings[name],
            'start': cell(row, schema, 'startTime'),
            'end': cell(row, schema, 'endTime'),
            'phase': cell(row, schema, 'phase'),
            'data': cell(row, schema, 'data') or {},
        }


def measures(thread):
    result = []
    for marker in markers(thread):
        data = marker['data']
        name = data.get('name', '')
        if isinstance(name, int):
            name = thread['stringTable'][name]
        if (data.get('type') == 'UserTiming' and data.get('entryType') == 'measure'
                and name.endswith(('-sync', '-async'))):
            if marker['phase'] != 1 or marker['end'] < marker['start']:
                raise ValueError('Benchmark measure must be a complete interval')
            result.append((name, marker['start'], marker['end']))
    return result


def threads(profile):
    yield from profile.get('threads', [])
    for process in profile.get('processes', []):
        yield from threads(process)


def distribution(values):
    values = sorted(values)
    if not values:
        return {'count': 0, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}
    return {
        'count': len(values),
        'p50_ms': values[math.ceil(len(values) * .5) - 1],
        'p95_ms': values[math.ceil(len(values) * .95) - 1],
        'max_ms': values[-1],
    }


def analyze(thread):
    measured = measures(thread)
    if not measured:
        raise ValueError('No benchmark -sync/-async UserTiming measures')
    all_markers = list(markers(thread))
    parser_intervals = []
    unmatched = 0
    pending = []
    yields = []
    for marker in all_markers:
        if marker['name'] == 'HTMLParserTreeOpsYieldedOnDeadline':
            yields.append(marker['start'])
        if marker['name'] != 'HTMLParserTreeOps':
            continue
        if marker['phase'] == 1:
            parser_intervals.append((marker['start'], marker['end']))
        elif marker['phase'] == 2:
            pending.append(marker['start'])
        elif marker['phase'] == 3:
            if pending:
                parser_intervals.append((pending.pop(), marker['end']))
            else:
                unmatched += 1
    unmatched += len(pending)
    if any(end < start for start, end in parser_intervals):
        raise ValueError('Reversed parser marker interval')
    samples = thread['samples']
    if 'eventDelay' not in samples['schema']:
        raise ValueError('Profile lacks eventDelay samples')
    result = {
        'pid': thread['pid'], 'tid': thread.get('tid'),
        'measure_count': len(measured),
        'unique_measure_names': len({name for name, _, _ in measured}),
        'parser_unmatched_interval_endpoints': unmatched,
        'scopes': {},
    }
    for scope in ('whole_thread', 'measured', 'sync', 'async'):
        selected = measured if scope == 'measured' else [
            m for m in measured if m[0].endswith('-' + scope)]
        windows = Windows([(start, end) for _, start, end in selected])
        whole = scope == 'whole_thread'
        parser = union(parser_intervals if whole else [
            interval for start, end in parser_intervals
            for interval in windows.clip(start, end)])
        parser_windows = Windows(parser)
        delays, parser_delays = [], []
        selected_samples = missing = negative = 0
        for row in samples['data']:
            time = cell(row, samples['schema'], 'time')
            if not whole and not windows.contains(time):
                continue
            selected_samples += 1
            delay = cell(row, samples['schema'], 'eventDelay')
            if delay is None:
                missing += 1
            elif not math.isfinite(delay):
                raise ValueError('Non-finite eventDelay')
            elif delay < 0:
                negative += 1
            else:
                delays.append(delay)
                if parser_windows.contains(time):
                    parser_delays.append(delay)
        result['scopes'][scope] = {
            'measure_count': len(selected) if not whole else None,
            'measure_union_ms': sum(b-a for a, b in windows.intervals) if not whole else None,
            'parser_interval_union_ms': sum(b-a for a, b in parser),
            'deadline_yield_count': sum(whole or windows.contains(t) for t in yields),
            'sample_count': selected_samples,
            'event_delay_missing': missing,
            'event_delay_negative_excluded': negative,
            'raw_event_delay': distribution(delays),
            'raw_event_delay_inside_parser_intervals': distribution(parser_delays),
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('profile', type=Path)
    parser.add_argument('--source-revision', required=True)
    parser.add_argument('--expect-sha256')
    parser.add_argument('--pid', type=int)
    args = parser.parse_args()
    with args.profile.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if args.expect_sha256 and digest != args.expect_sha256:
        parser.error('Profile SHA-256 differs from expected evidence')
    opener = gzip.open if args.profile.suffix == '.gz' else open
    with opener(args.profile, 'rt') as stream:
        profile = json.load(stream)
    candidates = [t for t in threads(profile) if t.get('name') == 'GeckoMain'
                  and (args.pid is None or int(t['pid']) == args.pid) and measures(t)]
    if len(candidates) != 1:
        parser.error(f'Expected one measured GeckoMain thread, found {len(candidates)}; specify --pid')
    result = analyze(candidates[0])
    result['provenance'] = {
        'profile_sha256': digest, 'source_revision_supplied': args.source_revision,
        'profile_version': profile['meta'].get('version'),
        'root_profile_app_build_id': profile['meta'].get('appBuildID'),
    }
    result['limits'] = [
        'Raw eventDelay = queue delay plus running-event elapsed time; not scheduler CPU cost.',
        'Quantiles weight samples equally, not CPU time, tasks, or elapsed time.',
        'Parser marker overlap is elapsed interval coverage, not parser exclusive CPU cost.',
        'Missing, negative and truncated data are reported, not converted to zero.',
        'Zero parser overlap does not exclude unmarked fragment parser or tokenizer work.',
        'Source revision is supplied provenance, not independently verified from the profile.',
    ]
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
