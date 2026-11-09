#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import argparse
import ctypes
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

BASELINE = "6181e6b5f28a8fbae1d5c3ef6f6626b5a2180213"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def readiness(root, config):
    marker = root / "baseline-complete.json"
    data = json.loads(marker.read_text())
    if not isinstance(data, dict) or type(data.get("schema")) is not int or data.get("schema") != 1 or data.get("source_revision") != BASELINE:
        raise ValueError("Readiness marker schema or preserved baseline revision mismatch")
    if data.get("mozconfig_sha256") != digest(config):
        raise ValueError("Readiness marker mozconfig hash mismatch")
    for field in ("preserved_runtime", "baseline_evidence", "profile_evidence"):
        path = Path(data[field])
        if not path.is_absolute() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"Readiness artifact missing/empty: {field}")
    if not os.access(data["preserved_runtime"], os.X_OK):
        raise ValueError("Preserved Firefox is not executable")
    return data


def write_record(path, data):
    pending = path.with_suffix(".json.tmp")
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(path)


class Cancelled(Exception):
    pass


class ProcessSupervisor:
    def __init__(self):
        self.cancelled = None
        self.child = None

    def __enter__(self):
        # Adopt orphaned grandchildren so every owned process group can be reaped.
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            raise OSError(ctypes.get_errno(), "Cannot enable child subreaper")
        self.previous = {signum: signal.signal(signum, self.cancel)
                         for signum in (signal.SIGINT, signal.SIGTERM)}
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            self.cleanup()
        finally:
            for signum, handler in self.previous.items():
                signal.signal(signum, handler)

    def cancel(self, signum, frame):
        if self.cancelled is None:
            self.cancelled = signum
        self.signal_group(signum)

    def signal_group(self, signum):
        if self.child is not None:
            try:
                os.killpg(self.child.pid, signum)
            except ProcessLookupError:
                pass

    def check_cancelled(self):
        if self.cancelled is not None:
            raise Cancelled()

    def cleanup(self):
        if self.child is None:
            return
        self.signal_group(signal.SIGKILL)
        self.child.wait()
        while True:
            try:
                os.waitpid(-self.child.pid, 0)
            except ChildProcessError:
                break
        self.child = None

    def run(self, command, source, env, log, lock_fd):
        self.check_cancelled()
        try:
            self.child = subprocess.Popen(command, cwd=source, env=env, stdout=log,
                                          stderr=subprocess.STDOUT, pass_fds=(lock_fd,),
                                          start_new_session=True)
            deadline = None
            while self.child.poll() is None:
                if self.cancelled is not None:
                    if deadline is None:
                        self.signal_group(self.cancelled)
                        deadline = time.monotonic() + 5
                    elif time.monotonic() >= deadline:
                        self.signal_group(signal.SIGKILL)
                time.sleep(0.05)
            code = self.child.returncode
        finally:
            # A failed foreground command may leave descendants behind too.
            self.cleanup()
        self.check_cancelled()
        return code

    def output(self, command, source, env, lock_fd):
        with tempfile.TemporaryFile() as log:
            code = self.run(command, source, env, log, lock_fd)
            log.seek(0)
            output = log.read()
        if code:
            raise subprocess.CalledProcessError(code, command, output=output)
        return output


