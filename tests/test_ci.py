"""Задание CI: каталог проекта без --repo, база из переменных CI, артефакты каталогом."""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import cli
from otk_1c.integrations import ci
from otk_1c.sources import workspace


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


class CiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_parallel_gate_publications_keep_complete_run_sets(self):
        directories = [self.tmp / "runs" / name for name in ("ra", "rb")]
        for directory in directories:
            directory.mkdir(parents=True)
            for name in ci.GATE_ARTIFACTS:
                (directory / name).write_text(directory.name, encoding="utf-8")
        destination = self.tmp / "artifacts"
        previous = destination / "previous"
        previous.mkdir(parents=True)
        (previous / "summary.json").write_text("previous", encoding="utf-8")
        legacy = ci.PRIVATE_ARTIFACTS | set(ci.GATE_ARTIFACTS)
        untouched = ("custom.log", *ci.DESIGNER_ARTIFACTS)
        for name in legacy:
            (destination / name).write_text("legacy", encoding="utf-8")
        for name in untouched:
            (destination / name).write_text("unrelated", encoding="utf-8")

        publish = """
import sys
import time
from pathlib import Path
from otk_1c.integrations import ci

directory, destination = map(Path, sys.argv[1:])
copy = ci.shutil.copyfile
def checked_copy(source, target):
    assert not any((destination / name).exists() for name in ci.PRIVATE_ARTIFACTS | set(ci.GATE_ARTIFACTS))
    return copy(source, target)

ci.shutil.copyfile = checked_copy
(directory / "ready").touch()
deadline = time.monotonic() + 10
while len(list(directory.parent.glob("*/ready"))) < 2:
    if time.monotonic() > deadline:
        sys.exit("parallel publisher did not start")
    time.sleep(0.01)
ci._publish("otk-gate", directory, ci.GATE_ARTIFACTS, print, destination)
"""
        env = {**os.environ, "PYTHONPATH": str(Path(ci.__file__).parents[2]), "PYTHONIOENCODING": "utf-8"}
        processes = [subprocess.Popen([sys.executable, "-c", publish, str(directory), str(destination)],
                                      env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, encoding="utf-8") for directory in directories]
        try:
            for process in processes:
                log, _ = process.communicate(timeout=20)
                self.assertEqual(process.returncode, 0, log)
                self.assertNotIn("не удалась", log)
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.communicate()
        self.assertFalse(any((destination / name).exists() for name in legacy))
        for name in untouched:
            self.assertEqual((destination / name).read_text(encoding="utf-8"), "unrelated")
        self.assertEqual((previous / "summary.json").read_text(encoding="utf-8"), "previous")
        for directory in directories:
            published = destination / directory.name
            self.assertEqual({path.name for path in published.iterdir()}, set(ci.GATE_ARTIFACTS))
            self.assertTrue(all(path.read_text(encoding="utf-8") == directory.name
                                for path in published.iterdir()))

    def test_gate_publication_cannot_modify_another_run(self):
        root = self.tmp / "runs"
        source, prior = root / "rcurrent", root / "rprevious"
        source.mkdir(parents=True)
        (source / "summary.json").write_text("current", encoding="utf-8")
        prior.mkdir()
        for name in ("otk.json", "static.json", "run.json", ".completed", "summary.json"):
            (prior / name).write_text("previous", encoding="utf-8")
        before = {path.name: path.read_bytes() for path in prior.iterdir()}
        for destination in (prior, prior / "child"):
            with self.subTest(destination=destination):
                messages = []
                ci._publish("otk-gate", source, ci.GATE_ARTIFACTS, messages.append, destination)
                self.assertIn("пересекается с каталогом прогона", messages[0])
                self.assertEqual({path.name: path.read_bytes() for path in prior.iterdir()}, before)

    def test_gate_publication_accepts_reports_sibling_of_run(self):
        """Каталог reports рядом с r-прогонами не считается другим прогоном."""
        root = self.tmp / "runs"
        source = root / "rcurrent"
        source.mkdir(parents=True)
        (source / "summary.json").write_text("current", encoding="utf-8")
        reports = root / "reports"
        for _run in range(2):
            messages = []
            ci._publish("otk-gate", source, ci.GATE_ARTIFACTS, messages.append, reports)
            self.assertFalse(any("не удалась" in message for message in messages), messages)
            self.assertEqual((reports / source.name / "summary.json").read_text(encoding="utf-8"), "current")

    def test_gate_publication_protects_empty_neighbor_run(self):
        """Даже ещё не начавшийся соседний прогон нельзя использовать для артефактов."""
        neighbor = workspace.new_run({"environment": {"runDir": str(self.tmp / "runs")}}, self.tmp)
        source = neighbor.parent / "rcurrent"
        source.mkdir(exist_ok=True)
        (source / "summary.json").write_text("current", encoding="utf-8")
        messages = []
        ci._publish("otk-gate", source, ci.GATE_ARTIFACTS, messages.append, neighbor)
        self.assertIn("пересекается с каталогом прогона", messages[0])
        self.assertEqual({p.name for p in neighbor.iterdir()}, {".otk-run"})

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
        (dest / "otk.json").write_text("полный план", encoding="utf-8")
        (dest / "designer-check.out").write_text("сырой лог", encoding="utf-8")
        n = ci.collect_artifacts([run / "summary.md", run / "junit.xml"], dest, run)
        self.assertEqual(n, 1)
        self.assertEqual(sorted(p.name for p in dest.iterdir()), ["summary.md"])
        self.assertEqual((dest / "summary.md").read_text(encoding="utf-8"), "сводка")

    def test_artifact_allowlist_excludes_internal_files(self):
        self.assertEqual(ci.GATE_ARTIFACTS, ("summary.html", "summary.md", "summary.json", "junit.xml",
                                             "problems.txt", "summary.sarif"))
        self.assertEqual(ci.DESIGNER_ARTIFACTS, ("designer-check.md", "junit-designer-check.xml"))
        self.assertNotIn("otk.json", ci.GATE_ARTIFACTS)
        self.assertNotIn("designer.json", ci.GATE_ARTIFACTS)

    def test_artifact_destination_cannot_overlap_run_dir(self):
        run = self.tmp / "run"
        run.mkdir()
        summary = run / "summary.md"
        summary.write_text("сводка", encoding="utf-8")
        plan = run / "otk.json"
        plan.write_text("план", encoding="utf-8")
        for dest in (run, run / "artifacts", self.tmp):
            with self.subTest(dest=dest), self.assertRaisesRegex(ValueError, "пересекается"):
                ci.collect_artifacts([summary], dest, run)
        with self.assertRaisesRegex(ValueError, "пересекается"):
            ci.collect_artifacts([run / "logs" / "nested.txt"], run / "artifacts", run)
        self.assertEqual(summary.read_text(encoding="utf-8"), "сводка")
        self.assertEqual(plan.read_text(encoding="utf-8"), "план")

    def test_artifact_symlink_cannot_overwrite_run_file(self):
        run, dest = self.tmp / "run", self.tmp / "out"
        run.mkdir()
        dest.mkdir()
        source = run / "summary.md"
        source.write_text("new summary", encoding="utf-8")
        plan = run / "otk.json"
        plan.write_text("keep plan", encoding="utf-8")
        symlink_target = dest / "summary.md"
        with mock.patch.object(Path, "is_symlink", lambda path: path == symlink_target), \
                self.assertRaisesRegex(ValueError, "символической ссылкой"):
            ci.collect_artifacts([source], dest, run)
        self.assertEqual(plan.read_text(encoding="utf-8"), "keep plan")

    def test_collect_artifacts_removes_stale_public_mode(self):
        run, dest = self.tmp / "run", self.tmp / "out"
        run.mkdir()
        dest.mkdir()
        for name in ci.GATE_ARTIFACTS:
            (dest / name).write_text("old gate", encoding="utf-8")
        designer = run / "designer-check.md"
        junit = run / "junit-designer-check.xml"
        designer.write_text("new designer", encoding="utf-8")
        junit.write_text("new junit", encoding="utf-8")
        self.assertEqual(ci.collect_artifacts([designer, junit], dest, run), 2)
        self.assertEqual(sorted(path.name for path in dest.iterdir()),
                         ["designer-check.md", "junit-designer-check.xml"])

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
