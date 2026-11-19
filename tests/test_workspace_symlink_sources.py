"""Каталог сценариев по ссылкам в выбранной Git-редакции."""
import os
import tempfile
import unittest
from pathlib import Path

from otk_1c import config
from otk_1c.gate import pipeline
from otk_1c.sources import gitdiff, workspace
from test_collect import BASE_MODULE, CONFIG, FLAGS, HEAD_MODULE, run, write


class TestScenarioSymlinks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="otk-scenario-links-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        run(self.root, "init", "-q", "-b", "master", self.repo.name)
        run(self.repo, "config", "user.email", "t@t")
        run(self.repo, "config", "user.name", "t")
        run(self.repo, "config", "core.symlinks", "false")
        self.source = "suite/cfe/Tests"
        write(self.repo, f"{self.source}/Configuration.xml", CONFIG)
        write(self.repo, f"{self.source}/CommonModules/М.xml", FLAGS)
        write(self.repo, f"{self.source}/CommonModules/М/Ext/Module.bsl", BASE_MODULE)
        run(self.repo, "add", "-A")
        run(self.repo, "commit", "-q", "-m", "base")

    def _link(self, name, value):
        blob = self.root / "link"
        blob.write_bytes(value.encode())
        oid = gitdiff.git(self.repo, "hash-object", "-w", str(blob)).strip()
        run(self.repo, "update-index", "--add", "--cacheinfo", f"120000,{oid},{name}")

    def _plan(self, path, run_dir, *, worktree=False):
        run_dir.mkdir()
        cfg = config._merge(config.load(self.repo), {"scenarios": {"path": path}})
        opts = pipeline.GateOptions("HEAD~1", head="HEAD", source=self.source, worktree=worktree)
        plan, _ = pipeline._plan_step(self.repo, cfg, run_dir, opts, pipeline.checks.Gate(cfg), None, {},
                                     lambda _: None)
        return plan

    def test_discovers_and_selects_scenarios_at_canonical_git_path(self):
        registration = ('Процедура ИсполняемыеСценарии() Экспорт\n'
                        'ЮТТесты.ДобавитьТест("Committed").Тег("М");\nКонецПроцедуры\n')
        tests = f"{self.source}/CommonModules/Tests/Ext/Module.bsl"
        write(self.repo, tests, registration)
        write(self.repo, f"{self.source}/CommonModules/М/Ext/Module.bsl", HEAD_MODULE)
        run(self.repo, "add", "-A")
        self._link("aliases/direct", "../suite/cfe")
        self._link("aliases/prefix", "../suite")
        self._link("aliases/chain", "prefix/cfe")
        run(self.repo, "commit", "-q", "-m", "scenarios")
        run(self.repo, "checkout-index", "--all", "--force")
        write(self.repo, tests, registration.replace("Committed", "Dirty"))

        paths = ["aliases/direct", "aliases/chain", "aliases/prefix/cfe", "aliases/direct/../cfe"]
        if os.name == "nt":
            paths.append("Aliases/Prefix/CfE")
        for index, path in enumerate(paths):
            for worktree, name in ((False, "Committed"), (True, "Dirty")):
                with self.subTest(path=path, worktree=worktree):
                    run_dir = self.root / f"run-{index}-{worktree}"
                    plan = self._plan(path, run_dir, worktree=worktree)
                    tree = run_dir / "trees" / plan["headSha"][:workspace.TREE_SHA]
                    self.assertEqual([ext["dir"] for ext in plan["scenarioExtensions"]], [self.source])
                    self.assertEqual([(test["name"], test["matched"]) for test in plan["scenarios"]], [(name, ["М"])])
                    self.assertIn(name, (tree / tests).read_text(encoding="utf-8-sig"))
                    self.assertFalse((tree / "aliases").exists())

    def test_missing_paths_and_dangling_targets_are_distinct_from_invalid_links(self):
        self._link("missing-target", "missing/suite")
        self._link("outer", "escape")
        self._link("escape", "../outside")
        self._link("cycle", "other")
        self._link("other", "cycle")
        run(self.repo, "commit", "-q", "-m", "invalid scenario links")

        for path in ("absent", "missing-target"):
            with self.subTest(path=path):
                plan = self._plan(path, self.root / path)
                self.assertEqual(plan["scenarioExtensions"], [])
                self.assertEqual(plan["scenarios"], [])
        errors = (("outer", "ссылка за пределы дерева"), ("escape/../suite/cfe", "ссылка за пределы дерева"),
                  ("cycle", "цикл ссылок"))
        for index, (path, error) in enumerate(errors):
            with self.subTest(path=path):
                run_dir = self.root / f"invalid-{index}"
                with self.assertRaisesRegex(gitdiff.GitError, error):
                    self._plan(path, run_dir)
                self.assertEqual(list(run_dir.glob("trees/*.done")), [])

    def test_resolves_relative_links_to_git_root(self):
        self._link("root", ".")
        self._link("nested/root", "..")
        run(self.repo, "commit", "-q", "-m", "root aliases")
        for path, expected in (("root", "."), ("nested/root", "."), ("nested/root/suite/cfe", "suite/cfe")):
            with self.subTest(path=path):
                self.assertEqual(workspace.git_tree_path(self.repo, "HEAD", path), expected)
