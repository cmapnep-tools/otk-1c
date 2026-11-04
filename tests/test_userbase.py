"""Рабочая база пользователя: всё, что ОТК поставил, после проверки возвращается к прежнему виду."""
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.errors import InfraError
from otk_1c.probes import userbase
from otk_1c.probes.platform import ConfigOps


class FakePlatform(ConfigOps):
    """Конфигуратор понарошку: в базе есть расширения existing; журнал вызовов."""

    def __init__(self, existing, fail=()):
        self.existing, self.fail, self.calls = set(existing), set(fail), []

    def designer(self, *args, label="", timeout=0):
        args = [str(a) for a in args]
        self.calls.append((args[0], args[args.index("-Extension") + 1] if "-Extension" in args else None))
        name = self.calls[-1][1]
        if args[0] == "/DumpCfg":
            if name in self.existing:
                Path(args[1]).write_bytes(b"cfe")
                return 0, 0.1, ""
            return 1, 0.1, f"Расширение {name} не найдено"
        if label.startswith(tuple(f"ub-restore-{n}" for n in self.fail)):
            return 1, 0.1, "ошибка восстановления"
        return 0, 0.1, ""


class UserBaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_restore_existing_and_delete_new(self):
        p = FakePlatform(existing={"demo", "YAXUNIT"})
        s = userbase.Session(p, self.tmp)
        s.guard("demo", tool=False)
        s.guard("YAXUNIT")
        s.guard("otk")
        s.guard("YAXUNIT")                                  # повторно — без второго сохранения
        self.assertEqual(sum(1 for c in p.calls if c == ("/DumpCfg", "YAXUNIT")), 1)
        p.calls.clear()
        text = s.restore()
        self.assertEqual(p.calls, [("/DeleteCfg", "otk"), ("/LoadCfg", "YAXUNIT"), ("/UpdateDBCfg", "YAXUNIT"),
                                   ("/LoadCfg", "demo"), ("/UpdateDBCfg", "demo")])
        self.assertIn("otk удалено", text)
        self.assertIn("demo возвращено", text)

    def test_keep_tools(self):
        p = FakePlatform(existing={"demo"})
        s = userbase.Session(p, self.tmp, keep_tools=True)
        s.guard("demo", tool=False)
        s.guard("otk")
        p.calls.clear()
        self.assertIn("инструменты оставлены", s.restore())
        self.assertEqual(p.calls, [("/LoadCfg", "demo"), ("/UpdateDBCfg", "demo")])  # расширение проекта — всегда

    def test_failed_restore_is_loud(self):
        p = FakePlatform(existing={"demo"}, fail={"demo"})
        s = userbase.Session(p, self.tmp)
        s.guard("demo", tool=False)
        self.assertTrue(s.restore().startswith("РАБОЧАЯ БАЗА НЕ ВОССТАНОВЛЕНА"))

    def test_guard_error_stops(self):
        class Broken(FakePlatform):
            def designer(self, *args, label="", timeout=0):
                return 1, 0.1, "Ошибка доступа к файлу"
        with self.assertRaises(InfraError):
            userbase.Session(Broken(existing=()), self.tmp).guard("demo")


if __name__ == "__main__":
    unittest.main()
