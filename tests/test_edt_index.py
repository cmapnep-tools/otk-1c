"""Индекс метаданных, формы и статика по исходникам EDT == по выгрузке Конфигуратора (этап 3)."""
import tempfile
import unittest
from pathlib import Path

from otk_1c.gate import collect, static
from otk_1c.sources import index, layout

D = layout.DESIGNER
E = layout.EDT
PROTECTED = b"\xff\xff\xff\x7f\x00\x01 binary"
MANAGER = "Функция Найти(Код, Необязательный = 1) Экспорт\nКонецФункции\n"
CM = "Функция Привет(Имя) Экспорт\nКонецФункции\n\nФункция Скрытая()\nКонецФункции\n"


def _attr(n):
    return f"<Attribute><Properties><Name>{n}</Name></Properties></Attribute>"


def designer_files():
    def props(n, extra=""):
        return f"<Properties><Name>{n}</Name>{extra}</Properties>"
    def wrap(tag, body):
        return f"<MetaDataObject><{tag}>{body}</{tag}></MetaDataObject>"
    return {
        "Configuration.xml": "<MetaDataObject><Configuration><ChildObjects><Catalog>Товары</Catalog>"
                             "<Enum>Режимы</Enum><CommonModule>М</CommonModule><CommonModule>Защ</CommonModule>"
                             "<CommonForm>Общая</CommonForm><EventSubscription>Подп</EventSubscription>"
                             "<ScheduledJob>Задание</ScheduledJob><Language>Русский</Language>"
                             "</ChildObjects></Configuration></MetaDataObject>",
        "Catalogs/Товары.xml": wrap("Catalog", props("Товары") + "<ChildObjects>" + _attr("Артикул") + _attr("Цена")
            + "<TabularSection>" + props("Состав") + "<ChildObjects>" + _attr("Строка") + _attr("Количество")
            + "</ChildObjects></TabularSection><Form>ФормаЭлемента</Form><Form>Старая</Form>"
              "<Command>Печать</Command><Template>Макет</Template></ChildObjects>"),
        "Catalogs/Товары/Ext/Predefined.xml": "<PredefinedData><Item><Name>Группа</Name><ChildItems><Item>"
                                              "<Name>Вложенный</Name>"
                                              "<ChildItems><Item><Name>Глубокий</Name></Item></ChildItems></Item></ChildItems></Item>"
                                              "<Item><Name>Второй</Name></Item></PredefinedData>",
        "Catalogs/Товары/Ext/ManagerModule.bsl": MANAGER,
        "Catalogs/Товары/Forms/Старая.xml": wrap("Form", props("Старая", "<FormType>Ordinary</FormType>")),
        "Catalogs/Товары/Forms/ФормаЭлемента.xml": wrap("Form", props("ФормаЭлемента", "<FormType>Managed</FormType>")),
        "Enums/Режимы.xml": wrap("Enum", props("Режимы")
                                 + "<ChildObjects><EnumValue><Properties><Name>Один</Name></Properties>"
                                 "</EnumValue><EnumValue><Properties><Name>Два</Name></Properties></EnumValue></ChildObjects>"),
        "CommonModules/М.xml": wrap("CommonModule", props("М", "<Global>true</Global><Server>true</Server>"
                                                               "<ServerCall>false</ServerCall>")),
        "CommonModules/М/Ext/Module.bsl": CM,
        "CommonModules/Защ.xml": wrap("CommonModule", props("Защ", "<Server>true</Server>")),
        "CommonModules/Защ/Ext/Module.bin": PROTECTED,
        "CommonForms/Общая.xml": wrap("CommonForm", props("Общая", "<FormType>Ordinary</FormType>")),
        "EventSubscriptions/Подп.xml": wrap("EventSubscription",
                                            props("Подп", "<Handler>CommonModule.М.Привет</Handler>")),
        "ScheduledJobs/Задание.xml": wrap("ScheduledJob",
                                          props("Задание", "<MethodName>CommonModule.М.Нет</MethodName>")),
    }


