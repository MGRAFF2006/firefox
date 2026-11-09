#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Exercise runtime mutation and cancellation without launching a browser."""

import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from run import browser_metadata, changed_artifacts


class RuntimeTests(unittest.TestCase):
    def test_engine_replacement_and_removal_reject_unchanged_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "firefox"
            binary.write_text("#!/bin/sh\necho fixture\n")
            binary.chmod(0o700)
            engine = root / "libxul.so"
            engine.write_bytes(b"original")
            ini = root / "platform.ini"
            ini.write_text("[Build]\nBuildID=fixture\n")
            metadata = browser_metadata(binary)
            self.assertEqual(changed_artifacts(metadata), [])
            stamp = engine.stat()
            engine.write_bytes(b"modified")
            os.utime(engine, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            self.assertEqual(changed_artifacts(metadata), [str(engine)])
            engine.write_bytes(b"original")
            os.utime(engine, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            ini.unlink()
            self.assertEqual(changed_artifacts(metadata), [str(ini)])


class CancellationTests(unittest.TestCase):
    def alive(self, pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
        except FileNotFoundError:
            return False

    def launch(self, root):
        workload = (
            "import json,os,signal,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c',"
            "'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)']); "
            "print(json.dumps([os.getpid(),child.pid]),flush=True); time.sleep(60)"
        )
        runner = (
            "import fcntl,os,sys; from run import run_workload,WorkloadCancelled; "
            "lock=open(sys.argv[1],'a'); fcntl.flock(lock,fcntl.LOCK_EX); "
            "\ntry: code=run_workload([sys.executable,'-c',sys.argv[2]],dict(os.environ),sys.stdout,lock.fileno())"
            "\nexcept WorkloadCancelled as error: code=128+error.signum"
            "\nsys.exit(code)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", runner, str(root / "lock"), workload],
            cwd=Path(__file__).parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        ready, _, _ = select.select([process.stdout], [], [], 10)
        if not ready:
            process.kill()
            stdout, stderr = process.communicate()
            self.fail(f"Fixture failed to start: {stdout} {stderr}")
        pids = json.loads(process.stdout.readline())
        return process, pids

    def test_signal_cleans_owned_group_before_unlock(self):
        for signum in (signal.SIGTERM, signal.SIGINT):
            with self.subTest(
                signal=signum
            ), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                process, pids = self.launch(root)
                try:
                    with (root / "lock").open("a") as lock:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        process.send_signal(signum)
                        process.communicate(timeout=10)
                        self.assertEqual(process.returncode, 128 + signum)
                        self.assertFalse(any(self.alive(pid) for pid in pids))
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                finally:
                    try:
                        os.killpg(pids[0], signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    if process.poll() is None:
                        process.kill()
                    process.communicate()

    def test_workload_inherits_lock_when_runner_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process, pids = self.launch(root)
            try:
                process.kill()
                process.wait(timeout=10)
                self.assertTrue(self.alive(pids[0]))
                with (root / "lock").open("a") as lock:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                try:
                    os.killpg(pids[0], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
