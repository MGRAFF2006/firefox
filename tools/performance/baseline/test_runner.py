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
import time
import unittest
import types
import io
from unittest.mock import patch
import run as runner
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
            "open(sys.argv[1], 'w').write(json.dumps([os.getpid(),child.pid])); time.sleep(60)"
        )
        runner = (
            "import os,sys; from pathlib import Path; from run import run_workload,lifecycle; "
            "root=Path(sys.argv[1]); "
            "code=lifecycle.run_guarded(root/'lock',root/'custody/state.json',"
            "lambda owner: run_workload([sys.executable,'-c',sys.argv[2],str(root/'ready')],dict(os.environ),sys.stdout,owner)); "
            "sys.exit(code)"
        )
        process = subprocess.Popen(
            [sys.executable, "-c", runner, str(root), workload],
            cwd=Path(__file__).parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            ready = root / "ready"
            if ready.exists() and ready.stat().st_size:
                pids = json.loads(ready.read_text())
                break
            time.sleep(.01)
        else:
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            self.fail(f"Fixture failed to start: {stdout} {stderr}")
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

    def test_custodian_drains_workload_when_runner_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            process, pids = self.launch(root)
            try:
                process.kill()
                process.wait(timeout=10)
                deadline = time.monotonic() + 10
                with (root / "lock").open("a") as lock:
                    while time.monotonic() < deadline:
                        try:
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except BlockingIOError:
                            time.sleep(.01)
                    else:
                        self.fail("Custodian retained lock beyond fixture deadline")
                    self.assertFalse(any(self.alive(pid) for pid in pids))
                    state = json.loads((root / "custody/state.json").read_text())
                    self.assertTrue(state["cleanup_complete"])
                    self.assertEqual(state["owned"], [])
            finally:
                # Stop fixture actors before manual custodian recovery, never
                # force unlock to satisfy a test deadline.
                for pid in reversed(pids):
                    if self.alive(pid):
                        fd = os.pidfd_open(pid)
                        try:
                            signal.pidfd_send_signal(fd, signal.SIGKILL)
                        finally:
                            os.close(fd)
                process.communicate(timeout=10)


class ExplicitSourceTests(unittest.TestCase):
    def test_source_validation_and_custody_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "harness"
            source.mkdir()
            with self.assertRaises(ValueError):
                runner.validate_source(source)
            (source / "mach").write_text("# synthetic fixture, never run\n")
            with self.assertRaises(ValueError):
                runner.validate_source(source)
            config = source / "tools/performance/baseline/mozconfig"
            config.parent.mkdir(parents=True)
            config.write_text("HARNESS_CONFIG_ONLY\n")
            fake_custody = source / "tools/standards/lifecycle.py"
            fake_custody.parent.mkdir(parents=True)
            fake_custody.write_text("raise RuntimeError('must never import SOURCE custody')\n")
            self.assertEqual(runner.validate_source(source), source.resolve())
            self.assertEqual(runner.LIFECYCLE_PATH, runner.TOOL_ROOT / "tools/standards/lifecycle.py")
            self.assertNotEqual(runner.LIFECYCLE_PATH, fake_custody)
            with self.assertRaises(FileNotFoundError):
                runner.validate_source(source / "missing")

    def test_cli_help_and_default_source(self):
        command = subprocess.run([sys.executable, str(Path(runner.__file__)), "--help"], capture_output=True, text=True)
        self.assertEqual(command.returncode, 0, command.stderr)
        self.assertIn("--source", command.stdout)
        def guarded(lock, record, callback):
            with self.assertRaisesRegex(ValueError, "synthetic-stop"):
                callback(object())
            return 0
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "root"
            with patch.object(runner, "validate_source", return_value=Path(directory)) as validate, patch.object(runner.lifecycle, "run_guarded", side_effect=guarded), patch.object(runner, "run_comparison", side_effect=ValueError("synthetic-stop")):
                with patch.object(sys, "argv", ["run.py", "--root", str(root), "--official", "/control", "--source-build", "/candidate"]):
                    self.assertEqual(runner.main(), 0)
                validate.assert_called_once_with(runner.TOOL_ROOT)

    def test_real_caller_source_paths_and_tool_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);source = base / "harness";source.mkdir();root = base / "root";root.mkdir()
            (source / "mach").write_text("never execute real Mach\n")
            for name, content in [("tools/performance/baseline/mozconfig", "HARNESS_CONFIG_ONLY\n"), ("tools/performance/baseline/run.py", "stale nonexecuted source caller\n"), ("testing/raptor/raptor/tests/benchmarks/speedometer-desktop.toml", "# synthetic manifest\n"), ("third_party/webkit/PerformanceTests/Speedometer3/package.json", "{}"), ("third_party/webkit/PerformanceTests/Speedometer3/moz.yaml", "{}"), ("tools/browsertime/node_modules/browsertime/package.json", "{}")]:
                path = source / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_text(content)
            work = source / "testing/mozharness/build";work.mkdir(parents=True)
            calls = []
            class Owner:
                def output(self, command, cwd, environment):
                    calls.append((command, cwd))
                    self_check.assertEqual(cwd, source)
                    if command[0] == "git":
                        self_check.assertEqual(command[1:3], ["-C", str(source)])
                        return b"fixture-harness-revision"
                    return b"fixture-version"
                def run(self, command, cwd, environment, log):
                    self_check.assertEqual(cwd, source)
                    if command[0] == "tmux": return 1
                    calls.append((command, cwd))
                    self_check.assertIn(str(source / "mach"), command)
                    self_check.assertIn(str(source / "tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver"), command)
                    self_check.assertEqual(environment["MOZCONFIG"], str(source / "tools/performance/baseline/mozconfig"))
                    (work / "raptor.json").write_text(json.dumps({"suites":[{"name":"speedometer3-synthetic", "unit":"score", "value":1}]}))
                    return 0
            self_check = self
            args = types.SimpleNamespace(profile=False, only_browser="source-firefox", rounds=1, chromium=base/"chromium", official=base/"control", source_build=base/"candidate", chromedriver=base/"chromedriver")
            with patch.object(runner, "browser_metadata", return_value={"runtime_artifacts":{}}), patch.object(runner, "swap_counters", return_value={"pswpin":0,"pswpout":0}), patch("sys.stdout",new=io.StringIO()):
                self.assertEqual(runner.run_comparison(args, root, Owner(), source), 0)
            output = next((root / "runs").iterdir());metadata = json.loads((output / "metadata.json").read_text())
            self.assertEqual(metadata["harness_source"]["path"], str(source))
            self.assertEqual(metadata["harness_source"]["mozconfig_sha256"], runner.digest(source / "tools/performance/baseline/mozconfig"))
            self.assertEqual(metadata["harness_sha256"]["run.py"], runner.digest(Path(runner.__file__)))
            self.assertNotEqual(metadata["harness_sha256"]["run.py"], runner.digest(source / "tools/performance/baseline/run.py"))
            self.assertEqual((output / "harness/source-mozconfig").read_text(), "HARNESS_CONFIG_ONLY\n")
            self.assertEqual((output / "harness/run.py").read_bytes(), Path(runner.__file__).read_bytes())
            self.assertFalse((work / "raptor.json").exists())
            self.assertIn("not implied", metadata["browser_slots"]["official-firefox"])
            self.assertIn("only", metadata["toolchain_scope"].lower())
            self.assertEqual(json.loads(next(output.glob("*-source-firefox/score.json")).read_text())["score"], 1)


if __name__ == "__main__":
    unittest.main()
