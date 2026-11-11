"""Загрузка исходников EDT в копию ИБ через экспорт 1cedtcli (этап 5)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _fs import rmtree_force
from otk_1c import config
from otk_1c.errors import InfraError
from otk_1c.gate import pipeline
from otk_1c.probes import build, edt, platform as pf, userbase
from otk_1c.probes.platform import ConfigOps
from otk_1c.sources import layout
from test_edt_index import edt_files, write

E = layout.EDT


class FindEdtcliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-edtcli-"))
        env = mock.patch.dict("os.environ", {"LOCALAPPDATA": str(self.tmp / "local")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OTK_EDTCLI", None)
        self.exe = pf.exe_name("1cedtcli")

    def tearDown(self):
        rmtree_force(self.tmp)

    def _install(self, version):
        p = self.tmp / "local" / "1C" / "1cedtstart" / "installations" / version / "1cedt" / self.exe
        p.parent.mkdir(parents=True)
        p.write_text("", encoding="utf-8")
        return p

    def test_installed_newest_by_numbers(self):
        self._install("1C_EDT (Lite) 2026.1")
        newest = self._install("1C_EDT (Lite) 2026.2.0")
        self._install("1C_EDT (Lite) 2025.10")
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(pf.find_edtcli(), newest)

    def test_order_setting_installed_path(self):
        installed = self._install("EDT 2026.1")
        setting = self.tmp / "set.exe"
        setting.write_text("", encoding="utf-8")
        self.assertEqual(pf.find_edtcli(str(setting)), setting)
        with mock.patch("shutil.which", return_value="C:/bin/1cedtcli.exe"):
            self.assertEqual(pf.find_edtcli(), installed)
            shutil.rmtree(self.tmp / "local")
            self.assertEqual(pf.find_edtcli(), Path("C:/bin/1cedtcli.exe"))

    def test_not_found(self):
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(pf.find_edtcli())

    def test_option_and_config(self):
        a = SimpleNamespace(edtcli=str(self.tmp / "ещё нет"), set=None)
        self.assertEqual(config.apply_args(config.DEFAULTS, a)["environment"]["edtcli"], str(self.tmp / "ещё нет"))
        exe = self.tmp / "e.exe"
        exe.write_text("", encoding="utf-8")
        a = SimpleNamespace(edtcli=str(exe), set=None)
        self.assertEqual(config.apply_args(config.DEFAULTS, a)["environment"]["edtcli"], str(exe.resolve()))


def fake_cli(calls, rc=0, create=True):
    """Подмена subprocess.run для 1cedtcli: создаёт Configuration.xml в каталоге --configuration-files."""
    def run(cmd, **kw):
        calls.append(cmd)
        if create:
            out = Path(cmd[cmd.index("--configuration-files") + 1])
            out.mkdir(parents=True)
            (out / "Configuration.xml").write_text("<MetaDataObject/>", encoding="utf-8")
        err = "Ошибка экспорта".encode("cp1251") if rc else b""
        return subprocess.CompletedProcess(cmd, rc, "".encode("utf-8"), err)
    return run


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-edtexp-"))
        self.project = self.tmp / "проект EDT (копия)"
        write(self.project / "src", edt_files())
        (self.project / ".project").write_text("<projectDescription/>", encoding="utf-8")
        (self.project / "DT-INF").mkdir()
        (self.project / "DT-INF" / "PROJECT.PMF").write_text("Runtime-Version: 8.3.27\n", encoding="utf-8")
        self.run_dir = self.tmp / "прогон"

    def tearDown(self):
        rmtree_force(self.tmp)

    def test_command_order(self):
        cmd = edt.command("1cedtcli.exe", "ws", "проект", "out", 600)
        self.assertEqual(cmd, ["1cedtcli.exe", "-data", "ws", "-timeout", "600", "-command", "export",
                               "--project", "проект", "--configuration-files", "out"])

    def test_export_and_cache(self):
        calls = []
        with mock.patch("subprocess.run", fake_cli(calls)):
            dump, sec = edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
            self.assertTrue((dump / "Configuration.xml").is_file())
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][calls[0].index("--project") + 1], str(self.project.resolve()))
            self.assertEqual(calls[0][calls[0].index("-timeout") + 1], "1800")
            dump2, sec2 = edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
            self.assertEqual((dump2, sec2, len(calls)), (dump, 0.0, 1))         # без изменений — экспорта нет
            (self.project / "src" / "CommonModules" / "М" / "Module.bsl").write_text("А = 1;\n", encoding="utf-8")
            dump3, _ = edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
            self.assertEqual(len(calls), 2)                                      # правка — новый экспорт
            self.assertNotEqual(dump3, dump)
            self.assertFalse(dump.exists())                                      # старый отпечаток не копится
            self.assertFalse((self.run_dir / edt.CACHE / "workspace").exists())

    def test_failures(self):
        with mock.patch("subprocess.run", fake_cli([], rc=1, create=False)):
            with self.assertRaisesRegex(InfraError, "Ошибка экспорта"):
                edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
        with mock.patch("subprocess.run", fake_cli([], create=False)):           # rc=0, но выгрузки нет
            with self.assertRaisesRegex(InfraError, "rc=0"):
                edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            with self.assertRaisesRegex(InfraError, "не завершился"):
                edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir, timeout=1)
        with self.assertRaisesRegex(InfraError, "1cedtcli не найден"):
            edt.export(None, self.project / "src", self.run_dir)
        self.assertEqual(list((self.run_dir / edt.CACHE).iterdir()), [])     # сбой не оставляет .tmp и .ws
        (self.project / ".project").unlink()
        with self.assertRaisesRegex(InfraError, "не проект EDT"):
            edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)


    def test_partial_export_removed_on_failure(self):
        """Тайм-аут или rc!=0 после частичного экспорта — каталог .tmp удаляется."""
        def partial(rc=0, timeout=False):
            def run(cmd, **kw):
                out = Path(cmd[cmd.index("--configuration-files") + 1])
                (out / "CommonModules").mkdir(parents=True)
                (out / "CommonModules" / "М.xml").write_text("<x/>", encoding="utf-8")
                if timeout:
                    raise subprocess.TimeoutExpired(cmd, 1)
                return subprocess.CompletedProcess(cmd, rc, b"", b"")
            return run
        for fake in (partial(timeout=True), partial(rc=1), partial()):
            with mock.patch("subprocess.run", fake):
                with self.assertRaises(InfraError):
                    edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
            self.assertEqual(list((self.run_dir / edt.CACHE).iterdir()), [])

    def test_orphans_of_finished_processes_removed(self):
        cache = self.run_dir / edt.CACHE
        live, dead = os.getpid() + 1, os.getpid() + 2
        names = [f"k.{dead}.tmp", f"k.{dead}.ws", f"k.{live}.tmp", f"k.{live}.ws", "k.x.tmp", "старый"]
        for name in names:
            (cache / name).mkdir(parents=True)
        with mock.patch("subprocess.run", fake_cli([])), mock.patch.object(edt, "pid_alive", lambda pid: pid == live):
            dump, _ = edt.export(Path("1cedtcli.exe"), self.project / "src", self.run_dir)
        self.assertEqual(sorted(p.name for p in cache.iterdir()),
                         sorted([dump.name, f"k.{live}.tmp", f"k.{live}.ws", "k.x.tmp"]))

    def test_alive(self):
        self.assertTrue(edt.pid_alive(os.getpid()))
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        self.assertFalse(edt.pid_alive(p.pid))


class ToDesignerTest(unittest.TestCase):
    """Пути EDT -> выгрузка Конфигуратора; непересчитываемое — полная загрузка (None)."""

    def lst(self, *changes):
        return build._load_list(list(changes), E)

    def test_module_and_form(self):
        self.assertEqual(self.lst(("M", "Documents/Д/ObjectModule.bsl")), ["Documents/Д/Ext/ObjectModule.bsl"])
        # форма (модуль и содержимое) грузится описанием формы: файлы внутри Ext Конфигуратор не разбирает
        self.assertEqual(self.lst(("M", "Documents/Д/Forms/Ф/Module.bsl")), ["Documents/Д/Forms/Ф.xml"])
        self.assertEqual(self.lst(("M", "Documents/Д/Forms/Ф/Form.form")), ["Documents/Д/Forms/Ф.xml"])
        self.assertEqual(self.lst(("M", "CommonModules/М/Module.bsl")), ["CommonModules/М/Ext/Module.bsl"])
        self.assertEqual(self.lst(("M", "CommonForms/Ф/Module.bsl")), ["CommonForms/Ф.xml"])
        self.assertEqual(self.lst(("A", "CommonModules/Н/Module.bsl")), ["CommonModules/Н/Ext/Module.bsl"])

    def test_mdo(self):
        # .mdo объекта несёт и то, что в выгрузке — отдельные файлы (формы, предопределённые, состав плана обмена)
        self.assertIsNone(self.lst(("M", "Catalogs/К/К.mdo")))
        self.assertIsNone(self.lst(("M", "CommonModules/М/М.mdo"), ("M", "CommonModules/М/Module.bsl")))
        self.assertIsNone(self.lst(("A", "Catalogs/К/К.mdo")))                      # новый объект — полная

    def test_full_load(self):
        self.assertIsNone(self.lst(("M", "Configuration/Configuration.mdo")))       # корень
        self.assertIsNone(self.lst(("M", "Documents/Д/Forms/Ф/BaseForm/Form.form")))
        self.assertIsNone(self.lst(("M", "Roles/Р/Rights.rights")))
        self.assertIsNone(self.lst(("M", "Subsystems/А/Subsystems/Б/Б.mdo")))
        self.assertIsNone(self.lst(("M", "Documents/Д/ObjectModule.bsl"), ("D", "Documents/Д/ManagerModule.bsl")))
        self.assertIsNone(self.lst(("M", "Documents/Д/ObjectModule.bsl"), ("M", "Roles/Р/Rights.rights")))

    def test_to_designer_direct(self):
        self.assertEqual(E.to_designer("Configuration/SessionModule.bsl"), "Ext/SessionModule.bsl")
        self.assertEqual(E.to_designer("Configuration/Configuration.mdo"), "Configuration.xml")
        self.assertEqual(E.to_designer("Catalogs/К/Commands/Кт/CommandModule.bsl"),
                         "Catalogs/К/Commands/Кт/Ext/CommandModule.bsl")
        self.assertEqual(layout.DESIGNER.to_designer("Catalogs/К.xml"), "Catalogs/К.xml")


class FakeDesigner(ConfigOps):
    def __init__(self, tmp, edtcli="1cedtcli.exe"):
        self.run_dir, self.ib, self.edtcli, self.edt_timeout = tmp / "прогон", tmp / "ib", edtcli, 1800
        self.calls = []
        self.lists = []

    def designer(self, *args, label="", timeout=0):
        self.calls.append(args)
        if "-listFile" in args:
            self.lists.append(Path(args[args.index("-listFile") + 1]).read_text(encoding="utf-8-sig").split())
        return 0, 0.1, ""

    def ibcmd(self, *args, timeout=0, label=None):
        self.calls.append(args)
        return 0, 0.1, ""


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="otk-edtload-"))
        self.project = self.tmp / "проект"
        write(self.project / "src", edt_files())
        (self.project / ".project").write_text("<projectDescription/>", encoding="utf-8")
        (self.project / "прогон").mkdir(exist_ok=True)
        (self.tmp / "прогон").mkdir()
        self.platform = FakeDesigner(self.tmp)
        self.dump = self.tmp / "прогон" / "edt-export" / "k"
        self.dump.mkdir(parents=True)
        (self.dump / "Configuration.xml").write_text("<MetaDataObject/>", encoding="utf-8")

    def tearDown(self):
        rmtree_force(self.tmp)

    def state(self):
        return {"ib": "x", "commit": None, "tree": None, "dir": "d", "extension": None}

    def test_full_load_from_export(self):
        with mock.patch.object(edt, "export", return_value=(self.dump, 42.0)) as ex:
            res = build._load(self.platform, self.tmp / "прогон", self.project / "src", None, None, None, self.state())
        self.assertEqual(ex.call_args.args[:2], ("1cedtcli.exe", self.project / "src"))
        self.assertEqual((res["action"], res["export"]), ("full", 42.0))
        self.assertEqual(self.platform.calls[0][:3], ("infobase", "config", "import"))
        self.assertEqual(str(self.platform.calls[0][3]), str(self.dump))             # грузится экспорт, не src

    def test_partial_load_list_in_designer_paths(self):
        files = build._load_list([("M", "CommonModules/М/Module.bsl"),
                                  ("M", "Catalogs/Товары/Forms/ФормаЭлемента/Module.bsl")], E)
        with mock.patch.object(edt, "export", return_value=(self.dump, 0.0)):
            res = build._load(self.platform, self.tmp / "прогон", self.project / "src", files, None, None, self.state())
        self.assertEqual(res["action"], "partial")
        self.assertEqual(self.platform.lists, [["Catalogs/Товары/Forms/ФормаЭлемента.xml",
                                                "CommonModules/М/Ext/Module.bsl"]])
        self.assertEqual(str(self.platform.calls[0][1]), str(self.dump))

    def test_nothing_to_load_does_not_export(self):
        with mock.patch.object(edt, "export", side_effect=AssertionError("экспорт без загрузки")):
            res = build._load(self.platform, self.tmp / "прогон", self.project / "src", [], None, None, self.state())
        self.assertEqual(res["action"], "up-to-date")

    def test_designer_dump_not_exported(self):
        d = self.tmp / "cf"
        write(d, {"Configuration.xml": "<MetaDataObject/>"})
        with mock.patch.object(edt, "export", side_effect=AssertionError("экспорт выгрузки Конфигуратора")):
            build._load(self.platform, self.tmp / "прогон", d, None, None, None, self.state())

    def test_userbase_loads_export(self):
        class P(FakeDesigner):
            def designer(self, *args, label="", timeout=0):
                if args[0] == "/DumpCfg":
                    return 1, 0.1, "Расширение не найдено"
                return super().designer(*args, label=label)
        p = P(self.tmp)
        with mock.patch.object(edt, "export", return_value=(self.dump, 0.0)):
            userbase.Session(p, self.tmp / "прогон").load_project_extension(self.project / "src", "demo")
        load = next(c for c in p.calls if c[0] == "/LoadConfigFromFiles")
        self.assertEqual(str(load[1]), str(self.dump))


class NoEdtcliTest(unittest.TestCase):
    """Исходники EDT, пробы заказаны, 1cedtcli нет: шаг run выполняется (правки одних модулей грузятся без экспорта);
    понадобился экспорт — пробы «не проверено» с причиной, а не сбой; статика работает."""

    def test_export_needed_not_checked(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-noedt-"))
        try:
            project = tmp / "проект"
            write(project / "src", edt_files())
            (tmp / "ib").mkdir()
            cfg = config._merge(config.DEFAULTS, {"checks": {"bslls": "off", "platform-api": "off"},
                                                  "environment": {"ib": str(tmp / "ib")}})
            with mock.patch("otk_1c.gate.runner.run", side_effect=edt.NoEdtcli("1cedtcli не найден")) as run:
                summary = pipeline.gate_run(tmp, cfg, tmp / "прогон", pipeline.GateOptions(head_dir=project / "src"),
                                            log=lambda m: None)
            run.assert_called_once()
            reasons = {i["check"]: i["reason"] for i in summary["notChecked"]}
            self.assertIn("1cedtcli", reasons["probes-server"])
            self.assertIn("probes-client", reasons)
            self.assertNotEqual(summary["code"], 2)
        finally:
            rmtree_force(tmp)

    def test_export_raises_no_edtcli(self):
        with self.assertRaises(edt.NoEdtcli):
            edt.export(None, Path("src"), Path("run"))


if __name__ == "__main__":
    unittest.main()
