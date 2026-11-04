import unittest

from otk_1c import lang
from otk_1c.sources import layout, module_kinds

FLAGS = """<CommonModule><Properties><Global>false</Global><ClientManagedApplication>true</ClientManagedApplication>
<Server>true</Server><ExternalConnection>false</ExternalConnection><ClientOrdinaryApplication>false</ClientOrdinaryApplication>
<ServerCall>false</ServerCall><Privileged>false</Privileged></Properties></CommonModule>"""


def read(rel):
    return FLAGS if rel == "CommonModules/М.xml" else None


class TestDescribe(unittest.TestCase):
    def d(self, path, **kw):
        return module_kinds.describe(path, read, **kw)

    def test_common_module(self):
        i = self.d("CommonModules/М/Ext/Module.bsl")
        self.assertEqual((i.kind, i.owner, i.contexts), ("CommonModule", "ОбщийМодуль.М", ["Server", "ThinClient"]))
        self.assertEqual(i.probe, {"strategy": "missing-method", "target": "М"})

    def test_object_and_manager(self):
        o = self.d("Documents/Заказ/Ext/ObjectModule.bsl")
        self.assertEqual(o.owner, "Документ.Заказ")
        self.assertEqual(o.probe, {"strategy": "create-object", "target": "Документы.Заказ",
                                   "method": "СоздатьДокумент"})
        self.assertEqual(o.contexts, ["Server", "ExternalConnection"])
        m = self.d("Catalogs/Н/Ext/ManagerModule.bsl")
        self.assertEqual(m.probe, {"strategy": "missing-method", "target": "Справочники.Н"})
        r = self.d("InformationRegisters/Р/Ext/RecordSetModule.bsl")
        self.assertEqual(r.probe["method"], "СоздатьНаборЗаписей")
        c = self.d("Constants/К/Ext/ValueManagerModule.bsl")
        self.assertEqual(c.probe["method"], "СоздатьМенеджерЗначения")

    def test_ordinary_application_option(self):
        o = self.d("Documents/Заказ/Ext/ObjectModule.bsl", ordinary_application=True)
        self.assertIn("OrdinaryClient", o.contexts)

    def test_forms_with_companion(self):
        f = self.d("Documents/Заказ/Forms/ФормаДокумента/Ext/Form/Module.bsl")
        self.assertEqual(f.owner, "Документ.Заказ.Форма.ФормаДокумента")
        self.assertEqual(f.companions, ["Documents/Заказ/Forms/ФормаДокумента/Ext/Form.xml"])
        cf = self.d("CommonForms/Ф/Ext/Form/Module.bsl")
        self.assertEqual((cf.owner, cf.probe["strategy"]), ("ОбщаяФорма.Ф", "get-form"))

    def test_without_probe(self):
        for p in ("CommonCommands/К/Ext/CommandModule.bsl", "Catalogs/Н/Commands/К/Ext/CommandModule.bsl",
                  "HTTPServices/С/Ext/Module.bsl"):
            self.assertEqual(self.d(p).probe["strategy"], "none", p)

    def test_root_modules(self):
        self.assertEqual(self.d("Ext/SessionModule.bsl").probe, {"strategy": "session-start"})

    def test_script_variant(self):
        self.assertEqual(layout.DESIGNER.properties("<ScriptVariant>English</ScriptVariant>").variant, "English")
        self.assertEqual(layout.DESIGNER.properties(None).variant, "Russian")
        # английский поддержан; имена владельцев и код проб — канонические (русские) при любом варианте
        en = module_kinds.describe("CommonModules/М/Ext/Module.bsl", read, variant="English")
        self.assertEqual(en.owner, module_kinds.describe("CommonModules/М/Ext/Module.bsl", read).owner)
        with self.assertRaises(lang.UnsupportedScriptVariant):
            module_kinds.describe("CommonModules/М/Ext/Module.bsl", read, variant="Klingon")


if __name__ == "__main__":
    unittest.main()
