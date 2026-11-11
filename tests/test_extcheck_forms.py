"""extcheck: модули форм и объектов во временном расширении — сборка выгрузки и разбор вывода."""
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.gate import report
from otk_1c.probes.executors import extended_check
from otk_1c.probes.platform import ConfigOps
from otk_1c.sources import layout

FMT = "2.17"      # выгрузка платформы 8.3.24: файлы временного расширения — в её формате
NS = f'xmlns="http://v8.1c.ru/8.3/MDClasses" xmlns:xr="http://v8.1c.ru/8.3/xcf/readable" version="{FMT}"'
DUMP = {
    "Configuration.xml": f"<MetaDataObject {NS}><Configuration uuid=\"c\"><Properties>"
                         "<DefaultLanguage>Language.Русский</DefaultLanguage>"
                         "<InterfaceCompatibilityMode>Taxi</InterfaceCompatibilityMode></Properties></Configuration>"
                         "</MetaDataObject>",
    "Languages/Русский.xml": f'<MetaDataObject {NS}><Language uuid="lang"><Properties><Name>Русский</Name>'
                             "<LanguageCode>ru</LanguageCode></Properties></Language></MetaDataObject>",
    "Catalogs/Товары.xml": f'<MetaDataObject {NS}><Catalog uuid="cat"><InternalInfo>'
                           '<xr:GeneratedType name="CatalogObject.Товары" category="Object"/></InternalInfo>'
                           "<Properties><Name>Товары</Name></Properties><ChildObjects>"
                           '<Attribute uuid="a1"><Properties><Name>Цена</Name><Type>xs:decimal</Type></Properties>'
                           '</Attribute><TabularSection uuid="ts1"><InternalInfo>'
                           '<xr:GeneratedType name="CatalogTabularSection.Товары.Состав" category="TabularSection"/>'
                           "</InternalInfo><Properties><Name>Состав</Name></Properties><ChildObjects>"
                           '<Attribute uuid="a2"><Properties><Name>Количество</Name></Properties></Attribute>'
                           "</ChildObjects></TabularSection><Form>Форма</Form></ChildObjects></Catalog></MetaDataObject>",
    "Catalogs/Товары/Forms/Форма.xml": f'<MetaDataObject {NS}><Form uuid="frm"><Properties><Name>Форма</Name>'
                                       "<FormType>Managed</FormType></Properties></Form></MetaDataObject>",
    "Catalogs/Товары/Forms/Форма/Ext/Form.xml": '<?xml version="1.0" encoding="UTF-8"?>\n'
                                                '<Form xmlns="http://v8.1c.ru/8.3/xcf/logform" version="2.22">'
                                                "<Events/><ChildItems/></Form>",
    "CommonAttributes/ОбластьДанных.xml": f'<MetaDataObject {NS}><CommonAttribute uuid="ca"><Properties>'
                                          "<Name>ОбластьДанных</Name><Type><v8:Type>xs:decimal</v8:Type></Type>"
                                          "<Content><xr:Item/></Content><AutoUse>Use</AutoUse>"
                                          "<DataSeparation>Separate</DataSeparation></Properties></CommonAttribute>"
                                          "</MetaDataObject>",
    "CommonAttributes/Неупомянутый.xml": f'<MetaDataObject {NS}><CommonAttribute uuid="cb"><Properties>'
                                         "<Name>Неупомянутый</Name></Properties></CommonAttribute></MetaDataObject>",
}
FORM_BASE = "Перем мКэш;\n\n&НаКлиенте\nПроцедура А()\n\tПерем Локальная;\nКонецПроцедуры\n"
FORM_HEAD = FORM_BASE + "\n&НаКлиенте\nПроцедура Б()\n\tНет();\nКонецПроцедуры\n"
OBJ = "Процедура ПередЗаписью(Отказ)\n\tЕсли ОбластьДанных = 0 Тогда\n\tКонецЕсли;\nКонецПроцедуры\n"
FORM_PATH = "Catalogs/Товары/Forms/Форма/Ext/Form/Module.bsl"
OBJ_PATH = "Catalogs/Товары/Ext/ObjectModule.bsl"
EDT_FORM_PATH = "Catalogs/Товары/Forms/Форма/Module.bsl"
EDT_OBJ_PATH = "Catalogs/Товары/ObjectModule.bsl"
EDT_OWNER = ('<?xml version="1.0" encoding="UTF-8"?>\n<mdclass:Catalog xmlns:mdclass='
             '"http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="cat"><name>Товары</name>'
             "<forms uuid=\"frm\"><name>Форма</name>{form_type}</forms></mdclass:Catalog>")
