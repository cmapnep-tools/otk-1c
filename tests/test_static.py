"""Статические проверки на синтетической выгрузке: дефекты каталога ловятся, перекрытые имена — нет."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
from otk_1c.gate import collect, platform_api, static
from otk_1c.sources import index

CONFIG = """<MetaDataObject><Configuration><Properties><ScriptVariant>Russian</ScriptVariant></Properties>
<ChildObjects><Catalog>Номенклатура</Catalog><Enum>ВидыЗаказов</Enum><Document>Заказ</Document>
<CommonModule>Общий</CommonModule><EventSubscription>ПриЗаписиЗаказа</EventSubscription></ChildObjects>
</Configuration></MetaDataObject>"""
NS = 'xmlns="http://v8.1c.ru/8.3/MDClasses"'
CATALOG = f"""<MetaDataObject {NS}><Catalog><Properties><Name>Номенклатура</Name></Properties><ChildObjects>
<Attribute><Properties><Name>Артикул</Name></Properties></Attribute></ChildObjects></Catalog></MetaDataObject>"""
PREDEFINED = """<PredefinedData xmlns="http://v8.1c.ru/8.3/xcf/predef"><Item><Name>Услуга</Name></Item></PredefinedData>"""
ENUM = f"""<MetaDataObject {NS}><Enum><Properties><Name>ВидыЗаказов</Name></Properties><ChildObjects>
<EnumValue><Properties><Name>Розница</Name></Properties></EnumValue></ChildObjects></Enum></MetaDataObject>"""
DOCUMENT = f"""<MetaDataObject {NS}><Document><Properties><Name>Заказ</Name></Properties><ChildObjects>
<Form>ФормаДокумента</Form></ChildObjects></Document></MetaDataObject>"""
FORM_XML = """<Form><Events><Event name="OnOpen">ПриОткрытии</Event></Events>
<Attributes><Attribute name="Документы"/></Attributes></Form>"""
FLAGS = ('<MetaDataObject><CommonModule><Properties><Global>false</Global><Server>true</Server>'
         '<ExternalConnection>false</ExternalConnection></Properties></CommonModule></MetaDataObject>')
SUBSCRIPTION = (f'<MetaDataObject {NS}><EventSubscription><Properties><Name>ПриЗаписиЗаказа</Name>'
                '<Handler>CommonModule.Общий.ПриЗаписи</Handler></Properties></EventSubscription></MetaDataObject>')

COMMON_BASE = """Функция Ф(А, Б = 1) Экспорт
	Возврат А;
КонецФункции

Процедура ПриЗаписи(Источник, Отказ) Экспорт
КонецПроцедуры

Функция Старая() Экспорт
КонецФункции
"""
COMMON_HEAD = """Функция Ф(А, Б = 1) Экспорт
	Возврат А;
КонецФункции
"""
CALLER = """Процедура Вызов()
	Х = Общий.Старая();
КонецПроцедуры
"""
FORM_BASE = """&НаКлиенте
Процедура ПриОткрытии(Отказ)
КонецПроцедуры
"""
FORM_HEAD = """&НаКлиенте
Процедура ПриОткрытииНовое(Отказ)
	Н = Справочники.Номенклотура.ПустаяСсылка();
	У = Справочники.Номенклатура.Услугаа;
	В = Перечисления.ВидыЗаказов.Оптт;
	Ф = ПолучитьФорму("Документ.Заказ.Форма.ФормаДокументаа");
	П = ПредопределенноеЗначение("Перечисление.ВидыЗаказов.Опт");
	З = Общий.НетТакого(1);
	Л = Общий.Ф(1, 2, 3);
	Т = "ВЫБРАТЬ ЗНАЧЕНИЕ(Справочник.Номенклатура.Товарр) КАК П ИЗ Справочник.Номенклатура КАК Н";
	// перекрытые и корректные — без находок
	Д = Документы.Параметры;
	Ок = Справочники.Номенклатура.Услуга;
	Ок2 = Перечисления.ВидыЗаказов.Розница;
	Ок3 = Справочники.ТипВсеСсылки();
	Ок4 = Общий.Ф(1);
	Ок5 = ПолучитьФорму("Документ.Заказ.ФормаСписка");
	Ок6 = ПолучитьФорму("Документ." + Имя);
