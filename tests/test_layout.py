"""Формат исходников (layout): адрес модуля по пути и обратно, свойства конфигурации."""
import tempfile
import unittest
from pathlib import Path

from otk_1c.sources import layout
from otk_1c.sources.layout import Addr

D = layout.DESIGNER
E = layout.EDT


class TestDesigner(unittest.TestCase):
    PATHS = {
        "Ext/SessionModule.bsl": Addr("", "", "SessionModule"),
        "CommonModules/М/Ext/Module.bsl": Addr("CommonModules", "М", "Module"),
        "CommonForms/Ф/Ext/Form/Module.bsl": Addr("CommonForms", "Ф", "FormModule"),
        "CommonCommands/К/Ext/CommandModule.bsl": Addr("CommonCommands", "К", "CommandModule"),
        "HTTPServices/С/Ext/Module.bsl": Addr("HTTPServices", "С", "Module"),
        "Catalogs/Т/Ext/ObjectModule.bsl": Addr("Catalogs", "Т", "ObjectModule"),
        "InformationRegisters/Р/Ext/RecordSetModule.bsl": Addr("InformationRegisters", "Р", "RecordSetModule"),
        "Constants/К/Ext/ValueManagerModule.bsl": Addr("Constants", "К", "ValueManagerModule"),
        "Documents/Д/Forms/Ф/Ext/Form/Module.bsl": Addr("Documents", "Д", "FormModule", "Ф"),
        "Documents/Д/Commands/К/Ext/CommandModule.bsl": Addr("Documents", "Д", "CommandModule", "К"),
    }

    def test_parse_and_back(self):
        for path, addr in self.PATHS.items():
            self.assertEqual(D.parse(path), addr, path)
            self.assertEqual(D.module_path(addr), path, path)

    def test_not_modules(self):
        for path in ("Ext/Module.bsl", "Ext/Foo.bsl", "Catalogs/Т/Ext/Foo.bsl", "Catalogs/Т.xml",
                     "Catalogs/Т/Ext/Form/Module.bsl", "Catalogs/Т/Forms/Ф/Ext/Form/Items/Module.bsl"):
            self.assertIsNone(D.parse(path), path)

    def test_files(self):
        self.assertEqual(D.object_file("Catalogs", "Т"), "Catalogs/Т.xml")
        self.assertEqual(D.form_files("Catalogs", "Т", "Ф"),
                         ("Catalogs/Т/Forms/Ф.xml", "Catalogs/Т/Forms/Ф/Ext/Form.xml"))
        self.assertEqual(D.form_files("CommonForms", "Ф"), ("CommonForms/Ф.xml", "CommonForms/Ф/Ext/Form.xml"))
        self.assertEqual(D.owner_key("Catalogs/Т/Forms/Ф/Ext/Form.xml"), "Catalogs/Т")
        self.assertEqual(D.owner_key("Ext/SessionModule.bsl"), "Ext/SessionModule.bsl")

    def test_properties(self):
        ext = ("<Properties><Name>Расш</Name>"
               "<ConfigurationExtensionPurpose>Customization</ConfigurationExtensionPurpose>"
               "<NamePrefix>р_</NamePrefix><ConfigurationExtensionCompatibilityMode>Version8_3_21"
               "</ConfigurationExtensionCompatibilityMode><CompatibilityMode>Version8_3_24</CompatibilityMode>"
               "<DefaultRunMode></DefaultRunMode></Properties>")
        p = D.properties(ext)
        self.assertEqual((p.extension, p.name, p.prefix, p.compat, p.run_mode, p.variant),
                         (True, "Расш", "р_", "Version8_3_21", None, "Russian"))
        main = ("<Properties><Name>Осн</Name><CompatibilityMode>DontUse</CompatibilityMode>"
                "<ScriptVariant>English</ScriptVariant></Properties>")
        p = D.properties(main)
        self.assertEqual((p.extension, p.name, p.compat, p.variant), (False, "Осн", None, "English"))
        self.assertEqual(D.properties(None), layout.Properties())
        mobile = ("<Properties><UsePurposes><v8:Value xsi:type=\"app:ApplicationUsePurpose\">MobilePlatformApplication"
                  "</v8:Value></UsePurposes><ModalityUseMode>UseWithWarnings</ModalityUseMode>"
                  "<SynchronousPlatformExtensionAndAddInCallUseMode>DontUse"
                  "</SynchronousPlatformExtensionAndAddInCallUseMode></Properties>")
        p = D.properties(mobile)
        self.assertEqual((p.mobile_app, p.modality, p.sync_calls), (True, "UseWithWarnings", "DontUse"))
        self.assertFalse(D.properties(main).mobile_app)


