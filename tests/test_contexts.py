"""Доступность методов и свойств платформы по контексту компиляции (6.13): случаи, проверенные Конфигуратором 8.5.1."""
import unittest

from otk_1c.gate import platform_api
from otk_1c.sources import bsl, contexts

GLOBAL = "Глобальный контекст"


class Help:
    """Справка: все имена — методы глобального контекста без версии (о контексте судит таблица доступности)."""
    version = "8.5.1.1529"

    def check(self, name, compat=None, scope="any"):
        return {"name": name, "found": scope in ("global", "method", "any")}


def run(kind, code, flags=("Server", "ThinClient", "ExternalConnection"), web_client=False):
    parsed = bsl.parse(code)
    entry = {"path": "m.bsl", "kind": kind, "contexts": list(flags)}
    found = []
    known = {n.lower() for n in bsl.declared(parsed.sanitized)}
    known |= {"мояпеременная"}
    platform_api.ApiChecker(Help(), "Version8_3_21", web_client=web_client).check(
        kind, parsed.sanitized.split("\n"), range(1, code.count("\n") + 2),
        lambda check, sev, line, msg: found.append((sev, line, msg)), known, parsed.conditions,
        contexts.line_contexts(entry, parsed))
    return [(sev, line, msg.split(" —")[0]) for sev, line, msg in found if "недоступ" in msg]


class TestEvaluate(unittest.TestCase):
    def test_preprocessor(self):
        self.assertTrue(contexts.evaluate("Сервер", contexts.EXTERNAL))       # Сервер истинно во внешнем соединении
        self.assertFalse(contexts.evaluate("Сервер", contexts.THIN))
        self.assertTrue(contexts.evaluate("Клиент", contexts.WEB))
        self.assertTrue(contexts.evaluate("НЕ (ВебКлиент) И (Клиент)", contexts.THIN))
        self.assertFalse(contexts.evaluate("Сервер Или ТолстыйКлиентОбычноеПриложение", contexts.THIN))
        self.assertTrue(contexts.evaluate("Server OR ExternalConnection", contexts.SERVER))
        self.assertIsNone(contexts.evaluate("НеизвестныйСимвол", contexts.SERVER))
        self.assertIsNone(contexts.evaluate("(Сервер", contexts.SERVER))
        self.assertIsNone(contexts.evaluate("Сервер Клиент", contexts.SERVER))
        self.assertIsNone(contexts.evaluate("Сервер И", contexts.SERVER))
        self.assertTrue(contexts.evaluate("", contexts.SERVER))

    def test_logic_precedence(self):
        """Приоритет и ассоциативность как у Python: not, затем and, затем or."""
        import itertools
        ops = ("and", "or")
        for a, b, c in itertools.product((True, False), repeat=3):
            for o1, o2 in itertools.product(ops, repeat=2):
                for neg in (False, True):
                    tokens = (["not"] if neg else []) + [a, o1, b, o2, c]
                    want = eval(" ".join(map(str, tokens)))        # noqa: S307 - эталон в тесте
                    self.assertEqual(contexts._logic(tokens), want, tokens)
                    grouped = [a, o1, "(", b, o2, c, ")"]
                    self.assertEqual(contexts._logic(grouped), eval(f"{a} {o1} ({b} {o2} {c})"), grouped)  # noqa: S307


