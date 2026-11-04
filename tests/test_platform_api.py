"""Методы и типы платформы (apicheck) на подставной справке: случаи, проверенные Конфигуратором 8.5.1."""
import unittest

from otk_1c.gate import platform_api
from otk_1c.sources import bsl


class FakeHelp:
    """Справка: имя -> (вид, владелец, с какой версии, есть ли конструктор)."""

    def __init__(self, version, entries):
        self.version = version
        self.entries = entries

    def check(self, name, compat=None, scope="any"):
        rows = [e for e in self.entries.get(name.lower(), [])
                if scope == "any" or (scope == "global" and e[1] == "Глобальный контекст" and e[0] == "method")
                or (scope == "method" and e[0] == "method") or (scope == "new" and e[0] == "type")]
        res = {"name": name, "found": bool(rows)}
        if rows:
            res["since"] = rows[0][2]
            if scope == "new" and not rows[0][3]:
                res["no_ctor"] = True
            key = ApiKey(rows[0][2])
            if compat and key > ApiKey(compat):
                res["newer_than_compat"] = ".".join(ApiKey.nums(compat))
        return res


class ApiKey(tuple):
    @staticmethod
    def nums(v):
        import re
        return re.findall(r"\d+", v)[:3]

    def __new__(cls, v):
        return super().__new__(cls, (int(x) for x in cls.nums(v)))


GLOBAL = "Глобальный контекст"
HELP_85 = FakeHelp("8.5.1.1529", {
    "стрнайти": [("method", GLOBAL, "8.3.6", None)],
    "вызватьпаузу": [("method", GLOBAL, "8.3.25", None)],
    "получитьобщуюпалитруцветовзначковтаблицы": [("method", GLOBAL, "8.5.1", None)],
    "записать": [("method", "СправочникОбъект", "8.0", None)],
    "структура": [("type", "", "8.0", True)],
    "генераторслучайныхпаролей": [("type", "", "8.3.22", True)],
    "описаниеобработкирасшифровкикомпоновкиданных": [("type", "", "8.5.1", True)],
    "websocketклиентсоединение": [("type", "", "8.3.27", False)],
    "окноклиентскогоприложения": [("type", "", "8.2", False)],   # конструктор есть, в справке — нет
})
HELP_827 = FakeHelp("8.3.27.2342", {"стрнайти": [("method", GLOBAL, "8.3.6", None)]})
HELP_OLD_GAP = FakeHelp("8.5.1.1529", {"получитьзаголовокклиентскогоприложения": [("method", GLOBAL, "8.3.3", None)]})

CODE = """Процедура Тест() Экспорт
	ВызватьПаузу(1);
	П = ПолучитьОбщуюПалитруЦветовЗначковТаблицы();
	НачатьТранзакциюЗаписи();
	С = Новый WebSocketКлиентСоединение;
	Г = Новый ГенераторСлучайныхПаролей();
	Н = Новый НесуществующийТип;
	Р = Новый ОписаниеОбработкиРасшифровкиКомпоновкиДанных();
КонецПроцедуры
Процедура Корректно()
	Ок = СтрНайти("а", "б"); Ок2 = Новый Структура("А", 1); Своя(); Ок3 = Объект.НеизвестныйМетод();
	Ок4 = Отчеты.Отчет.
		МетодМенеджера(1);
	Ок5 = Т. НайтиСтроки(О);
	// ВыдуманныйВКомментарии();
	Т = "ВыдуманныйВСтроке()";
	Ок6 = Новый ОкноКлиентскогоПриложения;
	УстановитьЗаголовокПриложения("З");
	Ок7 = Новый
		Структура("А", 1);
#Если МобильноеПриложениеКлиент Тогда
	ОтправитьВложениеАндроид(, "Т");
#КонецЕсли
#Если ВебКлиент Тогда
	Ждать ПаузаАсинх(2);
#КонецЕсли
КонецПроцедуры
#Если Сервер Тогда
Процедура Своя()
#Иначе
Процедура СвояКлиент()
#КонецЕсли
КонецПроцедуры
"""


def run(code, kind, help_ix, compat="Version8_3_21", others=(), checker=None):
    parsed = bsl.parse(code)
    found = []
    known = {n.lower() for n in bsl.declared(parsed.sanitized)}
    (checker or platform_api.ApiChecker(help_ix, compat, others)).check(
        kind, parsed.sanitized.split("\n"), range(1, code.count("\n") + 2),
        lambda check, sev, line, msg: found.append((sev, line, msg)), known, parsed.conditions)
    return found


