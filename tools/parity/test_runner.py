# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import fcntl
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('parity_runner', Path(__file__).with_name('run.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(root=self.root, source=self.root, official=self.root / 'official',
                                       custom=self.root / 'custom', output=self.root / 'result',
                                       node=Path(sys.executable), fixture=Path(__file__).with_name('unsigned-fixture.xpi'),
                                       official_source_record=None, custom_source_record=None)
        self.sample = {'available_bytes': 8 * 1024**3, 'psi_full_avg10': 0, 'swaps': 'header'}

    def read_report(self):
        return json.loads((self.args.output / 'resource-cleanup.json').read_text())

    def test_busy_lock_records_failure_without_launch(self):
        with (self.root / 'resource.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(runner, 'run_target') as launch, mock.patch.object(runner, 'resources', return_value=self.sample):
                self.assertEqual(runner.run(self.args), 1)
                launch.assert_not_called()
        report = self.read_report()
        self.assertFalse(report['lock_acquired'])
        self.assertIn('error', report)

    def test_first_failure_preserved_and_custom_not_launched(self):
        def fail(args, label, binary, record, output, report, deadline):
            report['runs'].append({'label': label})
            raise RuntimeError('synthetic official failure')
        with mock.patch.object(runner, 'run_target', side_effect=fail) as launch, mock.patch.object(runner, 'resources', return_value=self.sample):
            self.assertEqual(runner.run(self.args), 1)
            self.assertEqual(launch.call_count, 1)
        report = self.read_report()
        self.assertEqual(report['runs'], [{'label': 'official'}])
        self.assertIn('synthetic official failure', report['error'])
        self.assertTrue(report['lock_released'])

    def test_cancellation_records_failure_and_releases_lock(self):
        with mock.patch.object(runner, 'run_target', side_effect=KeyboardInterrupt('cancelled')), mock.patch.object(runner, 'resources', return_value=self.sample):
            self.assertEqual(runner.run(self.args), 1)
        self.assertTrue(self.read_report()['cancelled'])
        with (self.root / 'resource.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_existing_output_never_overwritten(self):
        self.args.output.mkdir()
        marker = self.args.output / 'prior-result'
        marker.write_text('retained')
        with self.assertRaises(FileExistsError):
            runner.run(self.args)
        self.assertEqual(marker.read_text(), 'retained')

    def test_owned_group_cleanup_terminates_descendant(self):
        child_pid = self.root / 'child-pid'
        code = ('import subprocess,sys,time; from pathlib import Path; '
                'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]); '
                'Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)')
        process = subprocess.Popen([sys.executable, '-c', code, str(child_pid)], start_new_session=True)
        try:
            deadline = time.monotonic() + 3
            while not child_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(child_pid.exists())
            self.assertEqual(runner.stop_owned_group(process), [])
            self.assertIsNotNone(process.returncode)
            self.assertEqual(runner.group_members(process.pid), [])
        finally:
            if process.poll() is None or runner.group_members(process.pid):
                runner.stop_owned_group(process)

    def test_provenance_omits_endpoint_and_environment(self):
        directory = self.root / 'package' / 'firefox'
        directory.mkdir(parents=True)
        (directory / 'application.ini').write_text('[App]\nVersion=1\nBuildID=2\n[Crash Reporter]\nServerURL=https://example.invalid/?key=secret\n')
        record = directory.parent / 'source-record.json'
        record.write_text(json.dumps({'source_revision': 'a' * 40, 'environment': {'TOKEN': 'secret'}}))
        result = runner.provenance(directory / 'firefox', None)
        self.assertNotIn('secret', json.dumps(result))
        self.assertIn('a' * 40, json.dumps(result))

    def test_zero_exit_with_partial_or_duplicate_result_is_rejected(self):
        self.args.output.mkdir()
        required = ['webrtc-local-data-transport', 'indexeddb-transaction-after-reload',
                    'unsigned-permanent-addon-rejected', 'runtime-configuration-inventory-collected',
                    'required-security-feature-configuration']
        for index, names in enumerate([required[:1], required + required[:1]]):
            with self.subTest(names=names):
                payload = json.dumps({'checks': [{'name': name, 'status': 'pass'} for name in names]})
                command = [sys.executable, '-c', 'import sys; print(sys.argv[1])', payload]
                report = {'runs': [], 'resources': []}
                with mock.patch.object(runner, 'command_for', return_value=command), mock.patch.object(runner, 'resources', return_value=self.sample):
                    with self.assertRaisesRegex(RuntimeError, 'every required check exactly once'):
                        runner.run_target(self.args, f'official-{index}', self.args.official, None, self.args.output, report, time.monotonic() + 5)
                self.assertEqual(report['runs'][0]['returncode'], 0)
                self.assertEqual(report['runs'][0]['remaining_owned_processes'], [])


if __name__ == '__main__':
    unittest.main()
