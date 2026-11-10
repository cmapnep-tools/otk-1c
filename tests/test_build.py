"""Сборка: ошибки файлов выгрузки при загрузке — находки, а не сбой инфраструктуры."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import schema_check
from _fs import rmtree_force
from otk_1c import cli, config
from otk_1c.probes import build
from otk_1c.sources import workspace

DUMP = "D:/runs/trees/abc/src/cf"
IBCMD = (f"[ERROR] Файл - {DUMP}/Catalogs/К/Forms/ФормаСписка/Ext/Form.xml: Неверный путь к данным: \"Список.Ref\".\n"
         f"[ERROR] Файл - {DUMP}/Catalogs/К/Forms/ФормаСписка/Ext/Form.xml: Неверный путь к данным: \"Список.Ref\".\n"
         "[ERROR] Импорт конфигурации из XML завершен с ошибкой\n")
DESIGNER = (f"Файл - {DUMP.replace('/', chr(92))}\\Documents\\Д\\Forms\\Ф\\Ext\\Form.xml: "
            "Неверный путь к данным: \"Объект.Х\".\n")


class BuildTest(unittest.TestCase):
    def test_source_errors(self):
        self.assertEqual(build.source_errors(IBCMD, DUMP),
                         [("Catalogs/К/Forms/ФормаСписка/Ext/Form.xml", 'Неверный путь к данным: "Список.Ref".')])
        self.assertEqual(build.source_errors(DESIGNER, DUMP),
                         [("Documents/Д/Forms/Ф/Ext/Form.xml", 'Неверный путь к данным: "Объект.Х".')])
        self.assertEqual(build.source_errors("Загрузка невозможна: редактирование запрещено!", DUMP), [])


class StateTest(unittest.TestCase):
    """Состояние сборки — рядом с копией ИБ."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-state-"))
        self.ib, self.run = self.tmp / "ib", self.tmp / "runs" / "repo-abc123"
        self.ib.mkdir()
        (self.ib / "1Cv8.1CD").write_bytes(b"x")

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_state_next_to_ib_shared_by_run_dirs(self):
        build.save_state(self.run, {"ib": str(self.ib), "commit": "a"}, self.ib)
        self.assertTrue((self.ib / build.STATE_FILE).exists())
        other_run = self.tmp / "runs" / "repo-def456"             # другой клон с той же копией ИБ
        self.assertEqual(build.load_state(other_run, self.ib)["commit"], "a")

    def test_recreated_ib_is_unknown(self):
        build.save_state(self.run, {"ib": str(self.ib), "commit": "a"}, self.ib)
        p = self.ib / build.STATE_FILE
        state = json.loads(p.read_text(encoding="utf-8"))
        p.write_text(json.dumps({**state, "ibCreated": state["ibCreated"] - 100}), encoding="utf-8")
        self.assertIsNone(build.load_state(self.run, self.ib))

    def test_no_ib_dir_uses_run_dir(self):
        build.save_state(self.run, {"commit": "a"}, None)
        self.assertTrue((self.run / build.RUN_STATE).exists())
        self.assertEqual(build.load_state(self.run, None)["commit"], "a")


