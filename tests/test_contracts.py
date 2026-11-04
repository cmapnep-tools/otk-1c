"""Контракты файлов шагов: схемы, план неизменен после collect, сводка только из файлов шагов."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import otk_1c
import schema_check
from _fs import rmtree_force
from otk_1c import checks, cli, config, model
from otk_1c.gate import pipeline, report, runner
from otk_1c.sources import workspace
from test_collect import BASE_MODULE, CONFIG, FLAGS, HEAD_MODULE, write

CALLER = "Процедура Вызов() Экспорт\n\tА = М.Изменяемая(1, 2, 3);\nКонецПроцедуры\n"
DEPENDENT = "CommonModules/Вызов/Ext/Module.bsl"
TIMINGS = ("index", "checks", "analyzers")
EMPTY_RUN = {"timings": {}, "notes": [], "results": []}


def fill(src, module):
    write(src, "Configuration.xml", CONFIG)
    write(src, "CommonModules/М.xml", FLAGS)
    write(src, "CommonModules/М/Ext/Module.bsl", module)
    write(src, "CommonModules/Вызов.xml", FLAGS)
    write(src, DEPENDENT, CALLER)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


class TestStepFiles(unittest.TestCase):
    """gate без копии ИБ на выгрузке со сменой сигнатуры экспортного метода: вызывающий модуль — зависимый."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        cls.base, cls.head, cls.project = cls.tmp / "было", cls.tmp / "стало", cls.tmp / "проект"
        fill(cls.base, BASE_MODULE)
        fill(cls.head, HEAD_MODULE)
        cls.project.mkdir()
        cls.cfg = config._merge(config.DEFAULTS, {"checks": {"analyzers": "off", "platform-api": "off"}})
        cls.run_dir = cls.tmp / "прогон"
        cls.summary = pipeline.gate_run(cls.project, cls.cfg, cls.run_dir,
                                        pipeline.GateOptions(head_dir=cls.head, base_dir=cls.base),
                                        log=lambda m: None)

    @classmethod
    def tearDownClass(cls):
        rmtree_force(cls.tmp)

    def test_schemas(self):
        for name, file in (("plan", "otk.json"), ("static", "static.json"), ("run", "run.json"),
                           ("summary", "summary.json")):
            with self.subTest(file):
                self.assertEqual(schema_check.errors(read(self.run_dir / file), schema_check.load(name)), [])

    def test_headers(self):
        """Каждый файл — schema и версия ОТК; внутренний (не контракт) — первым полем $comment."""
        for name, file, internal in (("plan", "otk.json", False), ("static", "static.json", True),
                                     ("run", "run.json", True), ("summary", "summary.json", False)):
            with self.subTest(file):
                data = read(self.run_dir / file)
                self.assertEqual((data["schema"], data["version"]), (f"otk.{name}.v1", otk_1c.__version__))
                self.assertEqual(next(iter(data)) == "$comment", internal)

    def test_published_open(self):
        """Публикуемые схемы: абсолютный $id, корень открыт, код причины — открытое перечисление."""
        for name in ("plan", "summary", "gate-result", "doctor-result"):
            schema = schema_check.load(name, strict=False)
            self.assertTrue(schema["$id"].startswith("https://"), name)
            self.assertNotIn('"additionalProperties": false', json.dumps(schema), name)
        reason = schema_check.load("gate-result", strict=False)["$defs"]["reason"]["properties"]["reasonCode"]
        self.assertIn({"type": "string"}, reason["anyOf"])

    def test_validator_catches(self):
        broken = {**read(self.run_dir / "run.json"), "results": [{"executor": "x", "outcome": "плохо"}]}
        self.assertTrue(schema_check.errors(broken, schema_check.load("run")))

    def test_dependents_in_static_not_plan(self):
        plan, static = read(self.run_dir / "otk.json"), read(self.run_dir / "static.json")
        self.assertNotIn(DEPENDENT, [m["path"] for m in plan["modules"]])
        self.assertEqual([(m["path"], m["reason"], m["dependentLines"]) for m in static["dependents"]],
                         [(DEPENDENT, "dependent", [2])])
        self.assertEqual(self.summary["modules"], len(plan["modules"]) + 1)       # сводка видит и зависимые
        self.assertEqual(static["dependents"][0]["uncovered"], [])          # все контексты модуля доступны

    def test_dependents_uncovered(self):
        """Контекст зависимого модуля недоступен локально — «не покрыто этим прогоном», как у изменённого."""
        cfg = config._merge(self.cfg, {"environment": {"contexts": ["Server", "ThinClient"]}})
        run_dir = self.tmp / "прогон-без-вс"
        pipeline.gate_run(self.project, cfg, run_dir, pipeline.GateOptions(head_dir=self.head, base_dir=self.base),
                          log=lambda m: None)
        plan, static = read(run_dir / "otk.json"), read(run_dir / "static.json")
        want = [{"context": "ExternalConnection", "needs": "designer:ExternalConnection",
                 "reason": "контекст недоступен локально"}]
        self.assertEqual([m["uncovered"] for m in plan["modules"]], [want])
        self.assertEqual([m["uncovered"] for m in static["dependents"]], [want])

    def test_decisions_in_step_files(self):
        """Без копии ИБ «не проверено» — решение шага run: оно в run.json, а сводка его только переносит."""
        run = read(self.run_dir / "run.json")
        self.assertIn("probes-server", {i["check"] for i in run["notChecked"]})
        self.assertEqual(run["results"], [])
        self.assertIn("выключено настройкой: analyzers, platform-api", read(self.run_dir / "otk.json")["notes"])

    def test_rerun_static(self):
        """Шаг static отдельно на файлах gate: план не меняется, static.json и сводка — те же."""
        plan_path = self.run_dir / "otk.json"
        plan_bytes, before = plan_path.read_bytes(), read(self.run_dir / "static.json")
        pipeline.static_step(self.project, self.cfg, self.run_dir, plan_path)
        after = read(self.run_dir / "static.json")
        self.assertEqual(plan_path.read_bytes(), plan_bytes)
        self.assertEqual({k: v for k, v in after.items() if k not in TIMINGS},
                         {k: v for k, v in before.items() if k not in TIMINGS})
        self.assertEqual(report.from_files(self.run_dir, self.summary["timings"], self.head), self.summary)

    def test_metadata_off(self):
        """[checks] metadata = "off": правила статики и поиск зависимых не выполняются, сводка это называет."""
        run_dir = self.copy_run()
        cfg = config._merge(self.cfg, {"checks": {"metadata": "off"}})
        static = pipeline.static_step(self.project, cfg, run_dir, run_dir / "otk.json")
        self.assertTrue(read(self.run_dir / "static.json")["dependents"])
        self.assertEqual((static["findings"], static["dependents"]), ([], []))
        self.assertIn("выключено настройкой: metadata, analyzers, platform-api", checks.Gate(cfg).notes())

    def test_report_command(self):
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(["report", "--repo", str(self.project), "--run-dir", str(self.run_dir)])
        self.assertEqual(code, self.summary["code"])
        got = read(self.run_dir / "summary.json")
        self.assertEqual({**got, "timings": {}}, {**self.summary, "timings": {}})

    def test_report_runs_root(self):
        """report --run-dir — и корень прогонов, как у gate: gate --run-dir X, затем report --run-dir X."""
        root = Path(tempfile.mkdtemp(prefix="otk-test-"))
        self.addCleanup(rmtree_force, root)
        name = workspace.run_dir(config.load(self.project), self.project).name
        shutil.copytree(self.run_dir, root / name)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = cli.main(["report", "--repo", str(self.project), "--run-dir", str(root)])
        self.assertEqual(code, self.summary["code"])
        self.assertIn(str(root / name / "summary.md"), out.getvalue())

    def test_collect_stdout_plan_only(self):
        """collect --out -: в stdout — только план (| jq), сводка плана — в stderr."""
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(["collect", "--repo", str(self.project), "--dir", str(self.head),
                             "--base-dir", str(self.base), "--out", "-"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["modules"], read(self.run_dir / "otk.json")["modules"])
        self.assertIn("модулей:", err.getvalue())

    def copy_run(self):
        tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        self.addCleanup(rmtree_force, tmp)
        shutil.copytree(self.run_dir, tmp / "прогон")
        return tmp / "прогон"

    def cli_run(self, run_dir):
        """otk run на файлах каталога прогона (пробы — заглушка): модули, которые получил исполнитель."""
        seen = []
        result = {**EMPTY_RUN, "results": [{"executor": "com", "kind": "probe", "module": DEPENDENT, "owner": None,
                                            "context": "Server", "outcome": "ok", "message": "", "line": None}]}
        with mock.patch("otk_1c.gate.runner._run", lambda repo, p, *a, **k: seen.append(p) or result), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            code = cli.main(["run", "--repo", str(self.project), "--plan", str(run_dir / "otk.json"),
                             "--run-dir", str(run_dir.parent / "другой")])
        return code, [m["path"] for m in seen[0]["modules"]], err.getvalue()

    def test_rerun_run(self):
        """Шаг run отдельно на файлах gate: берёт зависимые из static.json, run.json — по схеме и в сводке."""
        run_dir = self.copy_run()
        code, modules, _ = self.cli_run(run_dir)
        self.assertEqual(code, 0)
        self.assertIn(DEPENDENT, modules)
        run = read(next((run_dir.parent / "другой").glob("*/run.json")))
        self.assertEqual(schema_check.errors(run, schema_check.load("run")), [])
        self.assertEqual(run["plan"], read(run_dir / "static.json")["plan"])
        self.assertIn("копия ИБ считается загруженной выгрузкой «было» (--base-dir): загружены только отличия",
                      run["notes"])

    def test_stale_steps_ignored(self):
        """План пересобран (collect) — static.json и run.json прошлого плана не учитываются ни run, ни сводкой."""
        run_dir = self.copy_run()
        plan_path = run_dir / "otk.json"
        plan = read(plan_path)
        plan["notes"] = []                                   # другой план
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        code, modules, err = self.cli_run(run_dir)
        self.assertNotIn(DEPENDENT, modules)
        self.assertIn("static.json не учтён", err)
        summary = report.from_files(run_dir)
        self.assertEqual(summary["modules"], len(plan["modules"]))
        self.assertEqual([n for n in summary["notes"] if "не учтён" in n],
                         ["static.json не учтён: записан для другого плана (перезапустите шаг)",
                          "run.json не учтён: записан для другого плана (перезапустите шаг)"])

    def test_old_format_static(self):
        """static.json 0.5.x (dependents — словарь, без отпечатка) не роняет читателей: шаг считается невыполненным."""
        run_dir = self.copy_run()
        (run_dir / "static.json").write_text(json.dumps({"findings": [], "dependents": {"a.bsl": [1]}}),
                                             encoding="utf-8")
        self.assertEqual(report.step_for(read(run_dir / "otk.json"), run_dir / "static.json")[0], None)
        self.assertIn("static.json не учтён", " ".join(report.from_files(run_dir)["notes"]))

    def test_report_without_plan(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["report", "--repo", str(self.project), "--run-dir", str(self.tmp / "нет")]), 2)

    def test_steps_without_plan(self):
        """static и run без плана — код 2 и «нет плана» одной строкой, а не трассировка с кодом 1."""
        for cmd in ("static", "run"):
            with self.subTest(cmd), contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                code = cli.main([cmd, "--repo", str(self.project), "--plan", str(self.tmp / "нет" / "otk.json")])
            self.assertEqual(code, 2)
            self.assertIn("нет плана", err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())