КонецПроцедуры
"""


def write(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))


def run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class TestStatic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-static-"))
        r, src = cls.tmp, cls.tmp / "src" / "cf"
        run(r, "init", "-q", "-b", "master")
        run(r, "config", "user.email", "t@t")
        run(r, "config", "user.name", "t")
        files = {
            "Configuration.xml": CONFIG, "Catalogs/Номенклатура.xml": CATALOG,
            "Catalogs/Номенклатура/Ext/Predefined.xml": PREDEFINED, "Enums/ВидыЗаказов.xml": ENUM,
            "Documents/Заказ.xml": DOCUMENT, "Documents/Заказ/Forms/ФормаДокумента/Ext/Form.xml": FORM_XML,
            "Documents/Заказ/Forms/ФормаДокумента/Ext/Form/Module.bsl": FORM_BASE,
            "CommonModules/Общий.xml": FLAGS, "CommonModules/Общий/Ext/Module.bsl": COMMON_BASE,
            "EventSubscriptions/ПриЗаписиЗаказа.xml": SUBSCRIPTION,
            "DataProcessors/Вызывающая/Ext/ObjectModule.bsl": CALLER,
        }
        for rel, text in files.items():
            write(src, rel, text)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "base")
        write(src, "CommonModules/Общий/Ext/Module.bsl", COMMON_HEAD)
        write(src, "Documents/Заказ/Forms/ФормаДокумента/Ext/Form/Module.bsl", FORM_HEAD)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "head")
        cls.plan = collect.collect(r, "HEAD~1", "HEAD", "src/cf", ["Server", "ThinClient"])
        cls.ix = index.build(src)

        def read(rel):
            p = src / rel
            return p.read_bytes().decode("utf-8-sig") if p.exists() else None

        cls.repo, cls.read = r, staticmethod(read)
        cls.findings = static.run(r, cls.plan, cls.ix, read)
        cls.deps, cls.dep_findings = static.dependents(src, cls.plan, cls.ix)
        cls.messages = [f["message"] for f in cls.findings]

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def has(self, text, severity="error"):
        hits = [f for f in self.findings + self.dep_findings if text in f["message"] and f["severity"] == severity]
        self.assertTrue(hits, f"нет находки «{text}» ({severity}); есть: {self.messages}")

    def test_metadata_off(self):
        """[checks] metadata = "off": находок правил нет, platform-api решается своим режимом."""
        self.assertTrue(self.findings)
        self.assertEqual(static.run(self.repo, self.plan, self.ix, self.read, metadata=False), [])

        class Api:
            def check(self, kind, san_lines, lines, emit, known, conditions, contexts, variables=None):
                emit("platform-api", "error", 1, "нет такого метода")

        got = static.run(self.repo, self.plan, self.ix, self.read, Api(), metadata=False)
        self.assertEqual({f["check"] for f in got}, {"platform-api"})

    def test_platform_names(self):
        """Стандартные формы — из словаря имён платформы: без ФормаСписка в нём — находка form-name."""
        names = platform_api.platform_names()
        names["forms"] = names["forms"] - {"формасписка"}
        got = static.run(self.repo, self.plan, self.ix, self.read, names=names)
        self.assertIn("Нет формы: Документ.Заказ.ФормаСписка", [f["message"] for f in got])
        self.assertNotIn("Нет формы: Документ.Заказ.ФормаСписка", self.messages)

    def test_index(self):
        self.assertEqual(self.ix["objects"]["Catalogs"]["номенклатура"]["predefined"], ["Услуга"])
        self.assertEqual(self.ix["commonModules"]["общий"]["exports"]["ф"], ["Ф", 1, 2])

    def test_metadata_refs(self):
        self.has("Объект не найден: Справочники.Номенклотура")
        self.has("Нет предопределённого элемента: Справочники.Номенклатура.Услугаа")
        self.has("Нет значения перечисления: Перечисления.ВидыЗаказов.Оптт")
        self.has("ПредопределенноеЗначение: нет Перечисление.ВидыЗаказов.Опт")
        self.has("ЗНАЧЕНИЕ: нет Справочник.Номенклатура.Товарр")

    def test_forms_and_methods(self):
        self.has("Нет формы: Документ.Заказ.Форма.ФормаДокументаа")
        self.has("Нет экспортного метода: Общий.НетТакого")
        self.has("Слишком много параметров: Общий.Ф")

    def test_handlers(self):
        self.has("Обработчик из Form.xml не найден в модуле формы: ПриОткрытии")
        self.has("Обработчик CommonModule.Общий.ПриЗаписи не найден")

    def test_dependents(self):
        self.assertIn("DataProcessors/Вызывающая/Ext/ObjectModule.bsl", self.deps)
        self.has("Вызов удалённого экспортного метода Общий.Старая")

    def test_no_false_positives(self):
        noisy = [m for m in self.messages if any(x in m for x in
                 ("Документы.Параметры", "Номенклатура.Услуга ", "Розница", "ТипВсеСсылки", "ФормаСписка",
                  "Документ.\""))]
        self.assertEqual(noisy, [])
        fails = [f for f in self.findings if f["severity"] == "error"]
        self.assertEqual(len(fails), 10, [f["message"] for f in fails])


EN_CONFIG = CONFIG.replace("<ScriptVariant>Russian", "<ScriptVariant>English").replace(
    "ПриЗаписиЗаказа", "OnWriteOrder").replace("Номенклатура", "Products").replace("ВидыЗаказов", "OrderKinds").replace(
    "Заказ", "Order").replace("Общий", "Common")
EN_FORM_XML = """<Form><Events><Event name="OnOpen">OnOpen</Event></Events>
<Attributes><Attribute name="Docs"/></Attributes></Form>"""
EN_COMMON_BASE = """Function F(A, B = 1) Export
	Return A;
