"""Реестр проверок: режимы [checks], устаревшие выключатели, недостающие зависимости, сводка."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from otk_1c import checks
from otk_1c.gate import doctor, report
from otk_1c.probes import platform


class ModesTest(unittest.TestCase):
    def test_default_auto(self):
        mode, warnings = checks.modes({})
        self.assertEqual(set(mode.values()), {"auto"})
        self.assertEqual(warnings, [])

    def test_values_and_typos(self):
        mode, warnings = checks.modes({"checks": {"smoke": "OFF", "analyzers": "on", "queries": "maybe",
                                                  "smok": "off"}})
        self.assertEqual((mode["smoke"], mode["analyzers"], mode["queries"]), ("off", "on", "auto"))
        self.assertEqual(len(warnings), 2)          # неизвестная проверка и недопустимое значение



class MissingTest(unittest.TestCase):
    def test_alternatives(self):
        self.assertEqual(checks.missing("probes-server", {"ib": True, "com": True}), [])
        self.assertEqual(checks.missing("probes-server", {"ib": True, "yaxunit": True}), [])
        lacking = checks.missing("probes-server", {"ib": True})
        self.assertEqual(len(lacking), 1)
        self.assertIn("COM", lacking[0])
        self.assertIn("YAxUnit", lacking[0])
        self.assertEqual(checks.missing("metadata", {}), [])


class GateTest(unittest.TestCase):
    def test_auto_not_checked(self):
        g = checks.Gate({})
        self.assertFalse(g.ready("probes-client", {"ib": True}))
        self.assertFalse(g.ready("probes-client", {"ib": True}))
        self.assertEqual(g.infra, [])
        self.assertEqual(len(g.not_checked), 1)      # одна строка на проверку
        self.assertIn("YAxUnit", g.not_checked[0]["reason"])

    def test_on_infra(self):
        g = checks.Gate({"checks": {"smoke": "on"}})
        self.assertFalse(g.ready("smoke", {"ib": True}))
        self.assertEqual(len(g.infra), 1)
        self.assertEqual(g.not_checked, [])

    def test_off_silent(self):
        g = checks.Gate({"checks": {"scenarios": "off"}})
        self.assertFalse(g.ready("scenarios", {"ib": True, "yaxunit": True}))
        self.assertEqual((g.infra, g.not_checked), ([], []))
        self.assertEqual(g.notes(), ["выключено настройкой: scenarios"])

    def test_ready(self):
        self.assertTrue(checks.Gate({}).ready("smoke", {"ib": True, "yaxunit": True}))


class EnvironmentTest(unittest.TestCase):
    def com(self, env):
        with (mock.patch("otk_1c.probes.platform.WINDOWS", True),
              mock.patch("otk_1c.probes.platform.find_platform", return_value=None),
              mock.patch("otk_1c.probes.platform.find_bslls", return_value=None),
              mock.patch("otk_1c.probes.platform.find_edtcli", return_value=None),
              mock.patch("otk_1c.probes.executors.com.available", return_value=(True, "")) as available):
            return doctor.environment({"environment": env})["com"], available.called

    def test_com_unused_without_external_connection(self):
        """Внешнее соединение убрано из environment.contexts — COM не пробуется."""
        (ok, note), called = self.com({"contexts": ["Server", "ThinClient"]})
        self.assertFalse(ok)
        self.assertFalse(called)
        self.assertIn("environment.contexts", note)

    def test_com_probed_with_external_connection(self):
        for env in ({"contexts": ["Server", "ExternalConnection"]}, {}):
            with self.subTest(env):
                (ok, _), called = self.com(env)
                self.assertTrue(ok)
                self.assertTrue(called)

    def test_ibcmd(self):
        """ibcmd ставится с компонентами сервера: без него doctor говорит, что медленнее и что в обход; при
        ibTool = ibcmd — что сборка упадёт."""
        tmp = Path(tempfile.mkdtemp()) / "8.3.27.2342" / "bin"
        tmp.mkdir(parents=True)
        with mock.patch.dict("os.environ", {"OTK_IB_TOOL": ""}):
            ok, note = doctor._ibcmd(tmp, {})
            self.assertFalse(ok)
            self.assertIn("Конфигуратором", note)
            self.assertIn("COM", note)
            self.assertIn("сбоем", doctor._ibcmd(tmp, {"ibTool": "ibcmd"})[1])
            (tmp / platform.exe_name("ibcmd")).write_bytes(b"")
            self.assertTrue(doctor._ibcmd(tmp, {})[0])

    def test_linux_display(self):
        """Linux: без дисплея (нет Xvfb и DISPLAY) копия ИБ бесполезна — пробы недоступны, статика доступна."""
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir, ib, cfe = Path(tmp, "8.3.27.2342"), Path(tmp, "ib"), Path(tmp, "YAxUnit.cfe")
            bin_dir.mkdir()
            ib.mkdir()
            (ib / "1Cv8.1CD").write_bytes(b"")
            cfe.write_bytes(b"")
            cfg = {"environment": {"platform": str(bin_dir), "ib": str(ib), "yaxunit": str(cfe)}}

            def state(display):
                with (mock.patch("otk_1c.probes.platform.WINDOWS", False),
                      mock.patch("otk_1c.probes.desktop.display_status", return_value=display)):
                    lines, _ = doctor.doctor(cfg)
                return next(line for line in lines if line.strip().startswith("probes-client"))

            self.assertIn("НЕ доступна", state((False, "нет Xvfb и DISPLAY")))
            self.assertIn(": доступна", state((True, "Xvfb")))


class ReportTest(unittest.TestCase):
    def test_not_checked_in_outputs(self):
        g = checks.Gate({})
        g.ready("smoke", {"ib": True})
        summary = {"code": 0, "verdict": "можно отправлять", "base": "a", "head": "b", "modules": 1, "queries": 0,
                   "findings": [], "notChecked": g.not_checked, "uncovered": [], "notes": [], "timings": {}}
        md = report.markdown(summary)
        self.assertIn("## Не проверено (1)", md)
        self.assertNotIn("None", md)
        self.assertIn('<skipped message="', report.junit(summary))
        self.assertIn('skipped="1"', report.junit(summary))
        self.assertTrue(any("not-checked/smoke" in line for line in report.problems(summary, ".")))


if __name__ == "__main__":
    unittest.main()