class EnsureIbTest(unittest.TestCase):
    """Своей копии ИБ нет — сборка создаёт пустую файловую ИБ, загрузка полная."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-ensure-"))
        self.ib = self.tmp / "ib"
        self.created = []

        def create(path):
            self.created.append(path)
            path.mkdir(parents=True, exist_ok=True)
            (path / "1Cv8.1CD").write_bytes(b"x")
            return 0, ""
        self.platform = SimpleNamespace(ib=self.ib, connection=None, create_file_ib=create, lock=lambda: None)

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_missing_or_empty_dir_created(self):
        self.assertTrue(build.ensure_ib(self.platform))
        self.assertEqual(self.created, [self.ib])
        self.assertFalse(build.ensure_ib(self.platform))           # уже есть — не трогается
        shutil.rmtree(self.ib)
        self.ib.mkdir()
        self.assertTrue(build.ensure_ib(self.platform))           # пустой каталог

    def test_foreign_dir_and_extension_refused(self):
        self.ib.mkdir()
        (self.ib / "чужое.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(build.InfraError):
            build.ensure_ib(self.platform)
        with self.assertRaises(build.InfraError):
            build.ensure_ib(SimpleNamespace(ib=self.tmp / "new", connection=None), extension="Расш")
        self.assertEqual(self.created, [])

    def test_connection_never_created(self):
        self.assertFalse(build.ensure_ib(SimpleNamespace(ib=self.ib, connection="/F" + str(self.ib))))
        self.assertFalse(self.ib.exists())

    def test_build_after_create_is_full(self):
        run = self.tmp / "runs" / "repo-1"
        run.mkdir(parents=True)                                   # прежнее состояние в каталоге прогона
        (run / build.RUN_STATE).write_text(json.dumps({"ib": str(self.ib), "commit": "old", "tree": "t0"}),
                                              encoding="utf-8")
        loads = []
        with mock.patch("otk_1c.probes.build.tree_hash", return_value="t1"), \
                mock.patch("otk_1c.probes.build._load", lambda p, r, d, files,
                           *a: loads.append(files) or {"action": "full"}):
            res = build.build(self.tmp, "new", "src/cf", self.tmp, self.platform, run)
        self.assertEqual(loads, [None])
        self.assertTrue(res["created"])

    def test_lost_commit_is_full(self):
        """Коммита прошлой загрузки нет (пакет controls вне веток собран git gc) — полная загрузка, а не сбой."""
        (self.ib / "1Cv8.1CD").parent.mkdir(parents=True, exist_ok=True)
        (self.ib / "1Cv8.1CD").write_bytes(b"x")
        build.save_state(self.tmp / "run", {"ib": str(self.ib), "commit": "2b937af287e9", "tree": "t0"}, self.ib)
        loads = []
        with mock.patch("otk_1c.probes.build.tree_hash", return_value="t1"), \
                mock.patch("otk_1c.probes.build._commit_exists", return_value=False), \
                mock.patch("otk_1c.probes.build._git_changes", side_effect=AssertionError("не строить разницу")), \
                mock.patch("otk_1c.probes.build._load", lambda p, r, d, files,
                           *a: loads.append(files) or {"action": "full"}):
            res = build.build(self.tmp, "new", "src/cf", self.tmp, self.platform, self.tmp / "run")
        self.assertEqual(loads, [None])
        self.assertIn("2b937af287 в репозитории нет", res["action"])

    def test_commit_exists(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "x"], check=True)
        sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        self.assertTrue(build._commit_exists(repo, sha))
        self.assertFalse(build._commit_exists(repo, "2b937af287e9c779e0a91291eb165eee9d26583b"))


class PlanFromStateTest(unittest.TestCase):
    """Способ загрузки по состоянию копии ИБ — один расчёт у build и build --dry-run."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-planstate-"))
        self.ib = self.tmp / "ib"
        self.ib.mkdir()

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_no_state_is_full(self):
        self.assertEqual(build.plan_from_state(self.tmp, "src/cf", self.tmp, None, "new", self.ib),
                         (None, None, False))

    def test_partial_by_commit(self):
        state = {"ib": str(self.ib), "commit": "a" * 40, "tree": "t0"}
        with mock.patch.object(build, "_commit_exists", return_value=True), \
                mock.patch.object(build, "_git_changes",
                                  return_value=[("M", "CommonModules/М/Ext/Module.bsl")]):
            files, modules, lost = build.plan_from_state(self.tmp, "src/cf", self.tmp, state, "b" * 40, self.ib)
        self.assertEqual(files, ["CommonModules/М/Ext/Module.bsl"])
        self.assertIsNone(modules)
        self.assertFalse(lost)

    def test_lost_commit_is_full(self):
        state = {"ib": str(self.ib), "commit": "a" * 40, "tree": "t0"}
        with mock.patch.object(build, "_commit_exists", return_value=False), \
                mock.patch.object(build, "_git_changes", side_effect=AssertionError("не строить разницу")):
            self.assertEqual(build.plan_from_state(self.tmp, "src/cf", self.tmp, state, "b" * 40, self.ib),
                             (None, None, True))


