"""Сценарные тесты: разбор тегов YAxUnit, раскладка tests/cfe, выбор по изменённым методам плана."""
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.gate import scenarios

MODULE = """Процедура ИсполняемыеСценарии() Экспорт
	ЮТТесты.Тег("Ссылки")
		.ДобавитьТестовыйНабор("Нормализация", , "Набор")
		.ДобавитьТест("Https", "http в https", "demo_КлиентСервер.НормализованнаяСсылкаНаЗапись").ВТранзакции()
		.ДобавитьТест("Ip").Тег("demo_КлиентСервер.НормализованнаяСсылкаНаЗапись, Сеть")
		.ДобавитьТестовыйНабор("Прочее")
		.ДобавитьТест("Модуль", , "Справочник.Клиенты");
КонецПроцедуры

Процедура Https() Экспорт
	ЮТТесты.ДобавитьТест("НеРегистрация");     // вне ИсполняемыеСценарии — не тест
КонецПроцедуры
"""


def plan(*modules):
    return {"modules": list(modules)}


def module(owner, *methods, reason="changed"):
    return {"owner": owner, "reason": reason, "changedMethods": [{"name": m} for m in methods], "removedMethods": []}


class ScenariosTest(unittest.TestCase):
    def test_parse_tags(self):
        tests = scenarios.parse_module("Тест", MODULE)
        self.assertEqual([t["name"] for t in tests], ["Https", "Ip", "Модуль"])
        self.assertEqual(tests[0]["tags"], ["Ссылки", "Набор", "demo_КлиентСервер.НормализованнаяСсылкаНаЗапись"])
        self.assertEqual(tests[1]["tags"],
                         ["Ссылки", "Набор", "demo_КлиентСервер.НормализованнаяСсылкаНаЗапись", "Сеть"])
        self.assertEqual(tests[2]["tags"], ["Ссылки", "Справочник.Клиенты"])   # тег набора не переходит в следующий
        self.assertEqual(scenarios.parse_module("Т", "Процедура Другое() КонецПроцедуры"), [])

    def test_tags_for_plan(self):
        p = plan(module("ОбщийМодуль.demo_КлиентСервер", "НормализованнаяСсылкаНаЗапись"),
                 module("Справочник.Клиенты", "ПередЗаписью"), module("ОбщийМодуль.Зависимый", "Х", reason="dependent"))
        self.assertEqual(scenarios.tags_for(p), {"demo_КлиентСервер",
                                                  "demo_КлиентСервер.НормализованнаяСсылкаНаЗапись",
                                                  "Справочник.Клиенты", "Справочник.Клиенты.ПередЗаписью"})

    def test_discover_and_select(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ext = tmp / "tests" / "cfe" / "Тесты"
            (ext / "CommonModules" / "отс_Ссылки" / "Ext").mkdir(parents=True)
            (ext / "Configuration.xml").write_text("<Configuration><Properties><Name>demo_тесты</Name></Properties>"
                                                   "</Configuration>", encoding="utf-8")
            (ext / "CommonModules" / "отс_Ссылки" / "Ext" / "Module.bsl").write_text(MODULE, encoding="utf-8")
            exts = scenarios.discover(tmp)
            self.assertEqual(exts[0]["name"], "demo_тесты")
            self.assertEqual(exts[0]["dir"], "tests/cfe/Тесты")
            sel = scenarios.select(plan(module("ОбщийМодуль.demo_КлиентСервер",
                                               "НормализованнаяСсылкаНаЗапись")), exts)
            self.assertEqual([s["name"] for s in sel], ["Https", "Ip"])
            self.assertEqual(sel[0]["matched"], ["demo_КлиентСервер.НормализованнаяСсылкаНаЗапись"])
            self.assertEqual(scenarios.select(plan(module("ОбщийМодуль.Другой", "М")), exts), [])     # «0 сценариев»
            self.assertEqual(len(scenarios.select(plan(), exts, all_scenarios=True)), 3)
            self.assertEqual(scenarios.discover(tmp, "нет/такого"), [])
            # движок YAxUnit («Добавить YAxUnit» Platform Tools) и расширение без тестов не считаются тестовыми
            for folder, name, text in (("YAXUNIT", "YAXUNIT", MODULE),
                                       ("Пустое", "Пустое", "Процедура Х() КонецПроцедуры")):
                d = tmp / "tests" / "cfe" / folder
                (d / "CommonModules" / "М" / "Ext").mkdir(parents=True)
                (d / "Configuration.xml").write_text(f"<Name>{name}</Name>", encoding="utf-8")
                (d / "CommonModules" / "М" / "Ext" / "Module.bsl").write_text(text, encoding="utf-8")
            self.assertEqual([e["name"] for e in scenarios.discover(tmp)], ["demo_тесты"])
        finally:
            rmtree_force(tmp)


if __name__ == "__main__":
    unittest.main()
