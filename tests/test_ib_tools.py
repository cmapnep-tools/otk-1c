"""Инструменты: язык YAxUnit под основную конфигурацию; заимствование объектов для расширенной проверки."""
import re
import tempfile
import unittest
from pathlib import Path

from _fs import rmtree_force
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


if __name__ == "__main__":
    unittest.main()
