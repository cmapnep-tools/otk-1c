"""Инструменты: язык YAxUnit под основную конфигурацию; заимствование объектов для расширенной проверки."""
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.errors import InfraError
from otk_1c.probes import ib_tools

CONFIG = ('<MetaDataObject><Configuration><Properties><Name>YAXUNIT</Name>'
          '<DefaultLanguage>Language.Русский</DefaultLanguage></Properties>'
          '<ChildObjects><Language>Русский</Language><CommonModule>ЮТест</CommonModule></ChildObjects>'
          '</Configuration></MetaDataObject>')
LANGUAGE = ('<MetaDataObject><Language uuid="aaa"><Properties><ObjectBelonging>Adopted</ObjectBelonging>'
            '<Name>Русский</Name><ExtendedConfigurationObject>old-uuid</ExtendedConfigurationObject>'
            '<LanguageCode>ru</LanguageCode></Properties></Language></MetaDataObject>')

CATALOG = """<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses">
	<Catalog uuid="main-cat">
		<InternalInfo>
			<xr:GeneratedType name="CatalogObject.Товары" category="Object"><xr:TypeId>t1</xr:TypeId>\
<xr:ValueId>v1</xr:ValueId></xr:GeneratedType>
			<xr:GeneratedType name="CatalogRef.Товары" category="Ref"><xr:TypeId>t2</xr:TypeId>\
<xr:ValueId>v2</xr:ValueId></xr:GeneratedType>
		</InternalInfo>
		<Properties><Name>Товары</Name><Synonym/></Properties>
		<ChildObjects>
			<Attribute uuid="attr-1"><Properties><Name>Артикул</Name><Synonym/>
				<Type><v8:Type>xs:string</v8:Type></Type></Properties></Attribute>
			<TabularSection uuid="ts-1">
				<InternalInfo><xr:GeneratedType name="CatalogTabularSection.Товары.Цены" category="TabularSection">
					<xr:TypeId>t3</xr:TypeId><xr:ValueId>v3</xr:ValueId></xr:GeneratedType></InternalInfo>
				<Properties><Name>Цены</Name></Properties>
				<ChildObjects><Attribute uuid="ts-attr"><Properties><Name>Цена</Name></Properties></Attribute>\
</ChildObjects>
			</TabularSection>
		</ChildObjects>
	</Catalog>
</MetaDataObject>"""
ENUM = """<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"><Enum uuid="main-enum"><InternalInfo/>
<Properties><Name>Пол</Name></Properties><ChildObjects>
<EnumValue uuid="val-1"><Properties><Name>Мужской</Name></Properties></EnumValue>
<EnumValue uuid="val-2"><InternalInfo/><Properties><Name>Женский</Name></Properties></EnumValue>
</ChildObjects></Enum></MetaDataObject>"""
# у константы нет ChildObjects, у плана обмена в InternalInfo есть ThisNode
CONSTANT = """<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"><Constant uuid="main-const"><InternalInfo>
<xr:GeneratedType name="ConstantManager.Ставка" category="Manager"><xr:TypeId>t1</xr:TypeId><xr:ValueId>v1</xr:ValueId>
</xr:GeneratedType></InternalInfo><Properties><Name>Ставка</Name><Type><v8:Type>xs:decimal</v8:Type></Type>
</Properties></Constant></MetaDataObject>"""
EXCHANGE_PLAN = """<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses"><ExchangePlan uuid="main-plan"><InternalInfo>
<xr:ThisNode>base-node</xr:ThisNode>
<xr:GeneratedType name="ExchangePlanRef.Полный" category="Ref"><xr:TypeId>t1</xr:TypeId><xr:ValueId>v1</xr:ValueId>
</xr:GeneratedType></InternalInfo><Properties><Name>Полный</Name></Properties><ChildObjects/></ExchangePlan>
</MetaDataObject>"""


class TestAdoptLanguage(unittest.TestCase):
    def setUp(self):
        self.dump = Path(tempfile.mkdtemp(prefix="otk-tools-"))
        ib_tools.write(self.dump / "Configuration.xml", CONFIG)
        ib_tools.write(self.dump / "Languages" / "Русский.xml", LANGUAGE)

    def tearDown(self):
        rmtree_force(self.dump)

    def test_adopt_language(self):
        props = {"language": "English", "languageUuid": "base-uuid", "languageCode": "en"}
        ib_tools.adopt_language(self.dump, "Русский", props)
        self.assertFalse((self.dump / "Languages" / "Русский.xml").exists())
        lang = (self.dump / "Languages" / "English.xml").read_text(encoding="utf-8-sig")
        for part in ("<Name>English</Name>", "<ExtendedConfigurationObject>base-uuid<", "<LanguageCode>en<"):
            self.assertIn(part, lang)
        cfg = (self.dump / "Configuration.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<DefaultLanguage>Language.English</DefaultLanguage>", cfg)
        self.assertIn("<Language>English</Language>", cfg)
        self.assertNotIn("Русский", cfg)


