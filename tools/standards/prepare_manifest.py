#!/usr/bin/env python3
"""Generate a pinned WPT manifest and cache outside the source checkout."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess

from run import PIN, digest, git, outside_source, packages, validate_source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New runtime directory")
    parser.add_argument("--resource-lock", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve()
    validate_source(source)
    outside_source(source, output)
    outside_source(source, args.resource_lock.resolve())
    if output.exists():
        parser.error("output must be a new directory")
    with args.resource_lock.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        output.mkdir(parents=True)
        manifest = output / "MANIFEST.json"
        command = [str(args.python.absolute()), str(source / "testing/web-platform/tests/wpt"),
                   "--venv", str(args.python.absolute().parent.parent), "--skip-venv-setup",
                   "manifest", "--no-download", "--no-parallel", "--path", str(manifest),
                   "--cache-root", str(output / "cache")]
        environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(Path.home()),
                       "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1", "GIT_OPTIONAL_LOCKS": "0"}
        with (output / "manifest.log").open("w") as log:
            subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        provenance = {"wpt_tree": git(source, "rev-parse", "HEAD:testing/web-platform/tests"),
                      "wpt_upstream": PIN, "sha256": digest(manifest), "command": command,
                      "generator_sha256": digest(Path(__file__)), "python_sha256": digest(args.python),
                      "packages": packages(args.python.absolute(), environment)}
        manifest.with_suffix(".provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
        print(manifest)


if __name__ == "__main__":
    main()
