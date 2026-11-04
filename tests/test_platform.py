"""Умолчания без привязки к машине: поиск платформы, ProgID COM, BSL LS, режимы проверки всех модулей."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _fs import rmtree_force
from otk_1c.probes import designer_check, platform as pf


class PlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        rmtree_force(self.tmp)

    def _install(self, version, layout_bin=True):
        d = self.tmp / "1cv8" / version
        b = d / "bin" if layout_bin else d
        b.mkdir(parents=True)
        (b / pf.exe_name("1cv8")).write_text("", encoding="utf-8")
        return b

    def test_find_newest(self):
        self._install("8.3.27.2342")
        newest = self._install("8.5.1.1529")
        self._install("8.3.9.999")                       # 8.3.9 < 8.3.27 по числам, не по строке
        (self.tmp / "1cv8" / "common").mkdir()           # посторонние каталоги пропускаются
        (self.tmp / "1cv8" / "8.6.0.1" / "bin").mkdir(parents=True)   # без исполняемого файла
        self.assertEqual(pf.find_platform([self.tmp / "1cv8"]), newest)
        self.assertIsNone(pf.find_platform([self.tmp / "нет"]))

    def test_install_roots_windows(self):
        """Оба режима установки: «для компьютера» и «для пользователя»; самая новая — среди всех корней."""
        env = {"ProgramFiles": str(self.tmp / "pf"), "ProgramFiles(x86)": str(self.tmp / "pf86"),
               "LOCALAPPDATA": str(self.tmp / "local")}
        with mock.patch.object(pf, "WINDOWS", True), mock.patch.dict(os.environ, env):
            roots = pf.install_roots()
            self.assertEqual(roots, [self.tmp / "pf" / "1cv8", self.tmp / "pf86" / "1cv8",
                                     self.tmp / "local" / "Programs" / "1cv8_x64",
                                     self.tmp / "local" / "Programs" / "1cv8_x86",
                                     self.tmp / "local" / "Programs" / "1cv8"])
            for root, version in ((roots[0], "8.3.27.2342"), (roots[2], "8.5.1.1529")):
                (root / version / "bin").mkdir(parents=True)
                (root / version / "bin" / pf.exe_name("1cv8")).write_text("", encoding="utf-8")
            self.assertEqual(pf.find_platform(), roots[2] / "8.5.1.1529" / "bin")
            del os.environ["LOCALAPPDATA"]                 # patch.dict вернёт переменную
            self.assertEqual(len(pf.install_roots()), 2)

    def test_linux_layout(self):
        b = self._install("8.3.27.2342", layout_bin=False)   # /opt/1cv8/x86_64/<версия>/1cv8
        self.assertEqual(pf.find_platform([self.tmp / "1cv8"]), b)

    def test_com_progid(self):
        self.assertEqual(pf.com_progid(Path("C:/Program Files/1cv8/8.5.1.1529/bin")), "V85.COMConnector")
        self.assertEqual(pf.com_progid(Path("C:/Program Files/1cv8/8.3.27.2342/bin")), "V83.COMConnector")
        self.assertEqual(pf.com_progid(Path("/opt/1cv8/x86_64/8.3.25.1000")), "V83.COMConnector")

    def test_com_connection(self):
        """Строка соединения COM для целевой базы хука: файловая и серверная."""
        exe = self._install("8.3.27.2342")
        f = pf.Platform({"connection": r"/FC:\bases\Демо", "platform": str(exe)}, self.tmp)
        self.assertEqual(f.com_connection(), r'File="C:\bases\Демо";')
        self.assertEqual(f.ib_target(), r"/FC:\bases\Демо")
        s = pf.Platform({"connection": r"/Ssrv01\demo_prod", "platform": str(exe)}, self.tmp)
        self.assertEqual(s.com_connection(), 'Srvr="srv01";Ref="demo_prod";')
        self.assertIsNone(s.ib)
        s.unlock()                                       # серверная база: файловых блокировок нет

    def test_hidden_desktop_setting(self):
        exe = self._install("8.3.27.2342")
        env = {"ib": str(self.tmp), "platform": str(exe)}
        self.assertTrue(pf.Platform(env, self.tmp).hidden_desktop)                           # по умолчанию
        self.assertFalse(pf.Platform({**env, "hiddenDesktop": False}, self.tmp).hidden_desktop)

    @unittest.skipUnless(pf.WINDOWS, "скрытый рабочий стол — только Windows")
    def test_hidden_desktop_process(self):
        """Процесс на своём рабочем столе: код возврата, таймаут ожидания, принудительное завершение."""
        import subprocess

        from otk_1c.probes import desktop
        p = desktop.popen(["cmd", "/c", "exit 3"])
        self.assertIsInstance(p, desktop.HiddenProcess)
        self.assertEqual(p.wait(timeout=30), 3)
        self.assertEqual(p.poll(), 3)
        p = desktop.popen(["cmd", "/c", "ping -n 30 127.0.0.1 >nul"])
        self.assertIsNone(p.poll())
        with self.assertRaises(subprocess.TimeoutExpired):
            p.wait(timeout=0.2)
        p.kill()
        self.assertEqual(p.wait(timeout=30), 1)

    @unittest.skipUnless(pf.WINDOWS, "объект задания — только Windows")
    def test_client_descendants_terminated(self):
        """Потомок клиента (8.5 перезапускает себя дочерним процессом) не переживает ни kill, ни выход родителя:
        иначе он держит копию ИБ."""
        import ctypes
        import sys
        import tempfile
        import time

        from otk_1c.probes import desktop
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        spawn = ("import subprocess, sys, time; "
                 "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
                 "open(sys.argv[1], 'w').write(str(c.pid)); time.sleep(float(sys.argv[2]))")

        def child_ended(parent_wait, kill, hidden):
            with tempfile.TemporaryDirectory() as tmp:
                pid_file = Path(tmp) / "pid"
                p = desktop.popen([sys.executable, "-c", spawn, str(pid_file), parent_wait], hidden=hidden)
                t = time.time()
                while not (pid_file.exists() and pid_file.read_text()) and time.time() - t < 30:
                    time.sleep(0.1)
                h = k32.OpenProcess(0x100000, False, int(pid_file.read_text()))      # SYNCHRONIZE
                self.assertTrue(h)
                if kill:
                    p.kill()
                p.wait(timeout=60)
                ended = k32.WaitForSingleObject(h, 10_000) == 0
                k32.CloseHandle(h)
                return ended

        self.assertTrue(child_ended("120", kill=True, hidden=True))
        self.assertTrue(child_ended("0", kill=False, hidden=True))
        self.assertTrue(child_ended("120", kill=True, hidden=False))

    @unittest.skipIf(pf.WINDOWS, "группа процессов — только Linux")
    def test_group_descendants_terminated(self):
        """Linux: потомок клиента не переживает ни kill, ни выход родителя (как задание на Windows)."""
        import sys
        import time

        from otk_1c.probes import desktop
        spawn = ("import subprocess, sys, time; "
                 "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
                 "open(sys.argv[1], 'w').write(str(c.pid)); time.sleep(float(sys.argv[2]))")

        def alive(pid):
            try:  # процесс может уйти между kill и чтением /proc
                os.kill(pid, 0)
                stat = Path(f"/proc/{pid}/stat").read_text()
            except (ProcessLookupError, FileNotFoundError):
                return False
            return not stat.split(")")[-1].split()[0] == "Z"

        def child_ended(parent_wait, kill):
            pid_file = self.tmp / f"pid-{parent_wait}-{kill}"
            with mock.patch.object(desktop, "display_env", return_value=None):
                p = desktop.popen([sys.executable, "-c", spawn, str(pid_file), parent_wait])
            self.assertIsInstance(p, desktop.GroupProcess)
            t = time.time()
            while not (pid_file.exists() and pid_file.read_text()) and time.time() - t < 30:
                time.sleep(0.1)
            child = int(pid_file.read_text())
            if kill:
                p.kill()
            p.wait(timeout=60)
            t = time.time()
            while alive(child) and time.time() - t < 10:
                time.sleep(0.1)
            return not alive(child)

        self.assertTrue(child_ended("120", kill=True))
        self.assertTrue(child_ended("0", kill=False))

    @unittest.skipIf(pf.WINDOWS, "дисплей X — только Linux")
    def test_display_env(self):
        """Linux: hidden — свой Xvfb; без Xvfb — DISPLAY сеанса; нет ни того, ни другого — NoDisplay."""
        from otk_1c.probes import desktop
        with mock.patch("shutil.which", return_value=None):
            with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
                self.assertIsNone(desktop.display_env(True))
            with mock.patch.dict(os.environ, {}, clear=True):
                with self.assertRaises(desktop.NoDisplay):
                    desktop.display_env(True)
                self.assertFalse(desktop.display_status()[0])
        with (mock.patch("shutil.which", return_value="/usr/bin/Xvfb"),
              mock.patch.object(desktop, "_start_xvfb", return_value=":7") as start):
            with mock.patch.dict(os.environ, {"DISPLAY": ":0"}):
                self.assertIsNone(desktop.display_env(False))              # окна видны (отладка)
                self.assertEqual(desktop.display_env(True)["DISPLAY"], ":7")
            start.assert_called_once()

    @unittest.skipUnless(not pf.WINDOWS and __import__("shutil").which("Xvfb"), "нужен Xvfb")
    def test_xvfb_started_once(self):
        from otk_1c.probes import desktop
        try:
            first = desktop.display_env(True)["DISPLAY"]
            self.assertRegex(first, r"^:\d+$")
            self.assertEqual(desktop.display_env(True)["DISPLAY"], first)
            self.assertIsNone(desktop._xvfb[0].poll())
        finally:
            desktop._stop_xvfb()
        self.assertIsNone(desktop._xvfb)

    def test_designer_busy(self):
        """База открыта другим сеансом: на Linux блокировка не в .cfl — занятость сообщает сам Конфигуратор."""
        exe = self._install("8.3.27.2342")
        p = pf.Platform({"ib": str(self.tmp / "ib"), "platform": str(exe)}, self.tmp)

        def run(text, rc):
            def fake(cmd, **kw):
                Path(cmd[-1][len("/Out"):]).write_text(text, encoding="utf-8-sig")
                return mock.Mock(returncode=rc)
            with mock.patch.object(p, "display_env", return_value=None), mock.patch("subprocess.run", fake):
                return p.designer("/CheckModules", "-Server")

        with self.assertRaisesRegex(pf.InfraError, "занята"):
            run("Ошибка блокировки информационной базы для конфигурирования.\nВозможно, информационная база "
                "уже открыта Конфигуратором.", 1)
        self.assertEqual(run("Синтаксических ошибок не обнаружено!", 0)[::2],
                         (0, "Синтаксических ошибок не обнаружено!"))

    def test_find_bslls(self):
        exe = self.tmp / "bsl.exe"
        exe.write_text("", encoding="utf-8")
        self.assertEqual(pf.find_bslls(str(exe)), exe)

    def test_default_modes(self):
        cfg = self.tmp / "Configuration.xml"
        cfg.write_text("<Configuration><Properties><DefaultRunMode>ManagedApplication</DefaultRunMode>"
                       "<UseManagedFormInOrdinaryApplication>true</UseManagedFormInOrdinaryApplication>"
                       "</Properties></Configuration>", encoding="utf-8")
        self.assertNotIn(designer_check.ORDINARY, designer_check.default_modes(self.tmp))
        self.assertIn(designer_check.ORDINARY, designer_check.default_modes(self.tmp, ordinary_setting=True))
        cfg.write_text("<DefaultRunMode>OrdinaryApplication</DefaultRunMode>", encoding="utf-8")
        self.assertIn(designer_check.ORDINARY, designer_check.default_modes(self.tmp))
        self.assertEqual(designer_check.default_modes(self.tmp / "нет")[-1], "-ExtendedModulesCheck")

    def test_config_check_flags(self):
        """Флаги /CheckConfig — по свойствам конфигурации (демо: управляемая, без модальности)."""
        cfg = self.tmp / "Configuration.xml"
        cfg.write_text("<Configuration><Properties><DefaultRunMode>ManagedApplication</DefaultRunMode>"
                       "<UsePurposes><v8:Value>PlatformApplication</v8:Value></UsePurposes>"
                       "<ModalityUseMode>DontUse</ModalityUseMode>"
                       "<SynchronousPlatformExtensionAndAddInCallUseMode>Use"
                       "</SynchronousPlatformExtensionAndAddInCallUseMode></Properties></Configuration>",
                       encoding="utf-8")
        flags = designer_check.config_check_flags(self.tmp)
        for f in ("-ConfigLogIntegrity", "-IncorrectReferences", "-HandlersExistence", "-UnreferenceProcedures",
                  "-EmptyHandlers", "-ThinClient", "-WebClient", "-Server", "-ExternalConnection",
                  "-ExternalConnectionServer", "-ThickClientManagedApplication",
                  "-ThickClientServerManagedApplication", "-ExtendedModulesCheck", "-CheckUseModality"):
            self.assertIn(f, flags)
        for f in ("-CheckUseSynchronousCalls", "-MobileAppClient", "-UnsupportedFunctional", "-MobileClient",
                  "-DistributiveModules", designer_check.ORDINARY, "-ThickClientServerOrdinaryApplication"):
            self.assertNotIn(f, flags)
        self.assertEqual(len(flags), len(set(flags)))
        cfg.write_text("<Configuration><Properties><DefaultRunMode>OrdinaryApplication</DefaultRunMode>"
                       "<UsePurposes><v8:Value>PlatformApplication</v8:Value>"
                       "<v8:Value>MobilePlatformApplication</v8:Value></UsePurposes>"
                       "<ModalityUseMode>Use</ModalityUseMode></Properties></Configuration>", encoding="utf-8")
        flags = designer_check.config_check_flags(self.tmp)
        for f in (designer_check.ORDINARY, "-ThickClientServerOrdinaryApplication", "-MobileAppClient",
                  "-MobileAppServer", "-UnsupportedFunctional"):
            self.assertIn(f, flags)
        self.assertNotIn("-CheckUseModality", flags)
        # явные режимы [designerCheck] modes: без -ExtendedModulesCheck поиска модальности нет
        cfg.write_text("<ModalityUseMode>DontUse</ModalityUseMode>", encoding="utf-8")
        self.assertEqual(designer_check.config_check_flags(self.tmp, modes=["-Server"])[-1], "-Server")


class OrdinaryContextTest(unittest.TestCase):
    """Контекст «обычное приложение» у общего модуля — только если конфигурация в нём работает."""

    XML = ("<CommonModule><Properties><Global>false</Global><ClientManagedApplication>true</ClientManagedApplication>"
           "<Server>true</Server><ExternalConnection>false</ExternalConnection><ClientOrdinaryApplication>true"
           "</ClientOrdinaryApplication><ServerCall>false</ServerCall><Privileged>false</Privileged></Properties>"
           "</CommonModule>")

    def test_flag_alone_is_not_enough(self):
        from otk_1c.sources import module_kinds
        read = {"CommonModules/М.xml": self.XML}.get
        info = module_kinds.describe("CommonModules/М/Ext/Module.bsl", read)
        self.assertEqual(info.contexts, ["Server", "ThinClient"])
        info = module_kinds.describe("CommonModules/М/Ext/Module.bsl", read, ordinary_application=True)
        self.assertIn("OrdinaryClient", info.contexts)

    def test_default_run_mode(self):
        from otk_1c.sources import layout
        self.assertEqual(layout.DESIGNER.properties("<DefaultRunMode>OrdinaryApplication</DefaultRunMode>").run_mode,
                         "OrdinaryApplication")
        self.assertIsNone(layout.DESIGNER.properties(None).run_mode)


if __name__ == "__main__":
    unittest.main()
