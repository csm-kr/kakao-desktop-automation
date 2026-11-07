import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bootstrap = load("bootstrap_kmsg")
verification = load("verify_delivery")


def git(directory, *arguments):
    return subprocess.check_output(["git", "-C", str(directory), *arguments], text=True).strip()


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / "origin"
        self.origin.mkdir()
        git(self.origin, "init", "--quiet")
        git(self.origin, "config", "user.name", "Fixture")
        git(self.origin, "config", "user.email", "fixture@example.invalid")
        (self.origin / ".gitignore").write_text(".build/\n")
        (self.origin / "sender.txt").write_text("original\n")
        (self.origin / "AGENTS.md").write_text("fixture repository instructions\n")
        (self.origin / "CLAUDE.md").symlink_to("AGENTS.md")
        git(self.origin, "add", ".")
        git(self.origin, "commit", "--quiet", "-m", "fixture")
        self.commit = git(self.origin, "rev-parse", "HEAD")
        (self.origin / "sender.txt").write_text("adapted\n")
        self.package = self.root / "package"
        (self.package / "patches").mkdir(parents=True)
        (self.package / "scripts").mkdir()
        (self.package / "patches/kmsg-foreground.patch").write_text(git(self.origin, "diff") + "\n")
        (self.package / "scripts/probe.swift").write_text("fixture probe source\n")
        (self.package / "scripts/collect.swift").write_text("fixture collector source\n")
        self.project = self.root / "project"
        self.builds = []
        self.patches = [patch.object(bootstrap, "UPSTREAM_URL", str(self.origin)),
                        patch.object(bootstrap, "UPSTREAM_COMMIT", self.commit),
                        patch.object(bootstrap, "PACKAGE", self.package),
                        patch.object(bootstrap.sys, "platform", "darwin"),
                        patch.object(bootstrap.shutil, "which", return_value="fixture-tool"),
                        patch.object(bootstrap, "build", side_effect=self.fake_build)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def fake_build(self, directory, probe_source):
        self.builds.append(directory)
        binary_directory = directory / ".build/release"
        binary_directory.mkdir(parents=True)
        for name in ["kmsg", "kakao-probe", "kakao-collect"]:
            path = binary_directory / name
            path.write_text("fixture binary - never runs UI\n")

    def test_fixed_source_patch_and_cached_reuse(self):
        first = bootstrap.bootstrap(self.project)
        self.assertEqual(first["status"], "built")
        checkout = Path(first["kmsg"]).parents[2]
        self.assertEqual(git(checkout, "rev-parse", "HEAD"), self.commit)
        self.assertEqual((checkout / "sender.txt").read_text(), "adapted\n")
        self.assertTrue((checkout / "CLAUDE.md").is_symlink())
        second = bootstrap.bootstrap(self.project)
        self.assertEqual(second["status"], "reused")
        self.assertEqual(len(self.builds), 1)

    def test_unowned_existing_checkout_is_preserved(self):
        directory = self.project / ".tools/kmsg-foreground"
        directory.mkdir(parents=True)
        sentinel = directory / "important.txt"
        sentinel.write_text("keep")
        with self.assertRaisesRegex(RuntimeError, "no matching install receipt"):
            bootstrap.bootstrap(self.project)
        self.assertEqual(sentinel.read_text(), "keep")
        self.assertEqual(self.builds, [])

    def test_modified_source_is_not_overwritten(self):
        first = bootstrap.bootstrap(self.project)
        source = Path(first["kmsg"]).parents[2] / "sender.txt"
        source.write_text("user change")
        with self.assertRaisesRegex(RuntimeError, "source was changed"):
            bootstrap.bootstrap(self.project)
        self.assertEqual(source.read_text(), "user change")
        self.assertEqual(len(self.builds), 1)

    def test_build_failure_has_no_partial_checkout_or_receipt(self):
        with patch.object(bootstrap, "build", side_effect=RuntimeError("fixture failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture failure"):
                bootstrap.bootstrap(self.project)
        tools = self.project / ".tools"
        self.assertFalse((tools / "kmsg-foreground").exists())
        self.assertFalse((tools / "kmsg-foreground.install.json").exists())
        self.assertEqual(list(tools.glob(".kmsg-build-*")), [])

    def test_patch_failure_has_no_partial_checkout(self):
        (self.package / "patches/kmsg-foreground.patch").write_text("not a patch\n")
        with self.assertRaisesRegex(RuntimeError, "command failed"):
            bootstrap.bootstrap(self.project)
        self.assertFalse((self.project / ".tools/kmsg-foreground").exists())
        self.assertEqual(self.builds, [])

    def test_windows_rejected_before_creating_directory(self):
        with patch.object(bootstrap.sys, "platform", "win32"):
            with self.assertRaisesRegex(RuntimeError, "only on macOS"):
                bootstrap.bootstrap(self.project)
        self.assertFalse(self.project.exists())

    def test_changed_cached_executable_is_not_reused(self):
        first = bootstrap.bootstrap(self.project)
        Path(first["kmsg"]).write_text("different binary")
        with self.assertRaisesRegex(RuntimeError, "executable is missing or changed"):
            bootstrap.bootstrap(self.project)
        self.assertEqual(len(self.builds), 1)

    def test_changed_collector_is_not_reused(self):
        first = bootstrap.bootstrap(self.project)
        Path(first["collector"]).write_text("changed collector executable")
        with self.assertRaisesRegex(RuntimeError, "collector executable is missing or changed"):
            bootstrap.bootstrap(self.project)
        self.assertEqual(len(self.builds), 1)

    def test_symlink_outside_checkout_is_rejected(self):
        first = bootstrap.bootstrap(self.project)
        checkout = Path(first["kmsg"]).parents[2]
        (checkout / "CLAUDE.md").unlink()
        (checkout / "CLAUDE.md").symlink_to(self.package / "scripts/probe.swift")
        with self.assertRaisesRegex(RuntimeError, "inside its checkout"):
            bootstrap.bootstrap(self.project)


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.before = {"ready": True, "exact_window_count": 1, "pid": 123,
                       "target_title": "Example chat", "message_sha256": hashlib.sha256(b"fixture").hexdigest(),
                       "scan_limit": 80, "composer_empty": True, "matching_outgoing_count": 2}
        self.after = {**self.before, "matching_outgoing_count": 3}

    def test_one_new_ui_message_only(self):
        result = verification.verify(self.before, self.after)
        self.assertEqual(result["status"], "confirmed_in_ui")
        self.assertIsNone(result["native_message_id"])
        self.assertFalse(result["automatic_retry_allowed"])
        self.assertFalse(result["recipient_read_verified"])

    def test_session_state_can_invalidate_an_otherwise_matching_receipt(self):
        for flags in [{"CGSSessionScreenIsLocked": True}, {"kCGSessionLoginDoneKey": False},
                      {"kCGSSessionOnConsoleKey": False}, {"CGSSessionScreenIsLocked": "true"}, None]:
            with self.subTest(flags=flags):
                after = {**self.after, "session_flags": flags}
                self.assertEqual(verification.verify(self.before, after)["status"], "unconfirmed")
        # Previously observed unlocked sessions omitted the lock key.
        observed = {**self.after, "session_flags": {"kCGSessionLoginDoneKey": True,
                                                   "kCGSSessionOnConsoleKey": True}}
        self.assertEqual(verification.verify(self.before, observed)["status"], "confirmed_in_ui")

    def test_missing_duplicate_window_or_stale_message_never_passes(self):
        changes = [{"ready": False}, {"exact_window_count": 0}, {"exact_window_count": 2},
                   {"pid": 124}, {"target_title": "Different chat"}, {"message_sha256": "a" * 64},
                   {"matching_outgoing_count": 2}, {"matching_outgoing_count": 4},
                   {"matching_outgoing_count": True}, {"composer_empty": False}, {"scan_limit": 20}]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(verification.verify(self.before, {**self.after, **change})["status"], "unconfirmed")

    def test_nonempty_existing_draft_cannot_be_verified(self):
        self.assertEqual(verification.verify({**self.before, "composer_empty": False}, self.after)["status"], "unconfirmed")

    def test_invalid_identity_metadata_rejected(self):
        for change in [{"pid": True}, {"pid": -1}, {"message_sha256": ""}, {"target_title": ""}, {"scan_limit": False}]:
            with self.subTest(change=change):
                self.assertEqual(verification.verify({**self.before, **change}, {**self.after, **change})["status"], "unconfirmed")


if __name__ == "__main__":
    unittest.main()
