"""BSL LS до/после: новое замечание попадает в находки, старое — нет.

Тесты на настоящем анализаторе — по переменной OTK_TEST_BSLLS (путь к bsl-language-server); не задана — пропуск.
Правилам по справке платформы — установленная платформа или копия .hbk в OTK_TEST_HELP (Linux без платформы).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c import analyzers
from otk_1c.analyzers import bsl_ls
from otk_1c.sources import layout

EXE = Path(os.environ.get("OTK_TEST_BSLLS") or "нет-OTK_TEST_BSLLS")
BEFORE = "Процедура П(А) Экспорт\n\tА = А;\n\tПока Истина Цикл\n\tКонецЦикла;\nКонецПроцедуры\n"
AFTER = ("Процедура П(А) Экспорт\n\tА = А;\n\tПока Истина Цикл\n\tКонецЦикла;\nКонецПроцедуры\n\n"
         "Процедура Новая(Б) Экспорт\n\tБ = Б;\n\tВозврат 1;\nКонецПроцедуры\n")


def uri(p):
    """Путь файла в отчёте BSL LS: file:///D:/… в Windows, file:///home/… в Linux."""
    return "file:///" + Path(p).resolve().as_posix().lstrip("/")


def diag_each(work):
    """Отчёт заглушки анализатора: по замечанию SelfAssign в первой строке каждого модуля каталога запуска."""
    diag = {"code": "SelfAssign", "message": "м", "range": {"start": {"line": 0}}}
    return {"fileinfos": [{"path": uri(p), "diagnostics": [diag]}
                          for p in (work / "src").rglob("*.bsl")]}


