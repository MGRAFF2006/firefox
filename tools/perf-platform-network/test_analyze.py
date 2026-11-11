import unittest

from analyze import analyze

SCHEMA = {'name': 0, 'startTime': 1, 'endTime': 2, 'phase': 3, 'category': 4, 'data': 5}


def marker(start, end, data, phase=1):
    return [0, start, end, phase, 0, data]


def measure(start, end, name='step-sync', entry='measure', phase=1):
    return marker(start, end, {'type': 'UserTiming', 'entryType': entry, 'name': name}, phase)


def thread(tid, markers):
    return {'tid': tid, 'pid': tid, 'name': 'GeckoMain',
            'markers': {'schema': SCHEMA, 'data': markers}}


def process(start, children=(), threads=()):
    return {'meta': {'startTime': start}, 'threads': list(threads), 'processes': list(children)}


def network():
    return {'type': 'Network', 'status': 'STATUS_STOP', 'cache': 'Hit',
            'id': 1, 'URI': 'https://private.invalid/token'}


class MarkerAttributionTests(unittest.TestCase):
    def test_cross_process_clock_and_union_clipping(self):
        # Child windows land at 110..125 in root time, with overlapping measures.
        content = process(1100, threads=[thread(2, [measure(10, 20), measure(15, 25)])])
        root = process(1000, [content], [thread(1, [marker(105, 130, network())])])
        result = analyze(root)
        group = result['io_marker_groups']['Network / STATUS_STOP / Hit']
        self.assertEqual(result['measure_union_ms'], 15)
        self.assertEqual(group['overlap_ms'], 15)
        self.assertEqual(group['recorded_interval_ms'], 25)
        self.assertEqual(group['overlapping_markers'], 1)
        self.assertNotIn('private.invalid', str(result))

    def test_marks_prepare_load_and_touching_boundaries_do_not_count(self):
        markers = [measure(10, 20), measure(0, 100, 'runner-prepare'),
                   measure(0, 0, 'step-async', 'mark', 0),
                   marker(0, 10, network()), marker(20, 30, network()),
                   [0, 12, 0, 0]]  # Raw marker rows may omit optional payload.
        result = analyze(process(0, threads=[thread(1, markers)]))
        self.assertEqual(result['measure_count'], 1)
        self.assertEqual(result['io_marker_groups']['Network / STATUS_STOP / Hit']['overlap_ms'], 0)

    def test_incomplete_io_reported_without_fabricating_duration(self):
        markers = [measure(10, 20), marker(12, None, {'type': 'FileIO', 'status': None, 'cache': None}, phase=2)]
        result = analyze(process(0, threads=[thread(1, markers)]))
        self.assertEqual(result['incomplete_io_markers'], {'FileIO': 1})
        self.assertEqual(result['io_marker_groups']['FileIO / unspecified / unspecified']['overlap_ms'], 0)

    def test_ambiguous_timing_threads_require_explicit_selection(self):
        root = process(0, threads=[thread(1, [measure(0, 10)]), thread(2, [measure(20, 30)])])
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            analyze(root)
        result = analyze(root, '2')
        self.assertEqual(result['timing_tid'], 2)
        self.assertEqual(result['measure_union_ms'], 10)

    def test_incomplete_measure_is_rejected(self):
        root = process(0, threads=[thread(1, [measure(10, None)])])
        with self.assertRaisesRegex(ValueError, 'complete'):
            analyze(root)


if __name__ == '__main__':
    unittest.main()
