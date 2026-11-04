"""Шаг run без загрузки ОТК (--vendor, рабочая база): основная конфигурация, не применённая к БД."""
import unittest
from pathlib import Path
from unittest import mock

from otk_1c import checks
from otk_1c.errors import InfraError
from otk_1c.gate import runner
from otk_1c.probes.platform import ConfigOps


class Platform(ConfigOps):
    bin = Path("C:/Program Files/1cv8/8.5.1.1529/bin")
    user = password = ""

    def __init__(self, rc=0):
        self.calls, self.rc = [], rc

    def unlock(self):
        self.calls.append("unlock")

    def designer(self, *args, timeout=3600, label=""):
        self.calls.append(args[0])
        return self.rc, 12.34, "сбой" if self.rc else ""


class Ctx:
    def __init__(self, platform):
        self.platform, self.env, self.timings = platform, {}, {}
        self.gate = checks.Gate({"checks": {}})


class DbConfigTest(unittest.TestCase):
    def apply(self, changed, session=None, rc=0):
        ctx = Ctx(Platform(rc))
        with mock.patch("otk_1c.gate.runner.db_config_changed", lambda platform, progid: changed):
            note = runner._apply_db_config(ctx, session)
        return ctx, note

    def test_applied(self):
        ctx, note = self.apply(False)
        self.assertIsNone(note)
        self.assertEqual(ctx.platform.calls, ["unlock"])

    def test_copy_updated(self):
        ctx, note = self.apply(True)
        self.assertEqual(ctx.platform.calls, ["unlock", "/UpdateDBCfg"])
        self.assertIn("(12.3 c)", note)
        self.assertIn("не была применена", note)
        self.assertEqual(ctx.timings["update"], 12.34)

    def test_copy_unknown_updated(self):
        """COM не ответил — копию всё равно обновить: пробы по прежнему коду хуже лишних секунд."""
        ctx, note = self.apply(None)
        self.assertEqual(ctx.platform.calls[-1], "/UpdateDBCfg")
        self.assertIn("проверить применение не удалось", note)

    def test_copy_update_failed(self):
        with self.assertRaises(InfraError):
            self.apply(True, rc=1)

    def test_session_not_touched(self):
        """Рабочая база не меняется: пробы исполнения — «не проверено» с причиной, extcheck остаётся."""
        ctx, note = self.apply(True, session=object())
        self.assertNotIn("/UpdateDBCfg", ctx.platform.calls)
        self.assertIn("не обновлена", note)
        self.assertEqual({n["check"] for n in ctx.gate.not_checked}, set(runner.RUNTIME))
        self.assertFalse(any(ctx.gate.enabled(n) for n in runner.RUNTIME))
        self.assertTrue(ctx.gate.enabled("extcheck"))

    def test_session_unknown(self):
        ctx, note = self.apply(None, session=object())
        self.assertIsNone(note)
        self.assertEqual(ctx.gate.not_checked, [])


if __name__ == "__main__":
    unittest.main()