def edt_files():
    def wrap(tag, body):
        return f'<?xml version="1.0"?><mdclass:{tag} xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass">{body}</mdclass:{tag}>'
    def attr(n):
        return f"<attributes><name>{n}</name><synonym><key>ru</key><value>{n}</value></synonym></attributes>"
    return {
        "Configuration/Configuration.mdo": wrap("Configuration", "<name>К</name>"
            "<defaultLanguage>Language.Русский</defaultLanguage>"
            "<catalogs>Catalog.Товары</catalogs><enums>Enum.Режимы</enums><commonModules>CommonModule.М</commonModules>"
            "<commonModules>CommonModule.Защ</commonModules><commonForms>CommonForm.Общая</commonForms>"
            "<eventSubscriptions>EventSubscription.Подп</eventSubscriptions><scheduledJobs>ScheduledJob.Задание</scheduledJobs>"
            "<languages>Language.Русский</languages>"),
        "Catalogs/Товары/Товары.mdo": wrap("Catalog", "<name>Товары</name>" + attr("Артикул") + attr("Цена")
            + "<tabularSections><name>Состав</name>" + attr("Строка") + attr("Количество") + "</tabularSections>"
            "<forms><name>ФормаЭлемента</name><formType>Managed</formType></forms>"
            "<forms><name>Старая</name><formType>Ordinary</formType></forms>"
            "<commands><name>Печать</name></commands><templates><name>Макет</name></templates>"
            "<predefined><items><name>Группа</name><content><name>Вложенный</name><content><name>Глубокий</name></content>"
            "</content></items><items><name>Второй</name></items></predefined>"),
        "Catalogs/Товары/ManagerModule.bsl": MANAGER,
        "Enums/Режимы/Режимы.mdo": wrap("Enum", "<name>Режимы</name><enumValues><name>Один</name></enumValues>"
                                        "<enumValues><name>Два</name></enumValues>"),
        "CommonModules/М/М.mdo": wrap("CommonModule", "<name>М</name><global>true</global><server>true</server>"),
        "CommonModules/М/Module.bsl": CM,
        "CommonModules/Защ/Защ.mdo": wrap("CommonModule", "<name>Защ</name><server>true</server>"),
        "CommonModules/Защ/Module.bsl": PROTECTED,
        "CommonForms/Общая/Общая.mdo": wrap("CommonForm", "<name>Общая</name><formType>Ordinary</formType>"),
        "EventSubscriptions/Подп/Подп.mdo": wrap("EventSubscription",
                                                 "<name>Подп</name><handler>CommonModule.М.Привет</handler>"),
        "ScheduledJobs/Задание/Задание.mdo": wrap("ScheduledJob",
                                                  "<name>Задание</name><methodName>CommonModule.М.Нет</methodName>"),
    }


def write(root, files):
    for rel, data in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


class TestIndexParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.d, cls.e = Path(cls.tmp.name) / "d", Path(cls.tmp.name) / "e"
        write(cls.d, designer_files())
        write(cls.e, edt_files())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_equal(self):
        a, b = index.build(self.d), index.build(self.e)
        self.assertEqual(a, b)

    def test_content(self):
        ix = index.build(self.e)           # формат определён сам
        t = ix["objects"]["Catalogs"]["товары"]
        self.assertEqual(t["fields"], ["Артикул", "Цена"])
        self.assertEqual(t["tabular"], {"Состав": ["Строка", "Количество"]})
        self.assertEqual((t["forms"], t["commands"], t["templates"]),
                         (["ФормаЭлемента", "Старая"], ["Печать"], ["Макет"]))
        self.assertEqual(t["predefined"], ["Группа", "Вложенный", "Глубокий", "Второй"])
        self.assertEqual(list(t["managerExports"]), ["найти"])
        self.assertEqual(ix["objects"]["Enums"]["режимы"]["values"], ["Один", "Два"])
        self.assertEqual(ix["handlers"], [
            {"kind": "EventSubscriptions", "object": "Подп", "ref": "CommonModule.М.Привет"},
            {"kind": "ScheduledJobs", "object": "Задание", "ref": "CommonModule.М.Нет"}])
        m = ix["commonModules"]
        self.assertTrue(m["м"]["flags"]["Global"] and m["м"]["flags"]["Server"] and not m["м"]["flags"]["ServerCall"])
        self.assertEqual(list(ix["globalExports"]), ["привет"])
        self.assertEqual((m["защ"]["source"], m["защ"]["exports"]), (False, {}))

    def test_protected(self):
        self.assertTrue(E.protected(PROTECTED))
        self.assertTrue(E.protected(PROTECTED.decode("utf-8-sig", "replace")))
        self.assertFalse(E.protected("Процедура П()\nКонецПроцедуры\n"))
        self.assertFalse(E.protected(None))
        self.assertFalse(D.protected(PROTECTED.decode("utf-8-sig", "replace")))

    def test_config_children(self):
        self.assertEqual(E.config_children("<c><name>К</name><catalogs>Catalog.А</catalogs><x><y>Catalog.Б</y></x>"
                                           "<defaultRunMode>Auto</defaultRunMode>"
                                           "<defaultReportForm>CommonForm.Ф</defaultReportForm></c>"),
                         [("Catalog", "А")])
        self.assertEqual(D.config_children("<c><ChildObjects><Catalog>А</Catalog></ChildObjects></c>"),
                         [("Catalog", "А")])
        self.assertEqual(E.config_children("не xml"), [])

    def test_protected_dropped_from_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, edt_files())
            plan = collect.collect_dirs(None, tmp, ["Server", "ThinClient"])
            paths = {m["path"] for m in plan["modules"]}
            self.assertIn("CommonModules/М/Module.bsl", paths)
            self.assertNotIn("CommonModules/Защ/Module.bsl", paths)
            self.assertIn("CommonModules/Защ/Module.bsl", {m["path"] for m in plan["metadata"]})