class TestAdoptedObjects(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="otk-adopt-"))
        ib_tools.write(self.root / "Catalogs" / "Товары.xml", CATALOG)
        ib_tools.write(self.root / "Enums" / "Пол.xml", ENUM)
        ib_tools.write(self.root / "Constants" / "Ставка.xml", CONSTANT)
        ib_tools.write(self.root / "ExchangePlans" / "Полный.xml", EXCHANGE_PLAN)

    def tearDown(self):
        rmtree_force(self.root)

    def test_referenced_objects_both_languages(self):
        refs = ib_tools.referenced_objects(["А = Справочники.Товары.ПустаяСсылка();", "B = Enums.Пол.Мужской;",
                                         "Х = Объект.Справочники.Нет;"])
        self.assertEqual(refs, {("Catalogs", "Товары"), ("Enums", "Пол")})

    def test_catalog(self):
        o = ib_tools.adopted_object(self.root, "Catalogs", "товары")       # регистр имени — как в коде
        self.assertEqual((o["tag"], o["name"], o["collection"]), ("Catalog", "Товары", "Catalogs"))
        xml = o["xml"]
        self.assertIn("<ObjectBelonging>Adopted</ObjectBelonging>", xml)
        self.assertIn("<ExtendedConfigurationObject>main-cat</ExtendedConfigurationObject>", xml)
        self.assertIn('name="CatalogRef.Товары"', xml)
        self.assertNotIn("CatalogTabularSection", xml)                  # типы ТЧ — не в InternalInfo объекта
        self.assertNotIn("<TypeId>t1<", xml)                            # свои идентификаторы типов
        self.assertIn("<Name>Артикул</Name>", xml)
        self.assertIn("<ExtendedConfigurationObject>attr-1<", xml)
        self.assertIn("<v8:Type>xs:string</v8:Type>", xml)
        self.assertNotIn("Цена", xml)                                   # реквизиты ТЧ не заимствуются

    def test_enum_values_and_missing(self):
        xml = ib_tools.adopted_object(self.root, "Enums", "Пол")["xml"]
        self.assertIn("<ExtendedConfigurationObject>val-1<", xml)
        self.assertIn("<ExtendedConfigurationObject>val-2<", xml)
        self.assertIsNone(ib_tools.adopted_object(self.root, "Catalogs", "Нет"))

    def test_constant_without_child_objects(self):
        xml = ib_tools.adopted_object(self.root, "Constants", "Ставка")["xml"]
        self.assertIn("<ExtendedConfigurationObject>main-const<", xml)
        self.assertIn('name="ConstantManager.Ставка"', xml)
        self.assertNotIn("ChildObjects", xml)                          # константа с ChildObjects не загружается
        self.assertIn("<ChildObjects>", ib_tools.adopted_object(self.root, "Catalogs", "Товары")["xml"])

    def test_exchange_plan_this_node(self):
        xml = ib_tools.adopted_object(self.root, "ExchangePlans", "Полный")["xml"]
        node = re.search(r"<InternalInfo><xr:ThisNode>([^<]+)</xr:ThisNode><xr:GeneratedType ", xml)
        self.assertIsNotNone(node)                                       # первым в InternalInfo, свой uuid
        self.assertNotEqual(node.group(1), "base-node")
        self.assertNotIn("ThisNode", ib_tools.adopted_object(self.root, "Catalogs", "Товары")["xml"])

    def test_extension_order(self):
        props = {"language": "Русский", "languageUuid": "l", "languageCode": "ru", "interface": "Taxi"}
        objects = [ib_tools.adopted_object(self.root, "Enums", "Пол"),
                   ib_tools.adopted_object(self.root, "Catalogs", "Товары")]
        target = self.root / "ext"
        ib_tools.generate_test_extension(target, props, name="t", prefix="t_", modules={}, objects=objects)
        cfg = (target / "Configuration.xml").read_text(encoding="utf-8-sig")
        self.assertLess(cfg.index("<Catalog>Товары</Catalog>"), cfg.index("<Enum>Пол</Enum>"))
        self.assertTrue((target / "Catalogs" / "Товары.xml").exists())
        # без props["format"] — потолок ОТК: 2.20 загружается платформой 8.3.27 и 8.5.1
        self.assertIn(f'version="{ib_tools.FORMAT_MAX}"', cfg)

    def test_extension_run_mode(self):
        """Основной режим запуска расширения — как у расширяемой конфигурации: контролируемое свойство, с
        ManagedApplication расширение не загружается в конфигурацию обычного приложения."""
        props = {"language": "Русский", "languageUuid": "l", "languageCode": "ru", "interface": "Taxi"}
        target = self.root / "ext-run"
        ib_tools.generate_test_extension(target, props, name="t", prefix="t_", modules={}, objects=[])
        cfg = (target / "Configuration.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<DefaultRunMode>ManagedApplication</DefaultRunMode>", cfg)   # без runMode — как раньше
        ib_tools.generate_test_extension(target, {**props, "runMode": "OrdinaryApplication"}, name="t", prefix="t_",
                                         modules={}, objects=[])
        cfg = (target / "Configuration.xml").read_text(encoding="utf-8-sig")
        self.assertIn("<DefaultRunMode>OrdinaryApplication</DefaultRunMode>", cfg)

    def test_base_properties_run_mode(self):
        dump = self.root / "run-mode"
        (dump / "Languages").mkdir(parents=True)
        (dump / "Configuration.xml").write_text(
            '<MetaDataObject><Configuration><Properties><DefaultLanguage>Language.Русский</DefaultLanguage>'
            "<InterfaceCompatibilityMode>Taxi</InterfaceCompatibilityMode>"
            "<DefaultRunMode>OrdinaryApplication</DefaultRunMode></Properties></Configuration></MetaDataObject>",
            encoding="utf-8")
        (dump / "Languages" / "Русский.xml").write_text(
            '<MetaDataObject><Language uuid="lang-uuid"><Properties><Name>Русский</Name>'
            "<LanguageCode>ru</LanguageCode></Properties></Language></MetaDataObject>", encoding="utf-8")
        self.assertEqual(ib_tools.base_properties(dump)["runMode"], "OrdinaryApplication")
        (dump / "Configuration.xml").write_text(
            '<MetaDataObject><Configuration><Properties><DefaultLanguage>Language.Русский</DefaultLanguage>'
            "<InterfaceCompatibilityMode>Taxi</InterfaceCompatibilityMode>"
            "</Properties></Configuration></MetaDataObject>", encoding="utf-8")
        self.assertEqual(ib_tools.base_properties(dump)["runMode"], "ManagedApplication")   # свойства нет — обычный

    def test_extension_format(self):
        """Формат файлов расширения — как у выгрузки: Конфигуратор не грузит файлы разных версий."""
        props = {"language": "Русский", "languageUuid": "l", "languageCode": "ru", "interface": "Taxi",
                 "format": "2.16"}
        modules = {"М": ({"Server": "true"}, "Процедура П() Экспорт\nКонецПроцедуры\n")}
        objects = [ib_tools.adopted_object(self.root, "Catalogs", "Товары", fmt="2.16")]
        target = self.root / "ext16"
        ib_tools.generate_test_extension(target, props, name="t", prefix="t_", modules=modules, objects=objects)
        files = ["Configuration.xml", "Languages/Русский.xml", "CommonModules/М.xml", "Catalogs/Товары.xml"]
        for rel in files:
            with self.subTest(rel):
                text = (target / rel).read_text(encoding="utf-8-sig")
                self.assertIn('version="2.16"', text)
                self.assertNotIn('version="2.20"', text)


