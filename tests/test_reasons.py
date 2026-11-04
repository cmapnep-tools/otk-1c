"""Причины «не проверено» и сбоев с reasonCode: распознавание текста, итоги gate --json и doctor --json."""
import contextlib
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import schema_check
from otk_1c import checks, cli
from otk_1c.gate import reasons
from test_cli_only import EXT_CONFIG, MODULE
from test_collect import FLAGS, write

DOCS = Path(__file__).resolve().parent.parent / "docs" / "agents.md"

# тексты — как их пишут источники сбоев (platform.py, build.py, config.py, collect.py, executors, checks.Gate)
MESSAGES = {
    "ib-busy": ["база D:/ib занята (заблокированы 1Cv8.cfl); закройте Конфигуратор и сеансы 1С:Предприятия",
                "проверка Конфигуратором: база /home/ib занята (Ошибка блокировки информационной базы)"],
    "timeout": ["Конфигуратор не завершился за 600 с (загрузка конфигурации)",
                "экспорт EDT не завершился за 1800 с (environment.edtTimeout)"],
    "no-license": ["загрузка конфигурации: rc=1 Не обнаружена лицензия для запуска"],
    "ib-auth": ["ibcmd (config import) запрашивает имя пользователя: в базе есть пользователи"],
    "probe-failed": ["не выполнено: COM-соединение не установлено", "не выполнено: сеанс YAxUnit не стартовал"],
    "no-ibcmd": ["environment.ibTool = ibcmd, а ibcmd в C:/1cv8/bin нет: установите компоненты сервера",
                 "снять безопасный режим с расширения Р нечем: нет ibcmd и COM"],
    "no-platform": ["платформа 1С не найдена: --platform, environment.platform в otk.local.toml"],
    "ib-invalid": ["копия ИБ D:/ib: каталог не пуст, а файловой ИБ (1Cv8.1CD) в нём нет — укажите другой каталог",
                   "копии ИБ D:/ib нет: для проверки расширения нужна копия ИБ с основной конфигурацией"],
    "no-ib": ["не задана копия ИБ: --ib, environment.ib в otk.local.toml или OTK_IB",
              "проверка Конфигуратором: --designer: нет копии ИБ (environment.ib)"],
    "no-yaxunit": ["не задан YAxUnit: environment.yaxunit — путь к YAxUnit-*.cfe"],
    "no-com": ["COM недоступен: нет pywin32 (pip install pywin32)"],
    "no-parent": ["parent = ../cf: нет файла конфигурации (../cf/Configuration.xml) — выгрузка основной "
                  "конфигурации не найдена"],
    "no-source": ["нет файла конфигурации (Configuration.xml или Configuration/Configuration.mdo) в src/cf",
                  "D:/x: не выгрузка конфигурации (нет файла конфигурации в корне)"],
    "no-plan": ["нет плана D:/otk/otk.json: сначала otk collect или gate"],
    "edt-failed": ["экспорт EDT D:/edt/Проект: rc=1 ошибка"],
    "query-incomplete": ["текст собран с переменными частями"],
    "config-invalid": ["environment.ibTool = 'x': допустимо designer или ibcmd",
                       "[designerCheck] modes = ['Х']: неизвестные ключи ['Х']", cli.DESIGNER_STATIC,
                       "D:/p/otk.toml: ошибка синтаксиса TOML: Expected ']' at the end of a table declaration"],
    "platform-failed": ["загрузка конфигурации: rc=1 ошибка разбора", "не создана копия ИБ D:/ib: rc=3",
                        "выгрузка конфигурации ИБ не удалась (было, rc=1): …"],
    "other": ["что-то совсем другое", "", None],
}


