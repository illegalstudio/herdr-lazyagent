"""Exercise the interactive release command against local Git remotes."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release.sh"


class ReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix=".release-tests-", dir=ROOT)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        root = Path(self.temporary.name) / self._testMethodName
        root.mkdir()
        self.remote = root / "origin.git"
        self.repo = root / "checkout"
        self.git("init", "--bare", "--initial-branch=main", str(self.remote), cwd=root)
        self.git("init", "--initial-branch=main", str(self.repo), cwd=root)
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release-test@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.git("config", "tag.gpgsign", "false")
        self.git("config", "core.hooksPath", "/dev/null")
        (self.repo / "README.md").write_text("Test repository\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "test: initialize repository")
        self.git("remote", "add", "origin", str(self.remote))
        self.git("push", "--set-upstream", "origin", "main")

    def git(self, *args, cwd=None):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args], cwd=cwd or self.repo,
            text=True, capture_output=True, check=True,
        ).stdout.strip()

    def release(self, answers):
        return subprocess.run(
            ["bash", str(SCRIPT)], cwd=self.repo, input=answers,
            text=True, capture_output=True, timeout=15,
        )

    def test_first_release_pushes_an_annotated_tag_at_the_displayed_commit(self):
        head = self.git("rev-parse", "HEAD")
        result = self.release("\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Proposed next tag: v0.1.0", result.stdout)
        self.assertIn(head, result.stdout)
        self.assertEqual(self.git("cat-file", "-t", "refs/tags/v0.1.0"), "tag")
        self.assertEqual(self.git("rev-parse", "refs/tags/v0.1.0^{}", cwd=self.remote), head)
        self.assertEqual(self.git("rev-parse", "refs/heads/main", cwd=self.remote), head)

    def test_fetches_remote_tags_and_bumps_the_latest_stable_patch(self):
        self.git("tag", "v1.2.3")
        self.git("tag", "v2.0.0-rc.1")
        self.git("push", "--tags", "origin")
        self.git("tag", "-d", "v1.2.3")
        result = self.release("\ny\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Proposed next tag: v1.2.4", result.stdout)
        self.assertEqual(self.git("tag", "--list", "v1.2.4", cwd=self.remote), "v1.2.4")

    def test_accepts_a_custom_version_without_the_v(self):
        result = self.release("2.0.0\nyes\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("tag", "--list", "v2.0.0", cwd=self.remote), "v2.0.0")

    def test_confirmation_defaults_to_no(self):
        result = self.release("\n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Aborted.", result.stdout)
        self.assertEqual(self.git("tag", "--list"), "")
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")

    def test_rejects_uncommitted_changes(self):
        (self.repo / "README.md").write_text("Uncommitted change\n")
        result = self.release("\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("working tree is not clean", result.stderr)
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")

    def test_rejects_unpushed_commits(self):
        self.git("commit", "--allow-empty", "-m", "test: unpublished change")
        result = self.release("\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("main must match origin/main", result.stderr)
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")

    def test_rejects_a_feature_branch(self):
        self.git("checkout", "-b", "feature")
        result = self.release("\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("release from main", result.stderr)
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")

    def test_rejects_an_existing_tag(self):
        self.git("tag", "v1.0.0")
        self.git("push", "origin", "refs/tags/v1.0.0")
        result = self.release("v1.0.0\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tag v1.0.0 already exists", result.stderr)
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "v1.0.0")

    def test_rejects_a_non_semantic_version(self):
        result = self.release("v1.2.3-rc.1\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version must look like v1.2.3", result.stderr)
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")

    def test_failed_push_keeps_the_tag_and_prints_a_retry_command(self):
        hooks = self.remote / "hooks"
        self.git("config", "core.hooksPath", str(hooks), cwd=self.remote)
        hook = hooks / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        result = self.release("\ny\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git push origin refs/tags/v0.1.0", result.stderr)
        self.assertEqual(self.git("cat-file", "-t", "refs/tags/v0.1.0"), "tag")
        self.assertEqual(self.git("tag", "--list", cwd=self.remote), "")


if __name__ == "__main__":
    unittest.main()