class TestCompatWarnings(unittest.TestCase):
    """Минимальная платформа и выключатель предупреждений «новее режима»; ошибка новой линейки — всегда."""

    def lines(self, **kw):
        checker = platform_api.ApiChecker(HELP_85, "Version8_3_21", **kw)
        found = run(CODE, "CommonModule", HELP_85, checker=checker)
        return {line: sev for sev, line, _ in found if line in (2, 3, 6, 8)}, checker

    def test_min_platform(self):
        got, checker = self.lines(min_platform="8.3.24")
        # 8.3.25 и 8.5.1 новее 8.3.24 — предупреждение; конструктор 8.3.22 — скрыт
        self.assertEqual(got, {2: "warning", 3: "error", 8: "warning"})
        self.assertEqual(checker.hidden_note(), "предупреждений «новее режима совместимости» скрыто: 1 "
                                                "(не новее environment.minPlatform 8.3.24)")

    def test_disabled(self):
        got, checker = self.lines(compat_warnings=False)
        self.assertEqual(got, {3: "error"})
        self.assertEqual(checker.hidden, 3)
        self.assertIn("environment.compatWarnings = false", checker.hidden_note())

    def test_default_unchanged(self):
        got, checker = self.lines()
        self.assertEqual(got, {2: "warning", 3: "error", 6: "warning", 8: "warning"})
        self.assertIsNone(checker.hidden_note())


class TestApiCheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.found = run(CODE, "CommonModule", HELP_85)
        cls.by_line = {}
        for sev, line, msg in cls.found:
            cls.by_line.setdefault(line, []).append((sev, msg))

    def at(self, line):
        return self.by_line.get(line, [])

    def test_same_line_newer_method_warns(self):
        self.assertEqual([s for s, _ in self.at(2)], ["warning"])
        self.assertIn("8.3.25", self.at(2)[0][1])

    def test_newer_line_global_method_fails(self):
        # 8.5.1 при режиме 8.3.21: Конфигуратор — «не определена»
        self.assertEqual([s for s, _ in self.at(3)], ["error"])

    def test_unknown_procedure_fails(self):
        self.assertEqual(self.at(4), [("error", "Процедура или функция с указанным именем не определена: "
                                               "НачатьТранзакциюЗаписи")])

    def test_type_without_constructor_fails(self):
        self.assertEqual(self.at(5), [("error", "Конструктор не найден: Новый WebSocketКлиентСоединение")])

    def test_newer_constructor_only_warns(self):
        # конструкторы Конфигуратор не отсекает ни в какой линейке (тип 8.5.1 при 8.3.21 — без ошибки)
        self.assertEqual([s for s, _ in self.at(6)], ["warning"])
        self.assertEqual([s for s, _ in self.at(8)], ["warning"])

    def test_unknown_type_fails(self):
        self.assertEqual(self.at(7), [("error", "Тип не определен: Новый НесуществующийТип")])

    def test_no_false_positives(self):
        self.assertFalse([f for f in self.found if f[1] > 8], self.found)

    def test_new_carried_to_next_line(self):
        # «Новый» в конце строки, тип — на следующей; тип всё равно проверяется
        code = "Процедура П()\n\tА = Новый\n\t\tНесуществующийТип(1);\nКонецПроцедуры\n"
        self.assertEqual(run(code, "CommonModule", HELP_85),
                         [("error", 3, "Тип не определен: Новый НесуществующийТип")])

    def test_mobile_only_skipped_desktop_branch_checked(self):
        code = ("Процедура П()\n#Если МобильноеПриложениеКлиент Или МобильноеПриложениеСервер Тогда\n"
                "\tНетТакойА();\n#Иначе\n\tНетТакойБ();\n#КонецЕсли\n"
                "#Если Не МобильноеПриложениеКлиент Тогда\n\tНетТакойВ();\n#КонецЕсли\nКонецПроцедуры\n")
        names = [msg.rsplit(" ", 1)[1] for _, _, msg in run(code, "CommonModule", HELP_85)]
        self.assertEqual(names, ["НетТакойБ", "НетТакойВ"])

    def test_web_only_skipped_negation_checked(self):
        # Код под #Иначе после «#Если ВебКлиент» и под «Не ВебКлиент» — для остальных клиентов, проверяется
        code = ("Процедура П()\n#Если ВебКлиент Тогда\n\tНетТакойА();\n#Иначе\n\tНетТакойБ();\n#КонецЕсли\n"
                "#Если Не ВебКлиент Тогда\n\tНетТакойВ();\n#КонецЕсли\n"
                "#Если ВебКлиент Или МобильныйКлиент Тогда\n\tНетТакойГ();\n#КонецЕсли\n"
                "#Если ТонкийКлиент Или ВебКлиент Тогда\n\tНетТакойД();\n#КонецЕсли\nКонецПроцедуры\n")
        names = [msg.rsplit(" ", 1)[1] for _, _, msg in run(code, "CommonModule", HELP_85)]
        self.assertEqual(names, ["НетТакойБ", "НетТакойВ", "НетТакойД"])

    def test_object_module_accepts_methods_of_any_type(self):
        self.assertEqual(run("Процедура П()\n\tЗаписать();\nКонецПроцедуры\n", "ObjectModule", HELP_85), [])
        self.assertTrue(run("Процедура П()\n\tЗаписать();\nКонецПроцедуры\n", "CommonModule", HELP_85))

    def test_platform_without_method(self):
        found = run("Процедура П()\n\tП = ПолучитьОбщуюПалитруЦветовЗначковТаблицы();\nКонецПроцедуры\n",
                    "CommonModule", HELP_827)
        self.assertEqual([s for s, _, _ in found], ["error"])

    def test_gap_in_own_help_filled_by_other_platform(self):
        code = "Процедура П()\n\tЗ = ПолучитьЗаголовокКлиентскогоПриложения();\nКонецПроцедуры\n"
        self.assertTrue(run(code, "CommonModule", HELP_827))
        self.assertEqual(run(code, "CommonModule", HELP_827, others=[HELP_OLD_GAP]), [])

    def test_no_compat_mode_no_version_findings(self):
        self.assertEqual(run("Процедура П()\n\tВызватьПаузу(1);\nКонецПроцедуры\n", "CommonModule", HELP_85, None), [])

    def test_compat_mode(self):
        ext = ("<Configuration><ConfigurationExtensionPurpose>AddOn</ConfigurationExtensionPurpose>"
               "<ConfigurationExtensionCompatibilityMode>Version8_3_21</ConfigurationExtensionCompatibilityMode>")
        main = ("<Configuration><ConfigurationExtensionCompatibilityMode>Version8_3_27"
                "</ConfigurationExtensionCompatibilityMode><CompatibilityMode>Version8_3_24</CompatibilityMode>")
        self.assertEqual(platform_api.compat_mode(ext), "Version8_3_21")
        self.assertEqual(platform_api.compat_mode(main), "Version8_3_24")
        self.assertIsNone(platform_api.compat_mode("<CompatibilityMode>DontUse</CompatibilityMode>"))