def main():
    parser = argparse.ArgumentParser(description="Serialize warm Firefox builds/tests; plan unless --execute.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("operation", choices=("binaries", "faster", "full", "check"))
    parser.add_argument("command", nargs=argparse.REMAINDER, help="check only: -- COMMAND [ARGS]")
    args = parser.parse_args()
    source, root = args.source.resolve(strict=True), args.root.resolve(strict=True)
    config = source / "tools/performance/baseline/mozconfig"
    if not (source / "mach").is_file() or not config.is_file():
        raise ValueError("Source must contain mach and baseline mozconfig")
    extra = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.operation == "check":
        if not extra:
            parser.error("check requires -- COMMAND [ARGS]")
        command = extra
    else:
        if extra:
            parser.error("Build targets do not accept additional arguments")
        command = ["./mach", "--no-interactive", "build"]
        if args.operation != "full":
            command.append(args.operation)
    env = {"HOME": pwd.getpwuid(os.getuid()).pw_dir,
           "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
           "LC_ALL": "C.UTF-8", "TZ": "Europe/Berlin"}
    env.update(FIREFOX_BASELINE_ROOT=str(root), MOZBUILD_STATE_PATH=str(root / "state"),
               TMPDIR=str(root / "tmp"), MOZCONFIG=str(config), CARGO_HOME=str(root / "cargo"))
    plan = {"operation": args.operation, "command": command, "source": str(source),
            "root": str(root), "lock": str(root / "resource.lock"),
            "environment": env,
            "executed": False}
    if not args.execute:
        try:
            readiness(root, config)
            plan["readiness"] = "ready"
        except (OSError, ValueError, KeyError, TypeError) as error:
            plan["readiness"] = f"blocked: {error}"
        print(json.dumps(plan, indent=2))
        return 0
    # Handlers outlive the lock, including probes, cleanup and final evidence.
    with ProcessSupervisor() as processes, (root / "resource.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Resource busy: another local build/test/measurement owns resource.lock")
        marker = readiness(root, config)
        overrides = ("CC", "CXX", "RUSTC", "CARGO", "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS",
                     "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
                     "MOZ_OBJDIR", "MOZ_MAKE_FLAGS", "MAKEFLAGS", "MOZCONFIG_CONTENT", "CCACHE", "SCCACHE")
        present = [key for key in overrides if os.environ.get(key)]
        if present:
            raise ValueError("Unset build environment overrides first: " + ", ".join(present))
        if shutil.disk_usage(root).free < 10 * 1024**3:
            raise ValueError("Less than 10 GiB disk reserve")
        if not (root / "obj-release/config.status").is_file():
            raise ValueError("Warm configured obj-release absent; no cold build is permitted")
        info = json.loads((root / "obj-release/mozinfo.json").read_text())
        if (info.get("topsrcdir") != str(source)
                or info.get("topobjdir") != str(root / "obj-release")
                or info.get("mozconfig") != str(config)):
            raise ValueError("Warm objdir source/root/mozconfig paths mismatch; do not reuse across checkouts")
        stamp = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%dT%H-%M-%S%z")
        output = root / "iterations" / f"{stamp}-{os.getpid()}"
        output.mkdir(parents=True, exist_ok=False)
        plan.update(marker=marker, marker_sha256=digest(root / "baseline-complete.json"),
                    mozconfig_sha256=digest(config),
                    config_status_sha256=digest(root / "obj-release/config.status"),
                    host=platform.node(), platform=platform.platform(), load=os.getloadavg(),
                    started=datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), status="preparing")
        record = output / "invocation.json"
        write_record(record, plan)
        print(output, flush=True)
        start = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)

        def probe(argv):
            plan["active_command"] = argv
            write_record(record, plan)
            return processes.output(argv, source, env, lock.fileno())

        code = 1
        failure = None
        try:
            tools = {name: root / "state" / folder / "bin" / name for name, folder in
                     (("clang", "clang"), ("rustc", "rustc"), ("cargo", "rustc"))}
            toolchain = {}
            for name, path in tools.items():
                version = probe([str(path), "--version"]).decode().strip()
                toolchain[name] = {"path": str(path), "sha256": digest(path), "version": version}
            plan["source_revision"] = probe(["git", "rev-parse", "HEAD"]).decode().strip()
            plan["tracked_diff_sha256"] = hashlib.sha256(probe(["git", "diff", "HEAD", "--binary"])).hexdigest()
            plan["toolchain"] = toolchain
            fingerprint = {"environment": env, "mozconfig_sha256": plan["mozconfig_sha256"],
                           "toolchain": toolchain}
            plan["build_inputs_sha256"] = hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
            processes.check_cancelled()
            plan.update(status="running", executed=True, active_command=command)
            write_record(record, plan)
            with (output / "command.log").open("wb") as log:
                code = processes.run(command, source, env, log, lock.fileno())
            plan["status"] = "completed"
        except Cancelled:
            plan["status"] = "cancelled"
        except BaseException as error:
            plan["status"] = "launcher_error"
            plan["launcher_error"] = type(error).__name__
            failure = error
        finally:
            processes.cleanup()
            usage = resource.getrusage(resource.RUSAGE_CHILDREN)
            plan["resources"] = {"user_seconds": usage.ru_utime - before.ru_utime,
                                 "system_seconds": usage.ru_stime - before.ru_stime,
                                 "max_child_rss_kib_including_toolchain_probes": usage.ru_maxrss}
            plan["elapsed_seconds"] = time.monotonic() - start
            plan["finished"] = datetime.now(ZoneInfo("Europe/Berlin")).isoformat()
            try:
                plan["mozconfig_preserved"] = digest(config) == plan["mozconfig_sha256"]
            except OSError:
                plan["mozconfig_preserved"] = False
            while True:
                cancelled = processes.cancelled
                plan["cancelled_signal"] = cancelled
                plan["exit_code"] = 128 + cancelled if cancelled else code
                if cancelled:
                    plan["status"] = "cancelled"
                write_record(record, plan)
                if processes.cancelled == cancelled:
                    break
        if failure is not None and processes.cancelled is None:
            raise failure
        if not plan["mozconfig_preserved"]:
            raise ValueError(f"Mozconfig changed during operation; inspect {output}")
        code = plan["exit_code"]
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"iteration: {error}", file=sys.stderr)
        sys.exit(1)
