import unittest

from otk_1c.sources import bsl

MODULE = """#Область ПрограммныйИнтерфейс

Перем ПеременнаяМодуля Экспорт;

// Процедура Ложная() — в комментарии
Процедура Первая(Знач А, Б = 1,
	В = "строка с Процедура внутри") Экспорт
	Текст = "КонецПроцедуры в строке";
КонецПроцедуры

&НаСервере
&Вместо("Старая")
Функция Вторая()
	Текст = "ВЫБРАТЬ
	|	Т.Поле
	// комментарий между строками продолжения
	|ИЗ Справочник.Т КАК Т";
	Возврат Текст;
КонецФункции

#Если Сервер И НЕ ВнешнееСоединение Тогда
Асинх Процедура Третья() КонецПроцедуры
#Иначе
Procedure Fourth(Val X) Export
EndProcedure
#КонецЕсли

#КонецОбласти
ВызовВТелеМодуля();
"""


class TestLexer(unittest.TestCase):
    def test_literals(self):
        _, lits = bsl.lex('А = "a""b"; // "не строка"\nБ = "x\n|y";')
        self.assertEqual([lit.value for lit in lits], ['a"b', "x\ny"])
        self.assertEqual([lit.line for lit in lits], [1, 2])

    def test_sanitized_keeps_layout(self):
        text = 'А = "текст"; // комм\nБ = 1;'
        s, _ = bsl.lex(text)
        self.assertEqual(len(s), len(text))
        self.assertEqual(s.split("\n")[1], "Б = 1;")
        self.assertNotIn("комм", s)

    def test_comment_inside_multiline_string(self):
        _, lits = bsl.lex('Т = "ВЫБРАТЬ\n|1\n// комментарий\n|КАК П";')
        self.assertEqual(lits[0].value, "ВЫБРАТЬ\n1\nКАК П")

    def test_blank_line_inside_multiline_string(self):
        # пустая строка между строками продолжения не обрывает литерал (так в типовых, и они компилируются)
        san, lits = bsl.lex('Т = "ВЫБРАТЬ\n\t|1\n\t\r\n\t|ЕСТЬNULL(1, 2)";\nХ = 1;')
        self.assertEqual(len(lits), 1)
        self.assertNotIn("ЕСТЬNULL", san)
        self.assertIn("Х = 1", san)


class TestParser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = bsl.parse(MODULE)
        cls.by = {x.name: x for x in cls.m.methods}

    def test_methods_found(self):
        self.assertEqual([x.name for x in self.m.methods], ["Первая", "Вторая", "Третья", "Fourth"])

    def test_signature(self):
        p = self.by["Первая"]
        self.assertTrue(p.export)
        self.assertEqual([(x.name, x.by_value, x.has_default) for x in p.params],
                         [("А", True, False), ("Б", False, True), ("В", False, True)])
        self.assertEqual((p.header, p.end), (6, 9))

    def test_directive_and_annotation(self):
        f = self.by["Вторая"]
        self.assertEqual(f.kind, "function")
        self.assertEqual(f.directive, "Server")
        self.assertEqual(f.annotations, ['Вместо("Старая")'])
        self.assertEqual(f.start, 11)

    def test_one_line_async_and_english(self):
        self.assertTrue(self.by["Третья"].is_async)
        self.assertEqual(self.by["Третья"].start, self.by["Третья"].end)
        self.assertTrue(self.by["Fourth"].export)
        self.assertTrue(self.by["Fourth"].params[0].by_value)

    def test_preprocessor_conditions(self):
        third = self.by["Третья"].header
        fourth = self.by["Fourth"].header
        self.assertEqual(self.m.conditions[third], ("Сервер И НЕ ВнешнееСоединение",))
        self.assertEqual(self.m.conditions[fourth], ("НЕ (Сервер И НЕ ВнешнееСоединение)",))
        symbols, negated = bsl.condition_symbols(self.m.conditions[third])
        self.assertEqual(symbols, {"Server", "ExternalConnection"})
        self.assertTrue(negated)

    def test_body_lines(self):
        lines = MODULE.split("\n")
        body = {i for i in self.m.code_lines}
        self.assertIn(lines.index("Перем ПеременнаяМодуля Экспорт;") + 1, body)
        self.assertIn(lines.index("ВызовВТелеМодуля();") + 1, body)
        self.assertNotIn(lines.index("#Область ПрограммныйИнтерфейс") + 1, body)

    def test_end_after_statement_on_same_line(self):
        m = bsl.parse("Процедура П()\n\tЕсли А Тогда\n\t\tБ();\n\tКонецЕсли;КонецПроцедуры\n"
                      "Процедура Р()\nКонецПроцедуры")
        self.assertEqual([(x.name, x.end) for x in m.methods], [("П", 4), ("Р", 6)])

    def test_signature_compare(self):
        a = bsl.parse("Процедура П(А) Экспорт\nКонецПроцедуры").methods[0]
        b = bsl.parse("Процедура П(А, Б) Экспорт\nКонецПроцедуры").methods[0]
        c = bsl.parse("процедура п(а) экспорт\nконецпроцедуры").methods[0]
        self.assertNotEqual(a.signature(), b.signature())
        self.assertEqual(a.signature(), c.signature())


if __name__ == "__main__":
    unittest.main()
