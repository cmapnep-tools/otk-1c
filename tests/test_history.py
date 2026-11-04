"""Журнал срабатываний хуков: запись, ротация по размеру, сбой записи не роняет хук."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.integrations import history


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-history-"))
        self.cfg = {"environment": {"runDir": str(self.tmp / "runs")}}
        self.repo = self.tmp / "repo"

    def tearDown(self):
        rmtree_force(self.tmp)

    def rows(self, name=history.NAME):
        return [json.loads(x) for x in (self.tmp / "runs" / name).read_text(encoding="utf-8").splitlines()]

    def test_record(self):
        history.record(self.cfg, self.repo, "pt", "post", 1, time.time() - 2, command="ЗагрузитьИзИсходников")
        history.record(self.cfg, self.repo, "hook", "pre-push", 0, time.time(), command=None)
        rows = self.rows()
        self.assertEqual([(r["source"], r["action"], r["code"]) for r in rows],
                         [("pt", "post", 1), ("hook", "pre-push", 0)])
        self.assertGreaterEqual(rows[0]["seconds"], 2)
        self.assertEqual(rows[0]["command"], "ЗагрузитьИзИсходников")
        self.assertNotIn("command", rows[1])
        self.assertEqual(rows[0]["repo"], str(self.repo))

    def test_rotation(self):
        with mock.patch.object(history, "MAX_BYTES", 10):
            history.record(self.cfg, self.repo, "pt", "pre", 0, time.time())
            history.record(self.cfg, self.repo, "pt", "post", 0, time.time())
        self.assertEqual([r["action"] for r in self.rows()], ["post"])
        self.assertEqual([r["action"] for r in self.rows("history.1.jsonl")], ["pre"])

    def test_failure_ignored(self):
        with mock.patch.object(history, "path", side_effect=PermissionError("занято")):
            entry = history.record(self.cfg, self.repo, "pt", "check", 2, time.time())
        self.assertEqual(entry["code"], 2)
        self.assertFalse((self.tmp / "runs").exists())


if __name__ == "__main__":
    unittest.main()