EndFunction

Procedure OnWrite(Source, Cancel) Export
EndProcedure

Function Old() Export
EndFunction
"""
EN_COMMON_HEAD = """Function F(A, B = 1) Export
	Return A;
EndFunction
"""
EN_CALLER = """Procedure Caller()
	X = Common.Old();
EndProcedure
"""
EN_FORM_BASE = """&AtClient
Procedure OnOpen(Cancel)
EndProcedure
"""
# код английской конфигурации — на обоих языках: встроенные имена платформа принимает в любом варианте
EN_FORM_HEAD = """&AtClient
Procedure OnOpenNew(Cancel)
	N = Catalogs.Prodcts.EmptyRef();
	S = Catalogs.Products.Servce;
	K = Enums.OrderKinds.Wholesal;
	F = GetForm("Document.Order.Form.DocumentFormm");
	P = PredefinedValue("Enum.OrderKinds.Wholesale");
	Z = Common.NoSuch(1);
	L = Common.F(1, 2, 3);
	T = "SELECT VALUE(Catalog.Products.Goodd) AS P FROM Catalog.Products AS N";
	R = Справочники.Продукты.ПустаяСсылка();
	// корректные — без находок
	If Docs = Undefined Then
		D = Documents.Order.EmptyRef();
	EndIf;
	Ok = Catalogs.Products.Service;
	Ok2 = Enums.OrderKinds.Retail;
	Ok3 = Catalogs.AllRefsType();
	Ok4 = Common.F(1);
	Ok5 = GetForm("Document.Order.ListForm");
	Ok6 = Справочники.Products.Service;