class TestOrdinaryForm(unittest.TestCase):
    OWNER = edt_files()["Catalogs/Товары/Товары.mdo"]

    def test_edt(self):
        self.assertTrue(E.ordinary_form(self.OWNER, "Старая"))
        self.assertFalse(E.ordinary_form(self.OWNER, "ФормаЭлемента"))
        self.assertFalse(E.ordinary_form(self.OWNER, "Нет"))
        self.assertTrue(E.ordinary_form(edt_files()["CommonForms/Общая/Общая.mdo"]))
        self.assertFalse(E.ordinary_form("<f><name>О</name><formType>Managed</formType></f>"))
        self.assertFalse(E.ordinary_form(None, "Ф"))

    def test_designer(self):
        self.assertTrue(D.ordinary_form("<FormType>Ordinary</FormType>", "Ф"))
        self.assertFalse(D.ordinary_form("<FormType>Managed</FormType>", "Ф"))

    def test_changed_forms(self):
        files = {"Catalogs/Товары/Товары.mdo": self.OWNER}
        def mod(f):
            return {"path": f"Catalogs/Товары/Forms/{f}/Module.bsl", "kind": "FormModule",
                    "owner": f"Catalog.Товары.Форма.{f}", "probe": {"strategy": "get-form"}}
        out = collect.changed_forms([mod("Старая"), mod("ФормаЭлемента")], [], files.get, lay=E)
        self.assertEqual([f["form"] for f in out], ["Catalog.Товары.Форма.ФормаЭлемента"])


class TestFormParsing(unittest.TestCase):
    FORM_XML = ('<Form xmlns="http://v8.1c.ru/8.3/xcf/logform">'
                '<Events><Event name="OnOpen">ПриОткрытии</Event></Events>'
                '<ChildItems><InputField name="П"><Events><Event name="OnChange">ПриИзменении</Event></Events>'
                '</InputField></ChildItems>'
                '<Attributes><Attribute name="Реквизит"/></Attributes>'
                '<Parameters><Parameter name="Параметр"/></Parameters></Form>')
    FORM_FORM = ('<form:Form xmlns:form="http://g5.1c.ru/v8/dt/form"><items><name>П</name>'
                 '<handlers><event>OnChange</event><name>ПриИзменении</name></handlers></items>'
                 '<attributes><name>Реквизит</name><columns><name>Колонка</name></columns></attributes>'
                 '<parameters><name>Параметр</name></parameters>'
                 '<handlers><event>OnOpen</event><name>ПриОткрытии</name></handlers>'
                 '<formCommands><name>К</name><action><name>НеБрать</name></action></formCommands></form:Form>')

    def test_both_formats(self):
        for text in (self.FORM_XML, self.FORM_FORM):
            self.assertEqual(static.form_attributes(text), {"реквизит", "параметр"}, text)
            self.assertEqual(static.form_handlers(text), ["ПриИзменении", "ПриОткрытии"], text)
        self.assertEqual(static.form_handlers(None), [])
        self.assertEqual(static.form_attributes("не xml", E), set())

    def test_check_handler_refs_edt(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, edt_files())
            ix = index.build(tmp)
        plan = {"modules": [], "removedModules": [],
                "metadata": [{"path": "ScheduledJobs/Задание/Задание.mdo", "status": "M"}]}
        found = static.check_handler_refs(plan, ix, E)
        self.assertEqual([f["module"] for f in found], ["ScheduledJobs/Задание/Задание.mdo"])
        plan["metadata"] = [{"path": "EventSubscriptions/Подп/Подп.mdo", "status": "M"}]
        self.assertEqual(static.check_handler_refs(plan, ix, E), [])


if __name__ == "__main__":
    unittest.main()