class TestRunStep(unittest.TestCase):
    def test_run_takes_dependents(self):
        """run пробует зависимые модули static.json; план, переданный шагу, не меняется."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        try:
            plan = {"modules": [{"path": "a.bsl"}], "queries": []}
            static = {"dependents": [{"path": "b.bsl"}, {"path": "a.bsl"}]}
            seen = []
            cfg = config._merge(config.DEFAULTS, {})
            with mock.patch("otk_1c.gate.runner._run", lambda repo, p, *a, **k: seen.append(p) or dict(EMPTY_RUN)):
                out = runner.run(tmp, plan, cfg, tmp, tmp, static=static)
            self.assertEqual([m["path"] for m in seen[0]["modules"]], ["a.bsl", "b.bsl"])
            self.assertEqual(plan["modules"], [{"path": "a.bsl"}])
            self.assertEqual(read(tmp / "run.json"), out)
            self.assertEqual(schema_check.errors(out, schema_check.load("run")), [])
        finally:
            rmtree_force(tmp)

    def test_write_variants(self):
        """run.json всех веток шага — по схеме: загрузка не прошла, формы и сценарии, сбой инфраструктуры."""
        tmp = Path(tempfile.mkdtemp(prefix="otk-test-"))
        self.addCleanup(rmtree_force, tmp)
        plan = {"modules": [], "baseDir": "было"}
        load = {"executor": "build", "kind": "load", "module": "m.bsl", "owner": "m.bsl", "context": "Designer",
                "outcome": "compile-error", "message": "ошибка", "line": None}
        smoke = {"executor": "yaxunit", "kind": "smoke", "module": None, "owner": "Справочник.А.Форма.Ф",
                 "context": "ThinClient", "outcome": "skipped", "message": "отказ", "line": None}
        scen = {**smoke, "kind": "scenario", "outcome": "failed", "module": "tests/m.bsl", "line": 3}
        gate = checks.Gate(config._merge(config.DEFAULTS, {"checks": {"smoke": "on"}}))
        gate.lack("smoke", "YAxUnit")
        for out in ({"timings": {"build": 1.0}, "notes": ["пробы не выполнялись"], "results": [load]},
                    {**EMPTY_RUN, "results": [smoke, scen]},
                    {"notes": runner.base_notes(plan), "infra": [{"message": "сбой", "reasonCode": None}]}):
            with self.subTest(out=out):
                got = runner.write(tmp, out, gate, plan)
                self.assertEqual(schema_check.errors(got, schema_check.load("run")), [])
        self.assertEqual(runner.base_notes(plan)[0][:30], "копия ИБ считается загруженной")

    def test_plan_notes(self):
        cfg = config._merge(config.DEFAULTS, {"checks": {"analyzers": "off"}, "project": {"extension": "Расш"}})
        self.assertEqual(pipeline.plan_notes(cfg), ["выключено настройкой: analyzers", config.NO_PARENT])
        self.assertEqual(pipeline.plan_notes(cfg, parent="типовая"), ["выключено настройкой: analyzers"])

    def test_gate_take(self):
        gate = checks.Gate(config._merge(config.DEFAULTS, {"checks": {"queries": "on"}}))
        gate.lack("analyzers", "x")
        self.assertEqual([i["check"] for i in gate.take()["notChecked"]], ["analyzers"])
        gate.lack("analyzers", "x")                  # повтор — не новое решение
        gate.lack("queries", "y")
        taken = gate.take()
        self.assertEqual((taken["notChecked"], len(taken["infra"])), ([], 1))
        self.assertEqual(gate.take(), {"notChecked": [], "infra": []})


class TestModel(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(model.FAIL, {"compile-error", "unknown", "failed"})
        self.assertNotIn("runtime-error", model.FAIL)
        self.assertIs(report.PROBE_FAIL, model.FAIL)


if __name__ == "__main__":
    unittest.main()
