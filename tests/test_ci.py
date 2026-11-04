"""Задание CI: каталог проекта без --repo, база из переменных CI, артефакты каталогом."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import cli
from otk_1c.integrations import ci


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class CiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_checkout_dir(self):
        ws = self.tmp
        (ws / "otk-1c-pilot").mkdir()
        env = {"GITHUB_WORKSPACE": str(ws), "GITHUB_REPOSITORY": "pet/otk-1c-pilot"}
        self.assertEqual(ci.checkout_dir(env), str(ws / "otk-1c-pilot"))
        self.assertEqual(ci.checkout_dir({**env, "GITHUB_REPOSITORY": "pet/нет"}), ".")
        self.assertEqual(ci.checkout_dir({}), ".")

    def test_pr_target(self):
        self.assertEqual(ci.pr_target({"GITHUB_BASE_REF": "master"}), "master")
        self.assertEqual(ci.pr_target({"CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "develop"}), "develop")
        self.assertEqual(ci.pr_target({"CHANGE_TARGET": "main"}), "main")
        # push в GitHub Actions: переменная есть, но пустая
        self.assertIsNone(ci.pr_target({"GITHUB_BASE_REF": ""}))
        self.assertIsNone(ci.pr_target({}))

    def test_gate_outside_pr_needs_base(self):
        env = {k: v for k, v in os.environ.items() if k not in ci.TARGET_VARS}
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit) as e:
                cli.main(["ci", "gate"])
        self.assertEqual(e.exception.code, 2)
        self.assertIn("нужен --base", err.getvalue())

    def test_collect_artifacts(self):
        run, dest = self.tmp / "run", self.tmp / "out"
        (run / "logs").mkdir(parents=True)
        (run / "summary.md").write_text("сводка", encoding="utf-8")
        (run / "logs" / "designer-check.out").write_text("вывод", encoding="utf-8")
        dest.mkdir()
        (dest / "junit.xml").write_text("прошлый прогон", encoding="utf-8")
        n = ci.collect_artifacts([run / "summary.md", run / "logs" / "designer-check.out", run / "junit.xml"], dest)
        self.assertEqual(n, 2)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["designer-check.out", "summary.md"])
        self.assertEqual((dest / "summary.md").read_text(encoding="utf-8"), "сводка")

    def test_ensure_base_fetches_branch_and_history(self):
        origin = self.tmp / "origin"
        origin.mkdir()
        git(origin, "init", "-q", "-b", "master")
        for i in range(3):
            (origin / "f.txt").write_text(str(i), encoding="utf-8")
            git(origin, "add", "f.txt")
            git(origin, "commit", "-q", "-m", f"c{i}")
        git(origin, "checkout", "-q", "-b", "feature")
        (origin / "g.txt").write_text("правка", encoding="utf-8")
        git(origin, "add", "g.txt")
        git(origin, "commit", "-q", "-m", "feature")
        # как GitLab: одна ветка запроса, неполная история
        work = self.tmp / "work"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "--single-branch", "--branch", "feature",
                        origin.as_uri(), str(work)], check=True, capture_output=True)
        logs = []
        self.assertEqual(ci.ensure_base(work, "master", logs.append), "origin/master")
        self.assertEqual(git(work, "rev-parse", "--is-shallow-repository"), "false")
        self.assertEqual(git(work, "merge-base", "origin/master", "HEAD"), git(origin, "rev-parse", "master"))
        self.assertEqual(len(logs), 2)
        logs.clear()
        ci.ensure_base(work, "master", logs.append)     # всё на месте — ничего не догружает
        self.assertEqual(logs, [])


if __name__ == "__main__":
    unittest.main()