EndProcedure
"""


class TestStaticEnglish(unittest.TestCase):
    """Английский вариант встроенного языка (6.9): те же дефекты, ссылки на обоих языках."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-static-en-"))
        r, src = cls.tmp, cls.tmp / "src" / "cf"
        run(r, "init", "-q", "-b", "master")
        run(r, "config", "user.email", "t@t")
        run(r, "config", "user.name", "t")
        files = {
            "Configuration.xml": EN_CONFIG,
            "Catalogs/Products.xml": CATALOG.replace("Номенклатура", "Products").replace("Артикул", "Code2"),
            "Catalogs/Products/Ext/Predefined.xml": PREDEFINED.replace("Услуга", "Service"),
            "Enums/OrderKinds.xml": ENUM.replace("ВидыЗаказов", "OrderKinds").replace(
                "<EnumValue><Properties><Name>Розница</Name></Properties></EnumValue>",
                "<EnumValue><Properties><Name>Retail</Name></Properties></EnumValue>"),
            "Documents/Order.xml": DOCUMENT.replace("Заказ", "Order").replace("ФормаДокумента", "DocumentForm"),
            "Documents/Order/Forms/DocumentForm/Ext/Form.xml": EN_FORM_XML,
            "Documents/Order/Forms/DocumentForm/Ext/Form/Module.bsl": EN_FORM_BASE,
            "CommonModules/Common.xml": FLAGS, "CommonModules/Common/Ext/Module.bsl": EN_COMMON_BASE,
            "EventSubscriptions/OnWriteOrder.xml": SUBSCRIPTION.replace("ПриЗаписиЗаказа", "OnWriteOrder").replace(
                "CommonModule.Общий.ПриЗаписи", "CommonModule.Common.OnWrite"),
            "DataProcessors/Caller/Ext/ObjectModule.bsl": EN_CALLER,
        }
        for rel, text in files.items():
            write(src, rel, text)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "base")
        write(src, "CommonModules/Common/Ext/Module.bsl", EN_COMMON_HEAD)
        write(src, "Documents/Order/Forms/DocumentForm/Ext/Form/Module.bsl", EN_FORM_HEAD)
        run(r, "add", "-A")
        run(r, "commit", "-q", "-m", "head")
        cls.plan = collect.collect(r, "HEAD~1", "HEAD", "src/cf", ["Server", "ThinClient"])
        cls.ix = index.build(src)

        def read(rel):
            p = src / rel
            return p.read_bytes().decode("utf-8-sig") if p.exists() else None

        cls.findings = static.run(r, cls.plan, cls.ix, read)
        cls.deps, cls.dep_findings = static.dependents(src, cls.plan, cls.ix)
        cls.messages = [f["message"] for f in cls.findings + cls.dep_findings]

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def has(self, text):
        self.assertTrue(any(text in m for m in self.messages), f"нет находки «{text}»; есть: {self.messages}")

    def test_plan(self):
        self.assertEqual(self.plan["scriptVariant"], "English")
        owners = {m["owner"] for m in self.plan["modules"]}
        self.assertIn("ОбщийМодуль.Common", owners)              # имена владельцев — канонические
        self.assertEqual([q["text"][:6] for q in self.plan["queries"]], ["SELECT"])

    def test_defects(self):
        self.has("Объект не найден: Catalogs.Prodcts")
        self.has("Объект не найден: Справочники.Продукты")
        self.has("Нет предопределённого элемента: Catalogs.Products.Servce")
        self.has("Нет значения перечисления: Enums.OrderKinds.Wholesal")
        self.has("Нет формы: Document.Order.Form.DocumentFormm")
        self.has("ПредопределенноеЗначение: нет Enum.OrderKinds.Wholesale")
        self.has("ЗНАЧЕНИЕ: нет Catalog.Products.Goodd")
        self.has("Нет экспортного метода: Common.NoSuch")
        self.has("Слишком много параметров: Common.F")
        self.has("Обработчик из Form.xml не найден в модуле формы: OnOpen")
        self.has("Обработчик CommonModule.Common.OnWrite не найден")
        self.has("Вызов удалённого экспортного метода Common.Old")

    def test_no_false_positives(self):
        noisy = [m for m in self.messages if any(x in m for x in
                 ("Documents.Order", "Products.Service", "Retail", "AllRefsType", "ListForm", "Docs"))]
        self.assertEqual(noisy, [])
        self.assertEqual(len([f for f in self.findings if f["severity"] == "error"]), 11,
                         [f["message"] for f in self.findings if f["severity"] == "error"])


class TestModuleVariables(unittest.TestCase):
    def test_assignment_on_next_line(self):
        """«Х» и «=» на разных строках (так пишут типовые) — тоже переменная модуля."""
        code = "Процедура Т()\n\tПараметрыОткрытия\n\t\t= Новый Структура;\n\tДля Каждого Стр Из Т Цикл\n"
        self.assertEqual(static.module_variables(code) & {"параметрыоткрытия", "стр"}, {"параметрыоткрытия", "стр"})


class TestGlobalVars(unittest.TestCase):
    def test_export_vars_of_root_modules(self):
        """Экспортные переменные модулей приложения и внешнего соединения — в индексе (globalVars); прочие — нет."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-gvars-"))
        self.addCleanup(rmtree_force, tmp)
        write(tmp, "Configuration.xml", CONFIG)
        write(tmp, "Ext/ManagedApplicationModule.bsl",
              "Перем ПараметрыКлиента Экспорт;\nПерем Локальная;\nПерем А Экспорт, Б;\n")
        write(tmp, "Ext/ExternalConnectionModule.bsl", "// Перем Закомментирована Экспорт;\nVar Conn Export;\n")
        self.assertEqual(index.build(tmp)["globalVars"], ["conn", "а", "параметрыклиента"])


if __name__ == "__main__":
    unittest.main()
