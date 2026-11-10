"""Проект-расширение: проверка расширения Конфигуратором, исключения BSL LS, gate --all."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.analyzers import bsl_ls
from otk_1c.probes import designer_check
from otk_1c.probes.executors import extended_check
from otk_1c.sources import gitdiff

OUT = ("{demo ОбщийМодуль.demo_Сервер.Модуль(1080,5)}: Ожидается выражение\r\n"
       "\tА =<<?>> ; (Проверка: Сервер)\r\n"
       "{demo ОбщийМодуль.demo_Сервер.Модуль(1080,5)}: Ожидается выражение\r\n"
       "\tА =<<?>> ; (Проверка: Внешнее соединение)\r\n"
       "demo ОбщийМодуль.demo_Адаптер.Модуль Возможно ошибочное свойство: \"ВидыСобытий\"\r\n"
       "otk_check ОбщийМодуль.Чужой.Модуль Возможно ошибочное свойство: \"Х\"\r\n")


class FakePlatform:
    def __init__(self, out, rc=101):
        self.out, self.rc, self.args = out, rc, None

    def designer(self, *args, label="", timeout=0):
        self.args = args
        return self.rc, 1.0, self.out


class ExtensionTest(unittest.TestCase):
    def test_parse_extension_prefix(self):
        f = designer_check.parse(OUT, "src/cfe/Demo")
        self.assertEqual(f[0]["object"], "ОбщийМодуль.demo_Сервер.Модуль")
        self.assertEqual(f[0]["file"], "src/cfe/Demo/CommonModules/demo_Сервер/Ext/Module.bsl")
        self.assertEqual(f[0]["modes"], ["Сервер", "Внешнее соединение"])

    def test_run_extension(self):
        p = FakePlatform(OUT)
        entries = [{"path": "CommonModules/demo_Сервер/Ext/Module.bsl", "owner": "ОбщийМодуль.demo_Сервер"}]
        texts = {"CommonModules/demo_Адаптер/Ext/Module.bsl": "А = 1;\nПеречисления.ВидыСобытий.Звонок;\n"}
        res, note = extended_check.run_extension(p, "demo", entries, texts.get)
        self.assertIn("-ExtendedModulesCheck", p.args)
        self.assertEqual(p.args[-2:], ("-Extension", "demo"))
        self.assertEqual(len(res), 2)                      # чужое расширение не учитывается
        self.assertEqual(res[0]["module"], "CommonModules/demo_Сервер/Ext/Module.bsl")
        self.assertEqual(res[0]["line"], 1080)
        self.assertEqual(res[0]["outcome"], "compile-error")
        self.assertEqual(res[1]["module"], "CommonModules/demo_Адаптер/Ext/Module.bsl")
        self.assertEqual(res[1]["line"], 2)                # строка по идентификатору из текста модуля
        self.assertEqual(note, "")
        with self.assertRaises(extended_check.Failed) as e:      # не разобрано — проверка не выполнена целиком
            extended_check.run_extension(FakePlatform("непонятный вывод"), "demo", entries, texts.get)
        self.assertIn("rc=101", e.exception.reason)

    def test_bslls_excludes(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = tmp / ".bsl-language-server.json"
            cfg.write_text('{"excludePaths": ["**/demo_ModuleDFI/**"]}', encoding="utf-8")
            ex = bsl_ls.excluded_by(cfg)
            self.assertTrue(ex("CommonModules/demo_ModuleDFI/Ext/Module.bsl"))
            self.assertFalse(ex("CommonModules/demo_Сервер/Ext/Module.bsl"))
            self.assertFalse(bsl_ls.excluded_by(tmp / "нет.json")("CommonModules/demo_ModuleDFI/Ext/Module.bsl"))
        finally:
            rmtree_force(tmp)

    def test_empty_commit(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            subprocess.run(["git", "init", "-q", str(tmp)], check=True)
            (tmp / "a.txt").write_text("a", encoding="utf-8")
            subprocess.run(["git", "-C", str(tmp), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(tmp), "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-q", "-m", "a"],
                           check=True)
            base = gitdiff.empty_commit(tmp)
            self.assertEqual(gitdiff.git(tmp, "rev-parse", f"{base}^{{tree}}").strip(), gitdiff.EMPTY_TREE)
            names = gitdiff.git(tmp, "diff", "--name-status", base, "HEAD").split()
            self.assertEqual(names, ["A", "a.txt"])
            self.assertEqual(gitdiff.git(tmp, "branch", "--contains", base).strip(), "")   # вне веток
        finally:
            rmtree_force(tmp)


if __name__ == "__main__":
    unittest.main()
