#!/usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Test pinned extraction evidence without browsers or platform custody."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run as runner


def receipt_for(source):
    return {
        "status": "PASS", "revision": "9" * 40, "tree": "7" * 40,
        "archiveSHA256": "2" * 64, "catalogSHA256": "3" * 64,
        "extraction": {
            "status": "PASS", "source_revision": "9" * 40,
            "base_tree": "7" * 40, "canonical_source": str(source),
            "verified_files": 483823, "base_catalog_digest": "1" * 64,
            "extracted_identity_digest": "1" * 64, "cpp_sha256": "4" * 64,
            "overlay": None,
            "boundary": "full role/operator-boundary verification, not atomic execution attestation",
        },
    }


def write_receipt(base, source, receipt=None, raw=None):
    path = base / "source-identity.json"
    raw = raw if raw is not None else json.dumps(receipt or receipt_for(source)).encode()
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest()


class SourceReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base / "source"
        self.source.mkdir()
        (self.source / "mach").touch()
        config = self.source / "tools/performance/baseline/mozconfig"
        config.parent.mkdir(parents=True)
        config.touch()

    def test_catalog_no_git_and_source_symlink_resolution(self):
        path, sha = write_receipt(self.base, self.source)
        alias = self.base / "alias"
        alias.symlink_to(self.source, target_is_directory=True)
        with patch.object(runner, "capture", side_effect=AssertionError("Git forbidden")):
            result = runner.source_identity(runner.validate_source(alias), path, sha)
        self.assertIsNone(result["checkout_revision"])
        self.assertIsNone(result["source_diff"])
        self.assertEqual(result["kind"], "catalog-extraction")
        self.assertEqual(result["receipt_sha256"], sha)
        self.assertEqual(result["initial_extraction"]["revision"], "9" * 40)

    def test_git_default_requires_real_top_level(self):
        with self.assertRaises(subprocess.CalledProcessError):
            runner.source_identity(self.source)
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture", "--allow-empty"], check=True)
        expected = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        result = runner.source_identity(self.source)
        self.assertEqual(result["checkout_revision"], expected)
        self.assertEqual(result["source_diff"], "")
        child = self.source / "nested"
        child.mkdir()
        with self.assertRaisesRegex(ValueError, "top-level"):
            runner.source_identity(child)

    def test_bad_receipt_fields_fail_closed(self):
        changes = [
            ("status", "FAIL"), ("revision", "bad"), ("tree", "g" * 40),
            ("archiveSHA256", "bad"), ("catalogSHA256", None),
            ("extraction.status", "FAIL"), ("extraction.source_revision", "8" * 40),
            ("extraction.base_tree", "8" * 40),
            ("extraction.canonical_source", str(self.base)),
            ("extraction.verified_files", True), ("extraction.verified_files", 1.5),
            ("extraction.verified_files", 0),
            ("extraction.base_catalog_digest", "2" * 64),
            ("extraction.cpp_sha256", "bad"), ("extraction.extracted_identity_digest", None),
            ("extraction.overlay", {}), ("extraction.boundary", "atomic"),
        ]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                receipt = receipt_for(self.source)
                obj = receipt
                keys = field.split(".")
                for key in keys[:-1]:
                    obj = obj[key]
                obj[keys[-1]] = value
                path, sha = write_receipt(self.base, self.source, receipt)
                with self.assertRaises(ValueError):
                    runner.source_identity(self.source, path, sha)

    def test_hash_json_and_paired_flags(self):
        path, sha = write_receipt(self.base, self.source)
        for receipt_path, expected in ((path, None), (None, sha), (path, "0" * 64), (path, "bad")):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                runner.source_identity(self.source, receipt_path, expected)
        for raw in (b"{", b'[]', b'{"status":"PASS","status":"FAIL"}', b'{"extraction":{"status":"PASS","status":"PASS"}}'):
            path, sha = write_receipt(self.base, self.source, raw=raw)
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                runner.source_identity(self.source, path, sha)
        for flags in (["--source-receipt", str(path)], ["--source-receipt-sha256", sha]):
            with patch.object(sys, "argv", ["run.py", "--root", str(self.base / "out"), "--official", "/control", "--source-build", "/candidate", *flags]), patch.object(runner.lifecycle, "run_guarded") as guarded:
                with self.assertRaises(SystemExit) as error:
                    runner.main()
                self.assertEqual(error.exception.code, 2)
                guarded.assert_not_called()

    def test_unsafe_receipt_files(self):
        path, sha = write_receipt(self.base, self.source)
        link = self.base / "link"
        link.symlink_to(path)
        fifo = self.base / "fifo"
        os.mkfifo(fifo)
        large = self.base / "large"
        large.write_bytes(b" " * 65537)
        for selected in (link, fifo, large, self.base):
            with self.subTest(selected=selected), self.assertRaises((ValueError, OSError)):
                runner.source_identity(self.source, selected, sha)

    def test_changed_fd_or_replaced_path_rejected(self):
        original_fstat = os.fstat
        for replace in (False, True):
            path, sha = write_receipt(self.base, self.source)
            calls = 0
            def fstat(fd):
                nonlocal calls
                calls += 1
                if calls == 2:
                    if replace:
                        other = self.base / "replacement"
                        other.write_bytes(path.read_bytes())
                        other.replace(path)
                    else:
                        path.write_bytes(b"changed")
                return original_fstat(fd)
            with self.subTest(replace=replace), patch.object(runner.os, "fstat", side_effect=fstat):
                with self.assertRaisesRegex(ValueError, "changed while reading"):
                    runner.source_identity(self.source, path, sha)


if __name__ == "__main__":
    unittest.main()