class FormatTest(unittest.TestCase):
    """Версия формата файлов: по таблице ИТС для платформы, у выгрузки — её собственная; потолок — 2.20."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.tmp)

    def test_for_platform(self):
        expect = {"8.3.21.1302": "2.14", "8.3.23.2157": "2.16", "8.3.24.1467": "2.17", "8.3.27.2342": "2.20",
                  "8.5.1.1529": "2.22"}
        for version, fmt in expect.items():
            with self.subTest(version):
                self.assertEqual(ib_tools.format_for_platform(version), fmt)
        self.assertIsNone(ib_tools.format_for_platform("8.3.20.1000"))     # старее таблицы
        self.assertIsNone(ib_tools.format_for_platform(None))

    def test_file_format(self):
        dump = self.tmp / "cf"
        dump.mkdir()
        (dump / "Configuration.xml").write_text('<MetaDataObject xmlns="x" version="2.17"><Configuration/>'
                                                "</MetaDataObject>", encoding="utf-8")
        self.assertEqual(ib_tools.file_format(dump), "2.17")
        self.assertIsNone(ib_tools.file_format(self.tmp / "нет"))
        (self.tmp / "edt/Configuration").mkdir(parents=True)
        (self.tmp / "edt/Configuration/Configuration.mdo").write_text("<mdclass:Configuration/>", encoding="utf-8")
        self.assertIsNone(ib_tools.file_format(self.tmp / "edt"))

    def test_bad_format(self):
        """Нечитаемый Configuration.xml и неразбираемая версия — сбой инфраструктуры, а не молчание."""
        broken = self.tmp / "broken"
        broken.mkdir()
        (broken / "Configuration.xml").write_text('<MetaDataObject version="2.17"/>', encoding="utf-8")
        with mock.patch.object(Path, "read_text", side_effect=OSError("доступ запрещён")):
            with self.assertRaises(InfraError) as e:
                ib_tools.file_format(broken)
        self.assertIn("Configuration.xml", str(e.exception))
        odd = self.tmp / "odd"
        odd.mkdir()
        (odd / "Configuration.xml").write_text('<MetaDataObject version="2..1"><Configuration/></MetaDataObject>',
                                               encoding="utf-8")
        with self.assertRaises(InfraError) as e:
            ib_tools.file_format(odd)
        self.assertIn("2..1", str(e.exception))
        with self.assertRaises(InfraError):                    # ранняя проверка формата не падает в int()
            ib_tools.require_format(odd, self.tmp / "1cv8/8.3.23.2157/bin")

    def test_format_without_version(self):
        """Версии в выгрузке нет — формат берём у платформы, а не потолок: иначе на старой платформе не загрузится."""
        dump = self.tmp / "old"
        dump.mkdir()
        (dump / "Configuration.xml").write_text("<MetaDataObject><Configuration/></MetaDataObject>", encoding="utf-8")
        self.assertIsNone(ib_tools.file_format(dump))
        self.assertEqual(ib_tools.generated_format(dump, self.tmp / "1cv8/8.3.23.2157/bin"), "2.16")
        self.assertEqual(ib_tools.generated_format(dump), ib_tools.FORMAT_MAX)      # платформы нет — потолок

    def test_generated_format(self):
        dump = self.tmp / "cf"
        dump.mkdir()
        (dump / "Configuration.xml").write_text('<MetaDataObject version="2.17"><Configuration/></MetaDataObject>',
                                                encoding="utf-8")
        self.assertEqual(ib_tools.generated_format(dump), "2.17")            # своя версия выгрузки, не платформы
        edt = self.tmp / "edt/Configuration"
        edt.mkdir(parents=True)
        (edt / "Configuration.mdo").write_text("<mdclass:Configuration/>", encoding="utf-8")
        bin23 = self.tmp / "1cv8/8.3.23.2157/bin"
        self.assertEqual(ib_tools.generated_format(self.tmp / "edt", bin23), "2.16")
        bin85 = self.tmp / "1cv8/8.5.1.1529/bin"
        self.assertEqual(ib_tools.generated_format(self.tmp / "edt", bin85), ib_tools.FORMAT_MAX)
        self.assertEqual(ib_tools.generated_format(self.tmp / "edt"), ib_tools.FORMAT_MAX)   # платформы нет
        newer = self.tmp / "cf85"
        newer.mkdir()
        (newer / "Configuration.xml").write_text('<MetaDataObject version="2.22"><Configuration/></MetaDataObject>',
                                                 encoding="utf-8")
        self.assertEqual(ib_tools.generated_format(newer), ib_tools.FORMAT_MAX)  # потолок генерации

    def test_require_format(self):
        dump = self.tmp / "cf"
        dump.mkdir()
        (dump / "Configuration.xml").write_text('<MetaDataObject version="2.20"><Configuration/></MetaDataObject>',
                                                encoding="utf-8")
        with self.assertRaises(InfraError) as e:
            ib_tools.require_format(dump, self.tmp / "1cv8/8.3.23.2157/bin")
        self.assertEqual(e.exception.code, "format-unsupported")
        self.assertIn("2.20", str(e.exception))
        self.assertIn("8.3.23.2157", str(e.exception))
        ib_tools.require_format(dump, self.tmp / "1cv8/8.3.27.2342/bin")       # та же версия — грузится
        ib_tools.require_format(dump, None)                                    # платформа неизвестна — не судим


if __name__ == "__main__":
    unittest.main()