class TestLayout(unittest.TestCase):
    def test_help_dir_in_config(self):
        """Справка платформы передаётся BSL LS (v8platform.binPath): во встроенную конфигурацию и в копию
        проектной без своего ключа; проектная со своим ключом и запуск без справки — как есть."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        try:
            def bin_path(p):
                return json.loads(Path(p).read_text(encoding="utf-8")).get("v8platform", {}).get("binPath")
            self.assertEqual(bin_path(bsl_ls.analyzer_config(tmp, None, "/hbk/8.3.27")), "/hbk/8.3.27")
            self.assertIsNone(bin_path(bsl_ls.analyzer_config(tmp, None, None)))
            project = tmp / "project.json"
            project.write_text('{"language": "en", "v8platform": {"targetVersion": "8.3.21"}}', encoding="utf-8")
            copy = bsl_ls.analyzer_config(tmp, project, "/hbk/8.3.27")
            self.assertNotEqual(copy, project.resolve())
            self.assertEqual(json.loads(copy.read_text(encoding="utf-8")),
                             {"language": "en", "v8platform": {"targetVersion": "8.3.21", "binPath": "/hbk/8.3.27"}})
            self.assertEqual(bsl_ls.analyzer_config(tmp, project, None), project.resolve())
            project.write_text('{"v8platform": {"binPath": "/свой"}}', encoding="utf-8")
            self.assertEqual(bsl_ls.analyzer_config(tmp, project, "/hbk/8.3.27"), project.resolve())
        finally:
            rmtree_force(tmp)

    def test_metadata_files(self):
        self.assertEqual(bsl_ls.metadata_files("CommonForms/Ф/Ext/Form/Module.bsl"),
                         ["Configuration.xml", "CommonForms/Ф.xml", "CommonForms/Ф/Ext/Form.xml"])
        self.assertEqual(bsl_ls.metadata_files("Documents/Д/Forms/ФормаДокумента/Ext/Form/Module.bsl"),
                         ["Configuration.xml", "Documents/Д.xml", "Documents/Д/Forms/ФормаДокумента.xml",
                          "Documents/Д/Forms/ФормаДокумента/Ext/Form.xml"])
        self.assertEqual(bsl_ls.metadata_files("CommonCommands/К/Ext/CommandModule.bsl"),
                         ["Configuration.xml", "CommonCommands/К.xml"])
        self.assertEqual(bsl_ls.metadata_files("Ext/SessionModule.bsl"), ["Configuration.xml"])

    def test_metadata_files_edt(self):
        edt = layout.EDT
        cfg = "Configuration/Configuration.mdo"
        self.assertEqual(bsl_ls.metadata_files("Documents/Д/Forms/ФормаДокумента/Module.bsl", edt),
                         [cfg, "Documents/Д/Д.mdo", "Documents/Д/Forms/ФормаДокумента/Form.form"])
        self.assertEqual(bsl_ls.metadata_files("CommonForms/Ф/Module.bsl", edt),
                         [cfg, "CommonForms/Ф/Ф.mdo", "CommonForms/Ф/Form.form"])
        self.assertEqual(bsl_ls.metadata_files("Documents/Д/ObjectModule.bsl", edt), [cfg, "Documents/Д/Д.mdo"])
        self.assertEqual(bsl_ls.metadata_files("Configuration/SessionModule.bsl", edt), [cfg])

    def test_protected_before_skipped(self):
        """EDT: редакции — проектами (after/src/…), защищённая «до» (двоичный .bsl) не отдаётся в анализ, как
        отсутствующая — замечание «после» новое."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        seen = []

        def analyze(exe, cfg, work, timeout):
            seen.extend(sorted(p.relative_to(work / "src").as_posix() for p in (work / "src").rglob("*.bsl")))
            return diag_each(work), None
        mods = [{"path": "CommonModules/А/Module.bsl"}, {"path": "CommonModules/Б/Module.bsl"}]
        protected = b"\xff\xff\xff\x7f\x00\x01 binary".decode("utf-8-sig", "replace")  # как читает ОТК
        def before(rel):
            return protected if "/А/" in rel else "А = 1;"
        try:
            with mock.patch.object(bsl_ls, "_analyze", analyze):
                findings, _ = bsl_ls.run(EXE, tmp, mods, before, lambda rel: "А = 1;", lay=layout.EDT)
        finally:
            rmtree_force(tmp)
        self.assertEqual(seen, ["after/src/CommonModules/А/Module.bsl", "after/src/CommonModules/Б/Module.bsl",
                                "before/src/CommonModules/Б/Module.bsl"])
        self.assertEqual([(f["check"], f["module"], f["line"]) for f in findings],
                         [("bslls:SelfAssign", "CommonModules/А/Module.bsl", 1)])

    def test_builtin_config(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        try:
            data = json.loads(bsl_ls.builtin_config(tmp / "c.json").read_text(encoding="utf-8"))
        finally:
            rmtree_force(tmp)
        params = data["diagnostics"]["parameters"]
        self.assertEqual(data["diagnostics"]["mode"], "only")
        self.assertTrue(bsl_ls.FAIL_CODES <= set(params))
        self.assertNotIn("MissingCommonModuleMethod", params)
        self.assertNotIn("ServerSideExportFormMethod", params)
        self.assertNotIn("LineLength", params)

    def test_pack(self):
        """Пакеты по байтам, крупное — первым; единица больше бюджета — пакет из неё одной."""
        sizes = {"A": 9, "B": 6, "C": 4, "D": 3, "E": 1}
        self.assertEqual(bsl_ls.pack(sizes, 10), [["A", "E"], ["B", "C"], ["D"]])
        self.assertEqual(bsl_ls.pack({"X": 25, "Y": 2}, 10), [["X"], ["Y"]])
        self.assertEqual(bsl_ls.pack(sizes, 100), [["A", "B", "C", "D", "E"]])

    def test_oom_split(self):
        """OutOfMemoryError — пакет делится пополам до объекта; объект, не влезающий один, — не проверен, остальные
        проверены; модули одного объекта и обе редакции модуля — в одном пакете."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        seen = []

        def analyze(exe, cfg, work, timeout):
            files = sorted(p.relative_to(work / "src").as_posix() for p in (work / "src").rglob("*.bsl"))
            seen.append(files)
            if len(files) > 4 or any("Б/" in f for f in files):
                return None, "Caused by: java.lang.OutOfMemoryError: Java heap space"
            return diag_each(work), None
        mods = ([{"path": f"CommonModules/{n}/Ext/Module.bsl"} for n in "АБВ"]
                + [{"path": "Documents/Г/Ext/ObjectModule.bsl"}, {"path": "Documents/Г/Ext/ManagerModule.bsl"}])
        try:
            with mock.patch.object(bsl_ls, "_analyze", analyze):
                findings, info = bsl_ls.run(EXE, tmp, mods, lambda rel: "А = 1;", lambda rel: "А = 1;")
        finally:
            rmtree_force(tmp)
        self.assertEqual(findings, [])
        self.assertEqual(info["error"], "не проверено модулей 1 из 5: "
                                        "Caused by: java.lang.OutOfMemoryError: Java heap space")
        self.assertEqual(info["batches"], 5)  # все -> (Г, Б) + (А, В) -> Г + Б
        for side in ("after", "before"):
            self.assertIn([f"{side}/Documents/Г/Ext/ManagerModule.bsl", f"{side}/Documents/Г/Ext/ObjectModule.bsl"],
                          seen)

    def test_sides_separately(self):
        """Редакции — отдельными запусками: BSL LS читает метаданные одной конфигурации рабочего каталога, в общем
        запуске правила, которым нужен вид модуля, у «до» молчали (ложное «новое»). «До» — только модулям с
        замечаниями «после»; нет замечаний — один запуск."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        seen = []

        def analyze(exe, cfg, work, timeout):
            files = sorted(p.relative_to(work / "src").as_posix() for p in (work / "src").rglob("*.bsl"))
            seen.append(files)
            return {"fileinfos": [i for i in diag_each(work)["fileinfos"] if "/Т/" in i["path"]]}, None
        mods = [{"path": "Catalogs/Т/Ext/ObjectModule.bsl"}, {"path": "Catalogs/У/Ext/ObjectModule.bsl"}]
        try:
            with mock.patch.object(bsl_ls, "_analyze", analyze):
                findings, _ = bsl_ls.run(EXE, tmp, mods, lambda rel: "А = 1;", lambda rel: "А = 1;")
                seen_one = len(seen)
                bsl_ls.run(EXE, tmp, mods[1:], lambda rel: "А = 1;", lambda rel: "А = 1;")
        finally:
            rmtree_force(tmp)
        self.assertEqual(seen[:seen_one], [["after/Catalogs/Т/Ext/ObjectModule.bsl",
                                            "after/Catalogs/У/Ext/ObjectModule.bsl"],
                                           ["before/Catalogs/Т/Ext/ObjectModule.bsl"]])
        self.assertEqual(findings, [])                     # замечание есть у обеих редакций — старое
        self.assertEqual(len(seen) - seen_one, 1)          # У без замечаний — без запуска «до»

    def test_edt_batches(self):
        """EDT пакетами: в каждом пакете редакция — проект (after/src) с Configuration.mdo, иначе BSL LS молча
        не читает метаданные."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        seen = []

        def analyze(exe, cfg, work, timeout):
            seen.append(sorted(p.relative_to(work / "src").as_posix()
                               for p in (work / "src").rglob("*") if p.is_file()))
            return diag_each(work), None
        mods = [{"path": "CommonModules/А/Module.bsl"}, {"path": "Documents/Г/ObjectModule.bsl"}]
        try:
            with mock.patch.object(bsl_ls, "_analyze", analyze):
                _, info = bsl_ls.run(EXE, tmp, mods, lambda rel: "А = 1;", lambda rel: "А = 1;", batch_bytes=1,
                                    lay=layout.EDT)
        finally:
            rmtree_force(tmp)
        self.assertEqual(info["batches"], 2)
        self.assertEqual(len(seen), 4)        # пакет — по запуску на редакцию
        for files in seen:
            side = files[0].split("/")[0]
            self.assertIn(f"{side}/src/Configuration/Configuration.mdo", files)
        self.assertIn("after/src/Documents/Г/Г.mdo", sum(seen, []))

    def test_dot_break(self):
        """ParseError в методе с переносом после точки — дефект парсера BSL LS, не выдаётся;
        ошибка в другом методе и точка в строке-литерале, комментарии или тексте запроса — выдаётся."""
        text = ["Процедура А()",
                "\tЕсли Х = Модуль.",
                "\t\tМетод() Тогда",
                "\tКонецЕсли;",
                "КонецПроцедуры",
                "",
                "Процедура Б()",
                "\tС = \"Конец фразы.\"; // и комментарий.",
                "\tТекст = \"ВЫБРАТЬ Т.",
                "\t|ИЗ Т.",
                "\t|\";",
                "\tЕсли Тогда",
                "КонецПроцедуры"]
        self.assertTrue(bsl_ls.dot_break(text, 2))
        self.assertTrue(bsl_ls.dot_break(text, 4))     # каскад до конца метода
        self.assertFalse(bsl_ls.dot_break(text, 11))
        self.assertFalse(bsl_ls.dot_break([], 0))

    def test_this_object_property(self):
        """ThisObjectAssign — ошибка только там, где ЭтотОбъект — свойство модуля."""
        assign = ["Процедура П(Форма) Экспорт", "\tЭтотОбъект = Форма.ЭтотОбъект;", "КонецПроцедуры"]
        self.assertFalse(bsl_ls.this_object_property("Module", assign, 1))         # общий модуль
        self.assertFalse(bsl_ls.this_object_property("ManagerModule", assign, 1))
        self.assertTrue(bsl_ls.this_object_property("FormModule", assign, 1))
        self.assertTrue(bsl_ls.this_object_property("ObjectModule", assign, 1))
        param = ["Процедура П(Знач А,", "\t\tЭтотОбъект = Неопределено)", "\tЕсли ЭтотОбъект = Неопределено Тогда",
                 "\t\tЭтотОбъект = Объект;", "\tКонецЕсли;", "КонецПроцедуры"]
        self.assertFalse(bsl_ls.this_object_property("FormModule", param, 3))
        local = ["Процедура П()", "\tПерем А, ЭтотОбъект;", "\tЭтотОбъект = 1;", "КонецПроцедуры"]
        self.assertFalse(bsl_ls.this_object_property("ObjectModule", local, 2))
        module = ["Перем ЭтотОбъект;", "", "Процедура П()", "\tЭтотОбъект = 1;", "КонецПроцедуры"]
        self.assertFalse(bsl_ls.this_object_property("ObjectModule", module, 3))
        other = ["Процедура П(ЭтотОбъектФормы)", "\tПерем Объект; // ЭтотОбъект", "\tЭтотОбъект = 1;", "КонецПроцедуры"]
        self.assertTrue(bsl_ls.this_object_property("FormModule", other, 2))

    def test_known_defects_dropped(self):
        """run: ложные ParseError и ThisObjectAssign не попадают в находки, их число — в info["dropped"]."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        text = ("Процедура П(Форма) Экспорт\n\tЭтотОбъект = Форма.ЭтотОбъект;\n\tА = Модуль.\n\t\tМетод();\n"
                "КонецПроцедуры\n")

        def analyze(exe, cfg, work, timeout):
            path = uri(work / "src/after/CommonModules/А/Ext/Module.bsl")
            diags = [{"code": c, "message": c, "range": {"start": {"line": n}}}
                     for c, n in (("ThisObjectAssign", 1), ("ParseError", 3), ("ParseError", 4), ("SelfAssign", 1))]
            return {"fileinfos": [{"path": path, "diagnostics": diags}]}, None
        try:
            with mock.patch.object(bsl_ls, "_analyze", analyze):
                findings, info = bsl_ls.run(EXE, tmp, [{"path": "CommonModules/А/Ext/Module.bsl", "status": "A"}],
                                           lambda rel: None, lambda rel: text if rel.endswith(".bsl") else None)
        finally:
            rmtree_force(tmp)
        self.assertEqual([f["check"] for f in findings], ["bslls:SelfAssign"])
        self.assertEqual(info["dropped"], {"ThisObjectAssign": 1, "ParseError": 2})

    def test_drop_known_metadata(self):
        """QueryToMissingMetadata остаётся, только если объекта нет в индексе (в т.ч. основной конфигурации)."""
        index = {"objects": {"Catalogs": {"филиалы": {"name": "Филиалы"}}}}

        def f(obj, check="bslls:QueryToMissingMetadata"):
            return {"check": check, "message": f'Исправьте обращение к несуществующему метаданному "{obj}" в запросе'}
        kept = bsl_ls.drop_known([f("Справочник.Филиалы"), f("Catalog.Филиалы"), f("Справочник.НетТакого"),
                                  f("Справочник.Филиалы", "bslls:Typo")], index)
        self.assertEqual([k["message"].split('"')[1] for k in kept], ["Справочник.НетТакого", "Справочник.Филиалы"])

    def test_drop_known_common_method(self):
        """MissingCommonModuleMethod (заимствованный общий модуль в раскладке) остаётся, только если экспортного
        метода нет в индексе (в т.ч. основной конфигурации)."""
        index = {"objects": {}, "commonModules": {"общегоназначения": {"exports": {"значениереквизитаобъекта": []}}}}

        def f(text):
            return {"check": "bslls:MissingCommonModuleMethod", "message": text}
        kept = bsl_ls.drop_known([
            f("Метод ЗначениеРеквизитаОбъекта общего модуля ОбщегоНазначения не существует"),
            f("The method ЗначениеРеквизитаОбъекта of ОбщегоНазначения common module does not exist"),
            f("Метод НетТакого общего модуля ОбщегоНазначения не существует"),
            f("Метод Метод общего модуля НетМодуля не существует")], index)
        self.assertEqual([k["message"].split()[1] for k in kept], ["НетТакого", "Метод"])


