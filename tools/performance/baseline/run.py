#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run complete Speedometer 3 suites serially in isolated Raptor profiles."""

import argparse
import datetime
import hashlib
import json
import math
import os
import platform
import shutil
import signal
import tempfile
import uuid
import types
import subprocess
from pathlib import Path

from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
TOOL_ROOT = HERE.parents[2]
TIMEZONE = ZoneInfo("Europe/Berlin")

# Reuse the reviewed repository custodian; fail closed on a missing or changed
# dependency instead of importing a same-named module from the caller's path.
LIFECYCLE_PATH = TOOL_ROOT / "tools/standards/lifecycle.py"
LIFECYCLE_SHA256 = "c1854a305f633691f3991b094e7c2d5b84f3977385216212fae7d929ec346232"
_lifecycle_bytes = LIFECYCLE_PATH.read_bytes()
if hashlib.sha256(_lifecycle_bytes).hexdigest() != LIFECYCLE_SHA256:
    raise RuntimeError("Scoring lifecycle dependency differs from reviewed c0bef bytes")
lifecycle = types.ModuleType("_scoring_lifecycle")
lifecycle.__file__ = str(LIFECYCLE_PATH)
exec(compile(_lifecycle_bytes, str(LIFECYCLE_PATH), "exec"), lifecycle.__dict__)


def capture(*command, supervisor=None, source=TOOL_ROOT):
    if supervisor is not None:
        return supervisor.output(list(command), source, dict(os.environ)).decode().strip()
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, cwd=source).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_identity(path):
    status = path.stat()
    return [status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns]


def browser_metadata(binary, supervisor=None, *, source=TOOL_ROOT):
    result = {
        "path": str(binary),
        "version": capture(str(binary), "--version", source=source, supervisor=supervisor),
        "sha256": digest(binary),
        "file_identity": file_identity(binary),
    }
    artifacts = {
        str(binary): {
            "sha256": result["sha256"],
            "file_identity": result["file_identity"],
        }
    }
    for name in (
        "firefox-bin",
        "libxul.so",
        "application.ini",
        "platform.ini",
        "chrome-sandbox",
    ):
        artifact = binary.parent / name
        if artifact.is_file():
            result[name] = {"sha256": digest(artifact)}
            artifacts[str(artifact)] = {
                "sha256": result[name]["sha256"],
                "file_identity": file_identity(artifact),
            }
            if artifact.suffix == ".ini":
                result[name]["content"] = artifact.read_text()
    source_record = binary.parent.parent / "source-revision.txt"
    if source_record.is_file():
        result["source_revision_external"] = source_record.read_text().strip()
        artifacts[str(source_record)] = {
            "sha256": digest(source_record),
            "file_identity": file_identity(source_record),
        }
    result["runtime_artifacts"] = artifacts
    return result


def changed_artifacts(metadata):
    changed = []
    for name, expected in metadata["runtime_artifacts"].items():
        path = Path(name)
        try:
            if (
                file_identity(path) != expected["file_identity"]
                or digest(path) != expected["sha256"]
            ):
                changed.append(name)
        except OSError:
            changed.append(name)
    return changed


class WorkloadCancelled(Exception):
    def __init__(self, signum):
        self.signum = signum
        super().__init__(f"Workload cancelled by signal {signum}")


def run_workload(command, environment, log, supervisor, source=TOOL_ROOT):
    # Anonymous storage preserves environment-dump redaction without a second
    # process owner or a durable unredacted Raptor log. The custodian drains every
    # owned descendant, including separate sessions, before this call returns.
    with tempfile.TemporaryFile(mode="w+t") as raw:
        try:
            try:
                return supervisor.run(command, source, environment, raw)
            except lifecycle.Cancelled:
                raise WorkloadCancelled(supervisor.cancelled or signal.SIGTERM)
        finally:
            raw.seek(0)
            for line in raw:
                if "'env':" in line or '"env":' in line or "Using env:" in line:
                    log.write("[harness environment dump omitted]\n")
                else:
                    log.write(line)
            log.flush()