class TestEdt(unittest.TestCase):
    PATHS = {
        "Configuration/SessionModule.bsl": Addr("", "", "SessionModule"),
        "Configuration/ExternalConnectionModule.bsl": Addr("", "", "ExternalConnectionModule"),
        "CommonModules/М/Module.bsl": Addr("CommonModules", "М", "Module"),
        "CommonForms/Ф/Module.bsl": Addr("CommonForms", "Ф", "FormModule"),
        "CommonCommands/К/CommandModule.bsl": Addr("CommonCommands", "К", "CommandModule"),
        "HTTPServices/С/Module.bsl": Addr("HTTPServices", "С", "Module"),
        "Catalogs/Т/ObjectModule.bsl": Addr("Catalogs", "Т", "ObjectModule"),
        "Catalogs/Т/ManagerModule.bsl": Addr("Catalogs", "Т", "ManagerModule"),
        "InformationRegisters/Р/RecordSetModule.bsl": Addr("InformationRegisters", "Р", "RecordSetModule"),
        "Constants/К/ValueManagerModule.bsl": Addr("Constants", "К", "ValueManagerModule"),
        "Documents/Д/Forms/Ф/Module.bsl": Addr("Documents", "Д", "FormModule", "Ф"),
        "Documents/Д/Commands/К/CommandModule.bsl": Addr("Documents", "Д", "CommandModule", "К"),
    }

    def test_parse_and_back(self):
        for path, addr in self.PATHS.items():
            self.assertEqual(E.parse(path), addr, path)
            self.assertEqual(E.module_path(addr), path, path)

    def test_not_modules(self):
        for path in ("Configuration/Module.bsl", "Configuration/Foo.bsl", "Catalogs/Т/Foo.bsl", "Catalogs/Т/Т.mdo",
                     "Documents/Д/Forms/Ф/BaseForm/Module.bsl", "Documents/Д/Forms/Ф/Items/Module.bsl",
                     "Documents/Д/Forms/Ф/Form.form"):
            self.assertIsNone(E.parse(path), path)

    def test_files(self):
        self.assertEqual(E.object_file("Catalogs", "Т"), "Catalogs/Т/Т.mdo")
        self.assertEqual(E.form_files("Catalogs", "Т", "Ф"), ("Catalogs/Т/Т.mdo", "Catalogs/Т/Forms/Ф/Form.form"))
        self.assertEqual(E.form_files("CommonForms", "Ф"), ("CommonForms/Ф/Ф.mdo", "CommonForms/Ф/Form.form"))
        self.assertEqual(E.form_module("Catalogs/Т/Forms/Ф/Form.form"), "Catalogs/Т/Forms/Ф/Module.bsl")
        self.assertEqual(E.form_module("CommonForms/Ф/Form.form"), "CommonForms/Ф/Module.bsl")
        self.assertIsNone(E.form_module("Catalogs/Т/Forms/Ф/BaseForm/Form.form"))
        self.assertIsNone(E.form_module("Catalogs/Т/Т.mdo"))
        self.assertEqual(E.owner_key("Catalogs/Т/Forms/Ф/Form.form"), "Catalogs/Т")
        self.assertEqual(E.owner_key("Configuration/SessionModule.bsl"), "Configuration/SessionModule.bsl")

    def test_properties(self):
        ext = ("<mdclass:Configuration xmlns:mdclass='urn:x' xmlns:xsi='urn:y'><name>Расш</name>"
               "<extension xsi:type='e:ConfigurationExtension'><defaultRunMode>Checked</defaultRunMode>"
               "<interfaceCompatibilityMode>Checked</interfaceCompatibilityMode></extension>"
               "<namePrefix>р_</namePrefix><configurationExtensionCompatibilityMode>8.3.21"
               "</configurationExtensionCompatibilityMode><configurationExtensionPurpose>AddOn"
               "</configurationExtensionPurpose><defaultRunMode>ManagedApplication</defaultRunMode>"
               "<scriptVariant>English</scriptVariant></mdclass:Configuration>")
        p = E.properties(ext)
        self.assertEqual((p.extension, p.name, p.prefix, p.compat, p.run_mode, p.variant),
                         (True, "Расш", "р_", "Version8_3_21", "ManagedApplication", "English"))
        main = ("<mdclass:Configuration xmlns:mdclass='urn:x'><name>Осн</name>"
                "<configurationExtensionCompatibilityMode>8.3.27</configurationExtensionCompatibilityMode>"
                "<compatibilityMode>8.3.21</compatibilityMode></mdclass:Configuration>")
        p = E.properties(main)
        self.assertEqual((p.extension, p.name, p.compat, p.variant, p.run_mode, p.prefix),
                         (False, "Осн", "Version8_3_21", "English", None, ""))   # нет scriptVariant: умолчание EMF
        self.assertEqual(E.properties("<c><scriptVariant>Russian</scriptVariant></c>").variant, "Russian")
        self.assertIsNone(E.properties("<c><compatibilityMode>DontUse</compatibilityMode></c>").compat)
        self.assertEqual(E.properties(None), layout.Properties())
        p = E.properties("<c><usePurposes>PersonalComputer</usePurposes><usePurposes>MobileDevice</usePurposes>"
                         "<modalityUseMode>UseWithWarnings</modalityUseMode></c>")
        self.assertEqual((p.mobile_app, p.modality, p.sync_calls), (True, "UseWithWarnings", None))
        self.assertFalse(E.properties("<c><usePurposes>PersonalComputer</usePurposes></c>").mobile_app)
        self.assertEqual(E.properties("не xml"), layout.Properties())

    def test_flags_both_cases(self):
        from otk_1c.sources import module_kinds
        self.assertTrue(module_kinds.common_module_flags("<server>true</server>")["Server"])
        self.assertTrue(module_kinds.common_module_flags("<Server>true</Server>")["Server"])
        self.assertFalse(module_kinds.common_module_flags("<server>false</server>")["Server"])
        self.assertTrue(module_kinds.common_module_flags("<clientManagedApplication>true</clientManagedApplication>")
                        ["ClientManagedApplication"])