@unittest.skipUnless(EXE.exists(), "OTK_TEST_BSLLS не задана или файла нет")
class TestBslLs(unittest.TestCase):
    def test_only_new_diagnostics(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        try:
            entry = {"path": "CommonModules/М/Ext/Module.bsl"}
            findings, info = bsl_ls.run(EXE, tmp, [entry], lambda rel: BEFORE if rel.endswith(".bsl") else None,
                                       lambda rel: AFTER if rel.endswith(".bsl") else None)
        finally:
            rmtree_force(tmp)
        self.assertEqual((info.get("modules"), info.get("config")), (1, "встроенный набор"), info)
        # только новое и только из набора: старое «А = А» и пустой цикл (стиль, не в наборе) — нет
        got = sorted((f["check"], f["line"], f["severity"]) for f in findings)
        self.assertEqual(got, [("bslls:ProcedureReturnsValue", 9, "error"), ("bslls:SelfAssign", 8, "warning")])

    def test_dot_break_real(self):
        """На настоящем BSL LS: перенос после точки — ParseError анализатора снимается, настоящая
        ошибка разбора в другом методе остаётся."""
        after = ("Процедура А() Экспорт\n\tЕсли Модуль.\n\t\tМетод() Тогда\n\tКонецЕсли;\nКонецПроцедуры\n\n"
                 "Процедура Б() Экспорт\n\tЕсли Истина Тогда\nКонецПроцедуры\n")
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        try:
            entry = {"path": "CommonModules/М/Ext/Module.bsl"}
            findings, info = bsl_ls.run(EXE, tmp, [entry], lambda rel: BEFORE if rel.endswith(".bsl") else None,
                                       lambda rel: after if rel.endswith(".bsl") else None)
        finally:
            rmtree_force(tmp)
        self.assertGreater(info.get("dropped", {}).get("ParseError", 0), 0, info)
        lines = {f["line"] for f in findings if f["check"] == "bslls:ParseError"}
        self.assertTrue(lines and min(lines) >= 7, findings)

    def test_metadata_rule_old_stays_old(self):
        """Правило с видом модуля (EventHandlerInvalidSignature): над старым обработчиком дописан комментарий —
        замечание старое; в добавленном модуле — новое (метаданных хватает обеим редакциям)."""
        head = ('<?xml version="1.0" encoding="UTF-8"?>\n<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" '
                'version="2.20">\n')
        meta = {"Configuration.xml": head + '<Configuration uuid="104068ca-0733-4743-ac4a-2f3ec96140e6"><Properties>'
                '<Name>Т</Name></Properties><ChildObjects><Catalog>Т</Catalog></ChildObjects></Configuration>'
                '</MetaDataObject>\n',
                "Catalogs/Т.xml": head + '<Catalog uuid="86efa252-6629-47de-b28d-ad7959c9cde1"><Properties>'
                '<Name>Т</Name></Properties></Catalog></MetaDataObject>\n'}
        proc = "Процедура ОбработкаЗаполнения(ДанныеЗаполнения, СтандартнаяОбработка)\n\tВозврат;\nКонецПроцедуры\n"
        path = "Catalogs/Т/Ext/ObjectModule.bsl"

        def reader(text):
            return lambda rel: text if rel == path else meta.get(rel)
        help_dir = analyzers.platform_help({"helpDir": os.environ.get("OTK_TEST_HELP")})
        if not help_dir:
            self.skipTest("правилу нужна справка платформы: установленная или копия .hbk в OTK_TEST_HELP")
        tmp = Path(tempfile.mkdtemp(prefix="otk-bslls-"))
        try:
            old, _ = bsl_ls.run(EXE, tmp, [{"path": path}], reader(proc), reader("// правка\n" + proc),
                               help_dir=help_dir)
            new, _ = bsl_ls.run(EXE, tmp, [{"path": path, "status": "A"}], reader(None), reader(proc),
                               help_dir=help_dir)
        finally:
            rmtree_force(tmp)
        self.assertEqual(old, [])
        self.assertEqual([f["check"] for f in new], ["bslls:EventHandlerInvalidSignature"])


if __name__ == "__main__":
    unittest.main()
