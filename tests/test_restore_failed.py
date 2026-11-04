"""База не возвращена к виду до проверки — сбой инфраструктуры (restore-failed, код 2), а не зелёный прогон
и не обычный дефект: рабочая база пользователя (runner) и откат хука Platform Tools."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.errors import InfraError
from otk_1c.gate import runner
from otk_1c.integrations import platform_tools
from otk_1c.sources import workspace


class FakeSession:
    def __init__(self, failed):
        self.platform = mock.Mock()
        self.failed, self.unrestored = failed, []

    def restore(self):
        self.unrestored = list(self.failed)
        return "РАБОЧАЯ БАЗА НЕ ВОССТАНОВЛЕНА ПОЛНОСТЬЮ: demo" if self.failed else "рабочая база восстановлена"


class UserBaseRunTest(unittest.TestCase):
    def play(self, failed, run=None):
        written = {}
        cfg = {"environment": {"connection": "/FD:/base"}}
        with mock.patch.object(runner.userbase, "Session", lambda *a: FakeSession(failed)), \
                mock.patch.object(runner, "Platform"), \
                mock.patch.object(runner.model, "with_dependents", lambda plan, static: plan), \
                mock.patch.object(runner, "_run", run or (lambda *a, **k: {"notes": [], "results": []})), \
                mock.patch.object(runner, "write", lambda d, out, gate, plan: written.update(out) or out):
            runner.run(Path("."), {}, cfg, Path("."), Path("."), log=lambda m: None, gate=mock.Mock())
        return written

    def test_restored(self):
        out = self.play([])
        self.assertNotIn("infra", out)
        self.assertEqual(out["notes"], ["рабочая база восстановлена"])

    def test_not_restored_is_infra(self):
        out = self.play(["demo"])
        self.assertEqual([x["reasonCode"] for x in out["infra"]], ["restore-failed"])
        self.assertIn("НЕ ВОССТАНОВЛЕНА", out["infra"][0]["message"])

    def test_not_restored_after_failure(self):
        """Проверка прервалась, и база не возвращена: причина — restore-failed, исходный сбой — в тексте."""
        def broken(*a, **k):
            raise InfraError("обновление БД: rc=1", code="platform-failed")
        with self.assertRaises(InfraError) as e:
            self.play(["demo"], broken)
        self.assertEqual(e.exception.code, "restore-failed")
        self.assertIn("обновление БД: rc=1", str(e.exception))

    def test_failure_restored_keeps_cause(self):
        def broken(*a, **k):
            raise InfraError("обновление БД: rc=1", code="platform-failed")
        with self.assertRaises(InfraError) as e:
            self.play([], broken)
        self.assertEqual(e.exception.code, "platform-failed")


class FakePt:
    """Целевая база хука: коды возврата шагов отката."""

    def __init__(self, load=0, update=0):
        self.load, self.update = load, update

    def load_extension(self, name, path, label=""):
        return self.load, 0.1, "ошибка загрузки" if self.load else ""

    def delete_extension(self, name, label=""):
        return self.load, 0.1, ""

    def update_db(self, extension=None, label=""):
        return self.update, 0.1, "ошибка обновления" if self.update else ""


class PtRollbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def post(self, platform, on_error=False):
        state = {"existed": True, "backup": str(self.tmp / "demo.cfe"), "updatedb": True}
        (self.tmp / "state.json").write_text(json.dumps(state), encoding="utf-8")
        found = [{"severity": "error", "message": "ошибка"}]
        with mock.patch.object(platform_tools, "state_dir", lambda *a: self.tmp), \
                mock.patch.object(platform_tools, "run_checks", lambda *a, **k: (found, [])), \
                mock.patch.object(platform_tools, "platform_for", lambda *a: platform), \
                mock.patch.object(platform_tools, "_report", lambda *a: None):
            return platform_tools.post(self.tmp, {"project": {}}, {"ibconnection": "/FD:/base", "extension": "demo"},
                                       log=lambda m: None, open_summary=False, on_error=on_error)

    def test_rolled_back_is_defect(self):
        self.assertEqual(self.post(FakePt()), 1)

    def test_failed_load_is_infra(self):
        self.assertEqual(self.post(FakePt(load=1)), 2)
        self.assertEqual(self.post(FakePt(load=1), on_error=True), 2)

    def test_failed_update_is_infra(self):
        self.assertEqual(self.post(FakePt(update=1)), 2)

    def test_rollback_texts(self):
        state = {"existed": True, "backup": "D:/b/demo.cfe"}
        conn = {"updatedb": True}
        text, ok = platform_tools.rollback(FakePt(update=1), state, "demo", conn, print)
        self.assertFalse(ok)
        self.assertIn("ОТКАТ НЕ ЗАВЕРШЁН", text)
        text, ok = platform_tools.rollback(FakePt(), state, "demo", conn, print)
        self.assertTrue(ok)
        self.assertIn("конфигурация БД обновлена", text)


class MaterializeTest(unittest.TestCase):
    """Python 3.11.0–3.11.3: у tarfile нет фильтра извлечения — редакция git извлекается без него."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_without_filter(self):
        repo = self.tmp / "repo"
        (repo / "cf").mkdir(parents=True)
        (repo / "cf" / "Configuration.xml").write_text("<MetaDataObject/>", encoding="utf-8")
        for args in (["init", "-q"], ["add", "."],
                     ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "x"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True)
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True,
                             text=True).stdout.strip()
        for extract in ({}, workspace.SAFE_EXTRACT):
            trees = self.tmp / f"trees{len(extract)}"
            with mock.patch.object(workspace, "SAFE_EXTRACT", extract):
                target = workspace.materialize(repo, sha, "cf", trees)
            self.assertTrue((target / "cf" / "Configuration.xml").is_file())


if __name__ == "__main__":
    unittest.main()