class ClassifyTest(unittest.TestCase):
    def test_messages(self):
        for code, texts in MESSAGES.items():
            for text in texts:
                with self.subTest(text):
                    self.assertEqual(reasons.classify(text), code)

    def test_gate_decisions(self):
        """«не найдено: …» реестра проверок (checks.Gate) — код недостающей зависимости, и в режиме on."""
        expect = {"ib": "no-ib", "yaxunit": "no-yaxunit", "com": "no-com", "analyzer": "no-analyzer",
                  "edtcli": "no-edtcli", "help": "no-help"}
        for dep, code in expect.items():
            with self.subTest(dep):
                g = checks.Gate({"checks": {"smoke": "on", "scenarios": "auto"}})
                g.lack("scenarios", checks.DEPS[dep])
                g.lack("smoke", checks.DEPS[dep])
                self.assertEqual(reasons.classify(g.not_checked[0]["reason"]), code)
                self.assertEqual(reasons.classify(g.infra[0]), code)
                self.assertEqual(checks.DEP_CODES[dep], code)

    def test_codes_stable(self):
        """Каждый код: что сделать, перечислен в схеме и в документации; DEP_CODES — только из списка."""
        enum = schema_check.load("gate-result")["$defs"]["reason"]["properties"]["reasonCode"]["enum"]
        self.assertEqual(sorted(enum), sorted(reasons.CODES))
        documented = set(re.findall(r"^\| `([a-z-]+)` \|", DOCS.read_text(encoding="utf-8"), re.M))
        self.assertEqual(documented, set(reasons.CODES))
        self.assertLessEqual(set(checks.DEP_CODES.values()), set(reasons.CODES))
        self.assertTrue(all(reasons.CODES.values()))

    def test_code_from_source(self):
        """Код задаёт место сбоя, текст — только запасной путь (сообщения внешних программ)."""
        from otk_1c.errors import InfraError
        self.assertEqual(InfraError("лицензия", code="ib-busy").code, "ib-busy")
        self.assertEqual(reasons.entry({"message": "нет лицензии", "reasonCode": "timeout"})["reasonCode"], "timeout")
        self.assertEqual(reasons.entry("нет лицензии")["reasonCode"], "no-license")
        g = checks.Gate({"checks": {"smoke": "on", "scenarios": "auto"}})
        g.ready("smoke", {"ib": True})
        g.ready("scenarios", {"ib": True})
        self.assertEqual(g.infra[0]["reasonCode"], "no-yaxunit")
        self.assertEqual(g.not_checked[0]["reasonCode"], "no-yaxunit")


SUMMARY = {"code": 0, "verdict": "можно отправлять", "base": "a", "head": "b", "modules": 1, "queries": 0,
           "findings": [], "uncovered": [], "notes": [], "infra": [], "timings": {},
           "notChecked": [{"check": "smoke", "what": "проверка «открытие изменённых форм»", "file": None, "line": None,
                           "reason": f"не найдено: {checks.DEPS['ib']} — теряется: ошибки при открытии форм"}]}


class GateResultTest(unittest.TestCase):
    def test_status(self):
        cases = [({}, "ok"), ({"verdict": "нечего проверять"}, "nothing"), ({"code": 1}, "errors"),
                 ({"code": 2, "infra": ["база занята"]}, "infra"), ({"code": 2, "verdict": "ничего не проверено"},
                                                                   "not-checked")]
        for patch, status in cases:
            with self.subTest(status):
                res = reasons.gate_result({**SUMMARY, **patch}, "D:/run")
                self.assertEqual(res["status"], status)
                self.assertEqual(schema_check.errors(res, schema_check.load("gate-result")), [])

    def test_reasons(self):
        res = reasons.gate_result({**SUMMARY, "code": 2, "infra": ["база D:/ib занята (заблокированы 1Cv8.cfl)"]},
                                  "D:/run")
        nc, = res["notChecked"]
        self.assertEqual((nc["status"], nc["reasonCode"], nc["check"]), ("not-checked", "no-ib", "smoke"))
        self.assertEqual(nc["remediation"], reasons.CODES["no-ib"])
        self.assertEqual([(i["status"], i["reasonCode"]) for i in res["infra"]], [("infra", "ib-busy")])
        self.assertEqual(res["summaryPath"], str(Path("D:/run") / "summary.json"))

    def test_error(self):
        res = reasons.gate_error("нет файла конфигурации (Configuration.xml) в src/cf")
        self.assertEqual((res["code"], res["status"], res["infra"][0]["reasonCode"]), (2, "infra", "no-source"))
        self.assertEqual(schema_check.errors(res, schema_check.load("gate-result")), [])


