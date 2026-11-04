"""Заготовки сценарных тестов: какие методы без тестов, куда и как пишется каркас, черновик внешней командой."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.extras import scaffold
from otk_1c.gate import scenarios
from otk_1c.probes import ib_tools
from otk_1c.sources import bsl

EXISTING = """Процедура ИсполняемыеСценарии() Экспорт
	ЮТТесты
		.ДобавитьТест("Ссылка", , "М.Нормализовать");
КонецПроцедуры

Процедура Ссылка() Экспорт
КонецПроцедуры
"""


def module(owner, *methods, kind="CommonModule", flags=None, export=True):
    return {"owner": owner, "path": f"CommonModules/{owner.split('.')[-1]}/Ext/Module.bsl", "kind": kind,
            "reason": "changed", "flags": flags or {"Server": True}, "contexts": ["Server"],
            "changedMethods": [{"name": m, "kind": "function", "export": export, "params": ["П1", "П2"],
                                "lines": [1, 3]} for m in methods] + [{"name": "<тело>", "kind": "body"}]}


class ScaffoldTest(unittest.TestCase):
    def setUp(self):
        self.repo = Path(tempfile.mkdtemp())
        ext = self.repo / "tests" / "cfe" / "Т"
        ib_tools.generate_test_extension(ext, {"language": "Русский", "languageUuid": "u", "languageCode": "ru",
                                            "interface": "Taxi"}, name="проект_тесты", prefix="отс_",
                                      modules={"отс_М": ({"Server": "true"}, EXISTING)})

    def tearDown(self):
        rmtree_force(self.repo)

    def run_scaffold(self, plan, cfg=None):
        return scaffold.scaffold(self.repo, {"source": "src", **plan}, cfg or {},
                                 lambda rel: "Функция Х(П1, П2) Экспорт\n\tВозврат П1;\nКонецФункции\n",
                                 config_root=None, log=lambda m: None)

    def test_targets_skip_tested_and_body(self):
        exts = scenarios.discover(self.repo)
        plan = {"modules": [module("ОбщийМодуль.М", "Нормализовать", "Разобрать")]}
        self.assertEqual([t["tag"] for t in scaffold.targets(plan, exts)], ["М.Разобрать"])

    def test_names_and_flags(self):
        self.assertEqual(scaffold.module_name("ОбщийМодуль.demo_Сервер", "отс_", "demo_"), "отс_Сервер")
        self.assertEqual(scaffold.module_name("Справочник.Клиенты", "тст_"), "тст_Справочник_Клиенты")
        client = module("ОбщийМодуль.К", "А", flags={"ClientManagedApplication": True})
        self.assertEqual(scaffold.test_flags(client), {"ClientManagedApplication": "true"})
        self.assertEqual(scaffold.test_flags(module("ОбщийМодуль.С", "А")), {"Server": "true"})

    def test_append_to_existing_and_new_module(self):
        done = self.run_scaffold({"modules": [module("ОбщийМодуль.М", "Нормализовать", "Разобрать", "Ссылка"),
                                              module("ОбщийМодуль.Н", "Посчитать")]})
        self.assertEqual([(d["module"], d["test"]) for d in done],
                         [("отс_М", "Разобрать"), ("отс_М", "Ссылка_2"), ("отс_Н", "Посчитать")])
        ext = self.repo / "tests" / "cfe" / "Т"
        text = (ext / "CommonModules" / "отс_М" / "Ext" / "Module.bsl").read_bytes().decode("utf-8-sig")
        parsed = bsl.parse(text)
        self.assertEqual([m.name for m in parsed.methods], ["ИсполняемыеСценарии", "Ссылка", "Разобрать", "Ссылка_2"])
        self.assertIn('ЮТТесты.ДобавитьТест("Ссылка_2", "Ссылка: заготовка", "М.Ссылка");', text)
        self.assertIn("// Результат = М.Разобрать(П1, П2);", text)
        self.assertIn('ЮТест.Пропустить("заготовка: тест не написан");', text)
        tests = {t["name"]: t["tags"] for e in scenarios.discover(self.repo) for t in e["tests"]}
        self.assertEqual(tests["Посчитать"], ["Н.Посчитать"])                      # новый модуль найден
        self.assertIn("<CommonModule>отс_Н</CommonModule>", (ext / "Configuration.xml").read_text(encoding="utf-8-sig"))
        self.assertEqual(self.run_scaffold({"modules": [module("ОбщийМодуль.Н", "Посчитать")]}), [])  # повтор — нечего

    def test_draft_command(self):
        script = self.repo / "draft.py"
        script.write_text("import json, sys\ninfo = json.load(sys.stdin)\n"
                          "print('Результат = ' + info['module'].split('.')[1] + '.' + info['method'] + '(1, 2);')\n"
                          "print('ЮТест.ОжидаетЧто(Результат).Равно(1);')\n", encoding="utf-8")
        done = self.run_scaffold({"modules": [module("ОбщийМодуль.Н", "Посчитать")]},
                                 {"scaffold": {"draft": [sys.executable, str(script)]}})
        self.assertTrue(done[0]["draft"])
        text = (self.repo / done[0]["file"]).read_bytes().decode("utf-8-sig")
        self.assertIn("\tРезультат = Н.Посчитать(1, 2);", text)
        self.assertNotIn("ЮТест.Пропустить", text)

    def test_creates_extension(self):
        shutil.rmtree(self.repo / "tests")
        root = self.repo / "cf"
        (root / "Languages").mkdir(parents=True)
        (root / "Configuration.xml").write_text("<DefaultLanguage>Language.Русский</DefaultLanguage>"
                                                "<InterfaceCompatibilityMode>Taxi</InterfaceCompatibilityMode>",
                                                encoding="utf-8")
        (root / "Languages" / "Русский.xml").write_text('<Language uuid="u1"><LanguageCode>ru</LanguageCode>',
                                                         encoding="utf-8")
        done = scaffold.scaffold(self.repo, {"source": "src", "modules": [module("ОбщийМодуль.Н", "А")]}, {},
                                 lambda rel: "", config_root=root, log=lambda m: None)
        self.assertEqual(done[0]["file"], "tests/cfe/Тесты/CommonModules/тст_Н/Ext/Module.bsl")
        self.assertEqual([e["name"] for e in scenarios.discover(self.repo)], ["Тесты"])


if __name__ == "__main__":
    unittest.main()
