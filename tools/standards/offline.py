#!/usr/bin/env python3
"""Run pinned WPT with optional component installation declined."""
import builtins
from pathlib import Path
import sys


def configure(run, browser):
    def decline(self, component):
        print(f"Offline standards run: declining optional {component}", file=sys.stderr)
        return False

    def no_mojo(self, dest, browser_binary):
        print("Offline standards run: MojoJS installation omitted; MojoJS tests are outside this selection", file=sys.stderr)
        return None

    def unexpected_prompt(message):
        raise RuntimeError(f"Unexpected interactive setup request: {message}")

    run.BrowserSetup.prompt_install = decline
    browser.Chromium.install_mojojs = no_mojo
    builtins.input = unexpected_prompt


def read_only_manifest(manifest, tests_root, manifest_path, url_base, **kwargs):
    if kwargs.get("update", True):
        raise ValueError("Offline runner refuses manifest updates")
    loaded = manifest._load(manifest.get_logger(), tests_root, manifest_path,
                            types=kwargs.get("types"), allow_cached=False)
    if loaded is None or loaded.url_base != url_base:
        raise ValueError("Manifest cannot be reused without rebuilding")
    return loaded


def validate_artifact(tests_root, path):
    sys.path.insert(0, str(Path(tests_root)))
    from tools import localpaths
    from manifest import manifest
    loaded = read_only_manifest(manifest, tests_root, path, "/", update=False)
    for entry in loaded:
        pass


def main():
    if sys.argv[1] == "--validate-manifest":
        validate_artifact(sys.argv[2], sys.argv[3])
        return
    tests_root = Path(sys.argv.pop(1)).resolve(strict=True)
    sys.path.insert(0, str(tests_root))
    from tools.wpt import wpt
    from tools.wpt import run, browser
    configure(run, browser)
    from manifest import manifest
    manifest.load_and_update = lambda tests_root, manifest_path, url_base, **kwargs: read_only_manifest(
        manifest, tests_root, manifest_path, url_base, **kwargs)
    wpt.main()


if __name__ == "__main__":
    main()