class CliJsonTest(unittest.TestCase):
    """gate --json и doctor --json: в stdout — один объект по схеме, журнал — в stderr, код выхода прежний."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_ = tempfile.TemporaryDirectory(prefix="otk-test-")
        cls.tmp = Path(cls.tmp_.name)
        cls.ext, cls.work = cls.tmp / "расш", cls.tmp / "работа"
        write(cls.ext, "Configuration.xml", EXT_CONFIG)
        write(cls.ext, "CommonModules/Расш_Модуль.xml", FLAGS)
        write(cls.ext, "CommonModules/Расш_Модуль/Ext/Module.bsl", MODULE)
        cls.work.mkdir()

    @classmethod
    def tearDownClass(cls):
        cls.tmp_.cleanup()

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, json.loads(out.getvalue()), err.getvalue()

    def gate(self, folder, *extra):
        return self.run_cli("gate", "--repo", str(self.work), "--dir", str(folder), "--run-dir",
                            str(self.tmp / "прогоны"), "--set", "checks.bslls=off", "--set",
                            "checks.platform-api=off", "--json", *extra)

    def test_gate(self):
        code, res, err = self.gate(self.ext, "--static-only")
        self.assertEqual(schema_check.errors(res, schema_check.load("gate-result")), [])
        self.assertEqual(code, res["code"])
        self.assertIn("план: модулей", err)                     # журнал — не в stdout
        self.assertTrue(Path(res["summaryPath"]).is_file())
        summary = json.loads(Path(res["summaryPath"]).read_text(encoding="utf-8"))
        self.assertEqual(res["findings"], summary["findings"])

    def test_gate_error(self):
        empty = self.tmp / "пусто"
        empty.mkdir(exist_ok=True)
        code, res, err = self.gate(empty)
        self.assertEqual((code, res["status"]), (2, "infra"))
        self.assertEqual(res["infra"][0]["reasonCode"], "no-source")
        self.assertIn("ошибка:", err)

    def test_internal_error(self):
        """Исключение в самой ОТК: с --json — объект (internal, код 2), без --json — код 2, а не 1 «есть ошибки»."""
        with mock.patch.object(cli, "_gate_run", side_effect=KeyError("x")):
            code, res, err = self.gate(self.ext, "--static-only")
        self.assertEqual((code, res["infra"][0]["reasonCode"]), (2, "internal"))
        self.assertEqual(schema_check.errors(res, schema_check.load("gate-result")), [])
        self.assertIn("Traceback", err)
        with mock.patch.object(cli, "_gate_run", side_effect=OSError("диск")), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(["gate", "--repo", str(self.work), "--dir", str(self.ext), "--static-only"])
        self.assertEqual(code, 2)
        self.assertIn("внутренняя ошибка ОТК: OSError: диск", err.getvalue())

    def test_open_checked_first(self):
        """Неверный report.open — до прогона: итог не теряется после работы."""
        with mock.patch.object(cli, "_gate_run") as run:
            code, res, _ = self.gate(self.ext, "--static-only", "--set", "report.open=pdf")
        run.assert_not_called()
        self.assertEqual((code, res["infra"][0]["reasonCode"]), (2, "config-invalid"))

    def test_doctor_broken_toml(self):
        broken = self.tmp / "битый"
        write(broken, "otk.toml", "[project\n")
        code, res, _ = self.run_cli("doctor", "--repo", str(broken), "--dir", str(self.ext), "--json")
        self.assertEqual((code, res["error"]["reasonCode"]), (2, "config-invalid"))
        self.assertEqual(schema_check.errors(res, schema_check.load("doctor-result")), [])

    def test_doctor(self):
        code, res, _ = self.run_cli("doctor", "--repo", str(self.work), "--dir", str(self.ext), "--json")
        self.assertEqual(schema_check.errors(res, schema_check.load("doctor-result")), [])
        self.assertEqual(code, res["code"])
        self.assertEqual((res["project"]["extension"], res["project"]["reasonCode"]), ("Расш", "no-parent"))
        probes = next(r for r in res["checks"] if r["name"] == "probes-server")
        self.assertEqual((probes["state"], probes["reasonCodes"][0]), ("unavailable", "no-ib"))
        for row in res["checks"]:
            if row["state"] == "unavailable":
                self.assertTrue(row["reasonCodes"] and len(row["reasonCodes"]) == len(row["remediation"]))


if __name__ == "__main__":
    unittest.main()