class TestDetect(unittest.TestCase):
    def _tree(self, tmp, edt, project=False):
        root = Path(tmp)
        src = root / "src" if project else root
        cfg = src / (layout.EDT.config_file if edt else layout.DESIGNER.config_file)
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text("<c/>", encoding="utf-8")
        return root, src

    def test_detect_and_source_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, src = self._tree(tmp, edt=True, project=True)
            self.assertIsNone(layout.detect(root))
            self.assertEqual(layout.source_root(root), src)
            self.assertEqual(layout.source_root(src), src)
            self.assertIs(layout.detect(src), E)
        with tempfile.TemporaryDirectory() as tmp:
            root, _ = self._tree(tmp, edt=False)
            self.assertEqual(layout.source_root(root), root)
            self.assertIs(layout.detect(root), D)

    def test_object_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            for n in ("Б", "А"):
                (Path(tmp) / "Catalogs" / n).mkdir(parents=True)
                (Path(tmp) / "Catalogs" / n / f"{n}.mdo").write_text("<c/>", encoding="utf-8")
            (Path(tmp) / "Catalogs" / "В").mkdir()
            self.assertEqual(E.object_names(tmp, "Catalogs"), ["А", "Б"])
            self.assertEqual(E.object_names(tmp, "Documents"), [])

    def test_detect_read(self):
        self.assertIs(layout.detect_read(lambda r: "x" if r == "Configuration/Configuration.mdo" else None), E)
        self.assertIs(layout.detect_read(lambda r: "x" if r == "Configuration.xml" else None), D)
        self.assertIsNone(layout.detect_read(lambda r: None))


