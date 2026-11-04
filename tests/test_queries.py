import unittest

from otk_1c.sources import bsl, queries


def extract(text, lines=None):
    return queries.extract(bsl.parse(text), lines)


class TestQueries(unittest.TestCase):
    def test_complete_multiline(self):
        q = extract('Т = "ВЫБРАТЬ\n|\tН.Ссылка\n|ИЗ Справочник.Н КАК Н";')
        self.assertEqual(len(q), 1)
        self.assertTrue(q[0]["complete"])
        self.assertEqual(q[0]["text"], "ВЫБРАТЬ\n\tН.Ссылка\nИЗ Справочник.Н КАК Н")

    def test_literal_concatenation_is_complete(self):
        q = extract('Т = "ВЫБРАТЬ 1 " +\n "КАК П";')
        self.assertTrue(q[0]["complete"])
        self.assertEqual(q[0]["text"], "ВЫБРАТЬ 1 КАК П")

    def test_variable_part_is_incomplete(self):
        q = extract('Т = "ВЫБРАТЬ Н." + Поле + " ИЗ Справочник.Н КАК Н";')
        self.assertEqual(len(q), 1)
        self.assertFalse(q[0]["complete"])
        self.assertIn("<?>", q[0]["text"])
        self.assertEqual(q[0]["reason"], queries.TABLE_REASON)

    def test_table_name_is_not_guessed(self):
        for code in ('Т = "ВЫБРАТЬ 1 КАК А ИЗ " + Имя + " КАК Т";',
                     'Т = "ВЫБРАТЬ 1 КАК А ИЗ Справочник." + Имя + " КАК Т";',
                     'Т = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т УПОРЯДОЧИТЬ ПО " + Поля + "";'):
            q = extract(code)[0]
            self.assertFalse(q["complete"], code)
            self.assertTrue(q["reason"], code)

    def test_condition_and_field_neutral(self):
        q = extract('Процедура П()\nТ = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ Т.Б = 1 И " + Условие() + '
                    '" УПОРЯДОЧИТЬ ПО А";\n'
                    'КонецПроцедуры')[0]
        self.assertTrue(q["complete"] and q["assembled"])
        self.assertIn("И ИСТИНА УПОРЯДОЧИТЬ", q["text"])
        q = extract('Т = "ВЫБРАТЬ Т.А КАК А, " + Поле() + " ИЗ Р КАК Т";')[0]
        self.assertIn("А, NULL КАК ОТКПоле1 ИЗ", q["text"])
        q = extract('Т = "ВЫБРАТЬ " + Первые() + " Т.А КАК А ИЗ Р КАК Т";')[0]
        self.assertEqual(q["text"], "ВЫБРАТЬ  Т.А КАК А ИЗ Р КАК Т")
        q = extract('Т = "ВЫБРАТЬ Т.А КАК А ПОМЕСТИТЬ " + ИмяВТ + " ИЗ Р КАК Т";')[0]
        self.assertIn("ПОМЕСТИТЬ ВТ_ОТК1 ИЗ", q["text"])

    def test_variable_with_literal_value(self):
        text = ('Процедура П()\n'
                'Условие = "Т.Б = &Б";\n'
                'Т = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ " + Условие + " И Т.В = 1";\n'
                'КонецПроцедуры')
        q = extract(text)[0]
        self.assertTrue(q["assembled"])
        self.assertIn("ГДЕ Т.Б = &Б И Т.В", q["text"])

    def test_variable_assigned_twice_is_neutral(self):
        text = ('Процедура П()\nЕсли Х Тогда Условие = "Т.Б = 1"; Иначе Условие = "Т.Б = 2"; КонецЕсли;\n'
                'Т = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ " + Условие + "";\nКонецПроцедуры')
        self.assertIn("ГДЕ ИСТИНА", extract(text)[0]["text"])

    def test_template_placeholders(self):
        text = ('Функция Ф()\n'
                'Т = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ &Отбор И #Доп И Т.В ПОДОБНО ""%#%""";\n'
                'Т = СтрЗаменить(Т, "&Отбор", "Т.Б = &Б");\n'
                'Т = СтрЗаменить(Т, "#Доп", ТекстДоп);\n'
                'КонецФункции')
        q = extract(text)[0]
        self.assertTrue(q["assembled"])
        self.assertIn('ГДЕ Т.Б = &Б И ИСТИНА И Т.В ПОДОБНО "%#%"', q["text"])

    def test_replaced_parameter_is_not_parameter(self):
        text = ('Функция Ф()\n'
                'Т = "ВЫБРАТЬ ПЕРВЫЕ 1 Ссылка ИЗ &ПолноеИмя ГДЕ ИмяПредопределенныхДанных = &Имя";\n'
                'Т = СтрЗаменить(Т, "&ПолноеИмя", Коллекция + "." + Таблица);\n'
                'КонецФункции')
        q = extract(text)[0]
        self.assertFalse(q["complete"])
        self.assertEqual(q["reason"], queries.TABLE_REASON)

    def test_str_template(self):
        text = ('Функция Ф()\n'
                'Т = СтрШаблон("ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ %1 И %2", "Т.Б = 1", Условие);\n'
                'КонецФункции')
        q = extract(text)[0]
        self.assertTrue(q["assembled"])
        self.assertIn("ГДЕ Т.Б = 1 И ИСТИНА", q["text"])
        for marker in ("%ИмяТаблицы%", "[ИмяТаблицы]", "{0}"):
            q = extract(f'Т = "ВЫБРАТЬ Т.А КАК А ИЗ {marker} КАК Т";')[0]
            self.assertFalse(q["complete"], marker)

    def test_appended_later_and_fragment(self):
        text = ('Процедура П()\n'
                'ТекстЗапроса = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ Т.Б = 1";\n'
                'Если Х Тогда ТекстЗапроса = ТекстЗапроса + " И Т.В = 2"; КонецЕсли;\n'
                'КонецПроцедуры')
        q = extract(text)[0]
        self.assertEqual((q["complete"], q["reason"]), (False, "текст дописывается в коде дальше"))
        q = extract('Т = "ВЫБРАТЬ Т.А КАК А ИЗ Р КАК Т ГДЕ";')[0]
        self.assertFalse(q["complete"])

    def test_template_table_placeholder(self):
        q = extract('Т = "ВЫБРАТЬ Т.А КАК А ИЗ #Таблица КАК Т";')[0]
        self.assertFalse(q["complete"])
        self.assertEqual(q["reason"], queries.TABLE_REASON)
        q = extract('Т = "ВЫБРАТЬ Т.А КАК А ИЗ &Таблица КАК Т ГДЕ Т.Б = &Б";')[0]
        self.assertTrue(q["complete"])                        # параметр-таблица: так разбирает и платформа
        self.assertFalse(q["assembled"])

    def test_appended_to_variable_is_incomplete(self):
        q = extract('Т = Т + "ВЫБРАТЬ 1 КАК П";')
        self.assertFalse(q[0]["complete"])

    def test_english_and_case(self):
        self.assertEqual(len(extract('Т = "select 1 as F";')), 1)
        self.assertEqual(len(extract('Т = "выбрать 1 как П";')), 1)

    def test_not_a_query(self):
        self.assertEqual(extract('С = "Выбрать файл";'), [])   # нет слов запроса (ИЗ, КАК...)
        self.assertEqual(extract('С = "Нажмите ВЫБРАТЬ";'), [])

    def test_lines_filter(self):
        text = 'Т1 = "ВЫБРАТЬ 1 КАК А";\nТ2 = "ВЫБРАТЬ 2 КАК Б";'
        self.assertEqual([q["line"] for q in extract(text, {2})], [2])


if __name__ == "__main__":
    unittest.main()
