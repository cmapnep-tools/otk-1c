"""Единые знания языка и платформы (refactor-layers, этап 5): заголовки методов из `bsl` в обоих вариантах
языка, имена модулей и тексты платформы из словаря, режимы /CheckModules, чтение текста выгрузки."""
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c import lang
from otk_1c.extras import availability, controls
from otk_1c.probes import designer_check, platform
from otk_1c.probes.executors import extended_check, owner_of, scenarios
from otk_1c.sources import bsl, layout

ENGLISH = ("// Procedure Commented()\r\n"
           "&AtServer\r\n"
           "Procedure OnCreateAtServer(Cancel, StandardProcessing)\r\n"
           "\tValue = 1;\r\n"
           "EndProcedure\r\n"
           "\r\n"
           "Async Function ReadAsync(Val Name) Export\r\n"
           "\tReturn Name;\r\n"
           "EndFunction\r\n")


class TestHeaders(unittest.TestCase):
    def test_header_both_languages(self):
        names = [m.group("name") for m in bsl.header_re().finditer(ENGLISH)]
        self.assertEqual(names, ["OnCreateAtServer", "ReadAsync"])
        m = bsl.header_re().search("  асинх ФУНКЦИЯ Прочитать(А)")
        self.assertEqual(m.group("indent", "async", "kind", "name"), ("  ", "асинх", "ФУНКЦИЯ", "Прочитать"))

    def test_method_line_and_declared(self):
        self.assertEqual(bsl.method_line(ENGLISH, "readasync"), 7)
        self.assertIsNone(bsl.method_line(ENGLISH, "Commented"))
        self.assertIsNone(bsl.method_line(ENGLISH, "Read"))          # имя целиком, не префикс
        # заголовок без скобки на строке (разделён #Если) — в declared, но не в header_re
        text = "#Если Сервер Тогда\nПроцедура Раздельная\n#Иначе\nProcedure Раздельная\n#КонецЕсли\n(А)"
        self.assertEqual(bsl.declared(text), ["Раздельная", "Раздельная"])
        self.assertEqual(list(bsl.header_re().finditer(text)), [])
        self.assertTrue(bsl.end_re().match("\xa0КонецПроцедуры\r"))     # неразрывный пробел в отступе (типовая)

    def test_scen_line_of_english(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, tmp)
        (tmp / "Module.bsl").write_bytes(b"\xef\xbb\xbf" + ENGLISH.encode("utf-8"))
        self.assertEqual(scenarios._line_of(tmp / "Module.bsl", "OnCreateAtServer"), 3)
        self.assertIsNone(scenarios._line_of(tmp / "Module.bsl", "Missing"))
        self.assertIsNone(scenarios._line_of(tmp / "none.bsl", "OnCreateAtServer"))

    def test_controls_english(self):
        renamed = controls._rename_method(ENGLISH, "OnCreateAtServer", "ОТКПереименован")
        self.assertIn("Procedure OnCreateAtServerОТКПереименован(Cancel", renamed)
        self.assertIn("// Procedure Commented()", renamed)
        removed = controls._remove_method(ENGLISH, "OnCreateAtServer")
        self.assertNotIn("OnCreateAtServer", removed)
        self.assertIn("Async Function ReadAsync", removed)

    def test_interceptors_english(self):
        text, inserted = extended_check.to_interceptors(ENGLISH)
        self.assertIn('&Вместо("ReadAsync")', text)
        self.assertIn("Async Function kc_ReadAsync(Val Name) Export", text)
        self.assertEqual(len(inserted), 2)


class TestPlatformTexts(unittest.TestCase):
    def test_modules_from_dictionary(self):
        kinds = lang.platform_texts()["modules"]
        self.assertEqual(kinds["МодульСеанса"], "SessionModule")
        self.assertEqual(owner_of("ОбщаяКоманда.К.МодульКоманды"), "ОбщаяКоманда.К")
        self.assertEqual(owner_of("Конфигурация.МодульСеанса"), "Конфигурация.МодульСеанса")

    def test_module_file(self):
        self.assertEqual(designer_check.module_file("ОбщаяКоманда.К.МодульКоманды"),
                         "CommonCommands/К/Ext/CommandModule.bsl")
        self.assertEqual(designer_check.module_file("ОбщаяФорма.Ф.Форма"), "CommonForms/Ф/Ext/Form/Module.bsl")
        self.assertEqual(designer_check.module_file("HTTPСервис.С.Модуль"), "HTTPServices/С/Ext/Module.bsl")
        self.assertIsNone(designer_check.module_file("Справочник.С.Форма"))
        self.assertIsNone(designer_check.module_file("Конфигурация.МодульСеанса"))

    def test_check_modes(self):
        self.assertEqual(designer_check.MODES, ["-ThinClient", "-WebClient", "-Server", "-ExternalConnection",
                                           "-ThickClientOrdinaryApplication", "-ExtendedModulesCheck"])
        self.assertEqual(extended_check.MODES, ["-ThinClient", "-WebClient", "-Server", "-ExternalConnection",
                                          "-ExtendedModulesCheck"])
        self.assertEqual(availability.MODES, (("-Server", "Server"), ("-ThinClient", "ThinClient"),
                                       ("-WebClient", "WebClient"), ("-ExternalConnection", "ExternalConnection")))
        self.assertEqual(platform.check_mode("OrdinaryClient"), designer_check.ORDINARY)


class TestReadText(unittest.TestCase):
    def test_read_text(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, tmp)
        (tmp / "a.bsl").write_bytes(b"\xef\xbb\xbfA\r\n\xff")
        self.assertEqual(layout.read_text(tmp / "a.bsl"), "A\r\n�")
        self.assertIsNone(layout.read_text(tmp / "none.bsl"))
        self.assertIsNone(layout.read_text(tmp))


if __name__ == "__main__":
    unittest.main()