def swap_counters():
    return {
        key: int(value)
        for key, value in (
            line.split() for line in Path("/proc/vmstat").read_text().splitlines()
        )
        if key in ("pswpin", "pswpout")
    }


def collect_artifacts(run, source=TOOL_ROOT):
    work = source / "testing/mozharness/build"
    shared_upload = work / "blobber_upload_dir"
    if shared_upload.exists():
        shutil.move(str(shared_upload), str(run / "artifacts/mozharness"))
    for report in work.glob("raptor*.json"):
        shutil.move(str(report), str(run / report.name))
    screenshots = work / "screenshots.html"
    if screenshots.exists():
        shutil.move(str(screenshots), str(run / screenshots.name))


def validate_source(value):
    """Select a real harness checkout without letting it supply custody code."""
    source = value.resolve(strict=True)
    if not source.is_dir() or not (source / "mach").is_file():
        raise ValueError("--source must name an existing checkout containing mach")
    if not (source / "tools/performance/baseline/mozconfig").is_file():
        raise ValueError("--source lacks the harness mozconfig")
    return source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=TOOL_ROOT,
        help="Existing Mach/Raptor harness checkout (defaults to the tool checkout)",
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--official", type=Path, required=True)
    parser.add_argument("--source-build", type=Path, required=True)
    parser.add_argument(
        "--chromium", type=Path, default=Path("/usr/lib/chromium/chromium")
    )
    parser.add_argument(
        "--chromedriver", type=Path, default=Path("/usr/bin/chromedriver")
    )
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--profile", action="store_true")
    parser.add_argument(
        "--only-browser",
        choices=("chromium", "official-firefox", "source-firefox"),
        help="Run a diagnostic subset; incomplete comparisons cannot be summarized",
    )
    args = parser.parse_args()
    if args.rounds < 1:
        parser.error("--rounds must be positive")
    if args.profile and args.only_browser not in (None, "source-firefox"):
        parser.error("Profiling is only supported for the preserved source Firefox")
    source = validate_source(args.source)
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    custody = root / "scoring-custody" / uuid.uuid4().hex / "state.json"
    return lifecycle.run_guarded(
        root / "resource.lock", custody,
        lambda supervisor: run_comparison(args, root, supervisor, source),
    )


