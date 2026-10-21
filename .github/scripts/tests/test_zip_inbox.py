"""Regression tests for the ZIP inbox processor.

Run from the repository root:
    python -m unittest discover -s .github/scripts/tests -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))

import zip_inbox  # noqa: E402


class ZipInboxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "_inbox").mkdir()
        (self.root / "_failed").mkdir()

    def make_zip(self, path, entries):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries:
                archive.writestr(name, data)
        return path

    def inbox_zip(self, name, entries):
        return self.make_zip(self.root / "_inbox" / name, entries)

    def test_inspect_name_accepts_repository_relative_path(self):
        path, is_dir, problems = zip_inbox.inspect_name("docs/PLAN.md")
        self.assertEqual(path, "docs/PLAN.md")
        self.assertFalse(is_dir)
        self.assertEqual(problems, [])

    def test_inspect_name_rejects_traversal(self):
        _, _, problems = zip_inbox.inspect_name("../outside.txt")
        self.assertTrue(any("traversal" in p for p in problems))

    def test_inspect_name_rejects_absolute_path(self):
        _, _, problems = zip_inbox.inspect_name("/tmp/outside.txt")
        self.assertIn("absolute path", problems)

    def test_inspect_name_rejects_windows_drive_path(self):
        _, _, problems = zip_inbox.inspect_name("C:/outside.txt")
        self.assertIn("absolute path", problems)

    def test_inspect_name_rejects_backslash(self):
        _, _, problems = zip_inbox.inspect_name("docs\\PLAN.md")
        self.assertIn("name contains a backslash", problems)

    def test_policy_rejects_unapproved_root_file(self):
        problems = zip_inbox.policy_problems("malware.py", False)
        self.assertTrue(any("root-level file is not on the allowlist" in p for p in problems))

    def test_policy_rejects_github_directory(self):
        problems = zip_inbox.policy_problems(".github/workflows/new.yml", False)
        self.assertTrue(any("not on the allowlist" in p for p in problems))

    def test_policy_rejects_secret_filename(self):
        problems = zip_inbox.policy_problems("docs/private.key", False)
        self.assertTrue(any("looks like a secret" in p for p in problems))

    def test_environment_template_is_allowed_by_secret_name_check(self):
        self.assertIsNone(zip_inbox.secret_name_problem(".env.example"))

    def test_valid_archive_is_extracted(self):
        archive = self.inbox_zip("docs-update.zip", [("docs/PLAN.md", "hello")])
        outcomes = zip_inbox.process_inbox(self.root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        target = self.root / "docs/PLAN.md"
        self.assertEqual(target.read_text(encoding="utf-8"), "hello")
        self.assertFalse(archive.exists())
        self.assertEqual([o.status for o in outcomes], ["processed"])
        self.assertEqual(outcomes[0].applied.added, ["docs/PLAN.md"])

    def test_existing_file_is_updated(self):
        target = self.root / "docs/PLAN.md"
        target.parent.mkdir()
        target.write_text("old", encoding="utf-8")
        self.inbox_zip("docs-update.zip", [("docs/PLAN.md", "new")])
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(target.read_text(encoding="utf-8"), "new")
        self.assertEqual(outcomes[0].applied.updated, ["docs/PLAN.md"])

    def test_unchanged_file_is_reported(self):
        target = self.root / "docs/PLAN.md"
        target.parent.mkdir()
        target.write_text("same", encoding="utf-8")
        self.inbox_zip("docs-update.zip", [("docs/PLAN.md", "same")])
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].applied.unchanged, ["docs/PLAN.md"])

    def test_rejected_archive_is_quarantined_with_report(self):
        archive = self.inbox_zip("bad-upload.zip", [("../escape.txt", "no")])
        outcomes = zip_inbox.process_inbox(
            self.root, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertFalse(archive.exists())
        self.assertTrue((self.root / outcomes[0].failed_zip).is_file())
        report = (self.root / outcomes[0].failed_report).read_text(encoding="utf-8")
        self.assertIn("nothing from this archive was extracted", report)
        self.assertFalse((self.root.parent / "escape.txt").exists())

    def test_bad_zip_name_is_quarantined(self):
        archive = self.inbox_zip("Bad Name.zip", [("docs/PLAN.md", "hello")])
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertFalse(archive.exists())
        self.assertTrue((self.root / outcomes[0].failed_report).is_file())
        self.assertFalse((self.root / "docs/PLAN.md").exists())

    def test_secret_like_content_is_rejected_without_leaking_value(self):
        fake_key = "sk-ant-" + ("A" * 24)
        self.inbox_zip("docs-update.zip", [("docs/example.md", "example " + fake_key)])
        outcomes = zip_inbox.process_inbox(
            self.root, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertFalse((self.root / "docs/example.md").exists())
        report = (self.root / outcomes[0].failed_report).read_text(encoding="utf-8")
        self.assertIn("Anthropic API key", report)
        self.assertNotIn(fake_key, report)

    def test_corrupt_archive_is_quarantined(self):
        path = self.root / "_inbox" / "corrupt.zip"
        path.write_bytes(b"this is not a zip archive")
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertTrue((self.root / outcomes[0].failed_report).is_file())

    def test_non_zip_inbox_file_is_ignored(self):
        note = self.root / "_inbox" / "notes.txt"
        note.write_text("keep me", encoding="utf-8")
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "ignored")
        self.assertTrue(note.exists())

    def test_inbox_readme_is_ignored(self):
        (self.root / "_inbox" / "README.md").write_text("instructions", encoding="utf-8")
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes, [])

    def test_empty_archive_is_rejected(self):
        path = self.root / "_inbox" / "empty.zip"
        with zipfile.ZipFile(path, "w"):
            pass
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertIn("empty", " ".join(outcomes[0].reasons).lower())

    def test_case_insensitive_duplicate_paths_are_rejected(self):
        self.inbox_zip("duplicate.zip", [("docs/Plan.md", "one"), ("docs/plan.md", "two")])
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertFalse((self.root / "docs/Plan.md").exists())
        self.assertFalse((self.root / "docs/plan.md").exists())

    def test_file_parent_conflict_is_rejected(self):
        self.inbox_zip("conflict.zip", [("docs", "file"), ("docs/PLAN.md", "child")])
        outcomes = zip_inbox.process_inbox(self.root)
        self.assertEqual(outcomes[0].status, "rejected")
        self.assertFalse((self.root / "docs").exists())

    def test_summary_mentions_processed_file(self):
        self.inbox_zip("docs-update.zip", [("docs/PLAN.md", "hello")])
        outcomes = zip_inbox.process_inbox(self.root)
        summary = zip_inbox.render_summary(outcomes)
        self.assertIn("docs-update.zip", summary)
        self.assertIn("docs/PLAN.md", summary)
        self.assertIn("processed", summary)

    def test_missing_inbox_returns_no_outcomes(self):
        (self.root / "_inbox").rmdir()
        self.assertEqual(zip_inbox.process_inbox(self.root), [])


if __name__ == "__main__":
    unittest.main()
