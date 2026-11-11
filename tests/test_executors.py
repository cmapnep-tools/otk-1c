"""Части фазы 3 без платформы: разбор ошибок, перехватчики, план загрузки, пробы YAxUnit."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from _fs import rmtree_force
from otk_1c.probes import build
from otk_1c.probes.executors import classify, com, extended_check, owner_of, query_position, yaxunit


class TestClassify(unittest.TestCase):
    def test_probe_ok(self):
        self.assertEqual(classify("Метод объекта не обнаружен (__ОТКПроба__)")[0], "ok")

    def test_compile_error(self):
        text = ("Ошибка инициализации модуля: ОбщийМодуль.Х.Модуль | "
                "{ОбщийМодуль.Х.Модуль(60,2)}: Ожидается ключевое слово 'КонецЕсли' ('EndIf')")
        outcome, line, message = classify(text)
        self.assertEqual((outcome, line), ("compile-error", 60))
        self.assertIn("ОбщийМодуль.Х.Модуль(60,2)", message)

    def test_undeclared_variable(self):
        self.assertEqual(classify("{ОбщийМодуль.Х.Модуль(5,10)}: Переменная не определена (А)")[0], "compile-error")

    def test_unknown(self):
        self.assertEqual(classify("что-то совсем другое")[0], "unknown")

    def test_owner_and_query(self):
        self.assertEqual(owner_of("Документ.Д.МодульМенеджера"), "Документ.Д")
        self.assertEqual(owner_of("Справочник.С.Форма.Ф.Форма"), "Справочник.С.Форма.Ф")
        self.assertEqual(query_position('{(1, 11)}: Поле не найдено "Н.Х"'), (1, 11, 'Поле не найдено "Н.Х"'))


class TestComAvailable(unittest.TestCase):
    def test_no_pywin32_says_how_to_install(self):
        """Без pywin32 причина — что поставить, а не трассировка импорта (установка с нуля через pip)."""
        with patch.object(com, "WINDOWS", True), patch.dict(sys.modules, {"pythoncom": None}):
            ok, why = com.available()
        self.assertFalse(ok)
        self.assertIn("pip install pywin32", why)


class TestInterceptors(unittest.TestCase):
    def test_transform_and_lines(self):
        text = ("Перем А;\r\nПроцедура П() Экспорт\r\n\tА = 1;\r\nКонецПроцедуры\r\n"
                "Асинх Функция Ф(Х)\r\nКонецФункции\r\n")
        new, inserted = extended_check.to_interceptors(text)
        lines = new.split("\n")
        self.assertEqual(lines[1].strip(), '&Вместо("П")')
        self.assertTrue(lines[2].startswith("Процедура kc_П("))
        self.assertTrue(lines[6].startswith("Асинх Функция kc_Ф("))
        # строка «А = 1;» была 3-й, в расширении — 4-я
        self.assertEqual(extended_check.source_line(4, inserted), 3)
        self.assertEqual(extended_check.source_line(7, inserted), 5)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class TestPlanLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-build-"))
        git(self.tmp, "init", "-q", "-b", "master")
        git(self.tmp, "config", "user.email", "t@t")
        git(self.tmp, "config", "user.name", "t")
        self.src = self.tmp / "src" / "cf"
        for rel in ("Configuration.xml", "CommonModules/М/Ext/Module.bsl", "Catalogs/С/Forms/Ф/Ext/Form/Module.bsl",
                    "Catalogs/С/Forms/Ф/Ext/Form.xml", "CommonForms/О/Ext/Form/Module.bsl",
                    "CommonForms/О/Ext/Form.xml"):
            (self.src / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.src / rel).write_text("x", encoding="utf-8")
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "base")

    def tearDown(self):
        rmtree_force(self.tmp)

    def commit(self):
        git(self.tmp, "add", "-A")
        git(self.tmp, "commit", "-q", "-m", "c")

    def test_partial_form_by_description(self):
        """Модуль формы грузится описанием формы: файл внутри Ext Конфигуратор принимает за объект метаданных
        «…Ext» и падает (8.3.23), описание грузит форму вместе с содержимым и модулем."""
        (self.src / "Catalogs/С/Forms/Ф/Ext/Form/Module.bsl").write_text("y", encoding="utf-8")
        self.commit()
        self.assertEqual(build.plan_load(self.tmp, "src/cf", "HEAD~1", "HEAD"), ["Catalogs/С/Forms/Ф.xml"])
        (self.src / "CommonForms/О/Ext/Form/Module.bsl").write_text("y", encoding="utf-8")
        self.commit()
        self.assertEqual(build.plan_load(self.tmp, "src/cf", "HEAD~1", "HEAD"), ["CommonForms/О.xml"])

    def test_partial_form_content_by_description(self):
        """Изменённое содержимое формы — тоже описанием формы (оно несёт и содержимое, и модуль)."""
        (self.src / "Catalogs/С/Forms/Ф/Ext/Form.xml").write_text("y", encoding="utf-8")
        self.commit()
        self.assertEqual(build.plan_load(self.tmp, "src/cf", "HEAD~1", "HEAD"), ["Catalogs/С/Forms/Ф.xml"])

    def test_full_on_delete_or_configuration(self):
        (self.src / "CommonModules/М/Ext/Module.bsl").unlink()
        self.commit()
        self.assertIsNone(build.plan_load(self.tmp, "src/cf", "HEAD~1", "HEAD"))
        (self.src / "Configuration.xml").write_text("z", encoding="utf-8")
        self.commit()
        self.assertIsNone(build.plan_load(self.tmp, "src/cf", "HEAD~1", "HEAD"))


class TestYaxItems(unittest.TestCase):
    def test_items(self):
        modules = [
            {"path": "a", "owner": "ОбщийМодуль.К", "contexts": ["ThinClient", "OrdinaryClient"],
             "probe": {"strategy": "missing-method", "target": "К"}},
            {"path": "b", "owner": "Документ.Д.Форма.Ф", "contexts": ["ThinClient", "Server"],
             "probe": {"strategy": "get-form", "target": "Документ.Д.Форма.Ф"}},
            {"path": "c", "owner": "ОбщийМодуль.С", "contexts": ["Server"],
             "probe": {"strategy": "missing-method", "target": "С"}},
        ]
        items = yaxunit.build_items(modules, [], {"c"}, available=["Server", "ThinClient"])
        specs = [(i[0]["path"], i[1], i[2]["kind"], i[2]["expect"]) for i in items]
        self.assertEqual(specs, [("a", "ThinClient", "expr", "missing"), ("b", "ThinClient", "form", "none"),
                                 ("c", "Server", "expr", "missing")])


if __name__ == "__main__":
    unittest.main()