UNDECLARED = """Процедура Тест() Экспорт
	НетТакогоМодуля.Метод(1);
	ОбщийМодульЕсть.Метод(Структура.Количество());
	Поле = ВариантОткрытияОкна.БлокироватьОкноВладельца;
	Своя.Свойство = 1;
	Поле = ТипЭлементаСвоегоСловаря.Значение;
	Объект.Записать();
КонецПроцедуры
"""


class TestUndeclared(unittest.TestCase):
    """«Переменная не определена»: имя перед точкой, неизвестное модулю, конфигурации и справке."""

    def run_code(self, checker=None, variables=frozenset({"общиймодульесть", "своя", "объект"})):
        parsed = bsl.parse(UNDECLARED)
        found = []
        (checker or platform_api.ApiChecker(HELP_85, "Version8_3_21")).check(
            "CommonModule", parsed.sanitized.split("\n"), range(1, UNDECLARED.count("\n") + 2),
            lambda check, sev, line, msg: found.append((line, msg)), set(), parsed.conditions, variables=variables)
        return found

    def test_unknown_name(self):
        # Структура — тип из справки, ВариантОткрытияОкна — во встроенном словаре имён без справки
        self.assertEqual(self.run_code(), [(2, "Переменная не определена: НетТакогоМодуля"),
                                           (6, "Переменная не определена: ТипЭлементаСвоегоСловаря")])

    def test_off_without_variables(self):
        self.assertEqual(self.run_code(variables=None), [])

    def test_user_dictionary(self):
        names = platform_api.platform_names({"globals": ["ТипЭлементаСвоегоСловаря"]})
        checker = platform_api.ApiChecker(HELP_85, "Version8_3_21", names=names)
        self.assertEqual(self.run_code(checker), [(2, "Переменная не определена: НетТакогоМодуля")])


class TestUndocumentedDictionary(unittest.TestCase):
    """Словарь имён без справки: встроенный файл + раздел [platformNames] настроек."""

    def test_builtin(self):
        names = platform_api.platform_names()
        self.assertIn("установитьзаголовокприложения", names["methods"])
        self.assertIn("окноклиентскогоприложения", names["constructors"])
        self.assertIn("вариантоткрытияокна", names["globals"])

    def test_user_extends(self):
        names = platform_api.platform_names({"methods": ["НовыйМетодПлатформы"], "constructors": []})
        self.assertIn("новыйметодплатформы", names["methods"])
        self.assertIn("установитьзаголовокприложения", names["methods"])

    def test_user_constructor(self):
        code = "Процедура Т()\n\tС = Новый WebSocketКлиентСоединение;\nКонецПроцедуры\n"
        self.assertEqual([m for _, _, m in run(code, "CommonModule", HELP_85)],
                         ["Конструктор не найден: Новый WebSocketКлиентСоединение"])
        names = platform_api.platform_names({"constructors": ["WebSocketКлиентСоединение"]})
        checker = platform_api.ApiChecker(HELP_85, "Version8_3_21", names=names)
        # остаётся предупреждение о версии: тип 8.3.27 новее режима совместимости 8.3.21
        self.assertEqual([sev for sev, _, _ in run(code, "CommonModule", HELP_85, checker=checker)], ["warning"])

    def test_bad_section(self):
        with self.assertRaisesRegex(ValueError, "неизвестный ключ"):
            platform_api.platform_names({"types": ["Х"]})
        with self.assertRaisesRegex(ValueError, "нужен список"):
            platform_api.platform_names({"globals": "Х"})


if __name__ == "__main__":
    unittest.main()
