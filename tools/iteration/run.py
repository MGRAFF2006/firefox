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


def run_command(command, source, env, log, lock_fd):
    # Adopt orphaned grandchildren so cancellation can reap the whole owned group.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), "Cannot enable child subreaper")
    cancelled = None
    child = None

    def cancel(signum, frame):
        nonlocal cancelled
        if cancelled is None:
            cancelled = signum
        if child is not None:
            try:
                os.killpg(child.pid, signum)
            except ProcessLookupError:
                pass

    previous = {signum: signal.signal(signum, cancel)
                for signum in (signal.SIGINT, signal.SIGTERM)}
    try:
        child = subprocess.Popen(command, cwd=source, env=env, stdout=log,
                                 stderr=subprocess.STDOUT, pass_fds=(lock_fd,),
                                 start_new_session=True)
        deadline = None
        while True:
            if cancelled is not None:
                if deadline is None:
                    cancel(cancelled, None)
                    deadline = time.monotonic() + 5
                elif time.monotonic() >= deadline:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            code = child.poll()
            if code is not None:
                if cancelled is not None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    while True:
                        try:
                            os.waitpid(-child.pid, 0)
                        except ChildProcessError:
                            break
                return (128 + cancelled if cancelled else code), cancelled
            time.sleep(0.05)
    except BaseException:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            while True:
                try:
                    os.waitpid(-child.pid, 0)
                except ChildProcessError:
                    break
        raise
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


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
    # The baseline launcher already owns this inode; never unlink or replace it.
    with (root / "resource.lock").open("a") as lock:
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
        tools = {name: root / "state" / folder / "bin" / name for name, folder in
                 (("clang", "clang"), ("rustc", "rustc"), ("cargo", "rustc"))}
        toolchain = {}
        for name, path in tools.items():
            version = subprocess.check_output([str(path), "--version"], text=True, env=env).strip()
            toolchain[name] = {"path": str(path), "sha256": digest(path), "version": version}
        stamp = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%Y-%m-%dT%H-%M-%S%z")
        output = root / "iterations" / f"{stamp}-{os.getpid()}"
        output.mkdir(parents=True, exist_ok=False)
        plan.update(source_revision=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True, env=env).strip(),
                    tracked_diff_sha256=hashlib.sha256(subprocess.check_output(["git", "diff", "HEAD", "--binary"], cwd=source, env=env)).hexdigest(),
                    marker=marker, marker_sha256=digest(root / "baseline-complete.json"),
                    mozconfig_sha256=digest(config), toolchain=toolchain,
                    config_status_sha256=digest(root / "obj-release/config.status"),
                    host=platform.node(), platform=platform.platform(), load=os.getloadavg(),
                    started=datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), executed=True)
        fingerprint = {"environment": env, "mozconfig_sha256": plan["mozconfig_sha256"],
                       "toolchain": toolchain}
        plan["build_inputs_sha256"] = hashlib.sha256(
            json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()
        plan["status"] = "running"
        record = output / "invocation.json"
        write_record(record, plan)
        print(output, flush=True)
        start = time.monotonic()
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        try:
            with (output / "command.log").open("wb") as log:
                code, cancelled = run_command(command, source, env, log, lock.fileno())
            plan["exit_code"] = code
            plan["cancelled_signal"] = cancelled
            plan["status"] = "cancelled" if cancelled else "completed"
        except BaseException as error:
            plan["status"] = "launcher_error"
            plan["launcher_error"] = type(error).__name__
            raise
        finally:
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
            write_record(record, plan)
        if not plan["mozconfig_preserved"]:
            raise ValueError(f"Mozconfig changed during operation; inspect {output}")
        return code if code >= 0 else 128 - code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        print(f"iteration: {error}", file=sys.stderr)
        sys.exit(1)
