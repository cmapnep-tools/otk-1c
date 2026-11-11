"""Операции с конфигурацией ИБ: Конфигуратор или ibcmd по environment.ibTool."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from otk_1c.errors import InfraError
from otk_1c.probes import platform as pf
from otk_1c.probes.platform import ConfigOps


class Recorder(ConfigOps):
    def __init__(self, tool, ib="C:/ib"):
        self.ib_tool, self.ib, self.calls = tool, Path(ib) if ib else None, []

    def designer(self, *args, label="", timeout=0):
        self.calls.append(("designer", *map(str, args)))
        return 0, 0.1, ""

    def ibcmd(self, *args, timeout=0, label=None):
        self.calls.append(("ibcmd", *map(str, args)))
        return 0, 0.1, ""


def ops(p, tmp):
    lst = Path(tmp) / "load.list"
    lst.write_text("CommonModules/М/Ext/Module.bsl\n", encoding="utf-8-sig")
    names = Path(tmp) / "names.list"
    names.write_text("CommonModule.М\n", encoding="utf-8")
    p.load_files("D:/dump", lst)
    p.load_dir("D:/ext", extension="otk")
    p.dump_files("D:/out", names)
    p.update_db(extension="otk")
    p.save_extension("otk", "D:/otk.cfe")
    p.load_extension("otk", "D:/otk.cfe")
    p.delete_extension("otk")
    p.disable_support()
    return [c[:5] for c in p.calls]


class ConfigOpsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_designer_by_default(self):
        self.assertEqual([c[1] for c in ops(Recorder("designer"), self.tmp)],
                         ["/LoadConfigFromFiles", "/LoadConfigFromFiles", "/DumpConfigToFiles", "/UpdateDBCfg",
                          "/DumpCfg", "/LoadCfg", "/DeleteCfg", "/ManageCfgSupport"])

    def test_ibcmd(self):
        calls = ops(Recorder("ibcmd"), self.tmp)
        self.assertTrue(all(c[0] == "ibcmd" for c in calls))
        self.assertEqual([" ".join(c[3:5]) for c in calls],
                         ["import files", "import --extension=otk", "export objects", "apply --extension=otk",
                          "save --extension=otk", "load --extension=otk", "extension delete", "support disable"])

    def test_server_base_is_designer(self):
        calls = ops(Recorder("ibcmd", ib=None), self.tmp)
        self.assertTrue(all(c[0] == "designer" for c in calls))

    def test_long_list_falls_back_to_designer(self):
        p, lst = Recorder("ibcmd"), Path(self.tmp) / "long.list"
        lst.write_text("".join(f"CommonModules/Модуль{i}/Ext/Module.bsl\n" for i in range(1000)), encoding="utf-8")
        p.load_files("D:/dump", lst)
        self.assertEqual(p.calls[0][:2], ("designer", "/LoadConfigFromFiles"))

    def test_ibcmd_without_partial_uses_designer(self):
        """ibcmd платформы не знает --partial (8.3.23): частичная загрузка — Конфигуратором."""
        p, lst = Recorder("ibcmd"), Path(self.tmp) / "load.list"
        lst.write_text("CommonForms/Ф.xml\n", encoding="utf-8")
        p.probe_ibcmd_partial = lambda: False
        p.load_files("D:/dump", lst, extension="otk")
        self.assertEqual(p.calls, [("designer", "/LoadConfigFromFiles", "D:/dump", "-listFile", str(lst),
                                    "-Format", "Hierarchical", "-Extension", "otk")])

    def test_partial_probe_once_per_run(self):
        p, lst = Recorder("ibcmd"), Path(self.tmp) / "load.list"
        lst.write_text("CommonForms/Ф.xml\n", encoding="utf-8")
        probes = []
        p.probe_ibcmd_partial = lambda: probes.append(1) or True
        p.load_files("D:/dump", lst)
        p.load_files("D:/dump", lst)
        self.assertEqual(len(probes), 1)                       # проба кешируется на прогон
        self.assertEqual([c[0] for c in p.calls], ["ibcmd", "ibcmd"])
        self.assertIn("--partial", p.calls[0])


class PartialProbeTest(unittest.TestCase):
    """Поддержка --partial: проба двумя одинаковыми командами на отсутствующем файле, а не по версии платформы."""

    def platform(self, rc_with, rc_without):
        p = object.__new__(pf.Platform)                        # только проба: поиск платформы и ИБ не нужны
        p.run_dir = Path(tempfile.mkdtemp())
        calls = []

        def ibcmd(*args, timeout=0, label=None):
            calls.append(args)
            return (rc_with if "--partial" in args else rc_without), 0.1, ""

        p.ibcmd = ibcmd
        return p, calls

    def test_supported(self):
        p, calls = self.platform(0xFFFFFFFF, 0xFFFFFFFF)       # ключ есть: ошибка данных (-1), не разбор
        self.assertTrue(p.ibcmd_partial())
        self.assertEqual(len(calls), 1)                        # вторая проба не нужна
        self.assertEqual(calls[0][:5], ("infobase", "config", "import", "files", f"--base-dir={p.run_dir}"))
        self.assertEqual(calls[0][5:], ("--partial", pf.PARTIAL_PROBE))

    def test_key_unknown(self):
        p, calls = self.platform(pf.IBCMD_PARSE_ERROR, 0xFFFFFFFF)   # 8.3.23: --partial не разобран
        self.assertFalse(p.ibcmd_partial())
        self.assertEqual(len(calls), 2)
        self.assertNotIn("--partial", calls[1])                # та же команда без ключа — дело в нём

    def test_parse_error_not_about_key(self):
        """Код 2 у обеих проб — ошибка командной строки по другой причине: ключ разобран, судить будет загрузка."""
        p, calls = self.platform(pf.IBCMD_PARSE_ERROR, pf.IBCMD_PARSE_ERROR)
        self.assertTrue(p.ibcmd_partial())
        self.assertEqual(len(calls), 2)

    def test_cached(self):
        p, calls = self.platform(pf.IBCMD_PARSE_ERROR, 0xFFFFFFFF)
        self.assertFalse(p.ibcmd_partial())
        self.assertFalse(p.ibcmd_partial())
        self.assertEqual(len(calls), 2)                        # две команды пробы на весь прогон


class NoIbcmdTest(unittest.TestCase):
    """Клиентская установка платформы: ibcmd нет — полная загрузка Конфигуратором, безопасный режим — через COM."""

    def recorder(self, has_ibcmd, ib="C:/ib"):
        p = Recorder("designer", ib=ib)
        p.has_ibcmd = has_ibcmd
        p.com_unsafe = lambda name: p.calls.append(("com", name)) or (0, 0.1, "")
        return p

    def test_with_ibcmd(self):
        p = self.recorder(True)
        p.full_load("D:/dump")
        p.extension_unsafe("YAXUNIT")
        self.assertEqual([c[:5] for c in p.calls],
                         [("ibcmd", "infobase", "config", "import", "D:/dump"),
                          ("ibcmd", "infobase", "config", "extension", "update")])

    def test_without_ibcmd(self):
        p = self.recorder(False)
        p.full_load("D:/dump", extension="otk")
        p.extension_unsafe("YAXUNIT")
        self.assertEqual(p.calls, [("designer", "/LoadConfigFromFiles", "D:/dump", "-Extension", "otk"),
                                   ("com", "YAXUNIT")])

    def test_server_base(self):
        p = self.recorder(True, ib=None)
        p.full_load("D:/dump")
        self.assertEqual(p.calls[0][:2], ("designer", "/LoadConfigFromFiles"))

    def test_no_com_is_infra(self):
        p = Recorder("designer")
        p.has_ibcmd = False
        with self.assertRaises(InfraError):
            p.extension_unsafe("YAXUNIT")


class SettingTest(unittest.TestCase):
    def make(self, env, environ=None, ibcmd=True):
        with mock.patch.dict("os.environ", environ or {}, clear=False), \
                mock.patch.object(pf, "find_platform", return_value=Path("C:/1cv8/bin")), \
                mock.patch.object(pf, "ibcmd_installed", return_value=ibcmd):
            return pf.Platform({"ib": "C:/ib", **env}, tempfile.mkdtemp())

    def test_ibcmd_missing_is_infra(self):
        self.assertFalse(self.make({}, {"OTK_IB_TOOL": ""}, ibcmd=False).has_ibcmd)
        with self.assertRaisesRegex(InfraError, "ibTool = designer"):
            self.make({"ibTool": "ibcmd"}, {"OTK_IB_TOOL": ""}, ibcmd=False)

    def test_installed(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertFalse(pf.ibcmd_installed(tmp))
        self.assertFalse(pf.ibcmd_installed(None))
        (tmp / pf.exe_name("ibcmd")).write_bytes(b"")
        self.assertTrue(pf.ibcmd_installed(tmp))

    def test_no_designer_binary(self):
        """Каталог платформы без 1cv8 (серверная установка): резервный запуск Конфигуратора — понятный сбой."""
        p = self.make({"ibTool": "ibcmd", "platform": tempfile.mkdtemp()}, {"OTK_IB_TOOL": ""})
        with self.assertRaisesRegex(InfraError, "пакетный Конфигуратор не установлен"):
            p.designer("/LoadConfigFromFiles", "D:/dump")

    def test_values(self):
        self.assertEqual(self.make({}, {"OTK_IB_TOOL": ""}).ib_tool, "designer")
        self.assertEqual(self.make({"ibTool": "IBCMD"}, {"OTK_IB_TOOL": ""}).ib_tool, "ibcmd")

    def test_unknown_is_infra(self):
        with self.assertRaises(InfraError):
            self.make({"ibTool": "edt"}, {"OTK_IB_TOOL": ""})


class DecodeTest(unittest.TestCase):
    def test_console_stderr(self):
        raw = "Расширение конфигурации не найдено.\r\n".encode("cp866")
        self.assertEqual(pf.decode(raw, "cp866").strip(), "Расширение конфигурации не найдено.")

    def test_utf8_even_length_not_utf16(self):
        self.assertEqual(pf.decode("[INFO] Готово".encode("utf-8")), "[INFO] Готово")

    def test_utf16(self):
        self.assertEqual(pf.decode("Ошибка".encode("utf-16")), "Ошибка")


if __name__ == "__main__":
    unittest.main()
