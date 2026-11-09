#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Run complete Speedometer 3 suites serially in isolated Raptor profiles."""

import argparse
import datetime
import fcntl
import hashlib
import json
import math
import os
import platform
import shutil
import subprocess
from pathlib import Path

from zoneinfo import ZoneInfo

SOURCE = Path(__file__).resolve().parents[3]
TIMEZONE = ZoneInfo("Europe/Berlin")


def capture(*command):
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_identity(path):
    status = path.stat()
    return [status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns]


def browser_metadata(binary):
    result = {
        "path": str(binary),
        "version": capture(str(binary), "--version"),
        "sha256": digest(binary),
        "file_identity": file_identity(binary),
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
            if artifact.suffix == ".ini":
                result[name]["content"] = artifact.read_text()
    source_record = binary.parent.parent / "source-revision.txt"
    if source_record.is_file():
        result["source_revision_external"] = source_record.read_text().strip()
    return result


def swap_counters():
    return {
        key: int(value)
        for key, value in (
            line.split() for line in Path("/proc/vmstat").read_text().splitlines()
        )
        if key in ("pswpin", "pswpout")
    }


def collect_artifacts(run):
    work = SOURCE / "testing/mozharness/build"
    shared_upload = work / "blobber_upload_dir"
    if shared_upload.exists():
        shutil.move(str(shared_upload), str(run / "artifacts/mozharness"))
    for report in work.glob("raptor*.json"):
        shutil.move(str(report), str(run / report.name))
    screenshots = work / "screenshots.html"
    if screenshots.exists():
        shutil.move(str(screenshots), str(run / screenshots.name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    with (root / "resource.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (
            subprocess.run(
                ["tmux", "has-session", "-t", "firefox-build"],
                capture_output=True,
                check=False,
            ).returncode
            == 0
        ):
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
            SOURCE / "testing/raptor/raptor/tests/benchmarks/speedometer-desktop.toml"
        )
        metadata = {
            "checkout_revision": capture("git", "-C", str(SOURCE), "rev-parse", "HEAD"),
            "source_diff": capture("git", "-C", str(SOURCE), "diff", "--stat"),
            "host": platform.uname()._asdict(),
            "cpu": Path("/proc/cpuinfo").read_text(),
            "manifest_sha256": digest(manifest),
            "benchmark": {
                name: (
                    SOURCE / "third_party/webkit/PerformanceTests/Speedometer3" / name
                ).read_text()
                for name in ("package.json", "moz.yaml")
            },
            "mozconfig": (SOURCE / "tools/performance/baseline/mozconfig").read_text(),
            "rounds": args.rounds,
            "profile_only": args.profile,
            "browsers": {
                label: browser_metadata(binary) for label, _, binary in browsers
            },
            "chromedriver": capture(str(args.chromedriver), "--version"),
            "geckodriver": capture(
                str(
                    SOURCE
                    / "tools/browsertime/node_modules/@sitespeed.io/geckodriver/vendor/geckodriver"
                ),
                "--version",
            ),
            "toolchain": {
                name: capture(str(root / "state" / path), "--version")
                for name, path in {
                    "clang": "clang/bin/clang",
                    "rustc": "rustc/bin/rustc",
                    "cargo": "rustc/bin/cargo",
                    "node": "node/bin/node",
                }.items()
            },
            "harness_sha256": {
                name: digest(SOURCE / "tools/performance/baseline" / name)
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
                    SOURCE / "tools/browsertime/node_modules/browsertime/package.json"
                ).read_text()
            ),
        }
        harness_snapshot = output / "harness"
        harness_snapshot.mkdir()
        for name in metadata["harness_sha256"]:
            shutil.copyfile(
                SOURCE / "tools/performance/baseline" / name, harness_snapshot / name
            )
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
            "MOZCONFIG": str(SOURCE / "tools/performance/baseline/mozconfig"),
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
                shared_work = SOURCE / "testing/mozharness/build"
                if (shared_work / "blobber_upload_dir").exists() or (
                    shared_work / "raptor.json"
                ).exists():
                    raise SystemExit(
                        "Uncollected mozharness output exists; preserve it before starting another run"
                    )
                if (
                    file_identity(binary)
                    != metadata["browsers"][label]["file_identity"]
                ):
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
                    str(SOURCE / "mach"),
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
                        SOURCE
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
                    "processes": capture("ps", "-eo", "comm,pcpu,rss", "--sort=-pcpu"),
                    "started": datetime.datetime.now(TIMEZONE).isoformat(),
                }
                (run / "invocation.json").write_text(json.dumps(before, indent=2))
                with (run / "raptor.log").open("w") as log:
                    with subprocess.Popen(
                        command,
                        cwd=SOURCE,
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                    ) as result:
                        for line in result.stdout:
                            if (
                                "'env':" in line
                                or '"env":' in line
                                or "Using env:" in line
                            ):
                                log.write("[harness environment dump omitted]\n")
                            else:
                                log.write(line)
                            log.flush()
                        result.wait()
                collect_artifacts(run)
                swap_after = swap_counters()
                binary_changed = (
                    file_identity(binary)
                    != metadata["browsers"][label]["file_identity"]
                )
                swap_delta = {
                    key: swap_after[key] - before["swap_counters"][key]
                    for key in swap_after
                }
                (run / "exit.json").write_text(
                    json.dumps(
                        {
                            "exit_code": result.returncode,
                            "finished": datetime.datetime.now(TIMEZONE).isoformat(),
                            "loadavg": Path("/proc/loadavg").read_text(),
                            "cpu_stat": Path("/proc/stat").read_text(),
                            "swap_io_delta_pages": swap_delta,
                            "binary_changed": binary_changed,
                        },
                        indent=2,
                    )
                )
                if result.returncode:
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
        print(output)


if __name__ == "__main__":
    main()