def run_comparison(args, root, supervisor, source=TOOL_ROOT):
    with tempfile.TemporaryFile() as probe:
        if supervisor.run(["tmux", "has-session", "-t", "firefox-build"],
                          source, dict(os.environ), probe) == 0:
            raise SystemExit("Refusing to benchmark during firefox-build")
    if shutil.disk_usage(root).free < 10 * 1024**3:
        raise SystemExit("Less than 10 GiB disk reserve")
    stamp = datetime.datetime.now(TIMEZONE).strftime("%Y-%m-%dT%H-%M-%S%z")
    output = root / ("profiles" if args.profile else "runs") / stamp
    output.mkdir(parents=True)
    browsers = [
        ("chromium", "chrome", args.chromium.resolve()),
        ("official-firefox", "firefox", args.official.resolve()),
        ("source-firefox", "firefox", args.source_build.resolve()),
    ]
    if args.profile:
        browsers = browsers[2:]
    if args.only_browser:
        browsers = [
            browser for browser in browsers if browser[0] == args.only_browser
        ]
    manifest = (
        source / "testing/raptor/raptor/tests/benchmarks/speedometer-desktop.toml"
    )
    metadata = {
        "lifecycle": {
            "path": str(LIFECYCLE_PATH),
            "sha256": LIFECYCLE_SHA256,
            "accepted_merge": "c0bef74e02eee17d5df7a952269bbfb1557cb2ad",
        },
        "checkout_revision": capture("git", "-C", str(source), "rev-parse", "HEAD", source=source, supervisor=supervisor),
        "source_diff": capture("git", "-C", str(source), "diff", "--stat", source=source, supervisor=supervisor),
        "host": platform.uname()._asdict(),
        "cpu": Path("/proc/cpuinfo").read_text(),
        "manifest_sha256": digest(manifest),
        "benchmark": {
            name: (
                source / "third_party/webkit/PerformanceTests/Speedometer3" / name
            ).read_text()
            for name in ("package.json", "moz.yaml")
        },
        "mozconfig": (source / "tools/performance/baseline/mozconfig").read_text(),
        "rounds": args.rounds,
        "profile_only": args.profile,
        "browsers": {
            label: browser_metadata(binary, source=source, supervisor=supervisor) for label, _, binary in browsers
        },
        "chromedriver": capture(str(args.chromedriver), "--version", source=source, supervisor=supervisor),
        "geckodriver": capture(
            str(
                source
                / "tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver"
            ),
            "--version",
            source=source, supervisor=supervisor,
        ),
        "toolchain": {
            name: capture(str(root / "state" / path), "--version", source=source, supervisor=supervisor)
            for name, path in {
                "clang": "clang/bin/clang",
                "rustc": "rustc/bin/rustc",
                "cargo": "rustc/bin/cargo",
                "node": "node/bin/node",
            }.items()
        },
        "harness_sha256": {
            name: digest(HERE / name)
            for name in (
                "run.py",
                "mozconfig",
                "smoke.cjs",
                "build.sh",
                "preserve.sh",
                "summarize.py",
            )
        },
        "browsertime_package": json.loads(
            (
                source / "tools/browsertime/node_modules/browsertime/package.json"
            ).read_text()
        ),
    }
    harness_snapshot = output / "harness"
    harness_snapshot.mkdir()
    for name in metadata["harness_sha256"]:
        shutil.copyfile(
            HERE / name, harness_snapshot / name
        )
    # Tool snapshots are the executed collector, not similarly named source files.
    metadata["collector_semantics"] = "explicit-harness-source-v1"
    metadata["tool_root"] = str(TOOL_ROOT)
    metadata["harness_source"] = {
        "path": str(source), "checkout_revision": metadata["checkout_revision"],
        "source_diff": metadata["source_diff"],
        "mozconfig_path": str(source / "tools/performance/baseline/mozconfig"),
        "mozconfig_sha256": digest(source / "tools/performance/baseline/mozconfig"),
        "mozconfig_snapshot": "harness/source-mozconfig",
        "limits": "Harness identity/configuration only; not either supplied browser's compiled source/configuration.",
    }
    metadata["toolchain_scope"] = "Harness-side tools only; runtime build toolchains require separate role receipts."
    metadata["browser_slots"] = {
        "official-firefox": "Supplied control slot; official/PGO status is not implied by this legacy name.",
        "source-firefox": "Supplied candidate slot; bind genuine runtime-role/build receipts separately.",
        "chromium": "Supplied Chromium comparison runtime.",
    }
    shutil.copyfile(source / "tools/performance/baseline/mozconfig", harness_snapshot / "source-mozconfig")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    runtime_dir = root / "tmp" / f"runtime-{stamp}"
    runtime_dir.mkdir(parents=True, mode=0o700)
    environment = {
        "HOME": os.environ["HOME"],
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "FIREFOX_BASELINE_ROOT": str(root),
        "MOZBUILD_STATE_PATH": str(root / "state"),
        "TMPDIR": str(root / "tmp"),
        "MOZCONFIG": str(source / "tools/performance/baseline/mozconfig"),
        "DISABLE_PROFILE_LAUNCH": "1",
        "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "XDG_CACHE_HOME": str(root / "cache"),
        "UV_CACHE_DIR": str(root / "cache/uv"),
        "PIP_CACHE_DIR": str(root / "cache/pip"),
        "npm_config_cache": str(root / "cache/npm"),
        "PATH": str(root / "state/node/bin")
        + os.pathsep
        + str(Path.home() / ".local/bin")
        + os.pathsep
        + "/usr/local/bin:/usr/bin:/bin",
    }
    (root / "tmp").mkdir(exist_ok=True)
    for round_number in range(args.rounds):
        order = (
            browsers[round_number % len(browsers) :]
            + browsers[: round_number % len(browsers)]
        )
        for label, app, binary in order:
            shared_work = source / "testing/mozharness/build"
            if (shared_work / "blobber_upload_dir").exists() or (
                shared_work / "raptor.json"
            ).exists():
                raise SystemExit(
                    "Uncollected mozharness output exists; preserve it before starting another run"
                )
            if changed_artifacts(metadata["browsers"][label]):
                raise SystemExit(f"Browser changed during the comparison: {binary}")
            run = output / f"{round_number + 1:02d}-{label}"
            run.mkdir()
            upload = run / "artifacts"
            upload.mkdir()
            command = [
                "xvfb-run",
                "-a",
                "-s",
                "-screen 0 1920x1080x24",
                str(source / "mach"),
                "--no-interactive",
                "raptor",
                "--browsertime",
                "-t",
                "speedometer3",
                "--app",
                app,
                "--binary",
                str(binary),
                "--browser-cycles",
                "1",
                "--page-cycles",
                "1",
                "--browsertime-chromedriver",
                str(args.chromedriver),
                "--browsertime-geckodriver",
                str(
                    source
                    / "tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver"
                ),
                "--browsertime-arg",
                "docker=false",
            ]
            if app == "firefox":
                command.append("--is-release-build")
            if args.profile:
                command.append("--gecko-profile")
            environment["MOZ_UPLOAD_DIR"] = str(upload)
            before = {
                "command": command,
                "loadavg": Path("/proc/loadavg").read_text(),
                "meminfo": Path("/proc/meminfo").read_text(),
                "cpu_stat": Path("/proc/stat").read_text(),
                "swap_counters": swap_counters(),
                "processes": capture("ps", "-eo", "comm,pcpu,rss", "--sort=-pcpu", source=source, supervisor=supervisor),
                "started": datetime.datetime.now(TIMEZONE).isoformat(),
            }
            (run / "invocation.json").write_text(json.dumps(before, indent=2))
            cancelled_signal = None
            with (run / "raptor.log").open("w") as log:
                try:
                    exit_code = run_workload(
                        command, environment, log, supervisor, source
                    )
                except WorkloadCancelled as error:
                    cancelled_signal = error.signum
                    exit_code = 128 + error.signum
            collect_artifacts(run, source)
            swap_after = swap_counters()
            runtime_changes = changed_artifacts(metadata["browsers"][label])
            binary_changed = bool(runtime_changes)
            swap_delta = {
                key: swap_after[key] - before["swap_counters"][key]
                for key in swap_after
            }
            (run / "exit.json").write_text(
                json.dumps(
                    {
                        "exit_code": exit_code,
                        "cancelled_signal": cancelled_signal,
                        "finished": datetime.datetime.now(TIMEZONE).isoformat(),
                        "loadavg": Path("/proc/loadavg").read_text(),
                        "cpu_stat": Path("/proc/stat").read_text(),
                        "swap_io_delta_pages": swap_delta,
                        "binary_changed": binary_changed,
                        "changed_artifacts": runtime_changes,
                    },
                    indent=2,
                )
            )
            if exit_code:
                raise SystemExit(f"Failed run retained at {run}")
            if binary_changed:
                raise SystemExit(f"Browser changed during this run: {run}")
            if any(swap_delta.values()):
                raise SystemExit(
                    f"Swap I/O contaminated this run; fix host conditions before repeating: {run}"
                )
            report = json.loads((run / "raptor.json").read_text())
            suites = [
                suite
                for suite in report["suites"]
                if suite["name"].startswith("speedometer3")
                and suite.get("unit") == "score"
            ]
            if (
                len(suites) != 1
                or not math.isfinite(suites[0]["value"])
                or suites[0]["value"] <= 0
            ):
                raise SystemExit(f"Missing/invalid Speedometer score: {run}")
            (run / "score.json").write_text(
                json.dumps(
                    {
                        "round": round_number + 1,
                        "browser": label,
                        "score": suites[0]["value"],
                        "suite": suites[0]["name"],
                    },
                    indent=2,
                )
            )
    print(output, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