# Нет() в «стало» — строка 10; в расширении перед А и Б вставлено по &Вместо -> 12; в модуле объекта Х — 3 -> 4
AFTER = ("{otk_check Справочник.Товары.Форма.Форма.Форма(12,2)}: Процедура или функция с указанным именем "
         "не определена (Нет)\n\tНет(); (Проверка: Тонкий клиент)\n"
         "{otk_check Справочник.Товары.МодульОбъекта(4,7)}: Тип не определен (Х)\n"
         "\tХ = Новый Х; (Проверка: Сервер)\n"
         'otk_check Справочник.Товары.МодульОбъекта Возможно ошибочное свойство: "ОбластьДанных"\n')
BEFORE = 'otk_check Справочник.Товары.МодульОбъекта Возможно ошибочное свойство: "ОбластьДанных"\n'


def canary(obj, mode="Сервер"):
    """Ошибка канарейки (модуль скомпилирован) в выводе /CheckModules."""
    return (f"{{otk_check {obj}(90,2)}}: Процедура или функция с указанным именем не определена "
            f"({extended_check.CANARY})\n\t{extended_check.CANARY}(); (Проверка: {mode})\n")


FORM_OBJ, OBJ_OBJ = "Справочник.Товары.Форма.Форма.Форма", "Справочник.Товары.МодульОбъекта"
COMPILED = canary(FORM_OBJ) + canary(OBJ_OBJ)


class FakePlatform(ConfigOps):
    def __init__(self, after=AFTER + COMPILED, before=BEFORE + COMPILED, rc=1):
        self.calls, self.args = [], []
        self.out, self.rc = {"extcheck-after": after, "extcheck-before": before}, rc

    def designer(self, *args, label="", timeout=0):
        self.calls.append((args[0], label))
        self.args.append(args)
        if label in self.out:
            return self.rc, 0, self.out[label]
        return 0, 0, ""


class ExtCheckFormsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.tmp)
        self.root = self.tmp / "cf"
        for rel, text in DUMP.items():
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")

    def test_strip_vars(self):
        text = "&НаКлиенте\r\nПерем А;\r\nПерем Б Экспорт;\r\n\r\nПроцедура П()\r\n\tПерем В;\r\nКонецПроцедуры\r\n"
        out = extended_check.strip_vars(text)
        self.assertEqual(out.count("\n"), text.count("\n"))      # номера строк сохранены
        self.assertNotIn("Перем А", out)
        self.assertNotIn("Перем Б", out)
        self.assertNotIn("&НаКлиенте", out)
        self.assertIn("\tПерем В;", out)                          # локальная — остаётся

    def test_strip_vars_multiline(self):
        text = "Перем А,\r\n\tБ; // хвост\r\nПеремВ = 1;\r\n"
        out = extended_check.strip_vars(text)
        self.assertEqual(out, "\r\n\r\nПеремВ = 1;\r\n")

    @staticmethod
    def entries(contexts=(), status="M"):
        return [{"path": FORM_PATH, "owner": "Справочник.Товары.Форма.Форма", "kind": "FormModule",
                 "status": status, "reason": "changed", "contexts": list(contexts)},
                {"path": OBJ_PATH, "owner": "Справочник.Товары", "kind": "ObjectModule", "status": status,
                 "reason": "changed", "contexts": list(contexts)}]

    def run_check(self, platform=None, status="M", contexts=(), covered=None, base_texts=None):
        entries = self.entries(contexts, status)
        head = {FORM_PATH: FORM_HEAD, OBJ_PATH: OBJ.replace("Тогда", "Тогда\n\t\tХ = Новый Х;")}
        base = {FORM_PATH: FORM_BASE, OBJ_PATH: OBJ, **(base_texts or {})}
        platform = platform or FakePlatform()
        res, note = extended_check.run(platform, self.tmp / "run", self.root, entries,
                                       read_base=lambda e: base[e["path"]],
                                 read_head=lambda e: head[e["path"]], read_xml=lambda e: None,
                                 ordinary=extended_check.needed_ordinary(entries), covered=covered)
        return platform, res, note

    def test_extension_layout(self):
        self.run_check()
        ext = self.tmp / "run" / "extcheck-after"
        owner = (ext / "Catalogs/Товары.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<Name>Состав</Name>", owner)                           # ТЧ заимствована
        self.assertIn("<Name>Количество</Name>", owner)
        self.assertIn("<Form>Форма</Form></ChildObjects>", owner)
        form_md = (ext / "Catalogs/Товары/Forms/Форма.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<ExtendedConfigurationObject>frm</ExtendedConfigurationObject>", form_md)
        self.assertIn("<xr:State>Extended</xr:State>", form_md)
        form = (ext / "Catalogs/Товары/Forms/Форма/Ext/Form.xml").read_text(encoding="utf-8-sig")
        # Form.xml — в версии остальных файлов расширения (здесь выгрузки 2.17), иначе Конфигуратор её не загрузит
        self.assertTrue(form.endswith(f'version="{FMT}"><BaseForm version="{FMT}"/></Form>'), form)
        self.assertIn(f'version="{FMT}"', form_md)
        module = (ext / FORM_PATH).read_text(encoding="utf-8-sig")
        self.assertNotIn("мКэш", module)
        self.assertIn('&Вместо("Б")\nПроцедура kc_Б()', module)            # read_text: \r\n -> \n
        self.assertIn(f"\t{extended_check.CANARY}();", module)                  # канарейка в конце
        self.assertTrue((ext / OBJ_PATH).is_file())
        config = (ext / "Configuration.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<CommonAttribute>ОбластьДанных</CommonAttribute>", config)   # упомянут в модуле
        self.assertNotIn("Неупомянутый", config)
        attr = (ext / "CommonAttributes/ОбластьДанных.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<AutoUse>Use</AutoUse><DataSeparation>Separate</DataSeparation>", attr)   # разделитель виден
        self.assertIn("<Type><v8:Type>xs:decimal</v8:Type></Type>", attr)
        self.assertNotIn("<Content>", attr)

    def test_findings_and_masking(self):
        platform, res, note = self.run_check()
        self.assertEqual(note, "")
        errors = [(r["module"], r["line"], r["message"]) for r in res if r["outcome"] == "compile-error"]
        self.assertEqual(errors, [
            (FORM_PATH, 10, "Процедура или функция с указанным именем не определена (Нет) (Тонкий клиент)"
                            + extended_check.UNCOMPARED),                 # канарейки тонкого клиента в «после» нет
            (OBJ_PATH, 3, "Тип не определен (Х) (Сервер)")])            # «Возможно…» было и раньше — не новое
        partial = [r for r in res if r["outcome"] == "skipped"]
        self.assertEqual([r["module"] for r in partial], [OBJ_PATH])
        self.assertIn("проверен частично (Сервер)", partial[0]["message"])
        self.assertEqual(platform.calls[-1], ("/DeleteCfg", "extcheck-delete"))

    def test_not_compiled(self):
        """Канарейки формы нет — Конфигуратор форму отбросил: «не скомпилирован», а не «чисто»."""
        _, res, _ = self.run_check(FakePlatform(after=canary(OBJ_OBJ), before=canary(OBJ_OBJ), rc=1))
        self.assertEqual([(r["module"], r["outcome"]) for r in res], [(FORM_PATH, "skipped")])
        self.assertIn("не скомпилирован", res[0]["message"])

    def test_parse_error_without_canary(self):
        """Ошибка разбора останавливает компиляцию до канарейки: модуль с новой ошибкой — находка, а не «отброшен»."""
        parse = (f"{{otk_check {FORM_OBJ}(12,3)}}: Ожидается ключевое слово 'КонецПроцедуры' ('EndProcedure')\n"
                 "\t\t<<?>>КонецЕсли; (Проверка: Тонкий клиент)\n")
        _, res, _ = self.run_check(FakePlatform(after=parse + canary(OBJ_OBJ), before=canary(OBJ_OBJ), rc=1))
        self.assertEqual([(r["module"], r["outcome"], r["line"]) for r in res], [(FORM_PATH, "compile-error", 10)])
        self.assertIn("КонецПроцедуры", res[0]["message"])

    def test_dropped_with_possible_only(self):
        """«Возможно ошибочн…» без канарейки — не доказательство компиляции: модуль «не скомпилирован»."""
        possible = f'otk_check {FORM_OBJ} Возможно ошибочное свойство: "Х"\n'
        _, res, _ = self.run_check(FakePlatform(after=possible + canary(OBJ_OBJ), before=canary(OBJ_OBJ), rc=1))
        self.assertEqual([(r["module"], r["outcome"]) for r in res], [(FORM_PATH, "skipped")])

    def test_check_failed(self):
        """/CheckModules упал без разобранных строк — проверка не выполнилась целиком (решает реестр проверок)."""
        with self.assertRaises(extended_check.Failed) as e:
            self.run_check(FakePlatform(after="", before="", rc=1))
        self.assertIn("rc=1", e.exception.reason)

    def test_load_failed(self):
        """Расширение не загрузилось (формат новее платформы) — проверка не выполнена целиком, а не «модули чисты»."""
        platform = FakePlatform()
        platform.load_dir = lambda *a, **k: (
            1, 0, "Неизвестная версия формата 2.20 загружаемого файла run/extcheck-after/Configuration.xml")
        with self.assertRaises(extended_check.Failed) as e:
            self.run_check(platform)
        self.assertIn("Неизвестная версия формата", e.exception.reason)

    def test_before_failed(self):
        """«Было» не проверилось — находки «стало» не выдаются за новые."""
        _, res, _ = self.run_check(FakePlatform(before="", rc=1))
        self.assertFalse([r for r in res if r["outcome"] == "compile-error"])
        self.assertTrue(all("не сравнить с «было»" in r["message"] for r in res if not r["message"].startswith(
            "проверен частично")))

    def test_before_failed_keeps_comparison_reason(self):
        """Сбой проверки «было» и модуль без канарейки в «после»: в записи — и отсутствие сравнения, и некомпиляция."""
        _, res, _ = self.run_check(FakePlatform(before="", after=canary(OBJ_OBJ), rc=1))
        msgs = {r["module"]: r["message"] for r in res if r["outcome"] == "skipped"}
        self.assertIn("не сравнить с «было»", msgs[FORM_PATH])
        self.assertIn("модуль не скомпилирован", msgs[FORM_PATH])
        self.assertIn("не сравнить с «было»", msgs[OBJ_PATH])
        self.assertFalse([r for r in res if r["outcome"] == "compile-error"])

    def test_before_failed_with_parse_error_is_not_dropped(self):
        """Сбой «было» и ошибка разбора в «после» до канарейки: модуль не отброшен — запись только о сравнении,
        сама ошибка находкой не выдаётся."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Ожидается выражение\n"
               "\tА = ; (Проверка: Сервер)\n")
        _, res, _ = self.run_check(FakePlatform(before="", after=err, rc=1))
        msgs = {r["module"]: r["message"] for r in res if r["outcome"] == "skipped"}
        self.assertIn("не сравнить с «было»", msgs[FORM_PATH])
        self.assertNotIn("модуль не скомпилирован", msgs[FORM_PATH])
        self.assertIn("модуль не скомпилирован", msgs[OBJ_PATH])
        self.assertFalse([r for r in res if r["outcome"] == "compile-error"])

    def test_new_modules_without_possible(self):
        """Новые модули (без «было»): «Возможно ошибочн…» не показываются — без сравнения не отличить от ложных."""
        _, res, note = self.run_check(status="A")
        self.assertFalse([r for r in res if r["message"].startswith("Возможно")])
        self.assertIn("не показаны (1)", note)
        self.assertEqual(len([r for r in res if r["outcome"] == "compile-error"]), 2)

    def test_new_mode_is_new(self):
        """Та же ошибка была в режиме Сервер, теперь ещё и в тонком клиенте — новая (ключ с режимом)."""
        err = ("{otk_check Справочник.Товары.МодульОбъекта(1,1)}: Ожидается выражение\n"
               "\tА = ; (Проверка: {})\n")
        platform = FakePlatform(after=err.replace("{}", "Сервер") + err.replace("{}", "Тонкий клиент") + COMPILED,
                                before=err.replace("{}", "Сервер") + COMPILED)
        _, res, _ = self.run_check(platform)
        self.assertEqual([r["message"] for r in res if r["outcome"] == "compile-error"],
                         ["Ожидается выражение (Тонкий клиент)" + extended_check.UNCOMPARED])

    def test_ordinary_client_mode(self):
        """Контекст OrdinaryClient у проверяемого модуля — режим -ThickClientOrdinaryApplication и покрытие этого
        контекста; без него режима нет."""
        platform, _, _ = self.run_check()
        checks = [a for a in platform.args if a[0] == "/CheckModules"]
        self.assertTrue(checks)
        self.assertFalse([a for a in checks if "-ThickClientOrdinaryApplication" in a])
        self.assertNotIn("OrdinaryClient", extended_check.covered_contexts(False))
        platform, _, _ = self.run_check(contexts=["Server", "OrdinaryClient"])
        checks = [a for a in platform.args if a[0] == "/CheckModules"]
        self.assertTrue(all("-ThickClientOrdinaryApplication" in a for a in checks))
        self.assertIn("OrdinaryClient", extended_check.covered_contexts(True))
        self.assertIn("Server", extended_check.covered_contexts(True))

    def test_mode_contexts(self):
        """Метка режима в выводе /CheckModules -> контекст исполнения; неизвестная метка контекстом не считается."""
        self.assertEqual(extended_check.mode_contexts(["Сервер", "Толстый клиент (обычное приложение)"]),
                         frozenset({"Server", "OrdinaryClient"}))
        self.assertEqual(extended_check.mode_contexts(["Внешнее соединение", "Веб-клиент", "Тонкий клиент"]),
                         frozenset({"ExternalConnection", "WebClient", "ThinClient"}))
        self.assertEqual(extended_check.mode_contexts(["неизвестный режим"]), frozenset())
        self.assertEqual(extended_check.mode_contexts(None), frozenset())

    def test_canary_code_for_client_contexts(self):
        """Клиентская канарейка — только у модуля с клиентским контекстом: в модуле формы метод без директивы
        компилируется на сервере, и серверная канарейка клиентский контекст не докажет."""
        server = extended_check.canary_code({"contexts": ["Server", "ExternalConnection"]})
        self.assertIn(f"\t{extended_check.CANARY}();", server)
        self.assertNotIn("&НаКлиенте", server)
        for ctx in ("ThinClient", "WebClient", "OrdinaryClient"):
            with self.subTest(ctx):
                code = extended_check.canary_code({"contexts": ["Server", ctx]})
                self.assertIn("&НаКлиенте", code)
                self.assertIn("kc_ОТККанарейкаКлиент", code)

    def test_client_canary_proves_client_context(self):
        """Канарейка в режиме «Тонкий клиент» подтверждает пару ThinClient, хотя серверная канарейка клиента не
        доказывает; покрытие — объединение пар обеих канареек."""
        canaries = canary(FORM_OBJ, "Сервер") + canary(FORM_OBJ, "Тонкий клиент") + canary(OBJ_OBJ, "Сервер")
        covered = {}
        self.run_check(FakePlatform(after=AFTER + canaries, before=BEFORE + canaries),
                       contexts=["Server", "ThinClient"], covered=covered)
        self.assertEqual(covered[FORM_PATH], frozenset({"Server", "ThinClient"}))
        self.assertEqual(covered[OBJ_PATH], frozenset({"Server"}))

    def test_coverage_by_module_and_context(self):
        """Покрытие — по паре «модуль × контекст»: канарейка только в режиме «Сервер» не подтверждает обычный
        клиент, хотя режим запрашивался и модуль скомпилировался в серверном режиме."""
        covered = {}
        self.run_check(contexts=["Server", "OrdinaryClient"], covered=covered)
        self.assertEqual(covered[FORM_PATH], frozenset({"Server"}))
        self.assertEqual(covered[OBJ_PATH], frozenset({"Server"}))
        self.assertNotIn("OrdinaryClient", covered[FORM_PATH])

    def test_coverage_of_all_modes(self):
        """Канарейка есть в каждом режиме — покрыты все контексты, включая обычный клиент и веб-клиент."""
        modes = ("Тонкий клиент", "Веб-клиент", "Сервер", "Внешнее соединение",
                 "Толстый клиент (обычное приложение)")
        canaries = "".join(canary(obj, mode) for obj in (FORM_OBJ, OBJ_OBJ) for mode in modes)
        covered = {}
        self.run_check(FakePlatform(after=AFTER + canaries, before=BEFORE + canaries), contexts=["Server"],
                       covered=covered)
        self.assertEqual(covered[FORM_PATH], extended_check.covered_contexts(True))
        self.assertEqual(covered[OBJ_PATH], extended_check.covered_contexts(True))

    ORDINARY = "Толстый клиент (обычное приложение)"

    def summary(self, res, note, covered):
        """Сводка прогона по результатам extcheck — как собирает runner: run.json с extCheck.coverage и вердикт по
        обязательному OrdinaryClient."""
        run = {"results": res, "notes": [note] if note else [], "extCheck": {
            "modules": sorted(covered), "contexts": sorted(extended_check.covered_contexts(True)),
            "coverage": [{"module": p, "contexts": sorted(c)} for p, c in sorted(covered.items())]}}
        plan = {"base": "a", "head": "b", "source": "cf", "modules": self.entries(["Server", "OrdinaryClient"]),
                "queries": [], "removedModules": [], "metadata": [], "requiredContexts": ["OrdinaryClient"],
                "contextsAvailable": ["Server", "ThinClient", "OrdinaryClient", "ExternalConnection"]}
        return report.build(plan, {"findings": []}, run, {})

    @staticmethod
    def canaries(modes, objs=(FORM_OBJ, OBJ_OBJ)):
        return "".join(canary(obj, mode) for obj in objs for mode in modes)

    def test_context_only_in_after_is_not_compared(self):
        """Режим подтверждён только в «после»: диагностику с «было» не сравнить — она не выдаётся за новую
        (скрыта), пара не закрывается, а обязательный OrdinaryClient даёт код 2."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               f"\tНет(); (Проверка: {self.ORDINARY})\n")
        after = err + self.canaries(("Сервер", self.ORDINARY))
        before = self.canaries(("Сервер",))
        covered = {}
        _, res, note = self.run_check(FakePlatform(after=after, before=before),
                                      contexts=["Server", "OrdinaryClient"], covered=covered)
        self.assertEqual(covered[FORM_PATH], frozenset({"Server"}))
        self.assertEqual(covered[OBJ_PATH], frozenset({"Server"}))
        self.assertFalse([r for r in res if r["outcome"] == "compile-error"])
        msg = ("нет сравнения с «было» (OrdinaryClient): режим подтверждён только в редакции «после» — "
               "его диагностика не выдаётся за новую")
        self.assertEqual({r["module"]: r["message"] for r in res if r["outcome"] == "skipped"},
                         {OBJ_PATH: msg, FORM_PATH: msg + " (скрыто: 1)"})
        s = self.summary(res, note, covered)
        self.assertEqual((s["code"], s["verdict"]), (2, "не покрыто обязательное: OrdinaryClient"))

    def test_empty_module_in_base_is_compared(self):
        """Пустой модуль в «было» — существующий модуль, а не новый: подтверждённый только в «после» режим пару не
        закрывает (иначе обязательный OrdinaryClient получал покрытие без сравнения с «было»)."""
        canaries = self.canaries(("Сервер", self.ORDINARY))
        covered = {}
        _, res, note = self.run_check(FakePlatform(after=canaries, before=self.canaries(("Сервер",))),
                                      contexts=["Server", "OrdinaryClient"], covered=covered,
                                      base_texts={FORM_PATH: "", OBJ_PATH: ""})
        self.assertEqual(covered[FORM_PATH], frozenset({"Server"}))
        self.assertEqual(covered[OBJ_PATH], frozenset({"Server"}))
        self.assertTrue([r for r in res if r["outcome"] == "skipped"])
        self.assertEqual(self.summary(res, note, covered)["code"], 2)

    def test_error_without_comparison_is_marked(self):
        """Ошибка OrdinaryClient в «после» есть, канарейки обычного клиента нет ни в одной редакции: находка
        блокирует (код 1), но помечена «сравнение с «было» недоступно» — новизну её не доказать."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               f"\tНет(); (Проверка: {self.ORDINARY})\n")
        covered = {}
        _, res, note = self.run_check(FakePlatform(after=err + self.canaries(("Сервер",)),
                                                   before=self.canaries(("Сервер",))),
                                      contexts=["Server", "OrdinaryClient"], covered=covered)
        self.assertEqual(covered[FORM_PATH], frozenset({"Server"}))
        self.assertEqual([(r["module"], r["message"]) for r in res if r["outcome"] == "compile-error"],
                         [(FORM_PATH, "Процедура или функция с указанным именем не определена (Нет) "
                                      f"({self.ORDINARY})" + extended_check.UNCOMPARED)])
        self.assertEqual(self.summary(res, note, covered)["code"], 1)

    def test_marker_is_per_mode(self):
        """Одна ошибка в двух режимах: подтверждённый в «было» серверный режим не скрывает отсутствие сравнения у
        обычного клиента — пометка ставится."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               "\tНет(); (Проверка: Сервер)\n"
               f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               f"\tНет(); (Проверка: {self.ORDINARY})\n")
        _, res, _ = self.run_check(FakePlatform(after=err + self.canaries(("Сервер",)),
                                                before=self.canaries(("Сервер",))),
                                   contexts=["Server", "OrdinaryClient"])
        self.assertEqual([r["message"] for r in res if r["outcome"] == "compile-error"],
                         ["Процедура или функция с указанным именем не определена (Нет) "
                          f"(Сервер, {self.ORDINARY})" + extended_check.UNCOMPARED])

    def test_unknown_mode_is_not_compared(self):
        """Метка режима вне словаря: к какому контексту относится ошибка, не понять — сравнение с «было» не
        доказано, пометка ставится."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               "\tНет(); (Проверка: Мобильный клиент)\n")
        self.assertIsNone(extended_check.MODE_CONTEXTS.get("Мобильный клиент"))
        _, res, _ = self.run_check(FakePlatform(after=err + self.canaries(("Сервер",)),
                                                before=self.canaries(("Сервер",))),
                                   contexts=["Server", "OrdinaryClient"])
        self.assertEqual([r["message"] for r in res if r["outcome"] == "compile-error"],
                         ["Процедура или функция с указанным именем не определена (Нет) "
                          "(Мобильный клиент)" + extended_check.UNCOMPARED])

    def test_mode_confirmed_by_before_is_new(self):
        """«Было» подтвердило режим канарейкой, в «после» ошибка остановила компиляцию до канарейки: ошибка новая —
        пометки нет, в «было» все ошибки этого режима были видны."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               f"\tНет(); (Проверка: {self.ORDINARY})\n")
        _, res, _ = self.run_check(FakePlatform(after=err + self.canaries(("Сервер",)),
                                                before=self.canaries(("Сервер", self.ORDINARY))),
                                   contexts=["Server", "OrdinaryClient"])
        self.assertEqual([r["message"] for r in res if r["outcome"] == "compile-error"],
                         [f"Процедура или функция с указанным именем не определена (Нет) ({self.ORDINARY})"])

    def test_context_confirmed_in_both_revisions_is_covered(self):
        """Обе редакции подтвердили оба режима — сравнение доступно, обязательный контекст закрыт, код 0."""
        canaries = self.canaries(("Сервер", self.ORDINARY))
        covered = {}
        _, res, note = self.run_check(FakePlatform(after=canaries, before=canaries),
                                      contexts=["Server", "OrdinaryClient"], covered=covered)
        self.assertEqual(covered[FORM_PATH], frozenset({"Server", "OrdinaryClient"}))
        self.assertEqual(covered[OBJ_PATH], frozenset({"Server", "OrdinaryClient"}))
        self.assertFalse([r for r in res if r["outcome"] == "skipped"])
        self.assertEqual(self.summary(res, note, covered)["code"], 0)

    def test_new_error_of_confirmed_context_is_new(self):
        """Новая ошибка режима, подтверждённого обеими редакциями, — находка (код 1), а не «нет сравнения»."""
        err = (f"{{otk_check {FORM_OBJ}(12,2)}}: Процедура или функция с указанным именем не определена (Нет)\n"
               f"\tНет(); (Проверка: {self.ORDINARY})\n")
        canaries = self.canaries(("Сервер", self.ORDINARY))
        covered = {}
        _, res, note = self.run_check(FakePlatform(after=err + canaries, before=canaries),
                                      contexts=["Server", "OrdinaryClient"], covered=covered)
        self.assertEqual([(r["module"], r["line"], r["message"]) for r in res if r["outcome"] == "compile-error"],
                         [(FORM_PATH, 10, f"Процедура или функция с указанным именем не определена (Нет) "
                                          f"({self.ORDINARY})")])
        self.assertEqual(self.summary(res, note, covered)["code"], 1)

    def test_needed_ordinary(self):
        self.assertTrue(extended_check.needed_ordinary([{"contexts": ["Server", "OrdinaryClient"]}]))
        self.assertFalse(extended_check.needed_ordinary([{"contexts": ["Server"]}, {"contexts": None}, {}]))

    def test_supported(self):
        sup = extended_check.ExtCheck._supported
        self.assertTrue(sup(self.root, layout.DESIGNER, {"kind": "FormModule", "path": FORM_PATH}))
        self.assertTrue(sup(self.root, layout.DESIGNER, {"kind": "ObjectModule", "path": OBJ_PATH}))
        self.assertTrue(sup(self.root, layout.EDT, {"kind": "CommonModule", "path": "CommonModules/М/Module.bsl"}))
        # исходники EDT: путь EDT, описания — в выгрузке экспорта
        self.assertTrue(sup(self.root, layout.EDT, {"kind": "FormModule", "path": EDT_FORM_PATH}))
        self.assertTrue(sup(self.root, layout.EDT, {"kind": "ObjectModule", "path": EDT_OBJ_PATH}))
        self.assertFalse(sup(self.root, layout.EDT, {"kind": "FormModule",
                                                     "path": "Catalogs/Товары/Forms/Нет/Module.bsl"}))
        self.assertFalse(sup(self.root, layout.DESIGNER, {"kind": "CommandModule",
                                                          "path": "Catalogs/Товары/Commands/К/Ext/CommandModule.bsl"}))
        (self.root / "Catalogs/Товары/Forms/Форма.xml").write_text("<Form><FormType>Ordinary</FormType></Form>",
                                                                   encoding="utf-8")
        self.assertFalse(sup(self.root, layout.DESIGNER, {"kind": "FormModule", "path": FORM_PATH}))

    def test_edt_paths(self):
        """Исходники EDT: модули — путями EDT, находки возвращаются к записям плана (пути EDT), сборка — та же."""
        entries = [{"path": EDT_FORM_PATH, "owner": "Справочник.Товары.Форма.Форма", "kind": "FormModule",
                    "status": "M", "reason": "changed"},
                   {"path": EDT_OBJ_PATH, "owner": "Справочник.Товары", "kind": "ObjectModule", "status": "M",
                    "reason": "changed"}]
        head = {EDT_FORM_PATH: FORM_HEAD, EDT_OBJ_PATH: OBJ.replace("Тогда", "Тогда\n\t\tХ = Новый Х;")}
        base = {EDT_FORM_PATH: FORM_BASE, EDT_OBJ_PATH: OBJ}
        res, _ = extended_check.run(FakePlatform(), self.tmp / "run", self.root, entries,
                                    read_base=lambda e: base[e["path"]],
                              read_head=lambda e: head[e["path"]], read_xml=lambda e: None, lay=layout.EDT)
        errors = {(r["module"], r["line"]) for r in res if r["outcome"] == "compile-error"}
        self.assertEqual(errors, {(EDT_FORM_PATH, 10), (EDT_OBJ_PATH, 3)})
        self.assertTrue((self.tmp / "run/extcheck-after/Catalogs/Товары/Forms/Форма/Ext/Form/Module.bsl").is_file())

    def test_managed_form_edt(self):
        """Признак управляемой формы по исходникам EDT (контрольные дефекты строятся до экспорта)."""
        edt = self.tmp / "edt"
        files = {"Catalogs/Товары/Товары.mdo": EDT_OWNER.format(form_type=""),
                 "Catalogs/Товары/Forms/Форма/Form.form": "<form:Form/>"}
        for rel, text in files.items():
            (edt / rel).parent.mkdir(parents=True, exist_ok=True)
            (edt / rel).write_text(text, encoding="utf-8")
        a = layout.EDT.parse(EDT_FORM_PATH)
        self.assertTrue(extended_check.managed_form(edt, a, layout.EDT))
        (edt / "Catalogs/Товары/Товары.mdo").write_text(
            EDT_OWNER.format(form_type="<formType>Ordinary</formType>"), encoding="utf-8")
        self.assertFalse(extended_check.managed_form(edt, a, layout.EDT))


if __name__ == "__main__":
    unittest.main()
