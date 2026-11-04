"""Сборка: ошибки файлов выгрузки при загрузке — находки, а не сбой инфраструктуры."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
