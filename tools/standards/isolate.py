#!/usr/bin/env python3
"""Internal helper: bind private WPT hosts, enable loopback, then drop privileges."""
import argparse
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_text())
    if os.geteuid() != 0 or payload["uid"] == 0:
        parser.error("requires privileged namespace setup and a non-root test owner")
    namespaces = {name: os.readlink(f"/proc/self/ns/{name}") for name in ("net", "mnt")}
    if any(namespaces[name] == payload["parent_namespaces"][name] for name in namespaces):
        parser.error("private mount and network namespaces are required")
    subprocess.run(["mount", "--make-rprivate", "/"], check=True)
    subprocess.run(["mount", "--bind", payload["hosts"], "/etc/hosts"], check=True)
    subprocess.run(["ip", "link", "set", "lo", "up"], check=True)
    os.setgroups([])
    os.setgid(payload["gid"])
    os.setuid(payload["uid"])
    Path(payload["isolation_evidence"]).write_text(json.dumps({
        "namespaces": namespaces, "uid": os.getuid(), "gid": os.getgid(),
        "hosts": Path("/etc/hosts").read_text(),
    }, indent=2) + "\n")
    os.chdir(payload["cwd"])
    os.execve(payload["command"][0], payload["command"], payload["environment"])


if __name__ == "__main__":
    main()
