#!/usr/bin/env python3
"""Plan or execute the source tree's pinned WPT runner; keep artifacts outside Git."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
import subprocess
import sys

PIN = "0364154f9175aa87045a6647869641901b2efceb"
HERE = Path(__file__).resolve().parent


def git(source, *args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def packages(python, environment):
    command = [str(python), "-c", "import importlib.metadata as m, json; print(json.dumps(sorted((d.metadata['Name'], d.version) for d in m.distributions())))"]
    return json.loads(subprocess.check_output(command, text=True, env=environment, stdin=subprocess.DEVNULL))


def outside_source(source, path):
    if path == source or source in path.parents:
        raise ValueError(f"runtime path must be outside source: {path}")


def validate_source(source):
    wpt = source / "testing/web-platform"
    if (wpt / "meta/mozilla-sync").read_text().strip() != "upstream: " + PIN:
        raise ValueError("WPT pin differs; review selection and update pin explicitly")
    if git(source, "status", "--porcelain", "--", "testing/web-platform", "testing/profiles"):
        raise ValueError("WPT/profile source has uncommitted changes")


def validate_manifest(path, tree):
    provenance = json.loads(path.with_suffix(".provenance.json").read_text())
    if provenance["wpt_tree"] != tree or provenance["wpt_upstream"] != PIN:
        raise ValueError("manifest belongs to a different WPT tree")
    if digest(path) != provenance["sha256"]:
        raise ValueError("manifest hash differs from its provenance")
    return provenance


def selection_paths(selection, tests_root):
    tests = [line.strip() for line in selection.read_text().splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    if not tests or len(tests) != len(set(tests)):
        raise ValueError("selection must be nonempty with unique source paths")
    for test in tests:
        relative = Path(test)
        if relative.is_absolute() or ".." in relative.parts or not (tests_root / relative).is_file():
            raise ValueError(f"selection must name WPT source files: {test}")
    return tests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path, help="WPT virtualenv Python")
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--product", choices=["firefox", "chromium"], required=True)
    parser.add_argument("--label", required=True, help="e.g. upstream, fork, chromium")
    parser.add_argument("--output", required=True, type=Path, help="New directory outside source")
    parser.add_argument("--selection", type=Path, default=HERE / "smoke.txt")
    parser.add_argument("--metadata", choices=["mozilla", "empty"], required=True)
    parser.add_argument("--webdriver", type=Path, required=True)
    parser.add_argument("--certutil", type=Path)
    parser.add_argument("--component", type=Path, action="append", default=[], help="Additional engine/library binary to hash")
    parser.add_argument("--binary-source-revision", help="Source revision from the tested runtime build record; omitted means unknown")
    parser.add_argument("--build-config", type=Path, help="Optional mozconfig; record hash only")
    parser.add_argument("--manifest", type=Path, required=True, help="Prepared MANIFEST.json with adjacent .provenance.json")
    parser.add_argument("--tmp-root", type=Path, default=Path("/tmp"), help="Short private runtime directory parent")
    parser.add_argument("--resource-lock", type=Path, required=True, help="Shared failfast compute lock")
    parser.add_argument("--build-mode", choices=["noninstrumented-release"], required=True, help="Explicit runtime build declaration; other modes are not supported by this bounded wrapper")
    parser.add_argument("--execute", action="store_true", help="Requires agreed idle CPU slot")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    wpt = source / "testing/web-platform"
    output = args.output.resolve()
    if output == source or source in output.parents:
        parser.error("output must be outside source")
    if output.exists():
        parser.error("output must be a new directory")
    validate_source(source)
    outside_source(source, args.manifest.resolve(strict=True))
    outside_source(source, args.resource_lock.resolve())
    outside_source(source, args.tmp_root.resolve(strict=True))
    if len(str(args.tmp_root.resolve())) > 20:
        parser.error("tmp-root must be short (at most 20 characters) for Unix sockets")
    manifest_provenance = validate_manifest(args.manifest, git(source, "rev-parse", "HEAD:testing/web-platform/tests"))
    if args.product == "chromium" and (not args.webdriver or args.metadata != "empty"):
        parser.error("Chromium requires --webdriver and --metadata empty")
    if args.product == "firefox" and not args.certutil:
        parser.error("Firefox requires --certutil for the WPT test CA")
    tests = selection_paths(args.selection, wpt / "tests")
    binary = args.binary.resolve(strict=True)
    args.python.resolve(strict=True)
    # Preserve the venv launcher path; resolving its symlink loses venv discovery.
    python = args.python.absolute()
    args.webdriver = args.webdriver.resolve(strict=True)
    if args.certutil:
        args.certutil = args.certutil.resolve(strict=True)
    for tool in (args.webdriver, args.certutil, args.build_config, *args.component):
        if tool and not tool.is_file():
            parser.error(f"tool missing: {tool}")
    output.mkdir(parents=True)
    (output / "empty-metadata").mkdir()
    (output / "run-info").mkdir()
    (output / "run-info/mozinfo.json").write_text(json.dumps({
        "release_or_beta": True, "nightly_build": False, "debug": False,
        "ccov": False, "asan": False, "tsan": False, "ubsan": False,
    }))
    metadata = wpt / "meta" if args.metadata == "mozilla" else output / "empty-metadata"
    cmd = [str(python), str(HERE / "offline.py"), str(wpt / "tests"), "--venv", str(python.parent.parent),
           "--skip-venv-setup", "run", "--channel=stable", "--browser-channel=stable",
           "--binary", str(binary), "--headless", "--processes=1", "--no-pause-after-test",
           "--no-install-fonts", "--no-manifest-update", "--no-manifest-download", "--retry-unexpected=0", "--metadata", str(metadata),
           "--manifest", str(args.manifest.resolve()),
           "--run-info", str(output / "run-info"),
           "--log-wptreport", str(output / "wptreport.json"),
           "--log-raw", str(output / "raw.jsonl")]
    if args.product == "firefox":
        cmd += ["--certutil-binary", str(args.certutil), "--prefs-root", str(source / "testing/profiles")]
    if args.webdriver:
        cmd += ["--webdriver-binary", str(args.webdriver)]
    cmd += [args.product, *tests]
    evidence = {
        "label": args.label, "source_head": git(source, "rev-parse", "HEAD"),
        "source_head_scope": "harness/test checkout; NOT a claim about the tested binary source revision",
        "binary_source_revision": args.binary_source_revision,
        "binary_source_revision_scope": "operator declaration from external build record; not inferred from harness HEAD",
        "declared_binary_build_mode": args.build_mode,
        "wpt_upstream": PIN, "wpt_tree": git(source, "rev-parse", "HEAD:testing/web-platform/tests"),
        "profiles_tree": git(source, "rev-parse", "HEAD:testing/profiles"),
        "metadata_tree": git(source, "rev-parse", "HEAD:testing/web-platform/meta"),
        "manifest_provenance": manifest_provenance,
        "manifest_path": str(args.manifest.resolve()),
        "run_info_assumption": "noninstrumented stable release; confirm runtime build before interpreting expectations",
        "configuration_note": "WPT metadata and automation prefs apply; empty metadata is not stock-default exposure",
        "optional_components": "installation declined; MojoJS and downloaded OpenH264 coverage is unrun",
        "metadata_mode": args.metadata, "selection": tests,
        "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
        "binary": str(binary), "command": cmd, "executed": False,
        "environment": {key: os.environ.get(key) for key in ("DISPLAY", "LANG", "TZ")},
        "system": list(os.uname()),
        "created_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if args.build_config:
        evidence["build_config"] = {
            "path": str(args.build_config.absolute()),
            "sha256": hashlib.file_digest(args.build_config.open("rb"), "sha256").hexdigest(),
        }
    evidence_path = output / "invocation.json"
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
    if args.execute:
        if os.getuid() == 0:
            parser.error("tests must run as a non-root owner")
        lock = args.resource_lock.open("a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = Path(tempfile.mkdtemp(prefix="wpt-", dir=args.tmp_root.resolve()))
        environment = {"HOME": str(Path.home()), "PATH": "/usr/local/bin:/usr/bin:/bin",
                       "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "Europe/Berlin",
                       "PYTHONDONTWRITEBYTECODE": "1", "GIT_OPTIONAL_LOCKS": "0",
                       "MOZ_REMOTE_ALLOW_SYSTEM_ACCESS": "1"}
        for key, name in (("TMPDIR", "tmp"), ("XDG_CACHE_HOME", "cache"), ("XDG_RUNTIME_DIR", "xdg")):
            (runtime / name).mkdir(mode=0o700)
            environment[key] = str(runtime / name)
        evidence["environment"] = environment
        evidence["runtime_directory"] = str(runtime)
        try:
            evidence["binary_sha256"] = hashlib.file_digest(binary.open("rb"), "sha256").hexdigest()
            evidence["binary_version"] = subprocess.check_output([str(binary), "--version"], text=True, env=environment, stdin=subprocess.DEVNULL).strip()
            for name in ("application.ini", "platform.ini", "libxul.so"):
                component = binary.parent / name
                if component.is_file():
                    with component.open("rb") as stream:
                        evidence.setdefault("components_sha256", {})[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            for component in args.component:
                with component.open("rb") as stream:
                    evidence.setdefault("components_sha256", {})[str(component.absolute())] = hashlib.file_digest(stream, "sha256").hexdigest()
            for tool_name, tool in (("python", python), ("webdriver", args.webdriver), ("certutil", args.certutil)):
                if tool:
                    with tool.open("rb") as stream:
                        evidence.setdefault("tools_sha256", {})[tool_name] = hashlib.file_digest(stream, "sha256").hexdigest()
            evidence["python_packages"] = packages(python, environment)
            evidence["harness_sha256"] = {name: digest(HERE / name) for name in ("run.py", "offline.py", "isolate.py", "prepare_manifest.py")}
            hosts_command = [str(python), str(wpt / "tests/wpt"), "--venv", str(python.parent.parent),
                             "--skip-venv-setup", "make-hosts-file"]
            hosts = output / "hosts"
            hosts.write_text(subprocess.check_output(hosts_command, cwd=wpt / "tests", env=environment, stdin=subprocess.DEVNULL, text=True))
            payload = {"command": cmd, "cwd": str(wpt / "tests"), "hosts": str(hosts),
                       "uid": os.getuid(), "gid": os.getgid(), "environment": environment,
                       "isolation_evidence": str(output / "isolation.json"),
                       "parent_namespaces": {name: os.readlink(f"/proc/self/ns/{name}") for name in ("net", "mnt")}}
            payload_path = output / "isolation-payload.json"
            payload_path.write_text(json.dumps(payload, indent=2) + "\n")
            isolated_command = ["sudo", "-n", "unshare", "--mount", "--net", "--propagation", "private",
                                str(python), str(HERE / "isolate.py"), str(payload_path)]
            evidence["isolation_command"] = isolated_command
            evidence["executed"] = True
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
            with (output / "runner.log").open("w") as log:
                result = subprocess.run(isolated_command, cwd=wpt / "tests", env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            evidence["runner_exit"] = result.returncode
        except BaseException as error:
            evidence["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            evidence["runtime_cleanup"] = "retained for explicit owner cleanup"
            evidence_path.write_text(json.dumps(evidence, indent=2) + "\n")
            lock.close()
    print(output / "invocation.json")
    return evidence.get("runner_exit", 0)


if __name__ == "__main__":
    sys.exit(main())
