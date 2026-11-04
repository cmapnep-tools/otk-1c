"""Ложные находки статики на выгрузке типовой конфигурации: сигнатуры с комментариями, вызов с переносом
скобки."""
import unittest

from otk_1c.gate import static
from otk_1c.sources import index


class VendorCasesTest(unittest.TestCase):
    def test_comment_in_signature(self):
        text = ("Функция Выборка(Параметры,\n\tТовары,\n\tСклад, //Если склады в ТЧ, то параметр игнорируется\n"
                "\tСтроки = Неопределено) Экспорт\nКонецФункции\n")
        self.assertEqual(index.exports(text)["выборка"], ["Выборка", 3, 4])

    def test_comment_with_paren_in_signature(self):
        text = "Процедура П(Дерево, //Соответствие(Строк),\n\tВид) Экспорт\nКонецПроцедуры\n"
        self.assertEqual(index.exports(text)["п"], ["П", 2, 2])

    def test_call_paren_on_next_line(self):
        lines = ["\tК = Справочники.Упаковки.Коэффициент", "\t\t(Объект.Упаковка, Номенклатура);"]
        self.assertTrue(static._next_line_opens(lines, 1))
        self.assertFalse(static._next_line_opens(["\tК = Справочники.Упаковки.Штука;", "\tИначе"], 1))


if __name__ == "__main__":
    unittest.main()
