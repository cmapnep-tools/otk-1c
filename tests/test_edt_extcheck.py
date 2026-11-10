"""Исходники EDT в шаге run: свойства конфигурации из Configuration.mdo, выгрузка Конфигуратора для
расширенной проверки — экспорт сборки или (её не было) экспорт по требованию."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.errors import InfraError
from otk_1c.probes import ib_tools
from otk_1c.probes.executors import RunContext

CONFIG_MDO = """<?xml version="1.0" encoding="UTF-8"?>
<mdclass:Configuration xmlns:mdclass="http://g5.1c.ru/v8/dt/metadata/mdclass" uuid="cfg">
  <name>Демо</name>
  <defaultLanguage>Language.Русский</defaultLanguage>
  <interfaceCompatibilityMode>TaxiEnableVersion8_2</interfaceCompatibilityMode>
  <languages uuid="en-uuid"><name>Английский</name><languageCode>en</languageCode></languages>
  <languages uuid="ru-uuid"><name>Русский</name><languageCode>ru</languageCode></languages>
</mdclass:Configuration>
"""


class EdtRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(rmtree_force, self.tmp)
        self.src = self.tmp / "proj" / "src"
        (self.src / "Configuration").mkdir(parents=True)
        (self.src / "Configuration" / "Configuration.mdo").write_text(CONFIG_MDO, encoding="utf-8")

    def test_base_properties_mdo(self):
        # без платформы формат не определить — потолок ОТК
        self.assertEqual(ib_tools.base_properties(self.src),
                         {"language": "Русский", "languageUuid": "ru-uuid", "languageCode": "ru",
                          "interface": "TaxiEnableVersion8_2", "format": ib_tools.FORMAT_MAX,
                          "runMode": "ManagedApplication"})

    def test_base_properties_default_interface(self):
        """Значение по умолчанию EDT не пишет."""
        text = CONFIG_MDO.replace("  <interfaceCompatibilityMode>TaxiEnableVersion8_2</interfaceCompatibilityMode>\n",
                                  "")
        (self.src / "Configuration" / "Configuration.mdo").write_text(text, encoding="utf-8")
        self.assertEqual(ib_tools.base_properties(self.src)["interface"], "Version8_2")

    def ctx(self, config_root, dump=None):
        logs = []
        c = RunContext(self.tmp, {}, {}, self.tmp / "run", self.tmp, config_root, object(), None,
                       install=lambda r: "", log=logs.append, designer_dump=dump)
        return c, logs

    def test_designer_root_designer(self):
        root = self.tmp / "cf"
        root.mkdir()
        (root / "Configuration.xml").write_text("<x/>", encoding="utf-8")
        c, _ = self.ctx(root)
        with mock.patch("otk_1c.probes.build.loadable") as loadable:
            self.assertEqual(c.designer_root(), root)
        loadable.assert_not_called()

    def test_designer_root_from_build(self):
        c, _ = self.ctx(self.src, dump=str(self.tmp / "export"))
        with mock.patch("otk_1c.probes.build.loadable") as loadable:
            self.assertEqual(c.designer_root(), self.tmp / "export")
        loadable.assert_not_called()

    def test_designer_root_on_demand(self):
        """Сборка не экспортировала (рабочая база, «без изменений»): экспорт один раз, время — в журнал."""
        c, logs = self.ctx(self.src)
        with mock.patch("otk_1c.probes.build.loadable", return_value=(self.tmp / "exp", 42.0)) as loadable:
            self.assertEqual(c.designer_root(), self.tmp / "exp")
            self.assertEqual(c.designer_root(), self.tmp / "exp")
        loadable.assert_called_once()
        self.assertEqual(c.timings["export"], 42.0)
        self.assertTrue(any("42.0" in m for m in logs))

    def test_designer_root_error(self):
        c, _ = self.ctx(self.src)
        with mock.patch("otk_1c.probes.build.loadable", side_effect=InfraError("нет 1cedtcli")):
            with self.assertRaises(InfraError):
                c.designer_root()


if __name__ == "__main__":
    unittest.main()
