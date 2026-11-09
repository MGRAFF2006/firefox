import builtins
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import offline
from run import PIN, digest, outside_source, selection_paths, validate_manifest


class PreparationContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_stale_tree_or_tampered_manifest_is_rejected(self):
        manifest = self.root / "MANIFEST.json"
        manifest.write_text('{"version": 9}')
        sidecar = manifest.with_suffix(".provenance.json")
        sidecar.write_text(json.dumps({"wpt_tree": "tree1", "wpt_upstream": PIN, "sha256": digest(manifest)}))
        self.assertEqual(validate_manifest(manifest, "tree1")["wpt_tree"], "tree1")
        with self.assertRaisesRegex(ValueError, "different WPT tree"):
            validate_manifest(manifest, "tree2")
        manifest.write_text('{"version": 8}')
        with self.assertRaisesRegex(ValueError, "hash differs"):
            validate_manifest(manifest, "tree1")

    def test_selection_cannot_escape_test_tree_or_duplicate_outcomes(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "good.html").write_text("test fixture")
        (self.root / "outside.html").write_text("outside")
        selection = self.root / "selection.txt"
        selection.write_text("# comment\ngood.html\n")
        self.assertEqual(selection_paths(selection, tests), ["good.html"])
        for content in ("../outside.html", str(self.root / "outside.html"), "good.html\ngood.html", "missing.html", ""):
            with self.subTest(content=content):
                selection.write_text(content)
                with self.assertRaises(ValueError):
                    selection_paths(selection, tests)

    def test_runtime_paths_cannot_write_into_checkout(self):
        for path in (self.root, self.root / "metadata/cache"):
            with self.assertRaisesRegex(ValueError, "outside source"):
                outside_source(self.root, path)
        outside_source(self.root, self.root.parent / "runtime")

    def test_optional_installations_declined_and_unknown_prompt_fails_closed(self):
        setup = type("Setup", (), {})
        chromium = type("Chromium", (), {})
        run = types.SimpleNamespace(BrowserSetup=setup)
        browser = types.SimpleNamespace(Chromium=chromium)
        with patch.object(builtins, "input"):
            offline.configure(run, browser)
            self.assertFalse(setup().prompt_install("OpenH264"))
            self.assertIsNone(chromium().install_mojojs("dest", "binary"))
            with self.assertRaisesRegex(RuntimeError, "Unexpected interactive"):
                builtins.input("new installer prompt")


if __name__ == "__main__":
    unittest.main()