class CmdBuildTest(unittest.TestCase):
    """build собирает HEAD (или --head), а не редакцию последнего плана в каталоге прогона."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-cmdbuild-"))
        self.repo = self.tmp / "repo"
        (self.repo / "src" / "cf").mkdir(parents=True)

    def tearDown(self):
        rmtree_force(self.tmp)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def test_head_not_stale_plan(self):
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "src" / "cf" / "a.txt").write_text("1", encoding="utf-8")
        (self.repo / "otk.toml").write_text(f'[environment]\nrunDir = "{(self.tmp / "runs").as_posix()}"\n',
                                                 encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        first = self.git("rev-parse", "HEAD")
        (self.repo / "src" / "cf" / "a.txt").write_text("2", encoding="utf-8")
        self.git("commit", "-qam", "b")
        second = self.git("rev-parse", "HEAD")
        run_dir = workspace.run_dir(config.load(self.repo), self.repo)
        run_dir.mkdir(parents=True)
        stale = run_dir / "otk.json"                          # план прогона другой редакции
        stale.write_text(json.dumps({"headSha": first, "source": "src/cf"}), encoding="utf-8")
        calls = []
        args = {"repo": str(self.repo), "plan": None, "head": None, "full": False, "assume_current": False}
        with mock.patch("otk_1c.probes.build.build", lambda r, head, *a, **k: calls.append(head) or {"action": "x"}), \
                mock.patch("otk_1c.cli.Platform"):
            cli.cmd_build(SimpleNamespace(**args))
            cli.cmd_build(SimpleNamespace(**{**args, "head": first}))
            cli.cmd_build(SimpleNamespace(**{**args, "plan": str(stale)}))
        self.assertEqual(calls, [second, first, first])
        other = workspace.run_dir(config.load(self.repo), self.tmp / "other" / "repo")
        self.assertNotEqual(run_dir, other)                        # одноимённые каталоги — разные прогоны
        self.assertEqual(run_dir.name.split("-")[0], other.name.split("-")[0])

    def test_json_result(self):
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "src" / "cf" / "a.txt").write_text("1", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("otk_1c.probes.build.build", return_value={"action": "full"}), \
                mock.patch("otk_1c.cli.Platform"), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["build", "--repo", str(self.repo), "--json"])
        result = json.loads(out.getvalue())
        self.assertEqual(schema_check.errors(result, schema_check.load("build-result")), [])
        self.assertEqual((code, result["status"], result["result"]["build"]), (0, "ok", {"action": "full"}))
        self.assertIn("сборка:", err.getvalue())

    def test_json_load_error_redacts_stderr_and_payload(self):
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps({"headDir": str(self.tmp / "dump"), "source": "src/cf"}),
                             encoding="utf-8")
        cfg = {"project": {"source": "src/cf"}, "environment": {}}
        args = SimpleNamespace(repo=str(self.repo), plan=str(plan_path), head=None, full=False, assume_current=False,
                               json=True, dry_run=False)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "_project", return_value=self.repo), \
                mock.patch.object(cli, "_cfg", return_value=cfg), \
                mock.patch.object(cli.workspace, "run_dir", return_value=self.tmp / "runs"), \
                mock.patch.object(cli.workspace, "plan_root", return_value=self.tmp / "dump"), \
                mock.patch.object(cli.config, "detect_extension"), \
                mock.patch.object(cli.build, "build_dirs",
                                  side_effect=build.LoadError([("Form.xml", 'Pwd="secret"')])), \
                mock.patch.object(cli, "Platform"), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.cmd_build(args)
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertNotIn("secret", err.getvalue())
        self.assertNotIn("secret", json.dumps(result))
        self.assertIn("[скрыто]", err.getvalue())

    def test_dry_run_does_not_build_or_write_state(self):
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "src" / "cf" / "a.txt").write_text("1", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("otk_1c.probes.build.build", side_effect=AssertionError("build called")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["build", "--repo", str(self.repo), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn('"action": "full"', out.getvalue())
        self.assertIn("План сборки", out.getvalue())
        self.assertEqual(err.getvalue(), "")
        self.assertFalse((self.repo / "build-state.json").exists())

    def test_dry_run_reads_state_from_connection_ib(self):
        """--connection /F<каталог>: состояние копии — там же, где у обычной сборки (otk-state.json в каталоге ИБ),
        иначе dry-run показывал бы загрузку, когда сборка вернула бы up-to-date."""
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "src" / "cf" / "a.txt").write_text("1", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        head = self.git("rev-parse", "HEAD")
        ib = self.tmp / "ib"
        ib.mkdir()
        (ib / "1Cv8.1CD").write_bytes(b"x")
        run_dir = self.tmp / "runs"
        build.save_state(run_dir, {"ib": str(ib), "commit": head, "tree": build.tree_hash(self.repo, head, "src/cf")},
                         ib)
        cfg = {"project": {"source": "src/cf"}, "environment": {"connection": "/F" + str(ib)}}
        args = SimpleNamespace(repo=str(self.repo), plan=None, head=None, full=False, assume_current=False,
                               json=False, dry_run=True)
        out = io.StringIO()
        with mock.patch.object(cli, "_project", return_value=self.repo), \
                mock.patch.object(cli, "_cfg", return_value=cfg), \
                mock.patch.object(cli.workspace, "run_dir", return_value=run_dir), \
                mock.patch.object(cli.config, "detect_extension"), \
                mock.patch.object(cli.build, "build", side_effect=AssertionError("build called")), \
                contextlib.redirect_stdout(out):
            code = cli.cmd_build(args)
        self.assertEqual(code, 0)
        self.assertIn('"action": "up-to-date"', out.getvalue())

    def test_dry_run_json_keeps_revision_out_of_build(self):
        """Конверт build --json --dry-run: revision и origin — на верхнем уровне, в build — только план шага."""
        self.git("init", "-q", "-b", "master")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "src" / "cf" / "a.txt").write_text("1", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "a")
        out = io.StringIO()
        with mock.patch("otk_1c.probes.build.build", side_effect=AssertionError("build called")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(["build", "--repo", str(self.repo), "--dry-run", "--json"])
        result = json.loads(out.getvalue())
        self.assertEqual(schema_check.errors(result, schema_check.load("build-result")), [])
        self.assertEqual(code, 0)
        plan = result["result"]
        self.assertTrue(plan["build"]["dryRun"])
        self.assertEqual(plan["build"]["action"], "full")
        self.assertNotIn("revision", plan["build"])
        self.assertNotIn("origin", plan["build"])
        self.assertEqual(plan["revision"], self.git("rev-parse", "HEAD"))

    def test_dry_run_catalog_plan_without_ib_is_full(self):
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps({"headDir": str(self.tmp / "dump"), "source": "src/cf",
                                         "baseDir": str(self.tmp / "base")}), encoding="utf-8")
        ib = self.tmp / "ib"
        cfg = {"project": {"source": "src/cf"}, "environment": {"ib": str(ib)}}
        args = SimpleNamespace(repo=str(self.repo), plan=str(plan_path), head=None, full=False, assume_current=False)
        with mock.patch.object(cli, "_project", return_value=self.repo), \
                mock.patch.object(cli, "_cfg", return_value=cfg), \
                mock.patch.object(cli.workspace, "run_dir", return_value=self.tmp / "runs"), \
                mock.patch.object(cli.workspace, "plan_root", return_value=self.tmp / "dump"), \
                mock.patch.object(cli.config, "detect_extension"), \
                mock.patch.object(cli.build, "plan_load_dirs", return_value=["module.bsl"]):
            result = cli._build_plan(args)
        self.assertEqual(result["action"], "full")
        self.assertTrue(result["createDatabase"])
        self.assertIn("загрузить выгрузку: полностью", result["operations"])

    def test_dry_run_catalog_assume_current_only_marks_state(self):
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps({"headDir": str(self.tmp / "dump"), "source": "src/cf",
                                         "baseDir": str(self.tmp / "base")}), encoding="utf-8")
        cfg = {"project": {"source": "src/cf"}, "environment": {"ib": str(self.tmp / "ib")}}
        args = SimpleNamespace(repo=str(self.repo), plan=str(plan_path), head=None, full=False, assume_current=True)
        with mock.patch.object(cli, "_project", return_value=self.repo), \
                mock.patch.object(cli, "_cfg", return_value=cfg), \
                mock.patch.object(cli.workspace, "run_dir", return_value=self.tmp / "runs"), \
                mock.patch.object(cli.workspace, "plan_root", return_value=self.tmp / "dump"), \
                mock.patch.object(cli.config, "detect_extension"), \
                mock.patch.object(cli.build, "plan_load_dirs", side_effect=AssertionError("plan load called")):
            result = cli._build_plan(args)
        self.assertEqual(result["action"], "mark-current")
        self.assertFalse(result["createDatabase"])
        self.assertEqual(result["operations"], ["записать состояние текущей редакции без загрузки"])

    def test_dry_run_rejects_nonempty_invalid_ib(self):
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps({"headDir": str(self.tmp / "dump"), "source": "src/cf"}),
                             encoding="utf-8")
        ib = self.tmp / "ib"
        ib.mkdir()
        (ib / "keep.txt").write_text("leave untouched", encoding="utf-8")
        cfg = {"project": {"source": "src/cf"}, "environment": {"ib": str(ib)}}
        args = SimpleNamespace(repo=str(self.repo), plan=str(plan_path), head=None, full=False, assume_current=False)
        with mock.patch.object(cli, "_project", return_value=self.repo), \
                mock.patch.object(cli, "_cfg", return_value=cfg), \
                mock.patch.object(cli.workspace, "run_dir", return_value=self.tmp / "runs"), \
                mock.patch.object(cli.workspace, "plan_root", return_value=self.tmp / "dump"), \
                mock.patch.object(cli.config, "detect_extension"), \
                self.assertRaisesRegex(build.InfraError, "каталог не пуст"):
            cli._build_plan(args)
        self.assertEqual([path.name for path in ib.iterdir()], ["keep.txt"])

    def test_catalog_assume_current_skips_load(self):
        plan = {"headDir": str(self.tmp / "dump"), "source": "src/cf"}
        platform = SimpleNamespace(ib=self.tmp / "ib", connection=None, lock=lambda: None)
        with mock.patch.object(build, "_load", side_effect=AssertionError("load called")):
            result = build.build_dirs(plan, platform, self.tmp / "runs", assume_current=True)
        self.assertEqual(result["action"], "marked")
        self.assertFalse((platform.ib / "1Cv8.1CD").exists())
        self.assertTrue((self.tmp / "runs" / build.RUN_STATE).exists())


if __name__ == "__main__":
    unittest.main()
