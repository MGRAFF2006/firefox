import copy
import unittest

from analyze import analyze, measures, threads


def fixture():
    return {
        'name': 'GeckoMain', 'pid': 42, 'tid': 43,
        'stringTable': ['UserTiming', 'HTMLParserTreeOps',
                        'HTMLParserTreeOpsYieldedOnDeadline', 'case-sync'],
        'markers': {
            'schema': {'name': 0, 'startTime': 1, 'endTime': 2, 'phase': 3, 'data': 4},
            'data': [
                [0, 10, 20, 1, {'type': 'UserTiming', 'entryType': 'measure', 'name': 3}],
                [0, 18, 25, 1, {'type': 'UserTiming', 'entryType': 'measure', 'name': 'case-async'}],
                [0, 100, 100, 0, {'type': 'UserTiming', 'entryType': 'mark', 'name': 'case-sync'}],
                [1, 5, 12, 1], [1, 19, 22, 1], [1, 20, 23, 1],
                [2, 9, 0, 0], [2, 20, 0, 0], [2, 25, 0, 0],
            ],
        },
        'samples': {
            'schema': {'stack': 0, 'time': 1, 'eventDelay': 2},
            'data': [[None, 9, 900], [None, 10, 0], [None, 19, 5],
                     [None, 20, 8], [None, 21], [None, 22, -0.001], [None, 25, 999]],
        },
    }


class AttributionTests(unittest.TestCase):
    def test_clips_and_unions_overlapping_intervals(self):
        result = analyze(fixture())
        measured = result['scopes']['measured']
        self.assertEqual(result['measure_count'], 2)
        self.assertEqual(measured['measure_union_ms'], 15)
        self.assertEqual(measured['parser_interval_union_ms'], 6)
        self.assertEqual(measured['sample_count'], 5)
        self.assertEqual(measured['deadline_yield_count'], 1)
        self.assertEqual(measured['raw_event_delay'],
                         {'count': 3, 'p50_ms': 5, 'p95_ms': 8, 'max_ms': 8})
        self.assertEqual(measured['event_delay_missing'], 1)
        self.assertEqual(measured['event_delay_negative_excluded'], 1)

    def test_sync_async_are_half_open_and_can_overlap(self):
        result = analyze(fixture())['scopes']
        self.assertEqual(result['sync']['deadline_yield_count'], 0)
        self.assertEqual(result['async']['deadline_yield_count'], 1)
        self.assertEqual(result['sync']['sample_count'], 2)
        self.assertEqual(result['async']['sample_count'], 4)

    def test_nested_split_parser_markers_and_truncation(self):
        thread = fixture()
        thread['markers']['data'] = thread['markers']['data'][:2] + [
            [1, 0, 11, 3], [1, 12, 0, 2], [1, 13, 0, 2],
            [1, 0, 14, 3], [1, 0, 16, 3], [1, 24, 0, 2],
        ]
        result = analyze(thread)
        self.assertEqual(result['parser_unmatched_interval_endpoints'], 2)
        self.assertEqual(result['scopes']['measured']['parser_interval_union_ms'], 4)

    def test_no_measures_is_not_zero_cost(self):
        thread = fixture()
        thread['markers']['data'] = []
        with self.assertRaisesRegex(ValueError, 'No benchmark'):
            analyze(thread)

    def test_missing_event_delay_schema_is_not_zero_delay(self):
        thread = fixture()
        del thread['samples']['schema']['eventDelay']
        with self.assertRaisesRegex(ValueError, 'lacks eventDelay'):
            analyze(thread)

    def test_processed_profile_rejected(self):
        thread = fixture()
        thread['markers'] = {'name': [], 'length': 0}
        with self.assertRaisesRegex(ValueError, 'raw Gecko'):
            measures(thread)

    def test_child_process_discovery_preserves_local_timestamps(self):
        child = fixture()
        root = {'threads': [], 'processes': [{'threads': [child]}]}
        before = copy.deepcopy(root)
        self.assertEqual(list(threads(root)), [child])
        self.assertEqual(measures(child), [('case-sync', 10, 20), ('case-async', 18, 25)])
        self.assertEqual(root, before)


if __name__ == '__main__':
    unittest.main()
