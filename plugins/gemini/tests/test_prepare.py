"""Snapshot preparation tests; no model or network access is required."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "prepare.py"
SPEC = importlib.util.spec_from_file_location("gemini_prepare", MODULE_PATH)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Snapshot Test")
        self.git("config", "user.email", "snapshot@example.invalid")
        self.git("config", "core.autocrlf", "false")

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "commit.gpgsign=false", "-C", str(self.repo), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def commit(self, message="test commit"):
        self.git("add", "--all")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    @staticmethod
    def text(snapshot):
        return "\n".join(unit["text"] for unit in snapshot["units"])

    def test_working_tree_captures_staged_unstaged_and_untracked(self):
        self.write("staged.py", "old staged\n")
        self.write("unstaged.py", "old unstaged\n")
        self.write("both.py", "base\n")
        self.write(".gitignore", "ignored.txt\n")
        self.commit()
        self.write("staged.py", "new staged\n")
        self.write("both.py", "index change\n")
        self.git("add", "staged.py", "both.py")
        self.write("both.py", "index change\nworktree change\n")
        self.write("unstaged.py", "new unstaged\n")
        self.write("new.py", "untracked content\n")
        self.write("ignored.txt", "ignored content\n")
        status_before = self.git("status", "--porcelain=v1")

        result = prepare.capture_review(self.repo)

        self.assertEqual(result["files"], ["both.py", "new.py", "staged.py", "unstaged.py"])
        for content in ("new staged", "new unstaged", "index change", "worktree change", "untracked content"):
            self.assertIn(content, self.text(result))
        self.assertEqual(status_before, self.git("status", "--porcelain=v1"))

    def test_base_uses_merge_base_and_excludes_working_tree(self):
        self.write("common.txt", "common\n")
        root = self.commit()
        self.git("checkout", "-b", "feature")
        self.write("feature.txt", "committed feature\n")
        feature = self.commit()
        self.git("checkout", "main")
        self.write("main-only.txt", "main changes after branch\n")
        self.commit()
        self.git("checkout", "feature")
        self.write("feature.txt", "worktree-only content\n")
        self.write("untracked.txt", "untracked content\n")

        result = prepare.capture_review(self.repo, base="main")

        self.assertEqual(result["files"], ["feature.txt"])
        self.assertIn(root, result["scope"])
        self.assertEqual(result["head"], feature)
        self.assertIn("committed feature", self.text(result))
        self.assertNotIn("worktree-only", self.text(result))
        self.assertNotIn("untracked content", self.text(result))

    def test_commit_uses_first_parent_and_excludes_later_changes(self):
        self.write("target.txt", "before\n")
        self.commit()
        self.write("target.txt", "target version\n")
        target = self.commit()
        self.write("later.txt", "later commit\n")
        self.commit()
        self.write("target.txt", "uncommitted version\n")

        result = prepare.capture_review(self.repo, commit=target)

        self.assertEqual(result["files"], ["target.txt"])
        self.assertIn("-before", self.text(result))
        self.assertIn("+target version", self.text(result))
        self.assertNotIn("uncommitted version", self.text(result))

    def test_root_commit_includes_added_files(self):
        self.write("root.txt", "root content\n")
        root = self.commit()
        self.write("root.txt", "working change\n")
        result = prepare.capture_review(self.repo, commit=root)
        self.assertEqual(result["files"], ["root.txt"])
        self.assertIn("old:- new:1 +root content", self.text(result))
        self.assertNotIn("working change", self.text(result))

    def test_unborn_repo_captures_index_and_working_files(self):
        self.write("staged.txt", "index text\n")
        self.git("add", "staged.txt")
        self.write("staged.txt", "current working text\n")
        self.write("new.txt", "new text\n")
        result = prepare.capture_review(self.repo)
        self.assertIsNone(result["head"])
        self.assertEqual(result["files"], ["new.txt", "staged.txt"])
        self.assertIn("current working text", self.text(result))
        self.assertNotIn("index text", self.text(result))
        with self.assertRaisesRegex(ValueError, "committed HEAD"):
            prepare.capture_review(self.repo, base="main")

    def test_path_filter_is_literal_even_with_git_pathspec_syntax(self):
        self.write("baseline.txt", "baseline\n")
        self.commit()
        for name in ("[a]*.txt", "a-match.txt", ":(glob)*.txt"):
            self.write(name, name + "\n")
        for name in ("[a]*.txt", ":(glob)*.txt"):
            with self.subTest(name=name):
                result = prepare.capture_review(self.repo, paths=[name])
                self.assertEqual(result["files"], [name])
                self.assertEqual([f["path"] for f in result["captured"]], [name])
        for bad in ("../outside.txt", str(self.repo / "baseline.txt")):
            with self.assertRaisesRegex(ValueError, "repository-relative"):
                prepare.capture_review(self.repo, paths=[bad])

    def test_clean_repo_has_no_units(self):
        self.write("unchanged.txt", "unchanged\n")
        self.commit()
        result = prepare.capture_review(self.repo)
        self.assertEqual(result["files"], [])
        self.assertEqual(result["units"], [])
        self.assertEqual(result["omitted"], [])

    def test_binary_secret_and_untracked_symlink_are_omitted(self):
        self.write("tracked.bin", b"before\x00binary")
        self.write("baseline.txt", "baseline\n")
        self.commit()
        self.write("tracked.bin", b"after\x00binary")
        self.write("new.bin", b"\xff\x00not text")
        self.write(".env.local", "TOKEN=secret-marker\n")
        self.write("secrets/password.txt", "secret-marker\n")
        external = self.repo.parent / (self.repo.name + "-external.txt")
        external.write_text("external-secret-marker\n", encoding="utf-8")
        self.addCleanup(external.unlink)
        (self.repo / "link.txt").symlink_to(external)

        result = prepare.capture_review(self.repo)

        self.assertEqual(result["captured"], [])
        self.assertEqual(result["units"], [])
        self.assertEqual(len(result["omitted"]), 5)
        self.assertIn("untracked symlink", "\n".join(result["omitted"]))
        self.assertNotIn("secret-marker", self.text(result))

    def test_tracked_symlink_diff_does_not_follow_target(self):
        (self.repo / "link.txt").symlink_to("old-target.txt")
        self.commit()
        (self.repo / "link.txt").unlink()
        self.write("new-target.txt", "target-contents-must-not-be-read\n")
        (self.repo / "link.txt").symlink_to("new-target.txt")
        result = prepare.capture_review(self.repo, paths=["link.txt"])
        self.assertIn("+new-target.txt", self.text(result))
        self.assertNotIn("target-contents-must-not-be-read", self.text(result))

    def test_numbered_patch_retains_line_numbers_across_batch_splits(self):
        patch = "\n".join([
            "--- a/example.py", "+++ b/example.py", "@@ -40,4 +50,5 @@ function",
            " context", "-removed", "+added", "+extra", " after", " final",
            "@@ -90 +100 @@", "-last old", "+last new",
        ])
        rows = prepare.numbered_patch(patch)
        expected = [
            "old:40 new:50  context", "old:41 new:- -removed", "old:- new:51 +added",
            "old:- new:52 +extra", "old:42 new:53  after", "old:43 new:54  final",
            "old:90 new:- -last old", "old:- new:100 +last new",
        ]
        units = prepare.make_units("example.py", rows, limit=90)
        self.assertGreater(len(units), 1)
        combined = "\n".join(unit["text"] for unit in units)
        for line in expected:
            self.assertEqual(combined.count(line), 1)
        self.assertTrue(all(unit["text"].startswith('FILE "example.py"\n') for unit in units))

    def test_audit_captures_multiple_numbered_documents(self):
        first = self.write("plan.md", "# Plan\nFirst requirement\n")
        second = self.write("spec.md", "# Spec\nSecond requirement\n")
        result = prepare.capture_audit(self.repo, [first, second], "plan")
        self.assertEqual(result["kind"], "plan")
        self.assertEqual([f["path"] for f in result["captured"]], [str(first), str(second)])
        self.assertIn("line:2 First requirement", self.text(result))
        self.assertIn("line:2 Second requirement", self.text(result))
        self.assertIn(str(first), result["protected_paths"])
        self.assertIn(str(second), result["protected_paths"])
        self.assertEqual(result["snapshot_sha256"], prepare.capture_audit(self.repo, [first, second], "plan")["snapshot_sha256"])
        first.write_text("changed\n", encoding="utf-8")
        self.assertNotEqual(result["snapshot_sha256"], prepare.capture_audit(self.repo, [first, second], "plan")["snapshot_sha256"])

    def test_audit_rejects_empty_binary_and_secret_documents(self):
        for name, content, message in (
            ("empty.md", "  \n\t", "Empty or binary"),
            ("binary.md", "a\x00b", "Empty or binary"),
            (".env", "secret", "Credential-like"),
        ):
            with self.subTest(name=name):
                document = self.write(name, content)
                with self.assertRaisesRegex(ValueError, message):
                    prepare.capture_audit(self.repo, [document], "spec")

    def test_audit_protects_worktree_git_dir_and_shared_metadata(self):
        self.write("plan.md", "# Plan\nReview linked worktree metadata protection.\n")
        self.commit()
        with tempfile.TemporaryDirectory() as linked_temp:
            linked = Path(linked_temp).resolve() / "audit-worktree"
            self.git("worktree", "add", "-b", "audit-worktree", str(linked))
            git_dir = self.repo / ".git" / "worktrees" / "audit-worktree"
            self.assertTrue(git_dir.is_dir())

            result = prepare.capture_audit(linked, [linked / "plan.md"], "plan")

            self.assertIn(str(linked), result["protected_paths"])
            self.assertIn(str(git_dir), result["protected_paths"])
            self.assertIn(str(self.repo / ".git"), result["protected_paths"])
            self.assertIn("line:2 Review linked worktree", self.text(result))

    def test_audit_accepts_non_git_directory(self):
        with tempfile.TemporaryDirectory() as plain_temp:
            plain = Path(plain_temp).resolve()
            document = plain / "spec.md"
            document.write_text("# Plain document\n", encoding="utf-8")

            result = prepare.capture_audit(plain, [document], "spec")

            self.assertEqual(result["protected_paths"], [str(plain), str(document)])
            self.assertIn("line:1 # Plain document", self.text(result))


if __name__ == "__main__":
    unittest.main()