class TestAdoptedCommonModule(unittest.TestCase):
    """Заимствованный общий модуль расширения EDT: флаги — в описании основной конфигурации."""
    EXT = ('<mdclass:CommonModule><name>Польз</name><objectBelonging>Adopted</objectBelonging>'
           '<extension><module>Extended</module></extension></mdclass:CommonModule>')
    MAIN = ("<MetaDataObject><CommonModule><Properties><Server>true</Server><ServerCall>true</ServerCall>"
            "</Properties></CommonModule></MetaDataObject>")

    def setUp(self):
        self.files = {"CommonModules/Польз/Польз.mdo": self.EXT}
        self.read = self.files.get
        self.parent = {"CommonModules/Польз.xml": self.MAIN}.get
        self.path = "CommonModules/Польз/Module.bsl"

    def test_flags_from_parent(self):
        from otk_1c.sources import module_kinds
        info = module_kinds.describe(self.path, self.read, lay=E, read_parent=self.parent, parent_lay=D)
        self.assertEqual((info.kind, info.contexts, info.probe["strategy"]),
                         ("CommonModule", ["Server"], "missing-method"))
        self.assertTrue(info.flags["ServerCall"])

    def test_no_parent(self):
        from otk_1c.sources import module_kinds
        info = module_kinds.describe(self.path, self.read, lay=E)
        self.assertEqual((info.kind, info.contexts, info.probe["strategy"]), ("CommonModule", [], "none"))
        self.assertIn("--parent", info.probe["reason"])
        info = module_kinds.describe(self.path, self.read, lay=E, read_parent=lambda r: None, parent_lay=D)
        self.assertEqual(info.probe["strategy"], "none")

    def test_own_flags_kept(self):
        from otk_1c.sources import module_kinds
        own = {"CommonModules/Польз/Польз.mdo":
               "<m><objectBelonging>Adopted</objectBelonging><server>true</server></m>"}
        info = module_kinds.describe(self.path, own.get, lay=E, read_parent=lambda r: self.fail("не нужен"),
                                     parent_lay=D)
        self.assertEqual(info.contexts, ["Server"])

    def test_collect_dirs_with_parent(self):
        from otk_1c.gate import collect
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, text in (("ext/Configuration/Configuration.mdo", "<c><name>Р</name></c>"),
                              ("ext/CommonModules/Польз/Польз.mdo", self.EXT),
                              ("ext/CommonModules/Польз/Module.bsl", "Процедура Тест()\nКонецПроцедуры\n"),
                              ("main/Configuration.xml", "<c/>"),
                              ("main/CommonModules/Польз.xml", self.MAIN)):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text(text, encoding="utf-8")
            plan = collect.collect_dirs(None, root / "ext", ["Server", "ThinClient"], False, root / "main")
            self.assertEqual(plan["sourceFormat"], "edt")
            self.assertEqual(plan["modules"][0]["contexts"], ["Server"])
            plan = collect.collect_dirs(None, root / "ext", ["Server", "ThinClient"])
            self.assertEqual(plan["modules"][0]["probe"]["strategy"], "none")

    def test_dependent_describe_with_layout(self):
        from otk_1c.sources import module_kinds
        files = {"CommonModules/М/М.mdo": "<m><server>true</server></m>"}
        info = module_kinds.describe("CommonModules/М/Module.bsl", files.get, lay=E)
        self.assertEqual((info.kind, info.contexts), ("CommonModule", ["Server"]))
        self.assertEqual(module_kinds.describe("CommonModules/М/Module.bsl", files.get).kind, "Unknown")


class TestFingerprint(unittest.TestCase):
    def test_edt_always_full(self):
        from otk_1c.sources import index
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "Configuration" / "Configuration.mdo"
            f.parent.mkdir()
            f.write_text("<c/>", encoding="utf-8")
            g = Path(tmp) / "Catalogs" / "Т" / "Т.mdo"
            g.parent.mkdir(parents=True)
            g.write_text("<c/>", encoding="utf-8")
            before = index.dir_fingerprint(tmp, False, E)
            g.write_text("<cc/>", encoding="utf-8")
            self.assertNotEqual(before, index.dir_fingerprint(tmp, False, E))
            self.assertEqual(E.dump_markers, ())


if __name__ == "__main__":
    unittest.main()
