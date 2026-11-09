# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import fcntl
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import shutil
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

    def test_default_node_dispatcher_is_rejected_before_launch(self):
        backing = self.root / 'mise'
        backing.write_text('#!/bin/sh\ncase "$0" in */node) exit 0;; *) exit 23;; esac\n')
        backing.chmod(0o755)
        shim = self.root / 'node'
        shim.symlink_to(backing)
        self.assertEqual(subprocess.run([str(shim)], check=False).returncode, 0)
        self.assertEqual(subprocess.run([str(shim.resolve())], check=False).returncode, 23)
        for name in ['official', 'custom']:
            (self.root / name).touch()
        argv = ['run.py', '--root', str(self.root), '--source', str(self.root),
                '--official', str(self.args.official), '--custom', str(self.args.custom),
                '--output', str(self.args.output)]
        diagnostic = io.StringIO()
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(runner.shutil, 'which', return_value=str(shim)), \
             mock.patch.object(runner.subprocess, 'Popen') as launch, contextlib.redirect_stderr(diagnostic):
            with self.assertRaises(SystemExit) as error:
                runner.arguments()
            self.assertEqual(error.exception.code, 2)
            launch.assert_not_called()
        self.assertIn('pass --node a native ELF Node executable', diagnostic.getvalue())
        self.assertFalse(self.args.output.exists())

    def test_native_node_launches_with_isolated_home_and_path(self):
        candidate = shutil.which('node', path='/usr/bin:/bin')
        if not candidate:
            self.skipTest('native Node unavailable on system test PATH')
        node = runner.native_node(Path(candidate))
        home = self.root / 'isolated-home'
        home.mkdir()
        env = {'PATH': os.defpath, 'HOME': str(home), 'LANG': 'C.UTF-8'}
        probe = subprocess.run([str(node), '-e', 'process.stdout.write(process.release.name + "\\n" + process.env.HOME)'],
                               env=env, text=True, capture_output=True, check=True, timeout=5)
        self.assertEqual(probe.stdout, 'node\n' + str(home))

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
        def fail(args, label, binary, record, output, report, deadline, owned=None):
            report['runs'].append({'label': label})
            raise RuntimeError('synthetic official failure')
        with mock.patch.object(runner, 'run_target', side_effect=fail) as launch, mock.patch.object(runner, 'resources', return_value=self.sample):
            self.assertEqual(runner.run(self.args), 1)
            self.assertEqual(launch.call_count, 1)
        report = self.read_report()
        self.assertEqual(report['runs'], [{'label': 'official'}])
        self.assertIn('synthetic official failure', report['error'])
        self.assertTrue(report['lock_released'])

    def test_pending_descendant_retains_lock_and_failure_evidence(self):
        pending = True
        observations = []
        process = mock.Mock(pid=99999999)
        process.poll.return_value = 0

        def fail(args, label, binary, record, output, report, deadline, owned=None):
            owned.append(process)
            report['runs'].append({'label': label, 'owned_process_group': process.pid})
            raise RuntimeError('injected cleanup incomplete after leader exit')

        def wait_for_descendant(_):
            nonlocal pending
            snapshot = self.read_report()
            self.assertTrue(snapshot['cleanup_pending'])
            self.assertFalse(snapshot['lock_released'])
            self.assertIn('injected cleanup incomplete', snapshot['error'])
            probe = ('import fcntl,sys; f=open(sys.argv[1],"a"); '
                     '\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                     '\nexcept BlockingIOError: sys.exit(23)')
            result = subprocess.run([sys.executable, '-c', probe, str(self.root / 'resource.lock')], timeout=5)
            self.assertEqual(result.returncode, 23)
            observations.append('locked while descendant pending')
            pending = False

        clock = mock.Mock(wraps=runner.time)
        clock.sleep.side_effect = wait_for_descendant
        with mock.patch.object(runner, 'run_target', side_effect=fail), \
             mock.patch.object(runner, 'resources', return_value=self.sample), \
             mock.patch.object(runner, 'group_members', side_effect=lambda _: [process.pid] if pending else []), \
             mock.patch.object(runner, 'time', clock):
            self.assertEqual(runner.run(self.args), 1)
        self.assertEqual(observations, ['locked while descendant pending'])
        report = self.read_report()
        self.assertFalse(report['cleanup_pending'])
        self.assertTrue(report['lock_released'])
        self.assertEqual(report['unreaped_leaders'], [])
        with (self.root / 'resource.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

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