class TestCommonModule(unittest.TestCase):
    def test_flags(self):
        code = "Процедура П() Экспорт\n\tВызватьПаузу(1);\n\tОткрытьФорму(\"Х\");\nКонецПроцедуры\n"
        found = run("CommonModule", code)
        self.assertIn(("error", 2, "Метод ВызватьПаузу недоступен: тонкий клиент, веб-клиент (доступен: сервер, "
                                  "внешнее соединение)"), found)
        self.assertIn(("error", 3, "Метод ОткрытьФорму недоступен: сервер, внешнее соединение (доступен: тонкий "
                                  "клиент, веб-клиент)"), found)
        self.assertEqual([ln for _, ln, _ in run("CommonModule", code, flags=("Server",))], [3])   # только сервер

    def test_directives(self):
        code = ("&НаСервере\nПроцедура С()\n\tУстановитьПривилегированныйРежим(Истина);\n\tОткрытьФорму(\"Х\");\n"
                "КонецПроцедуры\n&НаКлиенте\nПроцедура К()\n\tОткрытьФорму(\"Х\");\n\tХ = ТекущаяДатаСеанса();\n"
                "КонецПроцедуры\n")
        found = run("CommonModule", code)
        self.assertEqual([(s, ln) for s, ln, _ in found], [("error", 4), ("error", 9)])

    def test_external_connection_accepts_server_methods(self):
        code = "Процедура П() Экспорт\n\tХ = ЗначениеВДанныеФормы(1, 2);\nКонецПроцедуры\n"
        self.assertEqual(run("CommonModule", code, flags=("ExternalConnection",)), [])

    def test_web_client_is_warning(self):
        code = "Процедура П() Экспорт\n\tИмя = ПолучитьИмяВременногоФайла();\n\tВопрос(\"х\", 1);\nКонецПроцедуры\n"
        found = run("CommonModule", code, flags=("ThinClient",))
        self.assertEqual(found, [("warning", 2, "Метод ПолучитьИмяВременногоФайла недоступен: веб-клиент "
                                             "(доступен: сервер, тонкий клиент, внешнее соединение)")])

    def test_web_client_mode_is_error(self):
        """environment.webClient: недоступное только в веб-клиенте — ошибка; в «#Если Не ВебКлиент» — нет."""
        code = ("Процедура П() Экспорт\n\tИмя = ПолучитьИмяВременногоФайла();\n"
                "#Если Не ВебКлиент Тогда\n\tИмя = ПолучитьИмяВременногоФайла();\n#КонецЕсли\nКонецПроцедуры\n")
        self.assertEqual([(s, ln) for s, ln, _ in run("CommonModule", code, flags=("ThinClient",), web_client=True)],
                         [("error", 2)])
        self.assertEqual([(s, ln) for s, ln, _ in run("CommonModule", code, flags=("ThinClient",))], [("warning", 2)])

    def test_constructor_by_context(self):
        """«Новый Т» по таблице типов: недоступен в контексте строки — как метод; только веб-клиент —
        предупреждение и с environment.webClient (веб-клиент типов — по справке); типа нет в таблице — не
        проверяется."""
        from unittest import mock

        class HelpNew(Help):
            def check(self, name, compat=None, scope="any"):
                return {"name": name, "found": True} if scope == "new" else super().check(name, compat, scope)

        table = {"new чтениетекста": ("type", frozenset({"Server", "ThinClient", "ExternalConnection"})),
                 "new comобъект": ("type", frozenset({"Server", "ExternalConnection"}))}
        code = ("&НаКлиенте\nПроцедура П()\n\tЧ = Новый ЧтениеТекста;\n\tК = Новый COMОбъект(\"x\");\n"
                "\tЗ = Новый Запрос;\nКонецПроцедуры\n")

        def go(web_client=False, measured=False):
            parsed = bsl.parse(code)
            entry = {"path": "m.bsl", "kind": "FormModule", "contexts": ["Server", "ThinClient"]}
            found = []
            with mock.patch.object(contexts, "availability_table", lambda version: table), \
                    mock.patch.object(contexts, "types_web_measured", lambda version: measured):
                platform_api.ApiChecker(HelpNew(), "Version8_3_21", web_client=web_client).check(
                    "FormModule", parsed.sanitized.split("\n"), range(1, code.count("\n") + 2),
                    lambda c, sev, line, msg: found.append((sev, line, msg)), set(), parsed.conditions,
                    contexts.line_contexts(entry, parsed))
            return [(s, ln, m.split(" —")[0]) for s, ln, m in found if "недоступ" in m]
        self.assertEqual(go(), [("warning", 3,
                                 "Тип ЧтениеТекста недоступен: веб-клиент (доступен: сервер, тонкий клиент, "
                                            "внешнее соединение)"),
                                ("error", 4, "Тип COMОбъект недоступен: тонкий клиент, веб-клиент (доступен: сервер, "
                                            "внешнее соединение)")])
        self.assertEqual([(s, ln) for s, ln, _ in go(web_client=True)], [("warning", 3), ("error", 4)])
        # веб-клиент типов измерен в веб-клиенте: с environment.webClient — ошибка, без — предупреждение
        self.assertEqual([(s, ln) for s, ln, _ in go(web_client=True, measured=True)], [("error", 3), ("error", 4)])
        self.assertEqual([(s, ln) for s, ln, _ in go(measured=True)], [("warning", 3), ("error", 4)])

    def test_apply_webclient(self):
        """Веб-клиент типов — тонкий клиент и не «Тип не определен»; роняющий клиент, не измеренный — как было."""
        from otk_1c.extras import availability
        ctx = ["Server", "ThinClient", "WebClient", "ExternalConnection"]
        data = {"contexts": ctx, "types": {
            "ПараметрВыбора": ["Server", "ThinClient", "ExternalConnection"],       # справка: веб-клиента нет
            "ЧтениеXML": ["Server", "ThinClient", "WebClient", "ExternalConnection"],
            "ХранилищеЗначения": ["Server", "ExternalConnection"],                  # тонкий клиент отверг
            "Рамка": ["Server", "ThinClient", "ExternalConnection"],
            "Новый8": ["Server", "ThinClient", "WebClient"]}}
        availability.apply_webclient(data, ("8.5.4", {"ПараметрВыбора": "defined", "ЧтениеXML": "undefined",
                                               "ХранилищеЗначения": "defined", "Рамка": "crash"}))
        self.assertEqual(data["types"], {
            "ПараметрВыбора": ctx, "ЧтениеXML": ["Server", "ThinClient", "ExternalConnection"],
            "ХранилищеЗначения": ["Server", "ExternalConnection"],
            "Рамка": ["Server", "ThinClient", "ExternalConnection"], "Новый8": ["Server", "ThinClient", "WebClient"]})
        self.assertEqual(data["typesWebClient"], "замер веб-клиента 8.5.4")
        self.assertIsNone(availability.webclient_types("8.2.19"))                        # другой линейки замера нет
        self.assertEqual(availability.webclient_types("8.5.6")[0], max(
            (f.stem for f in availability.WEB_DIR.glob("8.5.*.json")), key=contexts.version_key))

    def test_preprocessor_branch(self):
        code = ("Процедура П() Экспорт\n#Если Сервер Тогда\n\tОткрытьФорму(\"Х\");\n#КонецЕсли\n"
                "#Если Клиент Тогда\n\tОткрытьФорму(\"Х\");\n#КонецЕсли\nКонецПроцедуры\n")
        found = run("CommonModule", code)
        self.assertEqual([(s, ln) for s, ln, _ in found], [("error", 3)])

    def test_properties_and_locals(self):
        code = ("Процедура П() Экспорт\n\tХ = ПараметрыСеанса.Пользователь;\n\tМояПеременная = 1;\n"
                "\tХ = Объект.ПараметрыСеанса;\nКонецПроцедуры\n")
        found = run("CommonModule", code, flags=("ThinClient",))
        self.assertEqual(found, [("error", 2, "Свойство ПараметрыСеанса недоступно: тонкий клиент, веб-клиент "
                                             "(доступно: сервер, внешнее соединение)")])

    def test_english(self):
        code = "&AtServer\nProcedure P()\n\tOpenForm(\"X\");\nEndProcedure\n"
        self.assertEqual([(s, ln) for s, ln, _ in run("CommonModule", code)], [("error", 3)])


class TestFormModule(unittest.TestCase):
    def test_default_directive_is_server(self):
        code = ("Процедура БезДирективы()\n\tОткрытьФорму(\"Х\");\nКонецПроцедуры\n"
                "&НаКлиентеНаСервереБезКонтекста\nПроцедура Оба()\n\tВызватьПаузу(1);\nКонецПроцедуры\n"
                "&НаКлиенте\nПроцедура Клиент()\n\tОткрытьФорму(\"Х\");\nКонецПроцедуры\n")
        found = run("FormModule", code, flags=("ThinClient", "Server"))
        self.assertEqual([(s, ln) for s, ln, _ in found], [("error", 2), ("error", 6)])


if __name__ == "__main__":
    unittest.main()
